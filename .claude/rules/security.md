---
paths:
  - "backend/app/core/secrets.py"
  - "backend/app/core/security.py"
  - "backend/app/core/permissions.py"
  - "backend/app/core/config.py"
  - "backend/app/core/google_identity.py"
  - "backend/app/api/v1/auth.py"
  - "backend/app/api/v1/oauth.py"
  - "backend/app/api/deps.py"
  - "backend/app/bootstrap.py"
  - "backend/app/services/access.py"
  - ".env.example"
  - ".env.production.example"
  - "deploy/**"
  - "run.sh"
  - "run.ps1"
---

# Security rules

Changing an authentication or credential boundary requires an explicit security
review section in the plan, before any code.

## Credential storage

Product entities never hold plaintext — they hold a `secret_ref`
(`app/core/secrets.py`). The store uses envelope encryption: a fresh AES data
key per secret, wrapped with the KEK from `SECRET_ENCRYPTION_KEY`.

- Never write a credential into `configuration_json` or any model column. Use
  the split between config and secret, and make sure it recurses — nested specs
  such as `loading_method.credential.hmac_key_secret` are exactly how plaintext
  leaked before (`scripts/scan-plaintext-secrets.py` exists to clean that up).
- `SecretStore` is a Protocol. Add a backend by implementing it, not by
  reaching around it from a service.
- **Never regenerate or overwrite `SECRET_ENCRYPTION_KEY` or `JWT_SECRET`.**
  Losing the KEK makes every stored credential unrecoverable. Rotation has a
  dedicated path: `scripts/rotate-kek.py`.

## Never exposed

Passwords, JWT/encryption secrets, connector credentials, OAuth client secrets,
service-account keys and `OPENAI_API_KEY` must not reach: UI, log lines,
error responses, test fixtures, committed evidence, screenshots, or source.

- Log through `app/core/logging.py` with structured fields. Never log a config
  or secret dict.
- Error responses carry a category and a remediation action, never an upstream
  message that may embed a credential or a connection string.

## Authorization

- Permission checks come from `app/core/permissions.py` via `app/api/deps.py`.
  Never inline a role comparison in a router or a component.
- Every tenant-scoped query filters by workspace. The frontend hiding a control
  is not authorization.
- Admin/bootstrap credentials (`app/bootstrap.py`) must keep the forced
  password-change behaviour; there is a regression test for it.

## Repository hygiene

`.env*` (except `.env.example` and `.env.production.example`), `secrets/`,
`credentials/`, `*service-account*.json`, `*client_secret*.json`, `gcp-*.json`
and `evidence-*.json` are gitignored deliberately. Do not negate those rules,
do not add a real value to a `.example` file, and never commit a
machine-specific absolute path.
