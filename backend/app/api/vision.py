"""机器视觉面试题库 API（M13）。

这是当前的主线功能：**题目 + 答案 + 自测**。

三个设计取舍：

- **建档与内容分离**：题目内容在 `app/vision/bank_*.py`（代码常量，保证质量可控），
  首次访问时幂等播种进 `question_bank` 表（自测记录需要外键指向它）。
- **列表不带答案**：列表页要能快速扫几十道题，答案在详情里给全。
- **自测用三档自评**：让人打 1~10 分只会得到一堆无意义的 7 分，
  而「答上来了 / 答了一半 / 没答上来」是人能诚实区分的。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlmodel import Session

from ..deps import get_session
from ..schemas.vision import (
    VisionAttemptIn,
    VisionAttemptOut,
    VisionImportOut,
    VisionMetaOut,
    VisionQuestionBriefOut,
    VisionQuestionOut,
    VisionUploadOut,
)
from ..vision.import_doc import decode_bytes, parse_document
from ..vision_service import (
    VisionError,
    delete_upload,
    get_question,
    import_questions,
    list_questions,
    list_uploads,
    meta,
    record_attempt,
)

router = APIRouter(prefix="/api/vision", tags=["vision"])

#: 上传文档的大小上限。题库文档是纯文本，2MB 已经非常宽裕；
#: 设个上限是为了不让一个误传的大文件把内存吃满。
MAX_UPLOAD_BYTES = 2 * 1024 * 1024


@router.get("/meta", response_model=VisionMetaOut, summary="岗位方向与分类（给筛选器用）")
def read_meta(db: Session = Depends(get_session)) -> VisionMetaOut:
    return VisionMetaOut(**meta(db))  # type: ignore[arg-type]


@router.get("/questions", response_model=list[VisionQuestionBriefOut], summary="题目清单（不含答案）")
def read_questions(
    role: str | None = Query(default=None, description="development | application | implementation"),
    category: str | None = Query(default=None),
    max_difficulty: int | None = Query(default=None, ge=1, le=5),
    keyword: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_session),
) -> list[VisionQuestionBriefOut]:
    items = list_questions(
        db,
        role=role,
        category=category,
        max_difficulty=max_difficulty,
        keyword=keyword,
        limit=limit,
    )
    return [VisionQuestionBriefOut(**item) for item in items]  # type: ignore[arg-type]


@router.get("/questions/{question_id}", response_model=VisionQuestionOut, summary="题目详情：要点、参考答案、追问、常见错误")
def read_question(question_id: int, db: Session = Depends(get_session)) -> VisionQuestionOut:
    detail = get_question(db, question_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"没有这道题：{question_id}")
    return VisionQuestionOut(**detail)  # type: ignore[arg-type]


@router.post(
    "/import",
    response_model=VisionImportOut,
    summary="上传题库文档（JSON / CSV / Markdown / TXT）",
)
async def import_document(
    file: UploadFile = File(...),
    role: str = Form(default="uploaded"),
    db: Session = Depends(get_session),
) -> VisionImportOut:
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="文件是空的")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB。题库文档是纯文本，不该这么大。",
        )

    filename = file.filename or "未命名"
    content, encoding = decode_bytes(raw)
    parsed = parse_document(filename, content)

    if not parsed.questions:
        detail = "；".join(parsed.warnings) or "没能从文件里解析出题目"
        raise HTTPException(status_code=400, detail=detail)

    info = import_questions(db, filename=filename, questions=parsed.questions, role=role)
    return VisionImportOut(
        **info,  # type: ignore[arg-type]
        source_format=parsed.source_format,
        warnings=[f"按 {encoding} 解码", *parsed.warnings],
    )


@router.get("/uploads", response_model=list[VisionUploadOut], summary="已上传的题库批次")
def read_uploads(db: Session = Depends(get_session)) -> list[VisionUploadOut]:
    return [VisionUploadOut(**item) for item in list_uploads(db)]  # type: ignore[arg-type]


@router.delete("/uploads/{batch}", summary="删掉一个上传批次（连带它的自测记录）")
def remove_upload(batch: str, db: Session = Depends(get_session)) -> dict[str, int]:
    removed = delete_upload(db, batch)
    if removed == 0:
        raise HTTPException(status_code=404, detail=f"没有这个批次：{batch}")
    return {"removed": removed}


@router.post("/questions/{question_id}/attempt", response_model=VisionAttemptOut, summary="记录一次自测（自评）")
def create_attempt(
    question_id: int,
    payload: VisionAttemptIn,
    db: Session = Depends(get_session),
) -> VisionAttemptOut:
    try:
        result = record_attempt(db, question_id=question_id, rating=payload.rating, note=payload.note)
    except VisionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return VisionAttemptOut(**result)  # type: ignore[arg-type]
