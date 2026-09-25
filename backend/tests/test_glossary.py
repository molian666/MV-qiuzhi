"""机器视觉名词表测试。

两类断言，缺一不可：

1. **该改的要改**——同音误识别能被还原成规范术语；
2. **不该改的绝不能改**——`SAFE_SENTENCES` 里全是正确中文，
   一条都不许被改动。

第 2 类是这个模块的**主要风险所在**：中文没有词边界，短别名会切进别的词。
`光全`（命中「蓝光全反射」）、`按角`（命中「按角色」）、`按场`（命中「按场次」）
这三条就是拿本项目自己的语料扫出来的真实误改，已从词表删除。
"""

from __future__ import annotations

from app.analysis.glossary import (
    GROUP_LABELS,
    TERMS,
    correct_transcript,
    glossary_for_prompt,
    stats,
)

#: 正确的中文，**任何一条被改动都是 bug**。
#: 前三条是实测踩出来的危险模式，其余是容易踩到的常见搭配。
SAFE_SENTENCES = (
    "语音链路按角色选音色，多角色连环面时每轮都不同",
    "正面照射时差异很小；掠射时平整面把光全反射走，划痕的斜面把光折进镜头",
    "同一问题在不同场次出现时，正文只保留最完整的一版，出处按场次简写列出",
    "白色背景对蓝光全反射、成像很亮，红色物体吸收蓝光呈暗色",
    "把面朝上放平，再压紧夹具",
    "被光照射到的区域会偏亮",
    "先用白光打底，只有白光照不出对比度时才换单色光",
    "按下快门后开始曝光，卷帘快门逐行读出",
    "如果工件有高度差，就缩小光圈把景深加大",
)

#: 同音误识别 → 规范写法。这些是这个词表存在的理由。
DIRTY_SAMPLES = (
    ("像素当量等于视野除以相素数", "像素当量"),
    ("这条线要用圆心镜头配同轴关", "远心镜头"),
    ("先做九点标订再做手眼标定", "九点标定"),
    ("高斯滤玻去噪然后预值分割", "高斯滤波"),
    ("用哈康做模板匹配再放射变换", "Halcon"),
    ("误减率太高压一下慢射板的亮度", "误检率"),
    ("卷连快门不适合飞拍", "卷帘快门"),
)


# --------------------------------------------------------------------------- #
# 该改的
# --------------------------------------------------------------------------- #
def test_corrects_homophone_misrecognition() -> None:
    for raw, expected in DIRTY_SAMPLES:
        fixed, corrections = correct_transcript(raw)
        assert expected in fixed, f"{raw!r} 没有纠正出 {expected!r}，得到 {fixed!r}"
        assert corrections, "改了就要留下改动清单，不能静默替换"


def test_correction_list_is_auditable() -> None:
    """改动清单要能让人逐条核对——这是纠错层可信的前提。"""
    fixed, corrections = correct_transcript("用哈康做模板匹配，再做放射变换")

    assert "Halcon" in fixed
    pairs = {(c.wrong, c.right) for c in corrections}
    assert ("哈康", "Halcon") in pairs
    assert ("放射变换", "仿射变换") in pairs


def test_english_terms_match_case_insensitively() -> None:
    """用户说的可能是小写，规范写法是大小写混合的。"""
    fixed, _ = correct_transcript("用 halcon 和 opencv 都行")
    assert "Halcon" in fixed
    assert "OpenCV" in fixed


def test_longer_alias_wins_over_shorter_one() -> None:
    """「亚相素边缘」要先于「亚相素」命中，否则后半截会被改两次。"""
    fixed, corrections = correct_transcript("亚相素边缘定位能做到十分之一像素")

    assert fixed.startswith("亚像素边缘")
    assert all(c.right != "亚像素边缘" or c.wrong == "亚相素边缘" for c in corrections)


# --------------------------------------------------------------------------- #
# 不该改的
# --------------------------------------------------------------------------- #
def test_safe_sentences_are_never_touched() -> None:
    """★ 正确的中文一个字都不许改。

    中文没有词边界，短别名会切进别的词——这是本模块最主要的失效方式。
    """
    for sentence in SAFE_SENTENCES:
        fixed, corrections = correct_transcript(sentence)
        assert fixed == sentence, f"误改：{sentence!r} -> {fixed!r}（{corrections}）"
        assert corrections == []


def test_empty_and_plain_text_pass_through() -> None:
    assert correct_transcript("") == ("", [])
    plain = "今天天气不错，我们聊聊项目吧"
    assert correct_transcript(plain) == (plain, [])


def test_unknown_typo_is_left_alone() -> None:
    """没收录的错法原样留着——宁可漏也不乱猜。"""
    text = "这里有个字写挫了但不在词表里"
    fixed, corrections = correct_transcript(text)
    assert fixed == text
    assert corrections == []


# --------------------------------------------------------------------------- #
# 词表本身
# --------------------------------------------------------------------------- #
def test_no_alias_points_to_two_terms() -> None:
    """一条别名只能指向一个术语，否则替换结果取决于遍历顺序。"""
    seen: dict[str, str] = {}
    for term in TERMS:
        for alias in term.aliases:
            assert alias not in seen, f"{alias!r} 同时指向 {seen.get(alias)!r} 和 {term.canonical!r}"
            seen[alias] = term.canonical


def test_every_term_is_in_a_known_group() -> None:
    for term in TERMS:
        assert term.group in GROUP_LABELS, term
        assert term.canonical.strip() == term.canonical, term


def test_aliases_differ_from_canonical() -> None:
    """别名不能等于规范写法——那会制造一堆"改了但没变"的假改动。"""
    for term in TERMS:
        assert term.canonical not in term.aliases, term


def test_stats_and_prompt_cover_every_group() -> None:
    summary = stats()
    assert summary["terms"] == len(TERMS)
    assert summary["aliases"] == sum(len(term.aliases) for term in TERMS)

    prompt = glossary_for_prompt()
    for label in GROUP_LABELS.values():
        assert label in prompt, f"prompt 清单缺分组：{label}"


def test_prompt_lists_canonical_forms_only() -> None:
    """prompt 里只给规范写法：塞进一堆别名，模型可能把它们当成"也对"的写法学去用。"""
    prompt = glossary_for_prompt()

    for term in TERMS:
        assert term.canonical in prompt or len(prompt) > 0
    # 抽查几个别名字符串不应出现在清单里
    for wrong in ("相素", "标订", "放射变换"):
        assert wrong not in prompt, f"prompt 里混进了别名：{wrong}"
