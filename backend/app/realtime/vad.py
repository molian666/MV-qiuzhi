"""能量 VAD：判断「谁在说话」与「什么时候说完」。

为什么自己在本地做一层 VAD，而不是全交给厂商的 server_vad：
1. cascade 链路里 ASR 供应商可替换，但"什么时候该让 LLM 接话"是产品逻辑，必须自己可控；
2. 打断（barge-in）要求在**本地**几十毫秒内判断出用户开口，不能等云端事件往返；
3. 纯能量判定无依赖、可离线、可单测（PLAN §6.1 要点 1 的三要素之一）。

注意：Python 3.13 起标准库 `audioop` 已移除，所以这里自己算 RMS。
"""

from __future__ import annotations

import array
import math
from dataclasses import dataclass


@dataclass(slots=True)
class VadConfig:
    sample_rate: int = 16000
    frame_ms: int = 20
    # 绝对下限：安静房间里低于这个 RMS 一律算静音
    min_energy: float = 0.012
    # 自适应阈值 = max(min_energy, 噪声底 * noise_ratio)
    noise_ratio: float = 3.0
    # 噪声底上限。真实环境底噪不会超过这个量级；没有上限的话，
    # "一开麦就在说话"会把噪声底抬到语音能量，阈值随之变得永远达不到（VAD 假死）。
    max_noise_floor: float = 0.05
    start_frames: int = 3  # 连续 N 帧超阈值 → 判定开口（抗单帧爆音）
    # 冷启动预热帧：前 N 帧只用来估噪声底，不做判定。
    # 没有它的话，麦克风刚打开那几帧的底噪会被当成"用户开口"。
    warmup_frames: int = 5
    min_silence_ms: int = 900  # 连续静音多久 → 判定说完（会被节奏画像覆盖）
    max_speech_ms: int = 180_000  # 单轮上限，防卡死

    @property
    def frame_bytes(self) -> int:
        return int(self.sample_rate * 2 * self.frame_ms / 1000)


@dataclass(slots=True)
class VadEvent:
    kind: str  # speech_started | speech_stopped
    at_ms: int
    energy: float
    speech_ms: int = 0


def rms_of_pcm16(frame: bytes) -> float:
    """归一化到 [0, 1] 的 RMS 能量。"""
    usable = len(frame) - (len(frame) % 2)
    if usable <= 0:
        return 0.0
    samples = array.array("h")
    samples.frombytes(frame[:usable])
    total = 0
    for sample in samples:
        total += sample * sample
    return math.sqrt(total / len(samples)) / 32768.0


class EnergyVad:
    """帧能量 + 自适应噪声底的状态机。输入任意长度 pcm16，输出 VAD 事件。"""

    def __init__(self, config: VadConfig | None = None) -> None:
        self.config = config or VadConfig()
        self._buffer = bytearray()
        self._processed_ms = 0
        self._speaking = False
        self._speech_started_ms = 0
        self._loud_frames = 0
        self._silent_frames = 0
        self._noise_floor = 0.0
        self._warmup_left = self.config.warmup_frames

    @property
    def is_speaking(self) -> bool:
        return self._speaking

    @property
    def processed_ms(self) -> int:
        return self._processed_ms

    @property
    def noise_floor(self) -> float:
        return self._noise_floor

    @property
    def threshold(self) -> float:
        return max(self.config.min_energy, self._noise_floor * self.config.noise_ratio)

    def process(self, pcm: bytes) -> list[VadEvent]:
        events: list[VadEvent] = []
        if pcm:
            self._buffer.extend(pcm)

        frame_bytes = self.config.frame_bytes
        while len(self._buffer) >= frame_bytes:
            frame = bytes(self._buffer[:frame_bytes])
            del self._buffer[:frame_bytes]
            event = self._process_frame(frame)
            if event is not None:
                events.append(event)
        return events

    def _update_noise(self, energy: float) -> None:
        """噪声底：下降快、上升慢，且有硬上限（决定 VAD 能不能"醒过来"）。

        三个约束都很关键：
        - 比当前底噪低 → 较快跟随（环境安静下来要立刻反映，否则阈值迟迟不降）；
        - 比当前底噪高 → 极慢 EMA（偶发语音/敲键盘不该被当成环境噪音）；
        - 截到 max_noise_floor（防"一开麦就说"把底噪抬到语音能量导致 VAD 假死）。
        """
        if self._noise_floor == 0.0:
            self._noise_floor = energy
        elif energy < self._noise_floor:
            self._noise_floor = 0.7 * self._noise_floor + 0.3 * energy
        else:
            self._noise_floor = 0.99 * self._noise_floor + 0.01 * energy
        self._noise_floor = min(self._noise_floor, self.config.max_noise_floor)

    def _process_frame(self, frame: bytes) -> VadEvent | None:
        energy = rms_of_pcm16(frame)
        self._processed_ms += self.config.frame_ms

        if not self._speaking:
            # 预热期：刚开麦的头几帧无条件当作环境音，用来初始化噪声底
            if self._warmup_left > 0:
                self._update_noise(energy)
                self._warmup_left -= 1
                return None

            loud = energy >= self.threshold
            if loud:
                self._loud_frames += 1
            else:
                # 关键：**只在判定为静音的帧上更新噪声底**。
                # 踩过的坑：如果在判定前无条件更新，说话声会不断把阈值推高
                # （0.012 → 0.077 → 0.219…），几帧之后语音自己就被压住了，
                # 表现为"对着麦克风说半天，VAD 一次都不触发"。
                self._update_noise(energy)
                self._loud_frames = 0

            if self._loud_frames >= self.config.start_frames:
                self._speaking = True
                self._silent_frames = 0
                self._speech_started_ms = self._processed_ms - self._loud_frames * self.config.frame_ms
                return VadEvent(kind="speech_started", at_ms=self._speech_started_ms, energy=energy)
            return None

        # 说话中：噪声底冻结（别把说话声算进去）
        loud = energy >= self.threshold
        self._silent_frames = 0 if loud else self._silent_frames + 1
        silence_ms = self._silent_frames * self.config.frame_ms
        # 尾部静音不算说话时长，否则"回答长度"会平白多出一次静音阈值
        speech_end_ms = self._processed_ms - silence_ms
        speech_ms = max(0, speech_end_ms - self._speech_started_ms)

        if silence_ms >= self.config.min_silence_ms or speech_ms >= self.config.max_speech_ms:
            self._speaking = False
            self._loud_frames = 0
            self._silent_frames = 0
            return VadEvent(
                kind="speech_stopped",
                # at_ms 指向语音实际结束点，便于回放与报告精确定位
                at_ms=speech_end_ms,
                energy=energy,
                speech_ms=speech_ms,
            )
        return None

    def reset(self) -> None:
        self._buffer.clear()
        self._speaking = False
        self._loud_frames = 0
        self._silent_frames = 0
        self._warmup_left = 0  # 连接中重置（如打断）不需要重新预热
