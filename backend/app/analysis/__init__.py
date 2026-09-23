"""AI 分析层：全部是纯函数 + 结构化输出，不碰数据库、不碰网络。

为什么强制这个约束（PLAN §9.1 第 2 条）：
M8 的简历诊断、M10 的准备包与拒信归因都要复用同一套"构造 prompt → 调模型 → 规范化输出"的形状；
如果每个功能都自己在路由里拼 prompt，很快就会出现七八套互不一致的写法。
"""

from .rubric import DIMENSIONS, Dimension, build_rubric_prompt, dimension_keys, label_for
from .scoring import (
    DimensionScore,
    Exchange,
    ImprovementItemOut,
    NormalizedReport,
    build_exchanges,
    build_scoring_messages,
    normalize_report,
)

__all__ = [
    "DIMENSIONS",
    "Dimension",
    "DimensionScore",
    "Exchange",
    "ImprovementItemOut",
    "NormalizedReport",
    "build_exchanges",
    "build_rubric_prompt",
    "build_scoring_messages",
    "dimension_keys",
    "label_for",
    "normalize_report",
]
