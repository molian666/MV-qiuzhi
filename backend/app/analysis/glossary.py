"""机器视觉名词表：给语音转写兜底，也让模型点评时说对术语。

**为什么需要它**：语音识别对专业术语的误识别几乎都是**同音替换**——
「亚像素」→「亚相素」、「同轴光」→「同轴关」、「九点标定」→「九点标订」。
这些词在通用语料里极罕见，ASR 的语言模型自然把它们拉向更常见的同音词。
打字粘贴的文稿也会有同类错字（「像素」→「像数」）。

覆盖相机与成像 / 镜头与光学 / 光源与打光 / 标定与机器人 / 图像处理与算子 /
视觉软件平台 / 指标与精度七组，与 `vision/bank_*.py` 的题目分类大体对齐。

**边界（很重要）**：这里**只做术语级的精确替换**，不改写句子、不做猜测。
每条别名都必须是"中文里不成立的写法"，而且每处改动都进 `Correction` 清单，
用户能逐条核对。一个"顺手把话改通顺"的纠错层，出错时没人能发现——
这和 `skill_dict.py` 不用分词器是同一个理由：宁可笨，也要**可解释、可复现**。

**收录标准**：别名要么是同音/近音（语音误识别），要么是常见错字（打字误写）。
拿不准的一律不收——错替比漏替更糟，它会把用户说对的话改错。

**两字别名要格外小心**（中文没有词边界，短别名会切进别的词）：
- 「光全」会命中「蓝**光全**反射」
- 「按角」会命中「**按角**色选音色」
- 「被光」会命中「**被光**照到」
这几条都是实测踩出来的，所以判据是"**这个字串会不会是更长正常词的一部分**"，
而不是"看起来像不像错字"。测试里 `SAFE_SENTENCES` 专门钉住这类句子不被改动。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

GROUP_LABELS: dict[str, str] = {
    "camera": "相机与成像",
    "lens": "镜头与光学",
    "light": "光源与打光",
    "calib": "标定与机器人",
    "algo": "图像处理与算子",
    "software": "视觉软件平台",
    "metric": "指标与精度",
}


@dataclass(frozen=True, slots=True)
class Term:
    """一个规范术语，以及它常见被写错的样子。"""

    canonical: str
    aliases: tuple[str, ...]
    group: str
    note: str = ""


TERMS: tuple[Term, ...] = (
    # ---------------- 相机与成像 ----------------
    Term("像素当量", ("像数当量", "象素当量", "相素当量", "像素当亮", "像素档量"), "camera",
         "「当量」常被听成「当亮／档量」"),
    Term("像素", ("相素", "象素", "像数"), "camera",
         "单说「像素」也会被听错（视野除以相素数）；这几个组合都不是正常词"),
    Term("亚像素", ("亚相素", "亚象素", "亚像数", "压像素"), "camera",
         "「亚」与「压」同音，三字组合都不是正常词"),
    Term("面阵相机", ("面振相机", "面正相机"), "camera"),
    Term("线阵相机", ("线振相机", "线性相机"), "camera",
         "「线阵」易被听成「线性」，但线性是另一个意思"),
    Term("全局快门", ("全景快门", "全局快们"), "camera"),
    Term("卷帘快门", ("卷连快门", "卷链快门"), "camera"),
    Term("靶面", ("靶免",), "camera"),
    Term("帧率", ("真率", "帧绿"), "camera"),
    Term("行频", ("形频", "行平"), "camera"),
    Term("硬触发", ("硬处发", "硬触发信号"), "camera"),
    Term("软触发", ("软处发",), "camera"),
    Term("编码器", ("编玛器", "变码器"), "camera"),
    Term("黑电平", ("黑电瓶",), "camera"),

    # ---------------- 镜头与光学 ----------------
    Term("远心镜头", ("圆心镜头", "远芯镜头", "远心境头"), "lens",
         "「远心」被听成「圆心」是最典型的一例"),
    Term("物方远心", ("物防远心", "物方圆心"), "lens"),
    Term("像方远心", ("像防远心", "像方圆心"), "lens"),
    Term("双远心", ("双圆心",), "lens"),
    Term("景深", ("景伸", "井深"), "lens",
         "「井深」只在钻井语境成立，这里都是景深"),
    Term("光圈", ("光圈值",), "lens"),
    Term("弥散圆", ("迷散圆", "弥散园"), "lens"),
    Term("工作距离", ("工做距离", "工作聚集"), "lens"),
    Term("放大倍率", ("放大倍绿", "放大倍律"), "lens"),
    Term("视场角", ("市场角", "视长角"), "lens"),
    Term("畸变", ("机变", "畸变率"), "lens"),
    Term("暗角", (), "lens", "只进术语清单供 prompt 用：没把握的别名一律不收"),
    Term("法兰距", ("法蓝距", "法兰据"), "lens"),

    # ---------------- 光源与打光 ----------------
    Term("同轴光", ("同轴关", "同轴管", "铜轴光"), "light",
         "「光」被听成「关」在词组末尾极常见"),
    Term("环形光", ("环型光", "环行光"), "light"),
    Term("条形光", ("条型光", "条行光"), "light"),
    Term("圆顶光", ("圆顶管", "原顶光"), "light"),
    Term("背光", ("背广",), "light"),
    Term("明场", ("名场", "明厂"), "light"),
    Term("暗场", ("暗厂",), "light"),
    Term("漫射", ("慢射", "漫社"), "light"),
    Term("偏振片", ("偏正片", "偏震片"), "light"),
    Term("菲涅尔", ("菲尼尔", "飞涅尔"), "light"),
    Term("频闪", ("平闪", "频山"), "light"),

    # ---------------- 标定与机器人 ----------------
    Term("九点标定", ("九点标订", "九点标锭", "九点表定"), "calib",
         "「定」被听成「订」几乎每次都会发生"),
    Term("十二点标定", ("十二点标订", "12点标定"), "calib"),
    Term("手眼标定", ("手眼标订", "手演标定"), "calib"),
    Term("眼在手上", ("演在手上", "眼在首上"), "calib"),
    Term("眼在手外", ("演在手外", "眼在手卖"), "calib"),
    Term("旋转中心", ("选转中心", "旋转衷心"), "calib"),
    Term("仿射变换", ("放射变换", "仿设变换"), "calib",
         "「仿射」被听成「放射」是最典型的一例"),
    Term("透视变换", ("透世变换", "透射变换"), "calib"),
    Term("齐次变换", ("其次变换", "奇次变换"), "calib"),
    Term("棋盘格", ("棋牌格", "其盘格"), "calib"),
    Term("重复定位精度", ("重复定为精度", "重复定位经度"), "calib"),
    Term("机器人引导", ("机器人引倒", "机器人隐导"), "calib"),

    # ---------------- 图像处理与算子 ----------------
    Term("高斯滤波", ("高斯滤玻", "高斯绿波"), "algo"),
    Term("中值滤波", ("中值滤玻", "中直滤波"), "algo"),
    Term("均值滤波", ("军值滤波", "均值绿波"), "algo"),
    Term("双边滤波", ("双变滤波",), "algo"),
    Term("形态学", ("型态学", "行态学"), "algo"),
    Term("开运算", ("开运送", "开运算子"), "algo"),
    Term("闭运算", ("必运算", "闭运送"), "algo"),
    Term("腐蚀", ("府蚀", "负蚀"), "algo"),
    Term("膨胀", ("彭涨", "膨涨"), "algo"),
    Term("阈值分割", ("预值分割", "阈值分歌"), "algo"),
    Term("二值化", ("二植化", "二直化"), "algo"),
    Term("连通域", ("联通域", "连通玉"), "algo",
         "「连通域」是术语，`联通` 是运营商名"),
    Term("分水岭", ("分水领", "分水灵"), "algo"),
    Term("模板匹配", ("模版匹配", "摸板匹配"), "algo"),
    Term("金字塔", ("金自塔", "金字答"), "algo"),
    Term("边缘提取", ("边缘题取", "边沿提取"), "algo",
         "「边沿」在别的语境成立，但图像处理里是「边缘」"),
    Term("灰度直方图", ("灰度植方图", "回度直方图"), "algo"),
    Term("特征点", ("特征典", "凸征点"), "algo"),
    Term("亚像素边缘", ("亚相素边缘", "压像素边缘"), "algo"),

    # ---------------- 视觉软件平台 ----------------
    Term("Halcon", ("halcon", "哈康", "哈尔康", "哈儿康", "Hal-con"), "software",
         "英文术语被音译回去是常见误识别"),
    Term("VisionPro", ("vision pro", "visionpro", "威视pro", "visiopro"), "software"),
    Term("VisionMaster", ("vision master", "visionmaster", "威视master"), "software"),
    Term("OpenCV", ("open cv", "opencv", "欧朋cv"), "software"),
    Term("PMAlign", ("pm align", "pmalign", "批爱姆align"), "software"),
    Term("Calib", ("calib", "calibration工具"), "software"),

    # ---------------- 指标与精度 ----------------
    Term("节拍", ("接拍", "节排"), "metric"),
    Term("良率", ("凉率", "良绿"), "metric"),
    Term("误检率", ("误减率", "误检绿"), "metric"),
    Term("漏检率", ("漏减率", "漏检绿"), "metric"),
    Term("重复性", ("从复性", "重复型"), "metric"),
    Term("公差", ("工差",), "metric"),
    Term("亚像素精度", ("亚相素精度", "压像素精度"), "metric"),
)


#: 别名 → 规范写法。同一别名出现在多个术语下时以前者为准（构建时能查出来）。
_ALIAS_TO_CANONICAL: dict[str, str] = {}
_AMBIGUOUS: list[str] = []
for _term in TERMS:
    for _alias in _term.aliases:
        if _alias in _ALIAS_TO_CANONICAL and _ALIAS_TO_CANONICAL[_alias] != _term.canonical:
            _AMBIGUOUS.append(_alias)
        _ALIAS_TO_CANONICAL.setdefault(_alias, _term.canonical)

if _AMBIGUOUS:  # pragma: no cover - 词表写错时在导入期就炸，别等运行时
    raise RuntimeError(f"这些别名指向了两个不同术语，必须消歧：{sorted(set(_AMBIGUOUS))}")


def _has_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


#: 一份编译好的替换表。**按别名长度降序**：长别名先替换，
#: 免得短别名先把长别名的一部分改掉（「亚相素边缘」要先于「亚相素」命中）。
_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (
        re.compile(
            re.escape(alias),
            # 英文术语（Halcon / OpenCV）大小写不敏感，中文部分没有大小写概念
            0 if _has_cjk(alias) else re.IGNORECASE,
        ),
        canonical,
    )
    for alias, canonical in sorted(
        _ALIAS_TO_CANONICAL.items(), key=lambda item: len(item[0]), reverse=True
    )
)


@dataclass(frozen=True, slots=True)
class Correction:
    """一处改动。保留它，是为了让"纠错"这件事可核对而不是黑箱。"""

    wrong: str
    right: str


def correct_transcript(text: str) -> tuple[str, list[Correction]]:
    """把转写里已知的术语误识别改回规范写法。

    返回 `(纠正后的文本, 改动清单)`。**只替换词表里精确列出的别名**，
    不做任何推测性改写——没收录的错法就原样留着，宁可漏也不乱改。

    改动清单是有意返回的：语音转写是"要送进面试环节的原始素材"，
    用户有权知道系统改了哪几个字。
    """
    if not text:
        return text, []

    corrections: list[Correction] = []
    result = text
    for pattern, canonical in _REPLACEMENTS:
        if not pattern.search(result):
            continue
        # 记下实际命中的原文（大小写可能与词表里不同）
        hits = {match.group(0) for match in pattern.finditer(result)}
        for hit in sorted(hits):
            if hit != canonical:
                corrections.append(Correction(wrong=hit, right=canonical))
        result = pattern.sub(canonical, result)

    return result, corrections


def glossary_for_prompt(*, groups: tuple[str, ...] | None = None, limit: int = 60) -> str:
    """给模型看的术语清单（一行一个，按分组）。

    只列**规范写法**，不带别名——prompt 里塞进几百个别名既浪费 token，
    又可能让模型把它们当成"也是对的写法"学去用。
    """
    chosen = [term for term in TERMS if groups is None or term.group in groups]
    lines: list[str] = []
    for key, label in GROUP_LABELS.items():
        names = [term.canonical for term in chosen if term.group == key]
        if names:
            lines.append(f"- {label}：{'、'.join(names)}")
    return "\n".join(lines[:limit]) if lines else ""


def stats() -> dict[str, object]:
    """名词表概况，界面上用来告诉用户"覆盖了哪些"。"""
    by_group = {
        key: sum(1 for term in TERMS if term.group == key) for key in GROUP_LABELS
    }
    return {
        "terms": len(TERMS),
        "aliases": sum(len(term.aliases) for term in TERMS),
        "groups": [{"key": key, "label": GROUP_LABELS[key], "count": by_group[key]}
                   for key in GROUP_LABELS],
    }
