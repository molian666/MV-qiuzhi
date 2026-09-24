"""算法题白板（M13）。

`from __future__ import annotations` 在前端会让人误解——这里是后端包，
但与 `apply/` 一样按子包组织：题库、判题协议、评估三块。

判题**不在服务端执行**（PLAN §9.2 的技术规避点）：本机没有 docker，
起不了隔离容器，跑用户或模型生成的代码等于把自己的机器交出去。
所以浏览器端跑测试，服务端只接收**测试结果**并做「思路—取舍—边界」评估。
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
