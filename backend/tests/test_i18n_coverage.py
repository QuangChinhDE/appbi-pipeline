"""A key the catalog does not know renders as the key itself.

`translate()` falls back from `en` to `vi` and then to the key string, which is
the right behaviour at runtime -- a missing translation should not blank the
page -- but it means a typo or a forgotten entry ships as literal text. The
Transform workbench shipped three of them:

    tf.file.fromTable   the toolbar button read "tf.file.fromTable"
    tf.insp.columns     a table heading in the resource inspector
    tf.out.kind         a column heading in the results panel

Nobody noticed, because the string is only visible on the screen that uses it
and it looks enough like a label at a glance. They were found by walking the
rendered DOM of a running page, one screen at a time, which is not a thing
anybody does before a release.

The second half of this test is the other direction: Vietnamese written
straight into a component, which no language setting can reach. That was the
original complaint -- an English page with Vietnamese fragments in it. The
sweep that fixed it was driven by a literal-string extractor, so it could not
see a JSX text node spread over two lines or a template literal with a hole in
it, and it left fifty of them behind.

Both checks are structural: they read the frontend source, so they cost
nothing and they run with the rest of the suite.
"""

from __future__ import annotations

import pathlib
import re

import pytest

FRONTEND = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"
CATALOG = FRONTEND / "lib" / "i18n.ts"

#: A key written as a literal inside `t(...)`. Keys built from a template
#: literal -- `t(\`run.${status}\`)` -- are deliberately not matched: the value
#: comes from the server, and `tf()` exists precisely to give those a fallback.
CALL = re.compile(r"(?<![A-Za-z0-9_$.])t\(\s*'([A-Za-z0-9_.]+)'")

#: Both quote styles appear in the catalog, so the key pattern accepts either.
ENTRY = re.compile(r"^\s+(['\"])([A-Za-z0-9_.]+)\1\s*:", re.M)

VIETNAMESE = re.compile(
    r"[àáảãạăằắẳẵặâầấẩẫậđèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵ]",
    re.I,
)

#: Directories whose strings belong to the product's own interface. Anything
#: the server sends -- connector titles, Airbyte's log lines -- is exempt by
#: definition and does not live here.
UI_DIRS = ("app", "components", "providers", "hooks")


def _catalogs() -> tuple[set[str], set[str]]:
    text = CATALOG.read_text(encoding="utf-8")
    vi_source, rest = text.split("const en: Catalog = {", 1)
    en_source = rest.split("\nexport const CATALOGS", 1)[0]
    return (
        {match.group(2) for match in ENTRY.finditer(vi_source)},
        {match.group(2) for match in ENTRY.finditer(en_source)},
    )


def _sources() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for directory in UI_DIRS:
        files.extend(sorted((FRONTEND / directory).rglob("*.ts")))
        files.extend(sorted((FRONTEND / directory).rglob("*.tsx")))
    return files


@pytest.mark.skipif(not CATALOG.exists(), reason="frontend source not present")
def test_every_key_the_code_asks_for_is_in_both_catalogs() -> None:
    vi, en = _catalogs()
    missing: dict[str, list[str]] = {}
    for path in _sources():
        for match in CALL.finditer(path.read_text(encoding="utf-8")):
            key = match.group(1)
            if key in vi and key in en:
                continue
            missing.setdefault(key, []).append(
                path.relative_to(FRONTEND).as_posix()
            )

    assert not missing, "keys that render as themselves:\n" + "\n".join(
        f"  {key}  ({', '.join(sorted(set(files)))})"
        for key, files in sorted(missing.items())
    )


@pytest.mark.skipif(not CATALOG.exists(), reason="frontend source not present")
def test_the_two_catalogs_hold_the_same_keys() -> None:
    """Every key a screen asks for must exist in both, or it renders as itself.

    `.one` is exempt, and that is not a loophole. It is the singular form of a
    count, which English needs and Vietnamese does not have -- `1 workspace`
    against `in 1 workspaces`, where the Vietnamese plural is already correct
    for every number. `translate()` falls back to the base key when the variant
    is absent, so a missing `.one` cannot render as a raw key, which is the
    only thing this test exists to prevent. Demanding one anyway would mean
    copying the Vietnamese string beside itself to satisfy a rule about a
    problem that language does not have.
    """
    vi, en = _catalogs()
    plural_variant = lambda keys: {k for k in keys if not k.endswith(".one")}
    assert not plural_variant(vi) - en, f"in vi, missing from en: {sorted(plural_variant(vi) - en)}"
    assert not plural_variant(en) - vi, f"in en, missing from vi: {sorted(plural_variant(en) - vi)}"

    # A `.one` must still have the plural it falls back to.
    for catalog, name in ((vi, "vi"), (en, "en")):
        orphans = {k for k in catalog if k.endswith(".one") and k[: -len(".one")] not in catalog}
        assert not orphans, f"in {name}, .one without its plural: {sorted(orphans)}"


