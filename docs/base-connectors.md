# Base.vn connectors

Ten connectors for the Base.vn ecosystem, written as part of this product
rather than imported into it. Clone the repository onto another machine, start
it, and they are in the catalogue — there is no YAML to import, no connector to
pull from Airbyte, and no per-machine configuration.

Per-stream detail is in [base-connectors-inventory.md](base-connectors-inventory.md),
which is generated from the code.

## What ships

| Connector | Streams | Host |
|---|---:|---|
| `source-base-account` | 2 | `account.{domain}/extapi/v1/` |
| `source-base-hrm` | 25 | `hrm.{domain}/extapi/v1/` |
| `source-base-hiring` | 8 | `hiring.{domain}/publicapi/v2/` |
| `source-base-income` | 13 | `income.{domain}/extapi/v1/` |
| `source-base-wework` | 6 | `wework.{domain}/extapi/v3/` |
| `source-base-service` | 5 | `service.{domain}/extapi/v1/` |
| `source-base-workflow` | 3 | `workflow.{domain}/extapi/v1/` |
| `source-base-payroll` | 3 | `payroll.{domain}/extapi/v1/` |
| `source-base-request` | 2 | `request.{domain}/extapi/v1/` |
| `source-base-timeoff` | 2 | `timeoff.{domain}/extapi/v1/` |

`{domain}` is `base.vn` or `base.com.vn`, chosen per source. See below.

69 streams. 42 unchanged from the original manifests, 25 improved, 2 new,
4 removed.

## How it is put together

```
backend/app/connectors/base_vn/
    _shared.py     the Base API dialect: auth, pagination, cursors, errors
    core.py        Account, Workflow, Request, Timeoff
    work.py        WeWork, Service, Payroll
    hr.py          HRM, Hiring
    finance.py     Income
    __init__.py    the registry, and the compiler entry point
```

A connector is a `BaseConnector` holding `Stream`s. At import, each compiles to
an Airbyte declarative manifest through the shared layer, and
`adapters/registry.py` joins them to the bundled JSON catalogue so the rest of
the product sees no difference between these and an Airbyte connector — same
seeding, same permissions, same wizard, same sync path.

```
Project  ->  BaseConnector  ->  Stream  ->  manifest  ->  engine  ->  warehouse
```

### Shared logic, per-workspace credentials

The connector definition is one row in `connector_definitions` with one
manifest. `seed_catalog()` **overwrites** that manifest from the code on every
deploy, which is what makes the promise true: change the API logic once here,
and every workspace using that connector has it after the next deploy. Nothing
is copied per workspace and nothing needs re-configuring.

What is per workspace is the credential. Each source a customer creates holds
its own token in the encrypted secret store, and it reaches the connector as
`access_token_v2`. Two workspaces on the same connector share every line of
logic and share no credentials.

No token appears anywhere in the source. A test asserts that.

### Changing a connector

Edit the Python, redeploy. To add a stream:

```python
Stream(
    name="holiday", path="holiday/list", collection=("holidays",),
    primary_key=("id",), incremental=Incremental(), paginate=True,
)
```

Structural mistakes fail at import — a missing primary key, a parent that is
not a stream, a duplicate name — so a broken connector cannot reach a
customer's catalogue.

## What the original YAML got wrong

The ten manifests in `docs/base-api/` are where this came from, and they
worked. But they were ten copies of the same conventions, and they had drifted.

### 1. The credential name (all ten)

Every manifest sent `access_token`. Base's error taxonomy distinguishes the
fields precisely:

| Sent | Base says |
|---|---|
| `access_token: <v2 token>` | `access_token_invalid_2` |
| `access_token_v2: <malformed>` | `access_token_v2_invalid_1` |
| `access_token_v2: <well-formed>` | accepted, or `access_token_v2_invalid_3` |

So none of them could authenticate with a current token.

### 2. Failure looked like success (all ten)

Base answers **HTTP 200** when it refuses a request:

```json
{"code": 0, "message": "access_token_v2_invalid_3", "data": null}
```

Nothing in the old manifests read `code`. An expired token therefore produced
an empty collection, a sync that completed, and — on a full-refresh
destination — a customer's table replaced with nothing, reported as success.

Every stream now fails on `code: 0`, with the message Base gave. Verified
end to end through the product:

```
succeeded : False
technical : Base rejected this request: access_token_v2_invalid_3.
            An `access_token_v2_invalid` message means the token is not
            accepted for this application — issue a new one in the Base
            admin console.
```

### 3. Missing pagination (17 streams)

HRM paginated 8 of 25 streams. `employee/list` was not one of them, nor were
`contract`, `insurance`, `position`, `tax`, `team`, `office` or `timesheet`.
An unpaginated list endpoint returns Base's default page and stops, so any
account past that size lost rows — silently, with no error.

