"""Product-owned scheduling (sections 17, 48/ADR-004 option B).

The canonical schedule lives in the product DB and the product worker fires the
trigger. The engine connection stays manual-like, so quota, audit and overlap
policy are enforced in one place and swapping engines does not change the UX.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterBadCronError, croniter

from app.core.config import settings
from app.core.db import utcnow
from app.core.errors import ValidationError
from app.models.enums import ScheduleType


def resolve_zone(name: str | None) -> ZoneInfo:
    """Lenient read path: a stored value that is no longer a valid zone must not
    take a page down. Input is validated separately by `require_zone`."""
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return ZoneInfo("UTC")


def require_zone(name: str | None) -> str:
    """Strict write path: reject an unknown timezone instead of silently
    computing in UTC while echoing the bad name back to the user."""
    candidate = (name or "").strip()
    if not candidate:
        return "UTC"
    try:
        ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        raise ValidationError(
            f"Múi giờ '{candidate}' không hợp lệ.",
            code="INVALID_TIMEZONE",
            details={"timezone": candidate},
        ) from None
    return candidate


def validate(config: dict[str, Any]) -> dict[str, Any]:
    """Normalize + validate a submitted schedule, returning what we persist."""
    raw_type = (config.get("type") or "MANUAL").strip().upper()
    try:
        schedule_type = ScheduleType(raw_type)
    except ValueError:
        raise ValidationError(
            f"Loại lịch chạy '{raw_type}' không hợp lệ.",
            code="INVALID_SCHEDULE_TYPE",
            details={"allowed": [t.value for t in ScheduleType]},
        ) from None
    timezone_name = require_zone(config.get("timezone") or "Asia/Bangkok")

    normalized: dict[str, Any] = {"type": schedule_type.value, "timezone": timezone_name}

    if schedule_type is ScheduleType.INTERVAL:
        interval = int(config.get("interval_seconds") or 0)
        if interval < settings.min_schedule_interval_seconds:
            raise ValidationError(
                f"Khoảng chạy tối thiểu là {settings.min_schedule_interval_seconds // 60} phút.",
                details={"min_interval_seconds": settings.min_schedule_interval_seconds},
            )
        normalized["interval_seconds"] = interval

    elif schedule_type is ScheduleType.DAILY:
        raw = config.get("time_of_day") or "02:00"
        try:
            hour, minute = (int(part) for part in raw.split(":"))
            time(hour, minute)
        except (ValueError, TypeError) as exc:
            raise ValidationError("Giờ chạy hằng ngày phải có dạng HH:mm.") from exc
        normalized["time_of_day"] = f"{hour:02d}:{minute:02d}"

    elif schedule_type is ScheduleType.CRON:
        expression = (config.get("cron_expression") or "").strip()
        if not expression:
            raise ValidationError("Cron expression không được để trống.")
        try:
            croniter(expression)
        except (CroniterBadCronError, ValueError) as exc:
            raise ValidationError(f"Cron expression không hợp lệ: {exc}") from exc
        probe = croniter(expression, utcnow())
        first = probe.get_next(datetime)
        second = probe.get_next(datetime)
        gap = (second - first).total_seconds()
        if gap < settings.min_schedule_interval_seconds:
            raise ValidationError(
                f"Cron chạy dày hơn mức tối thiểu "
                f"({settings.min_schedule_interval_seconds // 60} phút).",
            )
        normalized["cron_expression"] = expression

    return normalized


def next_run_at(
    schedule_type: ScheduleType,
    config: dict[str, Any],
    timezone_name: str,
    *,
    after: datetime | None = None,
) -> datetime | None:
    """Timezone-aware, DST-correct next fire time in UTC."""
    if schedule_type is ScheduleType.MANUAL:
        return None
    zone = resolve_zone(config.get("timezone") or timezone_name)
    base_utc = (after or utcnow()).astimezone(timezone.utc)
    local_now = base_utc.astimezone(zone)

    if schedule_type is ScheduleType.INTERVAL:
        interval = int(config.get("interval_seconds") or settings.min_schedule_interval_seconds)
        return base_utc + timedelta(seconds=interval)

    if schedule_type is ScheduleType.DAILY:
        raw = config.get("time_of_day") or "02:00"
        hour, minute = (int(part) for part in raw.split(":"))
        candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= local_now:
            candidate = candidate + timedelta(days=1)
        return candidate.astimezone(timezone.utc)

    if schedule_type is ScheduleType.CRON:
        expression = config.get("cron_expression")
        if not expression:
            return None
        try:
            return croniter(expression, local_now).get_next(datetime).astimezone(timezone.utc)
        except (CroniterBadCronError, ValueError):
            return None
    return None


def preview(
    schedule_type: ScheduleType, config: dict[str, Any], timezone_name: str, count: int = 3
) -> list[datetime]:
    """Next N fire times -- the wizard shows these so users can confirm intent."""
    out: list[datetime] = []
    cursor = utcnow()
    for _ in range(count):
        nxt = next_run_at(schedule_type, config, timezone_name, after=cursor)
        if nxt is None:
            break
        out.append(nxt)
        cursor = nxt
    return out


def describe(schedule_type: ScheduleType, config: dict[str, Any]) -> str:
    if schedule_type is ScheduleType.MANUAL:
        return "Chạy thủ công"
    if schedule_type is ScheduleType.INTERVAL:
        seconds = int(config.get("interval_seconds") or 0)
        if seconds % 86400 == 0:
            return f"Mỗi {seconds // 86400} ngày"
        if seconds % 3600 == 0:
            return f"Mỗi {seconds // 3600} giờ"
        return f"Mỗi {max(1, seconds // 60)} phút"
    if schedule_type is ScheduleType.DAILY:
        return f"Hằng ngày lúc {config.get('time_of_day', '02:00')}"
    return f"Cron: {config.get('cron_expression', '')}"


def freshness_deadline(
    schedule_type: ScheduleType, config: dict[str, Any], last_success: datetime | None
) -> datetime | None:
    """expected_next_success + grace, per section 66."""
    if last_success is None or schedule_type is ScheduleType.MANUAL:
        return None
    expected = next_run_at(schedule_type, config, config.get("timezone") or "UTC", after=last_success)
    if expected is None:
        return None
    if schedule_type is ScheduleType.INTERVAL:
        interval = int(config.get("interval_seconds") or 3600)
    else:
        interval = 86400
    grace = max(timedelta(minutes=30), timedelta(seconds=interval * 0.5))
    return expected + grace
