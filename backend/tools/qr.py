"""终端二维码生成（纯标准库，不引第三方依赖）。

**为什么自己写**：手机访问时要敲一个 `https://xxx.tailnet-name.ts.net` 之类的长域名，
敲错一个字符就是"页面打不开"。扫一下二维码是这里唯一合理的交互。

**为什么不用 qrcode 库**：本项目的取向是本地优先、依赖能少就少（见 PLAN.md §1），
而二维码编码器是一个**规范固定、不会过时**的算法——ISO/IEC 18004 定死的东西，
自己实现没有"跟着上游变"的风险。代价是这 200 行要自己维护，所以测试写得很实
（见 `backend/tests/test_qr.py`：结构自检 + 与真实解码器对拍）。

只实现**字节模式**（URL 和中文都走 UTF-8 字节），版本 1–10——
够放一个域名（版本 10 最多 213 字节，见 `max_bytes()`），而且模块数 ≤ 57，终端里扫得动。
纠错等级 M（约 15%）。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

# --------------------------------------------------------------------------- #
# 容量与码块表（ISO/IEC 18004 表 9，只取字节模式 + 纠错等级 M，版本 1-10）
# --------------------------------------------------------------------------- #

#: (每块纠错码字数, 第一组块数, 第一组数据码字, 第二组块数, 第二组数据码字)
_ECC_M: dict[int, tuple[int, int, int, int, int]] = {
    1: (10, 1, 16, 0, 0),
    2: (16, 1, 28, 0, 0),
    3: (26, 1, 44, 0, 0),
    4: (18, 2, 32, 0, 0),
    5: (24, 2, 43, 0, 0),
    6: (16, 4, 27, 0, 0),
    7: (18, 4, 31, 0, 0),
    8: (22, 2, 38, 2, 39),
    9: (22, 3, 36, 2, 37),
    10: (26, 4, 43, 1, 44),
}

#: 每个版本的总码字数（数据 + 纠错）
_TOTAL_CODEWORDS: dict[int, int] = {
    1: 26, 2: 44, 3: 70, 4: 100, 5: 134,
    6: 172, 7: 196, 8: 242, 9: 292, 10: 346,
}

#: 对齐图形中心坐标（版本 1 没有对齐图形）
_ALIGN: dict[int, tuple[int, ...]] = {
    1: (),
    2: (6, 18),
    3: (6, 22),
    4: (6, 26),
    5: (6, 30),
    6: (6, 34),
    7: (6, 22, 38),
    8: (6, 24, 42),
    9: (6, 26, 46),
    10: (6, 28, 50),
}


def data_capacity(version: int) -> int:
    """该版本在纠错等级 M 下能放多少**数据码字**。"""
    ecc_per_block, blocks1, data1, blocks2, data2 = _ECC_M[version]
    del ecc_per_block
    return blocks1 * data1 + blocks2 * data2


def max_bytes(version: int) -> int:
    """该版本在字节模式下能编码的最大字节数。

    版本 1-10 依次是 14 / 26 / 42 / 62 / 84 / 106 / 122 / 152 / 180 / **213**。
    这个数**小于** `data_capacity()`——位流里还有 4 bit 模式指示符 + 字符计数，
    尾部不到一个码字的余量也用不上。算容量时忘了这一点是最容易犯的错，
    所以 `tests/test_qr.py` 里把这张表单独钉了一遍。
    """
    bits_for_data = data_capacity(version) * 8 - (4 + (8 if version <= 9 else 16))
    return bits_for_data // 8


def choose_version(byte_length: int) -> int:
    """选最小的能装下的版本。放不下就抛 ValueError（调用方给短一点的 URL）。"""
    for version in range(1, 11):
        if byte_length <= max_bytes(version):
            return version
    raise ValueError(
        f"内容太长：{byte_length} 字节，上限 {max_bytes(10)} 字节（版本 10）"
    )


# --------------------------------------------------------------------------- #
# GF(256) 上的 Reed-Solomon
# --------------------------------------------------------------------------- #

_EXP = [0] * 512
_LOG = [0] * 256


def _init_tables() -> None:
    """生成 GF(256) 的指数/对数表（本原多项式 0x11D，QR 规范指定）。"""
    value = 1
    for index in range(255):
        _EXP[index] = value
        _LOG[value] = index
        value <<= 1
        if value & 0x100:
            value ^= 0x11D
    for index in range(255, 512):
        _EXP[index] = _EXP[index - 255]


_init_tables()


def _mul(left: int, right: int) -> int:
    if left == 0 or right == 0:
        return 0
    return _EXP[_LOG[left] + _LOG[right]]


def _generator(degree: int) -> list[int]:
    """生成多项式 ∏(x - α^i)，返回降幂系数（最高次系数恒为 1）。"""
    poly = [1]
    for index in range(degree):
        product = [0] * (len(poly) + 1)
        for position, coefficient in enumerate(poly):
            product[position] ^= coefficient
            product[position + 1] ^= _mul(coefficient, _EXP[index])
        poly = product
    return poly


def _ecc(data: list[int], degree: int) -> list[int]:
    """多项式长除，返回余数（即纠错码字）。"""
    divisor = _generator(degree)
    remainder = list(data) + [0] * degree
    for start in range(len(data)):
        factor = remainder[start]
        if factor == 0:
            continue
        for offset, coefficient in enumerate(divisor):
            remainder[start + offset] ^= _mul(coefficient, factor)
    return remainder[len(data):]


# --------------------------------------------------------------------------- #
# 编码
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class QRCode:
    version: int
    size: int
    #: 每个 bool 是一个模块；True = 深色
    modules: list[list[bool]]

    def is_dark(self, row: int, col: int) -> bool:
        return self.modules[row][col]

    def to_text(self) -> str:
        """把模块矩阵导成 `#`/`.` 文本。

        **这是给测试用的**：`backend/tests/test_qr.py` 用真实解码器（ZXing）读这份
        文本，确认编码器产出的图片**真的能被扫出来**，而不是只看形状像不像。
        """
        return "\n".join(
            "".join("#" if cell else "." for cell in row) for row in self.modules
        )


def _bit_stream(payload: bytes, version: int) -> list[int]:
    """模式指示符(0100) + 字符计数 + 数据 + 终止符 + 补齐到码字边界。"""
    bits: list[int] = [0, 1, 0, 0]  # 字节模式
    count_bits = 8 if version <= 9 else 16
    for shift in range(count_bits - 1, -1, -1):
        bits.append((len(payload) >> shift) & 1)
    for byte in payload:
        for shift in range(7, -1, -1):
            bits.append((byte >> shift) & 1)

    capacity_bits = data_capacity(version) * 8
    # 终止符最多 4 个 0
    bits.extend([0] * min(4, capacity_bits - len(bits)))
    # 补齐到字节边界
    while len(bits) % 8:
        bits.append(0)
    # 用规范指定的两个填充码字交替填满
    pad = (0xEC, 0x11)
    index = 0
    while len(bits) < capacity_bits:
        for shift in range(7, -1, -1):
            bits.append((pad[index % 2] >> shift) & 1)
        index += 1

    return [int("".join(str(bit) for bit in bits[start:start + 8]), 2)
            for start in range(0, len(bits), 8)]


def _interleave(data: list[int], version: int) -> list[int]:
    """按规范把数据码字与纠错码字分块、交织（版本 8-10 有两组不同大小的块）。"""
    ecc_per_block, blocks1, data1, blocks2, data2 = _ECC_M[version]
    blocks: list[list[int]] = []
    cursor = 0
    for _ in range(blocks1):
        blocks.append(data[cursor:cursor + data1])
        cursor += data1
    for _ in range(blocks2):
        blocks.append(data[cursor:cursor + data2])
        cursor += data2

    ecc_blocks = [_ecc(block, ecc_per_block) for block in blocks]

    result: list[int] = []
    # 数据码字：第 i 个字节取自每块，短块先结束
    for index in range(max(len(block) for block in blocks)):
        for block in blocks:
            if index < len(block):
                result.append(block[index])
    # 纠错码字：所有块长度相同
    for index in range(ecc_per_block):
        for block in ecc_blocks:
            result.append(block[index])
    return result


def _new_matrix(size: int) -> list[list[bool]]:
    return [[False] * size for _ in range(size)]


def _reserved(size: int, version: int) -> list[list[bool]]:
    """标出功能图形占用的模块（数据放置时要跳过）。"""
    matrix = _new_matrix(size)

    def fill_rect(top: int, left: int, height: int, width: int) -> None:
        for row in range(top, top + height):
            for col in range(left, left + width):
                if 0 <= row < size and 0 <= col < size:
                    matrix[row][col] = True

    # 三个定位图形 + 分隔符
    fill_rect(0, 0, 9, 9)
    fill_rect(0, size - 8, 9, 8)
    fill_rect(size - 8, 0, 8, 9)

    # 校正图形
    for row_center in _ALIGN[version]:
        for col_center in _ALIGN[version]:
            # 与定位图形重叠的位置不画
            if (row_center, col_center) in {
                (6, 6), (6, size - 7), (size - 7, 6),
            }:
                continue
            fill_rect(row_center - 2, col_center - 2, 5, 5)

    # 定位（timing）图形
    for index in range(8, size - 8):
        matrix[6][index] = True
        matrix[index][6] = True

    # 版本信息（版本 ≥ 7）
    if version >= 7:
        fill_rect(size - 11, 0, 3, 6)
        fill_rect(0, size - 11, 6, 3)

    # 格式信息区域
    fill_rect(8, 0, 1, 9)
    fill_rect(0, 8, 9, 1)
    fill_rect(8, size - 8, 1, 8)
    fill_rect(size - 7, 8, 7, 1)
    matrix[size - 8][8] = True

    return matrix


def _place_patterns(modules: list[list[bool]], version: int) -> None:
    """画定位图形、分隔符、校正图形、定位图形、版本信息。"""
    size = len(modules)

    def finder(top: int, left: int) -> None:
        for row in range(-1, 8):
            for col in range(-1, 8):
                inside = 0 <= row <= 6 and 0 <= col <= 6
                ring = row in (0, 6) or col in (0, 6)
                core = 2 <= row <= 4 and 2 <= col <= 4
                if not inside:
                    value = False  # 分隔符：白
                elif ring or core:
                    value = True
                else:
                    value = False
                r, c = top + row, left + col
                if 0 <= r < size and 0 <= c < size:
                    modules[r][c] = value

    finder(0, 0)
    finder(0, size - 7)
    finder(size - 7, 0)

    for row_center in _ALIGN[version]:
        for col_center in _ALIGN[version]:
            if (row_center, col_center) in {(6, 6), (6, size - 7), (size - 7, 6)}:
                continue
            for row in range(-2, 3):
                for col in range(-2, 3):
                    edge = max(abs(row), abs(col))
                    modules[row_center + row][col_center + col] = edge != 1

    for index in range(8, size - 8):
        dark = index % 2 == 0
        modules[6][index] = dark
        modules[index][6] = dark

    if version >= 7:
        bits = _version_bits(version)
        for index in range(18):
            bit = (bits >> index) & 1
            row, col = divmod(index, 3)
            modules[size - 11 + row][col] = bool(bit)
            modules[col][size - 11 + row] = bool(bit)


def _version_bits(version: int) -> int:
    """版本的 18 bit BCH(18,6) 编码。"""
    value = version << 12
    for shift in range(5, -1, -1):
        if value & (1 << (shift + 12)):
            value ^= 0x1F25 << shift
    return (version << 12) | value


def _format_bits(mask: int) -> int:
    """格式信息的 15 bit BCH(15,5) 编码（纠错等级 M = 00）。"""
    data = (0b00 << 3) | mask
    value = data << 10
    for shift in range(4, -1, -1):
        if value & (1 << (shift + 10)):
            value ^= 0x537 << shift
    return ((data << 10) | value) ^ 0x5412


def _place_format(modules: list[list[bool]], mask: int) -> None:
    size = len(modules)
    bits = _format_bits(mask)

    def bit(index: int) -> bool:
        return bool((bits >> index) & 1)

    for index in range(6):
        modules[8][index] = bit(index)
    modules[8][7] = bit(6)
    modules[8][8] = bit(7)
    modules[7][8] = bit(8)
    for index in range(9, 15):
        modules[14 - index][8] = bit(index)

    for index in range(8):
        modules[size - 1 - index][8] = bit(index)
    for index in range(8, 15):
        modules[8][size - 15 + index] = bit(index)
    modules[size - 8][8] = True  # 固定的深色模块


def _place_data(modules: list[list[bool]], codewords: list[int], version: int) -> None:
    """按规范的之字形（两列一组、自下而上）放数据位。"""
    size = len(modules)
    reserved = _reserved(size, version)
    bits: list[int] = []
    for codeword in codewords:
        for shift in range(7, -1, -1):
            bits.append((codeword >> shift) & 1)

    index = 0
    column = size - 1
    upward = True
    while column > 0:
        if column == 6:  # 第 6 列是定位图形，整列跳过
            column -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for offset in (0, 1):
                col = column - offset
                if reserved[row][col]:
                    continue
                value = bits[index] if index < len(bits) else 0
                modules[row][col] = bool(value)
                index += 1
        upward = not upward
        column -= 2


_MASKS = (
    lambda row, col: (row + col) % 2 == 0,
    lambda row, col: row % 2 == 0,
    lambda row, col: col % 3 == 0,
    lambda row, col: (row + col) % 3 == 0,
    lambda row, col: (row // 2 + col // 3) % 2 == 0,
    lambda row, col: (row * col) % 2 + (row * col) % 3 == 0,
    lambda row, col: ((row * col) % 2 + (row * col) % 3) % 2 == 0,
    lambda row, col: ((row + col) % 2 + (row * col) % 3) % 2 == 0,
)


def _apply_mask(modules: list[list[bool]], version: int, mask: int) -> list[list[bool]]:
    size = len(modules)
    reserved = _reserved(size, version)
    # 格式信息位所在位置不算数据区，但掩码对它们不生效（稍后单独写）
    result = [row[:] for row in modules]
    fn = _MASKS[mask]
    for row in range(size):
        for col in range(size):
            if reserved[row][col]:
                continue
            if fn(row, col):
                result[row][col] = not result[row][col]
    return result


def _penalty(modules: list[list[bool]]) -> int:
    """规范定义的四个掩码惩罚项，取总分最低的掩码。"""
    size = len(modules)
    score = 0

    # 项 1：同色连续 ≥5
    for line in list(modules) + [list(column) for column in zip(*modules)]:
        run_length = 1
        for index in range(1, size):
            if line[index] == line[index - 1]:
                run_length += 1
            else:
                if run_length >= 5:
                    score += 3 + (run_length - 5)
                run_length = 1
        if run_length >= 5:
            score += 3 + (run_length - 5)

    # 项 2：2x2 同色块
    for row in range(size - 1):
        for col in range(size - 1):
            block = modules[row][col]
            if (modules[row][col + 1] == block
                    and modules[row + 1][col] == block
                    and modules[row + 1][col + 1] == block):
                score += 3

    # 项 3：1:1:3:1:1 的定位图形样式
    pattern_a = [True, False, True, True, True, False, True, False, False, False, False]
    pattern_b = [False, False, False, False, True, False, True, True, True, False, True]
    lines = list(modules) + [list(column) for column in zip(*modules)]
    for line in lines:
        for start in range(size - 10):
            window = line[start:start + 11]
            if window == pattern_a or window == pattern_b:
                score += 40

    # 项 4：深色比例偏离 50%
    dark = sum(1 for row in modules for module in row if module)
    percent = dark * 100 / (size * size)
    score += int(abs(percent - 50) / 5) * 10
    return score


def encode(text: str) -> QRCode:
    """把文本编成二维码（字节模式，UTF-8，纠错等级 M，自动选版本与掩码）。"""
    payload = text.encode("utf-8")
    version = choose_version(len(payload))
    size = 17 + 4 * version

    codewords = _interleave(_bit_stream(payload, version), version)

    base = _new_matrix(size)
    _place_patterns(base, version)
    _place_data(base, codewords, version)

    best: list[list[bool]] | None = None
    best_score = -1
    best_mask = 0
    for mask in range(8):
        candidate = _apply_mask(base, version, mask)
        _place_format(candidate, mask)
        score = _penalty(candidate)
        if best is None or score < best_score:
            best, best_score, best_mask = candidate, score, mask
    del best_mask

    assert best is not None
    return QRCode(version=version, size=size, modules=best)


#: 两个半块字符拼一行，等于**一个模块宽**，扫起来比例正确
_QUIET = 4


def _needs_full_blocks() -> bool:
    """终端能不能输出 `▀▄█`。不能就用整块字符兜底。

    这**不是杞人忧天**：中文版 PowerShell 5.1 的 stdout 默认是 GBK（cp936），
    而 `▀`(U+2580) 不在 GBK 里，直接 print 会抛 UnicodeEncodeError。
    """
    encoding = (getattr(sys.stdout, "encoding", "") or "").lower()
    return "utf" not in encoding


def render(code: QRCode, *, full_blocks: bool | None = None) -> str:
    """渲染成终端文本，留 4 模块静默区（扫码器需要）。

    两种模式，都是正方形、都扫得动：
    - **半块字符**（`▀▄█`，默认）：一个字符装上下两个模块，终端里最紧凑，约 30 行高；
    - **整块字符**（`██`/`  `）：每格两个字符、宽高同倍缩放，约 60 行高但更清晰。
      当前终端编码不安全时自动用它（见 `_needs_full_blocks`）。

    这里**不用 ANSI 反色**（`\\x1b[7m`）来省字符：PowerShell 5.1 下的转义支持要看
    具体宿主，可靠的做法是只用字符本身表达黑白。
    """
    if full_blocks is None:
        full_blocks = _needs_full_blocks()

    size = code.size
    span = range(-_QUIET, size + _QUIET)

    def dark(row: int, col: int) -> bool:
        if 0 <= row < size and 0 <= col < size:
            return code.is_dark(row, col)
        return False

    lines: list[str] = []
    if full_blocks:
        lines.append("")
        for row in span:
            lines.append("".join("██" if dark(row, col) else "  " for col in span))
        lines.append("")
        return "\n".join(lines)

    lines.append(" " * (size + _QUIET * 2))
    # 一次吃两行：上半个块 = 上行深/下行浅，下半个块 = 反过来
    for top in range(-_QUIET, size + _QUIET, 2):
        chars = []
        for col in span:
            upper = dark(top, col)
            lower = dark(top + 1, col)
            if upper and lower:
                chars.append("█")
            elif upper:
                chars.append("▀")
            elif lower:
                chars.append("▄")
            else:
                chars.append(" ")
        lines.append("".join(chars))
    lines.append(" " * (size + _QUIET * 2))
    return "\n".join(lines)


def write_png(code: QRCode, path: str, *, module_px: int = 8) -> None:
    """把二维码写成 PNG（纯标准库，手写 PNG 编码）。

    **为什么需要它**：终端里打印二维码看着聪明，实际很脆——Windows 中文环境的控制台
    是 cp936，而 Python 的 stdout 可能是 UTF-8，方块字符一错位就变成 `锟斤拷`，
    用户扫一个乱码图案只会更困惑。PNG 交给系统图片查看器打开，没有任何编码问题。

    顺手也能让二维码印到纸上、贴到墙上。
    """
    size = code.size
    scale = max(1, int(module_px))
    quiet = _QUIET
    side = size + quiet * 2
    pixels = side * scale

    black = b"\x00\x00\x00"
    white = b"\xff\xff\xff"
    quiet_px = white * (quiet * scale)

    rows: list[bytes] = []
    # 上静默区
    rows.extend([b"\x00" + white * pixels] * (quiet * scale))
    for row in range(size):
        line = bytearray(quiet_px)
        for col in range(size):
            line += (black if code.is_dark(row, col) else white) * scale
        line += quiet_px
        packed = b"\x00" + bytes(line)  # 行首 filter byte = 0（None）
        rows.extend([packed] * scale)   # 纵向也放大 scale 倍，保证模块是正方形
    # 下静默区
    rows.extend([b"\x00" + white * pixels] * (quiet * scale))

    raw = b"".join(rows)

    import zlib

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            len(payload).to_bytes(4, "big")
            + tag
            + payload
            + (zlib.crc32(tag + payload) & 0xFFFFFFFF).to_bytes(4, "big")
        )

    header = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", pixels.to_bytes(4, "big") * 2 + bytes([8, 2, 0, 0, 0]))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    with open(path, "wb") as handle:
        handle.write(header)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="生成/打印二维码")
    parser.add_argument("text", help="要编码的内容（URL 等）")
    parser.add_argument("--no-render", action="store_true", help="不打印到终端")
    parser.add_argument("--png", metavar="PATH", help="同时写出 PNG 文件")
    parser.add_argument("--dump", metavar="PATH", help="把模块矩阵导出为 #/. 文本（测试用）")
    args = parser.parse_args(argv)

    code = encode(args.text)

    if args.dump:
        with open(args.dump, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(code.to_text() + "\n")

    if args.png:
        write_png(code, args.png)

    if not args.no_render:
        print(render(code))
    # 只输出 ASCII：这个脚本会被 PowerShell 调用，而 Windows 上"控制台代码页 / Python
    # stdout 编码 / 父进程管道编码"三者经常不一致（实测同一台机器上分别看到 936 与 65001），
    # 中文状态行一旦错位就是乱码。中文提示统一交给调用方（PowerShell 自己 println）去说。
    print(f"[qr] version {code.version}, {code.size}x{code.size} modules, {len(args.text)} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
