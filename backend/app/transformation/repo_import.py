"""Read a dbt or Dataform repository and say what it would become here.

Somebody who already has a working Dataform or dbt project in Git has months of
modelling in it, and asking them to retype it is asking them not to come. So
this module converts: it reads the files, works out which of them are models,
rewrites the parts whose syntax differs, and reports honestly on the parts it
cannot carry across.

Nothing here touches the network or the database. It takes a mapping of path to
text and returns a plan -- which makes the conversion testable, and makes the
preview a user reads exactly the thing that will be applied.

The two dialects differ in shape more than in substance:

  dbt        models/x.sql          {{ ref('y') }}       {{ source('s','t') }}
  Dataform   definitions/x.sqlx    ${ref("y")}          ${ref("s","t")}

Dataform additionally puts a JavaScript `config { }` block at the top of every
file, which carries the materialisation, and wraps incremental logic in
`${when(incremental(), ...)}`.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

DBT = "DBT"
DATAFORM = "DATAFORM"

#: Directories whose contents are not models and cannot be represented here.
#: Named individually rather than skipped silently -- a user whose project leans
#: on macros needs to know that before they trust the import.
_DBT_UNSUPPORTED = {
    "macros": "macro",
    "snapshots": "snapshot",
    "seeds": "seed",
    "data": "seed",
    "analyses": "analysis",
    "analysis": "analysis",
}

_LAYER_HINTS = (
    (("staging", "stg", "base", "raw"), "STAGING"),
    (("mart", "marts", "reporting", "report", "presentation", "dm"), "MART"),
    (("intermediate", "core", "dim", "fact", "facts", "dimensions"), "CORE"),
)

_MATERIALIZATIONS = {
    "view": "VIEW",
    "table": "TABLE",
    "incremental": "INCREMENTAL",
    # Dataform's own vocabulary.
    "inline": "VIEW",
    "operations": "TABLE",
}

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class ImportedSource:
    """A table the project reads but does not create."""

    #: The name the repository's own `source()` / `ref()` calls use.
    alias: str
    table: str
    catalog: str | None
    schema: str
    relation: str
    #: True when the SQL names this table literally rather than through a
    #: `ref()` or `source()` call, so rewriting has to match the name itself.
    direct: bool = False


@dataclass
class ImportedTest:
    model: str
    rule: str
    column: str | None = None
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class ImportedModel:
    name: str
    path: str
    layer: str
    materialization: str
    sql: str
    description: str | None = None


@dataclass
class ImportPlan:
    kind: str
    project_name: str | None = None
    models: list[ImportedModel] = field(default_factory=list)
    sources: list[ImportedSource] = field(default_factory=list)
    tests: list[ImportedTest] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "project_name": self.project_name,
            "models": [
                {
                    "name": item.name, "path": item.path, "layer": item.layer,
                    "materialization": item.materialization, "sql": item.sql,
                    "description": item.description,
                }
                for item in self.models
            ],
            "sources": [
                {
                    "alias": item.alias, "table": item.table, "catalog": item.catalog,
                    "schema_name": item.schema, "relation": item.relation,
                    "direct": item.direct,
                }
                for item in self.sources
            ],
            "tests": [
                {
                    "model": item.model, "rule": item.rule,
                    "column": item.column, "config": item.config,
                }
                for item in self.tests
            ],
            "warnings": self.warnings,
        }


def detect(files: dict[str, str]) -> tuple[str, str]:
    """Which tool wrote this repository, and from which directory.

    Returns the kind and the prefix every other path is relative to, because a
    repository often holds the project in a subdirectory next to a README.
    """
    for path in sorted(files, key=lambda item: item.count("/")):
        base = posixpath.basename(path)
        prefix = posixpath.dirname(path)
        if base == "dbt_project.yml":
            return DBT, prefix
        if base in ("workflow_settings.yaml", "dataform.json"):
            return DATAFORM, prefix
    raise ValueError(
        "Không tìm thấy dbt_project.yml hay workflow_settings.yaml trong repository này."
    )


def _relative(files: dict[str, str], prefix: str) -> dict[str, str]:
    if not prefix:
        return files
    head = prefix + "/"
    return {
        path[len(head):]: text for path, text in files.items() if path.startswith(head)
    }


def _layer_for(path: str, name: str) -> str:
    """Which tier a model belongs to, read off the path the author chose.

    Directory layout is the only statement of intent most projects make, and it
    is a reliable one: nobody puts a mart in `models/staging`.
    """
    segments = [segment.lower() for segment in path.split("/")[:-1]]
    for hints, layer in _LAYER_HINTS:
        if any(segment in hints for segment in segments):
            return layer
    lowered = name.lower()
    if lowered.startswith(("stg_", "base_", "src_")):
        return "STAGING"
    if lowered.startswith(("mart_", "rpt_", "dm_")):
        return "MART"
    return "CORE"


def _safe_name(raw: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]", "_", raw).strip("_").lower()
    if not name:
        return "model"
    if not name[0].isalpha() and name[0] != "_":
        name = f"m_{name}"
    return name[:200]


def _load_yaml(text: str) -> Any:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return None


# --------------------------------------------------------------------------
# dbt
# --------------------------------------------------------------------------

_DBT_CONFIG = re.compile(r"\{\{\s*config\s*\((.*?)\)\s*\}\}", re.S)
_DBT_MATERIALIZED = re.compile(r"materialized\s*=\s*['\"]([a-z_]+)['\"]")


def _dbt_materialization(sql: str, defaults: dict[str, str], path: str) -> str:
    """Inline config wins over the project default, as it does in dbt."""
    match = _DBT_CONFIG.search(sql)
    if match:
        inner = _DBT_MATERIALIZED.search(match.group(1))
        if inner:
            return _MATERIALIZATIONS.get(inner.group(1), "VIEW")
    # `defaults` is keyed relative to models/, the way dbt_project.yml nests it.
    segments = path.removeprefix("models/").split("/")
    for depth in range(len(segments) - 1, 0, -1):
        key = "/".join(segments[:depth])
        if key in defaults:
            return _MATERIALIZATIONS.get(defaults[key], "VIEW")
    return _MATERIALIZATIONS.get(defaults.get("", "view"), "VIEW")


def _dbt_defaults(project: dict[str, Any] | None) -> dict[str, str]:
    """Flatten `models:` in dbt_project.yml into path prefix -> materialisation."""
    out: dict[str, str] = {}
    if not isinstance(project, dict):
        return out

    def walk(node: Any, path: list[str]) -> None:
        if not isinstance(node, dict):
            return
        materialized = node.get("+materialized") or node.get("materialized")
        if isinstance(materialized, str):
            out["/".join(path)] = materialized
        for key, value in node.items():
            if key.startswith("+") or not isinstance(value, dict):
                continue
            walk(value, [*path, key])

    models = project.get("models")
    if isinstance(models, dict):
        # The first level is the package name, which is not a directory.
        for value in models.values():
            walk(value, ["models"])
        walk({k: v for k, v in models.items() if k.startswith("+")}, ["models"])
    # Keys are stored with the leading `models` segment stripped for matching.
    return {key.removeprefix("models/").removeprefix("models"): value
            for key, value in out.items()}


_DBT_TEST_RULES = {
    "unique": "UNIQUE",
    "not_null": "NOT_NULL",
    "accepted_values": "ACCEPTED_VALUES",
    "relationships": "RELATIONSHIPS",
}


def _dbt_tests(node: Any, model_name: str) -> list[ImportedTest]:
    out: list[ImportedTest] = []
    for column in (node.get("columns") or []):
        if not isinstance(column, dict):
            continue
        column_name = column.get("name")
        # dbt 1.8 renamed `tests` to `data_tests`; both are still written.
        for test in (column.get("tests") or column.get("data_tests") or []):
            if isinstance(test, str):
                rule = _DBT_TEST_RULES.get(test)
                if rule:
                    out.append(ImportedTest(model=model_name, rule=rule, column=column_name))
            elif isinstance(test, dict):
                for key, body in test.items():
                    rule = _DBT_TEST_RULES.get(key)
                    if not rule:
                        continue
                    config: dict[str, Any] = {}
                    if isinstance(body, dict) and rule == "ACCEPTED_VALUES":
                        values = body.get("values")
                        if isinstance(values, list):
                            config["values"] = [str(value) for value in values]
                    out.append(ImportedTest(
                        model=model_name, rule=rule, column=column_name, config=config,
                    ))
    return out


def _dbt_sources(document: Any) -> list[ImportedSource]:
    out: list[ImportedSource] = []
    for source in (document.get("sources") or []):
        if not isinstance(source, dict):
            continue
        alias = source.get("name")
        if not alias:
            continue
        schema = source.get("schema") or alias
        catalog = source.get("database") or source.get("project")
        for table in (source.get("tables") or []):
            if not isinstance(table, dict) or not table.get("name"):
                continue
            out.append(ImportedSource(
                alias=str(alias), table=str(table["name"]),
                catalog=str(catalog) if catalog else None,
                schema=str(schema), relation=str(table.get("identifier") or table["name"]),
            ))
    return out


def _plan_dbt(files: dict[str, str]) -> ImportPlan:
    project = _load_yaml(files.get("dbt_project.yml", "")) or {}
    plan = ImportPlan(
        kind=DBT,
        project_name=str(project.get("name")) if project.get("name") else None,
    )
    defaults = _dbt_defaults(project)

    descriptions: dict[str, str] = {}
    for path, text in sorted(files.items()):
        if not path.endswith((".yml", ".yaml")) or path == "dbt_project.yml":
            continue
        document = _load_yaml(text)
        if not isinstance(document, dict):
            continue
        plan.sources.extend(_dbt_sources(document))
        for node in (document.get("models") or []):
            if not isinstance(node, dict) or not node.get("name"):
                continue
            name = str(node["name"])
            if node.get("description"):
                descriptions[name] = str(node["description"])
            plan.tests.extend(_dbt_tests(node, name))

    seen: set[str] = set()
    for path, text in sorted(files.items()):
        top = path.split("/")[0]
        if top in _DBT_UNSUPPORTED:
            continue
        if not path.startswith("models/"):
            continue
        if path.endswith(".py"):
            plan.warnings.append(
                f"{path}: model viết bằng Python, chưa hỗ trợ — hãy chuyển sang SQL."
            )
            continue
        if not path.endswith(".sql"):
            continue
        raw_name = posixpath.splitext(posixpath.basename(path))[0]
        name = _safe_name(raw_name)
        if name in seen:
            plan.warnings.append(f"{path}: trùng tên model `{name}`, đã bỏ qua.")
            continue
        seen.add(name)
        plan.models.append(ImportedModel(
            name=name, path=path, layer=_layer_for(path, name),
            materialization=_dbt_materialization(text, defaults, path),
            sql=text.strip(), description=descriptions.get(raw_name),
        ))

    for directory, label in _DBT_UNSUPPORTED.items():
        count = sum(1 for path in files if path.startswith(f"{directory}/")
                    and path.endswith((".sql", ".csv", ".yml")))
        if count:
            plan.warnings.append(
                f"{count} {label} trong `{directory}/` không được import; "
                "model nào phụ thuộc vào chúng sẽ cần sửa tay."
            )
    return plan


# --------------------------------------------------------------------------
# Dataform
# --------------------------------------------------------------------------

_SQLX_BLOCK = re.compile(
    r"^\s*(config|js|pre_operations|post_operations)\s*\{", re.M,
)
_DF_REF_TWO = re.compile(r"\$\{\s*ref\(\s*[\"']([^\"']+)[\"']\s*,\s*[\"']([^\"']+)[\"']\s*\)\s*\}")
_DF_REF_ONE = re.compile(r"\$\{\s*ref\(\s*[\"']([^\"']+)[\"']\s*\)\s*\}")
_DF_RESOLVE = re.compile(r"\$\{\s*resolve\(\s*[\"']([^\"']+)[\"']\s*\)\s*\}")
_DF_SELF = re.compile(r"\$\{\s*self\(\s*\)\s*\}")
_DF_WHEN = re.compile(
    r"\$\{\s*when\s*\(\s*incremental\(\)\s*,\s*(?P<q>[`\"'])(?P<body>.*?)(?P=q)\s*\)\s*\}",
    re.S,
)
_DF_JS_LEFTOVER = re.compile(r"\$\{[^}]*\}")

# Brackets deliberately excluded from the bare-word value: letting `[` be part
# of a value made `uniqueKey: ["id"]` consume the opening bracket, which threw
# the depth counter off by one and let keys nested inside `columns { }` be read
# as the dataset's own.
_DF_KEY = re.compile(r"""(\w+)\s*:\s*(?:"([^"]*)"|'([^']*)'|([A-Za-z0-9_.-]+))""")


