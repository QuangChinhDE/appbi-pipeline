from __future__ import annotations

import base64
import copy
import json
import mimetypes
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.context import RequestContext
from app.core.db import utcnow
from app.core.errors import NotFoundError, ValidationError
from app.models.builder import (
    BuilderAIChangeSet, BuilderAIMessage, BuilderAIPlan, BuilderAISession,
    BuilderAISource, BuilderProject, BuilderTestRun, BuilderTestSession,
)
from app.services import builder
from app.services.builder_ai import changesets
from app.services.builder_ai.client import OpenAIBuilderClient
from app.services.builder_ai.parsing import deterministic_knowledge, textual_content
from app.services.builder_ai.prompts import (
    AGENT_INSTRUCTIONS, KNOWLEDGE_INSTRUCTIONS, PLAN_INSTRUCTIONS, PROMPT_VERSION,
)
from app.services.builder_ai.redaction import redact_text, sanitize, sanitize_definition
from app.services.builder_ai.schemas import (
    AgentAnswer, ApiKnowledge, ConnectorPlan, plan_to_definition,
)
from app.services.builder_ai.tools import AgentPhase, record_tool


_INTENT_BASE_URL_RE = re.compile(
    r"(?:base[_\s-]*url|base\s+url|api\s+root|server|endpoint)"
    r"[^\n\r]{0,80}?(https?://[^\s,;]+)",
    re.IGNORECASE,
)
_INTENT_DOMAIN_RE = re.compile(
    r"(?:domain|host)[^\n\r]{0,80}?"
    r"([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9-]+)+)",
    re.IGNORECASE,
)


def _intent_base_url_override(intent: str | None, current_base_url: str) -> str | None:
    text = intent or ""
    url_matches = [
        match.rstrip(").,;")
        for match in _INTENT_BASE_URL_RE.findall(text)
    ]
    if len(set(url_matches)) == 1:
        parsed = urlparse(url_matches[0])
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return url_matches[0].rstrip("/")

    domain_matches = [
        match.rstrip(").,;").lower()
        for match in _INTENT_DOMAIN_RE.findall(text)
        if "://" not in match
    ]
    unique_domains = sorted(set(domain_matches))
    if len(unique_domains) != 1:
        return None
    domain = unique_domains[0]
    parsed_current = urlparse(current_base_url)
    if parsed_current.scheme not in {"http", "https"}:
        return None
    current_host = parsed_current.netloc.lower()
    labels = current_host.split(".")
    domain_labels = domain.split(".")
    prefix: list[str] = []
    if domain_labels and domain_labels[0] in labels:
        prefix = labels[:labels.index(domain_labels[0])]
    host = ".".join([*prefix, domain]) if prefix else domain
    path = parsed_current.path.rstrip("/")
    return f"{parsed_current.scheme}://{host}{path}"


def _source_attachment(source: BuilderAISource) -> list[dict[str, Any]]:
    if source.extracted_text:
        return [{
            "type": "input_text",
            "text": f"SOURCE {source.id} ({source.name})\n{redact_text(source.extracted_text, limit=120_000)}",
        }]
    if not source.content:
        return []
    encoded = base64.b64encode(source.content).decode()
    mime = source.mime_type or mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    if mime.startswith("image/"):
        return [{"type": "input_image", "image_url": f"data:{mime};base64,{encoded}", "detail": "high"}]
    if mime == "application/pdf" or Path(source.name).suffix.lower() == ".pdf":
        return [{
            "type": "input_file", "filename": source.name,
            "file_data": f"data:{mime};base64,{encoded}",
        }]
    return []


