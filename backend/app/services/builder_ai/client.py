from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from app.core.config import settings
from app.core.errors import AppError, ErrorCategory
from app.core.logging import log_event

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger(__name__)


class OpenAIBuilderClient:
    def __init__(self) -> None:
        if not settings.openai_api_key.strip():
            raise AppError(
                "AI Builder chưa được cấu hình. Hãy thêm OPENAI_API_KEY vào môi trường chạy API.",
                code="AI_NOT_CONFIGURED", category=ErrorCategory.CONFIGURATION,
                status_code=503,
            )
        self.client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            timeout=settings.openai_timeout_seconds,
            max_retries=2,
        )

    async def structured(
        self,
        *,
        model: str,
        instructions: str,
        prompt: str,
        schema: type[T],
        actor_id: str,
        operation: str,
        project_id: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> T:
        content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
        content.extend(attachments or [])
        safety_id = hashlib.sha256(actor_id.encode()).hexdigest()[:64]
        started = time.perf_counter()
        try:
            response = await self.client.responses.parse(
                model=model,
                instructions=instructions,
                input=[{"role": "user", "content": content}],
                text_format=schema,
                safety_identifier=safety_id,
                prompt_cache_key=f"builder:{schema.__name__}",
                store=False,
            )
            if response.output_parsed is None:
                raise ValueError("structured response did not contain parsed output")
            usage = response.usage
            log_event(
                logger,
                logging.INFO,
                "builder.ai.completed",
                operation=operation,
                project_id=project_id,
                model=model,
                latency_ms=round((time.perf_counter() - started) * 1000),
                input_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
                output_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
                total_tokens=getattr(usage, "total_tokens", 0) if usage else 0,
            )
            return response.output_parsed
        except AppError:
            raise
        except Exception as exc:  # provider detail stays server-side
            log_event(
                logger,
                logging.WARNING,
                "builder.ai.failed",
                operation=operation,
                project_id=project_id,
                model=model,
                latency_ms=round((time.perf_counter() - started) * 1000),
                error_type=type(exc).__name__,
            )
            raise AppError(
                "OpenAI chưa thể xử lý yêu cầu này. Hãy thử lại sau.",
                code="AI_PROVIDER_ERROR", category=ErrorCategory.UNKNOWN,
                status_code=502, technical_message=f"{type(exc).__name__}: {exc}"[:1000],
            ) from exc
