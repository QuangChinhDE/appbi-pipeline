"""Artifact schema-version dispatch.

dbt stamps every artifact with the schema it was written against:

    "metadata": {
        "dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v12.json",
        "dbt_version": "1.12.3",
        ...
    }

Reading an artifact without checking that stamp is how a dbt upgrade turns into
a ``KeyError`` in the middle of an index rebuild, with a stack trace that names
none of the causes.  Every reader in this package therefore asks
:func:`artifact_version` first and refuses a version it was not written for.

Refusing loudly is the whole design.  A reader that "does its best" with an
unknown schema produces a resource tree that is quietly missing resources, and
nothing about the UI says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.core.errors import ValidationError

_SCHEMA_URL = re.compile(
    r"https://schemas\.getdbt\.com/dbt/(?P<artifact>[a-z-]+)/v(?P<version>\d+)\.json",
)

#: Versions each reader is known to handle, highest first.
#:
#: Raise these only after running the fixture projects in
#: ``backend/tests/fixtures/dbt`` against the new dbt and confirming the parsers
#: still produce the same index -- that order is deliberate: engine lock first,
#: then fixtures, then parser compatibility, then the upgrade.
SUPPORTED: dict[str, tuple[int, ...]] = {
    "manifest": (12, 11),
    "run-results": (6, 5),
    "sources": (3,),
    "catalog": (1,),
}


@dataclass(frozen=True, slots=True)
class ArtifactVersion:
    artifact: str
    schema_version: int
    dbt_version: str | None
    adapter_type: str | None
    generated_at: str | None
    invocation_id: str | None
    raw_schema_version: str | None


def artifact_version(document: dict[str, Any], expected: str) -> ArtifactVersion:
    """Read and validate an artifact's version stamp.

    ``expected`` is the artifact kind: ``manifest``, ``run-results``,
    ``sources`` or ``catalog``.
    """
    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        raise ValidationError(
            "This dbt artifact has no metadata block, so its schema version "
            "cannot be established.",
            code="TRANSFORM_ARTIFACT_METADATA_MISSING",
            details={"artifact": expected},
        )
    raw = metadata.get("dbt_schema_version")
    match = _SCHEMA_URL.match(str(raw or ""))
    if match is None:
        raise ValidationError(
            f"Unrecognised dbt artifact schema `{raw}`.",
            code="TRANSFORM_ARTIFACT_SCHEMA_UNKNOWN",
            details={"artifact": expected, "dbt_schema_version": raw},
        )
    if match.group("artifact") != expected:
        raise ValidationError(
            f"Expected a dbt {expected} artifact but this is a "
            f"{match.group('artifact')} artifact.",
            code="TRANSFORM_ARTIFACT_KIND_MISMATCH",
            details={"artifact": expected, "found": match.group("artifact")},
        )
    version = int(match.group("version"))
    supported = SUPPORTED.get(expected, ())
    if version not in supported:
        raise ValidationError(
            f"dbt wrote a {expected} artifact at schema v{version}, which this "
            f"version of AppBI does not read (it reads "
            f"{', '.join(f'v{item}' for item in supported)}). Upgrade AppBI "
            "before upgrading dbt.",
            code="TRANSFORM_ARTIFACT_SCHEMA_UNSUPPORTED",
            details={
                "artifact": expected, "found": version, "supported": list(supported),
                "dbt_version": metadata.get("dbt_version"),
            },
        )
    return ArtifactVersion(
        artifact=expected,
        schema_version=version,
        dbt_version=_string(metadata.get("dbt_version")),
        adapter_type=_string(metadata.get("adapter_type")),
        generated_at=_string(metadata.get("generated_at")),
        invocation_id=_string(metadata.get("invocation_id")),
        raw_schema_version=_string(raw),
    )


def _string(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None