class BuilderAIService:
    async def get_source(
        self, session: AsyncSession, ctx: RequestContext, source_id: uuid.UUID,
    ) -> BuilderAISource:
        source = await session.scalar(select(BuilderAISource).where(
            BuilderAISource.id == source_id,
            BuilderAISource.workspace_id == ctx.workspace_id,
        ))
        if source is None:
            raise NotFoundError("Không tìm thấy tài liệu AI.", code="AI_SOURCE_NOT_FOUND")
        return source

    async def analyze_source(
        self, session: AsyncSession, ctx: RequestContext, source: BuilderAISource,
    ) -> ApiKnowledge:
        deterministic = deterministic_knowledge(str(source.id), source.name, source.extracted_text)
        if deterministic is not None:
            knowledge = deterministic
            source.status = "ANALYZED"
            source.knowledge = knowledge.model_dump()
            source.evidence = [item.model_dump() for item in knowledge.evidence]
            await session.flush()
            return knowledge

        client = OpenAIBuilderClient()
        knowledge = await client.structured(
            model=settings.openai_model_vision if source.mime_type and (
                source.mime_type.startswith("image/") or source.mime_type == "application/pdf"
            ) else settings.openai_model_planner,
            instructions=KNOWLEDGE_INSTRUCTIONS,
            prompt=(
                f"Normalize source_id={source.id}, name={source.name}. "
                "Every evidence item must use this exact source_id."
            ),
            schema=ApiKnowledge, actor_id=str(ctx.user_id or "system"),
            operation="analyze_source",
            project_id=str(source.project_id) if source.project_id else None,
            attachments=_source_attachment(source),
        )
        source.status = "ANALYZED"
        source.knowledge = sanitize(knowledge.model_dump())
        source.evidence = sanitize([item.model_dump() for item in knowledge.evidence])
        await session.flush()
        return knowledge

    async def create_plan(
        self,
        session: AsyncSession,
        ctx: RequestContext,
        source_ids: list[uuid.UUID],
        intent: str | None,
    ) -> BuilderAIPlan:
        if not source_ids or len(source_ids) > 20:
            raise ValidationError("Hãy chọn ít nhất một tài liệu.", code="AI_PLAN_SOURCES_REQUIRED")
        sources = [await self.get_source(session, ctx, source_id) for source_id in source_ids]
        knowledge: list[dict[str, Any]] = []
        for source in sources:
            if not source.knowledge:
                await self.analyze_source(session, ctx, source)
            knowledge.append(source.knowledge or {})
        client = OpenAIBuilderClient()
        plan = await client.structured(
            model=settings.openai_model_planner,
            instructions=PLAN_INSTRUCTIONS,
            prompt=json.dumps({
                "intent": (intent or "").strip()[:4000],
                "api_knowledge": knowledge,
            }, ensure_ascii=False),
            schema=ConnectorPlan, actor_id=str(ctx.user_id or "system"),
            operation="create_plan",
        )
        base_url_override = _intent_base_url_override(intent, plan.base_url)
        if base_url_override and base_url_override != plan.base_url:
            plan = plan.model_copy(update={
                "base_url": base_url_override,
                "assumptions": [
                    *plan.assumptions,
                    f"base_url overridden from user intent: {base_url_override}",
                ],
            })
        if not plan.streams:
            raise ValidationError(
                "Tài liệu chưa đủ để tạo stream có thể chạy.", code="AI_PLAN_NO_STREAMS",
                details={"unknowns": plan.unknowns},
            )
        # Compile before presenting the plan. The user never reviews something
        # that the product already knows it cannot represent.
        builder.compile_manifest(plan_to_definition(plan))
        row = BuilderAIPlan(
            workspace_id=ctx.workspace_id, source_ids=[str(item) for item in source_ids],
            intent=(intent or "").strip() or None, plan=plan.model_dump(),
            model=settings.openai_model_planner, prompt_version=PROMPT_VERSION,
            created_by=ctx.user_id,
        )
        session.add(row)
        await session.flush()
        return row

    async def get_plan(
        self, session: AsyncSession, ctx: RequestContext, plan_id: uuid.UUID,
    ) -> BuilderAIPlan:
        plan = await session.scalar(select(BuilderAIPlan).where(
            BuilderAIPlan.id == plan_id, BuilderAIPlan.workspace_id == ctx.workspace_id,
        ))
        if plan is None:
            raise NotFoundError("Không tìm thấy kế hoạch AI.", code="AI_PLAN_NOT_FOUND")
        return plan

    async def create_project_from_plan(
        self,
        session: AsyncSession,
        ctx: RequestContext,
        plan_row: BuilderAIPlan,
        *,
        name: str | None = None,
        description: str | None = None,
        icon: str | None = None,
        stream_reviews: list[dict[str, Any]] | None = None,
    ) -> BuilderProject:
        if plan_row.status != "READY":
            raise ValidationError("Kế hoạch AI đã được sử dụng.", code="AI_PLAN_ALREADY_USED")
        plan_data = copy.deepcopy(plan_row.plan)
        original_streams = plan_data.get("streams") or []
        if stream_reviews is not None:
            original_names = [str(item.get("name") or "") for item in original_streams]
            review_names = [str(item.get("source_name") or "") for item in stream_reviews]
            if len(review_names) != len(set(review_names)) or set(review_names) != set(original_names):
                raise ValidationError(
                    "Danh sách stream review không khớp với AI plan.",
                    code="AI_PLAN_STREAM_REVIEW_INVALID",
                )
            reviews = {str(item["source_name"]): item for item in stream_reviews}
            selected: list[dict[str, Any]] = []
            selected_names: list[str] = []
            for stream in original_streams:
                review = reviews[str(stream["name"])]
                if not review.get("enabled", True):
                    continue
                reviewed_name = str(review.get("name") or "").strip()
                if not reviewed_name:
                    raise ValidationError(
                        "Tên stream không được để trống.", code="AI_PLAN_STREAM_NAME_REQUIRED",
                    )
                updated = copy.deepcopy(stream)
                updated["name"] = reviewed_name
                selected.append(updated)
                selected_names.append(reviewed_name.casefold())
            if not selected:
                raise ValidationError(
                    "Hãy chọn ít nhất một stream.", code="AI_PLAN_STREAM_REQUIRED",
                )
            if len(selected_names) != len(set(selected_names)):
                raise ValidationError(
                    "Tên stream sau khi review bị trùng.", code="AI_PLAN_STREAM_NAME_DUPLICATE",
                )
            plan_data["streams"] = selected
        if name is not None:
            plan_data["name"] = name.strip()
        if description is not None:
            plan_data["description"] = description.strip()
        if icon is not None:
            plan_data["icon"] = icon
        plan = ConnectorPlan.model_validate(plan_data)
        project_id = uuid.uuid4()
        definition = plan_to_definition(plan)
        builder.compile_manifest(definition)
        project = BuilderProject(
            id=project_id, workspace_id=ctx.workspace_id, name=plan.name,
            description=plan.description, icon=plan.icon,
            connector_key=builder.connector_key_for(plan.name, project_id),
            definition=definition, created_by=ctx.user_id, updated_by=ctx.user_id,
        )
        session.add(project)
        plan_row.status = "USED"
        await session.flush()
        source_ids: list[uuid.UUID] = []
        for value in plan_row.source_ids:
            try:
                source_ids.append(uuid.UUID(str(value)))
            except ValueError:
                continue
        if source_ids:
            sources = list((await session.scalars(select(BuilderAISource).where(
                BuilderAISource.id.in_(source_ids),
                BuilderAISource.workspace_id == ctx.workspace_id,
            ))).all())
            for source in sources:
                source.project_id = project.id
        return project

    async def get_or_create_session(
        self, session: AsyncSession, ctx: RequestContext, project: BuilderProject,
    ) -> BuilderAISession:
        ai_session = await session.scalar(select(BuilderAISession).where(
            BuilderAISession.project_id == project.id,
            BuilderAISession.workspace_id == ctx.workspace_id,
            BuilderAISession.created_by == ctx.user_id,
            BuilderAISession.status == "ACTIVE",
        ).order_by(BuilderAISession.created_at.desc()))
        if ai_session is None:
            ai_session = BuilderAISession(
                workspace_id=ctx.workspace_id, project_id=project.id,
                created_by=ctx.user_id,
            )
            session.add(ai_session)
            await session.flush()
        return ai_session

    async def session_view(
        self, session: AsyncSession, ctx: RequestContext, project: BuilderProject,
    ) -> dict[str, Any]:
        ai_session = await self.get_or_create_session(session, ctx, project)
        messages = list((await session.scalars(select(BuilderAIMessage).where(
            BuilderAIMessage.session_id == ai_session.id,
        ).order_by(BuilderAIMessage.created_at.asc()).limit(80))).all())
        latest = await session.scalar(select(BuilderAIChangeSet).where(
            BuilderAIChangeSet.session_id == ai_session.id,
            BuilderAIChangeSet.status.in_(["PROPOSED", "APPLIED"]),
        ).order_by(BuilderAIChangeSet.created_at.desc()))
        sources = list((await session.scalars(select(BuilderAISource).where(
            BuilderAISource.project_id == project.id,
            BuilderAISource.workspace_id == ctx.workspace_id,
        ).order_by(BuilderAISource.created_at.asc()))).all())
        return {
            "id": str(ai_session.id),
            "available": bool(settings.openai_api_key.strip()),
            "sources": [{
                "id": str(item.id), "name": item.name, "source_type": item.source_type,
                "status": item.status,
            } for item in sources],
            "messages": [{
                "id": str(item.id), "role": item.role, "content": item.content,
                "context": item.context, "created_at": item.created_at,
            } for item in messages],
            "change_set": change_set_view(latest) if latest else None,
        }

    async def chat(
        self,
        session: AsyncSession,
        ctx: RequestContext,
        project: BuilderProject,
        *,
        message: str,
        stream_name: str | None,
        section: str | None,
        test_run_id: uuid.UUID | None,
    ) -> tuple[BuilderAISession, AgentAnswer, BuilderAIChangeSet | None]:
        if not message.strip():
            raise ValidationError("Hãy nhập yêu cầu cho AI.", code="AI_MESSAGE_REQUIRED")
        ai_session = await self.get_or_create_session(session, ctx, project)
        test_evidence: dict[str, Any] | None = None
        if test_run_id:
            test_run = await session.scalar(select(BuilderTestRun).where(
                BuilderTestRun.id == test_run_id,
                BuilderTestRun.project_id == project.id,
                BuilderTestRun.workspace_id == ctx.workspace_id,
            ))
            if test_run is None:
                raise NotFoundError("Không tìm thấy lần test.", code="BUILDER_TEST_RUN_NOT_FOUND")
            test_evidence = test_run.evidence

        recent_messages = list((await session.scalars(select(BuilderAIMessage).where(
            BuilderAIMessage.session_id == ai_session.id,
        ).order_by(BuilderAIMessage.created_at.desc()).limit(12))).all())
        recent_conversation = [{
            "role": item.role, "content": redact_text(item.content, limit=4000),
        } for item in reversed(recent_messages)]

        project_sources = list((await session.scalars(select(BuilderAISource).where(
            BuilderAISource.project_id == project.id,
            BuilderAISource.workspace_id == ctx.workspace_id,
            BuilderAISource.status == "ANALYZED",
        ).order_by(BuilderAISource.created_at.desc()).limit(8))).all())
        relevant_knowledge: list[dict[str, Any]] = []
        selected = next((item for item in (project.definition or {}).get("streams", [])
                         if item.get("name") == stream_name), None)
        selected_path = str((selected or {}).get("path") or "")
        for source in project_sources:
            knowledge = sanitize(source.knowledge or {})
            endpoints = knowledge.get("endpoints") if isinstance(knowledge, dict) else None
            if isinstance(endpoints, list) and (stream_name or selected_path):
                matches = [item for item in endpoints if isinstance(item, dict) and (
                    (selected_path and item.get("path") == selected_path)
                    or (stream_name and stream_name.casefold() in str(item.get("summary") or "").casefold())
                )]
                knowledge = {**knowledge, "endpoints": (matches or endpoints[:8])[:12]}
            relevant_knowledge.append({
                "source_id": str(source.id), "source_name": source.name,
                "knowledge": knowledge,
            })

        context = {
            "project_id": str(project.id), "project_name": project.name,
            "selected_stream": stream_name, "selected_section": section,
            "definition": sanitize_definition(project.definition or {}),
            "test_evidence": sanitize(test_evidence),
            "api_knowledge": relevant_knowledge,
            "recent_conversation": recent_conversation,
        }
        session.add(BuilderAIMessage(
            session_id=ai_session.id, role="user", content=message.strip()[:8000],
            context={"stream": stream_name, "section": section, "test_run_id": str(test_run_id) if test_run_id else None},
            created_at=utcnow(),
        ))
        await record_tool(
            session, ai_session_id=ai_session.id, phase=AgentPhase.DIAGNOSE,
            tool_name="read_builder_context",
            result_summary={"stream": stream_name, "section": section, "has_test": bool(test_evidence)},
        )
        if test_evidence:
            await record_tool(
                session, ai_session_id=ai_session.id, phase=AgentPhase.DIAGNOSE,
                tool_name="read_test_evidence",
                result_summary={"test_run_id": str(test_run_id)},
            )
        client = OpenAIBuilderClient()
        answer = await client.structured(
            model=settings.openai_model_agent, instructions=AGENT_INSTRUCTIONS,
            prompt=json.dumps({"request": message.strip(), "context": context}, ensure_ascii=False),
            schema=AgentAnswer, actor_id=str(ctx.user_id or "system"),
            operation="builder_chat",
            project_id=str(project.id),
        )
        change_set = None
        if answer.operations:
            change_set = await changesets.create_change_set(
                session, ctx, project, session_id=ai_session.id,
                operations=answer.operations, reason=answer.change_summary,
                evidence=[item.model_dump() for item in answer.evidence],
                model=settings.openai_model_agent, prompt_version=PROMPT_VERSION,
            )
            await record_tool(
                session, ai_session_id=ai_session.id, phase=AgentPhase.PROPOSE,
                tool_name="propose_change_set",
                result_summary={"change_set_id": str(change_set.id), "operations": len(answer.operations)},
            )
        session.add(BuilderAIMessage(
            session_id=ai_session.id, role="assistant",
            content=answer.assistant_message[:20_000],
            context={"change_set_id": str(change_set.id) if change_set else None},
            created_at=utcnow(),
        ))
        await session.flush()
        return ai_session, answer, change_set


