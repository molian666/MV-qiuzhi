"""音频生成小工具：测试用（不进生产代码）。

不用 numpy：一来避免为一个测试引依赖，二来正弦生成够用且行为完全可预测。
"""

from __future__ import annotations

import math
import struct

SAMPLE_RATE = 16000


def silence(ms: int, sample_rate: int = SAMPLE_RATE) -> bytes:
    return b"\x00\x00" * int(sample_rate * ms / 1000)


def tone(ms: int, amplitude: float = 0.3, freq: float = 440.0, sample_rate: int = SAMPLE_RATE) -> bytes:
    """正弦波。amplitude 是相对满量程的比例（RMS ≈ amplitude / √2）。"""
    count = int(sample_rate * ms / 1000)
    data = bytearray()
    for index in range(count):
        value = int(amplitude * 32767 * math.sin(2 * math.pi * freq * index / sample_rate))
        data += struct.pack("<h", value)
    return bytes(data)


def loud_then_silent(speak_ms: int, silent_ms: int) -> bytes:
    return tone(speak_ms) + silence(silent_ms)
