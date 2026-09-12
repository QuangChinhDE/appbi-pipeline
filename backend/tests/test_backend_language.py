"""What the backend says, it says in one language.

A failure's message is written by whoever raises it, which is long before
anybody reads it and in nobody's particular language -- a worker has no
request, so it cannot know who is asking. Whatever the author spoke got frozen
into the database and into the API, and that is why an English page answered a
failed sync in Vietnamese. Not a missing translation: a message that had
already decided.

The shape that fixes it is the one the product already had and was not using.
Every error travels with a stable code, so the code is what the browser
translates, the message is only the fallback, and the fallback is written in
the default language rather than the author's. The Vietnamese did not go
anywhere -- it moved to the `vi` catalog beside its English, where a reader can
be given either.

This test keeps it that way, and names the places where Vietnamese is still
correct.
"""

from __future__ import annotations

import ast
import pathlib
import re

APP = pathlib.Path(__file__).resolve().parents[1] / "app"

VIETNAMESE = re.compile(
    r"[àáảãạăằắẳẵặâầấẩẫậđèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵ]",
    re.I,
)

#: A `*_vi` key is the Vietnamese half of a bilingual pair, which is the whole
#: point of the pair. `localizeSpec()` in the browser picks one or the other.
VI_FIELD = re.compile(r'_vi["\']?\s*[:=]')
#: A dict key or an assignment. The quotes have to balance: a continuation line
#: reading `"cursor: every run reads from exactly this point"` is prose, not a
#: key, and reading it as one ends the `_vi` block three lines early.
ANY_KEY = re.compile(
    r'^\s*(?:(["\'])[A-Za-z_][A-Za-z0-9_]*\1|[A-Za-z_][A-Za-z0-9_]*)\s*[:=]'
)

#: Vietnamese that is correct where it is, and why. Each entry is a file and
#: the reason the words in it are not a translation problem.
ALLOWED: dict[str, str] = {
    # Quoting a vendor in translation makes the quote useless for finding the
    # thing it quotes. These docstrings carry KiotViet's own API documentation
    # and the Vietnamese error text one of its endpoints returns.
    "connectors/kiotviet/catalog.py": "quotes KiotViet's own documentation",
    "connectors/kiotviet/_shared.py": "quotes an error KiotViet returns",
    # A docstring about folding Vietnamese accents, and the fold table itself.
    # Both are code about Vietnamese rather than Vietnamese shown to anybody.
    "transforms/projects.py": "the accent-folding rule and its table",
    # The naming rule's example is a Vietnamese name because that is the case
    # the rule exists for.
    "services/actors.py": "a docstring example",
}


def _sources() -> list[pathlib.Path]:
    return sorted(APP.rglob("*.py"))


def _offending_lines(path: pathlib.Path) -> list[tuple[int, str]]:
    """Vietnamese lines, skipping comments and the `_vi` half of a pair."""
    out: list[tuple[int, str]] = []
    in_vi_field = False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        key = ANY_KEY.match(line)
        if key:
            in_vi_field = bool(VI_FIELD.search(line))
        if in_vi_field or line.strip().startswith("#"):
            continue
        if VIETNAMESE.search(line):
            out.append((number, line.strip()[:90]))
    return out


def test_no_vietnamese_outside_the_places_it_belongs() -> None:
    offences: list[str] = []
    for path in _sources():
        relative = path.relative_to(APP).as_posix()
        if relative in ALLOWED:
            continue
        for number, text in _offending_lines(path):
            offences.append(f"  {relative}:{number}  {text}")

    assert not offences, (
        "Vietnamese frozen into the backend, where no reader can choose it:\n"
        + "\n".join(offences)
        + "\n\nGive the error a code, write the message in English, and put the "
        "Vietnamese in the frontend catalog under `errorCode.<CODE>`."
    )


def test_the_allowed_list_has_no_stale_entries() -> None:
    """An exemption that stopped being needed is an exemption that will hide
    the next real one."""
    stale = [
        relative for relative in ALLOWED
        if not _offending_lines(APP / relative)
    ]
    assert not stale, f"these no longer hold any Vietnamese: {stale}"


def test_every_error_raised_carries_a_code() -> None:
    """Without a code there is nothing to translate by, and nothing to search
    for in support.

    Only the error classes that set a class-level default are exempt, and they
    are exempt by having one.
    """
    carries_own_code = {
        "ValidationError", "NotFoundError", "UnauthorizedError", "ForbiddenError",
        "ConflictError", "ResourceInUseError", "ResourceModifiedError",
        "QuotaExceededError", "RateLimitedError", "EngineUnavailableError",
        "EngineOperationError", "EngineResourceGoneError", "AppError",
    }

    missing: list[str] = []
    for path in _sources():
        source = path.read_text(encoding="utf-8")
        if not any(name in source for name in carries_own_code):
            continue
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
                continue
            call = node.exc
            name = call.func.id if isinstance(call.func, ast.Name) else None
            if name not in carries_own_code or not call.args:
                continue
            # A bare `raise NotFoundError()` takes the class's own code.
            if any(keyword.arg == "code" for keyword in call.keywords):
                continue
            message = call.args[0]
            if isinstance(message, ast.Constant) and not message.value.strip():
                continue
            missing.append(
                f"  {path.relative_to(APP).as_posix()}:{node.lineno}  {name}"
            )

    assert not missing, (
        "errors raised with a message but no code, so the browser has nothing "
        "to translate by:\n" + "\n".join(missing)
    )
