"""Income — revenue, and the documents that record it.

This is where the old manifest cost the most. Fifteen streams read six distinct
endpoints; eleven of them read just two:

    /incomes/last.update   6 streams
    /inflows/last.update   5 streams

Base returns one document per call containing `incomes`, `payments`,
`customers`, `estimates` and `income_item_lines` together, and each stream
crawled the whole paginated result to pick a different array out of it. So a
sync fetched the same bytes eleven times.

Airbyte's declarative framework gives one HTTP request per stream, so a stream
per array is the only way to land these as separate tables. That much is
unavoidable. What *was* avoidable is reading the same array twice, which the old
manifest did:

    income_inflow   ->  `inflows` out of /incomes/last.update
    inflow          ->  `inflows` out of /inflows/last.update
    inflow_income   ->  `incomes` out of /inflows/last.update
    income          ->  `incomes` out of /incomes/last.update

`income_inflow` and `inflow_income` are the same entities as `inflow` and
`income`, taken from the wrong endpoint -- an inflow listed inside an income
document is the same inflow, minus whatever the inflow endpoint returns that
the income endpoint does not. Both are gone. Nothing is lost: the streams that
remain read those entities from the endpoints that own them.

Eleven crawls of two endpoints becomes nine. Not a triumph, but it is two full
paginated crawls of a customer's revenue history per sync, and the data was
already there.
"""

from __future__ import annotations

from ._shared import BaseConnector, Incremental, Stream

# Income runs on `extapi/v1`, not `publicapi/v2`, and that older API differs in
# two ways that both had to be found against the live service:
#
#   ?updated_from=0            -> code 0, "Updated from param is required"
#   body updated_from=0        -> code 0, "Updated to param is required"
#   body updated_from + updated_to -> code 1, records
#
# So the bounds go in the form body, and the range has to be closed. Until this
# was fixed the connector sent `updated_from` in the query string, Income
# reported the parameter as missing, and the whole sync failed.
_INC = Incremental(end_param="updated_to", inject_into="body_data")


def _income(name: str, collection: str, *, source: str = "incomes",
            fields: dict[str, str] | None = None, note: str = "",
            paginate: bool = True) -> Stream:
    return Stream(
        name=name, path=f"{source}/last.update",
        collection=("data", collection),
        primary_key=("id",), incremental=_INC, paginate=paginate,
        fields=fields or {}, note=note,
    )


INCOME = BaseConnector(
    app="income",
    title="Base Income",
    summary="Chứng từ doanh thu, tiền đã thu, và dữ liệu danh mục đi kèm.",
    url_base="https://income.{domain}/extapi/v1/",
    docs_url="https://documenter.getpostman.com/view/26402107/2s9YsGiYxE",
    streams=(
        # ── the two documents ───────────────────────────────────────────────
        _income("income", "incomes",
                fields={"name": "string", "customer_id": "string",
                        "total": "number", "status": "string"},
                note="Revenue documents."),
        _income("inflow", "inflows", source="inflows",
                fields={"name": "string", "customer_id": "string",
                        "total": "number", "status": "string"},
                note="Cash inflows."),

        # ── their line-level detail ─────────────────────────────────────────
        _income("income_item_line", "income_item_lines",
                fields={"income_id": "string", "amount": "number"},
                note="Line items. The grain most revenue reporting needs."),
        _income("inflow_item_line", "inflow_item_lines", source="inflows",
                fields={"inflow_id": "string", "amount": "number"}),
        _income("income_payment", "payments",
                fields={"income_id": "string", "amount": "number"},
                note="Payments against an income document. The old manifest "
                     "sent a literal `limit: 500` here with pagination "
                     "switched off, so an account with more than 500 payments "
                     "lost the rest silently. Paginated now."),
        _income("inflow_payment", "payments", source="inflows",
                fields={"inflow_id": "string", "amount": "number"}),
        _income("income_estimate", "estimates",
                fields={"income_id": "string", "amount": "number"}),

        # ── parties, as the documents report them ───────────────────────────
        _income("income_customer", "customers",
                fields={"name": "string"},
                note="Customers as they appear on income documents. Base "
                     "exposes no standalone customer endpoint -- `customers/get` "
                     "and `clients/get` both 404 -- so this is the only route "
                     "to them. Paginated now; it was not."),
        _income("inflow_customer", "customers", source="inflows",
                fields={"name": "string"}),

        # ── reference data ─────────────────────────────────────────────────
        Stream(
            name="inflow_code", path="inflowcodes/get",
            collection=("data", "inflow_codes"), incremental=_INC,
            paginate=False, fields={"name": "string", "code": "string"},
            note="A short enumeration; no pagination needed.",
        ),
        Stream(
            name="inflow_code_group", path="inflowcodegroups/get",
            collection=("data", "inflow_code_groups"), incremental=_INC,
            paginate=False, fields={"name": "string"},
        ),
        Stream(
            name="revenue_unit", path="revenueunits/get",
            collection=("data", "revenue_units"),
            paginate=False, fields={"name": "string"},
        ),
        Stream(
            name="revenue_center", path="revenuecenters/get",
            collection=("data", "revenue_centers"),
            paginate=False, fields={"name": "string"},
        ),
    ),
)
