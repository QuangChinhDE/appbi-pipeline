---
name: review-change
description: Independently review a diff in AppBI Pipeline before it ships. Use to review uncommitted work, a branch or a PR against this repository's architecture boundaries, engine and migration contracts, security invariants and test expectations.
---

# Review a change

Review the **diff**, not the story about the diff. If you also wrote the code,
deliberately re-derive each judgement from what the diff says rather than from
what you intended.

Start with:

    git diff            # or: git diff master...HEAD

Read every hunk. A file you skim is a file you did not review.

## Checklist

**Intent**
- Does the diff do what the task asked, and only that?
- Are there unrelated hunks — a rename, a reformat, a "while I was here"?

**Layer**
- Business logic in a router instead of a service?
- A new repository-style abstraction, where this product has none?
- Transform code outside `app/transforms/`?
- Engine specifics outside `app/adapters/`?
- A data or business problem patched in a React component?

**API and data**
- A response shape, field name, nullability or status code changed without the
  task calling for it?
- A model changed without a migration, or a migration without the model?
- More than one Alembic head? (`python scripts/check-migration-heads.py`)
- A destructive migration, or a non-nullable column with no backfill?

**Engine and connectors**
- Any edit to `engine-lock.json`, `connector-lock.json` or `compatibility.yaml`?
  Those require explicit engine intent — treat an incidental one as blocking.
- An adapter changed on one side of the contract only (`airbyte_protocol` vs
  `airbyte_api`)?
- Engine behaviour reimplemented in AppBI code?
- A compose file changed — which deployment modes does that reach?

**Security**
- A credential, token, key or connection string in source, a log line, an error
  response, a fixture or a committed file?
- A permission check bypassed, inlined or removed?
- A query missing its workspace filter?
- A secret written into `configuration_json` instead of through the secret store?

**Frontend**
- A query key that is not workspace-scoped?
- Missing loading / error / empty state?
- A hardcoded user-facing string instead of a `t()` key?
- A duplicate UI primitive?

**Tests and verification**
- Does a behaviour change come with a test?
- Was a test deleted, skipped or weakened?
- Was a validation loosened to make something pass?

## Reporting

Group findings by severity:

- **Blocking** — correctness, security, data loss, contract or boundary violation.
- **Should fix** — a real problem that is not blocking.
- **Consider** — style, naming, a simpler approach.

Quote the file and line for each. Say what breaks, concretely — not "this could
be risky". If the diff is clean, say it is clean; do not manufacture findings.
