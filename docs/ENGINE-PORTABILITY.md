# Can this run on something other than Airbyte?

## Why this document exists

The architecture claims `IntegrationEngineAdapter` abstracts the execution
engine. Until recently that claim rested on two implementations that were both
Airbyte — same protocol, same catalog shape, same job model, same vocabulary.
An interface with one family behind it has not been tested as an interface; it
has been tested as a place to put Airbyte code.

So a third adapter was written that is not Airbyte in any respect, and this
records what broke.

## What was built

`backend/app/adapters/sql_direct/` — a Postgres-to-Postgres engine. No
connector images, no Airbyte Protocol, no `spec`/`check`/`discover`/`read`, no
server-side connection object, no job service. Rows move over a database
connection, in-process, with SQL.

Narrow on purpose: two connectors, full refresh and incremental on a cursor
column. The point is coverage of the *interface*, not of a connector ecosystem.
It is a reference implementation, not a product feature.

Select it with `ENGINE_TYPE=SQL_DIRECT`.

## Result

**The interface did not have to change.** `SqlDirectAdapter` satisfies the
`IntegrationEngineAdapter` Protocol — all 22 operations — with no new method,
no changed signature, and no Airbyte-shaped argument it had to fake.

Run for real against the demo databases:

```text
health      : HEALTHY sql-direct/1
check source: True          check dest: True
discover    : 3 streams  customers(6f,pk=[['id']])  orders(7f,pk=[['id']])
                         products(5f,pk=[['sku']])
sync 1      : SUCCEEDED  2007 records, 172,926 bytes
sync 2      : SUCCEEDED  0 records          <- incremental state held
```

The same primary keys Airbyte's `source-postgres` reports, from a completely
different discovery implementation.

Three things above the adapter *did* have to change, and each was a genuine
Airbyte leak that the exercise found:

| Leak | Where | Fix |
|---|---|---|
| A service imported the Airbyte protocol module | `services/schema_service.py` imported `adapters.airbyte_protocol.protocol` for `stream_schema_hash` | The function hashes a JSON schema and knows nothing about any engine. Moved to `DiscoveredStream.schema_hash` on the DTO. No layer outside `adapters/` imports an engine module now. |
| Secret detection assumed Airbyte's spec dialect | `services/catalog.py` treated only `airbyte_secret` as marking a credential | Recognises `airbyte_secret`, `writeOnly`, `secret` and `format: password`. A non-Airbyte spec that marks passwords the standard way now gets them encrypted instead of stored in plain configuration. |
| The product named an Airbyte image | `services/builder.py` pinned `airbyte/source-declarative-manifest` when publishing a built connector | The adapter declares its runner via `declarative_runner()`. An engine with no declarative runtime returns `None` and publishing fails with that reason. |

That third one is the interesting case: the fix is not an abstraction. The
Connector Builder compiles to the Airbyte low-code CDK, and there is no neutral
format to compile to. `sql_direct` returns `None` and the Builder is
unavailable on it. **The Builder is an Airbyte-CDK feature, not a portable
one** — saying so is more useful than a pretend abstraction that fails later.

## Where the interface pinched

Four operations assume the engine provides something a non-Airbyte engine does
not have. None required an interface change, but each needed the adapter to
synthesise an answer, and that is worth knowing before the next engine.

**Connector specs.** `get_connector_spec` assumes the engine can be asked what
configuration a connector takes; Airbyte answers from the connector image.
There is no image here, so the specs are declared in the adapter. Fine for an
engine with two connectors, awkward for one with hundreds — that engine would
need its own registry, which is what Airbyte's is.

**`check`.** Airbyte connectors implement a check operation. A database does
not. The adapter opens a connection and runs `SELECT 1`, which answers the
question the caller is actually asking. Any engine can synthesise this.

**Connection objects.** `create_connection` assumes a server-side object with a
lifecycle. Here a connection is only ever arguments to the next sync, so one is
fabricated in memory. **It does not survive a restart** — the `engine_mappings`
row then points at nothing. Acceptable for a reference implementation, and it
is the first thing a production non-Airbyte engine would have to fix.

**Job identity.** Same shape, same limitation: jobs live in the process. The
embedded Airbyte adapter has exactly this problem too, and the product's
reconciler already resolves lost jobs from its own database — so this is a
known pattern rather than a new hole.

## A bug the exercise found in passing

Writing a second discovery implementation surfaced something worth carrying to
any engine that reads Postgres.

The obvious portable query for primary keys is
`information_schema.table_constraints`. It returns **nothing** for a user who
only has `SELECT` — that view shows constraints to the table's *owner*. A
least-privilege reader is exactly the account a source connector should use, so
the natural query silently reports no primary keys, the product then offers no
deduplication, and no error appears anywhere.

Measured, as `demo_reader` on the demo source:

| Query | Rows |
|---|---|
| `information_schema.table_constraints` | 0 |
| `pg_index` / `pg_class` / `pg_attribute` | 3 (`id`, `id`, `sku`) |

`sql_direct` uses `pg_catalog`, ordered by `array_position(i.indkey, a.attnum)`
so composite keys keep index order. Airbyte's `source-postgres` does the same
thing, which is why the two discoveries now agree — a useful cross-check that
would not exist with only one implementation.

## What this does and does not prove

Proves: the boundary is real. A genuinely different engine plugs in behind it,
the domain layer needed no changes, and the three leaks that did exist were
small and are closed.

Does not prove: that any *particular* other engine is easy. Singer, dlt or a
managed ELT service would each pinch somewhere different — most likely on
connector specs, where Airbyte's registry does a lot of work that the interface
quietly assumes exists.

The honest summary for a roadmap: **swapping the engine is a contained piece of
work, not a rewrite.** One package, one enum value, one line in the registry.
The connector catalogue is the part that does not come with it.

## Adding an engine

1. `backend/app/adapters/<name>/adapter.py`, satisfying
   `IntegrationEngineAdapter`.
2. An `EngineType` value.
3. One branch in `adapters/registry.py:get_adapter`.
4. `pytest tests/test_adapter_contract.py` — the structural half runs without
   any engine and will name what is missing.
5. `python scripts/verify-engine-api.py` if the engine has an HTTP API.
6. The live gate is the same one Airbyte went through:
   `RUN_ENGINE_CONTRACT=1`, then `scripts/e2e.py --evidence`, then
   `scripts/release-gate.py`. Nothing about that gate is Airbyte-specific.

If you find yourself wanting to change a signature in `adapters/base.py`, that
is worth pausing on: it did not happen for an engine with nothing in common
with Airbyte, so it is more likely the adapter is being asked to do the
product's job than that the interface is wrong.
