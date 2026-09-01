"""What a column actually holds, as opposed to what its type claims.

A warehouse reports `since` as `text`. Its values are `'1778056562'` -- a Unix
epoch. Anyone reading only the type writes `cast(since as timestamp)` and gets
either an error or nonsense, and nothing in the schema warns them. The same
gap turns `status text` into `where status = 'active'` against a column whose
values are `'0'` and `'10'`.

So this module asks the warehouse the questions a type cannot answer: how many
distinct values, how often null, what a few of them look like, and what shape
that suggests. It is useful to a person writing SQL by hand and it is the input
an assistant needs most.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

# A value is treated as an epoch only inside a plausible range: 2001-09-09 to
# 2033-05-18 in seconds. Bare integers are common; guessing "epoch" for a
# quantity column would be worse than saying nothing.
_EPOCH_MIN = 1_000_000_000
_EPOCH_MAX = 2_000_000_000
_EPOCH_MS_MIN = _EPOCH_MIN * 1000
_EPOCH_MS_MAX = _EPOCH_MAX * 1000

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2})?")
_HTML_ENTITY = re.compile(r"&(?:amp|lt|gt|quot|#\d{2,4});")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

# Below this many distinct values a column reads as a code list rather than
# free content, and the values themselves carry the meaning.
CODED_MAX_DISTINCT = 25
SAMPLE_SIZE = 5

# A foreign key in a narrow table repeats exactly as a status code does: four
# distinct values over a hundred rows. It is not a code list, and calling it one
# invites an author -- human or model -- to write a mapping from '83619' to some
# invented label. Names are the only signal that separates the two cases, and
# they are a reliable one in practice.
_KEY_SUFFIXES = ("_id", "_key", "_uid", "_uuid", "_code_id", "_fk")
_KEY_NAMES = ("id", "uid", "uuid", "guid")


def looks_like_key(name: str) -> bool:
    """True when the column name says identifier, whatever its values look like."""
    lowered = (name or "").strip().lower()
    return lowered in _KEY_NAMES or lowered.endswith(_KEY_SUFFIXES)

# JSON, arrays and structs cannot be counted with DISTINCT and cannot be cast to
# a string the same way everywhere. Their shape is not what a model author is
# asking about anyway, so they are counted for nulls and left at that.
_UNCOUNTABLE = ("json", "struct", "array", "record", "repeated", "geography", "bytes")


def kind_from_type(data_type: str) -> tuple[str, str] | None:
    """The kind a declared type settles on its own, with a note for the author.

    JSON and nested types are the ones worth naming. They cannot be sampled, so
    nothing else in the profile describes them, and every warehouse reads them
    with different syntax -- which is exactly when an author, or an assistant,
    guesses and gets a cast error.
    """
    lowered = (data_type or "").lower()
    if "json" in lowered:
        return "JSON", "Cột JSON: phải dùng hàm đọc JSON của kho dữ liệu, không ép kiểu thẳng."
    if "array" in lowered or "repeated" in lowered:
        return "ARRAY", "Cột mảng: phải unnest trước khi dùng, không ép kiểu thẳng."
    if "struct" in lowered or "record" in lowered:
        return "STRUCT", "Cột lồng nhau: truy cập từng trường con, không ép kiểu cả cột."
    return None


def _countable(data_type: str) -> bool:
    lowered = (data_type or "").lower()
    return not any(token in lowered for token in _UNCOUNTABLE)


@dataclass
class ColumnProfile:
    name: str
    data_type: str
    distinct_count: int | None = None
    null_ratio: float | None = None
    samples: list[str] = field(default_factory=list)
    #: EPOCH_SECONDS | EPOCH_MILLIS | ISO_DATETIME | CODED | UUID | JSON |
    #: NUMERIC_TEXT | HTML_ESCAPED | FREE_TEXT | UNKNOWN
    inferred_kind: str = "UNKNOWN"
    #: True when the column looks like it identifies a row on its own.
    unique_candidate: bool = False
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "data_type": self.data_type,
            "distinct_count": self.distinct_count,
            "null_ratio": self.null_ratio,
            "samples": self.samples,
            "inferred_kind": self.inferred_kind,
            "unique_candidate": self.unique_candidate,
            "notes": self.notes,
        }


def classify(values: list[str], data_type: str) -> tuple[str, list[str]]:
    """Read a handful of values and say what the column really is."""
    present = [value for value in values if value not in (None, "")]
    if not present:
        return "UNKNOWN", []

    notes: list[str] = []
    lowered = (data_type or "").lower()

    if all(_UUID.match(value) for value in present):
        return "UUID", notes

    if all(value.lstrip("-").isdigit() for value in present):
        numbers = [int(value) for value in present]
        if all(_EPOCH_MIN <= number <= _EPOCH_MAX for number in numbers):
            notes.append("Số giây kể từ 1970 (Unix epoch).")
            return "EPOCH_SECONDS", notes
        if all(_EPOCH_MS_MIN <= number <= _EPOCH_MS_MAX for number in numbers):
            notes.append("Số mili-giây kể từ 1970 (Unix epoch).")
            return "EPOCH_MILLIS", notes
        if "char" in lowered or "text" in lowered or "string" in lowered:
            notes.append("Số nhưng lưu dưới dạng chuỗi; cần ép kiểu trước khi tính.")
            return "NUMERIC_TEXT", notes

    if all(_ISO_DATE.match(value) for value in present):
        return "ISO_DATETIME", notes

    if all(value.startswith(("{", "[")) for value in present):
        for value in present:
            try:
                json.loads(value)
            except ValueError:
                break
        else:
            return "JSON", notes

    if any(_HTML_ENTITY.search(value) for value in present):
        notes.append("Chứa HTML escape. Giữ nguyên giá trị gốc trong model; "
                     "việc giải mã thuộc về tầng hiển thị.")
        return "HTML_ESCAPED", notes

    return "FREE_TEXT", notes


def _quote(dialect: str, identifier: str) -> str:
    if dialect == "bigquery":
        return f"`{identifier}`"
    return '"' + identifier.replace('"', '""') + '"'


def build_profile_sql(
    dialect: str, catalog: str | None, schema: str, relation: str,
    columns: list[dict[str, Any]], row_limit: int = 2000,
) -> str:
    """One statement that profiles every column, over a bounded sample.

    Bounded on purpose: a full scan of a fact table to learn that a column holds
    epochs is a warehouse bill nobody agreed to.
    """
    parts = [_quote(dialect, schema), _quote(dialect, relation)]
    if catalog:
        parts.insert(0, _quote(dialect, catalog))
    target = ".".join(parts)

    selects: list[str] = ["count(*) as _rows"]
    for index, column in enumerate(columns):
        name = column.get("name")
        if not name:
            continue
        quoted = _quote(dialect, str(name))
        selects.append(
            f"count(distinct {quoted}) as d_{index}"
            if _countable(str(column.get("data_type") or ""))
            else f"cast(null as int64) as d_{index}" if dialect == "bigquery"
            else f"cast(null as bigint) as d_{index}"
        )
        selects.append(f"countif({quoted} is null) as n_{index}"
                       if dialect == "bigquery"
                       else f"count(*) filter (where {quoted} is null) as n_{index}")
    return (
        f"select {', '.join(selects)} "
        f"from (select * from {target} limit {row_limit}) as _sampled"
    )


def build_sample_sql(
    dialect: str, catalog: str | None, schema: str, relation: str,
    column: str, limit: int = SAMPLE_SIZE,
) -> str:
    """Distinct non-null values for one column, newest rows not required."""
    parts = [_quote(dialect, schema), _quote(dialect, relation)]
    if catalog:
        parts.insert(0, _quote(dialect, catalog))
    target = ".".join(parts)
    quoted = _quote(dialect, column)
    return (
        f"select distinct cast({quoted} as string) as v from {target} "
        f"where {quoted} is not null limit {limit}"
        if dialect == "bigquery" else
        f"select distinct cast({quoted} as text) as v from {target} "
        f"where {quoted} is not null limit {limit}"
    )


JSON_SAMPLE_ROWS = 3
JSON_MAX_KEYS = 12


def build_json_sample_sql(
    dialect: str, catalog: str | None, schema: str, relation: str, column: str,
) -> str:
    """A few whole JSON values as text, so their keys can be read off.

    Keys are the one thing an author needs from a JSON column and the one thing
    the schema never says. Without them the only honest thing to write is a
    placeholder path, which compiles and returns a column of nulls.
    """
    parts = [_quote(dialect, schema), _quote(dialect, relation)]
    if catalog:
        parts.insert(0, _quote(dialect, catalog))
    target = ".".join(parts)
    quoted = _quote(dialect, column)
    cast = f"to_json_string({quoted})" if dialect == "bigquery" else f"cast({quoted} as text)"
    return (
        f"select {cast} as v from {target} "
        f"where {quoted} is not null limit {JSON_SAMPLE_ROWS}"
    )


def json_keys(values: list[str]) -> list[str]:
    """Top-level keys across a few JSON values, in first-seen order."""
    seen: list[str] = []
    for value in values:
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, list):
            parsed = next((item for item in parsed if isinstance(item, dict)), None)
        if not isinstance(parsed, dict):
            continue
        for key in parsed:
            if key not in seen:
                seen.append(key)
            if len(seen) >= JSON_MAX_KEYS:
                return seen
    return seen


def summarise(profiles: list[ColumnProfile]) -> str:
    """The profile as prose an assistant can read, one line per column.

    Deliberately terse: this goes into a prompt beside the rest of the context,
    and a paragraph per column would crowd out the models it needs to imitate.
    """
    lines: list[str] = []
    for item in profiles:
        bits = [f"{item.name} ({item.data_type})"]
        if item.inferred_kind not in ("UNKNOWN", "FREE_TEXT"):
            bits.append(item.inferred_kind)
        if item.distinct_count is not None:
            bits.append(f"{item.distinct_count} giá trị phân biệt")
        if item.null_ratio:
            bits.append(f"{round(item.null_ratio * 100)}% null")
        if item.unique_candidate:
            bits.append("có thể làm khoá")
        # Values are shown only where they are the meaning -- a status code list
        # -- never for free text, which is where customer content lives.
        if item.samples and item.inferred_kind in (
            "CODED", "EPOCH_SECONDS", "EPOCH_MILLIS", "NUMERIC_TEXT", "ISO_DATETIME",
        ):
            shown = ", ".join(repr(value) for value in item.samples[:4])
            bits.append(f"ví dụ: {shown}")
        line = " · ".join(bits)
        if item.notes:
            line = f"{line} — {' '.join(item.notes)}"
        lines.append(line)
    return "\n".join(lines)
