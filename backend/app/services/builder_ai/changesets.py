from __future__ import annotations

import copy
import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.core.db import utcnow
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models.builder import BuilderAIChangeSet, BuilderProject
from app.models.enums import BuilderStatus
from app.services import builder
from app.services.builder_ai.schemas import AgentOperation

ALLOWED_ROOTS = {"name", "base_url", "auth", "user_inputs", "streams"}
SENSITIVE_KEYS = ("password", "secret", "token", "api_key", "apikey", "authorization")


def definition_hash(definition: dict[str, Any]) -> str:
    encoded = json.dumps(definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _segments(path: str) -> list[str]:
    if not path.startswith("/"):
        raise ValidationError("Đường dẫn thay đổi không hợp lệ.", code="AI_CHANGE_PATH_INVALID")
    parts = [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]
    if not parts or parts[0] not in ALLOWED_ROOTS or len(parts) > 12:
        raise ValidationError(
            "AI chỉ được đề xuất thay đổi trong định nghĩa connector.",
            code="AI_CHANGE_PATH_BLOCKED", details={"path": path},
        )
    return parts


def _index(value: str, size: int, *, allow_end: bool = False) -> int:
    if value == "-" and allow_end:
        return size
    try:
        parsed = int(value)
    except ValueError:
        raise ValidationError("Chỉ số thay đổi không hợp lệ.", code="AI_CHANGE_PATH_INVALID") from None
    limit = size if allow_end else size - 1
    if parsed < 0 or parsed > limit:
        raise ValidationError("Chỉ số thay đổi nằm ngoài phạm vi.", code="AI_CHANGE_PATH_INVALID")
    return parsed


def apply_operations(
    definition: dict[str, Any], operations: list[AgentOperation],
) -> dict[str, Any]:
    if not operations or len(operations) > 50:
        raise ValidationError("Đề xuất không có thay đổi hợp lệ.", code="AI_CHANGE_EMPTY")
    result = copy.deepcopy(definition)
    for operation in operations:
        parts = _segments(operation.path)
        current: Any = result
        for part in parts[:-1]:
            if isinstance(current, list):
                current = current[_index(part, len(current))]
            elif isinstance(current, dict) and part in current:
                current = current[part]
            else:
                raise ValidationError(
                    "Đường dẫn thay đổi không tồn tại.", code="AI_CHANGE_PATH_INVALID",
                    details={"path": operation.path},
                )
        leaf = parts[-1]
        value: Any = None
        if operation.op != "remove":
            try:
                value = json.loads(operation.value_json)
            except ValueError:
                raise ValidationError(
                    "Giá trị thay đổi không phải JSON hợp lệ.", code="AI_CHANGE_VALUE_INVALID",
                ) from None
        if isinstance(current, list):
            if operation.op == "add":
                current.insert(_index(leaf, len(current), allow_end=True), value)
            elif operation.op == "replace":
                current[_index(leaf, len(current))] = value
            else:
                current.pop(_index(leaf, len(current)))
        elif isinstance(current, dict):
            if operation.op == "remove":
                if leaf not in current:
                    raise ValidationError("Không tìm thấy trường cần xóa.", code="AI_CHANGE_PATH_INVALID")
                del current[leaf]
            elif operation.op == "replace" and leaf not in current:
                raise ValidationError("Không tìm thấy trường cần thay thế.", code="AI_CHANGE_PATH_INVALID")
            else:
                current[leaf] = value
        else:
            raise ValidationError("Đường dẫn thay đổi không hợp lệ.", code="AI_CHANGE_PATH_INVALID")
    for field in result.get("user_inputs") or []:
        if field.get("secret") and field.get("default") not in (None, ""):
            raise ValidationError(
                "AI không được đặt giá trị mặc định cho thông tin bí mật.",
                code="AI_CHANGE_LITERAL_SECRET",
            )
    for stream in result.get("streams") or []:
        rows = [*(stream.get("headers") or []), *(stream.get("query_params") or [])]
        rows.extend(((stream.get("request_body") or {}).get("entries") or []))
        for row in rows:
            key = str(row.get("key") or "").lower()
            value = str(row.get("value") or "")
            if any(hint in key for hint in SENSITIVE_KEYS) and "config[" not in value:
                raise ValidationError(
                    "AI không được ghi credential trực tiếp vào request.",
                    code="AI_CHANGE_LITERAL_SECRET", details={"field": row.get("key")},
                )
    # This is the authoritative semantic gate. A proposal that cannot compile
    # never reaches the review card and can therefore never be applied.
    builder.compile_manifest(result)
    return result


async def get_change_set(
    session: AsyncSession, ctx: RequestContext, change_set_id: uuid.UUID,
) -> BuilderAIChangeSet:
    item = await session.scalar(select(BuilderAIChangeSet).where(
        BuilderAIChangeSet.id == change_set_id,
        BuilderAIChangeSet.workspace_id == ctx.workspace_id,
    ))
    if item is None:
        raise NotFoundError("Không tìm thấy đề xuất AI.", code="AI_CHANGE_NOT_FOUND")
    return item


async def create_change_set(
    session: AsyncSession,
    ctx: RequestContext,
    project: BuilderProject,
    *,
    session_id: uuid.UUID | None,
    operations: list[AgentOperation],
    reason: str,
    evidence: list[dict[str, Any]],
    model: str,
    prompt_version: str,
) -> BuilderAIChangeSet:
    previous = copy.deepcopy(project.definition or {})
    proposed = apply_operations(previous, operations)
    item = BuilderAIChangeSet(
        workspace_id=ctx.workspace_id, project_id=project.id, session_id=session_id,
        base_hash=definition_hash(previous), proposed_hash=definition_hash(proposed),
        previous_definition=previous, proposed_definition=proposed,
        operations=[operation.model_dump() for operation in operations],
        reason=reason, evidence=evidence, model=model, prompt_version=prompt_version,
        created_by=ctx.user_id,
    )
    session.add(item)
    await session.flush()
    return item


async def apply_change_set(
    session: AsyncSession, ctx: RequestContext, project: BuilderProject,
    item: BuilderAIChangeSet,
) -> None:
    if item.project_id != project.id or item.status != "PROPOSED":
        raise ConflictError("Đề xuất AI không còn ở trạng thái có thể áp dụng.", code="AI_CHANGE_NOT_APPLICABLE")
    if definition_hash(project.definition or {}) != item.base_hash:
        raise ConflictError(
            "Connector đã thay đổi sau khi AI tạo đề xuất. Hãy yêu cầu AI tạo lại đề xuất mới.",
            code="AI_CHANGE_STALE",
        )
    builder.compile_manifest(item.proposed_definition)
    project.definition = copy.deepcopy(item.proposed_definition)
    project.last_test_ok = None
    project.status = BuilderStatus.DRAFT
    project.updated_by = ctx.user_id
    item.status = "APPLIED"
    item.decided_by = ctx.user_id
    item.decided_at = utcnow()
    await session.flush()


async def reject_change_set(ctx: RequestContext, item: BuilderAIChangeSet) -> None:
    if item.status != "PROPOSED":
        raise ConflictError("Đề xuất AI đã được xử lý.", code="AI_CHANGE_NOT_APPLICABLE")
    item.status = "REJECTED"
    item.decided_by = ctx.user_id
    item.decided_at = utcnow()


async def undo_change_set(
    session: AsyncSession, ctx: RequestContext, project: BuilderProject,
    item: BuilderAIChangeSet,
) -> None:
    if item.project_id != project.id or item.status != "APPLIED":
        raise ConflictError("Chỉ có thể hoàn tác đề xuất đã áp dụng.", code="AI_CHANGE_NOT_UNDOABLE")
    if definition_hash(project.definition or {}) != item.proposed_hash:
        raise ConflictError(
            "Connector đã được sửa sau lần Apply này nên không thể hoàn tác tự động.",
            code="AI_CHANGE_UNDO_STALE",
        )
    project.definition = copy.deepcopy(item.previous_definition)
    project.last_test_ok = None
    project.status = BuilderStatus.DRAFT
    project.updated_by = ctx.user_id
    item.status = "UNDONE"
    item.decided_by = ctx.user_id
    item.decided_at = utcnow()
    await session.flush()
