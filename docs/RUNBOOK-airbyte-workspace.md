# Runbook — `AIRBYTE_WORKSPACE_ID`

## What it decides

Every source, destination and connection the product creates in Airbyte belongs
to one workspace. `AIRBYTE_WORKSPACE_ID` names it.

A wrong value is worse than a missing one. Missing fails at boot with a clear
message. Wrong — a valid UUID copied from a different Airbyte, or from staging
into production — passes every check the product makes and then creates
customer connections in a tenant that is not theirs. Nothing detects that
except someone noticing.

So it is configured explicitly, per deployment, and verified before release.

## Finding the value

```bash
python scripts/airbyte-workspace.py list
```

Airbyte's Config API is not published to the host in this deployment (it is on
the internal `appbi` network on purpose), so run the script on that network:

```bash
docker run --rm --network appbi-pipeline_appbi \
  -v "$PWD/scripts:/scripts" python:3.11-slim \
  python /scripts/airbyte-workspace.py --url http://airbyte-server:8001 list
```

On Git Bash for Windows, prefix that with `MSYS_NO_PATHCONV=1`. Without it the
shell rewrites `/scripts/...` into a Windows path before Docker sees it, and the
container reports a missing file that exists.

Where the API *is* reachable — a staging Airbyte behind an internal hostname —
run it directly:

```bash
python scripts/airbyte-workspace.py --url https://airbyte.internal.example list
```

Basic auth comes from `AIRBYTE_API_USERNAME` / `AIRBYTE_API_PASSWORD` when set.

## First deployment

A bootloader-initialised Airbyte creates one workspace. On a fresh install:

```bash
python scripts/airbyte-workspace.py list
# -> 4f4b2d36-...  Default Workspace
```

For a deployment that should own a named workspace of its own:

```bash
python scripts/airbyte-workspace.py create --name "AppBI Production"
```

`create` is idempotent on the name: run it twice and it prints the existing id
rather than making a second workspace.

## Where the value goes

| Environment | How |
|---|---|
| Local Compose | `.env`, or leave unset — the staging overlay sets `AIRBYTE_WORKSPACE_AUTO=true` |
| Staging | Deployment env var, alongside `AIRBYTE_API_URL` |
| Production | Secret store (the same one holding `SECRET_ENCRYPTION_KEY`), injected as an env var |

It is an identifier, not a credential, so it does not need encryption at rest —
but it belongs with the deployment configuration rather than in a repo, because
it differs per environment and a copied `.env` is how the wrong one spreads.

## `AIRBYTE_WORKSPACE_AUTO`

Off by default. When on **and** the id is empty, the adapter resolves the
workspace — singular — that the Airbyte has, and refuses if there is more than
one. That refusal is the point: guessing is only safe when there is nothing to
guess between.

It is ignored in production. `check_configuration()` treats an empty id in
production as a hard failure regardless, because which tenant receives customer
data is a decision, not a default.

## Verifying before release

```bash
python scripts/airbyte-workspace.py verify --id "$AIRBYTE_WORKSPACE_ID"
```

This is the check that catches a staging UUID in a production env file. It
confirms the id exists on *this* Airbyte and that source definitions resolve for
it, which is what the adapter needs. Include the output in the release
certification artifact (`scripts/release-gate.py`).

## Rotating or moving workspace

Moving a running deployment to a different workspace **orphans everything it
has already created**. The sources, destinations and connections stay in the
old workspace; the product's rows still hold their engine references; syncs
keep working against resources the new workspace does not list.

There is no supported in-place move. If it has to happen:

1. Pause every pipeline (`POST /api/v1/pipelines/{id}/pause`) and let in-flight
   runs finish. Check `GET /api/v1/runs?status=RUNNING` is empty.
2. Record the current engine references. They live in `engine_mappings`, not on
   the actors themselves — the product keeps engine identifiers in one table so
   nothing else has to know they exist:
   ```bash
   docker exec appbi-pipeline-postgres psql -U appbi -d appbi_integration -c \
     "select product_resource_type, product_resource_id,
             engine_resource_type, engine_resource_ref, engine_version
        from engine_mappings order by product_resource_type;"
   ```
3. Recreate the actors in the new workspace through the product API, so the
   product records the new mappings itself. Do not edit `engine_mappings` by
   hand — a row pointing at a resource the configured workspace does not contain
   is undetectable until a sync fails.
4. Delete the old workspace's resources in Airbyte once the new ones are green.
5. Update `AIRBYTE_WORKSPACE_ID`, restart, and run
   `python scripts/airbyte-workspace.py verify --id <new>`.

## Symptoms of a wrong id

| Symptom | Cause |
|---|---|
| Boot fails: "AIRBYTE_WORKSPACE_ID is empty" | Not set, and auto is off or this is production |
| `Airbyte deployment này chưa có connector '<key>'` on every connector | The id belongs to a different Airbyte; definitions list empty for it |
| Sources created but invisible in Airbyte's UI | Right Airbyte, wrong workspace — check which workspace the UI is showing |
| Boot log: `adapter.workspace_auto_resolved` in a non-local deployment | Auto is on where it should not be; set the id explicitly |
