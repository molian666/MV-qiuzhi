"""算法题源（M13）。

**为什么内置在代码里而不是数据库**：题目是「内容」不是「用户数据」，
改题应该走代码评审而不是让用户手改行。M2 做 Alembic 时如果需要用户自建题目，
再把这张表搬进 DB —— 那时题库已经跑通，迁移有明确的对照物。

**选题原则**：全部是能独立复现的经典题，不编造。每道题的 `tests` 都包含
边界用例（空输入、单元素、重复元素、目标缺失），因为 M13 的验收标准是
「思路—取舍—边界」——边界不给足，就评不出边界能力。

题目形态统一为**函数式**（LeetCode 核心代码模式）：用户实现一个函数，
判题器用 `tests` 里的 `args` 调用它，和 `expected` 比对。
设计类题（LRU、迭代器）需要操作序列，这一波不做 —— 它们更适合放在
系统设计白板里讲清取舍，而不是靠代码判对错。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

CompareMode = Literal["exact", "unordered"]


@dataclass(frozen=True, slots=True)
class AlgoTestCase:
    """一个测试用例。

    `args` 是要展开传给用户函数的参数列表（用 list 而非 tuple，方便直接 JSON 化）。
    `compare="unordered"` 用于「顺序无关」的题（如 Top-K 高频），那时用多重集比较。
    """

    args: list[Any]
    expected: Any
    note: str = ""
    compare: CompareMode = "exact"


@dataclass(frozen=True, slots=True)
class AlgoProblem:
    slug: str
    title: str
    difficulty: Literal["easy", "medium", "hard"]
    tags: list[str]
    statement: str
    function_name: str
    signature: str
    starters: dict[str, str]
    tests: list[AlgoTestCase]
    hints: list[str]
    key_points: list[str] = field(default_factory=list)
    """评估时的对照要点：题目希望候选人主动谈到的方向。

    这些**不下发给用户**（`schemas/algo.py` 里刻意不暴露），因为它们是评分参照，
    提前看到就把「你有没有想到」变成了「你抄得对不对」。
    """


_LANGUAGES = ("javascript",)


def _starter(js: str) -> dict[str, str]:
    return {"javascript": js}


PROBLEMS: tuple[AlgoProblem, ...] = (
    AlgoProblem(
        slug="two-sum",
        title="两数之和",
        difficulty="easy",
        tags=["数组", "哈希表"],
        statement=(
            "给定一个整数数组 `nums` 和一个目标值 `target`，"
            "返回数组中和为目标值的两个数的**下标**（下标从 0 开始）。\n\n"
            "你可以假设每种输入只会对应一个答案，且同一个元素不能重复使用。\n\n"
            "示例：`nums = [2, 7, 11, 15]`，`target = 9` → `[0, 1]`。"
        ),
        function_name="twoSum",
        signature="twoSum(nums: number[], target: number): number[]",
        starters=_starter(
            "function twoSum(nums, target) {\n"
            "  // 返回两个下标，例如 [0, 1]\n"
            "}\n"
        ),
        tests=[
            AlgoTestCase(args=[[2, 7, 11, 15], 9], expected=[0, 1], note="题干示例"),
            AlgoTestCase(args=[[3, 3], 6], expected=[0, 1], note="重复元素各自只能用一次"),
            AlgoTestCase(args=[[-1, -2, -3], -5], expected=[1, 2], note="负数"),
            AlgoTestCase(args=[[0, 4, 0], 0], expected=[0, 2], note="目标值为 0，两个 0 分居两端"),
            AlgoTestCase(args=[[1, 5], 100], expected=[], note="无解：不应返回下标而是空数组"),
        ],
        hints=[
            "先想想暴力解法的时间复杂度是多少，瓶颈在哪一步。",
            "能不能用「记录已经扫过的数」把「找另一个数」这一步变成 O(1)？",
        ],
        key_points=[
            "说出暴力 O(n²) → 哈希表 O(n) 的优化路径",
            "明确「值 → 下标」的映射为什么不覆盖已扫过的元素",
            "注意同一元素不能用两次（先查后存，而不是先存后查）",
            "无解时返回什么要主动说明",
        ],
    ),
    AlgoProblem(
        slug="valid-parentheses",
        title="有效的括号",
        difficulty="easy",
        tags=["字符串", "栈"],
        statement=(
            "给定一个只包含 `(`、`)`、`{`、`}`、`[`、`]` 的字符串，判断是否有效。\n\n"
            "有效需要同时满足：左括号必须用**同类**右括号闭合，"
            "且闭合顺序正确（`([)]` 无效）。\n\n"
            "空字符串视为有效。"
        ),
        function_name="isValid",
        signature="isValid(s: string): boolean",
        starters=_starter(
            "function isValid(s) {\n"
            "  // 返回 true / false\n"
            "}\n"
        ),
        tests=[
            AlgoTestCase(args=["()[]{}"], expected=True, note="三种括号各成一对"),
            AlgoTestCase(args=["([)]"], expected=False, note="交叉嵌套：顺序错误"),
            AlgoTestCase(args=["{[]}"], expected=True, note="正确嵌套"),
            AlgoTestCase(args=[""], expected=True, note="空串视为有效"),
            AlgoTestCase(args=["(("], expected=False, note="只有左括号：判完还剩栈内容"),
            AlgoTestCase(args=["]"], expected=False, note="单个右括号：栈空时就该失败"),
        ],
        hints=[
            "这类「最近打开的还没关」天然符合什么数据结构？",
            "想想「右括号来了但栈是空的」这种情况该怎么处理。",
        ],
        key_points=[
            "用栈，右括号来时与栈顶配对",
            "扫描结束还要检查栈是否为空",
            "栈空时遇到右括号要直接返回 false（否则会误判）",
            "空串的边界",
        ],
    ),
    AlgoProblem(
        slug="binary-search",
        title="二分查找",
        difficulty="easy",
        tags=["数组", "二分查找"],
        statement=(
            "给定一个**升序**且元素不重复的整数数组 `nums` 和一个目标值 `target`，\n"
            "返回 `target` 的下标；不存在则返回 `-1`。\n\n"
            "要求时间复杂度 O(log n)。"
        ),
        function_name="search",
        signature="search(nums: number[], target: number): number",
        starters=_starter(
            "function search(nums, target) {\n"
            "  // 返回下标或 -1\n"
            "}\n"
        ),
        tests=[
            AlgoTestCase(args=[[-1, 0, 3, 5, 9, 12], 9], expected=4, note="题干示例：命中右侧"),
            AlgoTestCase(args=[[-1, 0, 3, 5, 9, 12], 2], expected=-1, note="目标不存在"),
            AlgoTestCase(args=[[5], 5], expected=0, note="单元素命中"),
            AlgoTestCase(args=[[5], -5], expected=-1, note="单元素未命中"),
            AlgoTestCase(args=[[1, 2, 3, 4, 5], 5], expected=4, note="命中最后一个（右边界）"),
            AlgoTestCase(args=[[1, 2, 3, 4, 5], 1], expected=0, note="命中第一个（左边界）"),
        ],
        hints=[
            "循环条件是 `low <= high` 还是 `low < high`？两种写法都行，但要能说清区别。",
            "`mid` 用 `Math.floor((low + high) / 2)` 在极端入参下有什么隐患？",
        ],
        key_points=[
            "区间定义（闭区间还是左闭右开）与循环条件保持一致",
            "中点计算避免溢出（JS 里数值是双精度，但要能说出这个考量）",
            "左边界与右边界都要能命中（测试覆盖了 0 和 n-1）",
            "未命中时返回 -1 的时机",
        ],
    ),
    AlgoProblem(
        slug="merge-intervals",
        title="合并区间",
        difficulty="medium",
        tags=["数组", "排序"],
        statement=(
            "给定一组区间 `intervals`（每个区间是 `[start, end]`），"
            "合并所有重叠的区间并返回结果。\n\n"
            "区间 `[1, 4]` 和 `[4, 5]` 视为重叠（端点相接算相交）。\n\n"
            "示例：`[[1,3],[2,6],[8,10],[15,18]]` → `[[1,6],[8,10],[15,18]]`。"
        ),
        function_name="merge",
        signature="merge(intervals: number[][]): number[][]",
        starters=_starter(
            "function merge(intervals) {\n"
            "  // 返回合并后的区间数组\n"
            "}\n"
        ),
        tests=[
            AlgoTestCase(
                args=[[[1, 3], [2, 6], [8, 10], [15, 18]]],
                expected=[[1, 6], [8, 10], [15, 18]],
                note="题干示例",
            ),
            AlgoTestCase(args=[[[1, 4], [4, 5]]], expected=[[1, 5]], note="端点相接也算重叠"),
            AlgoTestCase(args=[[[1, 4], [0, 4]]], expected=[[0, 4]], note="乱序输入：必须排序"),
            AlgoTestCase(args=[[]], expected=[], note="空输入返回空数组"),
            AlgoTestCase(args=[[[1, 2]]], expected=[[1, 2]], note="单区间原样返回"),
            AlgoTestCase(args=[[[1, 10], [2, 3]]], expected=[[1, 10]], note="完全被包含的区间"),
        ],
        hints=[
            "输入是乱序的吗？如果是，第一步该做什么？",
            "排好序之后，「要不要和上一个合并」的判断条件是什么？",
        ],
        key_points=[
            "先按起点排序（不排序的解法要么错要么更复杂）",
            "合并条件：当前起点 <= 上一个区间的终点",
            "合并时终点取较大值，而不是直接覆盖（被包含的区间）",
            "空输入与单区间的边界",
        ],
    ),
    AlgoProblem(
        slug="maximum-subarray",
        title="最大子数组和",
        difficulty="medium",
        tags=["数组", "动态规划"],
        statement=(
            "给定一个整数数组 `nums`，找出一个**连续子数组**，使其和最大，返回这个和。\n\n"
            "子数组至少包含一个元素。\n\n"
            "示例：`[-2, 1, -3, 4, -1, 2, 1, -5, 4]` → `6`（子数组 `[4, -1, 2, 1]`）。"
        ),
        function_name="maxSubArray",
        signature="maxSubArray(nums: number[]): number",
        starters=_starter(
            "function maxSubArray(nums) {\n"
            "  // 返回最大子数组和\n"
            "}\n"
        ),
        tests=[
            AlgoTestCase(args=[[-2, 1, -3, 4, -1, 2, 1, -5, 4]], expected=6, note="题干示例"),
            AlgoTestCase(args=[[1]], expected=1, note="单元素"),
            AlgoTestCase(args=[[5, 4, -1, 7, 8]], expected=23, note="整个数组都要"),
            AlgoTestCase(args=[[-3, -1, -2]], expected=-1, note="全负数：答案是最大的那个负数，不是 0"),
            AlgoTestCase(args=[[-1, 0, -2]], expected=0, note="0 可以成为答案"),
        ],
        hints=[
            "以每个位置结尾的子数组，最大和是多少？这个量能不能递推？",
            "「前面的和是负的」时，对后面的贡献是什么？",
        ],
        key_points=[
            "以「以 i 结尾」定义状态，说清递推关系",
            "全负数时的初始化不能写成 0（测试专门覆盖了）",
            "空间可以压到 O(1)（滚动变量），要能说出来",
            "能说出这是 Kadane 算法的思路",
        ],
    ),
    AlgoProblem(
        slug="longest-substring-without-repeating",
        title="无重复字符的最长子串",
        difficulty="medium",
        tags=["字符串", "滑动窗口", "哈希表"],
        statement=(
            "给定一个字符串 `s`，找出其中不含重复字符的**最长子串**的长度。\n\n"
            "注意是子串（连续），不是子序列。\n\n"
            "示例：`s = \"abcabcbb\"` → `3`（`\"abc\"`）。"
        ),
        function_name="lengthOfLongestSubstring",
        signature="lengthOfLongestSubstring(s: string): number",
        starters=_starter(
            "function lengthOfLongestSubstring(s) {\n"
            "  // 返回最长无重复子串的长度\n"
            "}\n"
        ),
        tests=[
            AlgoTestCase(args=["abcabcbb"], expected=3, note="题干示例"),
            AlgoTestCase(args=["bbbbb"], expected=1, note="全重复：窗口长度始终为 1"),
            AlgoTestCase(args=["pwwkew"], expected=3, note="答案是 wke，不是 pwke（子串不是子序列）"),
            AlgoTestCase(args=[""], expected=0, note="空串"),
            AlgoTestCase(args=[" "], expected=1, note="单个空格"),
            AlgoTestCase(args=["dvdf"], expected=3, note="左指针要跳到重复字符之后，而不是只跳到 left+1"),
        ],
        hints=[
            "维护一个「当前不含重复字符的窗口」，右边界移动时会发生什么？",
            "发现重复字符时，左边界应该移动到哪里？只加 1 够吗？",
        ],
        key_points=[
            "滑动窗口 + 记录字符最近出现的位置",
            "左指针跳到 `max(left, 上次出现位置 + 1)`，而不是简单地 left + 1",
            "注意是子串不是子序列（测试里用 pwwkew 区分）",
            "空串的边界",
        ],
    ),
)


_BY_SLUG = {problem.slug: problem for problem in PROBLEMS}


def list_problems() -> tuple[AlgoProblem, ...]:
    return PROBLEMS


def get_problem(slug: str) -> AlgoProblem | None:
    return _BY_SLUG.get(slug)


def supported_languages() -> tuple[str, ...]:
    return _LANGUAGES
