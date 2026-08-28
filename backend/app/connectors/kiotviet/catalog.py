"""KiotViet Retail, as its Public API actually answers.

Ported from the published KiotViet Public API document, then checked
endpoint by endpoint against a live shop. The document is accurate about the
envelope and the query parameters and wrong in two places, both recorded below.

What was measured, on retailer `cuahangnho`
-------------------------------------------

* **Auth.** `client_credentials` against `id.kiotviet.vn/connect/token` with
  `scopes=PublicApi.Access` returns a bearer token. Every other call needs both
  that token and a `Retailer` header. The document covers none of this -- it
  says only "Ngoại trừ API lấy Authentication Code / Access Token".
* **Envelope.** `{total, pageSize, data, timestamp}` on nineteen endpoints.
  `settings` is the exception and is not a collection at all.
* **Paging is an offset.** `currentItem=100` means "start at the hundred-and-
  first record", not "page 100". Walked three pages of the 754-record
  `locations` collection with no overlap and no repeats.
* **`lastModifiedFrom` filters server-side.** `branches` returned 1 unfiltered
  and 0 for a 2030 cursor.
* **`pageSize=200` works** even though the document says the maximum is 100.
  The connector asks for 100 anyway -- see `PAGE_SIZE`.

Two endpoints the document names do not exist
---------------------------------------------

* `surcharges` (§2.10, "Thu khác") answers **404** at that path, and at
  `surcharge` and `settings/surcharge` too. Not shipped: a stream that 404s is
  a permanently red row.
* `customergroup` (§2.13, "Nhóm khách hàng") also **404s**. The working path is
  `customers/group`, which the section heading does not say. Shipped under the
  real path.

Three more are shipped with a caveat rather than a guess
--------------------------------------------------------

* `ordersuppliers` answers **HTTP 420** on this shop: *Thiết lập "Đặt hàng
  nhập" đang không được bật*. That is a per-retailer setting, not a broken
  endpoint, so the stream ships and 420 is IGNORE-ed by the error handler --
  see `_error_handler`. A shop that has the module on gets the data; one that
  does not keeps syncing everything else.
* `webhooks` returned `{total, pageSize, timestamp}` with **no `data` key at
  all** while empty. The extractor tolerates that; it yields nothing rather
  than failing.
* `tax` is at `/tax`, not `/tax/detail` as §2.27.1 prints -- `/tax/detail`
  404s. Thirteen records came back from `/tax`.

The shop this was measured on is nearly empty
---------------------------------------------

`cuahangnho` holds 1 branch, 2 sale channels, 13 taxes and 754 locations;
products, orders, customers, invoices and the rest are all `total: 0`. So the
request side, the envelope, paging and the cursor are verified, and **record
parsing on the commercial collections is not**. Field types are declared from
the document rather than from records, and the schemas stay open so nothing is
dropped. The connector is BETA until a shop with data has run it.
"""

from __future__ import annotations

from ._shared import Incremental, KiotVietConnector, Stream

#: The filter is spelled the same everywhere KiotViet offers it; only the
#: record's own timestamp field changes.
_MODIFIED = Incremental(field="modifiedDate")
_CREATED = Incremental(field="createdDate")

