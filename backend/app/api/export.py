"""导出 API（M15）：全量数据的 Markdown / CSV(ZIP) / JSON 三种格式。

token 校验：本地也不能裸奔——同网段的任何进程都能读你的全量求职数据。
令牌从 backend/.env 的 EXPORT_TOKEN 读（为空则接口一律 401 并给出可读提示）。
三种格式都必须带令牌；预览接口同样（它泄露板块与行数分布）。
"""

from __future__ import annotations

import secrets
from datetime import UTC

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import Response
from sqlmodel import Session

from ..analysis.export import bundle_stats
from ..config import get_settings
from ..deps import get_session
from ..export_service import build_bundle, export_csv_zip, export_json_payload, export_markdown
from ..schemas.career import ExportPreviewOut, ExportSectionStat

router = APIRouter(prefix="/api/export", tags=["export"])


def require_export_token(
    x_export_token: str | None = Header(default=None),
    token: str | None = None,
) -> None:
    """令牌校验：X-Export-Token 头或 ?token= 均可（后者方便浏览器直接下载）。

    用 secrets.compare_digest 比对，不用 ==，避免时序侧信道（虽然本地场景概率极低，
    但正确写法不比错误写法贵）。
    """
    expected = get_settings().export_token
    if not expected:
        raise HTTPException(
            status_code=401,
            detail="导出未启用：请在 backend/.env 里设置 EXPORT_TOKEN（任意长随机字符串）后重启后端。"
                   "示例：python -c \"import secrets; print(secrets.token_urlsafe(32))\"",
        )
    supplied = x_export_token or token or ""
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="导出令牌不正确（X-Export-Token 头或 ?token=）")


def _markdown_response(markdown: str, filename: str) -> Response:
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/preview", response_model=ExportPreviewOut, summary="导出预览：板块与行数（需令牌）")
def preview(_ok: None = Depends(require_export_token), db: Session = Depends(get_session)) -> ExportPreviewOut:
    bundle = build_bundle(db)
    return ExportPreviewOut(
        generated_at=bundle.generated_at.astimezone(UTC).isoformat(timespec="seconds"),
        sections=[ExportSectionStat(**stat) for stat in bundle_stats(bundle)],
        formats=[
            {"key": "markdown", "label": "Markdown 单文件", "endpoint": "/api/export/markdown"},
            {"key": "csv", "label": "CSV 打包（ZIP，每板块一个文件，Excel 可开）", "endpoint": "/api/export/csv"},
            {"key": "json", "label": "JSON 全量（可作备份）", "endpoint": "/api/export/json"},
        ],
        integrations=[
            {
                "key": "notion",
                "label": "Notion",
                "status": "not_configured",
                "detail": "尚未接入：需要真实的 Notion Integration Token 与目标 database id，"
                          "拿到后走 /api/export/integrations/notion 配置。当前请先用 Markdown/CSV/JSON 导出。",
            },
            {
                "key": "feishu",
                "label": "飞书多维表格",
                "status": "not_configured",
                "detail": "尚未接入：需要真实的飞书自建应用 app_id/app_secret 与 bitable token，"
                          "拿到后走 /api/export/integrations/feishu 配置。当前请先用 Markdown/CSV/JSON 导出。",
            },
        ],
    )


@router.get("/markdown", summary="全量导出为 Markdown（需令牌）")
def markdown_export(
    _ok: None = Depends(require_export_token),
    db: Session = Depends(get_session),
) -> Response:
    return _markdown_response(export_markdown(db), "mianshi-export.md")


@router.get("/csv", summary="全量导出为 CSV 打包 ZIP（需令牌）")
def csv_export(
    _ok: None = Depends(require_export_token),
    db: Session = Depends(get_session),
) -> Response:
    payload = export_csv_zip(db)
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="mianshi-export-csv.zip"'},
    )


@router.get("/json", summary="全量导出为 JSON（需令牌）")
def json_export(
    _ok: None = Depends(require_export_token),
    db: Session = Depends(get_session),
) -> Response:
    import json as _json

    payload = export_json_payload(db)
    return Response(
        content=_json.dumps(payload, ensure_ascii=False, indent=1),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="mianshi-export.json"'},
    )


@router.post("/integrations/notion", summary="Notion 集成占位（未实现，返回可读报错）")
def notion_integration(_ok: None = Depends(require_export_token)) -> dict[str, str]:
    raise HTTPException(
        status_code=501,
        detail="Notion 集成尚未实现：需要真实的 Integration Token 并逐条核实 API 行为后才能写，"
               "不提供假装能用的假实现。当前请用 Markdown/CSV/JSON 导出。",
    )


@router.post("/integrations/feishu", summary="飞书集成占位（未实现，返回可读报错）")
def feishu_integration(_ok: None = Depends(require_export_token)) -> dict[str, str]:
    raise HTTPException(
        status_code=501,
        detail="飞书集成尚未实现：需要真实的自建应用凭证并逐条核实 API 行为后才能写，"
               "不提供假装能用的假实现。当前请用 Markdown/CSV/JSON 导出。",
    )