def _split_blocks(text: str) -> tuple[dict[str, str], str]:
    """Peel Dataform's `config { }`, `js { }` and operation blocks off the SQL.

    Brace-counted rather than regex-matched to the closing brace: a config block
    holds nested objects and a JS block holds arbitrary code, and a non-greedy
    match ends at the first inner `}`.
    """
    blocks: dict[str, str] = {}
    remaining = text
    while True:
        match = _SQLX_BLOCK.search(remaining)
        if not match:
            break
        name = match.group(1)
        start = match.end() - 1
        depth = 0
        end = None
        for index in range(start, len(remaining)):
            character = remaining[index]
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    end = index
                    break
        if end is None:
            break
        blocks.setdefault(name, remaining[start + 1:end])
        remaining = remaining[:match.start()] + remaining[end + 1:]
    return blocks, remaining.strip()


def _parse_config(block: str) -> dict[str, str]:
    """Read the flat keys we care about out of a JavaScript object literal.

    Not a JS parser and not trying to be. The keys that decide how a dataset is
    built -- type, schema, name, description -- are string or bare-word scalars
    at the top level in every project, and anything more elaborate is reported
    as a warning rather than half-understood. Nested objects and quoted text are
    skipped so a `name:` inside `columns: { ... }` cannot be mistaken for the
    dataset's own name.
    """
    out: dict[str, str] = {}
    depth = 0
    index = 0
    length = len(block)
    while index < length:
        character = block[index]
        if character in "\"'`":
            quote = character
            index += 1
            while index < length and block[index] != quote:
                index += 2 if block[index] == "\\" else 1
            index += 1
            continue
        if character in "{[":
            depth += 1
        elif character in "}]":
            depth -= 1
        elif depth == 0:
            match = _DF_KEY.match(block, index)
            if match:
                value = next(
                    (group for group in match.groups()[1:] if group is not None), "",
                )
                out.setdefault(match.group(1), value)
                index = match.end()
                continue
        index += 1
    return out


