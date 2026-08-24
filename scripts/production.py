#!/usr/bin/env python3
"""One command to stand this product up, and one place that refuses to.

    python scripts/production.py install  --config deploy/production.yaml
    python scripts/production.py upgrade  --config deploy/production.yaml
    python scripts/production.py status   --config deploy/production.yaml
    python scripts/production.py doctor   --config deploy/production.yaml
    python scripts/production.py logs api --config deploy/production.yaml
    python scripts/production.py rollback --artifact certification.json

This is an orchestrator, not a big `docker compose up`. It reads a config,
refuses to continue on anything a production deployment must not have, applies
the product, points it at an already-pinned Airbyte, migrates, waits for deep
readiness, reconciles, and records a release artifact. Every step is
idempotent: running `install` twice is a no-op plus a fresh artifact.

Two profiles, and they are not interchangeable:

    external-airbyte-k8s   production. Kubernetes, managed datastores, an
                           Airbyte that someone else operates and this only
                           talks to.
    single-host-demo       one machine, Docker Compose, everything local.
                           Refuses to run with APP_ENV=production, because the
                           easiest way to end up with a demo in production is
                           to make it convenient.

The fail-closed rules are the point of the file. A deployment that is missing
a workspace id, or still carrying `registry.internal/`, or pointing at an
Airbyte whose version nobody certified, stops here with a specific message --
rather than three steps later with a generic one.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _console import force_utf8  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

PROFILES = ("external-airbyte-k8s", "single-host-demo")

# Values a repository ships that an environment must replace. Kept in step with
# scripts/release-gate.py; both refuse the same strings, because a config that
# passes here and fails the gate wastes a deployment window.
PLACEHOLDERS = (
    "10.0.0.0/24", "registry.internal/", "appbi.example.internal",
    "postgres.internal", "REPLACE_ME", "changeme", "CHANGEME",
    "<", "TO BE ASSIGNED",
)

DEFAULT_CONFIG = ROOT / "deploy" / "production.yaml"


# ── output ───────────────────────────────────────────────────────────────────

def step(message: str) -> None:
    print(f"\n=== {message} ===", flush=True)


def ok(message: str) -> None:
    print(f"  ok    {message}", flush=True)


def warn(message: str) -> None:
    print(f"  warn  {message}", flush=True)


class Stop(SystemExit):
    """Refusal with a reason. Every message says what to change."""

    def __init__(self, *lines: str) -> None:
        print("\nSTOPPED", file=sys.stderr)
        for line in lines:
            print(f"  - {line}", file=sys.stderr)
        super().__init__(2)


# ── config ───────────────────────────────────────────────────────────────────

def load_config(path: Path) -> dict:
    if not path.exists():
        raise Stop(
            f"no config at {path}",
            "Copy deploy/production.yaml.example and fill it in. It is meant "
            "to be reviewed like code, because it is the whole deployment.")
    try:
        import yaml
    except ImportError:
        raise Stop("PyYAML is not installed: pip install pyyaml")
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(document, dict):
        raise Stop(f"{path} does not contain a mapping")
    return document


def _walk(node, trail: str = ""):
    """Every leaf in the config, with the path that reaches it."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, f"{trail}.{key}" if trail else str(key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, f"{trail}[{index}]")
    else:
        yield trail, node


def _require(config: dict, path: str) -> object:
    node = config
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise Stop(f"config is missing `{path}`")
        node = node[part]
    if node in (None, ""):
        raise Stop(f"config `{path}` is empty")
    return node


def validate(config: dict, *, strict: bool) -> list[str]:
    """Everything that must be true before anything is applied.

    Returns warnings; raises Stop on anything fatal. `strict` is on for the
    production profile and off for the demo, where a self-signed everything is
    the whole point.
    """
    warnings: list[str] = []

    profile = _require(config, "profile")
    if profile not in PROFILES:
        raise Stop(f"unknown profile {profile!r}; expected one of {', '.join(PROFILES)}")

    # 1. No placeholder survives into a real deployment, anywhere in the file.
    #    Checked over every leaf rather than a known list of keys: the next
    #    field someone adds should be covered without anyone remembering to.
    found = [f"`{where}` is {value!r}"
             for where, value in _walk(config)
             if isinstance(value, str)
             for token in PLACEHOLDERS if token in value]
    if found:
        raise Stop("the config still carries repository placeholders:", *found)

    # 2. Never a floating tag. An engine or a product that upgrades itself
    #    invalidates the certification that was recorded against it.
    tag = str(_require(config, "product.tag"))
    if tag in ("latest", "main", "master", "stable"):
        raise Stop(f"product.tag is {tag!r}; a floating tag cannot be certified. "
                   "Pin the exact version that was tested.")

    # 3. The engine has to be one that was actually certified.
    engine_version = str(_require(config, "engine.platform_version"))
    certified = certified_platform_versions()
    if engine_version not in certified:
        raise Stop(
            f"engine.platform_version {engine_version!r} is not certified",
            f"compatibility.yaml lists {', '.join(sorted(certified)) or '(none)'}",
            "Certify it first: docs/RUNBOOK-engine-upgrade.md")

    # 4. A workspace id that is a valid UUID from the *wrong* Airbyte passes
    #    every startup check and then creates customer connections in someone
    #    else's tenant. Shape is checked here; identity is checked against the
    #    live engine in `doctor`.
    workspace = str(_require(config, "engine.workspace_id"))
    try:
        uuid.UUID(workspace)
    except ValueError:
        raise Stop(f"engine.workspace_id {workspace!r} is not a UUID")

    # 5. Secrets are references, never values. A config file is reviewed,
    #    committed, and pasted into tickets.
    for where, value in _walk(config.get("secrets") or {}):
        text = str(value)
        if not text.startswith(("secret://", "env://", "file://")):
            raise Stop(
                f"secrets.{where} looks like a literal value",
                "Use a reference: secret://<k8s-secret>/<key>, env://NAME, or "
                "file:///path. This file must never hold the secret itself.")

    if profile == "external-airbyte-k8s":
        if not strict:
            warnings.append("production profile with strict checks disabled")
        _require(config, "product.namespace")
        _require(config, "product.overlay")
        _require(config, "engine.url")
        _require(config, "engine.namespace")
        _require(config, "engine.connector_policy_overlay")
        _require(config, "datastores.database_url_ref")

        # Fatal, not a warning. The Config API carries connector credentials in
        # request bodies and the product's own API carries session cookies; a
        # warning here is a warning nobody reads on the one deployment that
        # needed it.
        if str(config["engine"]["url"]).startswith("http://"):
            raise Stop(
                "engine.url is plain HTTP. The Config API carries connector "
                "credentials in request bodies. Use HTTPS, or terminate TLS in "
                "the mesh and say so explicitly with --allow-insecure.")
        if str((config.get("product") or {}).get("api_url") or "").startswith("http://"):
            raise Stop(
                "product.api_url is plain HTTP. Session cookies are issued "
                "with Secure, so a browser will not send them over it -- users "
                "would be unable to stay signed in.")
        if not (config.get("product") or {}).get("ingress_tls"):
            raise Stop("product.ingress_tls is not true; production ingress "
                       "must terminate TLS")
        auth = (config.get("engine") or {}).get("auth") or {}
        mode = str(auth.get("mode", "")).lower()
        if not auth:
            raise Stop(
                "engine.auth is unset. The certification profile ran with "
                "Airbyte auth disabled; a production engine must not, and an "
                "auth-enabled Airbyte refuses every call from a deployment "
                "with no credentials.")
        if mode not in ("client_credentials", "basic"):
            raise Stop(
                f"engine.auth.mode is {mode!r}; expected 'client_credentials' "
                "(Airbyte 1.x) or 'basic' (0.59.x)")
        if mode == "client_credentials":
            for field in ("client_id_ref", "client_secret_ref"):
                if not str(auth.get(field) or "").startswith(
                        ("secret://", "env://", "file://")):
                    raise Stop(f"engine.auth.{field} must be a secret reference")
        else:
            warnings.append(
                "engine.auth.mode is 'basic'. Airbyte 1.x with auth enabled "
                "answers Basic with 401 -- this only works against 0.59.x.")

        # Two databases, always. The ADR says so; this is where it is enforced
        # for a deployment rather than for a running process.
        product_db = str(_require(config, "datastores.database_url_ref"))
        engine_db = str((config.get("engine") or {}).get("database_url_ref") or "")
        if engine_db and engine_db == product_db:
            raise Stop(
                "datastores.database_url_ref and engine.database_url_ref are "
                "the same reference; the product and the engine must not share "
                "a database (docs/ADR-001-database-topology.md)")

    if profile == "single-host-demo":
        if str(config.get("app_env", "")).lower() == "production":
            raise Stop(
                "profile single-host-demo with app_env: production",
                "This profile runs an in-process database and no TLS. If this "
                "really is production, use external-airbyte-k8s.")
        warnings.append(
            "single-host-demo: local Postgres, local Redis, no TLS, no managed "
            "backups. Fine for a demo, not a production topology.")

    return warnings


