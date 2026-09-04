"""One-shot startup task: create schema, seed catalog, seed demo tenant.

Runs as its own compose service so api and worker both start against a database
that is already migrated and seeded.
"""

from __future__ import annotations

import asyncio
import logging
import pathlib
import uuid

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.config import settings
from app.core.db import Base, SessionLocal, engine
from app.core.logging import configure_logging, log_event
from app.core.permissions import OrgRole, Role
from email_validator import EmailNotValidError, validate_email

from app.core.security import hash_password, password_problems
from app.models import (  # noqa: F401 - import registers every table
    AlertRule, AuditEvent, BuilderProject, ConnectorDefinition, Destination, EngineInstance,
    EngineMapping,
    Membership, Notification, Operation, Organization, OrganizationMembership, Pipeline,
    PipelineRun, PipelineStream, RunAttempt, SchemaSnapshot, SecretRecord, Source, User,
    Workspace, register_transform_tables,
)

# Transform's tables register through a call rather than an import, because
# `app.transforms.models` imports `app.models.enums` and a top-level import
# either way round is a cycle.
register_transform_tables()
from app.models.enums import EngineStatus, EngineType
from app.services import actors as actor_service
from app.services import alerts, catalog

logger = logging.getLogger(__name__)


# `metadata.create_all` only creates *missing tables*; it will not alter a table
# that already exists. Anything that changes an existing table therefore needs an
# explicit, idempotent statement here.
#
# These run on exactly ONE path: adopting a database that has tables but no
# migration history. They are the changes that were applied by hand in the
# create_all era, and without them such a database can never match head.
#
# They must never run on a versioned database. Alembic owns that schema, and
# DDL applied after `upgrade head` makes the live schema differ from what the
# migration history describes -- which is not theoretical: a stray DROP INDEX
# in this list ran on every boot and left `alembic check` failing on a database
# reporting itself at head.
SCHEMA_FIXUPS = [
    # Connector Builder: a catalogue entry can now carry its own behaviour.
    'ALTER TABLE "connector_definitions" ADD COLUMN IF NOT EXISTS '
    '"declarative_manifest" JSONB',
    'ALTER TABLE "connector_definitions" ADD COLUMN IF NOT EXISTS '
    '"owner_workspace_id" UUID',
    'ALTER TABLE "connector_definitions" ADD COLUMN IF NOT EXISTS '
    '"display_version" VARCHAR(64)',
    # The owner and display-name indexes are declared on the model, so
    # create_all and Alembic both know about them. Creating them here under a
    # different name produced two indexes on one column and permanent
    # autogenerate drift; drop the stray ones from any database that has them.
    'DROP INDEX IF EXISTS "ix_connector_definitions_owner"',

    # Catalogue metadata added when the registry grew from 4 hand-written entries
    # to the full upstream set; create_all cannot add columns to a live table.
    'ALTER TABLE "connector_definitions" ADD COLUMN IF NOT EXISTS "icon_url" VARCHAR(500)',
    'ALTER TABLE "connector_definitions" ADD COLUMN IF NOT EXISTS "documentation_url" VARCHAR(500)',
    'ALTER TABLE "connector_definitions" ADD COLUMN IF NOT EXISTS "support_level" '
    "VARCHAR(32) NOT NULL DEFAULT 'community'",
    # NOTE: there used to be a `DROP INDEX ... ix_connector_definitions_display_name`
    # here, under a comment saying the catalogue is browsed by name -- the
    # statement did the opposite of what the comment claimed. The model declares
    # `display_name index=True` and the baseline migration creates it, so every
    # boot dropped an index Alembic had just created: `alembic current` said
    # head while `alembic check` reported a missing index.

    # Name uniqueness applies only to *live* rows: a soft-deleted resource must
    # not reserve its name forever. The original constraint ignored deleted_at.
    'ALTER TABLE "sources" DROP CONSTRAINT IF EXISTS "uq_source_ws_name"',
    'ALTER TABLE "destinations" DROP CONSTRAINT IF EXISTS "uq_destination_ws_name"',
    'ALTER TABLE "pipelines" DROP CONSTRAINT IF EXISTS "uq_pipeline_ws_name"',
    'CREATE UNIQUE INDEX IF NOT EXISTS "uq_source_ws_name_live" '
    'ON "sources" (workspace_id, name) WHERE deleted_at IS NULL',
    'CREATE UNIQUE INDEX IF NOT EXISTS "uq_destination_ws_name_live" '
    'ON "destinations" (workspace_id, name) WHERE deleted_at IS NULL',
    'CREATE UNIQUE INDEX IF NOT EXISTS "uq_pipeline_ws_name_live" '
    'ON "pipelines" (workspace_id, name) WHERE deleted_at IS NULL',
]


