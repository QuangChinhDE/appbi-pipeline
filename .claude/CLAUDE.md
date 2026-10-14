# AppBI Pipeline — Claude Code adapter

The project contract is imported, not copied, so the two cannot drift:

@../AGENTS.md

Everything below is specific to Claude Code.

## Rules

`.claude/rules/*.md` load automatically when a file matching their `paths:`
frontmatter is read or edited. Before editing, check which rules match the
files in your diff — the table in `AGENTS.md` lists them.

## Skills

| Skill | Use for |
|---|---|
| `/implement-feature` | adding or changing product behaviour |
| `/fix-bug` | a defect, regression or failing check |
| `/review-change` | the independent review High-risk work requires, in a fresh session |

## Hooks

`.claude/settings.json` wires `scripts/claude_guard.py` as PreToolUse hooks.
They are local defence-in-depth, not a security boundary: they match command
text, so they catch the common forms of an irreversible command or a hand-edit
of a protected contract at the moment it is attempted, and an indirect enough
command gets past them. They err toward blocking -- a heredoc that merely
quotes a dangerous command is refused; write such text with the Write tool. Server-side branch rules and CI are the
enforcement; the hook only makes the mistake visible earlier.

| Hook | Blocks |
|---|---|
| `bash` (matcher `Bash\|PowerShell`) | `git reset --hard`, discarding checkouts/restores, `git clean -f`, force push, `branch -D`, `docker compose down -v`, `docker volume rm/prune`, `run.* --clean`, KEK rotation, lock regeneration, and any shell command that names a lock file, `.env` or `secrets/` and also writes (`>`, `sed -i`, `tee`, `cp`/`mv`/`rm`, `Set-Content`, ...) |
| `contracts` (matcher `Write\|Edit\|MultiEdit\|NotebookEdit`) | edits to `engine-lock.json`, `connector-lock.json`, `compatibility.yaml`, `.env*` (not the examples) and `secrets/` |

If a hook blocks something the user explicitly asked for, say so and let the
user run it themselves. Never edit the hook or its settings to get past it.

Personal settings belong in `.claude/settings.local.json`, which is gitignored.

## Memory

Auto Memory is one machine's notes and sits at the bottom of the
source-of-truth hierarchy. Verify a remembered file, flag or command before
acting on it.

## Learning policy

When the same class of mistake recurs, do not stop at Auto Memory. Decide where
it belongs and put it there:

| Kind of learning | Home |
|---|---|
| One-off debugging detail | Auto Memory |
| Stable convention for one area | a scoped file in `.claude/rules/` |
| Workflow repeated across tasks | a skill in `.claude/skills/` |
| Architecture invariant | `AGENTS.md` or a scoped rule |
| Mechanically checkable invariant | a test in `backend/tests/` |
| Release-critical invariant | `.github/workflows/ci.yml` |
| Operation that must never happen unattended | a hook in `.claude/settings.json` |

Prefer executable enforcement over more prose. When a rule here becomes a test,
delete the prose.
