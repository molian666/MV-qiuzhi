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

from ..deps import get_llm, get_session
from ..providers.llm.base import LLMClient
from ..schemas.vision import (
    VisionAdoptIn,
    VisionAttemptIn,
    VisionAttemptOut,
    VisionDeleteIn,
    VisionDraftOut,
    VisionGenerateIn,
    VisionGenerateOut,
    VisionImportOut,
    VisionMetaOut,
    VisionQuestionBriefOut,
    VisionQuestionOut,
    VisionTagsIn,
    VisionUploadOut,
)
from ..vision.import_doc import decode_bytes, parse_document
from ..vision_service import (
    VisionError,
    adopt_draft,
    adopt_questions,
    delete_question,
    delete_questions,
    delete_upload,
    discard_draft,
    generate_draft,
    get_question,
    import_questions,
    list_drafts,
    list_questions,
    list_uploads,
    meta,
    record_attempt,
    update_question_tags,
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
    tag: list[str] | None = Query(default=None, max_length=20),
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_session),
) -> list[VisionQuestionBriefOut]:
    """题目清单。`tag` 可重复传，多选且取交集：
    `?tag=高频&tag=易错` 只返回两个标签都带上的题。
    """
    items = list_questions(
        db,
        role=role,
        category=category,
        max_difficulty=max_difficulty,
        keyword=keyword,
        tag=tag,
        limit=limit,
    )
    return [VisionQuestionBriefOut(**item) for item in items]  # type: ignore[arg-type]


@router.get("/questions/{question_id}", response_model=VisionQuestionOut, summary="题目详情：要点、参考答案、追问、常见错误")
def read_question(question_id: int, db: Session = Depends(get_session)) -> VisionQuestionOut:
    detail = get_question(db, question_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"没有这道题：{question_id}")
    return VisionQuestionOut(**detail)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 删题：单题 + 批量
#
# 路由顺序：批量端点 `/questions/delete` 是**两段**路径，与三段的
# `/questions/{question_id}/attempt` 不会互相遮蔽；但它和 `{question_id}`
# 同处一级，所以紧挨着声明，别让后来者插在中间。
# --------------------------------------------------------------------------- #


@router.delete("/questions/{question_id}", summary="删除一道题（连带它的自测记录，不可撤销）")
def remove_question(question_id: int, db: Session = Depends(get_session)) -> dict[str, int]:
    """★ 不可逆：题连同它的自测记录一起删掉。

    返回 `{"deleted": 1}` 而不是 204，是为了跟 `DELETE /uploads/{batch}` 的
    `{"removed": n}` 一个风格：调用方拿得到"到底删掉了几条"。
    草稿不从这里删（草稿还没进题库），它们走 `DELETE /drafts/{batch}`。
    """
    if not delete_question(db, question_id):
        raise HTTPException(status_code=404, detail=f"没有这道题：{question_id}")
    return {"deleted": 1}


@router.post("/questions/delete", summary="批量删除题目（勾选多道一起删）")
def remove_questions(payload: VisionDeleteIn, db: Session = Depends(get_session)) -> dict[str, int]:
    """多选删除。返回真正删掉的条数：不存在的 id、草稿、已经删过的都静默跳过——
    界面上的勾选集合可能比库里的旧，报错只会让人重试一个本来就会失败的请求。
    """
    return {"deleted": delete_questions(db, payload.question_ids)}


@router.post(
    "/import",
    response_model=VisionImportOut,
    summary="上传题库文档（JSON / CSV / 面经 Markdown / TXT）",
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


@router.post(
    "/questions/{question_id}/tags",
    response_model=VisionQuestionBriefOut,
    summary="给一道题添加/移除标记标签（高频、易错等，可自定义）",
)
def update_question_tags_route(
    question_id: int,
    payload: VisionTagsIn,
    db: Session = Depends(get_session),
) -> VisionQuestionBriefOut:
    """给一道题打上 / 摘下「高频」「易错」这类标记，或自定义标签。

    只影响 `标记:` 那一类：分类与「上传:」来源不动。更新后立即返回这题的
    清单信息（tags 已去掉前缀），前端拿它刷新列表与详情。
    移除不存在的标签是静默的——重复点击不会报错。
    """
    updated = update_question_tags(
        db, question_id=question_id, add=payload.add, remove=payload.remove
    )
    if updated is None:
        raise HTTPException(status_code=404, detail=f"没有这道题：{question_id}")
    return VisionQuestionBriefOut(**updated)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 模型出题：草稿 → 采纳
# --------------------------------------------------------------------------- #


@router.post(
    "/generate",
    response_model=VisionGenerateOut,
    summary="让模型出题（先存为草稿，采纳后才进入抽题池）",
)
async def generate_questions_route(
    payload: VisionGenerateIn,
    db: Session = Depends(get_session),
    llm: LLMClient = Depends(get_llm),
) -> VisionGenerateOut:
    try:
        result = await generate_draft(
            db,
            llm=llm,
            role=payload.role,
            count=payload.count,
            category=payload.category,
            difficulty=payload.difficulty,
            question_type=payload.question_type,
            domain=payload.domain,
            mode=payload.mode,
            batch=payload.batch,
            note=payload.note,
        )
    except VisionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return VisionGenerateOut(**result)  # type: ignore[arg-type]


@router.get("/drafts", response_model=list[VisionDraftOut], summary="待采纳的草稿批次")
def read_drafts(db: Session = Depends(get_session)) -> list[VisionDraftOut]:
    return [VisionDraftOut(**item) for item in list_drafts(db)]  # type: ignore[arg-type]


@router.post("/drafts/{batch}/adopt", summary="采纳这批草稿（进入抽题池）")
def adopt_draft_route(batch: str, db: Session = Depends(get_session)) -> dict[str, int]:
    try:
        adopted = adopt_draft(db, batch)
    except VisionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"adopted": adopted}


@router.post("/questions/adopt", summary="按题采纳草稿（只采纳选中的几道）")
def adopt_questions_route(payload: VisionAdoptIn, db: Session = Depends(get_session)) -> dict[str, int]:
    """生成一批 5 道、只满意 2 道时用这个。

    只对「模型生成 + 仍是草稿」的题生效：不是草稿的会被忽略（已经采纳过的不重复计数），
    非生成来源的题一律不动——这个接口不该能改动已有的题。
    """
    return {"adopted": adopt_questions(db, payload.question_ids)}


@router.delete("/drafts/{batch}", summary="丢弃这批草稿")
def discard_draft_route(batch: str, db: Session = Depends(get_session)) -> dict[str, int]:
    try:
        removed = discard_draft(db, batch)
    except VisionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"removed": removed}