KIOTVIET = KiotVietConnector(
    app="kiotviet",
    title="KiotViet",
    summary="Hàng hóa, đơn hàng, hóa đơn, khách hàng và tồn kho từ KiotViet "
            "Retail qua Public API.",
    docs_url="https://www.kiotviet.vn/huong-dan-su-dung-kiotviet/"
             "retail-ket-noi-api/public-api/",
    streams=(
        # ── the shop itself ──────────────────────────────────────────────
        Stream(
            name="branch", path="branches", incremental=_CREATED,
            # Measured: a branch record carries `createdDate` and no
            # `modifiedDate`, so the cursor tracks the field that exists.
            fields={"branchName": "string", "address": "string",
                    "contactNumber": "string", "retailerId": "integer"},
            note="Chi nhánh của cửa hàng.",
        ),
        Stream(
            name="user", path="users", incremental=_MODIFIED,
            fields={"userName": "string", "givenName": "string",
                    "retailerId": "integer"},
            note="Người dùng trong cửa hàng.",
        ),
        Stream(
            # Not a collection: the response *is* the settings object, with the
            # flags at the top level and no `total`/`data`. An empty collection
            # path makes the whole body one record.
            name="setting", path="settings", collection=(), paginate=False,
            primary_key=("retailerId",),
            fields={"ManagerCustomerByBranch": "boolean",
                    "AllowSellWhenOutStock": "boolean"},
            note="Thiết lập cửa hàng. Một bản ghi duy nhất, không phải danh sách.",
        ),

        # ── catalogue ────────────────────────────────────────────────────
        Stream(
            name="category", path="categories", incremental=_MODIFIED,
            fields={"categoryName": "string", "parentId": "integer",
                    "hasChild": "boolean", "retailerId": "integer"},
            note="Nhóm hàng, tối đa 3 cấp.",
        ),
        Stream(
            name="product", path="products", incremental=_MODIFIED,
            # `includeInventory` is off by default and the document lists it as
            # optional. Left off: inventory per branch multiplies the payload
            # and `inventory` below reads it directly.
            fields={"code": "string", "name": "string", "fullName": "string",
                    "categoryId": "integer", "categoryName": "string",
                    "basePrice": "number", "unit": "string",
                    "hasVariants": "boolean", "retailerId": "integer"},
            note="Hàng hóa của cửa hàng.",
        ),
        Stream(
            name="trademark", path="trademark", incremental=_MODIFIED,
            fields={"name": "string", "retailerId": "integer"},
            note="Thương hiệu.",
        ),
        Stream(
            name="pricebook", path="pricebooks",
            fields={"name": "string", "isActive": "boolean",
                    "startDate": "string", "endDate": "string"},
            note="Bảng giá.",
        ),

        # ── people ───────────────────────────────────────────────────────
        Stream(
            name="customer", path="customers", incremental=_MODIFIED,
            fields={"code": "string", "name": "string", "contactNumber": "string",
                    "email": "string", "groups": "string", "debt": "number",
                    "retailerId": "integer"},
            note="Khách hàng.",
        ),
        Stream(
            # `customers/group`, measured. Not `customergroup`, which 404s
            # despite being the heading of §2.13.
            name="customer_group", path="customers/group",
            fields={"name": "string", "description": "string",
                    "retailerId": "integer"},
            note="Nhóm khách hàng.",
        ),
        Stream(
            name="supplier", path="suppliers", incremental=_MODIFIED,
            fields={"code": "string", "name": "string", "contactNumber": "string",
                    "email": "string", "retailerId": "integer"},
            note="Nhà cung cấp.",
        ),

        # ── money moving ─────────────────────────────────────────────────
        Stream(
            name="order", path="orders", incremental=_MODIFIED,
            fields={"code": "string", "purchaseDate": "string",
                    "branchId": "integer", "customerId": "integer",
                    "total": "number", "status": "integer",
                    "statusValue": "string", "retailerId": "integer"},
            note="Đặt hàng.",
        ),
        Stream(
            name="invoice", path="invoices", incremental=_MODIFIED,
            fields={"code": "string", "purchaseDate": "string",
                    "branchId": "integer", "customerId": "integer",
                    "total": "number", "totalPayment": "number",
                    "status": "integer", "retailerId": "integer"},
            note="Hóa đơn bán hàng.",
        ),
        Stream(
            name="return_order", path="returns", incremental=_MODIFIED,
            fields={"code": "string", "returnDate": "string",
                    "branchId": "integer", "customerId": "integer",
                    "totalPayment": "number", "retailerId": "integer"},
            note="Phiếu trả hàng.",
        ),
        Stream(
            name="purchase_order", path="purchaseorders", incremental=_MODIFIED,
            fields={"code": "string", "purchaseDate": "string",
                    "branchId": "integer", "supplierId": "integer",
                    "total": "number", "retailerId": "integer"},
            note="Nhập hàng.",
        ),
        Stream(
            # Ships despite answering 420 on the shop this was measured on:
            # that is the "Đặt hàng nhập" module being switched off for this
            # retailer, not a broken path. The error handler skips 420.
            name="order_supplier", path="ordersuppliers", incremental=_MODIFIED,
            fields={"code": "string", "branchId": "integer",
                    "supplierId": "integer", "total": "number"},
            note="Đặt hàng nhập. Chỉ có dữ liệu nếu cửa hàng bật tính năng này; "
                 "không bật thì stream được bỏ qua chứ không làm hỏng lần sync.",
        ),
        Stream(
            name="transfer", path="transfers", incremental=_MODIFIED,
            fields={"code": "string", "fromBranchId": "integer",
                    "toBranchId": "integer", "status": "integer"},
            note="Chuyển hàng giữa các chi nhánh.",
        ),
        Stream(
            name="cashflow", path="cashflow",
            fields={"code": "string", "branchId": "integer",
                    "amount": "number", "method": "string"},
            note="Sổ quỹ.",
        ),

        # ── reference data ───────────────────────────────────────────────
        Stream(
            name="sale_channel", path="salechannel",
            fields={"name": "string", "isActive": "boolean"},
            note="Kênh bán hàng.",
        ),
        Stream(
            name="bank_account", path="bankaccounts",
            fields={"bankName": "string", "accountNumber": "string",
                    "accountName": "string"},
            note="Tài khoản ngân hàng.",
        ),
        Stream(
            # `/tax`, measured (13 records). Not `/tax/detail` as §2.27.1
            # prints -- that path 404s.
            name="tax", path="tax",
            fields={"name": "string", "value": "number", "type": "integer",
                    "typeName": "string"},
            note="Danh mục thuế KiotViet hỗ trợ.",
        ),
        Stream(
            name="location", path="locations",
            fields={"name": "string", "normalName": "string"},
            note="Danh mục tỉnh/huyện dùng cho địa chỉ.",
        ),
        Stream(
            name="voucher_campaign", path="voucherCampaign",
            fields={"code": "string", "name": "string",
                    "startDate": "string", "endDate": "string"},
            note="Đợt phát hành voucher.",
        ),
        Stream(
            # Answers `{total, pageSize, timestamp}` with no `data` key while
            # empty; the extractor yields nothing rather than failing.
            name="webhook", path="webhooks",
            fields={"type": "string", "url": "string", "isActive": "boolean"},
            note="Webhook đã đăng ký.",
        ),
    ),
)