def _convert_sqlx(sql: str, declarations: dict[str, ImportedSource]) -> tuple[str, list[str]]:
    """Rewrite Dataform's template calls into dbt Jinja."""
    notes: list[str] = []

    def two(match: re.Match[str]) -> str:
        return f"{{{{ source('{match.group(1)}', '{match.group(2)}') }}}}"

    def one(match: re.Match[str]) -> str:
        name = match.group(1)
        declared = declarations.get(name)
        if declared is not None:
            return f"{{{{ source('{declared.alias}', '{declared.table}') }}}}"
        return f"{{{{ ref('{_safe_name(name)}') }}}}"

    converted = _DF_REF_TWO.sub(two, sql)
    converted = _DF_REF_ONE.sub(one, converted)
    converted = _DF_RESOLVE.sub(one, converted)
    converted = _DF_SELF.sub("{{ this }}", converted)
    converted = _DF_WHEN.sub(
        lambda match: "{% if is_incremental() %}"
                      f"{match.group('body')}"
                      "{% endif %}",
        converted,
    )
    leftover = _DF_JS_LEFTOVER.findall(converted)
    if leftover:
        notes.append(
            "Còn biểu thức JavaScript chưa chuyển được: "
            + ", ".join(sorted({item[:60] for item in leftover})[:3])
        )
    return converted.strip(), notes


