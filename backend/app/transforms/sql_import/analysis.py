"""Read uploaded SQL without rewriting a line of it.

The whole point of this module is what it refuses to do. A person uploading a
query is uploading the thing they trust; a model asked to "convert it to dbt"
will helpfully reformat it, and somewhere in that reformatting a LEFT JOIN
becomes a JOIN and nobody notices until a number is wrong in a report. So the
conversion is split, and this half is the mechanical one:

    here                what a parser can prove -- which statements a file
                        holds, what each one creates, which tables it reads,
                        and where those names sit in the text
    the model           what a parser cannot know -- whether a query belongs
                        in staging or marts, what to call it, which column
                        looks like a key worth testing

Nothing here asks a model anything, and nothing here reformats. The rewrite is
a character-range substitution over the original text, so every byte that is
not a table name survives exactly as it was typed, comments and all. A
parse-and-regenerate round trip would be easier and is the obvious thing to
reach for; it also silently drops dialect syntax the parser models imperfectly,
which for BigQuery is a long list.

The parse is still done, for two things a substitution cannot work out on its
own: what a statement creates, and which names are CTEs rather than tables.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.tokens import Token, TokenType

#: dbt adapter name -> the dialect sqlglot knows it by.
DIALECTS = {"bigquery": "bigquery", "postgres": "postgres"}

#: A table name follows one of these. `INTO` and `UPDATE` are here so that an
#: INSERT or an UPDATE is recognised and reported rather than half-converted.
_TABLE_INTRODUCERS = {
    TokenType.FROM, TokenType.JOIN, TokenType.INTO, TokenType.UPDATE,
}

#: Parts of a name: `a.b.c`, and the quoted forms the dialects use.
_NAME_PARTS = {TokenType.VAR, TokenType.IDENTIFIER, TokenType.STRING}

#: A statement whose body AppBI can turn into a model.
SELECTABLE = {"SELECT", "CREATE_TABLE", "CREATE_VIEW"}


@dataclass(frozen=True)
class TableRef:
    """A table name, and exactly where it sits in the file."""

    #: The name as written, backticks, quotes and all.
    text: str
    #: The name split into parts, unquoted and case-folded for matching.
    parts: tuple[str, ...]
    #: Character offsets into the file, `end` exclusive.
    start: int
    end: int

    @property
    def name(self) -> str:
        return self.parts[-1] if self.parts else ""


@dataclass
class Statement:
    """One statement out of an uploaded file."""

    index: int
    #: SELECT | CREATE_TABLE | CREATE_VIEW | UNSUPPORTED
    kind: str
    #: What the statement creates, if it creates anything.
    creates: tuple[str, ...] | None
    #: Offsets of the part that becomes a model body -- the SELECT, with the
    #: CREATE ... AS prefix left behind. `end` exclusive.
    body_start: int
    body_end: int
    refs: tuple[TableRef, ...] = ()
    #: Why this statement cannot become a model, when it cannot.
    problem: str | None = None

    @property
    def usable(self) -> bool:
        return self.problem is None and self.kind in SELECTABLE


@dataclass
class ParsedFile:
    """An uploaded file, read."""

    name: str
    text: str
    statements: list[Statement] = field(default_factory=list)
    #: Set when the file could not be parsed at all.
    problem: str | None = None

    @property
    def usable(self) -> list[Statement]:
        return [item for item in self.statements if item.usable]


def _parts(node: exp.Expression | None) -> tuple[str, ...]:
    """The dotted parts of a table-ish node, unquoted and folded."""
    if node is None:
        return ()
    if isinstance(node, exp.Schema):
        node = node.this
    if not isinstance(node, exp.Table):
        return ()
    out = [
        part.name.strip().casefold()
        for part in (node.args.get("catalog"), node.args.get("db"), node.this)
        if part is not None and part.name
    ]
    return tuple(out)


def _split_name(text: str) -> tuple[str, ...]:
    """`\\`proj.raw.orders\\`` and `raw.orders` both give their parts."""
    cleaned = text.strip()
    for quote in ('`', '"', "'", "[", "]"):
        cleaned = cleaned.replace(quote, "")
    return tuple(part.strip().casefold() for part in cleaned.split(".") if part.strip())


def _cte_names(statement: exp.Expression) -> set[str]:
    """Names bound by WITH, which are not tables however much they look it."""
    return {
        cte.alias.strip().casefold()
        for cte in statement.find_all(exp.CTE)
        if cte.alias
    }


def _statement_tokens(tokens: list[Token], start: int, end: int) -> list[Token]:
    return [token for token in tokens if start <= token.start < end]


def _name_run(tokens: list[Token], index: int) -> tuple[int, str, int, int] | None:
    """Read a dotted name starting at `index`.

    Returns the index after it, the text, and its character span. `None` when
    what follows is not a name -- a subquery, a function call, a keyword.
    """
    if index >= len(tokens) or tokens[index].token_type not in _NAME_PARTS:
        return None
    first = tokens[index]
    last = first
    position = index + 1
    while (
        position + 1 < len(tokens)
        and tokens[position].token_type == TokenType.DOT
        and tokens[position + 1].token_type in _NAME_PARTS
    ):
        last = tokens[position + 1]
        position += 2
    return position, "", first.start, last.end + 1


def _find_refs(text: str, tokens: list[Token], ctes: set[str]) -> list[TableRef]:
    """Every table name in the statement, with its span in the file."""
    refs: list[TableRef] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.token_type not in _TABLE_INTRODUCERS:
            index += 1
            continue
        run = _name_run(tokens, index + 1)
        if run is None:
            # A subquery, a lateral, a function -- not a name to rewrite.
            index += 1
            continue
        after, _unused, start, end = run
        written = text[start:end]
        parts = _split_name(written)
        # A bare name bound by WITH is a CTE, not a table. A qualified one is a
        # table that happens to share a CTE's name, and is.
        if len(parts) == 1 and parts[0] in ctes:
            index = after
            continue
        refs.append(TableRef(text=written, parts=parts, start=start, end=end))
        index = after
    return refs


def _body_span(
    text: str, tokens: list[Token], statement: exp.Expression, start: int, end: int,
) -> tuple[int, int, str | None]:
    """Where the model body begins: the SELECT, not the CREATE around it.

    `CREATE TABLE x AS SELECT ...` becomes `SELECT ...`. dbt writes the CREATE
    itself, from the materialization -- leaving one in a model file means dbt
    creates a table that creates a table.
    """
    if not isinstance(statement, exp.Create):
        return start, end, None
    depth = 0
    for token in tokens:
        if token.token_type == TokenType.L_PAREN:
            depth += 1
        elif token.token_type == TokenType.R_PAREN:
            depth -= 1
        elif depth == 0 and token.token_type in (TokenType.SELECT, TokenType.WITH):
            return token.start, end, None
    return start, end, "It creates something without a query behind it."


def _kind(statement: exp.Expression) -> tuple[str, tuple[str, ...] | None, str | None]:
    if isinstance(statement, exp.Select) or isinstance(statement, exp.Union):
        return "SELECT", None, None
    if isinstance(statement, exp.Create):
        target = _parts(statement.this)
        kind = (statement.args.get("kind") or "TABLE").upper()
        if kind in ("TABLE", "VIEW", "MATERIALIZED VIEW"):
            return (
                "CREATE_VIEW" if "VIEW" in kind else "CREATE_TABLE",
                target or None,
                None,
            )
        return "UNSUPPORTED", target or None, f"AppBI does not convert CREATE {kind}."
    if isinstance(statement, exp.Insert):
        return "UNSUPPORTED", _parts(statement.this), (
            "INSERT adds rows to a table somebody else owns. A dbt model owns "
            "what it writes, so this one needs a decision a converter cannot "
            "make for you."
        )
    name = type(statement).__name__.upper()
    return "UNSUPPORTED", None, f"AppBI does not convert a {name} statement."


def parse_file(name: str, text: str, *, dialect: str) -> ParsedFile:
    """Read one uploaded file."""
    read = DIALECTS.get(dialect, None)
    parsed = ParsedFile(name=name, text=text)
    if not text.strip():
        parsed.problem = "The file is empty."
        return parsed
    try:
        statements = [item for item in sqlglot.parse(text, read=read) if item is not None]
        tokens = sqlglot.tokenize(text, read=read)
    except Exception as exc:  # sqlglot raises several distinct types
        parsed.problem = f"This file is not SQL this parser can read: {exc}"[:300]
        return parsed

    if not statements:
        parsed.problem = "The file holds no statements."
        return parsed

    # sqlglot does not hand back a statement's span, so the boundaries come
    # from the semicolons between them.
    bounds = _statement_bounds(tokens, len(text))
    for index, statement in enumerate(statements):
        start, end = bounds[index] if index < len(bounds) else (0, len(text))
        kind, creates, problem = _kind(statement)
        own = _statement_tokens(tokens, start, end)
        body_start, body_end, body_problem = _body_span(text, own, statement, start, end)
        refs = _find_refs(text, _statement_tokens(tokens, body_start, body_end),
                          _cte_names(statement))
        parsed.statements.append(Statement(
            index=index, kind=kind, creates=creates,
            body_start=body_start, body_end=body_end,
            refs=tuple(refs), problem=problem or body_problem,
        ))
    return parsed


def _statement_bounds(tokens: list[Token], length: int) -> list[tuple[int, int]]:
    """Character spans between semicolons, `end` exclusive."""
    bounds: list[tuple[int, int]] = []
    start = 0
    for token in tokens:
        if token.token_type == TokenType.SEMICOLON:
            bounds.append((start, token.start))
            start = token.end + 1
    if start < length:
        bounds.append((start, length))
    return bounds or [(0, length)]


def rewrite(
    file_text: str, statement: Statement, replacements: dict[int, str],
) -> str:
    """The statement's body with its table names replaced.

    `replacements` maps a ref's `start` to the text that takes its place. Every
    other character comes through untouched, which is the promise this module
    exists to keep.
    """
    body = []
    cursor = statement.body_start
    for ref in sorted(statement.refs, key=lambda item: item.start):
        if ref.start not in replacements:
            continue
        body.append(file_text[cursor:ref.start])
        body.append(replacements[ref.start])
        cursor = ref.end
    body.append(file_text[cursor:statement.body_end])
    return "".join(body).strip()