def source_view(source: BuilderAISource) -> dict[str, Any]:
    return {
        "id": str(source.id), "name": source.name, "source_type": source.source_type,
        "mime_type": source.mime_type, "source_url": source.source_url,
        "size_bytes": source.size_bytes, "status": source.status,
        "knowledge": source.knowledge, "created_at": source.created_at,
    }


def plan_view(row: BuilderAIPlan) -> dict[str, Any]:
    return {"id": str(row.id), "status": row.status, "plan": row.plan}


def change_set_view(item: BuilderAIChangeSet) -> dict[str, Any]:
    return {
        "id": str(item.id), "project_id": str(item.project_id), "base_hash": item.base_hash,
        "status": item.status, "operations": item.operations, "reason": item.reason,
        "evidence": item.evidence, "model": item.model, "prompt_version": item.prompt_version,
        "created_at": item.created_at,
    }


async def purge_project_ai_data(
    session: AsyncSession, ctx: RequestContext, project: BuilderProject,
) -> None:
    """Remove private AI artifacts when a Builder project is deleted.

    Builder projects are soft-deleted for auditability, so database cascades do
    not run. Raw documentation, ephemeral credentials and conversation content
    have a different retention expectation and are explicitly removed here.
    """
    sources = list((await session.scalars(select(BuilderAISource).where(
        BuilderAISource.project_id == project.id,
        BuilderAISource.workspace_id == ctx.workspace_id,
    ))).all())
    source_ids = {str(item.id) for item in sources}
    if source_ids:
        plans = list((await session.scalars(select(BuilderAIPlan).where(
            BuilderAIPlan.workspace_id == ctx.workspace_id,
        ))).all())
        for plan in plans:
            if source_ids.intersection(str(item) for item in (plan.source_ids or [])):
                await session.delete(plan)

    for model in (BuilderAIChangeSet, BuilderAISession, BuilderTestRun, BuilderTestSession):
        rows = list((await session.scalars(select(model).where(
            model.project_id == project.id,
            model.workspace_id == ctx.workspace_id,
        ))).all())
        for row in rows:
            await session.delete(row)
    for source in sources:
        await session.delete(source)
    await session.flush()
