"""Mock 适配器端到端冒烟：抓候选 → 人工确认 → 演练 → 真投（Mock）→ 清理 → 还原设置。

只访问本机 127.0.0.1:8000 的 /api/apply/*，Mock 适配器**不联网、不碰真实平台**。
跑完会把 platform 还原成脚本开始时的值（其余设置一并还原），队列里不留 pending/approved。

用法：
    backend\\.venv\\Scripts\\python.exe scripts\\apply_mock_smoke.py [报告文件路径]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"
REPORT_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("apply_mock_smoke_report.json")

report: dict = {"base": BASE, "steps": []}


def record(step: str, data: object) -> None:
    report["steps"].append({"step": step, "data": data})
    blob = json.dumps(data, ensure_ascii=False)
    print(f"[{step}] {blob[:400]}")


def main() -> int:
    with httpx.Client(base_url=BASE, timeout=180.0) as client:
        # 0. 记录原始设置（PUT 是全量覆盖，必须整份回传）
        original = client.get("/api/apply/settings").raise_for_status().json()
        record("settings.before", original)

        rules = client.get("/api/apply/rules").raise_for_status().json()
        record("rules", [{"name": r["name"], "kind": r["kind"], "enabled": r["enabled"]} for r in rules])

        # 0.5 临时关掉「已投过去重」：否则第二次跑时 Mock 的三个岗位全被"已经投过了"拦住，
        #     脚本就只能空转（第一次跑完就再也验证不了执行链路）。跑完会还原。
        dedup = next((r for r in rules if r["kind"] == "dedup"), None)
        dedup_was_enabled = bool(dedup and dedup["enabled"])
        if dedup_was_enabled and dedup is not None:
            client.put(
                f"/api/apply/rules/{dedup['id']}",
                json={**dedup, "enabled": False},
            ).raise_for_status()
            record("dedup.disabled", {"rule_id": dedup["id"]})

        # 1. 切到 Mock 平台（保留 enabled / 风险确认 / 限速）
        mock_settings = {**original, "platform": "mock"}
        after = client.put("/api/apply/settings", json=mock_settings).raise_for_status().json()
        record("settings.platform=mock", {"platform": after["platform"], "enabled": after["enabled"],
                                          "adapter_ready": None})

        status = client.get("/api/apply/status").raise_for_status().json()
        record("status(mock)", status)

        # 2. 抓候选（只入队，不投递）
        search = client.post(
            "/api/apply/search",
            json={"keywords": ["后端 Python"], "limit": 5},
        ).raise_for_status().json()
        record(
            "search",
            {
                "fetched": search["fetched"],
                "passed": search["passed"],
                "blocked": search["blocked"],
                "queued": search["queued"],
                "duplicates": search["duplicates"],
                "notes": search["notes"],
                "candidates": [
                    {
                        "task_id": c["task_id"],
                        "company": c["company"],
                        "title": c["title"],
                        "salary": c["salary_range"],
                        "score": c["matched_score"],
                        "passed": c["passed"],
                        "reasons": c["reasons"],
                        "say_hi": c["say_hi"],
                    }
                    for c in search["candidates"]
                ],
            },
        )

        approvable = [c["task_id"] for c in search["candidates"] if c["passed"] and c["task_id"]]
        if not approvable:
            record("abort", "没有通过过滤的候选，无法继续验证确认闸门")
            _restore(client, original)
            _write()
            return 1

        picked = approvable[:2]

        # 3. 人工确认闸门：未确认的记录不应该被投出去
        pre = client.post("/api/apply/execute", json={"limit": 10, "dry_run": True}).raise_for_status().json()
        record("gate(before approve)", pre)

        approved = client.post("/api/apply/queue/approve", json={"task_ids": picked}).raise_for_status().json()
        record("approve", {"task_ids": picked, **approved})
        if approved.get("approved", 0) == 0:
            note = (
                "approve 0 条：这些 Mock 岗位的任务已经是 sent/failed 状态（第一次跑就投过了），"
                "没有 pending 可确认。**执行链路只能在干净库上完整验证一次**；"
                "要再验证请先清掉 mock 岗位的 apply_task 记录（或换一个 data 目录）。"
                "注意：这不是故障，确认闸门依然生效（未确认就投不出去）。"
            )
            record("note", note)
            print(f"[note] {note}", flush=True)

        # 4. 演练模式：只报告会投什么
        dry = client.post("/api/apply/execute", json={"limit": 10, "dry_run": True}).raise_for_status().json()
        record("execute(dry_run=True)", dry)

        # 5. 真投（Mock 会真的"发出"，含 3-9 秒随机抖动）
        real = client.post("/api/apply/execute", json={"limit": 2, "dry_run": False}).raise_for_status().json()
        record("execute(dry_run=False)", real)

        # 6. 清理：把剩下的 pending 全部跳过，队列不留残留
        queue = client.get("/api/apply/queue").raise_for_status().json()
        leftover = [t["id"] for t in queue if t["status"] == "pending"]
        if leftover:
            skipped = client.post("/api/apply/queue/skip", json={"task_ids": leftover}).raise_for_status().json()
            record("skip(leftover)", {"task_ids": leftover, **skipped})

        snapshot = client.get("/api/apply/queue/snapshot").raise_for_status().json()
        record("queue.snapshot", snapshot)
        final_queue = client.get("/api/apply/queue").raise_for_status().json()
        record(
            "queue.items",
            [
                {"id": t["id"], "status": t["status"], "company": t["company"], "title": t["title"],
                 "attempts": t["attempts"], "error": t["error"], "say_hi": t["say_hi_text"]}
                for t in final_queue
            ],
        )

        # 7. 还原设置（platform 等全部回到跑之前）与临时改过的规则
        _restore(client, original)
        if dedup_was_enabled and dedup is not None:
            client.put(
                f"/api/apply/rules/{dedup['id']}",
                json={**dedup, "enabled": True},
            ).raise_for_status()
            record("dedup.restored", {"rule_id": dedup["id"]})
        record("settings.after", client.get("/api/apply/settings").raise_for_status().json())

    _write()
    return 0


def _restore(client: httpx.Client, original: dict) -> None:
    restored = client.put("/api/apply/settings", json=original).raise_for_status().json()
    record("settings.restore", {"platform": restored["platform"], "enabled": restored["enabled"],
                               "risk_acknowledged": restored["risk_acknowledged"],
                               "selectors": restored["selectors"]})


def _write() -> None:
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report -> {REPORT_PATH}")


if __name__ == "__main__":
    raise SystemExit(main())
