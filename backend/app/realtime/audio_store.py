"""音频落盘：整场 `full.wav` + 逐回合 `turn_{idx:03d}.wav`。

为什么 M1 就要落盘（PLAN §9.1 第 1 条）：
- M3 的报告要能"跳回对应回合回听"；
- M9 的"表达体检"要把卡顿定位到具体句子；
- M4 的真实面试录音分析复用同一套读取逻辑。
这些都要求每段音频与 `session_turn` 的 `start_ms/end_ms` 一一对应，
所以回合切片必须与回合边界同步落盘，而不是事后猜测切分。
"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Any


class AudioStore:
    """单场会话的音频写入器（pcm16 单声道）。"""

    def __init__(self, base_dir: Path, session_id: int, *, sample_rate: int = 16000) -> None:
        self.session_id = session_id
        self.sample_rate = sample_rate
        self.dir = Path(base_dir) / str(session_id)
        self.dir.mkdir(parents=True, exist_ok=True)

        self._full: Any = None
        self._turn: Any = None
        self._turn_path: Path | None = None
        self._turn_idx: int | None = None
        self.total_bytes = 0

    # ------------------------------------------------------------------ #
    @property
    def full_path(self) -> Path:
        return self.dir / "full.wav"

    @property
    def duration_ms(self) -> int:
        if self.sample_rate <= 0:
            return 0
        return int(self.total_bytes / (self.sample_rate * 2) * 1000)

    @property
    def current_turn(self) -> int | None:
        return self._turn_idx

    # ------------------------------------------------------------------ #
    def _open(self, path: Path) -> Any:
        handle = wave.open(str(path), "wb")
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(self.sample_rate)
        return handle

    def append(self, pcm: bytes) -> None:
        """把音频同时写进整场文件与当前回合文件。"""
        if not pcm:
            return
        if self._full is None:
            self._full = self._open(self.full_path)
        self._full.writeframes(pcm)
        if self._turn is not None:
            self._turn.writeframes(pcm)
        self.total_bytes += len(pcm)

    def append_turn_only(self, pcm: bytes) -> None:
        """只写当前回合切片，不重复写整场文件。

        用途：VAD 判定"开口"是滞后的（要连续几帧超阈值），那几帧其实已经写进 full.wav 了；
        但它们属于这一回合的语音起点，必须补进切片，否则回放会缺开头。
        """
        if pcm and self._turn is not None:
            self._turn.writeframes(pcm)

    def begin_turn(self, idx: int) -> Path:
        """开始一个新回合的切片（会自动收尾上一段）。"""
        self.end_turn()
        self._turn_idx = idx
        self._turn_path = self.dir / f"turn_{idx:03d}.wav"
        self._turn = self._open(self._turn_path)
        return self._turn_path

    def end_turn(self) -> Path | None:
        path = self._turn_path
        if self._turn is not None:
            self._turn.close()
            self._turn = None
        self._turn_idx = None
        self._turn_path = None
        return path

    def close(self) -> None:
        self.end_turn()
        if self._full is not None:
            self._full.close()
            self._full = None

    # ------------------------------------------------------------------ #
    def turn_path(self, idx: int) -> Path:
        return self.dir / f"turn_{idx:03d}.wav"

    def relative(self, path: Path) -> str:
        """转成相对音频根目录的路径（存库用，便于整目录搬迁）。"""
        try:
            return path.relative_to(self.dir.parent).as_posix()
        except ValueError:
            return path.as_posix()

    def list_turn_files(self) -> list[Path]:
        return sorted(self.dir.glob("turn_*.wav"))