def _plan_dataform(files: dict[str, str]) -> ImportPlan:
    settings = (
        _load_yaml(files.get("workflow_settings.yaml", ""))
        or _load_yaml(files.get("dataform.json", ""))
        or {}
    )
    plan = ImportPlan(
        kind=DATAFORM,
        project_name=str(settings.get("defaultDataset") or settings.get("defaultSchema") or "")
        or None,
    )
    default_catalog = settings.get("defaultProject") or settings.get("defaultDatabase")
    default_schema = settings.get("defaultDataset") or settings.get("defaultSchema")

    # Declarations first: a model referencing one must resolve to a source, and
    # files are not read in dependency order.
    declarations: dict[str, ImportedSource] = {}
    parsed: list[tuple[str, dict[str, str], str]] = []
    for path, text in sorted(files.items()):
        if not path.startswith("definitions/") or not path.endswith((".sqlx", ".sql")):
            continue
        blocks, body = _split_blocks(text)
        config = _parse_config(blocks.get("config", ""))
        parsed.append((path, config, body))
        if config.get("type") != "declaration":
            continue
        name = config.get("name") or posixpath.splitext(posixpath.basename(path))[0]
        schema = config.get("schema") or default_schema
        if not schema:
            plan.warnings.append(f"{path}: declaration thiếu schema, đã bỏ qua.")
            continue
        declarations[name] = ImportedSource(
            alias=str(schema), table=str(name),
            catalog=str(config.get("database") or default_catalog or "") or None,
            schema=str(schema), relation=str(name),
        )
    plan.sources.extend(declarations.values())

    seen: set[str] = set()
    for path, config, body in parsed:
        kind = config.get("type", "table")
        if kind == "declaration":
            continue
        if kind == "assertion":
            plan.warnings.append(
                f"{path}: assertion của Dataform chưa chuyển tự động được; "
                "hãy dựng lại bằng mục Kiểm tra."
            )
            continue
        if kind == "operations":
            plan.warnings.append(
                f"{path}: khối `operations` chạy SQL tuỳ ý, không phải model — chưa import."
            )
            continue
        raw_name = config.get("name") or posixpath.splitext(posixpath.basename(path))[0]
        name = _safe_name(raw_name)
        if name in seen:
            plan.warnings.append(f"{path}: trùng tên model `{name}`, đã bỏ qua.")
            continue
        seen.add(name)
        sql, notes = _convert_sqlx(body, declarations)
        for note in notes:
            plan.warnings.append(f"{path}: {note}")
        plan.models.append(ImportedModel(
            name=name, path=path, layer=_layer_for(path, name),
            materialization=_MATERIALIZATIONS.get(kind, "TABLE"),
            sql=sql, description=config.get("description") or None,
        ))

    for path, text in sorted(files.items()):
        if path.startswith("includes/") and path.endswith(".js"):
            plan.warnings.append(
                f"{path}: hàm JavaScript dùng chung không chuyển được sang dbt Jinja."
            )
    for path, _config, _body in parsed:
        blocks, _ = _split_blocks(files[path])
        for block in ("js", "pre_operations", "post_operations"):
            if block in blocks:
                plan.warnings.append(f"{path}: khối `{block}` đã bị bỏ khi chuyển đổi.")
    return plan


