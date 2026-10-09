"""二维码编码器的验证测试（`backend/tools/qr.py`）。

**为什么这些测试值得写这么细**：二维码编码器是自己实现的（见 `tools/qr.py` 的模块说明），
而"自己实现的编码器"最大的风险不是崩，是**静默编错**——矩阵看着有模有样、扫出来却是乱码。
所以这里不只是断言"形状对不对"，而是**把编码结果解回来**：

1. `_decode` 是本文件里的一个独立解码器，走的是与编码器**相反**的路径
   （读格式信息 → 去掩码 → 反之字形读位 → 解交织 → 拆位流）；
2. 双向往返一致，才说明位流、交织、掩码、格式信息四件事同时对；
3. 纠错码字单独与编码器算出的对照（解码器不实现纠错，但可以比对）。

注意：解码器**不做纠错译码**（那需要另一套独立实现，收益低）。
它读出来的格式信息直接取低 3 位当掩码——不纠错，所以一旦编码器的格式信息错了，
掩码就会取错、解出来的内容立刻变成乱码而失败。这正是我们想要的把关强度。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from tools import qr  # noqa: E402  （tools 不是包，靠上面的 sys.path 注入）


# --------------------------------------------------------------------------- #
# 独立解码器（只覆盖编码器用到的子集：字节模式、纠错等级 M、版本 1-10）
# --------------------------------------------------------------------------- #


def _read_format(matrix: list[list[bool]]) -> tuple[int, int]:
    """读左上角那份格式信息的 15 位，返回 (纠错等级, 掩码)。"""
    size = len(matrix)

    def bit(row: int, col: int) -> int:
        return int(matrix[row][col])

    # 与 _place_format 对称的读取顺序
    value = 0
    positions = (
        [(8, index) for index in range(6)]
        + [(8, 7), (8, 8), (7, 8)]
        + [(14 - index, 8) for index in range(9, 15)]
    )
    for index, (row, col) in enumerate(positions):
        value |= bit(row, col) << index

    assert 0 <= value < (1 << 15)
    # 直接反异或取数据位，不做 BCH 纠错（见模块说明）
    data = (value ^ 0x5412) >> 10
    level = (data >> 3) & 0b11
    mask = data & 0b111
    return level, mask


def _read_version(matrix: list[list[bool]], version: int) -> int:
    """读右上角那份版本信息，还原出版本号。"""
    size = len(matrix)
    value = 0
    for index in range(18):
        row, col = divmod(index, 3)
        value |= int(matrix[col][size - 11 + row]) << index
    # 低 12 位是 BCH 校验，高 6 位是版本号
    assert value & 0xFFFFF == (version << 12) | (qr._version_bits(version) & 0xFFF)
    return value >> 12


def _deinterleave(codewords: list[int], version: int) -> list[int]:
    """把交织后的码字还原成数据码字序列。"""
    ecc_per_block, blocks1, data1, blocks2, data2 = qr._ECC_M[version]
    sizes = [data1] * blocks1 + [data2] * blocks2
    blocks: list[list[int]] = [[] for _ in sizes]

    cursor = 0
    for index in range(max(sizes)):
        for block_index, block_size in enumerate(sizes):
            if index < block_size:
                blocks[block_index].append(codewords[cursor])
                cursor += 1
    return [value for block in blocks for value in block]


def _decode(matrix: list[list[bool]], version: int) -> bytes:
    """从模块矩阵还原出原始字节。任何一步不对都会 assert 失败。"""
    size = len(matrix)
    level, mask = _read_format(matrix)
    assert level == 0b00, "纠错等级应为 M（二进制 00）"
    if version >= 7:
        assert _read_version(matrix, version) == version

    reserved = qr._reserved(size, version)
    mask_fn = qr._MASKS[mask]

    bits: list[int] = []
    column = size - 1
    upward = True
    while column > 0:
        if column == 6:
            column -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for offset in (0, 1):
                col = column - offset
                if reserved[row][col]:
                    continue
                value = matrix[row][col]
                if mask_fn(row, col):
                    value = not value
                bits.append(int(value))
        upward = not upward
        column -= 2

    codewords = [
        int("".join(str(bit) for bit in bits[start:start + 8]), 2)
        for start in range(0, len(bits) - 7, 8)
    ]
    data = _deinterleave(codewords, version)

    # 拆位流：模式(4) + 长度 + 数据
    stream: list[int] = []
    for codeword in data:
        for shift in range(7, -1, -1):
            stream.append((codeword >> shift) & 1)

    assert stream[:4] == [0, 0, 0, 1] or stream[:4] == [0, 1, 0, 0], "模式指示符应为字节模式"
    count_bits = 8 if version <= 9 else 16
    length = 0
    for index in range(4, 4 + count_bits):
        length = (length << 1) | stream[index]

    payload = bytearray()
    for byte_index in range(length):
        value = 0
        for offset in range(8):
            value = (value << 1) | stream[4 + count_bits + byte_index * 8 + offset]
        payload.append(value)
    return bytes(payload)


# --------------------------------------------------------------------------- #
# 往返测试
# --------------------------------------------------------------------------- #

URLS = [
    "https://mianshi-pc.tailnet-name.ts.net",          # 典型的 Tailscale 长域名
    "https://mianshi-pc.tailnet-name.ts.net/#report",  # 带路径与锚点
    "http://192.168.1.23:8443",                        # 局域网退路
    "https://a.b.ts.net",                              # 短内容 → 低版本
    "https://中文域名试探.tailnet-name.ts.net",          # 非 ASCII 走 UTF-8 字节
]


@pytest.mark.parametrize("text", URLS)
def test_round_trip_decodes_to_original(text: str) -> None:
    code = qr.encode(text)
    assert _decode(code.modules, code.version) == text.encode("utf-8")


def test_version_matches_content_length() -> None:
    short = qr.encode("https://a.b.ts.net")
    long = qr.encode("https://" + "x" * 150 + ".ts.net")
    assert short.version < long.version
    assert short.size == 17 + 4 * short.version
    assert long.size == 17 + 4 * long.version


#: 字节模式 + 纠错等级 M 的标准容量表（ISO/IEC 18004 表 7）。
#: **独立钉在这里**，不复用 `tools/qr.py` 里那张表——否则两边一起错就发现不了。
BYTE_CAPACITY_M = {1: 14, 2: 26, 3: 42, 4: 62, 5: 84,
                   6: 106, 7: 122, 8: 152, 9: 180, 10: 213}


def test_byte_capacity_table_matches_the_standard() -> None:
    for version, expected in BYTE_CAPACITY_M.items():
        assert qr.max_bytes(version) == expected, f"版本 {version} 容量对不上规范"
        # 数据码字数必须够放下"头部 + 这么多字节"
        header_bits = 4 + (8 if version <= 9 else 16)
        assert qr.data_capacity(version) * 8 >= header_bits + expected * 8


def test_version_boundaries_follow_the_byte_capacity_table() -> None:
    """每个版本的容量边界前后各测一次：够→该版本，多一字节→升版本。"""
    for version in range(1, 10):
        capacity = qr.max_bytes(version)
        assert qr.choose_version(capacity) == version
        assert qr.choose_version(capacity + 1) == version + 1


def test_longest_supported_content_round_trips() -> None:
    """版本 10 的容量上限要刚好装下，多一字节要明确报错（而不是悄悄截断）。"""
    limit = qr.max_bytes(10)
    assert limit == BYTE_CAPACITY_M[10]

    text = "h" * limit
    code = qr.encode(text)
    assert code.version == 10
    assert _decode(code.modules, code.version) == text.encode("utf-8")

    with pytest.raises(ValueError, match="太长"):
        qr.encode("h" * (limit + 1))


def test_multi_block_versions_round_trip() -> None:
    """版本 8-10 是**两组不同大小的块**，交织最容易写错，逐个版本都要过。"""
    for version in range(8, 11):
        text = "https://" + "a" * (qr.max_bytes(version) - 9)
        assert qr.choose_version(len(text.encode("utf-8"))) == version
        code = qr.encode(text)
        assert code.version == version
        assert _decode(code.modules, code.version) == text.encode("utf-8")


def test_too_long_content_is_rejected_not_truncated() -> None:
    with pytest.raises(ValueError, match="太长"):
        qr.encode("x" * 400)


# --------------------------------------------------------------------------- #
# 结构自检（这些位置规范写死，错了任何扫码器都读不出来）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("text", URLS)
def test_finder_patterns_are_intact(text: str) -> None:
    code = qr.encode(text)
    size = code.size

    for top, left in ((0, 0), (0, size - 7), (size - 7, 0)):
        # 外框全深
        for index in range(7):
            assert code.is_dark(top, left + index), f"定位图形外框 (row={top + 0})"
            assert code.is_dark(top + 6, left + index)
            assert code.is_dark(top + index, left)
            assert code.is_dark(top + index, left + 6)
        # 3x3 实心核
        for row in range(2, 5):
            for col in range(2, 5):
                assert code.is_dark(top + row, left + col)
        # 内外环之间的白环
        for index in range(1, 6):
            assert not code.is_dark(top + 1, left + index)
            assert not code.is_dark(top + 5, left + index)


@pytest.mark.parametrize("text", URLS)
def test_separators_and_dark_module(text: str) -> None:
    code = qr.encode(text)
    size = code.size

    for index in range(8):
        assert not code.is_dark(7, index), "左上分隔符"
        assert not code.is_dark(index, 7), "左上分隔符"
        assert not code.is_dark(7, size - 1 - index), "右上分隔符"
        assert not code.is_dark(size - 1 - index, 7), "左下分隔符"

    assert code.is_dark(size - 8, 8), "规范固定的深色模块"


@pytest.mark.parametrize("text", URLS)
def test_timing_patterns_alternate(text: str) -> None:
    code = qr.encode(text)
    for index in range(8, code.size - 8):
        assert code.is_dark(6, index) == (index % 2 == 0)
        assert code.is_dark(index, 6) == (index % 2 == 0)


def test_alignment_patterns_are_intact() -> None:
    """版本 2 起有校正图形，形状是**5x5：外环深、内环白、中心深**。

    两个容易搞错的地方（我第一版测试就踩了）：
    1. 三个角上的中心要跳过——那里已经是定位图形；
    2. 中心周围 **8 格是白的**，不是深色实心 3x3。断言写错时失败信息会把
       中心坐标报出来，看起来像"中心不是深色"，实际挂在 (中心-1, 中心-1) 上。
    """
    code = qr.encode("https://" + "b" * 60 + ".ts.net")
    assert code.version >= 2
    size = code.size
    skipped = {(6, 6), (6, size - 7), (size - 7, 6)}

    checked = 0
    for row_center in qr._ALIGN[code.version]:
        for col_center in qr._ALIGN[code.version]:
            if (row_center, col_center) in skipped:
                continue
            for row in range(-2, 3):
                for col in range(-2, 3):
                    edge = max(abs(row), abs(col))
                    expected = edge != 1  # 外环(2)深 / 内环(1)白 / 中心(0)深
                    assert code.is_dark(row_center + row, col_center + col) == expected, (
                        f"校正图形 (中心 row={row_center}, col={col_center}) "
                        f"偏移 ({row},{col}) 应为 {'深' if expected else '白'}"
                    )
            checked += 1
    assert checked >= 1, "版本 2 起至少要有一个非角上的校正图形"


def test_alignment_pattern_cells_are_reserved_and_not_masked() -> None:
    """校正图形占的 5x5 必须全在保留区里，否则数据或掩码会压坏它。"""
    for version in range(2, 11):
        size = 17 + 4 * version
        reserved = qr._reserved(size, version)
        for row_center in qr._ALIGN[version]:
            for col_center in qr._ALIGN[version]:
                if (row_center, col_center) in {(6, 6), (6, size - 7), (size - 7, 6)}:
                    continue
                for row in range(-2, 3):
                    for col in range(-2, 3):
                        assert reserved[row_center + row][col_center + col], (
                            f"v{version} 校正图形 ({row_center},{col_center}) "
                            f"偏移 ({row},{col}) 未保留"
                        )


# --------------------------------------------------------------------------- #
# 纠错码字与掩码选择
# --------------------------------------------------------------------------- #


def test_ecc_codewords_are_consistent_with_encoder() -> None:
    """解码器不做纠错译码，但要确认交织里真的带上了正确的纠错码字。"""
    text = "https://mianshi-pc.tailnet-name.ts.net"
    version = qr.choose_version(len(text.encode("utf-8")))
    data = qr._bit_stream(text.encode("utf-8"), version)

    ecc_per_block, blocks1, data1, blocks2, data2 = qr._ECC_M[version]
    sizes = [data1] * blocks1 + [data2] * blocks2
    cursor = 0
    expected_ecc: list[int] = []
    for block_size in sizes:
        expected_ecc.extend(qr._ecc(data[cursor:cursor + block_size], ecc_per_block))
        cursor += block_size

    interleaved = qr._interleave(data, version)
    assert interleaved[len(data):] == expected_ecc


def test_mask_choice_minimises_penalty() -> None:
    """选中的掩码必须是 8 个里惩罚分最低的（否则白拿 0 分）。"""
    text = "https://mianshi-pc.tailnet-name.ts.net"
    code = qr.encode(text)
    version = code.version
    payload = text.encode("utf-8")
    codewords = qr._interleave(qr._bit_stream(payload, version), version)

    base = qr._new_matrix(code.size)
    qr._place_patterns(base, version)
    qr._place_data(base, codewords, version)

    scores = []
    for mask in range(8):
        candidate = qr._apply_mask(base, version, mask)
        qr._place_format(candidate, mask)
        scores.append(qr._penalty(candidate))

    chosen = _read_format(code.modules)[1]
    assert scores[chosen] == min(scores)
    # 掩码确实生效了：至少有一个掩码给出的是不同的矩阵
    assert len({tuple(map(tuple, qr._apply_mask(base, version, mask))) for mask in range(8)}) > 1


def test_format_info_bch_is_self_consistent() -> None:
    """格式信息的 BCH 校验位要能自校验。"""
    for mask in range(8):
        bits = qr._format_bits(mask)
        assert bits < (1 << 15)
        # 反异或后应能被生成多项式 0x537 整除（BCH(15,5)）
        value = bits ^ 0x5412
        remainder = value
        for shift in range(14, 9, -1):
            if remainder & (1 << shift):
                remainder ^= 0x537 << (shift - 10)
        assert remainder == 0, f"mask={mask} 的格式信息不是合法 BCH 码字"


# --------------------------------------------------------------------------- #
# 终端渲染：两种模式都必须**正方形**、留静默区、只用实心块
# --------------------------------------------------------------------------- #


def test_render_modes_are_square() -> None:
    """两种模式都必须是正方形。

    **这条是刚需**：扫码器认的是模块的方形比例。第一版整块模式写成"深色 `##`、
    浅色两个空格"，等于每格宽 2、高 1——横向被拉长一倍，印出来就是个扁码。
    """
    code = qr.encode("https://mianshi-pc.tailnet-name.ts.net")

    # 整块模式：每模块 2 字符，宽 = 2*(size+8)，模块行数 = size+8
    dense = qr.render(code, full_blocks=True).split("\n")
    assert len(dense) == code.size + 8 + 2, "首尾各一个分隔空行"
    assert dense[0] == "" and dense[-1] == ""
    body = dense[1:-1]
    assert {len(line) for line in body} == {2 * (code.size + 8)}
    # 宽高的"模块数"必须相等：字符宽 2 → 字符高 1，模块数比 = 1:1
    assert len(body) == (len(body[0]) // 2), "整块模式的模块宽高必须相等"

    # 半块模式：一字符装两模块，所以行数是模块数的一半（向上取整），宽度等于模块数
    half = qr.render(code, full_blocks=False).split("\n")
    assert {len(line) for line in half} == {code.size + 8}
    assert len(half) - 2 == (code.size + 8 + 1) // 2


def test_render_uses_solid_blocks_only() -> None:
    """只用 `█` 表达深色——`#` 的点阵太稀疏、模块之间留缝，扫不出来。"""
    code = qr.encode("https://a.b.ts.net")
    dense = qr.render(code, full_blocks=True)
    assert "#" not in dense
    assert set(dense) <= {"█", " ", "\n"}

    half = qr.render(code, full_blocks=False)
    assert set(half) <= {"█", "▀", "▄", " ", "\n"}


def test_render_preserves_the_quiet_zone() -> None:
    """静默区必须是白的：扫码器靠它把码从背景里切出来。"""
    code = qr.encode("https://a.b.ts.net")
    dense = qr.render(code, full_blocks=True).split("\n")
    body = dense[1:-1]
    assert len(body) == code.size + 8
    quiet = 4 * 2  # 每边 4 模块，每模块 2 字符

    assert all(set(line[:quiet]) == {" "} for line in body), "左侧静默区"
    assert all(set(line[-quiet:]) == {" "} for line in body), "右侧静默区"
    assert set("".join(body[:4])) == {" "}, "上侧静默区"
    assert set("".join(body[-4:])) == {" "}, "下侧静默区"
    # 中间必须有内容，否则上面几条断言等于没测
    assert set("".join(body[4:-4])) == {" ", "█"}


def test_terminal_encoding_detection() -> None:
    """检测逻辑本身要成立：utf-8 用半块（更紧凑），非 utf 退整块。"""
    import io

    original = sys.stdout
    try:
        sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")  # type: ignore[assignment]
        assert qr._needs_full_blocks() is False
        sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="gbk")  # type: ignore[assignment]
        assert qr._needs_full_blocks() is True
    finally:
        sys.stdout = original


def test_reserved_area_matches_placement() -> None:
    """保留区必须把定位/校正/定位图形/格式信息全部盖住，否则数据会压坏功能图形。"""
    for version in range(1, 11):
        size = 17 + 4 * version
        reserved = qr._reserved(size, version)

        modules = qr._new_matrix(size)
        qr._place_patterns(modules, version)

        # 所有功能图形的深色模块，都必须在保留区里
        for row in range(size):
            for col in range(size):
                if modules[row][col]:
                    assert reserved[row][col], f"v{version} ({row},{col}) 功能图形未保留"
        # 格式信息区也必须在保留区里
        for row, col in [(8, index) for index in range(9)] + [(index, 8) for index in range(9)]:
            assert reserved[row][col]
        assert reserved[size - 8][8]
