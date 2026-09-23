"""评分维度定义（Rubric）。

写死在代码里而不是让模型自由发挥，原因很实际：
- 同一场面试重复评分要**可比**（不然趋势图没意义）；
- 每个维度都要能落到"下一步该练什么"，模型自创维度做不到这点；
- 维度是产品决策，不该每次调 prompt 都变。

每维给 1 / 3 / 5 三个锚点，中间分让模型按锚点插值——比写满 5 档更省 token，
而且实测锚点越具体，模型打分越稳（不会一律给 4 分）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Dimension:
    key: str
    label: str
    focus: str
    anchors: tuple[str, str, str]  # 1 分 / 3 分 / 5 分的样子


DIMENSIONS: tuple[Dimension, ...] = (
    Dimension(
        key="relevance",
        label="切题度",
        focus="是否正面回答了被问的问题，有没有跑题或答非所问",
        anchors=(
            "答非所问，或者只把问题里的词重复一遍、不展开",
            "大体对题，但有一部分在讲别的东西",
            "正面命中问题，并明确呼应了提问的落点",
        ),
    ),
    Dimension(
        key="structure",
        label="结构",
        focus="回答是否有结构：结论先行 / STAR / 总分总",
        anchors=(
            "想到哪说到哪，听不出层次",
            "有结构但松散，中间会断线",
            "结论先行、层次清晰，听完能复述出骨架",
        ),
    ),
    Dimension(
        key="quantification",
        label="信息密度与量化",
        focus="有没有数字、指标、可验证的事实；信息是不是空话",
        anchors=(
            "全是形容词和职责描述，没有一个可验证的点",
            "有部分事实，但关键结果没有量化",
            "关键结果都有数字或可验证的事实支撑",
        ),
    ),
    Dimension(
        key="depth",
        label="技术深度与正确性",
        focus="概念是否准确，是否讲到边界、取舍、失败与代价",
        anchors=(
            "概念有明显错误，或者只会背定义",
            "结论正确但停留在表面，没有取舍分析",
            "讲到原理、边界与权衡，并说明为什么这么选",
        ),
    ),
    Dimension(
        key="delivery",
        label="表达与流畅",
        focus="语速、口头禅、长时间停顿、啰嗦与重复",
        anchors=(
            "大量口头禅与停顿，听起来吃力",
            "基本流畅，个别地方绕",
            "表达干净、节奏舒服，几乎没有填充词",
        ),
    ),
    Dimension(
        key="time_control",
        label="时间控制",
        focus="单题回答时长是否落在合理区间（项目/技术题约 1–3 分钟）",
        anchors=(
            "明显失当：过短（信息不足）或过长（绕不出来）",
            "略有偏差但可接受",
            "长短得当，该展开的展开、该收的收住",
        ),
    ),
    Dimension(
        key="initiative",
        label="主动性",
        focus="反问质量、是否主动补充关键信息、薪资与规划的沟通",
        anchors=(
            "没有反问，或只问「加不加班」这类无效问题",
            "有反问但偏通用，没有结合岗位",
            "反问具体且体现对岗位的理解，还主动补了加分信息",
        ),
    ),
)


def dimension_keys() -> tuple[str, ...]:
    return tuple(dimension.key for dimension in DIMENSIONS)


def label_for(key: str) -> str:
    for dimension in DIMENSIONS:
        if dimension.key == key:
            return dimension.label
    return key


def is_valid_dimension(key: str) -> bool:
    return key in dimension_keys()


def build_rubric_prompt() -> str:
    """把维度表渲染成给模型的评分说明。"""
    blocks: list[str] = []
    for index, dimension in enumerate(DIMENSIONS, start=1):
        one, three, five = dimension.anchors
        blocks.append(
            f"{index}. {dimension.label}（key = {dimension.key}）\n"
            f"   看什么：{dimension.focus}\n"
            f"   1 分：{one}\n"
            f"   3 分：{three}\n"
            f"   5 分：{five}"
        )
    return "评分维度（每项 1–5 分，必须给出评分理由）：\n\n" + "\n\n".join(blocks)
