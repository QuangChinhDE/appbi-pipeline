"""AI-native Connector Builder API.

Every route is workspace-scoped and permission checked. OpenAI can analyze and
propose; only explicit product endpoints can create or mutate a project.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import Field

from app.api.deps import CtxDep, SessionDep
from app.api.v1.builder import BUILDER_ICONS, _detail
from app.core.config import settings
from app.core.errors import AppError, ErrorCategory, ValidationError
from app.core.permissions import Action, Module
from app.models.builder import BuilderAISource
from app.schemas.common import ORMModel
from app.services import audit, builder
from app.services.builder_ai import changesets
from app.services.builder_ai.ingestion import crawl_url, validate_upload
from app.services.builder_ai.parsing import textual_content
from app.services.builder_ai.redaction import redact_text, sanitize
from app.services.builder_ai.service import (
    BuilderAIService, change_set_view, plan_view, source_view,
)

router = APIRouter(prefix="/builder", tags=["builder-ai"])
service = BuilderAIService()


class URLSourceRequest(ORMModel):
    url: str = Field(min_length=8, max_length=2048)
    project_id: uuid.UUID | None = None


class PlanRequest(ORMModel):
    source_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)
    intent: str | None = Field(default=None, max_length=4000)


class PlanStreamReview(ORMModel):
    source_name: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=160)
    enabled: bool = True


class FromPlanRequest(ORMModel):
    plan_id: uuid.UUID
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    icon: str | None = None
    streams: list[PlanStreamReview] | None = Field(default=None, max_length=200)


class ChatRequest(ORMModel):
    message: str = Field(min_length=1, max_length=8000)
    stream_name: str | None = Field(default=None, max_length=160)
    section: str | None = Field(default=None, max_length=60)
    test_run_id: uuid.UUID | None = None


def _require_ai() -> None:
    if not settings.openai_api_key.strip():
        raise AppError(
            "AI Builder chưa được cấu hình. Hãy thêm OPENAI_API_KEY vào môi trường chạy API.",
            code="AI_NOT_CONFIGURED", category=ErrorCategory.CONFIGURATION, status_code=503,
        )


@router.post("/ai/sources", status_code=201)
async def upload_source(
    session: SessionDep,
    ctx: CtxDep,
    file: UploadFile = File(...),
    project_id: uuid.UUID | None = Form(default=None),
) -> dict[str, Any]:
    ctx.require(Module.CONNECTORS, Action.CREATE)
    if project_id:
        await builder.get_project(session, ctx, project_id)
    content = await file.read(settings.builder_ai_source_max_bytes + 1)
    validate_upload(file.filename or "document", file.content_type, content)
    extracted = textual_content(file.filename or "document", file.content_type, content)
    source = BuilderAISource(
        workspace_id=ctx.workspace_id, project_id=project_id,
        name=(file.filename or "document")[:300], source_type="FILE",
        mime_type=file.content_type, content=content, extracted_text=extracted,
        size_bytes=len(content), created_by=ctx.user_id,
    )
    session.add(source)
    await session.flush()
    await audit.record(
        session, ctx, action="builder.ai.source.uploaded",
        resource_type="builder_ai_source", resource_id=source.id,
        resource_name=source.name, after={"mime_type": source.mime_type, "size_bytes": len(content)},
    )
    await session.commit()
    await session.refresh(source)
    return source_view(source)


@router.post("/ai/sources/url", status_code=201)
async def add_url_source(
    payload: URLSourceRequest, session: SessionDep, ctx: CtxDep,
) -> dict[str, Any]:
    ctx.require(Module.CONNECTORS, Action.CREATE)
    if payload.project_id:
        await builder.get_project(session, ctx, payload.project_id)
    try:
        crawled = await crawl_url(payload.url.strip())
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError(
            "Không thể tải tài liệu từ URL này.", code="AI_SOURCE_FETCH_FAILED",
            details={"reason": f"{type(exc).__name__}: {exc}"[:300]},
        ) from exc
    source = BuilderAISource(
        workspace_id=ctx.workspace_id, project_id=payload.project_id,
        name=redact_text(payload.url.strip(), limit=300), source_type="URL", mime_type="text/plain",
        source_url=redact_text(payload.url.strip(), limit=2048), extracted_text=crawled.text,
        size_bytes=crawled.size_bytes, evidence=sanitize(crawled.pages), created_by=ctx.user_id,
    )
    session.add(source)
    await session.flush()
    await audit.record(
        session, ctx, action="builder.ai.source.url_added",
        resource_type="builder_ai_source", resource_id=source.id,
        resource_name=source.name, after={"pages": len(crawled.pages), "size_bytes": crawled.size_bytes},
    )
    await session.commit()
    await session.refresh(source)
    return source_view(source)


@router.delete("/ai/sources/{source_id}", status_code=204)
async def delete_source(source_id: uuid.UUID, session: SessionDep, ctx: CtxDep) -> None:
    ctx.require(Module.CONNECTORS, Action.CREATE)
    source = await service.get_source(session, ctx, source_id)
    await session.delete(source)
    await session.commit()


@router.post("/ai/sources/{source_id}/analyze")
async def analyze_source(source_id: uuid.UUID, session: SessionDep, ctx: CtxDep) -> dict[str, Any]:
    ctx.require(Module.CONNECTORS, Action.CREATE)
    source = await service.get_source(session, ctx, source_id)
    # OpenAPI/Postman parsing remains available without a key; unstructured
    # sources raise AI_NOT_CONFIGURED only when model understanding is needed.
    knowledge = await service.analyze_source(session, ctx, source)
    await session.commit()
    return {"source": source_view(source), "knowledge": knowledge.model_dump()}


@router.post("/ai/plans", status_code=201)
async def create_plan(payload: PlanRequest, session: SessionDep, ctx: CtxDep) -> dict[str, Any]:
    ctx.require(Module.CONNECTORS, Action.CREATE)
    _require_ai()
    plan = await service.create_plan(session, ctx, payload.source_ids, payload.intent)
    await audit.record(
        session, ctx, action="builder.ai.plan.created",
        resource_type="builder_ai_plan", resource_id=plan.id,
        after={"source_count": len(payload.source_ids), "model": plan.model},
    )
    await session.commit()
    await session.refresh(plan)
    return plan_view(plan)


@router.delete("/ai/plans/{plan_id}", status_code=204)
async def delete_plan(plan_id: uuid.UUID, session: SessionDep, ctx: CtxDep) -> None:
    ctx.require(Module.CONNECTORS, Action.CREATE)
    plan = await service.get_plan(session, ctx, plan_id)
    if plan.status != "READY":
        raise ValidationError(
            "Kế hoạch đã được dùng để tạo connector.", code="AI_PLAN_ALREADY_USED",
        )
    await session.delete(plan)
    await session.commit()


@router.post("/projects/from-plan", status_code=201)
async def create_project_from_plan(
    payload: FromPlanRequest, session: SessionDep, ctx: CtxDep,
) -> dict[str, Any]:
    ctx.require(Module.CONNECTORS, Action.CREATE)
    if payload.icon is not None and payload.icon not in BUILDER_ICONS:
        raise ValidationError("Icon connector không hợp lệ.", code="BUILDER_ICON_INVALID")
    plan = await service.get_plan(session, ctx, payload.plan_id)
    project = await service.create_project_from_plan(
        session,
        ctx,
        plan,
        name=payload.name,
        description=payload.description,
        icon=payload.icon,
        stream_reviews=[item.model_dump() for item in payload.streams]
        if payload.streams is not None else None,
    )
    await audit.record(
        session, ctx, action="builder.project.created_from_ai_plan",
        resource_type="builder_project", resource_id=project.id, resource_name=project.name,
        after={"plan_id": str(plan.id), "icon": project.icon},
    )
    await session.commit()
    await session.refresh(project)
    return _detail(project).model_dump()


@router.get("/projects/{project_id}/ai/session")
async def get_ai_session(project_id: uuid.UUID, session: SessionDep, ctx: CtxDep) -> dict[str, Any]:
    ctx.require(Module.CONNECTORS, Action.VIEW)
    project = await builder.get_project(session, ctx, project_id)
    view = await service.session_view(session, ctx, project)
    await session.commit()
    return view


@router.post("/projects/{project_id}/ai/chat")
async def chat(
    project_id: uuid.UUID, payload: ChatRequest, session: SessionDep, ctx: CtxDep,
) -> StreamingResponse:
    ctx.require(Module.CONNECTORS, Action.EDIT)
    _require_ai()
    project = await builder.get_project(session, ctx, project_id)

    async def events():
        yield "event: progress\ndata: " + json.dumps({
            "stage": "reading_context", "message": "Đang đọc cấu hình và lần test hiện tại...",
        }, ensure_ascii=False) + "\n\n"
        try:
            ai_session, answer, item = await service.chat(
                session, ctx, project, message=payload.message,
                stream_name=payload.stream_name, section=payload.section,
                test_run_id=payload.test_run_id,
            )
            await audit.record(
                session, ctx, action="builder.ai.proposed",
                resource_type="builder_project", resource_id=project.id,
                resource_name=project.name,
                after={"change_set_id": str(item.id) if item else None, "has_changes": bool(item)},
            )
            await session.commit()
            yield "event: final\ndata: " + json.dumps({
                "session_id": str(ai_session.id),
                "message": answer.assistant_message,
                "change_set": change_set_view(item) if item else None,
            }, ensure_ascii=False, default=str) + "\n\n"
        except AppError as exc:
            await session.rollback()
            yield "event: error\ndata: " + json.dumps(
                exc.to_envelope("")["error"], ensure_ascii=False,
            ) + "\n\n"

    return StreamingResponse(
        events(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/projects/{project_id}/ai/change-sets/{change_set_id}")
async def get_change_set(
    project_id: uuid.UUID, change_set_id: uuid.UUID, session: SessionDep, ctx: CtxDep,
) -> dict[str, Any]:
    ctx.require(Module.CONNECTORS, Action.VIEW)
    await builder.get_project(session, ctx, project_id)
    item = await changesets.get_change_set(session, ctx, change_set_id)
    if item.project_id != project_id:
        raise ValidationError("Đề xuất không thuộc connector này.", code="AI_CHANGE_PROJECT_MISMATCH")
    return change_set_view(item)


async def _mutate_change_set(
    action: str, project_id: uuid.UUID, change_set_id: uuid.UUID,
    session: SessionDep, ctx: CtxDep,
) -> dict[str, Any]:
    ctx.require(Module.CONNECTORS, Action.EDIT)
    project = await builder.get_project(session, ctx, project_id)
    item = await changesets.get_change_set(session, ctx, change_set_id)
    if item.project_id != project.id:
        raise ValidationError("Đề xuất không thuộc connector này.", code="AI_CHANGE_PROJECT_MISMATCH")
    if action == "apply":
        await changesets.apply_change_set(session, ctx, project, item)
    elif action == "reject":
        await changesets.reject_change_set(ctx, item)
    else:
        await changesets.undo_change_set(session, ctx, project, item)
    await audit.record(
        session, ctx, action=f"builder.ai.change_set.{action}",
        resource_type="builder_project", resource_id=project.id, resource_name=project.name,
        after={"change_set_id": str(item.id), "status": item.status},
    )
    await session.commit()
    await session.refresh(project)
    return {"project": _detail(project).model_dump(), "change_set": change_set_view(item)}


@router.post("/projects/{project_id}/ai/change-sets/{change_set_id}/apply")
async def apply_change_set(project_id: uuid.UUID, change_set_id: uuid.UUID, session: SessionDep, ctx: CtxDep):
    return await _mutate_change_set("apply", project_id, change_set_id, session, ctx)


@router.post("/projects/{project_id}/ai/change-sets/{change_set_id}/reject")
async def reject_change_set(project_id: uuid.UUID, change_set_id: uuid.UUID, session: SessionDep, ctx: CtxDep):
    return await _mutate_change_set("reject", project_id, change_set_id, session, ctx)


@router.post("/projects/{project_id}/ai/change-sets/{change_set_id}/undo")
async def undo_change_set(project_id: uuid.UUID, change_set_id: uuid.UUID, session: SessionDep, ctx: CtxDep):
    return await _mutate_change_set("undo", project_id, change_set_id, session, ctx)
