"""机器视觉方向的算法题（M13）。

**选题标准**：CV 面试现场真会让人手写的东西，而不是从 LeetCode 里挑。
覆盖三条线：检测后处理（IoU / NMS）、底层算子（卷积）、训练基础（数值稳定的
softmax + 交叉熵）。这三块是「能不能立刻上手写代码」最常被卡住的地方。

**只用 numpy，不依赖任何框架**：这些算子的手写版在面试里就是这样要求的，
用上 `torchvision.ops.nms` 就失去意义了。沙箱里预置 `np`，题面里也说明了。

**为什么只有 python**：CV 岗面试就是写 Python。给个 JS 起始代码只会让人以为
自己选错了语言。
"""

from __future__ import annotations

from .problem import AlgoProblem, AlgoTestCase

_PY = "python"


def _starter(code: str) -> dict[str, str]:
    return {_PY: code}


def _name(name: str) -> dict[str, str]:
    return {_PY: name}


def _sig(text: str) -> dict[str, str]:
    return {_PY: text}


VISION_PROBLEMS: tuple[AlgoProblem, ...] = (
    AlgoProblem(
        slug="vision-compute-iou",
        title="计算 IoU",
        difficulty="easy",
        tags=["目标检测", "边界框"],
        statement=(
            "给定两个边界框，计算它们的**交并比（IoU）**。\n\n"
            "框的格式是 `[x1, y1, x2, y2]`，满足 `x1 < x2`、`y1 < y2`：\n\n"
            "```\n"
            "IoU = 交集面积 / 并集面积\n"
            "面积 = (x2 - x1) * (y2 - y1)\n"
            "```\n\n"
            "要求：\n\n"
            "- 两框不相交时返回 `0.0`，**不要返回负数**；\n"
            "- 允许边界恰好相接（交集面积为 0）；\n"
            "- 返回 Python 的 `float`。\n\n"
            "两个入参都是 numpy 数组，形状 `(4,)`。"
        ),
        starters=_starter(
            "import numpy as np\n"
            "\n"
            "\n"
            "def compute_iou(box_a, box_b):\n"
            '    """返回两个框的 IoU（float）。box 格式 [x1, y1, x2, y2]。"""\n'
            "    # 你的实现\n"
            "    pass\n"
        ),
        function_names=_name("compute_iou"),
        signatures=_sig(
            "compute_iou(box_a: np.ndarray, box_b: np.ndarray) -> float"
        ),
        tests=[
            AlgoTestCase(
                args=[[0, 0, 2, 2], [0, 0, 2, 2]], expected=1.0, note="完全重合"
            ),
            AlgoTestCase(
                args=[[0, 0, 2, 2], [1, 1, 3, 3]],
                expected=1 / 7,
                note="半重叠：交 1、并 7",
            ),
            AlgoTestCase(
                args=[[0, 0, 4, 4], [1, 1, 2, 2]],
                expected=1 / 16,
                note="一个完全包含另一个",
            ),
            AlgoTestCase(
                args=[[0, 0, 2, 2], [2, 2, 4, 4]],
                expected=0.0,
                note="只在一个角相接：交集面积为 0",
            ),
            AlgoTestCase(
                args=[[0, 0, 2, 2], [5, 5, 7, 7]], expected=0.0, note="完全分离"
            ),
            AlgoTestCase(
                args=[[1, 1, 1, 1], [0, 0, 2, 2]],
                expected=0.0,
                note="零面积框：不要出现除零或负数",
            ),
        ],
        hints=[
            "交集的左上角是两框左上角的逐元素最大值，右下角是最小值。",
            "算完 w、h 之后要想一下：如果它们小于 0 会怎样？",
            "并集是面积之和减去交集，别把交集算两次。",
        ],
        key_points=[
            "用 max/min 求交集边界（而不是分类讨论谁在左谁在右）",
            "w、h 要 clip 到 0，否则分离的框会算出负面积",
            "并集 = 面积和 - 交集",
            "零面积框的处理（测试里有一例）",
        ],
    ),
    AlgoProblem(
        slug="vision-nms",
        title="实现 NMS",
        difficulty="medium",
        tags=["目标检测", "后处理"],
        statement=(
            "实现**非极大值抑制（NMS）**。\n\n"
            "输入：\n\n"
            "- `boxes`：形状 `(N, 4)` 的框数组，格式 `[x1, y1, x2, y2]`；\n"
            "- `scores`：长度 `N` 的置信度；\n"
            "- `iou_threshold`：阈值。\n\n"
            "返回：**保留下来的框的下标列表**，按分数从高到低排列。\n\n"
            "算法：\n\n"
            "1. 按分数从高到低排序；\n"
            "2. 取当前分最高的框，保留它；\n"
            "3. 把它与剩余框中 IoU **严格大于** `iou_threshold` 的丢弃；\n"
            "4. 重复直到没有剩余。\n\n"
            "注意第 3 步是**严格大于**：`IoU == iou_threshold` 时保留。\n\n"
            "**分数相同时，下标小的框优先**（结果必须是确定的，不能依赖排序实现的偶然行为）。\n\n"
            "你可以自己再写一个算 IoU 的小函数（不必调用上一题的实现）。"
        ),
        starters=_starter(
            "import numpy as np\n"
            "\n"
            "\n"
            "def nms(boxes, scores, iou_threshold):\n"
            '    """返回保留框的下标列表，按分数降序。"""\n'
            "    # 你的实现\n"
            "    pass\n"
        ),
        function_names=_name("nms"),
        signatures=_sig(
            "nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]"
        ),
        tests=[
            AlgoTestCase(args=[[], [], 0.5], expected=[], note="空输入"),
            AlgoTestCase(
                args=[[[0, 0, 2, 2], [10, 10, 12, 12]], [0.9, 0.8], 0.5],
                expected=[0, 1],
                note="完全不重叠：两个都留，按分数降序",
            ),
            AlgoTestCase(
                args=[[[0, 0, 2, 2], [0, 0, 2, 2]], [0.7, 0.9], 0.5],
                expected=[1],
                note="完全重合：只留分数高的那个",
            ),
            AlgoTestCase(
                args=[[[0, 0, 2, 2], [0, 0, 2, 2]], [0.7, 0.9], 1.0],
                expected=[1, 0],
                note="阈值 1.0：IoU 最大为 1，严格大于不成立，两个都留",
            ),
            AlgoTestCase(
                args=[
                    [[0, 0, 2, 2], [0.2, 0.2, 2.2, 2.2], [10, 10, 12, 12]],
                    [0.6, 0.95, 0.9],
                    0.5,
                ],
                expected=[1, 2],
                note="高分框先抑制重叠的次高分框，远处的保留",
            ),
            AlgoTestCase(
                args=[[[0, 0, 2, 2], [0.1, 0.1, 2.1, 2.1]], [0.5, 0.5], 0.5],
                expected=[0],
                note="分数相同时要稳定：按下标顺序取，结果不应随机",
            ),
        ],
        hints=[
            "先 argsort 分数再倒序，把「按分数处理」这件事变成迭代顺序问题。",
            "IoU 的计算可以直接照抄上一题，但要保证它不会返回负值。",
            "分数相同时怎么办？测试里有一例，用稳定的排序语义。",
        ],
        key_points=[
            "先按分数降序排序，再顺序处理（不是每轮重新扫全部）",
            "抑制条件是 IoU > threshold（严格大于），测试里用阈值 1.0 卡了这点",
            "分数相同时的稳定性（argsort 的快排不稳定，要注意）",
            "返回的是下标列表而不是框本身",
        ],
    ),
    AlgoProblem(
        slug="vision-conv2d",
        title="二维卷积（valid）",
        difficulty="medium",
        tags=["卷积", "底层算子"],
        statement=(
            "实现二维卷积，**valid 模式、stride=1、无 padding、单通道**。\n\n"
            "输入：\n\n"
            "- `image`：形状 `(H, W)` 的二维数组；\n"
            "- `kernel`：形状 `(kh, kw)` 的卷积核。\n\n"
            "输出形状是 `(H - kh + 1, W - kw + 1)`。\n\n"
            "**注意这是互相关（cross-correlation），不是数学意义上的卷积**——"
            "也就是**不翻转**卷积核。深度学习框架里的 `conv2d` 就是这个语义，"
            "做题时不要把它翻过来。\n\n"
            "输出位置 `(i, j)` 的值等于：`image[i:i+kh, j:j+kw]` 与 `kernel` 的逐元素乘积之和。"
        ),
        starters=_starter(
            "import numpy as np\n"
            "\n"
            "\n"
            "def convolve2d(image, kernel):\n"
            '    """valid 模式的二维互相关，返回二维数组。"""\n'
            "    # 你的实现\n"
            "    pass\n"
        ),
        function_names=_name("convolve2d"),
        signatures=_sig(
            "convolve2d(image: np.ndarray, kernel: np.ndarray) -> np.ndarray"
        ),
        tests=[
            AlgoTestCase(
                args=[[[1, 2, 3], [4, 5, 6], [7, 8, 9]], [[0, 0, 0], [0, 1, 0], [0, 0, 0]]],
                expected=[[5]],
                note="3×3 图与 3×3 核：只出一个值，等于中心像素",
            ),
            AlgoTestCase(
                args=[[[1, 2, 3], [4, 5, 6], [7, 8, 9]], [[1]]],
                expected=[[1, 2, 3], [4, 5, 6], [7, 8, 9]],
                note="1×1 核取 1：原样返回",
            ),
            AlgoTestCase(
                args=[[[1, 2], [3, 4]], [[0, 0], [0, 0]]],
                expected=[[0]],
                note="全零核：输出全零，形状仍要正确",
            ),
            AlgoTestCase(
                args=[
                    [[1, 2, 3], [4, 5, 6], [7, 8, 9]],
                    [[0, 1], [1, 0]],
                ],
                expected=[[6, 8], [12, 14]],
                note="2×2 核：输出 2×2，注意下标不能越界",
            ),
            AlgoTestCase(
                args=[
                    [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12], [13, 14, 15, 16]],
                    [[1, 1], [1, 1]],
                ],
                expected=[[14, 18, 22], [30, 34, 38], [46, 50, 54]],
                note="4×4 图与 2×2 全 1 核：每个位置是 2×2 邻域和",
            ),
        ],
        hints=[
            "输出形状先算出来，然后按输出坐标去原图上取窗口。",
            "窗口和核的逐元素乘加可以直接用 `np.sum(window * kernel)`。",
            "想想核比图还大的情况——测试里没有，但你该知道会怎样。",
        ],
        key_points=[
            "先确定输出形状 (H-kh+1, W-kw+1)",
            "是互相关不是卷积：不翻转核（题面已说明，做错说明没读题）",
            "窗口的切片边界（用 i:i+kh 而不是 i+kh）",
            "能说出这个朴素实现是 O(H·W·kh·kw)，以及 im2col / 分离卷积能怎么优化",
        ],
    ),
    AlgoProblem(
        slug="vision-softmax-cross-entropy",
        title="数值稳定的 softmax 交叉熵",
        difficulty="medium",
        tags=["深度学习", "损失函数"],
        statement=(
            "实现**数值稳定**的 softmax 交叉熵（多分类、单样本、标签是类别下标）。\n\n"
            "输入：\n\n"
            "- `logits`：长度 `C` 的数组（未归一化的分数）；\n"
            "- `label`：正确类别的下标（`int`）。\n\n"
            "返回一个 `float`：\n\n"
            "```\n"
            "loss = -log( softmax(logits)[label] )\n"
            "```\n\n"
            "**数值稳定性是这题的重点**：`logits` 里出现 `1e4` 这种极值时，\n"
            "直接 `np.exp(logits)` 会溢出成 `inf`，算出来就是 `nan`。\n"
            "正确做法是先用最大值归一化（减 `max`），但要自己写出来。"
        ),
        starters=_starter(
            "import numpy as np\n"
            "\n"
            "\n"
            "def softmax_cross_entropy(logits, label):\n"
            '    """返回交叉熵损失（float），要求数值稳定。"""\n'
            "    # 你的实现\n"
            "    pass\n"
        ),
        function_names=_name("softmax_cross_entropy"),
        signatures=_sig("softmax_cross_entropy(logits: np.ndarray, label: int) -> float"),
        tests=[
            AlgoTestCase(
                args=[[0.0, 0.0, 0.0], 0],
                expected=1.0986122886681098,
                note="均匀分布：损失是 log(3)",
            ),
            AlgoTestCase(
                args=[[0.0, 0.0, 0.0], 2],
                expected=1.0986122886681098,
                note="均匀分布时，哪个标签都一样",
            ),
            AlgoTestCase(
                args=[[10.0, 0.0, 0.0], 0],
                expected=9.079573746717529e-05,
                note="预测正确：损失很小但非零",
            ),
            AlgoTestCase(
                args=[[10.0, 0.0, 0.0], 1],
                expected=10.000090795737467,
                note="预测错得离谱：损失约等于 logits 之差",
            ),
            AlgoTestCase(
                args=[[1e4, 0.0, 0.0], 1],
                expected=10000.0,
                note="极值：不减去最大值就会溢出成 nan",
            ),
            AlgoTestCase(
                args=[[1e4, 0.0, 0.0], 0], expected=0.0, note="极值且预测正确：损失趋近 0"
            ),
            AlgoTestCase(
                args=[[5.0], 0], expected=0.0, note="只有一类：softmax 恒为 1，损失为 0"
            ),
        ],
        hints=[
            "softmax(x) 和 softmax(x - c) 是相等的（c 是任意常数）——这就是稳定性的来源。",
            "取 c = max(x)，指数上就不会有正的大数了。",
            "算 log(softmax) 时不要先 softmax 再 log：小概率会下溢到 0，log(0) 是 -inf。",
        ],
        key_points=[
            "减去最大值（而不是别的常数；减 max 能保证指数非正）",
            "用 log_softmax 的等价形式：loss = -(logits[label] - logsumexp(logits))，避免先 exp 再 log",
            "能说出「先 softmax 再 log」在下溢时数值不成立",
            "单类别与极值的边界",
        ],
    ),
)