_REF_CALL = re.compile(r"\{\{\s*ref\(\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}")
_SOURCE_CALL = re.compile(
    r"\{\{\s*source\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}"
)


_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)

#: A table named in the SQL itself: `proj.dataset.table` in backticks, or a
#: bare dotted name after FROM/JOIN. Requires at least one dot, which is what
#: keeps a CTE -- always a single identifier -- from being mistaken for a table.
_BACKTICKED = re.compile(r"`([A-Za-z0-9_\-]+(?:\.[A-Za-z0-9_\-]+)+)`")
_BARE_TABLE = re.compile(
    r"\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)",
    re.I,
)


def strip_comments(sql: str) -> str:
    """SQL with comments removed, so a commented-out table is not a dependency."""
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", sql))


def direct_tables(sql: str) -> list[tuple[str | None, str, str]]:
    """Qualified tables the SQL names outright, as (catalog, schema, relation).

    Dataform lets a project skip declarations entirely and write
    `` `project.dataset.table` `` inline, and plenty of real projects do. Left
    alone those import as SQL that may well run while the Transform shows no
    inputs at all -- no lineage, no freshness, nothing to say where the data
    came from. Finding them is what lets them become real sources.
    """
    body = strip_comments(sql)
    found: list[tuple[str | None, str, str]] = []
    seen: set[tuple[str | None, str, str]] = set()
    for match in list(_BACKTICKED.finditer(body)) + list(_BARE_TABLE.finditer(body)):
        parts = match.group(1).split(".")
        if len(parts) == 2:
            entry = (None, parts[0], parts[1])
        elif len(parts) == 3:
            entry = (parts[0], parts[1], parts[2])
        else:
            continue
        if entry not in seen:
            seen.add(entry)
            found.append(entry)
    return found


def _collect_direct(plan: ImportPlan) -> None:
    """Turn literal table names into sources the import can actually resolve."""
    declared = {
        (item.catalog, item.schema, item.relation) for item in plan.sources
    }
    produced = {item.name for item in plan.models}
    for model in plan.models:
        for catalog, schema, relation in direct_tables(model.sql):
            # A model writing to its own output schema is not reading a source.
            if _safe_name(relation) in produced:
                continue
            key = (catalog, schema, relation)
            if key in declared:
                continue
            declared.add(key)
            plan.sources.append(ImportedSource(
                alias=schema, table=relation, catalog=catalog,
                schema=schema, relation=relation, direct=True,
            ))


