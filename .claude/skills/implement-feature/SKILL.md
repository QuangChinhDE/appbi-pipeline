---
name: implement-feature
description: Implement a feature or change in AppBI Pipeline. Use when adding or changing product behaviour in the backend, frontend, transform subsystem, connectors or deployment. Walks from intent through impact analysis, the smallest coherent change, layered verification, and a Changed/Verified/Not verified/Risks report.
---

# Implement a feature

## 1. Decide the risk level first

Low, Medium and High are defined in `.claude/CLAUDE.md`, with what each adds to
the required workflow. Decide before editing: the level determines whether you
plan first, whether regression coverage is required, and whether the change
needs an independent `/review-change`.

Judge it by what the change can break, not by the size of the diff: a one-line
edit to a permission check, a migration or connector compatibility is High. So
is a task that only turns out to touch one of those halfway through.

Where the work crosses layers or the acceptance is unclear, write the plan in
`.sdlc/features/<slug>/` from `.sdlc/templates/` rather than in the session.

## 2. Understand before proposing

- Read the existing implementation of the thing you are changing. Do not design
  from the directory names.
- Name the layers affected, using the table in `.claude/CLAUDE.md`. Remember:
  routers are thin, business logic is in `services/`, there is no repository
  layer, engines are reachable only through `app/adapters/base.py`.
- Read the scoped rule for each area you will touch (`.claude/rules/`).
- Find the tests that already cover it (`backend/tests/`). Name them before
  writing code. If none exist, that is the first thing to write.
- Name the regression surface: what else calls this, what persists, what a
  running installation already has.

## 3. Impact analysis, when it applies

Write this out before coding if the change touches an engine, the database,
authentication, credentials or deployment:

- What contract does this change? (`engine-lock.json`, `connector-lock.json`,
  `compatibility.yaml`, an API response shape, a table)
- Which deployments are affected? (embedded / airbyte / transform / storage /
  egress / production / kubernetes)
- What happens to an existing installation with real data?
- How is it rolled back?

If the answer to any of these is "I do not know", find out before editing.

## 4. Implement

- The smallest coherent change. No unrelated cleanup, no speculative refactor,
  no dependency upgrades.
- Reuse what exists: `app/core/` on the backend, `src/components/ui/` and
  `src/lib/api.ts` on the frontend.
- Match the surrounding style, including the comment habit of this repository:
  comments explain *why*, and often cite the incident that caused the rule.

## 5. Verify, continuously

    python scripts/verify.py fast

Run it after each meaningful edit. It picks steps from your diff.

## 6. Before you say done

1. Read your own diff: `git diff`. Look for debug output, unrelated hunks,
   a secret, a machine-specific path.
2. Run `python scripts/verify.py task`.
3. Re-read the acceptance criteria and check each one.
4. Self-review: is any part of this fixed in the wrong layer? Did a UI change
   paper over a backend bug? Did an adapter change leave the other
   implementation behind?

## 7. Report

Exactly four sections: **Changed**, **Verified** (commands and their real
results), **Not verified** (and why), **Risks**. Never call something verified
that you only reasoned about.