async def _alembic_config():
    """Alembic wired to the same models and URL the application uses."""
    from alembic.config import Config

    root = pathlib.Path(__file__).resolve().parent.parent
    ini = root / "alembic.ini"
    if not ini.exists():
        return None
    config = Config(str(ini))
    config.set_main_option("script_location", str(root / "migrations"))
    return config


def _current_revision(connection) -> str | None:
    from alembic.runtime.migration import MigrationContext

    return MigrationContext.configure(connection).get_current_revision()


def _schema_matches_models(connection) -> list:
    """The difference between this database and what the models describe.

    Empty means the schema already equals the current head, which is the only
    condition under which adopting an unversioned database is safe.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    context = MigrationContext.configure(
        connection, opts={"compare_type": True, "compare_server_default": True})
    return compare_metadata(context, Base.metadata)


def _has_tables(connection) -> bool:
    from sqlalchemy import inspect

    return bool(inspect(connection).get_table_names())


def _is_lock_timeout(exc: DBAPIError) -> bool:
    """Whether Postgres gave up waiting for a lock, rather than rejecting the DDL.

    Matched on SQLSTATE 55P03 (lock_not_available) and not on the message text,
    which is localised by the server's lc_messages: a Vietnamese or German
    Postgres would otherwise turn a deferrable timeout into a failed boot.
    """
    code = getattr(getattr(exc, "orig", None), "sqlstate", None) or getattr(exc, "code", None)
    if code == "55P03":
        return True
    # psycopg exposes it as pgcode; asyncpg as sqlstate on the wrapped error.
    for attribute in ("pgcode", "sqlstate"):
        if getattr(getattr(exc, "orig", None), attribute, None) == "55P03":
            return True
    return False


async def apply_schema_fixups() -> None:
    """Idempotent DDL for databases created before a change was expressed as a
    migration.

    Each fixup runs in its own transaction. A fixup takes ACCESS EXCLUSIVE, so
    if another session is mid-transaction on the same table the DDL waits — and
    every later query queues behind the DDL, turning one slow reader into a
    table-wide stall. Failing fast and retrying on the next boot is strictly
    better than freezing the product, and isolating each statement means one
    blocked fixup does not discard the ones that already succeeded.
    """
    applied: int = 0
    deferred: list[str] = []
    for statement in SCHEMA_FIXUPS:
        try:
            async with engine.begin() as connection:
                await connection.execute(text("SET LOCAL lock_timeout = '5s'"))
                await connection.execute(text(statement))
            applied += 1
        except DBAPIError as exc:
            if not _is_lock_timeout(exc):
                raise
            deferred.append(statement.split('"')[1] if '"' in statement else statement[:40])

    if deferred:
        log_event(logger, logging.WARNING, "bootstrap.schema_fixups_deferred",
                  deferred=deferred)
    log_event(logger, logging.INFO, "bootstrap.schema_fixups",
              applied=applied, deferred=len(deferred))


async def migrate_schema() -> None:
    """Bring the database to the current migration head.

    Alembic owns the schema. Three states are possible and each is handled
    explicitly rather than by hoping:

    * **Versioned** — the normal case. Run the migrations that are missing.
    * **Empty** — a fresh deployment. `upgrade head` builds everything.
    * **Populated but unversioned** — a database created before migrations
      existed. Stamping it blindly would mark migrations as applied that never
      ran, so instead the schema is compared against the models and adopted
      only when it already matches. If it does not, the operator is told rather
      than left with a database Alembic believes is current.
    """
    from alembic import command

    config = await _alembic_config()
    if config is None:
        # A layout without the migration tree (tooling, some test runs). Build
        # the schema directly so the process can still work, and say so.
        log_event(logger, logging.WARNING, "bootstrap.migrations_absent")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await apply_schema_fixups()
        return

    async with engine.begin() as connection:
        revision = await connection.run_sync(_current_revision)
        populated = await connection.run_sync(_has_tables)

    if revision is None and populated:
        # Legacy adoption. Fixups first: they are the changes that were applied
        # by hand in the create_all era, and without them a legacy database can
        # never match head.
        await apply_schema_fixups()
        async with engine.begin() as connection:
            differences = await connection.run_sync(_schema_matches_models)
        if differences:
            summary = [str(d)[:120] for d in differences[:5]]
            log_event(logger, logging.ERROR, "bootstrap.schema_adoption_refused",
                      differences=summary, total=len(differences))
            raise SystemExit(
                "This database has tables but no migration history, and its schema "
                "does not match the current models. Refusing to stamp it as "
                "up to date — that would mark migrations as applied that never ran. "
                f"First difference: {summary[0] if summary else 'unknown'}"
            )
        await asyncio.to_thread(command.stamp, config, "head")
        log_event(logger, logging.INFO, "bootstrap.schema_adopted")
        return

    # No fixups here. Once a database is versioned, Alembic owns its schema
    # outright -- running hand-written DDL after `upgrade head` means the
    # schema is whatever the migrations produced *and then* whatever startup
    # did to it, which is exactly how `alembic check` came to fail on a
    # database sitting at head. The fixups exist only to carry a pre-migration
    # database up to the point where it can be adopted, and that path is above.
    await asyncio.to_thread(command.upgrade, config, "head")

    async with engine.begin() as connection:
        head = await connection.run_sync(_current_revision)
    log_event(logger, logging.INFO, "bootstrap.schema_ready", revision=head)


class BootstrapRefused(RuntimeError):
    """The database cannot be seeded safely, so nothing is seeded."""


async def seed() -> None:
    """Bring a database to a usable state without inventing a credential.

    Two paths, chosen by `SEED_DEMO_DATA`, and the split is the whole point.
    The setting existed before this and nothing read it: production manifests
    set it to false and still got `admin@appbi.local` with a password published
    in this repository, plus three more demo accounts sharing one.
    """
    async with SessionLocal() as session:
        engine_instance = await session.scalar(
            select(EngineInstance).where(EngineInstance.is_default.is_(True))
        )
        if engine_instance is None:
            engine_instance = EngineInstance(
                name="Primary integration engine",
                engine_type=EngineType(settings.engine_type.upper()),
                base_url_ref=settings.airbyte_api_url or "local-docker",
                adapter_contract_version=settings.adapter_contract_version,
                status=EngineStatus.UNKNOWN,
                is_default=True,
            )
            session.add(engine_instance)
            await session.flush()

        # The connector catalogue carries no credentials, so both modes get it.
        outcome = await catalog.seed_catalog(session)
        log_event(logger, logging.INFO, "bootstrap.catalog_seeded",
                  created=outcome.created,
                  manifests_changed=sorted(outcome.manifests_changed))
        # A declarative connector's logic lives inside each source's config in
        # the engine, not in a shared definition, so re-seeding the catalogue
        # does not reach a source that already exists. Push it.
        republished = await actor_service.republish_manifests(
            session, outcome.manifests_changed
        )
        if republished:
            log_event(logger, logging.INFO, "bootstrap.manifests_republished",
                      resources=republished)

        if settings.seed_demo_data:
            if settings.is_production:
                raise BootstrapRefused(
                    "SEED_DEMO_DATA is true with APP_ENV=production. That "
                    "combination creates admin@appbi.local and three more "
                    "accounts with a password published in this repository. "
                    "Set SEED_DEMO_DATA=false and supply BOOTSTRAP_ADMIN_EMAIL "
                    "and BOOTSTRAP_ADMIN_PASSWORD."
                )
            await _seed_demo(session, engine_instance)
        else:
            await _bootstrap_admin(session, engine_instance)

        await session.commit()


async def _ensure_organization(session) -> Organization:
    """The organisation every workspace hangs off.

    `workspaces.organization_id` is NOT NULL, so a seed that creates a workspace
    without one does not fail later in some subtle way -- it fails at insert, on
    a fresh install, which is the first thing anybody runs.
    """
    organization = await session.scalar(
        select(Organization).where(Organization.slug == "default")
    )
    if organization is None:
        organization = Organization(name="Tổ chức mặc định", slug="default")
        session.add(organization)
        await session.flush()
    return organization


async def _ensure_org_membership(session, organization, user, role: OrgRole) -> None:
    existing = await session.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization.id,
            OrganizationMembership.user_id == user.id,
        )
    )
    if existing is None:
        session.add(OrganizationMembership(
            organization_id=organization.id, user_id=user.id, role=role
        ))


async def _seed_demo(session, engine_instance) -> None:
    """What a first look at the product needs, and what production may not have."""
    admin = await session.scalar(
        select(User).where(User.email == settings.seed_admin_email.lower())
    )
    if admin is None:
        admin = User(
            email=settings.seed_admin_email.lower(),
            full_name="Platform Admin",
            password_hash=hash_password(settings.seed_admin_password),
            is_platform_admin=True,
        )
        session.add(admin)
        await session.flush()

    organization = await _ensure_organization(session)
    # The seeded admin runs the organisation, which is what makes the second
    # workspace below administrable without a membership row per workspace.
    await _ensure_org_membership(session, organization, admin, OrgRole.ORG_OWNER)

    workspace = await session.scalar(select(Workspace).where(Workspace.slug == "default"))
    if workspace is None:
        workspace = Workspace(
            name="AppBI Data Team", slug="default", timezone="Asia/Bangkok",
            organization_id=organization.id,
            engine_instance_id=engine_instance.id,
        )
        session.add(workspace)
        await session.flush()

    membership = await session.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace.id, Membership.user_id == admin.id
        )
    )
    if membership is None:
        session.add(Membership(workspace_id=workspace.id, user_id=admin.id, role=Role.OWNER))

    # A second tenant plus non-owner accounts make tenant isolation and RBAC
    # observable in the running system, not just in tests.
    #
    # One account per assignable role, so every row of the matrix can be signed
    # in as. Three of the six were seeded before, which left CONNECTOR_DEV and
    # AUDITOR -- the two whose whole purpose is to hold *less* than the others
    # -- impossible to look at without creating an account by hand first.
    for email, name, role, password in (
        ("dataadmin@appbi.local", "Data Admin", Role.DATA_ADMIN, "Admin@123456"),
        ("connectordev@appbi.local", "Connector Dev", Role.CONNECTOR_DEV, "Admin@123456"),
        ("operator@appbi.local", "Operator", Role.OPERATOR, "Admin@123456"),
        ("analyst@appbi.local", "Analyst", Role.ANALYST, "Admin@123456"),
        ("auditor@appbi.local", "Auditor", Role.AUDITOR, "Admin@123456"),
    ):
        user = await session.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email, full_name=name, password_hash=hash_password(password))
            session.add(user)
            await session.flush()
        exists = await session.scalar(
            select(Membership).where(
                Membership.workspace_id == workspace.id, Membership.user_id == user.id
            )
        )
        if exists is None:
            session.add(Membership(workspace_id=workspace.id, user_id=user.id, role=role))
        # Plain organisation members: they reach the workspaces they were added
        # to and no others, which is what makes the two layers observable.
        await _ensure_org_membership(session, organization, user, OrgRole.ORG_MEMBER)

    other = await session.scalar(select(Workspace).where(Workspace.slug == "marketing"))
    if other is None:
        other = Workspace(name="Marketing Analytics", slug="marketing",
                          timezone="Asia/Bangkok", organization_id=organization.id,
                          engine_instance_id=engine_instance.id)
        session.add(other)
        await session.flush()
        session.add(Membership(workspace_id=other.id, user_id=admin.id, role=Role.OWNER))

    await session.flush()
    await alerts.ensure_default_rules(session, workspace.id)
    await alerts.ensure_default_rules(session, other.id)
    log_event(logger, logging.INFO, "bootstrap.seed_complete",
              mode="demo", workspace=str(workspace.id), admin=admin.email)


async def _bootstrap_admin(session, engine_instance) -> None:
    """One platform admin, from a one-time secret, that must change its password.

    Idempotent by absence: if any user already exists this does nothing. A
    deployment that has been used is not re-bootstrapped, so re-running the
    migration Job cannot resurrect a removed account or reset a password.
    """
    existing = await session.scalar(select(User).limit(1))
    if existing is not None:
        log_event(logger, logging.INFO, "bootstrap.seed_skipped",
                  reason="the database already has users")
        return

    email = (settings.bootstrap_admin_email or "").strip().lower()
    password = settings.bootstrap_admin_password
    if not email or not password:
        raise BootstrapRefused(
            "This database has no users and SEED_DEMO_DATA is false, so there "
            "is no account to sign in with. Supply BOOTSTRAP_ADMIN_EMAIL and "
            "BOOTSTRAP_ADMIN_PASSWORD as a one-time secret; the account they "
            "create must change its password before it can do anything else. "
            "Refusing rather than falling back to a default, because a "
            "privileged account with a guessable password is worse than a "
            "deployment that will not start."
        )
    if password == settings.seed_admin_password:
        raise BootstrapRefused(
            "BOOTSTRAP_ADMIN_PASSWORD is the demo password from this "
            "repository. Generate one with "
            "python -c 'import secrets;print(secrets.token_urlsafe(24))'"
        )
    # The same policy a user would face. Refusing only the one known demo
    # string left every other weak secret acceptable, and this account is a
    # platform admin on an empty production database.
    weak = password_problems(password)
    if weak:
        raise BootstrapRefused(
            "BOOTSTRAP_ADMIN_PASSWORD does not meet the password policy: "
            + " ".join(weak)
            + " Generate one with "
            "python -c 'import secrets;print(secrets.token_urlsafe(24))'"
        )
    try:
        validate_email(email, check_deliverability=False)
    except EmailNotValidError as exc:
        raise BootstrapRefused(
            f"BOOTSTRAP_ADMIN_EMAIL {email!r} is not a valid address: {exc}. "
            "It becomes the login for the only account on this deployment."
        ) from exc

    admin = User(
        email=email,
        full_name="Platform Admin",
        password_hash=hash_password(password),
        is_platform_admin=True,
        password_change_required=True,
    )
    session.add(admin)
    await session.flush()

    organization = await _ensure_organization(session)
    await _ensure_org_membership(session, organization, admin, OrgRole.ORG_OWNER)

    workspace = await session.scalar(select(Workspace).where(Workspace.slug == "default"))
    if workspace is None:
        workspace = Workspace(
            name="Default", slug="default", timezone="Asia/Bangkok",
            organization_id=organization.id,
            engine_instance_id=engine_instance.id,
        )
        session.add(workspace)
        await session.flush()
    session.add(Membership(workspace_id=workspace.id, user_id=admin.id, role=Role.OWNER))

    await session.flush()
    await alerts.ensure_default_rules(session, workspace.id)
    log_event(logger, logging.INFO, "bootstrap.admin_created",
              mode="bootstrap", email=email,
              detail="password change required before this account can be used")


async def main() -> None:
    configure_logging()
    for attempt in range(30):
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            break
        except Exception as exc:  # noqa: BLE001 - waiting for postgres to accept
            log_event(logger, logging.INFO, "bootstrap.waiting_for_db",
                      attempt=attempt + 1, error=str(exc)[:120])
            await asyncio.sleep(2)
    else:
        raise SystemExit("database never became reachable")

    await migrate_schema()
    await seed()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
