"""让模型出机器视觉面试题（M13）。

与「导入文档」共用同一套字段约定（`question` / `answer` / `points` / `category` /
`difficulty`），所以解析直接复用 `import_doc.build_imported_question`——
两套解析会漂移，而它们的目标结构是同一个。

**prompt 里最要紧的两条约束**，就这个项目而言比生成质量本身更重要：

1. **不写具体产品型号与价格**——会过时，而且没有权威来源，写了就是编造。
   这条和内置题库守的是同一条线。
2. **不要把已有题目重复出一遍**。用户点「继续生成」时，新题必须和已有题不重样，
   否则连点几次会得到一堆近义题，题库反而更难用。所以要把已有题干带进 prompt。

生成结果**先以草稿入库、不进抽题池**：用户要看过、决定采纳，才进入面试可选范围。
"""

from __future__ import annotations

from typing import Any, Sequence

from ..providers.llm.base import LLMClient, LLMMessage, extract_json
from ..vision.import_doc import ImportedQuestion, build_imported_question
from ..vision.question import ROLE_DESCRIPTIONS, ROLE_LABELS
from .question_bank import BankQuestion

#: 每个 LLM 调用都要带 task 标签（PLAN §9.1 第 5 条）
TASK = "vision_question_generate"

#: 一次最多生成多少道。再多模型质量会掉，而且一条 prompt 也塞不下。
MAX_GENERATE_COUNT = 12

_OUTPUT_SHAPE = """只输出一个 JSON 对象，不要解释文字、不要 markdown 代码块：

{
  "questions": [
    {
      "question": "题干，写成面试官会问出来的样子",
      "category": "二级分类，如 相机与传感器 / 镜头与光学 / 光源与打光 / 标定与坐标 / 传统视觉算子 / 深度学习与缺陷检测 / 软件与工具 / 现场实施",
      "difficulty": 3,
      "points": ["答题要点，3-6 条，每条一句话"],
      "answer": "参考答案。分小节写，需要时带公式或小例子",
      "followups": ["面试官在这个问题之后大概率会追问什么，2-4 条"],
      "pitfalls": ["常见错误回答，2-4 条"]
    }
  ]
}"""


def build_system_prompt(role: str, *, difficulty: int | None, category: str | None) -> str:
    label = ROLE_LABELS.get(role, role)
    description = ROLE_DESCRIPTIONS.get(role, "")

    lines = [
        f"你是机器视觉行业的资深技术面试官，正在为**{label}**岗位出一套面试题。",
        "",
        f"这个岗位的面试重点：{description}",
        "",
        "出题要求：",
        "- 出**真实面试会问**的东西，不要偏门的理论题，也不要出成教材习题；",
        "- 题干写成面试官会问出来的样子（一句话、口语、具体），**不要写成知识点标题**；",
        "- `points` 是答题要点，同时会被候选人当自评清单用，要具体、能对照；",
        "- `answer` 要有实质内容（推导、公式、做法、场景判断），不要空泛的套话；",
        "- `followups` 写面试官在这个问题之后会怎么追——答对主问题只是入场券；",
        "- `pitfalls` 写常见错误回答，这个比多背一条正确答案更能帮到人；",
        "",
        "**硬性约束（违反就作废）**：",
        "- **不要写具体产品型号、版本号与价格**。那些会过时，而且没有权威来源——写了就是编造。",
        "  需要引用参数时，一律说「以厂商规格书为准」。",
        "- 分不清「标准做法」和「经验之谈」的地方，要明确标出来（写成「经验上……」）。",
        "- 不要编造具体数值当结论：可以用公式或区间说明，但别断言「某某型号能到多少」。",
        "- 全部用中文。",
    ]

    if category:
        lines.append(f"- 本批题目的分类统一为：{category}。")
    if difficulty is not None:
        lines.append(
            f"- 本批题目的难度统一为 {difficulty}（1 入门 ~ 5 资深，按「答得上需要多少现场经验」估）。"
        )

    return "\n".join(lines)


def build_messages(
    *,
    role: str,
    count: int,
    difficulty: int | None = None,
    category: str | None = None,
    existing: Sequence[BankQuestion] = (),
    extra_note: str = "",
) -> list[LLMMessage]:
    """`existing` 是已经生成过的题——带上它，模型才知道不要重复出。"""
    parts = [f"请出 {count} 道题。"]

    if extra_note.strip():
        parts.append(f"\n额外要求：{extra_note.strip()}")

    if existing:
        listed = "\n".join(f"- {item.text}" for item in existing)
        parts.append(
            "\n**下面这些题已经出过了，本批不要再出同义或近似的问题**：\n" + listed
        )

    parts.append(f"\n{_OUTPUT_SHAPE}")

    return [
        LLMMessage(
            role="system",
            content=build_system_prompt(role, difficulty=difficulty, category=category),
        ),
        LLMMessage(role="user", content="\n".join(parts)),
    ]


async def generate_questions(
    llm: LLMClient,
    *,
    role: str,
    count: int,
    difficulty: int | None = None,
    category: str | None = None,
    existing: Sequence[BankQuestion] = (),
    extra_note: str = "",
) -> list[ImportedQuestion]:
    """让模型出题。返回解析后的题目（可能少于 `count`——模型少给是常事）。"""
    messages = build_messages(
        role=role,
        count=count,
        difficulty=difficulty,
        category=category,
        existing=existing,
        extra_note=extra_note,
    )
    result = await llm.complete(
        task=TASK,
        messages=messages,
        temperature=0.8,
        json_object=True,
    )
    return parse_generated(result.text)


def parse_generated(text: str) -> list[ImportedQuestion]:
    """解析模型输出。**解析失败返回空列表而不是抛异常**——
    上层据此提示「这次没生成出来，可以重试」，比让整个请求 500 好。"""
    try:
        raw = extract_json(text)
    except ValueError:
        return []

    items: Any = raw.get("questions")
    if not isinstance(items, list):
        # 兼容模型直接给一个数组、或者只给一道题的情况
        items = [raw] if any(key in raw for key in ("question", "题干")) else []

    out: list[ImportedQuestion] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        question = build_imported_question(item)
        if question is None or question.text in seen:
            continue
        seen.add(question.text)
        out.append(question)
    return out


def compose_answer_text(
    answer: str,
    followups: Sequence[str],
    pitfalls: Sequence[str],
) -> str:
    """把答案、追问、常见错误拼成一份完整的 Markdown。

    格式与内置题库的 `render_answer` 一致，所以前端的 Markdown 渲染会把它
    显示成带小标题的完整答案——字段虽然存在一处，看起来是分块的。
    """
    parts = [answer.strip()]
    if followups:
        lines = "\n".join(f"- {item}" for item in followups)
        parts.append(f"## 面试官大概率会追问\n\n{lines}")
    if pitfalls:
        lines = "\n".join(f"- {item}" for item in pitfalls)
        parts.append(f"## 常见错误回答\n\n{lines}")
    return "\n\n".join(part for part in parts if part)


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]
