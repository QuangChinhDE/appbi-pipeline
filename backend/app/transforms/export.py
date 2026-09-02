"""Export a project as a standard dbt project.

The acceptance criterion this serves is stated plainly in the blueprint: a valid
dbt project must survive `git clone` → open in AppBI → edit → export, without
losing an unsupported config or an unrecognised file.  Because a revision *is*
the file set, that criterion is met by copying bytes -- there is no
reconstruction step in which something could be dropped.

No credentials are included, ever.  A profile is built at run time and destroyed
with the process, so there is nothing here to leak into a ZIP somebody emails.
"""

from __future__ import annotations

import io
import zipfile

from sqlalchemy.ext.asyncio import AsyncSession

from app.transforms import files as file_service
from app.transforms.models import TransformProject, TransformProjectRevision
from app.transforms.storage import object_store

_PROFILE_TEMPLATE = """\
# Paste this into ~/.dbt/profiles.yml and fill in your own credentials.
#
# AppBI never exports credentials. It resolves them at run time into a profile
# that exists only for the life of the dbt process.

{profile_name}:
  target: dev
  outputs:
    dev:
      # Replace with your warehouse's own settings.
      type: {adapter}
      schema: your_dev_schema
      threads: 4
"""


async def export_zip(
    session: AsyncSession,
    project: TransformProject,
    *,
    revision: TransformProjectRevision | None = None,
) -> bytes:
    """Zip one revision, plus a profile template to make it runnable.

    Defaults to the working revision.  Passing a release's revision is how the
    "download exactly what production is running" case is served -- which is
    also the backup a destructive migration should take first.
    """
    revision = revision or await file_service.working_revision(session, project)
    contents = await file_service.read_all(revision, store=object_store())
    facts = await file_service.project_facts(revision)

    root = facts.name or project.dbt_project_name or _slug(project.name)
    adapter = (project.dbt_adapter_name or "").replace("dbt-", "") or "postgres"

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(contents):
            archive.writestr(f"{root}/{path}", contents[path])
        archive.writestr(
            f"{root}/profiles.yml.example",
            _PROFILE_TEMPLATE.format(
                profile_name=facts.profile or "appbi_runtime", adapter=adapter,
            ),
        )
    return buffer.getvalue()


def _slug(value: str) -> str:
    import re

    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return slug or "dbt_project"


def safe_filename(value: str) -> str:
    return "".join(
        char if char.isalnum() or char in ("-", "_") else "_" for char in value
    )[:100] or "project"
