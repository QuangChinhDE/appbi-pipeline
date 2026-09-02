"""Readers for dbt's own artifacts.

These modules parse `manifest.json`, `run_results.json`, `sources.json` and
`catalog.json`.  They are the only place in the product allowed to answer
"what resources exist", "what depends on what" and "what happened in this run",
because those questions already have answers and dbt wrote them down.

Every reader dispatches on `metadata.dbt_schema_version` rather than assuming
one shape forever, so a dbt upgrade surfaces as an explicit unsupported-version
error instead of a KeyError halfway through an index rebuild.
"""
