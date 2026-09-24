"""算法题白板（M13）。

与 `apply/` 一样按子包组织，分几块：

- `problem.py`：题目与用例的数据结构、多语言与判题的约定；
- `bank_general.py` / `bank_vision.py`：题库——后者是**机器视觉方向**，本项目的重点；
- `problems.py`：汇总与查询；
- `review.py`：「思路—取舍—边界」评估的 prompt 与解析。

判题**不在服务端执行**（PLAN §9.2 的技术规避点）：本机没有 docker，
起不了隔离容器，跑用户或模型生成的代码等于把自己的机器交出去。
所以浏览器端跑测试，服务端只接收**测试结果**并做评估。
"""

from __future__ import annotations

from .problems import PROBLEMS, AlgoProblem, AlgoTestCase, get_problem, list_problems

__all__ = [
    "PROBLEMS",
    "AlgoProblem",
    "AlgoTestCase",
    "get_problem",
    "list_problems",
]
