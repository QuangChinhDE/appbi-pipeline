---
paths:
  - "frontend/**"
---

# Frontend rules

Next.js 15 App Router, React 18, TypeScript, Tailwind, TanStack Query. All
verified from `frontend/src/`.

## Data access

- Every server call goes through `src/lib/api.ts`. Do not `fetch('/api/v1/...')`
  from a component; add a typed function to the client instead.
- Response and request types live in `src/lib/types.ts`. Do not inline an
  `any`-shaped literal at the call site.
- Query keys come from `src/lib/queryKeys.ts` (`qk.*`) and are **workspace
  scoped** — `['workspace', wsId, ...]`. A new key that omits `wsId` survives a
  workspace switch and leaks one tenant's cache into another. Add to `qk`, never
  hand-write an array.
- Errors arrive in the normalized envelope as `ApiError`, which carries a
  remediation action. Surface that action as the screen's primary CTA rather
  than printing a raw message.

## Screens

- Routes are `src/app/(main)/workspaces/[wsId]/...` for tenant screens and
  `src/app/(admin)/...` for cross-workspace admin. Put a screen where its
  audience is.
- Every data-backed view needs three states: loading, error, empty. Shared
  primitives are in `src/components/ui/Feedback.tsx`.
- Reuse `src/components/ui/` (Badge, Button, Disclosure, Feedback, FilePicker,
  Input, Menu, Modal, Tabs). Do not add a second button or modal.
- Tailwind utilities with `clsx` / `tailwind-merge` (`cn` in `src/lib/utils.ts`).
  No ad-hoc CSS files, no inline style objects for things Tailwind expresses.

## i18n

Bilingual (en/vi) via `src/lib/i18n.ts` and `src/providers/LanguageProvider.tsx`.

- User-visible strings call `t('some.key')` with a **literal** key, and the key
  must exist in both catalogs. A missing key renders as the key string itself.
- Never write Vietnamese (or English) prose directly into a component under
  `app/`, `components/`, `providers/` or `hooks/`. No language setting can reach it.
- Server-supplied text (connector titles, engine log lines) is exempt and uses
  `tf()` for its fallback.

This is enforced structurally by `backend/tests/test_i18n_coverage.py`, which
reads the frontend source. It runs in CI. Run it after touching UI strings.

## Discipline

- No business rule or data correction implemented in the UI when the root cause
  is in a service, an adapter or the database. Fix the owner of the failure.
- `tsc --noEmit` and `next lint --max-warnings 0` must both pass; warnings fail CI.
- For a user-facing change, verify it visually in a running app when browser
  tooling is available — in addition to, never instead of, typecheck/lint/build.
