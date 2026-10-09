"""诊断 LLM 调用为什么失败（只读 .env，key 打码）。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import httpx

env_path = Path(r"D:\VibeCoding\mianshi\backend\.env")
cfg: dict[str, str] = {}
for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, _, value = line.partition("=")
    cfg[key.strip()] = value.strip()

base = cfg.get("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
model = cfg.get("LLM_CHAT_MODEL", "")
api_key = cfg.get("LLM_API_KEY", "")
print(f"provider={cfg.get('LLM_PROVIDER')} base={base} model={model!r} key={api_key[:5]}***len={len(api_key)}")
print("proxy env:", {k: v for k, v in os.environ.items() if "proxy" in k.lower()} or "none")

url = f"{base}/chat/completions"
body = {"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 4}
try:
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, json=body, headers={"Authorization": f"Bearer {api_key}"})
    print("HTTP", resp.status_code)
    print("body:", resp.text[:600])
except Exception as exc:  # noqa: BLE001
    print("CALL FAILED:", type(exc).__name__, str(exc)[:300])

# 顺便看看 DeepSeek 官方有哪些模型可用
try:
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(f"{base}/models", headers={"Authorization": f"Bearer {api_key}"})
    print("models HTTP", resp.status_code, resp.text[:400])
except Exception as exc:  # noqa: BLE001
    print("models FAILED:", type(exc).__name__, str(exc)[:200])
