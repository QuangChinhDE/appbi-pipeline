---
paths:
  - "frontend/**"
---

# Frontend rules

Next.js 15 App Router, React 18, TypeScript, Tailwind, TanStack Query. Every
statement below was checked against `frontend/src/`; counts are from 2026-09-15.

## Data access

- Server calls go through `src/lib/api.ts`. There is exactly **one** raw
  `fetch()` elsewhere in the tree
  (`app/(main)/workspaces/[wsId]/transforms/new/page.tsx`); treat it as the
  exception it is, not as licence. Add a typed function to the client instead.
- Request and response types live in `src/lib/types.ts`.
- Errors arrive as `ApiError` carrying `remediation.action` from the backend's
  `ERROR_UX_MATRIX`. Turn that action into the screen's primary CTA rather than
  printing a raw message. `isPermissionDenied(error)` in
  `components/ui/Feedback.tsx` distinguishes a permission failure from a real one.

## Query keys — two conventions, both correct

Do not "fix" one into the other. 87 `qk.*` uses and ~50 literal keys coexist on
purpose:

1. **Reads use `qk.*`** from `src/lib/queryKeys.ts`. Workspace-scoped keys are
   shaped `['workspace', wsId, ...]`. A new *read* key belongs in `qk`.
2. **Broad invalidation uses the literal prefix**
   `queryClient.invalidateQueries({ queryKey: ['workspace', workspaceId] })`.
   That is the designed mechanism for evicting a whole tenant subtree in one
   call — twelve screens do it. It is not a missing `qk` entry.
3. **Admin screens under `src/app/(admin)/`** use non-workspace keys
   (`['organization']`, `['org-people']`, `['auth-config']`) because they are
   cross-workspace by nature. Correct as-is.
4. A few component-local keys (`['builder-ai-session', project.id]`) are scoped
   to a dialog's own lifetime. Also fine.

The invariant worth protecting: a **workspace-scoped read** must sit under the
`['workspace', wsId, …]` prefix, or a workspace switch will serve one tenant's
data to another.

## Screens and states

- `src/app/(main)/workspaces/[wsId]/...` for tenant screens,
  `src/app/(admin)/...` for cross-workspace admin.
- Every data-backed view needs loading, error and empty states. Use
  `components/ui/Feedback.tsx`: `Skeleton`, `TableSkeleton`, `CardSkeleton`,
  `Spinner`, `EmptyState`, `ErrorState`. Adoption is already broad —
  `ErrorState` in 25 files, `EmptyState` in 27 — so a new screen without them
  is the outlier.
- `ErrorState` takes an `onRetry`; wire it to the query's `refetch()`.
- Reuse `src/components/ui/`: Badge, Button, Disclosure, Feedback, FilePicker,
  Input, Menu, Modal, Tabs. Do not add a second button or modal.
- Tailwind with `cn()` from `src/lib/utils.ts` (clsx + tailwind-merge). No ad-hoc
  CSS files.

## i18n

Bilingual en/vi via `src/lib/i18n.ts` and `src/providers/LanguageProvider.tsx`.

- User-visible strings call `t('some.key')` with a **literal** key present in
  both catalogs. An unknown key renders as the key string itself.
- Never write Vietnamese or English prose directly into a component under
  `app/`, `components/`, `providers/`, `hooks/`.
- Server-supplied text (connector titles, engine log lines) is exempt and uses
  `tf()` for its fallback.

Enforced by `backend/tests/test_i18n_coverage.py`, which reads this source tree
and runs in CI. Run it after touching UI strings: `npm run check:i18n`.

## Discipline

- No business rule or data correction implemented in the UI when the root cause
  is in a service, an adapter or the database.
- `tsc --noEmit` must pass. `next lint` must pass — note the CI lane does *not*
  pass `--max-warnings 0` even though the `lint` script does; two
  `react-hooks/exhaustive-deps` warnings are outstanding. Do not silence them
  incidentally.
- For a user-facing change, verify it visually in a running app when browser
  tooling is available — alongside, never instead of, typecheck/lint/build.
