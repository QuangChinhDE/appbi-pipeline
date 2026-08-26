# Airbyte 0.59.1 source

The images in this directory are the engine. This note is about the *source*,
which is a separate decision.

## Why this version

0.59.1 is the last Airbyte that runs a sync under Docker Compose.
From the 0.63 line onward every connector job goes through the workload
launcher, which resolves `kubernetes.default.svc` and has no Docker mode —
verified against the real images, not from documentation:

| Version | In Compose |
|---|---|
| 1.8.5 | bootloader needs a Kubernetes namespace, twice; the second has no opt-out |
| 0.64.7 | control plane runs, every connector job fails in the workload launcher |
| 0.59.1 | predates the workload launcher; the worker starts connectors on the Docker daemon |

## Getting the source

```bash
git clone --depth 1 --branch v0.59.1 \
    https://github.com/airbytehq/airbyte.git vendor/engine/src
```

It is a Gradle monorepo in Java and Kotlin. Building the platform images:

```bash
cd vendor/engine/src
./gradlew :oss:airbyte-server:assemble
./gradlew :oss:airbyte-container-orchestrator:assemble
```

Expect a long first build and a large Gradle cache.

## Before forking

Airbyte is licensed **ELv2**. Running it is one thing; modifying and
distributing it inside a commercial product is a different question, and it is
the same question `LIC-001` in `compatibility.yaml` is already open on. Get that
answered before shipping a modified build to a customer.

A local patch that is *not* a fork — an environment variable, a config change,
a sidecar — avoids the question entirely and is worth trying first.
