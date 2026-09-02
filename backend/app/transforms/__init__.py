"""Transform V2 — a dbt-native development environment.

The rule this package exists to enforce:

    dbt project files are the source of truth for Transform content.

AppBI owns project metadata, environments, connections, invocations, releases
and Git bindings.  dbt owns project semantics: what a resource is, what depends
on what, how it compiles, how it runs.  Everything this package reports about
resources, lineage, status and columns is read out of dbt's own artifacts, never
re-derived from a regex or a product table.

The corollary matters as much as the rule: a config AppBI has no form for is
still preserved byte-for-byte, because the file is canonical and AppBI never
rewrites a file it did not have to touch.
"""