@pytest.mark.skipif(not CATALOG.exists(), reason="frontend source not present")
def test_no_vietnamese_is_written_into_a_component() -> None:
    """The catalog is the only place a Vietnamese string belongs.

    Comments are exempt: they are for whoever reads the code, not for whoever
    reads the screen. Nothing else is. Two screens used to choose their words
    with `locale === 'vi' ? ... : ...`, which is correct in both languages and
    still wrong: the next string somebody adds to such a file gets written the
    same way, and a third language means editing every line instead of adding
    a catalog. They were converted so this rule needs no exception.
    """
    offences: list[str] = []
    for path in _sources():
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            stripped = line.strip()
            if stripped.startswith(("//", "*", "/*")):
                continue
            if not VIETNAMESE.search(line):
                continue
            # A transliteration table is code about Vietnamese, not
            # Vietnamese shown to anybody.
            if ".replace(" in line and "/g" in line:
                continue
            offences.append(
                f"  {path.relative_to(FRONTEND).as_posix()}:{number}  "
                f"{stripped[:80]}"
            )

    assert not offences, (
        "Vietnamese no language setting can reach:\n" + "\n".join(offences)
    )


@pytest.mark.skipif(not CATALOG.exists(), reason="frontend source not present")
def test_every_placeholder_has_a_value_to_fill_it() -> None:
    """A hole the envelope cannot fill would reach the reader as `{name}`.

    A translated error interpolates from `details`, the envelope's structured
    account of the failure. So a catalog entry that says `{stream}` needs the
    raise site to put `stream` in `details`, or the sentence arrives with the
    brace in it -- which is worse than the English it replaced, because the
    English at least had the value.

    `translateError` falls back rather than showing a half-built sentence, so
    this cannot reach a user. What it does instead is quietly give Vietnamese
    readers English, which is the bug this whole sweep was about, one entry at
    a time and invisibly. Hence a test.
    """
    import ast as _ast

    backend = pathlib.Path(__file__).resolve().parents[1] / "app"
    catalog = CATALOG.read_text(encoding="utf-8")
    vi_source, rest = catalog.split("const en: Catalog = {", 1)
    en_source = rest.split("\nexport const CATALOGS", 1)[0]

    line = re.compile(
        r"^\s+(['\"])(errorCode\.[A-Za-z0-9_.]+)\1\s*:\s*(['\"])(.*?)\3,\s*$", re.M
    )
    needed: dict[str, set[str]] = {}
    for source in (vi_source, en_source):
        for match in line.finditer(source):
            code = match.group(2).split(".", 1)[1]
            needed.setdefault(code, set()).update(
                re.findall(r"\{(\w+)\}", match.group(4))
            )

    raisers = {
        "ValidationError", "NotFoundError", "UnauthorizedError", "ForbiddenError",
        "ConflictError", "ResourceInUseError", "ResourceModifiedError",
        "QuotaExceededError", "RateLimitedError", "EngineUnavailableError",
        "EngineOperationError", "EngineResourceGoneError", "AppError",
    }
    supplied: dict[str, set[str]] = {}
    for path in sorted(backend.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if not any(name in text for name in raisers):
            continue
        for node in _ast.walk(_ast.parse(text)):
            if not isinstance(node, _ast.Call):
                continue
            name = node.func.id if isinstance(node.func, _ast.Name) else None
            if name not in raisers:
                continue
            code = None
            details: set[str] = set()
            for keyword in node.keywords:
                if keyword.arg == "code" and isinstance(keyword.value, _ast.Constant):
                    code = keyword.value.value
                if keyword.arg == "details" and isinstance(keyword.value, _ast.Dict):
                    details = {
                        key.value for key in keyword.value.keys
                        if isinstance(key, _ast.Constant)
                    }
            if code:
                supplied.setdefault(code, set()).update(details)

    gaps = [
        f"  {code}: {sorted(needed[code] - supplied[code])}"
        for code in sorted(needed)
        if code in supplied and needed[code] - supplied[code]
    ]
    assert not gaps, (
        "catalog entries whose placeholders nothing fills:\n" + "\n".join(gaps)
        + "\n\nAdd the value to `details` at the raise site."
    )