`income.income_payment` was worse: it sent a literal `limit: 500` with
pagination switched off, so payment 501 onward simply did not exist.

### 4. WeWork read one hardcoded project

```yaml
project:
  path: project/get.full
  request_body_data: { id: "131471" }
```

A project id baked into the connector. Every WeWork sync, for every customer,
returned that one project — and `task`, `topic`, `tasklist` and `milestone` all
hung off it. The spec even declared a `project_id` config field the body
ignored.

`project/list` exists and was never used. It is the parent now, so WeWork reads
the whole account.

### 5. Streams that cost requests and returned nothing new

- `service.test` — identical to `service.ticket` in path, extractor, parent and
  cursor. Every sync crawled every ticket twice, into two tables.
- `payroll.test` — `GET /test`, empty extractor, no records.
- `income.income_inflow` and `income.inflow_income` — the same entities as
  `inflow` and `income`, read from the endpoint that does not own them. Two
  full paginated crawls of a customer's revenue history, per sync, for data
  already being collected.

### 6. Cursors that filtered on the wrong thing

`timeoff.timeoff` tracked `last_update` and asked the server for
`start_date_from`. A leave request booked last year and approved today changes
its `last_update` but not its start date, so incremental syncs skipped it.

`hrm.employee` declared a `last_update` cursor with no request parameter at
all: it re-read every employee on every sync and only looked incremental.

### 7. Eight streams had no primary key

Both Account streams, `timeoff.group`, `wework.project`, `wework.milestone`,
and — most costly — `service.ticket`, the largest table in that application.
Without one, a re-sync cannot deduplicate.

### 8. HRM asked for an API version

```yaml
url_base: https://hrm.{{ config['domain'] }}/extapi/{{ config['version'] }}
```

Two required fields with no defaults. `version` is gone — letting a workspace
change `extapi/v1` only lets them point at an API these streams are not written
against. `domain` stayed and is now consistent across all ten connectors, with
a default and a dropdown; see below for why it matters more than it looks.

## New streams

| Stream | Why |
|---|---|
| `request.request`, `request.group` | There was no Request manifest at all. The endpoint surface was found by probing: `request/list`, `request/get` and `group/list` exist; twenty other candidates 404. |
| `wework.project` (rebuilt) | `project/list` instead of one hardcoded id. |

## Testing

### Structural — runs anywhere

```bash
python -m pytest qa/backend/test_base_connectors.py -q
```

162 checks: every connector compiles, every stream has a primary key, the
credential is spelled one way everywhere, no token is embedded, the host is not
configurable, and each of the eight defects above is asserted so it cannot come
back.

### Live — needs tokens

```bash
python qa/e2e/base-connectors.py                     # spec, check, discover
python qa/e2e/base-connectors.py --read --records 50 # pull records
python qa/e2e/base-connectors.py --incremental       # sync twice, compare
```

Tokens go in `secrets/base-tokens.json` as `{"<app>": "<token>"}`. That path is
git-ignored; these are live credentials for somebody's Base account.

The suite deliberately runs `check` twice — once with the token, once with it
corrupted. Base answers HTTP 200 to both, so a connector that passes the second
is broken in the most dangerous way available. It belongs in the happy path.

### Results, 2026-08-25

```
30/40 checks passed

spec        10/10   the manifest declares access_token_v2
bad-token   10/10   a corrupted token is refused, not silently accepted
discover    10/10   69/69 streams, every one with a primary key
check        0/10   every token rejected: access_token_v2_invalid_3
```

## The domain, and the afternoon it cost

`base.vn` and `base.com.vn` are **separate installations with separate
accounts**. A token issued on one is refused by the other with:

```json
{"code": 0, "message": "access_token_v2_invalid_3"}
```

which is the same message an expired token produces. The supplied tokens are
for `base.com.vn`; tested against `base.vn` they all looked dead.

So `domain` is a config field on every connector, defaulting to `base.vn`, and
it renders as a dropdown rather than a text box — two installations, no typos.
The docs panel beside the form says this explicitly, because the failure mode
is a correct token that looks revoked.

The other half of the old HRM config, `version`, stayed removed. Letting a
workspace change `extapi/v1` only lets them point at an API these streams are
not written against.

## Test results — 2026-08-25, against base.com.vn

```
107/107 checks passed

spec         10/10   the manifest declares access_token_v2 and domain
check        10/10   Base accepts the token
bad-token    10/10   a corrupted token is refused, not silently accepted
discover     10/10   69/69 streams, every one with a primary key
read         16 streams returned data; 21 were empty in this account
incremental   3/3 where there was data to carry a cursor
```

