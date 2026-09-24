"""机器视觉面试题库（M13 的内容主体）。

这个包只负责**内容**——题、答案、要点，以及把内容播种进 `question_bank` 表。
表结构复用 M0 就预留好的 `QuestionBank`（`sample_answer` / `reference_points`
正是为这个准备的），所以不需要新表、也不需要迁移。

分三个方向，对应机器视觉行业里三类真实岗位：

- `bank_development.py`：**开发工程师**——算法选型与实现、底层算子、性能、标定数学；
- `bank_application.py`：**应用工程师**——相机/镜头/光源选型、精度评估、方案取舍；
- `bank_implementation.py`：**实施工程师**——现场部署、标定落地、故障排查、验收。

**答案的写法约定**（重要，关系到这个库能不能信）：

- 只写**能站得住的技术内容**：公式、原理、工程做法。
- **不写具体产品型号与价格**——那些会过时，而且我没有权威来源。
  需要引用参数时一律指向「厂商规格书」。
- 分不清「标准做法」和「常见经验」的地方，明确标出来。比如景深够不够
  不只是算出来的，还跟现场机械稳定性有关——这是经验，不是定理。
"""

from __future__ import annotations

from .question import VisionQuestion, all_questions, by_role, get_question, roles

__all__ = [
    "VisionQuestion",
    "all_questions",
    "by_role",
    "get_question",
    "roles",
]