def rewrite_direct_tables(
    sql: str, mapping: dict[tuple[str | None, str, str], tuple[str, str]],
) -> str:
    """Replace literal table names with `source()` calls, comments untouched.

    Only names that resolved to a registered asset are replaced. One that did
    not is left exactly as written, so the model still says what it always said
    and the warning explains why it has no source behind it.
    """
    def qualified(match: re.Match[str]) -> str:
        raw = match.group(1) if match.lastindex else match.group(0)
        parts = raw.split(".")
        key = (
            (None, parts[0], parts[1]) if len(parts) == 2
            else (parts[0], parts[1], parts[2]) if len(parts) == 3
            else None
        )
        target = mapping.get(key) if key else None
        if target is None:
            return match.group(0)
        return f"{{{{ source('{target[0]}', '{target[1]}') }}}}"

    # Backticks first: the bare pattern would otherwise match inside them.
    out = _BACKTICKED.sub(qualified, sql)

    def bare(match: re.Match[str]) -> str:
        replaced = qualified(match)
        if replaced == match.group(0):
            return match.group(0)
        keyword = match.group(0)[:match.start(1) - match.start(0)]
        return keyword + replaced

    return _BARE_TABLE.sub(bare, out)


def _check_references(plan: ImportPlan) -> None:
    """Name every reference that will not resolve once imported.

    A model can only be built from things that came across, and the usual thing
    that does not is a seed: `jaffle_shop` reads `{{ ref('raw_customers') }}`
    from a CSV, which has no equivalent here. Importing that quietly produces a
    Transform whose first run fails on a reference the user never chose, so the
    check happens while they are still deciding.
    """
    known = {item.name for item in plan.models}
    declared = {(item.alias, item.table) for item in plan.sources}
    missing_refs: dict[str, list[str]] = {}
    missing_sources: dict[str, list[str]] = {}
    for model in plan.models:
        for name in _REF_CALL.findall(model.sql):
            if _safe_name(name) not in known:
                missing_refs.setdefault(name, []).append(model.name)
        for alias, table in _SOURCE_CALL.findall(model.sql):
            if (alias, table) not in declared:
                missing_sources.setdefault(f"{alias}.{table}", []).append(model.name)
    for name, users in sorted(missing_refs.items()):
        plan.warnings.append(
            f"`{name}` được tham chiếu bởi {', '.join(sorted(users))} nhưng không có "
            "trong repository — thường là seed (CSV) hoặc snapshot. Hãy nạp bảng đó "
            "vào kho dữ liệu rồi thêm làm nguồn."
        )
    for name, users in sorted(missing_sources.items()):
        plan.warnings.append(
            f"Nguồn `{name}` được {', '.join(sorted(users))} dùng nhưng không được "
            "khai báo trong repository, nên chưa nối được tới bảng thật."
        )


def build_plan(files: dict[str, str]) -> ImportPlan:
    """Read a repository and describe the Transform it would become."""
    kind, prefix = detect(files)
    scoped = _relative(files, prefix)
    plan = _plan_dbt(scoped) if kind == DBT else _plan_dataform(scoped)
    if not plan.models:
        plan.warnings.append("Không tìm thấy model nào để import.")
    _collect_direct(plan)
    _check_references(plan)
    direct = [item for item in plan.sources if item.direct]
    if direct:
        plan.warnings.append(
            f"{len(direct)} bảng được viết thẳng tên trong SQL thay vì khai báo nguồn. "
            "Hệ thống sẽ đăng ký chúng làm nguồn và thay bằng tham chiếu, để Transform "
            "có đủ sơ đồ phụ thuộc; bảng nào không đọc được sẽ được giữ nguyên và báo lại."
        )
    return plan


def rewrite_sources(sql: str, mapping: dict[tuple[str, str], tuple[str, str]]) -> str:
    """Point `source()` calls at the source this Transform actually generated.

    Two things differ, not one. The repository names its sources whatever its
    author chose while here a source is named after the schema it lives in; and
    a dbt source table can carry an `identifier`, so `source('raw','orders')`
    may physically be `orders_v2`, which is the name the generated project uses.
    Rewriting both is what makes an imported model compile on arrival rather
    than after an hour of find-and-replace.
    """
    pattern = re.compile(
        r"\{\{\s*source\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}"
    )

    def replace(match: re.Match[str]) -> str:
        target = mapping.get((match.group(1), match.group(2)))
        if target is None:
            return match.group(0)
        return f"{{{{ source('{target[0]}', '{target[1]}') }}}}"

    return pattern.sub(replace, sql)