Reads worth naming:

| | |
|---|---|
| `workflow.job` | **2670 records**, 2670 unique ids, 0 duplicates — six pages of 500 |
| `request.request` | 266 records |
| `wework.project` | **55 projects** — the hardcoded id returned 1 |
| `hiring.interview` | 40 records |

Incremental, second sync after the first:

| Stream | First | Second |
|---|---:|---:|
| `request.request` | 266 | **1** |
| `workflow.workflow` | 20 | **1** |
| `hrm.employee` | 2 | 2 |

Pagination verified directly against the API as well: `limit` is honoured up to
500, `page_id` is zero-based, consecutive pages share no ids, and a page past
the end returns an empty array — so the paginator terminates on its own.

`updated_from` filters server-side and progressively: 2670 records unfiltered,
75 since 2024, 2 since January 2026, 1 since June.

### Empty is reported as empty, not as a pass

21 streams returned nothing, because this test account has no Income data, no
leave records and no service compounds. The harness marks those inconclusive
rather than passing them — a read that returns nothing looks identical whether
the table is empty or the harness is broken, and that distinction was not
academic:

> `subprocess.run(text=True)` decodes with the locale codec. On Windows that is
> cp1252, so the first Vietnamese character in a Base record killed the reader
> thread and handed back empty output. Every `read` reported "0 records" for
> streams returning thousands — and reported it as a pass. The suite was lying
> for a full run before this was caught.

## The setup screen

Configuration on the left, documentation on the right — the shape Airbyte uses,
for the same reason: a connector form is a list of field names that only mean
something to somebody who already knows the system, and sending them to a
documentation site loses the form.

The panel holds what the form cannot say: where to get the token, which tables
this connector produces, and a link to Base's API reference. It does **not**
repeat each field's description — the form already shows that under the input
it belongs to, and the same paragraph twice on one screen is noise. On a narrow
screen the panel stacks above the form, because the explanation is more useful
than a head start on typing.

Driving it with a browser found things reading the code had not:

| | |
|---|---|
| **10 icon 404s** | one per Base connector, on the first screen of the wizard. Eight icons come from the `n8n-nodes-basevn-*` repositories; Account and Hiring have none published, so those two are drawn to match the set and say so in a comment |
| **The domain rendered as a paragraph box** | the form chose `<textarea>` when `description.length > 140`. The length of the help text decided the widget, so writing a careful explanation made the form worse. Now it is `format: textarea` or a long value, and `domain` is an enum, so it renders as a dropdown |
| **English help under Vietnamese labels** | every field description was in English in an otherwise Vietnamese product. All rewritten |
| **Markdown on screen** | descriptions contained backticks, which the form renders as literal punctuation |
| **`v7.28.2` under "Base HRM"** | the manifest runner's tag, meaningless to a user and easily read as Base's version. Now shows "25 bảng dữ liệu" |
| **`Production Postgres`** | the name placeholder, on a Base HRM form. Now derived from the connector |
| **The docs panel ran under the sticky footer** | 25 streams makes it taller than the viewport; it scrolls inside itself now |

Two scripts keep this honest, both in `qa/audit/`:

```bash
cd frontend
SHOT_DIR=../.shots DEMO_PASSWORD=... node ../qa/audit/connector-form.mjs
SHOT_DIR=../.shots DEMO_PASSWORD=... node ../qa/audit/base-source-journey.mjs
```

The first counts console errors and failed requests, reads back the labels and
help actually rendered, and checks the mobile layout for sideways overflow. The
second creates a Base source the way a person does — pick the connector, type a
name, paste the token, choose the domain, run the test, save. Last run: the
dropdown set `base.com.vn`, the test returned **Kết nối thành công**, the source
saved, and the console was clean.

## Suggested next phase

The framework takes a new Base application in about thirty lines, so these are
cheap to add once somebody wants them:

| Application | Note |
|---|---|
| Goal, Sign | Core Base, documented, not yet requested |
| Inside, Office, Meeting, Booking, Square, Table | The workspace suite |
| Checkin, Onboard, Schedule, Overtime | HR, alongside HRM and Timeoff |
| Expense, Asset, Bankfeed | Finance. `docs/base-api/base_expense.yaml` already exists and was left out of this scope — it has the same `start`-versus-`last_update` cursor bug as Timeoff |

Two things worth doing regardless:

- **`hrm.timesheet` has no cursor.** It is one of the largest tables in the
  product and is read in full every sync. Base documents no `updated_from`
  there; worth asking them.
- **Nothing verifies Base's page size.** `limit: 500` is what the old manifests
  asked for and Base may cap lower. One live run answers it.
