# Spec: <feature>

Fill in only the sections this feature actually has. Delete the rest — invented
detail is worse than an absent section.

## Behaviour

What the system does, including the unhappy paths.

## UX

Screens, states (loading / error / empty), navigation, what the user sees when
it fails. New user-facing strings need `t()` keys in both `en` and `vi`.

## API contract

Endpoints, methods, request and response shapes, status codes, error categories
and their remediation actions. State explicitly whether this is
backward-compatible.

## Data contract

Tables and columns touched, migration strategy, what happens to existing rows,
what a partially-upgraded installation sees.

## Permissions

Which roles may do this, where it is enforced, and how tenancy is scoped.

## Edge cases

Empty, very large, concurrent, partially failed, retried, offline engine.

## Compatibility

Engine versions, connector versions, deployment modes affected. If this touches
`engine-lock.json`, `connector-lock.json` or `compatibility.yaml`, say why here.

## Security

Credentials handled, secrets stored, anything newly exposed in a response or a
log. Any change to an auth or credential boundary needs a review paragraph here.

## Affected architecture

Layers and files, using the table in `.claude/CLAUDE.md`.