def certified_platform_versions() -> set[str]:
    import yaml

    document = yaml.safe_load(
        (ROOT / "compatibility.yaml").read_text(encoding="utf-8"))
    return {
        str(block["platform_version"])
        for key, block in document.items()
        if key.startswith("airbyte_api_certification") and isinstance(block, dict)
        and block.get("platform_version")
    }


# ── shelling out ─────────────────────────────────────────────────────────────

def run(command: list[str], *, check: bool = True, capture: bool = False,
        env: dict | None = None, timeout: int = 1800) -> subprocess.CompletedProcess:
    printed = " ".join(command)
    print(f"  $ {printed}", flush=True)
    result = subprocess.run(
        command, cwd=ROOT, text=True, timeout=timeout,
        capture_output=capture,
        env={**os.environ, **(env or {})})
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[:800]
        raise Stop(f"`{printed}` failed with exit {result.returncode}",
                   *( [detail] if detail else []))
    return result


def need(binary: str, why: str) -> None:
    if shutil.which(binary) is None:
        raise Stop(f"{binary} is not on PATH -- {why}")


def http(url: str, *, timeout: int = 10,
         auth: tuple[str, str] | None = None,
         bearer: str = "") -> tuple[int, str]:
    request = urllib.request.Request(url)
    if bearer:
        request.add_header("Authorization", f"Bearer {bearer}")
    elif auth and auth[0]:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        request.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - connection refused, DNS, timeout
        return 0, str(exc)


