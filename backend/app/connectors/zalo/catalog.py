"""Zalo Ads, ported from the reviewed contract with two things left out.

The contract is a Connector Builder export, and most of it is straightforward:
one collection at `open-api/ads`, OAuth2 client credentials, and the ad account
identified by a phone number passed as a query parameter. That much is kept as
written.

What was verified without credentials
-------------------------------------

Nobody here has a Zalo Ads application, so this is what could be checked:

* `open-api/oauth/token` exists and speaks the flow. Probed with a deliberately
  wrong client id: `No application registered for client_id`, which is an OAuth
  answer, not a 404.
* `open-api/ads` exists and refuses an unauthenticated call.

`check`, `discover` and a read were **not** exercised, so the connector ships
BETA and `compatibility.yaml` says exactly that.

Two things in the contract are deliberately not carried over
------------------------------------------------------------

**A cursor is not declared, and `date` stays an input.** The contract feeds the
`date` parameter from a config number the user types, which is a manual "read
from here" rather than a cursor -- it never advances. Turning it into a real
`DatetimeBasedCursor` would be the obvious improvement and is not made here,
because the parameter is `date` (singular) and nothing available says whether
the API reads it as "from this date" or "on this date". Guessing wrong in the
second case silently narrows every sync to one day. So the field is kept, named
so it reads as what it is, and the stream is honestly full refresh.

**The `last_update` field is dropped.** The contract stamps every record with
`now_utc()` under that name. A field called `last_update` that holds the moment
the connector read the row, not the moment the row changed, is the kind of thing
somebody builds a report on. Airbyte already records read time as
`_airbyte_extracted_at`, so this was a duplicate wearing a misleading name.

`phone_account` is kept: it records which ad account a row came from, which
genuinely is not recoverable from the record once several accounts land in one
table.
"""

from __future__ import annotations

from ._shared import Stream, ZaloConnector

ZALO_ADS = ZaloConnector(
    app="zalo-ads",
    title="Zalo Ads",
    summary="Chiến dịch, quảng cáo và số liệu báo cáo từ Zalo Ads theo từng "
            "tài khoản quảng cáo.",
    docs_url="https://ads.zalo.me/",
    config=(
        {
            "name": "phone",
            "title": "Số điện thoại tài khoản quảng cáo",
            "description": (
                "Zalo Ads định danh tài khoản quảng cáo bằng số điện thoại chứ "
                "không suy ra từ token, nên mỗi tài khoản cần một nguồn riêng. "
                "Số này cũng được ghi vào cột phone_account của mọi bản ghi."
            ),
        },
        {
            "name": "date",
            "title": "Đọc dữ liệu từ mốc",
            "type": "integer",
            "description": (
                "Mốc thời gian dạng epoch giây gửi cho tham số date của API. "
                "Đây là một giá trị cố định do người dùng nhập, không phải "
                "cursor: mỗi lần chạy đều đọc từ đúng mốc này. Cần đồng bộ "
                "tăng dần thì đặt lịch và chỉnh mốc, hoặc chờ tới khi có tài "
                "khoản thật để đo xem API hiểu date là từ mốc hay đúng ngày."
            ),
        },
    ),
    streams=(
        Stream(
            name="ads",
            path="ads",
            primary_key=("ad_id",),
            # The response is the array itself, per the reviewed contract.
            collection=(),
            params={
                "date": "{{ config['date'] }}",
                "phone": "{{ config['phone'] }}",
            },
            # Which account this row belongs to, which the record does not say.
            stamp={"phone_account": "{{ config['phone'] }}"},
            fields={
                "ad_name": "string", "ad_status": "string", "ad_type": "string",
                "campaign_id": "integer", "campaign_name": "string",
                "campaign_status": "string", "created": "integer",
                "start_date": "integer", "end_date": "integer",
                "reports": "object", "target": "object",
            },
            note="Quảng cáo kèm chiến dịch và số liệu báo cáo lồng trong "
                 "trường reports.",
        ),
    ),
)
