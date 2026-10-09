"""直接调用应用自己的 LLM 客户端，验证「AI 招呼语」这条真实链路（含 reasoning 模型行为）。

用法（cwd 必须是 backend/）：
    backend\\.venv\\Scripts\\python.exe <此脚本>
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(r"D:\VibeCoding\mianshi\backend")))

from app.apply.say_hi import build_say_hi_messages, normalize_say_hi  # noqa: E402
from app.deps import get_llm  # noqa: E402

RESUME = """王工 · 机器视觉应用工程师 · 4 年经验

2021.07 - 2024.06  某自动化设备有限公司  视觉工程师
- 负责 3C 结构件外观检测设备的光学方案与算法开发，交付产线 12 条；
- 用 Halcon 实现亚像素定位与模板匹配，重复定位精度做到 ±0.02mm；
- 主导九点标定流程标准化，把现场调试时间从 3 天压缩到 1 天；
- 针对划伤类缺陷，改用同轴光 + 低角度环形光组合打光，误检率从 8% 降到 2.3%。

技能：Halcon / VisionPro / OpenCV、C# / C++、相机与镜头选型、九点标定、手眼标定、PLC 通信"""

JD = """岗位职责：负责机器视觉检测设备的方案设计与现场实施；
任职要求：熟悉 Halcon 或 VisionPro，有亚像素定位、模板匹配经验；
能独立完成相机与镜头选型、九点标定；有 3C 行业外观检测经验优先。"""


async def main() -> int:
    llm = get_llm()
    print(f"client={type(llm).__name__} label={getattr(llm, 'label', '-')} "
          f"chat_model={getattr(llm, '_chat_model', '-')} reasoner={getattr(llm, '_reasoner_model', '-')}")

    messages = build_say_hi_messages(
        job_title="机器视觉应用工程师",
        company="某某智能装备",
        jd_text=JD,
        resume_text=RESUME,
    )
    try:
        result = await llm.complete(task="say_hi", messages=messages, temperature=0.6)
    except Exception as exc:  # noqa: BLE001
        print("LLM FAILED:", type(exc).__name__, str(exc)[:400])
        return 2

    print(f"model={result.model} latency={result.latency_ms}ms "
          f"tokens={result.prompt_tokens}/{result.completion_tokens}")
    print("raw text  =", repr(result.text[:300]))
    raw = result.raw or {}
    try:
        msg = raw["choices"][0]["message"]
        print("has reasoning_content =", bool(msg.get("reasoning_content")))
        print("finish_reason =", raw["choices"][0].get("finish_reason"))
    except Exception:  # noqa: BLE001
        pass
    final = normalize_say_hi(result.text)
    print("normalized =", final or "（空！→ 会变成空招呼语）")
    print("len =", len(final))
    return 0 if final else 3


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