def _engine_bearer(config: dict) -> str:
    """A bearer token from the engine, using the configured client credentials.

    The probe used to send nothing, so against an auth-enabled Airbyte it read
    401 and reported the engine as the wrong version or unreachable. Verifying
    an engine over a path the product does not use is not verification.
    """
    auth = (config.get("engine") or {}).get("auth") or {}
    if str(auth.get("mode", "")).lower() != "client_credentials":
        return ""
    client_id = resolve_secret(str(auth.get("client_id_ref") or ""))
    client_secret = resolve_secret(str(auth.get("client_secret_ref") or ""))
    if not client_id:
        warn("engine.auth uses client credentials held in Kubernetes secrets, "
             "which this process cannot read. The pre-deploy probe runs "
             "unauthenticated; post-deploy verification through the product's "
             "own Pod is what proves the authenticated path.")
        return ""
    url = str((config.get("engine") or {}).get("url") or "").rstrip("/")
    request = urllib.request.Request(
        f"{url}/api/v1/applications/token",
        data=json.dumps({"client_id": client_id,
                         "client_secret": client_secret}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return str(json.loads(response.read() or b"{}").get("access_token") or "")
    except Exception as exc:  # noqa: BLE001
        raise Stop(f"could not obtain an engine token from {url}: {exc}",
                   "The client_id/client_secret must belong to an Application "
                   "created in Airbyte; the instance admin's email and "
                   "password are not accepted at this endpoint.")


def _engine_auth(config: dict) -> tuple[str, str] | None:
    """Resolve the engine's Basic credentials from the references in the config."""
    auth = (config.get("engine") or {}).get("auth") or {}
    if str(auth.get("mode", "")).lower() not in ("basic", "base"):
        return None
    user = resolve_secret(str(auth.get("username_ref") or ""))
    password = resolve_secret(str(auth.get("password_ref") or ""))
    if not user:
        # `secret://` is resolved by Kubernetes, not here, so a production
        # config legitimately yields nothing. Say so rather than silently
        # probing unauthenticated and calling the result a verification.
        warn("engine.auth is declared but its credentials are Kubernetes "
             "secret references, which this process cannot read. The engine "
             "probe runs unauthenticated; post-deploy verification through the "
             "product's own Pod is what proves the authenticated path.")
        return None
    return (user, password)


def wait_for(url: str, *, label: str, attempts: int = 60, delay: int = 5) -> dict:
    """Poll until it answers 200, then return the parsed body.

    Reports the last thing it saw on failure. "readiness timed out" with no
    detail sends people to the wrong logs.
    """
    last = ""
    for attempt in range(1, attempts + 1):
        status, body = http(url)
        if status == 200:
            ok(f"{label} after {attempt} attempt(s)")
            try:
                return json.loads(body)
            except ValueError:
                return {}
        last = f"HTTP {status}: {body[:300]}" if status else body[:300]
        time.sleep(delay)
    raise Stop(f"{label} did not come up after {attempts * delay}s", f"last: {last}")


# ── profile: single-host demo ────────────────────────────────────────────────

def demo_env_file(config: dict) -> Path:
    """Write .env with real secrets, generating any that are missing.

    Generated rather than prompted for. The three-step "copy the example,
    generate a key, paste it in" quick start has two places to get it wrong and
    one of them fails at runtime with an error about encryption.
    """
    env_path = ROOT / ".env"
    example = ROOT / ".env.example"
    if not example.exists():
        raise Stop("no .env.example to build a demo environment from")

    existing: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                existing[key.strip()] = value.strip()

    lines: list[str] = []
    generated: list[str] = []
    for line in example.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            lines.append(line)
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.split("#")[0].strip()

        kept = existing.get(key, "")
        if kept and "REPLACE_ME" not in kept:
            lines.append(f"{key}={kept}")
            continue

        if key == "SECRET_ENCRYPTION_KEY":
            # 32 bytes, urlsafe-base64. The process refuses to start otherwise,
            # and generating it here is the difference between one command and
            # a support question.
            value = base64.urlsafe_b64encode(os.urandom(32)).decode()
            generated.append(key)
        elif key == "JWT_SECRET" and ("change" in value or not value):
            value = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
            generated.append(key)
        elif key == "APP_ENV":
            value = str(config.get("app_env", "local"))
        lines.append(f"{key}={value}")

    # The demo's seeded operator password, written so the `env://` reference in
    # deploy/demo.yaml actually resolves. Without it the install finishes but
    # reconcile comes back 401 -- worse than failing, because the run looks
    # complete and one check silently did not happen.
    #
    # A literal here rather than a reference: this is the demo profile, the
    # account is seeded with a published password, and the profile refuses to
    # be production. The production profile resolves secrets through Kubernetes
    # and never reaches this code.
    if config.get("profile") == "single-host-demo":
        seeded = str((config.get("operator") or {}).get("seed_password")
                     or "Admin@12345")
        lines.append("")
        lines.append("# Written by scripts/production.py for the demo profile.")
        lines.append(f"APPBI_DEMO_PASSWORD={seeded}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Load what was just written into this process too. `env://` references are
    # resolved here, and a value that exists only in a file the containers read
    # is not available to the installer's own API calls.
    for entry in lines:
        if "=" in entry and not entry.lstrip().startswith("#"):
            key, _, value = entry.partition("=")
            os.environ.setdefault(key.strip(), value.strip())
    if generated:
        ok(f"generated {', '.join(generated)} in .env")
    else:
        ok(".env already had its secrets; kept them")
    return env_path


def compose_files(config: dict) -> list[str]:
    files = (config.get("demo") or {}).get("compose_files") or [
        "docker-compose.yml", "docker-compose.embedded.yml"]
    for name in files:
        if not (ROOT / name).exists():
            raise Stop(f"compose file {name} does not exist")
    return files


def compose(config: dict, *args: str, check: bool = True,
            capture: bool = False) -> subprocess.CompletedProcess:
    command = ["docker", "compose"]
    for name in compose_files(config):
        command += ["-f", name]
    command += list(args)
    # COMPOSE_FILE in .env would be added to the -f list above, silently
    # doubling the service set. Explicit -f flags are the whole point here.
    return run(command, check=check, capture=capture,
               env={"COMPOSE_FILE": "", "COMPOSE_PATH_SEPARATOR": ""})


def api_base(config: dict) -> str:
    configured = (config.get("product") or {}).get("api_url")
    if configured:
        return str(configured).rstrip("/")
    port = (config.get("demo") or {}).get("api_port") or 8010
    return f"http://localhost:{port}"


def install_demo(config: dict, *, build: bool) -> None:
    step("prerequisites")
    need("docker", "the demo profile runs on Docker Compose")
    result = run(["docker", "compose", "version"], capture=True, check=False)
    if result.returncode != 0:
        raise Stop("`docker compose` is not available (Compose v2 is required)")
    ok((result.stdout or "").strip().splitlines()[0] if result.stdout else "docker compose present")

    step("environment")
    demo_env_file(config)

    step("build and start")
    compose(config, "up", "-d", *(["--build"] if build else []))

    step("readiness")
    base = api_base(config)
    wait_for(f"{base}/readyz", label="the API is serving")
    deep = wait_for(f"{base}/readyz?deep=1", label="the engine answered",
                    attempts=24, delay=5)
    ok(f"engine {deep.get('engine_type', '?')}")


# ── profile: external Airbyte on Kubernetes ──────────────────────────────────

def kubectl(config: dict, *args: str, check: bool = True,
            capture: bool = False) -> subprocess.CompletedProcess:
    command = ["kubectl"]
    context = (config.get("kubernetes") or {}).get("context")
    if context:
        command += ["--context", str(context)]
    return run(command + list(args), check=check, capture=capture)


def render_from_config(config: dict, workdir: Path) -> Path:
    """Turn the reviewed config into the manifests that will actually be applied.

    The Kustomize tree is *copied* into the temp root and the generated overlay
    sits beside it, referring to it relatively. The obvious shape -- a
    kustomization in a temp directory whose `resources` points at the repo by
    absolute path -- does not work at all:

        error: accumulating resources from '.../overlays/production':
        new root ... cannot be absolute

    Kustomize refuses roots outside its own tree, and the flag that would allow
    it turns off a load restriction that exists for good reason. Copying is the
    honest fix: the generated overlay is a sibling, and the reviewed config is
    still the only input.

    Generated per run into a temporary directory rather than written back into
    the repository: the reviewed artefact is the config, and a generated overlay
    living in git is a second source of truth waiting to drift.
    """
    import shutil

    product = config["product"]
    engine = config["engine"]

    # 1. The repository's Kustomize tree, copied so relative paths resolve.
    tree = workdir / "kubernetes"
    shutil.copytree(ROOT / "deploy" / "kubernetes", tree)

    overlay_relative = Path(str(product["overlay"])).relative_to("deploy/kubernetes")
    base = (tree / overlay_relative).resolve()
    if not base.is_dir():
        raise Stop(f"product.overlay {product['overlay']!r} is not a directory")

    # The copied overlay carries its own `images:` block with the repository's
    # example registry. Two image sources means the config loses -- the inner
    # transformer renames `appbi-pipeline-backend` first, so the outer one
    # matches nothing and the rendered image stays at `registry.internal/...`.
    # Stripping it from the copy leaves exactly one source of truth. Safe to
    # mutate: this is a copy, made for this render.
    _drop_static_images(base / "kustomization.yaml")

    generated = workdir / "generated"
    generated.mkdir()
    relative_to_base = os.path.relpath(base, generated).replace(os.sep, "/")

    registry = str(product["registry"]).rstrip("/")
    tag = str(product["tag"])
    backend = f"{registry}/{product.get('image', 'backend')}"
    frontend = f"{registry}/{product.get('frontend_image', 'frontend')}"

    # 2. Everything the config says, as env the Pod actually receives.
    #    Literals go in the ConfigMap patch; anything secret is bound as a
    #    `secretKeyRef` so the value never passes through this process or the
    #    rendered YAML.
    literals = {
        "AIRBYTE_API_URL": str(engine["url"]),
        "APP_ENV": "production",
        "COOKIE_SECURE": "true",
        "SEED_DEMO_DATA": "false",
    }
    config_patch = [{"op": "replace", "path": f"/data/{key}", "value": value}
                    for key, value in literals.items()]

    # `AIRBYTE_WORKSPACE_ID` was the sharp one: it lived only in a Secret, so
    # the installer verified a workspace the Pod never saw. It is not a
    # credential -- it is a deployment identity, and it belongs in the config
    # that gets reviewed.
    config_patch.append({"op": "add", "path": "/data/AIRBYTE_WORKSPACE_ID",
                         "value": str(engine["workspace_id"])})

    secret_env = _secret_env(config)
    env_patch = [{"op": "add", "path": "/spec/template/spec/containers/0/env",
                  "value": secret_env}] if secret_env else []

    kustomization = {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "namespace": str(product["namespace"]),
        "resources": [relative_to_base],
        "images": [
            {"name": "appbi-pipeline-backend", "newName": backend, "newTag": tag},
            {"name": "appbi-pipeline-frontend", "newName": frontend, "newTag": tag},
        ],
        "patches": [
            {"path": "config.yaml", "target": {"kind": "ConfigMap", "name": "appbi-config"}},
        ],
    }

    (generated / "config.yaml").write_text(yaml_dump(config_patch), encoding="utf-8")

    if secret_env:
        for name in ("appbi-api", "appbi-worker"):
            (generated / f"env-{name}.yaml").write_text(yaml_dump(env_patch), encoding="utf-8")
            kustomization["patches"].append(
                {"path": f"env-{name}.yaml",
                 "target": {"kind": "Deployment", "name": name}})

        # The migration Job runs bootstrap, so it needs the same runtime
        # credentials plus the one-time admin. It kept a hard-coded
        # `secretRef: appbi-secrets`, so a config naming a different Secret
        # left the Job reading the old one while preflight passed.
        migrate_env = list(secret_env) + _bootstrap_env(config)
        (generated / "env-migrate.yaml").write_text(
            yaml_dump([
                {"op": "replace",
                 "path": "/spec/template/spec/containers/0/env",
                 "value": migrate_env},
                # Drop the blanket secretRef; the ConfigMap entry stays.
                {"op": "remove",
                 "path": "/spec/template/spec/containers/0/envFrom/1"},
            ]),
            encoding="utf-8")
        kustomization["patches"].append(
            {"path": "env-migrate.yaml",
             "target": {"kind": "Job", "name": "appbi-migrate"}})

    host = product.get("ingress_host") or _host_of(product.get("api_url"))
    if host:
        # Both the rule and the TLS entry. Patching only the rule left the
        # certificate requested for the example hostname -- the deployment
        # comes up and every browser rejects it.
        (generated / "ingress.yaml").write_text(
            yaml_dump([
                {"op": "replace", "path": "/spec/rules/0/host", "value": host},
                {"op": "replace", "path": "/spec/tls/0/hosts", "value": [host]},
            ]),
            encoding="utf-8")
        kustomization["patches"].append(
            {"path": "ingress.yaml", "target": {"kind": "Ingress"}})

    (generated / "kustomization.yaml").write_text(yaml_dump(kustomization), encoding="utf-8")

    rendered = workdir / "rendered.yaml"
    result = run(["kubectl", "kustomize", str(generated)], capture=True)
    rendered.write_text(result.stdout, encoding="utf-8")
    return rendered


def _drop_static_images(kustomization: Path) -> None:
    """Remove an overlay's hard-coded `images:` so the config decides."""
    import yaml

    if not kustomization.exists():
        return
    document = yaml.safe_load(kustomization.read_text(encoding="utf-8")) or {}
    if document.pop("images", None) is not None:
        kustomization.write_text(yaml_dump(document), encoding="utf-8")


def _host_of(url: str | None) -> str:
    """The hostname in `product.api_url`.

    The example config carries `api_url` and not `ingress_host`, so a renderer
    that only reads `ingress_host` silently left the ingress at its example
    host -- while the installer checked readiness against the URL the operator
    actually filled in. Two fields, one fact.
    """
    if not url:
        return ""
    from urllib.parse import urlparse

    return urlparse(str(url)).hostname or ""


def _bootstrap_env(config: dict) -> list[dict]:
    """The one-time bootstrap admin, bound only onto the migration Job.

    Optional at the Pod level: the runbook says to delete the Secret after the
    first sign-in, and every later upgrade must still run. `optional: true` is
    what makes a deleted Secret a no-op instead of a failed Job.
    """
    secrets = config.get("secrets") or {}
    env: list[dict] = []
    for variable, field in (("BOOTSTRAP_ADMIN_EMAIL", "bootstrap_admin_email_ref"),
                            ("BOOTSTRAP_ADMIN_PASSWORD", "bootstrap_admin_password_ref")):
        reference = str(secrets.get(field) or "")
        if not reference.startswith("secret://"):
            continue
        name, _, key = reference[len("secret://"):].partition("/")
        env.append({"name": variable,
                    "valueFrom": {"secretKeyRef": {"name": name, "key": key,
                                                   "optional": True}}})
    return env


def _secret_env(config: dict) -> list[dict]:
    """Bind every `secret://` the config names to a `secretKeyRef`.

    The manifests carried `envFrom: appbi-secrets`, so whichever Secret happened
    to be called that supplied the runtime -- the config's references were
    decoration. Naming each key explicitly means the deployment fails on a
    missing key instead of starting with a silently absent one.
    """
    wanted = {
        "SECRET_ENCRYPTION_KEY": ("secrets", "encryption_key_ref"),
        "JWT_SECRET": ("secrets", "jwt_secret_ref"),
        "DATABASE_URL": ("datastores", "database_url_ref"),
        "DATABASE_URL_SYNC": ("datastores", "database_url_sync_ref"),
        # Airbyte 1.x with auth enabled rejects HTTP Basic, including the
        # instance admin's own login, so a production deployment binds client
        # credentials. Basic stays for 0.59.x.
        "AIRBYTE_CLIENT_ID": ("engine.auth", "client_id_ref"),
        "AIRBYTE_CLIENT_SECRET": ("engine.auth", "client_secret_ref"),
        "AIRBYTE_API_USERNAME": ("engine.auth", "username_ref"),
        "AIRBYTE_API_PASSWORD": ("engine.auth", "password_ref"),
    }
    env: list[dict] = []
    for variable, (section, field) in wanted.items():
        node = config
        for part in section.split("."):
            node = (node or {}).get(part) or {}
        reference = str(node.get(field) or "")
        if not reference.startswith("secret://"):
            continue
        name, _, key = reference[len("secret://"):].partition("/")
        env.append({"name": variable,
                    "valueFrom": {"secretKeyRef": {"name": name, "key": key}}})
    return env


def yaml_dump(document) -> str:
    import yaml

    return yaml.safe_dump(document, sort_keys=False)


def assert_rendered_matches(config: dict, rendered: Path) -> None:
    """The manifests about to be applied must say what the config says.

    A rendering step that is not checked is a rendering step that silently
    stops working the first time the base changes shape -- a renamed ConfigMap
    key, a moved ingress rule -- and the deployment quietly keeps the old
    value. Checking the output rather than trusting the patch is the same
    lesson the kube-dns selector taught.
    """
    import yaml

    product = config["product"]
    engine = config["engine"]
    documents = [d for d in yaml.safe_load_all(rendered.read_text(encoding="utf-8")) if d]

    problems: list[str] = []
    namespaces = {d.get("metadata", {}).get("namespace") for d in documents}
    namespaces.discard(None)
    if namespaces != {str(product["namespace"])}:
        problems.append(f"namespaces {sorted(namespaces)} != {product['namespace']!r}")

    registry = str(product["registry"]).rstrip("/")
    tag = str(product["tag"])
    images = {
        container["image"]
        for document in documents
        for spec in _pod_specs(document)
        for container in spec.get("containers", []) + spec.get("initContainers", [])
    }
    # Both images, not just the backend. The frontend was never asserted, so a
    # deployment could ship a backend from the release and a UI from wherever
    # the overlay happened to point.
    for role, default in (("image", "backend"), ("frontend_image", "frontend")):
        expected = f"{registry}/{product.get(role, default)}:{tag}"
        if expected not in images:
            problems.append(f"{expected!r} is not among the rendered images "
                            f"{sorted(images)}")
    foreign = sorted(i for i in images if not i.startswith(registry + "/"))
    if foreign:
        problems.append(f"images from a registry the config does not name: {foreign}")

    # Every credential the config declares must arrive as a secretKeyRef on the
    # workloads that need it. Preflight used to confirm the Secret existed while
    # the Pod read a different one entirely.
    # Name -> (secret, key), not just the variable name. Comparing names alone
    # passed a config that pointed the same variable at a different Secret.
    declared = {entry["name"]: (entry["valueFrom"]["secretKeyRef"]["name"],
                                entry["valueFrom"]["secretKeyRef"]["key"])
                for entry in _secret_env(config)}
    for document in documents:
        if document.get("metadata", {}).get("name") not in ("appbi-api", "appbi-worker"):
            continue
        name = document["metadata"]["name"]
        for spec in _pod_specs(document):
            container = spec["containers"][0]
            bound = {
                e["name"]: (e["valueFrom"]["secretKeyRef"].get("name"),
                            e["valueFrom"]["secretKeyRef"].get("key"))
                for e in container.get("env", [])
                if (e.get("valueFrom") or {}).get("secretKeyRef")
            }
            for variable, target in sorted(declared.items()):
                if variable not in bound:
                    problems.append(f"{name} does not bind {variable}")
                elif bound[variable] != target:
                    problems.append(
                        f"{name} binds {variable} to {bound[variable]}, but the "
                        f"config says {target}")
            blanket = [f["secretRef"].get("name")
                       for f in container.get("envFrom", []) if "secretRef" in f]
            if blanket:
                problems.append(
                    f"{name} still has a blanket secretRef {blanket}; it binds "
                    "whatever Secret carries that name regardless of the config")

    for document in documents:
        if document.get("kind") == "ConfigMap" and document["metadata"]["name"] == "appbi-config":
            data = document.get("data") or {}
            if data.get("AIRBYTE_API_URL") != str(engine["url"]):
                problems.append(
                    f"the rendered engine URL {data.get('AIRBYTE_API_URL')!r} is not "
                    f"{engine['url']!r} -- the installer would verify one engine and "
                    "deploy Pods pointing at another")
            if str(data.get("COOKIE_SECURE", "")).lower() != "true":
                problems.append("COOKIE_SECURE is not true in the rendered ConfigMap")
            if str(data.get("SEED_DEMO_DATA", "")).lower() != "false":
                problems.append("SEED_DEMO_DATA is not false in the rendered ConfigMap")
            # The workspace decides which Airbyte tenant receives customer
            # data. A valid id from the wrong deployment passes every other
            # check and then creates connections in someone else's tenant.
            if data.get("AIRBYTE_WORKSPACE_ID") != str(engine["workspace_id"]):
                problems.append(
                    f"the rendered workspace {data.get('AIRBYTE_WORKSPACE_ID')!r} "
                    f"is not {engine['workspace_id']!r}")

        if document.get("kind") == "Ingress":
            # From `api_url` when `ingress_host` is absent -- the example config
            # carries only the former, so keying off `ingress_host` alone left
            # the ingress unchecked on any deployment that followed the docs.
            wanted = str(product.get("ingress_host") or _host_of(product.get("api_url")))
            if wanted:
                hosts = [rule.get("host") for rule in document["spec"].get("rules", [])]
                if wanted not in hosts:
                    problems.append(f"ingress hosts {hosts} do not include {wanted!r}")
                # The certificate too: patching only the rule leaves TLS
                # requested for the example hostname.
                for entry in document["spec"].get("tls", []):
                    if wanted not in (entry.get("hosts") or []):
                        problems.append(
                            f"ingress TLS hosts {entry.get('hosts')} do not "
                            f"include {wanted!r}")

    text = rendered.read_text(encoding="utf-8")
    for token in PLACEHOLDERS:
        if token in ("<", "REPLACE_ME") or token not in text:
            continue
        problems.append(f"the rendered manifests still contain {token!r}")

    if problems:
        raise Stop("the rendered manifests do not match the config:", *problems)
    ok(f"rendered manifests match the config ({len(documents)} objects)")


def _pod_specs(document: dict):
    """Every pod spec in an object, whatever wraps it."""
    kind = document.get("kind")
    if kind in ("Deployment", "StatefulSet", "DaemonSet", "Job"):
        yield document["spec"]["template"]["spec"]
    elif kind == "CronJob":
        yield document["spec"]["jobTemplate"]["spec"]["template"]["spec"]


def assert_secrets_exist(config: dict) -> None:
    """Every Secret AppBI's Pods actually read must exist, in AppBI's namespace.

    The previous version collected every `secret://` anywhere in the config and
    looked for all of them in `product.namespace`. That asked for
    `engine.database_url_ref` -- Airbyte's own database credential, which lives
    in Airbyte's namespace and which AppBI must never read (ADR-001) -- in the
    product namespace, where it correctly does not exist.

    So the set is derived from what is bound into the Pods, plus the bootstrap
    Secret, and nothing else. A reference the product does not consume is a
    topology declaration, not a runtime dependency, and checking it here would
    fail a correct deployment.
    """
    namespace = str(config["product"]["namespace"])
    wanted: dict[str, set[str]] = {}

    for entry in _secret_env(config):
        reference = entry["valueFrom"]["secretKeyRef"]
        wanted.setdefault(reference["name"], set()).add(reference["key"])

    problems: list[str] = []
    for name, keys in sorted(wanted.items()):
        result = run(["kubectl", "-n", namespace, "get", "secret", name,
                      "-o", "jsonpath={.data}"], capture=True, check=False)
        if result.returncode != 0:
            problems.append(f"secret {name!r} does not exist in namespace {namespace}")
            continue
        present = set(json.loads(result.stdout or "{}"))
        missing = sorted(keys - present)
        if missing:
            problems.append(f"secret {name!r} is missing key(s): {', '.join(missing)}")

    # The bootstrap Secret is required only for a fresh install. The runbook
    # says to delete it once someone has signed in, and every later upgrade has
    # to keep working without it -- so its absence is reported, never fatal.
    bootstrap = _bootstrap_secret_name(config)
    if bootstrap:
        result = run(["kubectl", "-n", namespace, "get", "secret", bootstrap],
                     capture=True, check=False)
        if result.returncode != 0:
            warn(f"secret {bootstrap!r} is not present. Correct after the first "
                 "sign-in; on a fresh database the deployment will refuse to "
                 "start without it.")
        else:
            ok(f"bootstrap secret {bootstrap!r} present (delete it after first use)")

    if problems:
        raise Stop("secrets the product reads are not in the cluster:", *problems)
    if wanted:
        ok(f"{sum(len(k) for k in wanted.values())} secret key(s) present in {namespace}")


def _bootstrap_secret_name(config: dict) -> str:
    reference = str((config.get("secrets") or {}).get("bootstrap_admin_password_ref") or "")
    if not reference.startswith("secret://"):
        return ""
    return reference[len("secret://"):].partition("/")[0]


def install_k8s(config: dict) -> None:
    import tempfile

    product = config["product"]
    engine = config["engine"]

    step("prerequisites")
    need("kubectl", "the production profile applies Kubernetes manifests")
    kubectl(config, "version", "--client=true", "-o", "yaml", capture=True)
    ok("kubectl present")

    step("the engine is the one that was certified")
    verify_engine(config)

    step("secrets")
    assert_secrets_exist(config)

    step("render the config into manifests")
    with tempfile.TemporaryDirectory(prefix="appbi-render-") as tmp:
        workdir = Path(tmp)
        rendered = render_from_config(config, workdir)
        assert_rendered_matches(config, rendered)

        # Placeholders, checked on what will actually be applied. The source
        # overlay is *supposed* to contain them; checking there refused every
        # install before the renderer could replace them.
        leftover = static_gates(config, rendered=rendered)
        if leftover:
            raise Stop("the rendered manifests still carry placeholders:", *leftover)
        ok("no placeholder survived into the rendered manifests")

        namespace = str(product["namespace"])

        # 1. Migrations, alone, and finished, before anything reads the schema.
        #    A completed Job is not re-run by apply and its pod template is
        #    immutable, so it is deleted first -- explicitly, rather than via an
        #    annotation for a controller this project does not use.
        step("migrate")
        kubectl(config, "-n", namespace, "delete", "job", "appbi-migrate",
                "--ignore-not-found", "--wait=true")
        migration = _extract(rendered, workdir / "migrate.yaml",
                             kinds={"Job"}, names={"appbi-migrate"})
        kubectl(config, "apply", "-f", str(migration))
        result = kubectl(config, "-n", namespace, "wait", "--for=condition=complete",
                         "job/appbi-migrate", "--timeout=15m", check=False)
        if result.returncode != 0:
            kubectl(config, "-n", namespace, "logs", "job/appbi-migrate",
                    "--tail=100", check=False)
            raise Stop(
                "the migration Job did not complete, so nothing else was rolled out",
                "New code on an old schema fails in ways that read like "
                "application bugs. The logs above are the actual error.")
        ok("migrations are at the head this image expects")

        # 2. Everything else.
        step("apply")
        kubectl(config, "apply", "-f", str(rendered))
        kubectl(config, "apply", "-k", str(engine["connector_policy_overlay"]))

    step("rollout")
    namespace = str(product["namespace"])
    kubectl(config, "-n", namespace, "rollout", "status",
            "deploy/appbi-api", "--timeout=10m")
    kubectl(config, "-n", namespace, "rollout", "status",
            "deploy/appbi-worker", "--timeout=10m")

    step("readiness")
    base = api_base(config)
    if base.startswith("http://localhost"):
        raise Stop(
            "product.api_url is not set, so readiness cannot be checked against "
            "what users actually reach. Set it to the ingress URL.")
    wait_for(f"{base}/readyz", label="the API is serving")
    wait_for(f"{base}/readyz?deep=1", label="the engine answered")

    step("engine identity, from inside the product's own Pod")
    verify_engine_in_pod(config)


def verify_engine_in_pod(config: dict) -> None:
    """Ask the product what engine it is talking to, over the path it uses.

    The pre-deploy probe cannot do this. Production holds its credentials in
    Kubernetes secrets and `resolve_secret()` deliberately refuses to read them
    -- an installer that can read every secret is a bigger target than the
    deployment it installs. So that probe runs unauthenticated and, against an
    auth-enabled Airbyte, reads 401 and reports the engine as the wrong version
    or unreachable.

    The Pod has the credentials, the network path and the adapter. Asking it is
    the only check that proves the combination the runtime actually uses.
    """
    namespace = str(config["product"]["namespace"])
    engine = config["engine"]

    result = kubectl(
        config, "-n", namespace, "exec", "deploy/appbi-api", "--",
        "python", "-c",
        "import json,urllib.request;"
        "print(urllib.request.urlopen("
        "'http://127.0.0.1:8000/readyz?deep=1', timeout=30).read().decode())",
        capture=True, check=False)
    if result.returncode != 0:
        raise Stop("could not reach the product's own Pod to verify the engine",
                   (result.stderr or result.stdout).strip()[:300])
    try:
        report = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise Stop("the Pod's readiness output was not JSON",
                   result.stdout.strip()[:300])

    if not (report.get("dependencies") or {}).get("engine", {}).get("ok"):
        raise Stop(
            "the product cannot reach its engine from inside the cluster",
            json.dumps(report)[:400],
            "On an auth-enabled Airbyte this is usually the Application "
            "credential: see docs/RUNBOOK-engine-upgrade.md.")
    if report.get("engine_type") != str(engine.get("type", "AIRBYTE_API")):
        raise Stop(f"the Pod reports engine {report.get('engine_type')!r}, "
                   f"config says {engine.get('type')!r}")
    ok(f"the Pod reaches engine {report.get('engine_type')} over its own path")


def _extract(rendered: Path, out: Path, *, kinds: set[str], names: set[str]) -> Path:
    """Pull specific objects out of a rendered stream, keeping them byte-identical.

    Applying the migration separately must apply *the same* object the rest of
    the render describes; re-rendering a subset invites the two to differ.
    """
    import yaml

    documents = [d for d in yaml.safe_load_all(rendered.read_text(encoding="utf-8"))
                 if d and d.get("kind") in kinds
                 and d.get("metadata", {}).get("name") in names]
    if not documents:
        raise Stop(f"no {sorted(kinds)} named {sorted(names)} in the rendered manifests")
    out.write_text(yaml_dump_all(documents), encoding="utf-8")
    return out


def yaml_dump_all(documents) -> str:
    import yaml

    return yaml.safe_dump_all(documents, sort_keys=False)


def verify_engine(config: dict) -> None:
    """Refuse to deploy against an engine that is not the pinned one.

    An Airbyte that upgraded itself since certification runs different
    connector versions under a product that certified the old ones. The version
    is read from the engine, not from the config, because the config is what
    someone believes and the engine is what is true.
    """
    engine = config["engine"]
    url = str(engine.get("url") or "").rstrip("/")
    if not url:
        warn("engine.url is unset; cannot verify the engine identity")
        return

    # With the Basic auth the config declares. Probing without it against an
    # auth-enabled Airbyte returns 401, which is neither "wrong version" nor
    # "healthy" -- so the check silently never exercised the production path.
    token = _engine_bearer(config)
    status, body = http(f"{url}/api/v1/instance_configuration", timeout=20,
                        auth=_engine_auth(config), bearer=token)
    if status != 200:
        raise Stop(f"the engine at {url} did not answer instance_configuration "
                   f"(HTTP {status})", body[:300])
    try:
        live = str(json.loads(body).get("version") or "")
    except ValueError:
        raise Stop(f"the engine at {url} answered non-JSON", body[:200])

    expected = str(engine["platform_version"])
    if live != expected:
        raise Stop(
            f"the engine at {url} reports {live!r}, config pins {expected!r}",
            "Either the deployment drifted or the config is stale. Certify the "
            "running version before deploying against it.")
    ok(f"engine {live} matches the pinned version")


# ── shared steps ─────────────────────────────────────────────────────────────

def login(config: dict) -> str | None:
    """A session cookie for the operator endpoints, if credentials are given."""
    credentials = (config.get("operator") or {})
    email, password = credentials.get("email"), credentials.get("password_ref")
    if not email or not password:
        return os.getenv("APPBI_COOKIE")
    secret = resolve_secret(str(password))
    if not secret:
        return os.getenv("APPBI_COOKIE")
    request = urllib.request.Request(
        api_base(config) + "/api/v1/auth/login",
        data=json.dumps({"email": email, "password": secret}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            for header, value in response.getheaders():
                if header.lower() == "set-cookie":
                    return value.split(";")[0]
    except Exception as exc:  # noqa: BLE001
        warn(f"could not log in as {email}: {exc}")
    return os.getenv("APPBI_COOKIE")


def dotenv() -> dict[str, str]:
    """`.env`, read on demand.

    `install` writes this file and loads it into its own process, but `doctor`
    and `status` are run later from a fresh shell -- where an `env://`
    reference would resolve to nothing and the command would silently skip the
    check it was run to perform. It did: doctor reported "reconcile did not
    run: 401" and still printed a verdict.
    """
    path = ROOT / ".env"
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.split("#")[0].strip()
    return values


def resolve_secret(reference: str) -> str:
    """Turn a reference into a value, without the config ever holding one."""
    if reference.startswith("env://"):
        name = reference[len("env://"):]
        return os.getenv(name) or dotenv().get(name, "")
    if reference.startswith("file://"):
        path = Path(reference[len("file://"):])
        return path.read_text(encoding="utf-8").strip() if path.exists() else ""
    # secret:// is resolved by Kubernetes at pod start, not by this process --
    # deliberately: an installer that can read every secret is a bigger target
    # than the deployment it installs.
    return ""


def reconcile(config: dict, cookie: str | None) -> dict:
    request = urllib.request.Request(api_base(config) + "/api/v1/engine/reconcile")
    if cookie:
        request.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return json.loads(response.read() or b"{}")
    except Exception as exc:  # noqa: BLE001
        warn(f"reconcile did not run: {exc}")
        return {}


def record_artifact(config: dict, cookie: str | None, out: Path) -> bool:
    """Record what was actually deployed. No artifact, no release."""
    evidence = [str(ROOT / name)
                for name in (config.get("release") or {}).get("evidence", [])
                if (ROOT / name).exists()]
    if not evidence:
        warn("no evidence files listed in release.evidence, or none exist; "
             "skipping the artifact. A deployment without one is not a release "
             "-- run scripts/e2e.py --evidence first.")
        return False

    command = [sys.executable, str(ROOT / "scripts" / "release-gate.py"), "record",
               "--out", str(out), "--product-url", api_base(config),
               "--evidence", *evidence]
    overlay = (config.get("product") or {}).get("overlay")
    if overlay:
        command += ["--overlay", str(overlay)]
    policy = (config.get("engine") or {}).get("connector_policy_overlay")
    if policy:
        command += ["--engine-policy-overlay", str(policy)]
    result = run(command, check=False, env={"APPBI_COOKIE": cookie or ""})
    return result.returncode == 0


# ── commands ─────────────────────────────────────────────────────────────────

def static_gates(config: dict, rendered: Path | None = None) -> list[str]:
    """Everything decidable without the cluster, checked on the right artefact.

    Two ordering bugs lived here.

    The first was running this *after* `install_k8s()`, so a deployment with
    `LIC-001: NOT_CLEARED` migrated the database and rolled out Pods and only
    then exited 1. The exit code was right and the deployment had happened.

    The second was checking the **source** overlay for placeholders. That
    overlay is supposed to contain `registry.internal` and
    `appbi.example.internal` -- they are the deliberately-wrong values that make
    an unedited `kubectl apply -k` fail closed. Checking them there meant every
    install refused before `render_from_config()` had a chance to replace them,
    so a correct config could never get past its own gate.

    Placeholders are a property of what will be applied, so they are checked on
    the rendered manifests. Licence and on-call need no manifests at all, so
    they are checked first -- before anything is rendered or pulled.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "release_gate", ROOT / "scripts" / "release-gate.py")
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)

    problems: list[str] = []
    problems += gate.check_release_gates()
    problems += gate.check_oncall_assigned()

    if rendered is not None:
        text = rendered.read_text(encoding="utf-8")
        for token in PLACEHOLDERS:
            if token in ("<", "REPLACE_ME") or token not in text:
                continue
            problems.append(
                f"{token!r} is still in the rendered manifests")
    return problems


def cmd_install(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))
    profile = config["profile"]
    # Production has no warning-only release invariants. The demo does, because
    # a laptop has no artifact and no reconcile history, and saying so is more
    # useful than refusing.
    strict = profile != "single-host-demo"
    failures: list[str] = []

    def problem(message: str) -> None:
        if strict:
            failures.append(message)
            print(f"  FAIL  {message}", file=sys.stderr, flush=True)
        else:
            warn(message)

    step(f"validating {args.config}")
    for message in validate(config, strict=not args.allow_insecure):
        warn(message)
    ok(f"profile {profile}")

    if strict:
        step("static gates (before anything is deployed)")
        blocked = static_gates(config)
        if blocked:
            print("\nDEPLOYMENT REFUSED", file=sys.stderr)
            for message in blocked:
                print(f"  - {message}", file=sys.stderr)
            print("\nNothing was applied. These are decidable without the "
                  "cluster, so they are checked before the cluster is touched.",
                  file=sys.stderr)
            return 1
        ok("licence, on-call and placeholder gates pass")

    if profile == "single-host-demo":
        install_demo(config, build=not args.no_build)
    else:
        install_k8s(config)

    cookie = login(config)

    step("engine mappings match the engine")
    report = reconcile(config, cookie)
    if not report:
        problem("reconcile did not run, so nothing is known about whether this "
                "deployment's engine mappings resolve")
    elif report.get("consistent"):
        ok(report.get("detail", "consistent"))
    else:
        problem(report.get("detail", "engine mappings do not match the engine"))
        for item in report.get("missing", [])[:10]:
            print(f"        missing {item['resource_type']}: {item['name']}",
                  file=sys.stderr)

    step("release artifact")
    artifact = Path(args.artifact)
    if not record_artifact(config, cookie, artifact):
        problem("no release artifact was recorded; a deployment without one is "
                "not a release")
    else:
        gate = run([sys.executable, str(ROOT / "scripts" / "release-gate.py"),
                    "check", str(artifact)], check=False)
        if gate.returncode != 0:
            problem(f"the release gate refused this artifact (exit {gate.returncode})")
        else:
            ok("release gate passed")

    if failures:
        print("\nINSTALL FAILED", file=sys.stderr)
        for message in failures:
            print(f"  - {message}", file=sys.stderr)
        print("\nThe workload is running. It is not a release: the checks "
              "above are what make it one.", file=sys.stderr)
        return 1

    step("done")
    print(f"  product : {api_base(config)}")
    if profile == "single-host-demo":
        port = (config.get("demo") or {}).get("proxy_port") or 8080
        print(f"  UI      : http://localhost:{port}")
        operator = config.get("operator") or {}
        if operator.get("email"):
            print(f"  sign in : {operator['email']}")
    print("  next    : python scripts/production.py doctor "
          f"--config {args.config}")
    return 0


def cmd_upgrade(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))

    step(f"validating {args.config}")
    for message in validate(config, strict=not args.allow_insecure):
        warn(message)

    step("backup before anything is changed")
    if args.skip_backup:
        warn("--skip-backup: no rollback point will exist for this upgrade")
    else:
        backup = run([sys.executable, str(ROOT / "scripts" / "backup.py"), "dump"],
                     check=False)
        if backup.returncode != 0:
            raise Stop(
                "the pre-upgrade backup failed, so the upgrade stops here",
                "An upgrade with no rollback point is not an upgrade, it is a "
                "one-way door. Fix the backup or pass --skip-backup knowingly.")
        ok("backup taken")

    return cmd_install(args)


def cmd_status(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))
    base = api_base(config)

    step("product")
    unhealthy: list[str] = []
    for path in ("/healthz", "/readyz", "/readyz?deep=1"):
        status, body = http(f"{base}{path}")
        mark = "ok   " if status == 200 else "FAIL "
        print(f"  {mark} {path:<18} {status or 'unreachable'} {body[:120]}")
        if status != 200:
            unhealthy.append(f"{path} answered {status or 'nothing'}")

    if config["profile"] == "single-host-demo":
        step("containers")
        compose(config, "ps", check=False)
    else:
        step("workloads")
        kubectl(config, "-n", str(config["product"]["namespace"]),
                "get", "deploy,pod", check=False)

    if unhealthy:
        # `status` returned 0 whatever it printed, so anything that consumed it
        # -- a deploy pipeline, a cron check, a person's `&&` -- read a failing
        # deployment as healthy. A command whose output says FAIL and whose
        # exit code says success is worse than no command.
        print("\nNOT HEALTHY", file=sys.stderr)
        for message in unhealthy:
            print(f"  - {message}", file=sys.stderr)
        return 1
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))
    service = args.service or "api"
    if config["profile"] == "single-host-demo":
        compose(config, "logs", "--tail", str(args.tail), service, check=False)
    else:
        kubectl(config, "-n", str(config["product"]["namespace"]),
                "logs", f"deploy/appbi-{service}", f"--tail={args.tail}",
                check=False)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Everything an operator would otherwise check by hand, with a verdict."""
    config = load_config(Path(args.config))
    base = api_base(config)
    problems: list[str] = []
    warnings: list[str] = []

    step("config")
    try:
        warnings += validate(config, strict=not args.allow_insecure)
        ok("config validates")
    except SystemExit:
        return 2

    step("health")
    ready = None
    for path in ("/healthz", "/readyz", "/readyz?deep=1"):
        status, body = http(f"{base}{path}")
        print(f"  {'ok   ' if status == 200 else 'FAIL '} {path:<18} {status or 'unreachable'}")
        if path.endswith("deep=1"):
            if status != 200:
                problems.append(f"deep readiness is failing: {body[:200]}")
            else:
                try:
                    ready = json.loads(body)
                except ValueError:
                    pass

    step("engine")
    if ready:
        ok(f"type {ready.get('engine_type')}")
    try:
        verify_engine(config)
    except SystemExit:
        problems.append("the running engine is not the pinned version")

    engine = config.get("engine") or {}
    url = str(engine.get("url") or "")
    if url:
        status, body = http(f"{url}/api/v1/instance_configuration", timeout=20)
        if status == 200:
            try:
                mode = (json.loads(body).get("auth") or {}).get("mode")
            except ValueError:
                mode = None
            if mode in (None, "none") and config["profile"] == "external-airbyte-k8s":
                problems.append(
                    "the engine has authentication disabled. The certification "
                    "profile ran that way deliberately; a production engine "
                    "must not.")
            else:
                ok(f"engine auth mode {mode}")
        if url.startswith("http://") and config["profile"] == "external-airbyte-k8s":
            warnings.append("engine.url is plain HTTP")

    step("database separation")
    status, body = http(f"{base}/readyz?deep=1")
    if status == 200 and "separation" in body.lower():
        problems.append(f"the readiness probe reports a separation problem: {body[:200]}")
    else:
        ok("the product did not report sharing a database with the engine")

    step("engine mappings")
    report = reconcile(config, login(config))
    if not report:
        warnings.append("reconcile did not run; could not check mappings")
    elif report.get("consistent"):
        ok(report.get("detail", "consistent"))
    elif not report.get("engine_reachable", True):
        problems.append("the engine did not answer a reconcile")
    else:
        problems.append(f"{len(report.get('missing', []))} mapped resources are "
                        "not on this engine")

    step("release gates")
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "release_gate", ROOT / "scripts" / "release-gate.py")
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    problems += gate.check_release_gates()
    problems += gate.check_oncall_assigned()
    for overlay, label in ((("product" in config and config["product"].get("overlay")), "the product"),
                           (((config.get("engine") or {}).get("connector_policy_overlay")), "the connector policy")):
        if overlay:
            problems += gate.check_deployment_placeholders(str(overlay), label=label)

    step("verdict")
    for message in warnings:
        warn(message)
    if problems:
        print("\nNOT PRODUCTION READY", file=sys.stderr)
        for message in problems:
            print(f"  - {message}", file=sys.stderr)
        return 1
    ok("no problems found")
    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    """Print the rollback that matches an artifact. Deliberately not automatic.

    Rolling the product back past a migration, or past engine resources it has
    since created, is not a button -- it is a decision with a runbook. What this
    can do is say exactly what was deployed, which is the part nobody has to
    hand at 3am.
    """
    path = Path(args.artifact)
    if not path.exists():
        raise Stop(f"no artifact at {path}")
    artifact = json.loads(path.read_text(encoding="utf-8"))

    print(f"\nThis artifact describes:\n")
    print(f"  recorded   : {artifact.get('recorded_at', '?')}")
    print(f"  commit     : {artifact.get('commit', '(none recorded)')}")
    print(f"  product    : {artifact.get('product_version', '?')}")
    engine = artifact.get("engine") or {}
    print(f"  engine     : {engine.get('type')} {engine.get('platform_version')}")
    for key, value in (engine.get("connector_versions") or {}).items():
        print(f"               {key}: {value.get('engine_image')}")

    print("""
To roll back:

  1. Restore the product database from the dump taken before the upgrade,
     together with its KEK. See docs/RUNBOOK-backup-restore.md.
  2. Redeploy the product at the commit above.
  3. Run `production.py doctor` and then `scripts/reconcile.py`.

The step people skip is 3. Rolling the product back past resources it created
on the engine leaves mappings pointing at things that no longer exist, and the
symptom is a not-found on the next sync rather than an error at rollback time.
""")
    return 0


def main() -> int:
    force_utf8()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def with_config(subparser):
        subparser.add_argument("--config", default=str(DEFAULT_CONFIG))
        subparser.add_argument("--allow-insecure", action="store_true",
                               help="downgrade production-only checks to "
                                    "warnings; never for a real deployment")
        return subparser

    install = with_config(sub.add_parser("install", help="stand it up, idempotently"))
    install.add_argument("--artifact", default="certification.json")
    install.add_argument("--no-build", action="store_true")

    upgrade = with_config(sub.add_parser("upgrade", help="back up, then install"))
    upgrade.add_argument("--artifact", default="certification.json")
    upgrade.add_argument("--no-build", action="store_true")
    upgrade.add_argument("--skip-backup", action="store_true")

    with_config(sub.add_parser("status", help="what is running"))
    with_config(sub.add_parser("doctor", help="is this deployment production-ready"))

    logs = with_config(sub.add_parser("logs", help="tail one service"))
    logs.add_argument("service", nargs="?", default="api")
    logs.add_argument("--tail", type=int, default=200)

    rollback = sub.add_parser("rollback", help="what an artifact was, and how to undo it")
    rollback.add_argument("--artifact", default="certification.json")

    args = parser.parse_args()
    return {
        "install": cmd_install, "upgrade": cmd_upgrade, "status": cmd_status,
        "doctor": cmd_doctor, "logs": cmd_logs, "rollback": cmd_rollback,
    }[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
