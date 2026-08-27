# Current status — AppBI Data Integration

One page. `PRODUCTION_READINESS_REVIEW.md` is the full review log and reads
chronologically; this is where it has got to.

**Updated:** 2026-08-27 (cursor `deal_activity` lọc thật)

---

## Bootstrap khi deploy chạy câm (2026-08-27)

Tìm ra khi đi kiểm chứng bản vá ở trên: container `migrate` thoát 0 mà không in
một dòng nào sau phần Alembic — không `bootstrap.schema_ready`, không
`bootstrap.catalog_seeded`, không `bootstrap.seed_complete`.

Nó không hề bỏ qua bước nào. `migrations/env.py` gọi
`fileConfig(alembic.ini)` vô điều kiện, mà file đó mô tả logging cho CLI đứng
một mình: root ở mức WARNING với một handler stderr thường. Áp cấu hình đó bên
trong một tiến trình chủ sẽ thay luôn handler mà tiến trình đã cài.
`python -m app.bootstrap` migrate **rồi mới** seed, nên toàn bộ phần sau đi vào
im lặng. `disable_existing_loggers=False` không đủ: hỏng là ở mức và handler của
root bị thay, không phải ở việc logger cũ bị tắt.

Đây không chỉ là chuyện đẹp mắt. Bước đẩy manifest mới thêm ở trên ghi
`catalog.manifest_republish_failed` khi engine không tới được — đúng cảnh báo mà
người vận hành cần thấy, và nó rơi vào đúng khoảng câm này. Một bootstrap chạy
đúng mà không nói gì thì không phân biệt được với một bootstrap đã bỏ qua.

Sửa: chỉ áp `alembic.ini` khi chưa ai cấu hình logging. Khoá bằng
`test_running_migrations_does_not_silence_the_application_log`, và test đó tự
chứng minh mối nguy còn thật trước khi kiểm tra guard — nếu ngày nào
`alembic.ini` thôi thay logging của chủ, test sẽ nói ra thay vì lặng lẽ bảo vệ
một thứ không còn tồn tại.

---

## Sửa connector không tới được source đã tạo (2026-08-27)

Tìm ra khi truy vấn `deal_activity`, và nặng hơn chính lỗi ban đầu.

`app/connectors/base_vn/__init__.py` viết rằng "sửa một lần, mọi workspace nhận
được ở lần deploy sau, vì `seed_catalog()` ghi đè manifest đã lưu". Nửa đầu
đúng, nửa sau không. Một connector khai báo **không** tồn tại trong engine dưới
dạng một definition có logic; logic nằm trong `__injected_declarative_manifest`
của *từng source*, được nhét vào đúng lúc tạo source, và Airbyte giữ bản sao đó
mãi. Deploy lại chỉ cập nhật hàng trong `connector_definitions`, bản xem trước
của Builder, và những source tạo *từ giờ trở đi*.

Triệu chứng: sau khi vá `deal_activity`, chạy lại ba lần vẫn ra đúng 3.970 bản
ghi không lọc. Manifest trong catalogue đã có `is_client_side_incremental: true`
lúc 10:13:25; source trong engine thì vẫn là bản dựng lúc 09:14. Chỉ đến khi
`PATCH /api/v1/sources/{id}` — thao tác tay — engine mới nhận bản mới. Đó không
phải việc một workspace phải biết mà làm.

Sửa: `seed_catalog` trả về `SeedOutcome(created, manifests_changed)` — so
manifest cũ với mới ngay chỗ ghi đè — và bootstrap gọi
`actors.republish_manifests()` đẩy bản mới tới mọi source/destination dựng trên
những connector đó. Manifest không đổi thì không đụng gì tới tài nguyên đang
chạy. Engine tạm không tới được thì ghi log rồi bỏ qua, không chặn khởi động;
lần deploy sau thử lại, vì mốc so sánh là trạng thái đã lưu chứ không phải một
cờ "đã xong".

Khoá bằng `test_a_rebuilt_manifest_reaches_the_sources_already_built_on_it`: một
deploy không đổi gì thì không được đẩy gì, một deploy đổi manifest thì phải đẩy
đúng resource đang có. Tắt bước đẩy đi thì test đỏ — đã thử ngược.

---

## Hạn mức 100 req/phút, và một lỗi Airbyte biến nó thành bí ẩn (2026-08-27)

Lần chạy Leads đầu tiên thất bại ba attempt liền, và thông báo duy nhất là:

    io.temporal.failure.ServerFailure: Complete result exceeds size limit.

Không nói gì về stream nào, cũng không phải lỗi thật. Truy theo bốn lớp:

1. **Gốc, và là phần của mình:** `lead/feed/list` gọi một lượt cho mỗi lead —
   546 lượt — và Base CRM Leads chặn ở **100 request/phút**. Nó từ chối bằng
   HTTP **400** với `{"code": 0, "message": "Quota exceeded: 100 req/min"}`:
   không 429, không `Retry-After`. Luật catch-all FAIL của mình (đúng đắn cho
   `code: 0`) đọc đó là lỗi chí tử và giết stream sau 37 giây.
2. `lead_feed` chuyển sang INCOMPLETE lúc 14:16:59, rồi nhận INCOMPLETE **lần
   hai** lúc 14:17:02 khi job dọn dẹp.
3. `StreamStatusTracker` ném `StreamStatusException` cho lần chuyển trùng đó — và
   trong Airbyte 0.59.1 `StreamStatusException.getMessage()` **gọi chính nó**.
   Đọc bytecode cho thấy lệnh đầu tiên của phương thức là `invokevirtual
   getMessage` trên `this`. Log nó là StackOverflowError.
4. Stacktrace hàng nghìn khung đó đi vào `StandardSyncOutput.failures`, đẩy kết
   quả activity vượt giới hạn blob 2 MB của Temporal. Temporal từ chối, Airbyte
   retry, ba lần đều thế.

State chỉ 70 KB và catalog 1 KB — đã đo, để loại trừ hai nghi phạm dễ đoán.

Sửa hai lớp mình sở hữu:

* **`BaseConnector.rate_limit`** phát `api_budget` với
  `MovingWindowCallRatePolicy` 100/PT1M. CDK tự điều tiết thay vì chạy hết tốc
  rồi học giới hạn từ một lời từ chối. Không có matcher, vì hạn mức tính theo
  token chứ không theo endpoint.
* **`RATE_LIMIT_REFUSALS`** khớp `quota exceeded` / `too many request` với action
  `RATE_LIMITED`, **đặt trước** FAIL. Đây là lưới an toàn cho trường hợp có thứ
  khác đang tiêu cùng hạn mức đó.

Chỉ khai cho Leads. API Sales chịu 5.582 request trong một lần sync ở khoảng
220/phút mà không bị từ chối, nên hai ứng dụng không dùng chung giới hạn; đặt
bừa một con số cho mười connector còn lại sẽ làm mọi lần sync chậm đi để phòng
một cái trần chưa ai thấy. Khoá bằng
`test_only_the_application_with_a_measured_cap_declares_one`.

Ba lớp 2–4 là của Airbyte, không sửa được từ đây. Điều đáng ghi lại: ở phiên bản
này, **một stream lỗi có thể biến thành một job không đọc được thông báo**. Khi
gặp `Complete result exceeds size limit`, chỗ cần tìm là stream nào đổi trạng
thái hai lần, rồi tìm lỗi thật ngay trước đó.

---

## Builder: chọn stream cha xong phải chạy được (2026-08-27)

Đối chiếu với ảnh Connector Builder của Airbyte anh gửi. Bố cục của mình đã có
đủ các nhóm tương ứng — Request, Pagination, Incremental, Partition,
Transformations, Error handler — và gom thành khối gập được thay vì cuộn phẳng,
nên phần bố cục không cần đổi. Thiếu là ở **ba ô điều khiển**, và cả ba đều là
bài học rút ra trong ngày.

### 1. Chọn stream cha nhưng không gửi id cha

Đây chính là chỗ anh nói "connect stream cha sang stream con đang gặp lỗi".

`SubstreamPartitionRouter` chỉ **lặp** stream một lần cho mỗi bản ghi cha; nó
không sửa request. Builder trước đây chọn cha xong là hết — request con không
đổi, nên nó đọc đúng một trang giống nhau N lần rồi báo thành công. Đường thoát
duy nhất là biết tới `{{ stream_partition.<field> }}`, mà gợi ý trên form chỉ nói
tới **URL path**, trong khi mọi API của Base nhận id cha trong **form body**.

Sửa ba lớp:

* CDK cho phép `request_option` trên `ParentStreamConfig`. Điền tên tham số là
  id cha được gửi tự động, kèm ô chọn nơi gửi (query / body form / body JSON /
  header).
* Chọn cha mà **không** điền tên tham số và cũng **không** tham chiếu phân mảnh ở
  path, query, header hay body thì `validate()` từ chối, với thông báo nêu cả hai
  cách sửa. N request giống nhau báo thành công tệ hơn một lỗi.
* Thêm ô `incremental_dependency` ("Incremental Parent" trong ảnh), **mặc định
  tắt**, kèm cảnh báo ghi rõ số đo: 234/234 pipeline CRM có deal mới hơn chính
  pipeline, nên bật lên là những cha đó rơi khỏi danh sách phân mảnh sau lần sync
  đầu và con của chúng ngừng về trong khi mọi lần chạy vẫn xanh.

### 2. Khai cursor mà không lọc gì

Ảnh của Airbyte có ô "API Time Filtering Capabilities". Builder của mình không
có, nên gặp endpoint kiểu `deal/get.activities` hay `lead/feed/list` thì người
dùng chỉ còn hai lựa chọn: bịa một tên tham số, hoặc nhận một cursor không làm
gì. Đúng lỗi vừa sửa ở `deal_activity`.

Thêm ô "Ai lọc theo thời gian" với hai lựa chọn: API lọc (gửi tham số) hoặc API
không lọc (phát `is_client_side_incremental`, không gửi gì). **Hai chứ không phải
ba như Airbyte** — `is_data_feed` là một lời hứa khác (API trả mới nhất trước,
phân trang dừng ở cursor) và CDK từ chối kết hợp nó với lọc phía client; một ô
không giải thích được trên form thì tệ hơn là không có.

Cũng thêm ô chọn nơi gửi mốc thời gian, vì trước đây luôn ép vào query string.

### 3. Cỡ trang mặc định 50

Builder luôn phát `page_size` và luôn bịa tên tham số (`per_page`/`limit`) đặt
vào query string — cả ba đều sai với API POST-form. Nay để trống được, và khi
trống thì không phát `page_size_option` lẫn `page_size` trong strategy. Lý do đã
đo trên `lead/list`: nó phân trang, cỡ 100 cố định, và bỏ qua `limit` ở cả 5,
500, 1000.

`page_size` là con số CDK đem so với một trang ngắn để biết đã hết. Khai một cỡ
server chưa từng đồng ý thì hoặc dừng ngay ở trang mặc định đầu tiên, hoặc lật
trang qua khỏi cuối mãi mãi.

### Và một lỗi mất dữ liệu trong lúc rà

`definition_from_manifest` thay **mọi** partition router bằng `{"mode": "none"}`.
Nhập một manifest có liên kết cha–con rồi lưu là mất liên kết — đúng điều
docstring của chính nó hứa không xảy ra ("anything unrecognised makes the import
fail loudly instead"). Cũng mất luôn `cursor_end_param`, `step`, `lookback`, và
đặt `page_size` về 50 khi manifest không khai. Nay round-trip giữ hết, và có test
đi hai chiều: định nghĩa → YAML → định nghĩa → biên dịch lại phải ra đúng router
cũ.

### Bằng chứng

`test_the_builder_can_express_base_crm_leads` dựng lại Base CRM - Leads bằng
đúng những gì điền được trên form, rồi so từng thành phần với connector đã ship:
cùng record selector, cùng loại paginator, cùng chuỗi cha hai tầng có
`request_option` vào body, cùng cursor server-side ở `lead` và client-side ở
`lead_feed`. Connector viết tay ship kèm sản phẩm; một workspace gặp cùng API mà
không đi tới được cùng manifest thì Builder chỉ là bản demo.

Lưu ý về nút Test: ở chế độ `AIRBYTE_API`, Test trong Builder chạy `check` +
`discover` và **không** trả về bản ghi mẫu — Config API của Airbyte không có
endpoint đọc giới hạn, và tự gọi HTTP trong sản phẩm sẽ cho một bản xem trước
chạy bằng network, Python và CDK của sản phẩm chứ không phải của connector đã
phát hành. Adapter báo thẳng qua `record_preview_supported`. Muốn thấy bản ghi
thì publish rồi chạy trong một connection — đúng như cảnh báo trong ảnh Airbyte
anh gửi.

---

## Base CRM tách đôi: Deals và Leads (2026-08-27)

Ba endpoint Lead trong tài liệu (`lead/services`, `lead/list`,
`lead/feed/list`) không nằm cùng ứng dụng với phần đang có:

| | Base CRM - Deals | Base CRM - Leads |
|---|---|---|
| Gốc đường dẫn | `apis.basecrm.vn/sales/v1/` | `apis.basecrm.vn/leads/` |
| Token | riêng | **riêng, khác hẳn** |
| Thực thể chung | — | không có |

Đo được: token Sales bị `/leads/` từ chối với `INVALID TOKEN APPKEY` trên cả hai
host, có và không có mật khẩu, trong khi chính token đó vẫn trả về 271 pipeline
ở `sales/v1/pipeline/all`. Gửi bằng `access_token_v2` thì lỗi khác
(`access_token_invalid_1`) — nó đọc cả hai tên và không nhận cái nào từ nhầm
app. Tài liệu gọi cặp này là `leads_access_token` / `leads_password` cũng vì thế.

Nên tách thành `source-base-crm-leads`, và connector cũ đổi tên hiển thị thành
**Base CRM - Deals** (khoá vẫn là `source-base-crm` — đổi khoá sẽ làm mồ côi mọi
source đã dựng). Gộp chung sẽ bắt workspace chỉ bán hàng phải giữ credential
Leads, hoặc để cặp đó tùy chọn và sinh ra ba stream hiện trong Schema rồi hỏng
lúc sync — đúng kiểu lỗi câm vừa dọn hôm nay.

Ba stream: `lead_service → lead → lead_feed`. `lead/services` liệt kê dịch vụ từ
token nên không cần `service_id` điền sẵn, giống cách phần Deals đã bỏ được
`service_id: '248'` hardcode.

**`time_filter_key` là điểm đáng chú ý.** `lead/list` không có một trường "đổi
từ lúc nào" duy nhất: nó nhận khoảng `start_time`/`end_time` **cộng với**
`time_filter_key` chỉ ra khoảng đó áp lên cột nào — `since`, `last_update`,
`last_update_stage` hay `last_update_status`. Mặc định là `since`, tức thời điểm
tạo; incremental theo đó sẽ gom được lead mới và **không bao giờ thấy** một lead
cũ bị sửa hay chuyển giai đoạn. Connector ghim `last_update`. Hai key còn lại
mỗi cái chỉ nhúc nhích theo một loại thay đổi nên không dùng làm cursor.

Hai khả năng mới trong `_shared.py`, cả hai đều xuất phát từ `lead/list`:

* `page_size_field=None` — endpoint có `page` nhưng **không có** tham số cỡ
  trang. Gửi thêm `limit` là bịa tham số; và nguy hơn, `page_size` khai trong
  `PageIncrement` chính là con số CDK đem so với một trang ngắn để biết đã hết.
  Khai một cỡ mà server chưa từng đồng ý thì hoặc dừng ngay ở trang mặc định đầu
  tiên, hoặc lật trang qua khỏi cuối mãi mãi. Không khai thì nó dừng khi gặp
  trang rỗng — điều luôn đúng.
* `BaseConnector.certification` — một connector dựng từ tài liệu nhưng chưa chạy
  thật thì nói `BETA`, không nhận `SUPPORTED`.

### Đã đo với token thật, và một chỗ tôi đoán sai

`qa/probe/base_crm_leads.py`. Những gì nó sửa:

* **`lead/services` trả về khoá `services`, không phải `lead_services`.** Quy ước
  của mười một connector Base còn lại — collection đặt tên theo thực thể — không
  đúng ở đây. Dựng theo phỏng đoán thì đọc được **0 bản ghi và vẫn báo thành
  công**. Đây chính là lý do connector này ship `BETA` cho tới khi có số đo.
* **`service_id` là bắt buộc** — thiếu nó `lead/list` trả `Invalid service`.
* **Trang cỡ 100 cố định, `limit` bị bỏ qua hoàn toàn** (5 / 500 / 1000 đều trả
  100). Lật trang trên dịch vụ 205 lead ra 100 / 100 / 5 / 0, không trùng lặp,
  và `page=0` lặp lại trang 1.
* **`lead/feed/list` không phân trang và không lọc thời gian** — `page=2` trả
  đúng 15 bản ghi như trang 1, và cả sáu phép thử tham số thời gian đều trả đủ
  15. Mọi feed có `last_update`, nên nó nhận cursor lọc phía client.
* Bản ghi lead **không có** `service_id` lẫn `stage_id`; giai đoạn là `stage`,
  trạng thái là `status`. Kiểu trường khai theo bản ghi, không theo tham số.

### Chạy thật lên BigQuery

| Stream | Lần 1 | Lần 2 |
|---|---:|---:|
| lead_service | 19 | 19 |
| lead | 24 | 24 |
| **lead_feed** | 1.790 | **707** |

1.833 bản ghi, 8 phút, 1 attempt. `lead` giữ 24 vì đó là các bản ghi nằm đúng
mốc biên (một cho mỗi dịch vụ có lead) — cùng trạng thái dừng như `deal` ở phần
Deals. `lead_feed` giảm 1.790 → 707 nhờ cursor phía client. Đã nâng lên
`SUPPORTED` với bằng chứng ghi trong
`test_only_verified_connectors_claim_certification`.

---

## Cursor khai báo mà không lọc gì — `deal_activity` (2026-08-27)

Câu hỏi của anh về `deal_activity` đúng ở cả hai vế, và vế thứ hai là một lỗi
thật.

**Vế 1 — nó chạy theo toàn bộ deal id, không phải deal mới nhất.** Đây là hành
vi cố ý. `SubstreamPartitionRouter` đọc stream cha độc lập với cursor của cha,
nên 5.582 deal đều được duyệt mỗi lần sync. Bật `incremental_dependency` sẽ cắt
xuống hai chục lần nhưng **không an toàn ở đây**: trong 121 deal có feed, 46
deal có feed mới hơn chính deal đó (chênh tới hai ngày). Những deal ấy sẽ rơi
khỏi danh sách phân mảnh và hoạt động mới nhất của chúng ngừng về, trong khi mọi
lần chạy vẫn báo thành công.

**Vế 2 — Sync mode `incremental` là nhãn suông.** Stream khai báo cursor, lưu
state đủ 5.583 phân mảnh, cursor có tiến — và vẫn phát lại đúng 3.970 bản ghi ở
lần chạy sau. CDK chỉ so bản ghi với mốc nước khi manifest bật
`is_client_side_incremental`; connector không phát cờ đó ở bất kỳ đâu.

Trong lúc truy vấn, một kết luận cũ của tôi hoá ra sai và đã sửa: tôi từng ghi
`pipeline/deals`, `account/list`, `contact/list` "khai báo bộ lọc thời gian rồi
lờ đi". Không phải. Chúng lọc thật, **nhưng chỉ khi nhận đủ cặp**:

| Gửi tới `pipeline/deals` (65 deal) | Trả về |
|---|---:|
| không tham số | 65 |
| chỉ `last_update_stime` = 2030 | 65 |
| `last_update_stime` + `last_update_etime` = 2030 | 0 |

Phép thử cũ chỉ gửi một vế nên đọc ra "không lọc". Bằng chứng ngược đã nằm sẵn
trong số liệu: các stream này giảm 5.583→263, 957→36, 1.317→27 ở lần sync thứ
hai *trong khi connector chưa hề có lọc phía client* — chỉ server mới làm được
điều đó. `deal/get.activities` thì đúng là không lọc: sáu phép thử
(`last_update_stime`, `stime`, `since_stime`, mỗi tên thử đơn lẻ và theo cặp)
đều trả về đủ 16 bản ghi.

Sửa: thêm `Incremental.client_side`, phát `is_client_side_incremental: true`, và
chỉ bật cho `_INC_FEED`. Không bật ở nơi server đã lọc — một lượt lọc thứ hai
chỉ có thể bất đồng với server và làm rơi dòng.

Khoá bằng `test_stream_wiring_reaches_the_manifest`: cursor nào không gửi tham
số thì **bắt buộc** phải bật lọc phía client, và ngược lại. Bỏ cờ ra thì test đỏ
đúng ở `crm.deal_activity` — đã thử ngược.

Kết quả đo sau khi sửa, cùng một pipeline:

| Stream | Đầy đủ | Trước sửa | Sau sửa |
|---|---:|---:|---:|
| deal | 5.583 | 263 | 263 |
| **deal_activity** | 3.970 | **3.970** | **1.707** |
| pipeline_log | 1.389 | 0 | 0 |
| contact | 1.317 | 27 | 27 |
| account | 957 | 36 | 36 |

1.707 chứ không phải 0, và đó là đúng. State có 5.583 phân mảnh, trong đó 3.884
deal không có feed nào (cursor 0) và **1.699 deal có feed**. Cursor của Airbyte
lấy biên dưới theo kiểu bao gồm ở mức giây, nên mỗi phân mảnh phát lại đúng bản
ghi nằm trên mốc: 1.699 + 8 bản ghi mới thật = 1.707. `deal` cũng vậy — 263 ≈
một deal mới nhất cho mỗi pipeline có deal. Đây là trạng thái dừng bình thường
của Airbyte, và `append_dedup` ở đích khử trùng chúng.

Cái không sửa được bằng cursor: `deal_activity` vẫn gọi API đủ 5.582 lần mỗi lần
sync, vì router phân mảnh đọc stream cha độc lập. Lọc phía client tiết kiệm lượt
ghi vào kho, không tiết kiệm lượt gọi. Lý do không bật `incremental_dependency`
đã ghi ở mục dưới.

---

## Base CRM dựng lại theo tài liệu API thật (2026-08-27)

Bản đầu tôi dựng từ YAML và chỉ ship 2 stream. Ảnh tài liệu anh gửi cho thấy
**hai endpoint tôi tìm mãi không ra**: `account/service/all` và
`contact/service/all` — chỉ cần token + password, trả về danh mục service.
Vậy `service_id` **khám phá được**, không phải hardcode, và toàn bộ nhánh
account/contact mở ra. Connector giờ **10 stream**, không còn giá trị nào của
tenant khác.

### Cây stream

```
pipeline (270)                 account_service (29)      contact_service (22)
├─ deal (5.582)                ├─ account (957)          ├─ contact (1.317)
│  └─ deal_activity            └─ account_segment (16)   └─ contact_segment (5)
└─ pipeline_log (1.389)
```

`deal_activity` là **cháu** của pipeline (pipeline → deal → activity), và đó
chính là thứ trước đây phải bắn ra webhook n8n. Đã chạy được trong hệ thống:
`deal/get.activities` nhận deal id, trả `feeds`, và `user_id` hoá ra **tuỳ
chọn** — cùng 6 bản ghi dù có hay không, nên không bắt workspace phải nhập.

### Ba endpoint cố ý không ship, đã đo

| Endpoint | Lý do |
|---|---|
| `pipeline/get.stages` | Trùng khít `pipeline.cached_stages`. So từng trường trên 20 pipeline: khác **duy nhất** `token` — giá trị phù du mỗi response. Ship riêng = 270 lượt gọi/lần sync cho dữ liệu đã có. |
| `pipeline/get.segments` | Tương tự với `cached_segments`, giống hệt trên cả 15 pipeline đã kiểm. |
| `account/get.activities`, `contact/get.activities` | Trả về chuỗi thuần **`Function 1 is deprecated`** — không phải JSON, không phải mã lỗi. Đã bỏ. |

Khoá bằng `test_crm_does_not_ship_endpoints_whose_data_is_already_embedded`,
liệt kê theo **đường dẫn** chứ không theo tên stream — vì sai lầm dễ mắc là
thêm lại endpoint đó dưới một cái tên khác.

### Không endpoint nào lọc theo thời gian

`last_update_stime`/`etime` có trong tài liệu của `pipeline/deals`,
`account/list`, `contact/list`. Không cái nào áp dụng: gửi mốc năm 2030 vẫn
trả về đúng 41 / 260 / 333 bản ghi như không gửi. Cursor vẫn khai, để CDK lọc
phía client — đích chỉ nhận bản ghi đổi và `append_dedup` có cái để khử trùng.
**Tiết kiệm lượt ghi kho, không tiết kiệm lượt gọi API.**

Tài liệu cũng ghi `service_id` là optional cho `account/list`. Thực tế **bắt
buộc**: thiếu là `INVALID_ACCOUNT_SERVICE`.

### `deal_activity` đắt, và vì sao không tối ưu được

Một lượt gọi cho mỗi deal, đo được 0,35s → **~30 phút mỗi lần sync** ở quy mô
5.582 deal. `incremental_dependency` sẽ cắt được 20 lần, và **không an toàn**:
trong 121 deal có feed, **46 deal có feed mới hơn chính deal**, lệch tới 2
ngày. Bật lên thì những deal đó rời danh sách phân mảnh và hoạt động mới nhất
của chúng ngừng về.

Đáng ghi lại: mẫu đầu tiên tôi lấy 9 deal cho kết quả 9/9 cascade đúng, suýt
kết luận là an toàn. Mở rộng lên 300 deal thì hỏng 46/121. Mẫu nhỏ ở đây
không nói lên gì cả.

Stream này để người dùng tự chọn bật/tắt theo nhu cầu.

### Chạy thật qua sản phẩm

Cả 10 stream vào BigQuery, một lần chạy, **không retry** — heartbeat fix ở mục
trước giữ được. Số emit khớp tuyệt đối với số dòng đếm trong BigQuery:

| Stream | Bản ghi | | Stream | Bản ghi |
|---|---:|---|---|---:|
| deal | 5.583 | | pipeline | 271 |
| deal_activity | 3.970 | | account_service | 29 |
| pipeline_log | 1.389 | | contact_service | 22 |
| contact | 1.317 | | account_segment | 16 |
| account | 957 | | contact_segment | 5 |
| | | | **Tổng** | **13.559** |

```
21 phút, 1 attempt, SUCCEEDED
suite   510 passed, 37 skipped
```

---

## Base CRM, và câu trả lời cho vấn đề parent_id (2026-08-27)

### Vấn đề anh nêu: đúng, và đây là con số

Stream cha lọc theo `last_update` ra ít id, nhưng stream con vẫn nhận **đủ**
parent id. Tái hiện trên Base CRM (270 pipeline):

| | Lần 1 (không state) | Lần 2 (có state) |
|---|---:|---:|
| deal emit | 5.582 | **263** |
| gọi `pipeline/all` (cha) | 1 | 1 |
| gọi `pipeline/deals` (con) | 270 | **270** |

Emit giảm 95%, **số lần gọi API không giảm**. Nguyên nhân: cursor của cha thu
hẹp thứ nó *emit*, không thu hẹp thứ partition router *duyệt*.

CDK **có** cờ `incremental_dependency` trên `ParentStreamConfig` đúng cho việc
này. Đã thử bật:

* **Không giúp gì trên API này.** Bật lên: vẫn 263 record, vẫn 270 lần gọi —
  vì `pipeline/all` bỏ qua `last_update_stime`, trả đủ 270 bản ghi dù có lọc
  hay không, nên router chẳng có gì hẹp hơn để duyệt.
* **Bật lên ở chỗ nó có tác dụng thì mất dữ liệu.** Nó chỉ đúng khi cha được
  chạm mỗi lần con đổi. Base không làm vậy: **234/234** pipeline CRM có deal
  mới hơn chính pipeline, có cái lệch hơn một năm; WeWork tương tự, 41/42
  project. Bật cờ này thì các cha đó rời khỏi danh sách phân mảnh sau lần sync
  đầu, con ngừng về — mà mọi lần chạy vẫn báo xanh.

Cặp `workflow.workflow → workflow.stage` là chỗ duy nhất cha đang incremental,
và an toàn vì lý do ngược lại: 20/20 workflow đều mới ít nhất bằng stage mới
nhất của nó. Đã ghi vào docstring `Parent` và khoá bằng
`test_no_substream_asks_the_router_to_follow_the_parent_cursor`.

### Một bug nền tảng thật, tìm ra nhờ connector này

Lần sync đầy đủ đầu tiên của CRM **thất bại ở attempt 1** với
`activity Heartbeat timeout`, sau khi đã ghi 5.319 dòng; retry mới xong. Nguyên
nhân: `ACTIVITY_MAX_TIMEOUT_SECOND` của Airbyte mặc định **120 giây**, không đủ
cho stream con duyệt 270 phân mảnh.

Đã nâng lên 900s. Kiểm chứng: pipeline mới, state trắng, đọc đủ **5.852
record trong 1 attempt**, không retry. Khoá bằng
`test_the_replication_activity_is_given_longer_than_two_minutes`.

Đây là thứ sẽ đánh vào **mọi** substream lớn, không riêng CRM.

### Connector Base CRM

Dialect khác hẳn 10 app trước, mỗi điểm đều đo trên API thật:

| | Các app khác | Base CRM |
|---|---|---|
| Token | `access_token_v2` | `access_token` |
| Bí mật | chỉ token | token **+ password**, gửi mỗi request |
| Host | `<app>.base.com.vn` | `apis.base.vn` / `apis.basecrm.vn` |
| Cursor | `updated_from` trên query | `last_update_stime`/`etime` trong body |
| Phân trang | page bắt đầu 0 | bắt đầu **1**, gửi ngay request đầu |

`_shared.py` nhận thêm 5 nút xoay cho các khác biệt này
(`token_field`, `domains`, `schema_app`, `ConfigField.send_in_body`,
`Stream.first_page`/`page_on_first_request`) thay vì rẽ nhánh theo tên app.

Qua sản phẩm: check khoẻ, discover 2 stream, sync **5.852 record vào BigQuery**
(5.582 deal + 270 pipeline), đã đối chiếu bằng truy vấn.

### Bốn stream trong YAML **không** ship, và vì sao

* `contact`, `account`, `account_segment`, `contact_segment` cần `service_id`;
  YAML hardcode giá trị của **một tenant** (`248`, `['680','211']`). Thử với
  token này: `INVALID_CONTACT_SERVICE`. Không có endpoint nào liệt kê service
  của một token, nên không có gì để khám phá. Ship id của người khác nghĩa là
  mọi workspace có một stream chết ngay lần sync đầu.
* `deal_feed` trỏ tới `https://n8n.base-datateam.com/webhook/get-deal-feed`
  kèm `user_id` hardcode — máy chủ tự động hoá riêng, không phải API Base.
* `deal_activity` gọi `deal/get.activities` mà **không có** partition router,
  tức gọi endpoint cần deal id nhưng không truyền — trả về lỗi `998`.

### Một hạn chế của tính năng sửa Connection state (vòng trước)

Gửi `state: []` để **xoá** cursor không có tác dụng: Airbyte bỏ qua
`streamState` rỗng và giữ nguyên state cũ. Panel có so sánh gửi-vs-giữ nên
người dùng thấy cảnh báo "engine đã chuẩn hoá lại", nhưng thao tác "quên
cursor" cần dùng `connections/reset` của Airbyte chứ không phải ghi state
rỗng. Chưa nối vào sản phẩm.

```
suite   492 passed, 37 skipped
```

---

## Kiểm tra lịch chạy và incremental trên Base.vn (2026-08-27)

Đặt lịch `INTERVAL 300s` cho ba pipeline **workflow · wework · request**, để
chạy 4–5 vòng, rồi so từng stream. Kết luận: **không hề full refresh** — lịch
bắn đúng và incremental có hiệu lực.

### Lịch

Bắn đúng giờ (đặt 03:40:59, chạy 03:41:05), tự đặt lại `next_run_at`, không bỏ
vòng nào. Chu kỳ tính **từ lúc chạy xong** chứ không từ lúc bắt đầu, nên ba
pipeline lệch nhau dần — mỗi lần sync mất 4–10 phút.

### Incremental

| | Lần đầu (thủ công) | 4–5 lần theo lịch |
|---|---:|---:|
| workflow | 2.793 | 105 · 105 · 105 · 105 |
| wework | 4.368 | 401 · 401 · 401 |
| request | 286 | 21 · 21 · 21 · 21 |

Con số ổn định tuyệt đối vì Base không có dữ liệu mới trong lúc đo. Phần dư
giải thích được hết, không có chỗ nào là "đọc lại toàn bộ":

* **Stream INCREMENTAL thường** đọc đúng **1** bản ghi mỗi lần
  (`request.request`, `workflow.job`, `workflow.workflow`) — đó là bản ghi biên,
  vì `updated_from` của Base là **bao gồm cả mốc** (`>=`), không có cách diễn
  đạt "lớn hơn hẳn".
* **`wework.task` đọc 226/546.** Nó là substream trên 55 phân mảnh. Đo trực
  tiếp: gọi API với `updated_from = cursor` từng phân mảnh trả về **173** bản
  ghi, trong đó **0** thực sự mới; phần còn lại là `lookback_window` do CDK tự
  tính. Đích dùng APPEND_DEDUP nên không sinh bản trùng — tốn lượt gọi API,
  không sai dữ liệu.
* **Stream FULL_REFRESH đọc lại nguyên vẹn**, và đó là đúng: đã dò từng
  endpoint, `request.group`, `wework.dept`, `workflow.stage`, `wework.topic`
  đều **không có `last_update`** trên bản ghi hoặc endpoint bỏ qua
  `updated_from`.

### Một giả thuyết của tôi sai, và số đo bác bỏ

Thấy `wework.project` **có** `last_update` và endpoint **có** lọc, tôi kết luận
đây là cơ hội bị bỏ lỡ — rồi lại kết luận ngược: không được làm, vì `project`
là partition parent, incremental parent sẽ chỉ sinh phân mảnh cho project có
thay đổi và task của project không đổi sẽ ngừng được đồng bộ. Tôi viết cả một
test để chặn.

Test đó lập tức đỏ ở `workflow.workflow` — vốn **đang** vừa incremental vừa là
parent của `workflow.stage`. Đo thật: 20 workflow, tổng 103 stage, và connector
đọc đủ **103 mỗi lần chạy** trong khi parent chỉ emit 1. Vậy
`SubstreamPartitionRouter` đọc parent **độc lập** với cursor của parent — giả
thuyết sai, và test tôi vừa viết sẽ cấm một mẫu đang chạy đúng.

Đã bỏ test đó, thay bằng `test_every_substream_names_a_parent_that_exists` —
kiểm thứ thật sự làm mất dữ liệu: parent không tồn tại, hoặc không có trường để
bơm id vào. Comment trong `work.py` cũng sửa theo số đo: để `project` full
refresh **không phải để tránh hỏng**, mà vì lượt gọi API lấy danh sách project
vẫn xảy ra cho việc phân mảnh — chuyển sang incremental chỉ bớt 55 dòng emit,
không bớt một lượt gọi nào.

### Sau khi đo xong

Ba pipeline chuyển về `DAILY 08:00 Asia/Ho_Chi_Minh` — không để 5 phút/lần đập
vào tài khoản Base thật.

```
suite   484 passed, 37 skipped
```

---

## Thêm SQL Server làm connector mặc định (2026-08-27)

Catalogue: 7 → 9 connector Airbyte (tổng 19 mục chọn được cùng 10 connector
Base.vn). SQL Server có cả hai chiều, như BigQuery.

| | Pin | Mức | Đã chạy thật |
|---|---|---|---|
| `source-mssql` | 5.0.0 | **SUPPORTED** | check · discover (bảng + khoá chính) · full refresh 3 bản ghi |
| `destination-mssql` | 2.2.20 | **BETA** | check · ghi 3 dòng, đã query để xác nhận |

Kiểm chứng bằng SQL Server 2022 thật dựng trên mạng connector, đi qua **API sản
phẩm** chứ không gọi thẳng connector. Container thử nghiệm đã xoá sau khi xong.

### Vì sao destination để BETA

Sync **thành công** và dữ liệu **có tới nơi** — nhưng chỉ ở dạng JSON trong
`airbyte_internal.<schema>_raw__stream_<name>`. Không có bảng đã định kiểu.
Thử cả `1.0.0`, `2.0.0`, `2.2.20`: cả ba hành xử giống nhau, trong khi
`destination-postgres:2.0.10` trên **cùng nền tảng** vẫn dựng bảng typed
(`synced_29095.customers`). Vậy đây là hạn chế của connector, không phải của
nền tảng. Gọi nó SUPPORTED là hứa một bảng SQL Server mà người dùng không nhận
được.

### Hai giả định của tôi hoá ra sai, và sửa được nhờ đo

1. **"Destination phải cũ hơn refresh protocol."** Đúng với postgres/bigquery,
   **sai với mssql**: `2.2.20` khai `supportsRefreshes: true` nhưng chạy trơn
   trên 0.59.1 — nó khai mà không đòi ở runtime. Tôi đã pin 1.0.0 theo suy luận
   trước khi đo.
2. **Pin thấp hơn upstream có giá của nó.** Spec đóng gói lấy từ registry, mô tả
   bản **hiện tại**. Pin 1.0.0 nghĩa là form hỏi `user` và `load_type` trong khi
   connector muốn `username` và không có `load_type` — người dùng điền xong thì
   connector từ chối. Pin theo upstream làm spec khớp bản chạy, nên máy mới
   dùng được ngay mà không cần refresh spec.

Một lỗi của connector 1.0.0 gặp trên đường: thiếu `tunnel_method` thì nó báo
"Could not connect with provided SSH configuration" — nói về SSH trong khi
không ai yêu cầu tunnel.

### Kèm theo

`connector-lock.json` 8 mục (thêm `source-mssql` vì đã SUPPORTED) ·
icon `source-mssql.svg` / `destination-mssql.svg` đã vendor ·
`CONNECTOR_BETA_ALLOWLIST` trong `.env.example` thêm `destination-mssql` để máy
mới chọn được · `scripts/pull-engine-images.py` **tự nhận**, giờ pre-pull 10
image (suy ra từ catalogue, không có danh sách thứ hai).

Ba test khoá phạm vi catalogue đã cập nhật có chủ ý — đó chính là việc của
chúng. Nhân tiện sửa một comment đã lỗi thời trong `test_regressions.py` còn
ghi token Base bị `access_token_v2_invalid_3`, trong khi cả 10 app đã chạy được
9.049 bản ghi vào BigQuery.

```
suite    483 passed, 37 skipped
```

---

## Connection state sửa được, và một vòng review (2026-08-26)

### Sửa được con trỏ replication

`PUT /pipelines/{id}/state`. Panel giờ là editor thật: sửa JSON, nút bật khi
hợp lệ, xác nhận trước khi lưu. Lý do người ta mở panel này thường là **con trỏ
đang sai** — nguồn back-date bản ghi, backfill hỏng, một stream cần đọc lại từ
mốc đã biết. Chỉ đọc thì cách chữa duy nhất còn lại là full refresh toàn bộ.

Ba chốt an toàn:

1. **Từ chối khi pipeline đang chạy.** Lần chạy đó giữ bản sao con trỏ của nó
   và commit khi kết thúc, nên sửa giữa chừng sẽ bị ghi đè vài phút sau **mà
   không có lỗi ở đâu cả**. Người dùng sẽ kết luận tính năng hỏng, hoặc tệ hơn
   là sửa thêm lần nữa.
2. **Ghi nhật ký kèm giá trị trước.** `pipeline.state_edited` lưu cả trước lẫn
   sau. Khi vài ngày sau một lần sync nhân đôi hoặc bỏ sót bản ghi, đây là bản
   ghi duy nhất cho biết có người đổi mốc — và "trước đó là gì" là câu hỏi đầu
   tiên.
3. **Chặn shape sai ở schema.** Editor là ô JSON tự do; một mảng chuỗi parse
   được và vô nghĩa với mọi engine. Chặn ở đây (422) thay vì để nó chết trong
   job log vài giờ sau.

**Airbyte âm thầm chuẩn hoá thứ được ghi.** Đo thật: gửi kèm một khoá lạ trong
stream entry thì lưu thành công nhưng đọc lại không còn nó. Nên endpoint
**đọc lại từ engine** thay vì vọng lại request, và panel so sánh gửi-vs-giữ:
khác nhau thì báo "engine đã chuẩn hoá lại", chứ không nói "đã lưu" rồi hiển
thị nội dung khác.

### Review: bốn thứ hụt, đã sửa

| Vấn đề | Ảnh hưởng |
|---|---|
| Job History tải 50 nhưng hiện **tổng**, và bộ lọc chỉ lọc trong 50 đã tải | Với 684 job như ảnh Airbyte thì cả con số lẫn bộ lọc đều nói sai. Giờ lọc ở server, có "Tải thêm", và hiện `n / tổng`. |
| Menu ⋮ ở **hàng cuối tràn khỏi đáy màn hình** | Đo ở viewport 900×700: y=642 + h=105 > 700 — mục có tính phá huỷ nằm cuối danh sách chính là phần rơi ra ngoài. Menu giờ tự lật lên. |
| `pipelines.runQueued` **không có trong catalog** | Toast hiện ra đúng chữ `pipelines.runQueued`. Kế thừa từ trang cũ. Thiếu cả `starting` / `cancel_requested`. |
| Cổng i18n **không đọc được khoá nháy kép** | `keysOf` chỉ khớp `'key':`, nên 120 khoá tôi thêm vô hình: cổng đếm 836, báo "coverage OK", và chưa từng nhìn vào một trăm khoá trong đó. Giờ 956 khoá, và cổng bắt thêm loại lỗi thứ ba: **khoá được dùng trong code mà không catalog nào có** — đúng loại đã để `runQueued` lọt. |

```
suite                482 passed, 37 skipped
tsc --noEmit         sạch
i18n gate            956 vi / 956 en, 0 khoá lạ (đã kiểm chứng ngược)
walkthrough          8/8 kiểm tra ok, 0 lỗi console/HTTP
sửa cursor qua UI    round-trip + khôi phục sạch
menu ở viewport hẹp  không tràn (hàng đầu và hàng cuối)
```

---

## FE trang Connection dựng lại theo bố cục Airbyte (2026-08-26)

Giữ nguyên theme sáng của app, đổi bố cục theo Airbyte. Bốn tab thay cho bốn
tab cũ: **Trạng thái · Lịch sử chạy · Dữ liệu & cấu trúc · Cài đặt**.

| Màn | Trước | Sau |
|---|---|---|
| Header | tiêu đề + 4 nút hành động | tiêu đề, chip nguồn → đích (bấm được, có nhãn CUSTOM), lịch chạy, "Đồng bộ ngay", **công tắc BẬT/TẮT** |
| Trạng thái | 6 ô số tổng của cả pipeline | bảng **từng stream**: trạng thái, số bản ghi lần gần nhất, "dữ liệu mới tới", menu ⋮ |
| Lịch sử chạy | bảng 5 cột | một dòng một lần chạy, đọc như câu: `Đồng bộ thành công · 50.11 KB · 33 bản ghi · 37s` |
| Cấu trúc | bảng chỉ đọc | toggle bật/tắt, dropdown 4 chế độ đồng bộ, cursor, **modal ánh xạ trường Nguồn → Đích** |
| Cài đặt | phần lớn chỉ đọc | form sửa được, "Cấu hình nâng cao" thu gọn, vùng nguy hiểm, **panel Trạng thái replication** đọc cursor thật từ engine |

### BE: bốn chỗ phải chuẩn hoá để hứng

1. **`PipelineStreamView.last_sync`** — trạng thái/bản ghi/byte/thời điểm **theo từng stream**. Dữ liệu đã nằm trong `pipeline_stream_stats` từ đầu, ghi mỗi lần chạy, **chưa ai đọc**. Không có nó thì không trả lời được câu hỏi quan trọng nhất: pipeline báo SUCCEEDED nhưng một trong 25 stream đọc được 0 bản ghi.
2. **`schema_service.field_tree()`** — `field_list()` chỉ lấy tầng trên cùng, nên modal chỉ hiện `account_export: object` thay vì 22 cột thật sinh ra ở đích. Cây có `path` + `depth` tính ở server, không tách chuỗi theo dấu chấm ở browser — một trường tên thật là `a.b` sẽ bị vẽ như lồng nhau.
3. **`GET /pipelines/{id}/state`** — cursor replication. Endpoint riêng, tải khi mở panel: câu trả lời đến từ engine, mà trang Cài đặt phải render được cả khi engine không với tới.
4. **Tắt một stream không còn là xoá nó.** `_update` xoá mọi stream không được chọn, nên màn Cấu trúc **không thể** dùng đúng nghĩa: chỉ hiện được stream đang bật, "Ẩn luồng đã tắt" không bao giờ ẩn được gì, tắt một luồng là nó biến mất, bật lại phải chạy discovery. Giờ luồng bị tắt được giữ lại với `selected=false`; chỉ luồng **biến mất khỏi catalogue nguồn** mới bị xoá. Engine vẫn chỉ nhận luồng đang bật.

### Một chỗ tôi viết sai rồi sửa

Phiên bản đầu của `replication_state` khẳng định trong docstring rằng cột
`pipelines.sync_state` "chưa bao giờ được ghi". Sai — `runs.py:527` ghi nó, ở
chế độ **embedded**. Đúng là: `AIRBYTE_API` thì Airbyte giữ cursor và không trả
lại nên cột rỗng; `AIRBYTE_EMBEDDED` thì destination commit state về và cột
chính là câu trả lời. Giờ hỏi engine trước, thiếu thì lấy cột.

### Công cụ QA

Cả 6 script trong `qa/audit/` đã sửa (xem mục trước). Thêm
`qa/audit/connection-layout.mjs` đi hết 4 tab, mở menu ⋮, mở modal, bung panel
replication, chụp 6 ảnh và thu mọi lỗi console/HTTP. Nó từng **tự sửa dữ liệu**
— bấm nhầm nút toggle của hàng đầu tiên và tắt một stream thật; giờ chỉ đọc.

```
tsc --noEmit             sạch
suite                    479 passed, 37 skipped
walkthrough 4 tab        6/6 kiểm tra ok, 0 lỗi console/HTTP
```

---

## Base.vn → BigQuery: 10/10 app, và hai bug thật (2026-08-26)

Chạy qua **API sản phẩm** (`qa/e2e/base-to-bigquery.py`), Airbyte thực thi, rồi
hỏi thẳng BigQuery xem có gì. Domain `base.com.vn`, token nạp vào
`secrets/base-tokens.json` (gitignored, không có trong source).

| App | Bản ghi | Bảng BigQuery |
|---|---:|---:|
| account | 39 | 2 |
| hrm | 67 | 25 |
| hiring | 579 | 8 |
| workflow | 2.793 | 3 |
| request | 286 | 2 |
| service | 902 | 5 |
| wework | 4.368 | 6 |
| timeoff | 1 | 2 |
| payroll | 14 | 3 |
| income | 0 | 13 |
| **Tổng** | **9.049** | **69** |

`timeoff` và `income` ít/không có dữ liệu là **thật**, đã đối chiếu trực tiếp
với API: Timeoff có 1 group và 0 đơn nghỉ; Income trả `num_items: 0` cho toàn
bộ khoảng thời gian.

### Bug 1 — một record private giết cả sync (Hiring)

`stage/list` phân mảnh theo `opening_id`. Opening 386 là private, Base trả
HTTP 200 kèm `{"code": 0, "message": "This opening is private."}`.
`_error_handler()` coi **mọi** `code: 0` là chí mạng → stream chết → sync chết →
3 lần retry chết y hệt, sau khi đã ghi 579 bản ghi.

Sửa: thêm luật `IGNORE` **trước** luật `FAIL`, khớp một allowlist hẹp
(`PARTITION_REFUSALS`). Giữ hẹp là chủ ý — lý do `code: 0` phải fail ngay từ
đầu là vì coi nó như "rỗng" sẽ để một token bị từ chối ghi đè bảng của khách
bằng số không rồi báo thành công. Refusal lạ vẫn phải dừng ầm ĩ.

Sau khi sửa: 579 bản ghi, 8 bảng, 280s (trước đó fail sau 21 phút).

### Bug 2 — Income gửi tham số vào chỗ API không đọc

Income chạy trên `extapi/v1`, không phải `publicapi/v2`. Đo trực tiếp:

```
?updated_from=0                        -> code 0, "Updated from param is required"
body updated_from=0                    -> code 0, "Updated to param is required"
body updated_from=0 & updated_to=now   -> code 1
```

Connector gửi `updated_from` vào **query string** và không gửi bound đóng. Base
báo "thiếu tham số" trong khi tham số **đang được gửi** — tới chỗ ứng dụng đó
không đọc. `Incremental` giờ có `inject_into` và `end_param`; chỉ Income dùng
`body_data` + `updated_to`, chín app còn lại giữ nguyên query mở — có test chặn
cả hai chiều, vì gửi bound đóng cho `publicapi/v2` sẽ âm thầm cắt mọi sync tại
thời điểm nó bắt đầu.

### Một lỗi công cụ QA

Cả 6 script duyệt trình duyệt trong `qa/audit/` **không chạy được** kể từ khi
bộ test dời sang `qa/`: ESM resolve `playwright` theo thư mục của **file**, chứ
không theo cwd, nên hướng dẫn "chạy từ `frontend/`" trong chính các file đó là
vô nghĩa. Đã sửa bằng `createRequire` trỏ vào `frontend/node_modules`.

Chạy được rồi thì đường FE **không có lỗi**: dropdown domain, test kết nối
SUCCESS, lưu source thành công, 0 lỗi console.

### Thêm `--updated-from`

`base-to-bigquery.py` hardcode `updated_from: "0"` (toàn bộ lịch sử). Giờ nhận
`--updated-from 90d` / `12h` / epoch để giới hạn dữ liệu khi cần.

Suite: 475 passed, 37 skipped.

---

## Xoá sạch Docker rồi dựng lại từ zero (2026-08-26)

Xoá toàn bộ container / volume / network / image build của `appbi-pipeline`,
lọc theo **label compose project** chứ không theo tên — trên máy này có
`appbi-ai_db_data`, `appbi-workboard_db_data`, `appbi-integration_postgres_data`
thuộc project khác, khớp tên `appbi-*` nhưng không được đụng. `appbi-ai` vẫn
chạy nguyên 4 container suốt quá trình.

### Ba lỗi chỉ lộ ra khi volume trống

Lần dựng lại đầu tiên **thất bại**. Cả ba lỗi chỉ cắn trong lúc Postgres chạy
script `/docker-entrypoint-initdb.d`, mà việc đó xảy ra đúng một lần cho mỗi
data directory — nên một stack đã restart hàng trăm lần vẫn có thể không khởi
động nổi từ số 0.

1. **Healthcheck Postgres dò sai socket.** `pg_isready` không có host thì đi qua
   unix socket, mà image chính thức giữ một server tạm lắng nghe ở đó *trong
   lúc* init. Probe xanh giữa chừng init → `service_healthy` bắn sớm → mọi thứ
   phía sau khởi động vào một DB đang trả lời `the database system is starting
   up`. Temporal chết vì cái này: nó tạo 2 database của mình lúc boot đầu, lệnh
   `create` fail, rồi Airbyte server exit 1 ở bước kiểm tra schema — thông báo
   lỗi không nhắc gì tới Postgres hay init. Sửa bằng đúng một cờ:
   `pg_isready -h 127.0.0.1`.

2. **`airbyte-server` không hề depends_on `airbyte-temporal`.** Nó mở Temporal
   client lúc wiring bean và exit nếu không nối được. Nó chạy được bao lâu nay
   là **nhờ may**: chuỗi `storage-init → minio` làm nó chậm đủ để Temporal kịp
   lên. Tôi gỡ MinIO là gỡ luôn độ trễ đó, và cold start kế tiếp fail với
   `UnknownHostException: airbyte-temporal` — trông như lỗi mạng chứ không phải
   thiếu một cạnh trong đồ thị phụ thuộc.

3. **`service_started` không phải là sẵn sàng.** Temporal mở cổng 7233 trước
   khi tạo xong schema. Phải có healthcheck riêng: `tctl cluster health`, và
   probe vào `$(hostname -i)` chứ không phải `127.0.0.1` — entrypoint bind
   Temporal vào IP container nên probe loopback bị refuse bởi một server hoàn
   toàn khoẻ mạnh (tôi đã mắc đúng lỗi này ở lần sửa thứ hai).

Cả ba đều có test đọc thẳng file compose, và đều đã kiểm chứng ngược.

### Kết quả

```
docker compose down -v && docker compose up -d   8/8 healthy, 4m31s
golden path trên stack vừa dựng                  2.500 record
restart (giữ volume)                             27s, 8/8 healthy
golden path sau restart                          2.500 record
suite                                            472 passed, 37 skipped
```

### Tốn bao nhiêu

| | Trên đĩa | Tải về |
|---|---|---|
| Engine Airbyte (5 image) | 6,99 GB | 2,26 GB |
| Connector (8 image) | 8,42 GB | 2,56 GB |
| postgres · nginx · python | 0,64 GB | 0,16 GB |
| Build tại chỗ (backend, frontend) | 0,74 GB | — |
| Volume sau một lần sync | 0,12 GB | — |
| **Tổng** | **~16,9 GB** | **~5,0 GB** |

Hai cột lệch nhau vì image lưu dạng giải nén nhưng tải về dạng nén. Cột "trên
đĩa" còn là phép cộng chưa trừ layer dùng chung giữa các image Airbyte, nên
thư mục thật nhỏ hơn 16,9 GB.

RAM idle ~2,1 GB cho 8 container, trong đó server + worker của Airbyte chiếm
1,5 GB. `vendor/engine/` thêm 2,3 GB nếu giữ archive offline — không cần để
chạy, chỉ cần khi Docker Hub không với tới.

---

## Giới hạn connector khi pull, và dọn tiếp (2026-08-26)

### Chỉ pull 8 image, không phải 600

Engine offer 600+ connector (bootloader seed catalogue hiện tại của Airbyte).
Sản phẩm offer 7, cộng 1 runner. Script pre-pull giờ **suy ra** danh sách từ
`backend/app/resources/connector_registry.json` — đúng cái wizard hiển thị —
nên không có danh sách thứ hai để lệch:

```bash
python scripts/pull-engine-images.py     # 8 image, chạy được khi stack đang tắt
```

Trước đó nó giữ `WANTED` hardcode **4 repository** trong khi sản phẩm ship 8:
`source-bigquery`, `destination-bigquery`, `source-google-sheets` và
`destination-google-sheets` chưa bao giờ được pre-pull, nên mỗi cái đều treo ở
sync đầu tiên bên trong job — nơi timeout hiện ra thành `ENGINE_UNAVAILABLE`
và trông như engine hỏng chứ không phải cache lạnh. Test giữ:
`test_the_prepull_set_is_exactly_the_product_catalogue` (kiểm chứng ngược: bỏ
một connector khỏi nguồn thì test đỏ).

Đọc từ registry chứ không phải từ `connector-lock.json`, vì lock **cố ý** chỉ
phủ SUPPORTED — `destination-google-sheets` là BETA nhưng vẫn chọn được, nên
vẫn phải có sẵn trên máy.

### Xoá

| Việc | Lý do |
|---|---|
| `scripts/pull-connectors.sh` | Trùng việc với script Python, **và đang hỏng**: nó grep `RUNNER_VERSION` trong `builder.py` nhưng định nghĩa đã chuyển sang `builder_manifest.py`, nên ghép tag rỗng và `set -e` abort. Chạy thử: exit 1, không output, không pull gì. |
| `airbyte/destination-postgres:3.0.17` | 918 MB. Bản 3.x là bản làm hỏng sync trên platform 0.59.1 — cái mà pin tồn tại để tránh. |
| `airbyte/destination-bigquery:3.0.22` | 1.62 GB. Cùng lý do. |
| `.cache/oss_registry.json` | 6.2 MB, cache tải về, tự sinh lại. |

Còn đúng 13 image Airbyte trên máy = 5 engine + 8 connector.

### Về độ nặng khi pull sang máy khác

`git gc` xong: `.git` còn **5.2 MB** (working tree tracked chỉ 2.3 MB / 238
file). Clone đã nhẹ — JSON nén rất tốt, kể cả bản `oss_registry.json` cũ còn
trong history. Thứ nặng thật không nằm trong git mà là Docker image, và đó là
cái vừa giới hạn ở trên.

`vendor/engine/` 2.3 GB vẫn git-ignore, đi theo thư mục dự án chứ không theo
git — nó tồn tại để engine dựng lại được khi Docker Hub không với tới.

### Một sự cố tôi gây ra, nói thẳng

Tôi chạy `git checkout --` lên `compatibility.yaml` và
`qa/backend/test_operations.py` để hoàn nguyên một thay đổi của chính mình, và
việc đó **xoá luôn phần chưa commit** của hai file — không chỉ phần tôi sửa.
Git không giữ bản nào vì chúng chưa từng được stage.

Đã dựng lại từ bằng chứng có thật, không suy đoán: bảng connector trong
`docs/CURRENT_STATUS.md` (164 stream BigQuery, 50 record, 30 record Sheets, lý
do `destination-google-sheets` để BETA) và các test tự định nghĩa yêu cầu. Suite
xanh trở lại: 470 passed / 37 skipped. Hai điểm bản HEAD sai mà nay đúng:
`destination-postgres` pin `2.0.10` chứ không phải `3.0.17`, và bỏ
`refresh_generations: true` — 2.0.10 có trước refresh protocol, khai nó là khai
đúng cái hành vi mà pin sinh ra để tránh.

**Còn 717+ thay đổi chưa commit và chưa có git remote.** Nên commit sớm.

---

## Thu gọn stack và dọn dự án (2026-08-26)

### Docker: 10 container → 8

| Bỏ | Vì sao |
|---|---|
| `airbyte-cron` | 513 MB. Job duy nhất của nó (`WorkspaceCleaner`) **lỗi ở mọi lần chạy** vì `/tmp/workspace` không được mount vào. Nó còn chạy definitions updater — tranh quyền quyết định version connector với `airbyte-connector-pin`. |
| `airbyte-minio` | 124 MB. Object storage có trong compose gốc để server và worker đọc chung job log. Ở đây hai container **đã** mount chung volume `airbyte_workspace`, nên `STORAGE_TYPE: LOCAL` ghi vào chỗ cả hai đều thấy. |
| `airbyte-storage-init` | Chỉ tồn tại để tạo bucket cho MinIO. |

RAM: ~2.3 GB → ~1.5 GB. Archive `vendor/engine/`: 2.9 GB → 2.3 GB (bỏ 3 tar
không còn dùng). `engine-lock.json`: 8 → 5 image.

Mỗi lần bỏ đều verify lại bằng golden path đầy đủ — 2.500 record, sync
incremental, và cancel về `CANCELLED` — chứ không suy luận. Chi tiết:
[engine.md](engine.md).

### Một bug thật lộ ra khi làm việc này

Sync liên tục chết với `getGenerationId(...) must not be null` **sau khi**
`airbyte-connector-pin` đã báo pin `destination-postgres` về 2.0.10 thành công.
Pin có thật. Có thứ khác undo nó — và thứ đó là **chính sản phẩm**.

`connector_definitions` có hai cột version: `version` là cái sản phẩm pin,
`engine_version` là cái quan sát được từ engine. `seed_catalog` chỉ ghi
`version` bên trong nhánh `spec_source == "BUNDLED"`, nên ngay khi spec của một
row được đọc lại từ engine (`spec_source` thành `ENGINE`), pin không bao giờ
áp dụng cho row đó nữa. Bootloader của engine re-seed từ catalogue *hiện tại*
mỗi lần khởi động; drift đó bị copy vào DB sản phẩm một lần rồi trở thành câu
trả lời của chính sản phẩm, và `_ensure_definition_version` đẩy nó ngược lại
engine mỗi lần tạo resource — vài phút sau khi pin chạy.

Version pin không phải là một phần của spec. Đã sửa: `existing.version` ghi vô
điều kiện. Test giữ:
`test_an_engine_sourced_spec_does_not_freeze_the_version_pin` — đã kiểm chứng
ngược (revert fix thì test báo 3.0.17).

### Dọn dự án

Root: 40 mục → 19.

| Việc | Chi tiết |
|---|---|
| Key GCP ở root | `base-testlab-01-*.json` (private key thật) chuyển vào `secrets/`. Đã gitignore từ trước, chưa từng bị commit. |
| 7 file `evidence-*.json` rác | Untracked, đều là output sinh lại được — xoá. |
| `evidence/` trùng lặp | `backend/evidence/` gộp vào `evidence/`. |
| Tài liệu ở root | `CURRENT_STATUS.md`, `PRODUCTION_READINESS_REVIEW.md` → `docs/`; BA spec → `docs/spec/`. Đã rewrite toàn bộ link, 0 link gãy. |
| 24 database test bỏ quên | 211 MB trong Postgres. Xoá, **và sửa nguyên nhân**: `qa/backend/scratchdb.py` + fixture session-scope trong `conftest.py` tự drop khi hết run. Sau khi chạy full suite: còn 0. |
| Credential chết | `secrets/airbyte-app-base-cert.json` — Application credential của kind cluster đã bị xoá, không code nào đọc. |
| Runbook chỉ lệnh không chạy được | `RUNBOOK-engine-upgrade.md` còn hướng dẫn gọi 2 script và values file đã xoá cùng đường Kubernetes. Giữ lại phát hiện, bỏ lệnh. |
| `--into-kind` chết | Gỡ khỏi `scripts/pull-engine-images.py` (103 → 79 dòng) cùng phần docstring nói sai về ai sở hữu version connector. |
| `RUNBOOK-backup-restore.md` sai chỗ | Bảng ghi job log nằm ở "MinIO / S3" — giờ là volume `airbyte_workspace`. |
| Cache | `.cache/` (6.2 MB) untrack + gitignore; `__pycache__`/`.pytest_cache` xoá. |

### Một điều chưa đóng, nói thẳng

Trên máy **hoàn toàn mới**, sync đầu tiên có thể vượt quá ngân sách chờ 900s
của bộ e2e — không phải vì hỏng, mà vì worker kéo image connector (mỗi cái vài
trăm MB) đúng lúc job cần. Lần chạy nguội đo được đã hết 900s và bộ test báo
`FAILED: RUNNING`; chạy lại khi image đã có thì pass đầy đủ. Cách xử lý và lệnh
pre-pull nằm trong [engine.md](engine.md) mục "First sync on a cold machine".
Tôi cố ý **không** thêm container pre-pull vào stack: nó sẽ cộng lại đúng cái
container vừa bỏ đi, để tiết kiệm một lần chờ duy nhất trên mỗi máy.

---

## Engine: Airbyte 0.59.1 vendored, Kubernetes bỏ hẳn (2026-08-26)

Owner quyết định: đưa Airbyte vào ngay trong dự án, chạy chung Docker, có source
trên máy để **kiểm soát được sản phẩm về sau**. Tôi đã nêu các đánh đổi và owner
xác nhận — đây là quyết định về quyền kiểm soát, không phải về kỹ thuật thuần.

Chi tiết đầy đủ: [docs/engine.md](engine.md).

### Một điều chỉnh quan trọng so với ý ban đầu: 0.59.1 chứ không phải 0.63

Repo này **đã từng test thật** các bản image, và 0.63 là bản tệ nhất có thể chọn:

| Bản | Trong Docker Compose |
|---|---|
| 1.8.5 | bootloader cần namespace k8s hai lần; lần thứ hai không có flag tắt |
| 0.64.7 | control plane chạy tốt, nhưng **mọi connector job đi qua workload launcher** → resolve `kubernetes.default.svc`, không có Docker mode |
| **0.59.1** | trước khi có workload launcher; worker chạy connector container thẳng trên Docker daemon |

Workload launcher xuất hiện từ dòng 0.63. Chọn 0.63 sẽ được kết quả tệ nhất:
**khởi động lên trông khỏe mạnh và không sync nào chạy được.**

### Cái giá phải trả, nói thẳng

Destination connector **phải cũ hơn refresh protocol** của Airbyte. Bản nào khai
`supportsRefreshes` đều cần `generationId`, mà platform 0.59.1 không gửi:

```text
BeanInstantiationException: PostgresWriter
Caused by: NullPointerException: getGenerationId(...) must not be null
```

| Connector | Upstream | Pin ở đây |
|---|---|---|
| `destination-postgres` | 3.0.16 | **2.0.10** |
| `destination-bigquery` | 3.0.22 | **2.4.19** |

Nâng destination vượt qua ranh giới đó = phải nâng platform = phải dùng
Kubernetes. Đó là toàn bộ đánh đổi, gói trong một câu.

### Cái bẫy cắn hai lần

Bootloader của 0.59.1 seed connector definition từ catalog **hiện tại** của
Airbyte, và làm thế **mỗi lần khởi động**. Nên một platform từ 2024 khởi động lên
lại đang chào bán connector mới ra tháng này. Không có cảnh báo nào: definition
trông khỏe, `check` pass, và lỗi chỉ xuất hiện lúc replication.

Pin bằng tay xong thì `docker compose up` lần sau xoá sạch — tôi dính đúng hai
lần trước khi hiểu ra.

Giải pháp: service `airbyte-connector-pin` trong stack, chạy sau khi server
healthy và áp lại `connector-lock.json` vào engine. `api` và `worker` đều
`depends_on` nó với `service_completed_successfully`, nên sản phẩm không thể khởi
động trên một engine chưa pin.

### Giữ được engine khi upstream gỡ

`docker pull` không phải chuỗi cung ứng — nó là quyết định của người khác về việc
giữ lại cái gì.

- `engine-lock.json` ghi digest của cả 8 image, **có commit**
- `vendor/engine/*.tar` chứa bytes thật, **3.0 GB**, gitignore, đi theo thư mục dự án
- `scripts/vendor-engine.py restore` nạp lại trên máy chưa từng pull

Đã chứng minh chứ không phải giả định: xoá `minio/mc` khỏi daemon, restore từ
archive, digest khớp lock.

`container-orchestrator` nằm trong lock mà không nằm trong Compose file — worker
spawn nó theo từng job, nên máy thiếu nó vẫn khởi động sạch rồi chết ở sync đầu
tiên.

### Đã dọn

| Xoá | Vốn là |
|---|---|
| kind cluster `appbi-base-cert` | cluster test của dev |
| 7 container Airbyte + volume | stack cũ đang Exited |
| `deploy/kubernetes/airbyte/` | Helm values, external Postgres, network policy |
| `docker-compose.k8s-cert.yml`, 3 script Helm/k8s | máy móc quanh Airbyte-trên-k8s |
| CI lane `airbyte-k8s-contract` | chứng nhận Airbyte trên Kubernetes |
| 41 connector image không dùng | ~27 GB |
| build cache | 21 GB |

Giữ lại `deploy/kubernetes/base` + `overlays/production`: chúng deploy **AppBI**,
vẫn hợp lệ; engine khi đó là một URL bên ngoài.

**Không đụng volume của 7 project khác** trên cùng daemon (`appbi-ai`,
`open-metadata`, `rivalpulseai`, `tm-ai-*`...). 33.8 GB volume "reclaimable" phần
lớn thuộc về họ, không phải của mình để xoá.

### Verification

```text
một lệnh khởi động lạnh   docker compose down && up -d -> 10 container
deep readiness            200, engine_type AIRBYTE_API, engine ok
golden path               2.500 record (500 customers + 2.000 orders)
incremental               lần hai đọc 1 record mỗi stream
cancel                    CANCEL_REQUESTED -> terminal CANCELLED
engine images             8/8 khớp engine-lock.json
archive restore           xoá image khỏi daemon, restore, digest khớp
backend suite             468 passed, 37 skipped
```

**Cancel đạt terminal `CANCELLED` là điều mới.** Embedded runner chưa bao giờ
chứng minh được — sync luôn xong trước khi cancel kịp — và đây là acceptance gap
PM để mở suốt nhiều vòng. Platform thật đóng được nó.

---

## Base.vn connectors: domain, icons và giao diện setup (2026-08-25)

### Token không hỏng — sai domain

`base.vn` và `base.com.vn` là **hai bản cài riêng, tài khoản tách biệt**. Token
của bản này bị bản kia từ chối bằng `access_token_v2_invalid_3` — đúng thông
báo mà token hết hạn cũng trả về. Vòng trước tôi kết luận "token bị từ chối" là
đúng triệu chứng nhưng sai nguyên nhân, và tôi đã **bỏ `domain` khỏi config**
với lập luận host thuộc về sản phẩm. Lập luận đó sai.

`domain` giờ là config field của cả 10 connector, mặc định `base.vn`, và là
**dropdown** chứ không phải ô text — hai lựa chọn, không gõ nhầm được. Panel tài
liệu cạnh form nói thẳng điều này, vì kiểu hỏng ở đây là một token đúng trông
như bị thu hồi.

Phần còn lại của config HRM cũ, `version`, vẫn bỏ: cho workspace đổi `extapi/v1`
chỉ khiến họ trỏ vào một API mà các stream này không viết cho.

### Kết quả test thật trên base.com.vn

```text
107/107 checks passed

spec        10/10   manifest khai access_token_v2 + domain
check       10/10   Base chấp nhận token
bad-token   10/10   token hỏng bị TỪ CHỐI
discover    10/10   69/69 stream, tất cả có primary key
read        16 stream có dữ liệu; 21 stream rỗng trong tài khoản test
incremental  3/3 nơi có dữ liệu để chạy cursor
```

| Stream | Kết quả |
|---|---|
| `workflow.job` | **2.670 record**, 2.670 id duy nhất, 0 trùng — 6 trang × 500 |
| `request.request` | 266 record |
| `wework.project` | **55 project** — bản hardcode id cũ trả về 1 |
| `hiring.interview` | 40 record |

Incremental (lần 1 → lần 2): `request` **266 → 1**, `workflow` **20 → 1**.

Pagination kiểm trực tiếp: `limit` được tôn trọng tới 500, `page_id` đếm từ 0,
hai trang liên tiếp không trùng id, trang quá cuối trả mảng rỗng.
`updated_from` lọc đúng và tăng dần: 2.670 → 75 (từ 2024) → 2 (từ 01/2026) →
1 (từ 06/2026).

**Một lỗi của chính bộ test, đáng ghi lại:** `subprocess.run(text=True)` decode
theo locale; trên Windows là cp1252, nên ký tự tiếng Việt đầu tiên trong record
Base làm chết reader thread và trả về output rỗng. Mọi lệnh `read` báo
"0 record" cho stream đang trả về hàng nghìn dòng — **và báo là PASS**. Bộ test
nói dối suốt một lượt chạy đầy đủ trước khi bị phát hiện. Giờ decode UTF-8
tường minh, và một read trả 0 record được tính là *inconclusive* chứ không phải
pass.

### Giao diện: đã test bằng browser thật, không phải đọc code

Dựng panel tài liệu cạnh form theo đúng kiểu Airbyte: trái là cấu hình, phải là
hướng dẫn. Panel chứa thứ form không nói được — lấy token ở đâu, connector này
sinh ra những bảng nào, link tài liệu API. Nó **không** lặp lại mô tả từng
trường, vì form đã hiển thị ngay dưới ô nhập rồi. Màn hình hẹp thì panel xếp lên
trên form.

Chạy Playwright thao tác như user tìm ra những thứ đọc code không thấy:

| | |
|---|---|
| **10 lỗi 404 icon** | mỗi connector Base một cái, ngay màn đầu của wizard. 8 icon lấy từ repo `n8n-nodes-basevn-*`; Account và Hiring không có repo icon nên tự vẽ theo đúng phong cách bộ đó và ghi chú rõ |
| **Ô domain render thành textarea** | form chọn `<textarea>` khi `description.length > 140` — **độ dài help text quyết định loại ô nhập**, nên viết giải thích cẩn thận làm form xấu đi |
| **Help tiếng Anh dưới label tiếng Việt** | toàn bộ mô tả field đã viết lại tiếng Việt |
| **Markdown hiện nguyên dấu backtick** | form render help dạng plain text |
| **`v7.28.2` dưới "Base HRM"** | tag của manifest runner, vô nghĩa với user và dễ bị hiểu là version của Base. Giờ hiện "25 bảng dữ liệu" |
| **Placeholder `Production Postgres`** | trên form Base HRM. Giờ lấy theo connector |
| **Panel tài liệu chui xuống dưới footer** | HRM 25 stream nên panel cao hơn viewport; giờ tự cuộn bên trong |

Hai script giữ cho việc này không tái diễn, đều ở `qa/audit/`:
`connector-form.mjs` (đếm console error, đọc lại label/help thực render, kiểm
tràn ngang trên mobile) và `base-source-journey.mjs` (tạo source Base đúng như
user: chọn connector → nhập tên → dán token → chọn domain → test → lưu).

Lần chạy cuối: dropdown chọn `base.com.vn`, test trả **Kết nối thành công**,
source lưu thành công, console **0 lỗi** (trước đó 22).

### Verification

```text
backend pytest              476 passed, 37 skipped
Base connector structural   162 passed
Base connector live         107/107 trên base.com.vn
FE lint / typecheck / i18n  pass / pass / 829-829
browser walkthrough         0 console error, 0 tràn ngang trên mobile 390px
```

---

## Base.vn connectors (2026-08-25)

Mười connector cho hệ sinh thái Base.vn, **native trong source code** chứ không
import từ ngoài. Clone repo sang máy khác, chạy lên, chúng đã có trong catalog —
không phải import YAML, không phải kéo connector từ Airbyte, không phải cấu hình
lại gì.

Chi tiết đầy đủ: [base-connectors.md](base-connectors.md) và
[base-connectors-inventory.md](base-connectors-inventory.md) (file thứ hai
được **sinh ra từ code**, nên không thể lệch với thực tế).

| Connector | Stream | | Connector | Stream |
|---|---:|---|---|---:|
| `source-base-hrm` | 25 | | `source-base-service` | 5 |
| `source-base-income` | 13 | | `source-base-workflow` | 3 |
| `source-base-hiring` | 8 | | `source-base-payroll` | 3 |
| `source-base-wework` | 6 | | `source-base-account` / `request` / `timeoff` | 2 mỗi cái |

69 stream: 42 giữ nguyên, 25 cải thiện, 2 mới, 4 bỏ.

**Kiến trúc.** `backend/app/connectors/base_vn/` — `_shared.py` giữ toàn bộ
"phương ngữ" của Base API (auth, pagination, cursor, error), mỗi module là một
nhóm ứng dụng. Compile ra Airbyte declarative manifest lúc import; registry ghép
chúng vào catalog chung nên phần còn lại của sản phẩm không phân biệt chúng với
connector Airbyte.

**Dùng chung logic, tách credential.** Một row `connector_definitions`, một
manifest, và `seed_catalog()` **ghi đè** manifest đó từ code mỗi lần deploy — đó
chính là điều làm cho "sửa một lần, mọi workspace đều có" là thật. Token thì nằm
riêng theo từng source của từng workspace, trong kho mã hoá, và tới connector qua
`access_token_v2`. Có test assert không có token nào nằm trong source.

### Tám lỗi trong YAML cũ, đã sửa

| | |
|---|---|
| **Sai tên credential** (cả 10) | YAML gửi `access_token`. Base phân biệt rõ: `access_token_invalid_2` cho tên đó, `access_token_v2_invalid_1` cho token sai định dạng, `_invalid_3` cho token đúng định dạng nhưng bị từ chối. Nghĩa là **không manifest cũ nào authenticate được** với token hiện tại |
| **Thất bại trông như thành công** (cả 10) | Base trả **HTTP 200** kèm `{"code": 0}` khi từ chối. Không manifest nào đọc `code`, nên token hết hạn → collection rỗng → sync "thành công" → destination full-refresh **xoá sạch bảng của khách** và báo OK |
| **Thiếu pagination** (17 stream) | HRM paginate 8/25. `employee/list` không nằm trong đó — tài khoản lớn hơn page mặc định là **mất người, không báo lỗi**. `income.income_payment` còn gửi `limit: 500` với pagination tắt |
| **WeWork đọc đúng 1 project hardcode** | `request_body_data: {id: "131471"}`. Mọi sync WeWork của mọi khách chỉ trả về project đó. `project/list` có tồn tại và chưa từng được dùng |
| **Stream thừa** | `service.test` trùng hệt `service.ticket` (crawl toàn bộ ticket hai lần); `payroll.test` gọi `GET /test` rỗng; `income.income_inflow` + `inflow_income` đọc lại đúng entity từ endpoint không sở hữu nó |
| **Cursor lọc sai thứ** | `timeoff` theo dõi `last_update` nhưng lọc `start_date_from` — đơn nghỉ tạo năm ngoái duyệt hôm nay bị bỏ sót. `hrm.employee` khai cursor mà không có request param nào |
| **8 stream không có primary key** | Gồm `service.ticket`, bảng lớn nhất của ứng dụng đó |
| **HRM bắt workspace khai host** | `https://hrm.{{ config['domain'] }}/extapi/{{ config['version'] }}` — gõ nhầm là trỏ đi chỗ khác |

### Kết quả test thật

```text
structural   160/160   (qa/backend/test_base_connectors.py)
spec          10/10    manifest khai đúng access_token_v2
bad-token     10/10    token hỏng bị TỪ CHỐI, không im lặng trả rỗng
discover      10/10    69/69 stream, tất cả đều có primary key
check          0/10    mọi token đều bị Base từ chối
```

### Token không dùng được — cần cấp lại

Cả 10 token đều bị Base từ chối giống hệt nhau:

```text
POST account.base.vn/extapi/v1/units   access_token_v2=2329~PaOq…
->  200 {"code": 0, "message": "access_token_v2_invalid_3"}
```

Đã loại trừ trước khi kết luận: sai tên field cho message **khác**
(`access_token_invalid_2`); bỏ tiền tố `2329~` cho `_invalid_1`; body/query/
header/`~` mã hoá hay không đều như nhau; và cả 10 token đều thế, mỗi cái với
đúng ứng dụng của nó. `_invalid_3` là mã của Base cho token **đúng định dạng
nhưng không được chấp nhận** — hết hạn, bị thu hồi, hoặc cấp cho tài khoản khác.

Xin token mới; ngay khi có, `python qa/e2e/base-connectors.py --read
--incremental` sẽ chạy nốt phần chưa chứng minh được: check thật, đọc record,
pagination dưới dữ liệu thật, và state qua hai lần sync.

---

## Dọn dẹp: launch scope 7 connector, và tách test khỏi code sản phẩm (2026-08-25)

Theo yêu cầu owner: xoá sạch Sources/Destinations đang có, thu catalog về đúng
ba hệ thống cần dùng, và tách phần test/nháp ra khỏi code chạy dự án.

### Catalog: 654 → 7

| | Source | Destination |
|---|---|---|
| PostgreSQL | ✅ SUPPORTED | ✅ SUPPORTED |
| BigQuery | ✅ SUPPORTED | ✅ SUPPORTED |
| Google Sheets | ✅ SUPPORTED | ⚠️ BETA |
| Sample Data | ✅ SUPPORTED | — |

`destination-google-sheets` để BETA chứ không SUPPORTED: `spec` chạy được nhưng
chưa có `check` nào chạy với một spreadsheet thật, vì ghi cần OAuth grant hoặc
một sheet được share quyền write. Owner đã nói biết trước hai cái Google chưa
connect được — nhãn trong sản phẩm nói đúng như vậy thay vì ngụ ý ngược lại.
Nó vẫn chọn được, qua `CONNECTOR_BETA_ALLOWLIST`.

**Dung lượng bỏ đi:**

```text
connector_registry.json   2.1 MB  ->  62 KB
connector_icons/          4.0 MB  ->  192 KB   (572 file -> 6)
connector_definitions     654 row ->  7 row
wizard payload (source)   503 KB  ->  3.5 KB   (0 card bị khoá)
wizard payload (dest)     47 KB   ->  2.5 KB
```

**Hai thay đổi để việc này không tự quay lại:**

- `build-connector-registry.py` mặc định chỉ phát ra launch scope. `--full-catalog`
  vẫn có, cho deployment nào quyết định đứng sau nhiều hơn — nhưng mặc định phải
  là thứ sản phẩm này thật sự hỗ trợ được.
- `seed_catalog()` trước đây chỉ insert/update, không bao giờ xoá. Nghĩa là thu
  gọn bundle vẫn để lại 654 row trong database — catalog lặng lẽ trở thành hợp
  của mọi phiên bản từng deploy. Giờ có prune, với hai ngoại lệ: connector do
  Builder tạo (`spec_source != BUNDLED`) không phải của mình để xoá, và connector
  còn resource đang dùng thì bị **disable** kèm lý do chứ không xoá — xoá sẽ làm
  mồ côi một kết nối đang chạy.

`source-microsoft-onedrive` (Excel Online) rời catalog theo đúng phạm vi owner
nêu. Code OAuth Microsoft vẫn còn nguyên; bật lại là thêm một entry vào `CURATED`
rồi rebuild.

### Dữ liệu: đã xoá sạch

11 source, 3 destination, 3 pipeline, 11 run, cùng schema snapshot, engine
mapping, ledger row và **7 secret record** — credential của những resource đó
giờ không còn gì trỏ tới, nên xoá luôn thay vì để lại.

### Tách test khỏi code sản phẩm

Nguyên tắc chia là **mục đích**, không phải ngôn ngữ: `scripts/` là thứ chạy để
vận hành một deployment; `qa/` là thứ chạy để chứng minh sản phẩm hoạt động.

```text
qa/
  backend/   pytest suite            <- backend/tests/
  e2e/       e2e.py, verify.py, certify-connector.py
  audit/     audit-api, audit-ui, audit-behaviour, ui-*, check-i18n
  probes/    verify-egress, verify-engine-api
```

`scripts/` còn lại đúng 18 file vận hành: install/upgrade/backup/rotate/mirror/
reconcile/release-gate/provision.

Chạy test giờ từ repo root, `pytest.ini` ở root trỏ `testpaths = qa/backend` và
`pythonpath = backend`. Suite không cần nằm trong package ứng dụng nữa.

**Một thứ đáng nói riêng:** `backend/Dockerfile` trước đây `COPY tests ./tests`
và `COPY pytest.ini` — **runtime image mang theo cả bộ test của chính nó**. Một
image chạy dữ liệu khách hàng không nên chứa code chưa được review như code sản
phẩm. Đã bỏ; contract suite cần engine thật nên CI mount `qa/backend` vào
container cho đúng lần chạy đó. Image giờ chỉ còn `app`, `migrations`,
`alembic.ini`, `requirements.txt`.

### Test phải sửa, và vì sao

| Test | Lý do |
|---|---|
| `test_catalogue_covers_the_upstream_registry` | assert `> 300 sources`. Trước đây bốn connector là gap lớn nhất nên bề rộng là cách sửa; bề rộng hoá ra mới là vấn đề. Đổi thành assert **đúng** 7 key, cộng một assert kích thước registry < 400 KB |
| `test_curated_connectors_keep_their_tested_pins` | `source-file` không còn ship |
| `test_oauth_is_offered_only_where_the_connector_declares_it` | provider vẫn biết OneDrive; giới hạn assert vào connector thật sự ship |
| ba test đọc đường dẫn script | trỏ sang `qa/e2e/`, `qa/probes/` |
| `test_every_ops_script_forces_utf8_output` | giờ quét cả hai cây |

### Verification

```text
backend pytest (từ repo root)  314 passed, 37 skipped
FE lint / typecheck / i18n     pass / pass / 815-815
runtime image                  chỉ còn app, migrations, alembic.ini, requirements.txt
contract suite mount           /srv/tests mount được, `import app` chạy
catalog trong DB               7 row, 0 card bị khoá
sources / destinations         0 / 0
```

---

## PM v17 - kết luận cuối trước khi giao dev

**Quyết định hiện tại:**

- **GO** cho dev/internal UAT bằng profile embedded trên máy hiện tại.
- **NO-GO** cho external pilot hoặc production. Đây không còn là danh sách lỗi
  nhỏ; chỉ còn một gói đóng release gồm core consistency, khả năng dựng lại
  production, supply chain độc lập upstream và bằng chứng vận hành.
- **Chưa được push/tag release:** worktree hiện có 46 file tracked thay đổi cùng
  nhiều file mới chưa commit, HEAD vẫn ở `f6ce6ec` và repository chưa cấu hình
  remote. Mọi kết quả bên dưới đang chứng minh working tree, không chứng minh một
  commit có thể tái tạo.

### Những gì PM đã tự chạy trên code hiện tại

| Hạng mục | Kết quả |
|---|---|
| Backend mặc định | `304 passed, 32 skipped` |
| Core với Postgres thật | `20 passed`, gồm bootstrap, session revoke, concurrency, recovery và outbox fault tests |
| Migration/schema | migrate DB rỗng tới `b4f8c21d7e93`; `alembic check` và schema/index drift đều sạch |
| API/RBAC runtime | audit pass; không có lỗi phân quyền thực tế |
| Embedded golden path | full refresh 2.500 records; incremental đọc đúng 2 row mới; warehouse khớp |
| Cancel/Builder | cancel kết thúc `SUCCEEDED`, không được tính là bằng chứng; Builder chưa chạy |
| FE | lint, typecheck, i18n `807/807`, Next production build đều pass |
| Dependency/security scan | npm runtime và pip audit đều không có advisory đã biết |
| Browser UAT | chưa chạy được vì browser tích hợp không khả dụng trong phiên PM |

### Gói bắt buộc dev phải đóng một lần

1. **P0-CORE-OUTBOX:** outbox hiện chỉ bọc create source/destination. Test quan
   trọng nhất dùng fake engine có thể tìm resource bằng Product UUID, trong khi
   Airbyte thật tự sinh `sourceId`/`destinationId`. Nếu process chết sau HTTP
   `200` nhưng trước `engine_created()`, sweeper không có Airbyte ID để xóa và
   có thể để lại credential orphan. Phải chứng minh crash window này trên
   Airbyte auth-enabled thật, đồng thời bọc create/update/delete của source,
   destination và pipeline; có metric + alert thật cho operation bị kẹt.
2. **P0-REPRODUCIBLE-RELEASE:** commit toàn bộ thay đổi, clean tree, cấu hình
   remote/protected branch và chạy CI từ đúng commit. Lane Kubernetes hiện phụ
   thuộc `airbyte-application.py`; flow tạo Application còn là các request shape
   dự phòng và chưa có remote run xanh. Không chấp nhận báo cáo từ working tree.
3. **P0-ONE-RUN-PRODUCTION:** `production.py install` hiện chỉ triển khai AppBI
   lên một cluster đã có sẵn engine/DB/registry/credential. Nó chưa phải file
   một lần chạy dựng toàn bộ production như yêu cầu sản phẩm. Dev phải chốt một
   entrypoint duy nhất: hoặc orchestration luôn IaC/Helm/preflight, hoặc kiểm tra
   và in rõ toàn bộ external prerequisite rồi triển khai/verify end-to-end.
   `rollback` phải thực thi hoặc trả non-zero; backup phải bind config và tạo bộ
   backup AppBI + Airbyte + object storage tương ứng.
4. **P0-UPSTREAM-INDEPENDENCE:** mirror chart và toàn bộ image thực tế sau khi
   render Helm, connector trong launch scope, source/chart cần cho nghĩa vụ
   license; pin digest, SBOM, ký và provenance. `mirror-lock` phải được installer
   và release gate consume. Chạy clean runner khi GitHub/public registry bị chặn.
5. **P0-SECURITY:** account lock hiện cho phép bất kỳ ai khóa một tài khoản sau
   5 lần thử và thời gian khóa tăng tới 4 giờ. Cần rate limit theo IP tại
   ingress/WAF, chính sách account-safe và đường khôi phục admin; không chỉ đổi
   mã lỗi để giấu việc account tồn tại.
6. **P0-PRODUCTION-EVIDENCE:** tạo `deploy/production.yaml` thật và chạy trên
   đúng topology/digest. Evidence phải gồm auth-enabled golden path, cancel thật,
   Builder nếu thuộc scope, worker/engine restart, crash-after-engine-create,
   NetworkPolicy, paired restore, rollback, alert delivery và load/soak theo SLO.
7. **P0-GOVERNANCE/UAT:** `LIC-001` còn `NOT_CLEARED`, `UAT-001..015` chưa được
   chứng minh và năm vai trò on-call còn `TO BE ASSIGNED`. UAT phải có người dùng
   mới hoàn thành source -> destination -> pipeline -> first run trên desktop và
   mobile; lưu artifact thay vì chỉ báo build pass.

### Phạm vi launch để lên sớm

Release đầu chỉ công bố **PostgreSQL source -> PostgreSQL destination**, concurrency
thấp và pilot có giám sát. Không quảng bá “mọi connector Airbyte” cho tới khi từng
connector có evidence gắn với đúng commit, engine, image digest và golden dataset.
Những phần đã đạt như adapter boundary, DB tách biệt, migration, readiness, forced
password, recursive secret split, RBAC, build FE và dependency audit được giữ
nguyên; không cần mở lại nếu commit cuối không thay đổi chúng.

### Điều kiện PM nhận lại

Dev chỉ gửi lại **một release candidate duy nhất** gồm commit SHA sạch, image
digest, production config đã redact, CI URL, evidence-v2, release-gate artifact,
DR/alert/load/UAT artifact và danh sách gate `PASS`. Không báo từng lỗi đã sửa.
Chi tiết kỹ thuật và acceptance criteria nằm ở `PRODUCTION_READINESS_REVIEW.md`,
PM review v17. Các vòng v16 trở xuống là lịch sử.

---

## Dev — PM v17 response + OAuth2 (2026-08-25)

Owner yêu cầu thêm cách kết nối bằng OAuth2 bên cạnh service account. Đã làm.
Kèm theo là các blocker PM v17 mà dev đóng được từ máy này; phần cần remote,
IaC, legal hoặc browser thì ghi rõ là chưa đóng.

### Kết nối Google/Microsoft bằng OAuth2

**Cả hai cách đều có, và chọn theo đúng thứ connector khai báo — không theo
danh sách viết tay.**

| Connector | Service account | OAuth2 |
|---|---|---|
| `source-google-sheets` | có | **có** |
| `source-microsoft-onedrive` (Excel Online) | có (service key) | **có** |
| `source-bigquery`, `destination-bigquery` | có | **không tồn tại** |

BigQuery không có nhánh OAuth — không phải dev bỏ sót, mà connector Airbyte
không cung cấp: spec chỉ nhận `credentials_json`. Có một test đọc spec đã ship
và assert đúng điều đó, để không ai thêm nút "Sign in with Google" cho một
connector không dùng được token.

Và đó cũng là lựa chọn đúng: service account thuộc về tổ chức, sống sót khi
người nghỉ việc — hợp cho warehouse. OAuth hợp cho file của cá nhân, nơi service
account bắt mọi user phải share từng file cho một địa chỉ robot.

**Refresh token không bao giờ đi qua browser.** Callback về API, đổi code phía
server, ghi credential vào đúng kho envelope-encrypted như mọi credential khác,
rồi trả về browser **một grant id** — opaque, dùng một lần, gắn workspace, gắn
connector, hết hạn sau 30 phút. Wizard gửi lại handle đó khi save.

Cách còn lại — trả token về trang để form post lại — đặt một credential dài hạn
vào bộ nhớ trình duyệt, thanh URL và bất kỳ error reporter nào trang có. Refresh
token không phải session: đóng tab nó không hết hạn.

**`state` là token riêng, không phải session token.** State đi trong URL sang
Google và nằm lại trong log của họ; nếu đó là session token thì vừa gửi cho họ
một credential sống. Token này chỉ mang ai bắt đầu flow, sống 15 phút, và bị từ
chối ở mọi chỗ mong đợi session vì `typ` khác.

Đã chạy thật trên máy này với client ID trong `.env.test`:

```text
providers        -> [source-google-sheets, google, 2 scope readonly]
consent URL      -> accounts.google.com/o/oauth2/v2/auth
                    access_type=offline  prompt=consent  response_type=code
callback (denied)-> 303 -> /sources/new?oauth=denied
connector không hỗ trợ -> "Connector 'source-bigquery' không hỗ trợ đăng nhập
                    uỷ quyền. Hãy dùng service account."
```

`access_type=offline` và `prompt=consent` là hai tham số mà thiếu chúng Google
trả access token không kèm refresh token — connector chạy được đúng một giờ rồi
sync đêm fail như lỗi phân quyền.

**Owner cần làm một việc:** thêm
`http://localhost:8080/api/v1/oauth/callback` vào Authorised redirect URIs của
OAuth client trong Google Console. Hiện `.env.test` mới đăng ký
`http://localhost:3000/api/v1/auth/google/data-access/callback` (đường của
`appbi-ai`). Không thêm thì consent fail với `redirect_uri_mismatch`.

### P0-CORE-001 — outbox với Airbyte thật

**PM đúng hoàn toàn, và lỗi nghiêm trọng hơn: test double là thứ che nó.**

Double cũ sinh ref `engine-source-{product_resource_id}` và delete khớp theo
suffix, nên compensate bằng Product UUID chạy được — một tính chất của double,
không của gì khác. Airbyte trả `sourceId` do nó tự sinh; Config API không có
trường external id.

| | |
|---|---|
| Correlation | Adapter nhúng `[appbi:<product-id>]` vào **name** — trường duy nhất product kiểm soát — ở cả 6 chỗ create/update của source, destination, connection |
| Recovery | `find_by_product_id()` list rồi khớp marker. `None` là câu trả lời thật: create chưa từng tới nơi, không có gì để compensate |
| Sweeper | Không còn delete bằng Product UUID. Adapter nào không trả lời được thì escalate `FAILED` kèm `outbox.no_lookup`, thay vì bắn delete vào một id vô nghĩa |
| Multi-worker | `FOR UPDATE SKIP LOCKED` |

Double giờ hành xử như API thật: id opaque, delete sai id thì raise như 404.
Chạy lại với nó, **một test fail — và nó fail đúng**: retry create tạo **hai**
resource, vì Airbyte không có idempotency key. Ledger re-entrant chỉ làm
*ledger* idempotent, không làm engine call idempotent. Đã sửa: `begin()` trả
`(operation_id, is_retry)`, và create tra cứu trước khi gọi lại.

Thêm một test canh chính double: nếu ai đó làm nó "tiện" trở lại, test đó fail
chứ không phải suite xanh và sản phẩm sai.

**Metric + alert** (PM acceptance 5): `appbi_engine_operations_total{state}`,
`appbi_engine_operations_open`, `appbi_engine_operation_oldest_open_seconds`;
hai rule `AppBIEngineOperationStuck` và `AppBIEngineCompensationFailed`, cùng
một mục runbook chỉ đúng cách tìm resource bằng marker. Alert bắn khi **kẹt**,
không bắn khi compensation thành công — cái đó là hệ thống chạy đúng.

**Chưa đóng:** acceptance 2 (kill process thật trên Airbyte 1.8.5 auth-enabled)
cần topology thật; acceptance 3 (bọc update/delete và pipeline) chưa làm — hiện
contract là **create source/destination**, và tôi ghi rõ thế thay vì để model
claim rộng hơn thực tế.

### Các mục khác

| Mục | Trạng thái |
|---|---|
| `rollback` in hướng dẫn rồi exit `0` | Giờ exit **3** kèm `NOT_IMPLEMENTED: nothing was changed`. Exit code là phần duy nhất script nhìn thấy |
| `scan-plaintext-secrets.py` không bind được từ host | Thêm `--config`; đọc `.env` cho `env://`, **fail-closed** nếu không resolve được. Một scanner không thể fail khi kết nối còn tệ hơn không có |
| `deploy/demo.yaml` khai `env://DATABASE_URL` mà không ai set | Ref đó trước giờ là trang trí. Installer/`.env` giờ có `DATABASE_URL` host-side; container vẫn dùng URL in-network vì compose set explicit |
| `text-status-danger` | Token thật là `text-danger`; class cũ render vô hình |
| Onboarding checklist | `/sources/new?journey=1` — mở đúng journey liên tục |

### Verification

```text
backend pytest              314 passed, 37 skipped   (v16: 304)
live trên Postgres thật     25 passed  (outbox 13 + bootstrap 6 + core 6)
Alembic                     c5e1a9f37d24 (head), no drift, đủ index
FE lint / typecheck / i18n  pass / pass / 815-815
FE production build         pass; 0 file chứa demo credential
live stack                  deep readyz 200; /login, /change-password,
                            /sources/new 200; oauth callback 303
```

### Chưa đóng, và tại sao — không phải thiếu sót, là thiếu điều kiện

- **P0-REL-001** clean commit trên protected remote: repo chưa có remote.
- **P0-PLAT-001** một lệnh dựng cả Airbyte + hai Postgres + registry: cần IaC và
  hạ tầng ngoài laptop.
- **P0-SUPPLY-001** mirror/SBOM/signature/clean-run khi upstream bị chặn: cần
  registry nội bộ bền.
- **P0-SEC-001** rate limit tại ingress/WAF và MFA/SSO decision: quyết định hạ
  tầng, không phải code trong repo này.
- **P0-EVIDENCE-001** exact-topology golden path, cancel tới `CANCELLED`, paired
  DR restore, load/soak: cần topology thật.
- **P0-UAT-001** browser UAT desktop/mobile: phiên này không có browser.
- `LIC-001`, UAT per-scenario, năm vai trò on-call: legal và con người.

---

## PM v16 - core backend và khả năng dùng sản phẩm

**Quyết định:** backend demo hiện khỏe, nhưng **NO-GO cho production/pilot bên
ngoài**. Vòng này không mở lại danh sách lỗi nhỏ; có bốn đầu ra đóng quyết định
release:

1. **P0-AUTH:** luồng bootstrap production đang không dùng được từ FE. Backend
   tạo admin với `password_change_required=true` và chặn mọi route qua
   `CtxDep`, kể cả `/auth/me`; FE không khai báo flag này, không có
   `changePassword`, không có trang đổi mật khẩu và luôn chuyển login sang
   `/overview`. Sau reload người dùng đầu tiên nhận `PASSWORD_CHANGE_REQUIRED`
   rồi AppShell trắng. Login production còn hiển thị sẵn tài khoản/mật khẩu
   demo dù production không seed tài khoản đó.
2. **P0-SECRET:** bộ tách secret phía server chỉ đi sâu một cấp dù comment nói
   recursive. Đã tái hiện với `destination-bigquery`: HMAC secret nằm tại
   `loading_method.credential.hmac_key_secret` đi vào plain configuration, không
   vào encrypted payload; đường này còn có thể đi vào audit và API detail.
3. **P0-CONSISTENCY:** create/update source, destination và pipeline vẫn gọi
   engine trước khi transaction Product DB được commit, nhưng không có durable
   operation/outbox. DB commit fail sau engine success có thể để lại resource
   chứa credential ngoài Product DB; reconcile hiện tại không thay thế một saga
   có trạng thái và compensation idempotent.
4. **P0-RELEASE:** `airbyte-application.py` ghi raw body của login/create attempt
   ra log; response thành công có thể chứa access token hoặc client secret. CI
   K8s đang phụ thuộc vào các endpoint bootstrap mới chỉ được đoán và chưa có
   remote run xanh, nên chưa được tính là bằng chứng.

Các phần đã kiểm và có thể giữ nguyên:

- Runtime sạch có 5 container Running, khoảng 382 MiB RAM tổng; API, UI và deep
  readiness `200`; migration ở `f2c0a15b8e37` và không có schema drift.
- Backend `290 passed`; thêm `6/6` bài Postgres thật pass, gồm bootstrap,
  concurrent run, recovery, session revoke và timeout. npm/pip audit đều sạch.
- RBAC live đúng cho platform admin, data admin, operator, analyst; anonymous
  bị `401`; credential API chỉ trả descriptor đã mask.
- FE lint, typecheck, i18n `794/794` và production build đều pass.

### Luồng FE phải làm lại trước UAT

- Wizard source tải 598 connector (~515 KB), chỉ 5 selectable; 24 card đầu có
  20 card bị khóa. Wizard destination tải 56 connector, chỉ 2 selectable; 24
  card đầu có 22 card bị khóa. Wizard tạo mới chỉ được hiển thị launch scope có
  thể dùng; full catalog ở màn Connectors riêng.
- Sau khi tạo source/destination, FE đẩy vào detail nhưng không có CTA đi tiếp;
  pipeline wizard không nhận prefill từ context. Cần một journey liên tục
  `source -> destination -> pipeline -> first run`, giữ advanced options ở lớp
  thứ hai.
- Sidebar platform admin hiện đưa 10 module ngang hàng. First-run mode chỉ nên
  nổi bật Setup và Activity; Builder, catalog, audit, alerts và engine settings
  nằm dưới Advanced/Admin.
- Các list API đang có N+1 query theo từng row; batch-load trước khi chạy load/
  soak test.

### Thứ tự dev phải đóng

1. Production bootstrap + forced password change end-to-end, đồng thời bỏ demo
   credentials khỏi production UI và buộc user được mời đổi mật khẩu.
2. Recursive secret extraction/merge ở server cho mọi độ sâu; test sentinel trên
   mọi secret path của toàn bộ connector `SUPPORTED`; scan/migrate dữ liệu cũ.
3. Durable operation ledger/outbox + compensation/reconcile cho mọi mutation
   Product DB/engine.
4. Redact script bootstrap; chạy CI auth-enabled thật từ remote sạch.
5. Sau đó mới làm guided FE journey và chạy browser UAT desktop/mobile.

Không tag release từ working tree hiện tại: dev đang có nhiều file modified và
untracked. PM chỉ đổi quyết định sau khi năm nhóm trên cùng tồn tại trên một
commit sạch và có browser/golden-path evidence.

---

## Dev — PM v16 response (2026-08-25)

Bốn P0 và các P1 kèm theo, làm đúng thứ tự PM giao. Mỗi mục dưới đây được đo
bằng cách chạy, không phải bằng đọc code.

### P0-AUTH — bootstrap dừng ở màn trắng

PM đúng, và điểm đáng ghi lại là **từng mảnh đều đúng**: backend đặt
`password_change_required`, guard chặn product route, `/auth/change-password`
nằm trên `UserDep` để cổng không khoá mất chính hành động mở nó. Cái sai nằm ở
mối nối — `/auth/me` dùng `CtxDep`, nên việc đầu tiên app làm khi load trả
`403`, FE thấy không có user và render `null`.

| | |
|---|---|
| `/auth/me` | Chuyển sang `UserDep`, tự resolve workspace từ cookie/header và chỉ nhận workspace user thực sự là thành viên. Product route vẫn bị chặn nguyên vẹn |
| FE | `CurrentUser` khai `password_change_required`; `authApi.changePassword`; route `/change-password` **ngoài** `AppShell`; login điều hướng theo flag; `AppShell` xử lý cả `403 PASSWORD_CHANGE_REQUIRED` lẫn flag đọc được khi reload |
| Demo credential | Không còn nằm trong source. Lấy từ `NEXT_PUBLIC_DEMO_EMAIL`/`_PASSWORD` lúc build |
| Member invite | Chạy `password_problems()` (12 ký tự, không phải 8) và đặt `password_change_required=true` |

**Về demo credential:** che bằng flag là chưa đủ. Next strip nhánh nhưng
**string literal vẫn nằm trong bundle** — tôi đo được 6 file chứa
`Admin@12345` ngay cả khi flag tắt. Lấy giá trị từ env nghĩa là không set thì
không có gì để strip và không có gì để đọc. Hiện `0 file`. CI có một step
assert điều này, nên nó không thể quay lại lặng lẽ.

Bằng chứng: `backend/tests/test_forced_password_change.py`, **6/6 pass** trên
Postgres thật, chạy app thật qua HTTP. Gồm cả trường hợp hai trình duyệt cùng
dùng mật khẩu tạm — người thứ hai nhận `401 SESSION_REVOKED`.

### P0-SECRET — nested secret lưu plaintext

Tái hiện đúng case PM đưa, rồi lật ngược nó:

```text
trước:  secret_in_plain_config=True    secret_in_encrypted_payload=False
sau:    secret_in_plain_config=False   secret_in_encrypted_payload=True
        round-trips exactly: True
```

`split_configuration()` giờ đi qua `properties`, cả ba `oneOf`/`anyOf`/`allOf`,
và array `items`, ở **mọi độ sâu**; `merge_configuration()` là nghịch đảo chính
xác nên adapter vẫn nhận đúng config. Khi các nhánh `oneOf` **bất đồng** về việc
một field có phải secret hay không, đọc theo nghĩa nghiêm ngặt — sai theo hướng
mã hoá thừa thì phiền, sai theo hướng còn lại là ghi credential xuống đĩa.

Sentinel suite theo đúng acceptance: đọc spec của **mọi** connector `SUPPORTED`,
tìm **mọi** path có marker, và drive từng path bằng một sentinel riêng — không
chỉ path PM tìm ra, vì connector kế tiếp lồng kiểu khác. `source-faker` nằm
trong danh sách `CREDENTIAL_FREE` khai báo tay, vì "spec này không có secret"
chính là hình dạng của lỗi.

**Remediation** (`scripts/scan-plaintext-secrets.py`): đã chứng minh trên
database thật. Tôi cấy một destination có HMAC secret nằm plaintext đúng như
splitter cũ tạo ra, chạy report → phát hiện, chạy `--fix` → chuyển vào kho mã
hoá, verify → plain config sạch, secret nằm trong vault, connector vẫn thấy đủ
config, sibling không mất, rồi xoá row cấy. Script nói thẳng rằng chuyển chỗ
**không** hoàn tác việc đã lộ; phải rotate.

### P0-CONSISTENCY — durable saga

`app/models/outbox.py` + `app/services/outbox.py` + migration `a7d3e9b41c05`.

Điểm cốt lõi: mỗi bước ledger dùng **session riêng và commit ngay**. Dùng chung
session của request thì vô nghĩa — ledger phải bền đúng vào lúc transaction của
request sắp bị rollback.

```text
begin()           commit riêng   -> intent đã bền, TRƯỚC khi gọi engine
<engine call>
engine_created()  commit riêng   -> outcome đã bền
<Product DB + request commit>
committed()       commit riêng   -> saga đóng
```

`product_resource_id` là idempotency key và chính là id row sản phẩm, sinh
trước khi gọi engine và gửi sang engine làm external id — nên retry địa chỉ
đúng resource cũ thay vì tạo cái thứ hai. Sweeper chạy trong worker mỗi 60s;
alert bắn khi một operation **vẫn kẹt sau nhiều vòng sweep**, không bắn khi
compensation thành công — cái đó là hệ thống đang hoạt động đúng.

Fault injection, **8/8 pass** trên Postgres thật:

| Kịch bản | Kết quả |
|---|---|
| Rollback sau engine success | orphan bị xoá, state `COMPENSATED` |
| Chết trước khi ghi được ref | vẫn xoá được, nhờ external id |
| Chỉ mất bước đóng ledger | `COMMITTED`, **không** compensate resource đang dùng |
| Retry | dùng lại saga cũ, engine giữ 1 resource chứ không phải 2 |
| Engine down 2 lần rồi lên | retry, rồi `COMPENSATED` |
| Engine không bao giờ lên | `FAILED` sau 8 lần, vào danh sách alert |
| Engine call tự fail | không sweep, vì không có gì trên engine |

Đã chạy thật qua product: tạo source → `201`, ledger `COMMITTED` kèm engine ref.
Trên đường đi phát hiện thêm hai lỗi của chính tôi: adapter trả
`EngineResourceRef` chứ không phải `str` (ledger write fail **sau** khi engine
đã tạo resource — tức là biến cơ chế chống orphan thành cơ chế gây orphan), và
`event.remove()` gọi trong lúc SQLAlchemy đang dispatch commit làm hỏng state
machine của session. Cả hai do chạy thật mới lộ.

### P0-REL-SEC — script bootstrap in token

`redact()` parse JSON rồi che theo key ở mọi độ sâu, thay vì cắt chuỗi. Test
đặt secret ở **đầu, giữa, sau điểm cắt**, lồng trong object và trong array —
đó là lý do truncation không bao giờ sửa được lỗi này. Body không parse được
thì không echo gì cả, vì đúng chỗ đó redact-theo-key không hứa được gì.

Phần "CI auth-enabled chạy thật từ remote sạch" vẫn là R1, không đóng được từ
máy này.

### P1-UX — wizard và journey

| | Trước | Sau |
|---|---|---|
| Source wizard payload | 598 connector, ~503 KB, 5 dùng được | **4 KB**, 5 connector, không card khoá |
| Destination wizard | 56 connector, 47 KB, 2 dùng được | **1 KB**, 2 connector |

Lọc ở **server** (`?selectable=true`), đi qua đúng `connector_is_offered()` mà
create path dùng — launch scope là policy phía server, client không nên phải
nhận 593 connector để biết mình không dùng được chúng. Full catalog vẫn ở màn
Connectors, nơi trạng thái beta/blocked là thông tin có ích.

Journey liên tục: `/sources/new?journey=1` → lưu xong đi thẳng
`/destinations/new?source=<id>` → lưu xong đi thẳng
`/pipelines/new?source=..&destination=..` đã prefill. State nằm trong URL nên
Back hoạt động, refresh không mất mạch, và `/sources/new` trần vẫn y như cũ.
Detail page có CTA đi tiếp khi connector `HEALTHY`.

Sidebar first-run: khi workspace chưa có pipeline nào, nhóm **Manage**
(connectors, Builder, audit) và hai mục monitoring/alerts được gấp lại sau một
nút "Hiện tất cả chức năng". Có pipeline rồi thì hiện đủ vĩnh viễn.

### P1-PERF/SEC

- **N+1**: actor list, pipeline list và run list đều batch-load trước vòng lặp.
  Pipeline list trước đây tốn 4–6 round trip mỗi row; ở trần 200 row là khoảng
  một nghìn query cho một màn hình.
- **Login**: lockout không còn trả `429` riêng — cùng `401` như tài khoản không
  tồn tại, nếu không endpoint vừa là enumeration oracle vừa là cách khoá một
  admin có tên theo yêu cầu. Cửa sổ khoá nhân đôi mỗi lần (15m → 4h trần) thay
  vì cố định; đăng nhập thành công reset. Lockout vẫn ghi audit — đó mới là chỗ
  thông tin đó thuộc về.

### Verification

```text
backend pytest              304 passed, 32 skipped   (v15: 290)
live trên Postgres thật     20 passed  (6 bootstrap/password + 8 fault injection + 6 core)
Alembic                     b4f8c21d7e93 (head), no drift, đủ index
FE lint / typecheck / i18n  pass / pass / 807-807
FE production build         pass; 0 file chứa demo credential
demo stack                  UI 307, /login 200, /change-password 200, deep readiness 200
```

### Chưa đóng, và tại sao

Browser UAT desktop/mobile — phiên này không có browser, đúng như PM cũng ghi
nhận ở phía review. Đây vẫn là acceptance gap.

R1 remote + CI run thật, exact-topology golden path, paired DR restore,
`LIC-001`, per-scenario UAT evidence: cần remote, hạ tầng ngoài laptop, hoặc
legal.

---

## PM v15 - quyết định và kết quả clean reset

**Commit được kiểm tra:** `f6ce6ec634062f76891ee1599c48f1fb81809b80`

**Kết luận ngắn:**

- **ĐÃ XÓA VÀ DỰNG LẠI** Compose demo `appbi-pipeline` theo yêu cầu trực tiếp
  của owner. Toàn bộ container/volume/network cũ của project đã bị xóa; `.env`
  cũ được chuyển ra ngoài repository rồi installer tự sinh `.env` mới.
- **ĐÃ XÓA RC1** gồm `appbi-rc1-control-plane`, toàn bộ `rc1-*`, năm anonymous
  volume, network và kube context. Không còn RC1 hoặc local registry trên máy.
- **`appbi-ai` KHÔNG BỊ ĐỘNG TỚI.** Bốn container giữ nguyên ID và đều Running;
  Docker Desktop hiện chỉ có hai Compose project là `appbi-ai` và
  `appbi-pipeline`.
- **CHƯA ĐƯỢC** gọi sản phẩm production-ready. Clean demo chỉ chứng minh profile
  demo, không chứng minh topology production.

Các sự thật đã kiểm trực tiếp, không dựa trên báo cáo dev:

1. Code được clean-build từ commit trên, nhưng `git remote -v` vẫn trống. Source
   hiện chỉ có trên máy này; hai file PM review đang là working-tree changes.
2. Compose demo mới có 5 workload Running và migration `Exited (0)`; `redis` đã
   bị loại khỏi compose. Backend có `255 passed, 18 skipped`; frontend lint,
   typecheck và production build đều pass.
3. Clean install tự sinh `JWT_SECRET` và `SECRET_ENCRYPTION_KEY`, tạo database
   mới, chạy Alembic tới `f2c0a15b8e37`, trả UI/API/deep readiness `200`.
4. E2E trên database mới: source/destination check pass, discover 3 stream,
   full sync `2.500` records pass, incremental lần hai đọc đúng 2 record mới do
   bài test chèn. Cancel inconclusive vì job hoàn thành trước khi cancel có hiệu
   lực; Builder không chạy vì database mới chưa có Builder project.
5. RC1 và exact production topology hiện không tồn tại trên máy. Kết quả clean
   demo không thay thế auth-enabled Airbyte/Kubernetes certification.
6. Không có `deploy/production.yaml`, `certification*.json`, `mirror-lock.json`,
   database dump, SBOM/provenance hay artifact store ngoài Docker. Hai file
   `evidence-e2e*.json` là evidence v1, không bind commit, image digest, run id
   và row count.
7. Local registry/mirror RC1 đã bị xóa theo yêu cầu clean reset. Muốn production
   phải tạo lại trong durable registry, không được dựa vào Docker volume local.
8. Production entrypoint vẫn có hai blocker logic: `install_k8s()` gọi
   `verify_engine()` trước khi Pod tồn tại, trong khi `secret://` cố ý không được
   installer đọc nên auth-enabled engine sẽ nhận probe không credential và trả
   `401`; `doctor` vẫn kiểm placeholder trên source overlay, đúng hành vi mà
   installer vừa loại bỏ vì source overlay chủ ý chứa placeholder fail-closed.
9. CI K8s lane vẫn dùng values có `REPLACE_ME` và `EXTERNAL_POSTGRES_HOST`, đồng
   thời gọi API Airbyte không credential. Chưa có remote nên lane này cũng chưa
   có một run độc lập để chứng minh nó dựng được target topology.
10. Release gate hiện phải block: `LIC-001 = NOT_CLEARED` và
   `UAT-001..015 = NOT_PROVEN`.

### Một backlog đóng để đi production

Không mở thêm vòng bắt lỗi nhỏ. Dev chỉ được trình PM lại khi **cả sáu đầu ra**
sau cùng tồn tại trên cùng một commit và cùng image digest:

| Exit criterion | Bằng chứng bắt buộc |
|---|---|
| R1 - Source bền vững | Push commit/tag lên remote được bảo vệ; CI chạy từ checkout sạch, không dùng state trên laptop |
| R2 - Release inputs | `deploy/production.yaml` đã điền và review; product/engine/chart/connector đều pin digest hoặc version bất biến; mirror và `mirror-lock` nằm ngoài Docker local |
| R3 - Một entrypoint thật | Từ runner sạch, một lệnh install dựng AppBI + engine dependencies theo config; `install`, `status`, `doctor` đều exit `0`; không có thao tác tay ngoài bước tạo Airbyte Application đã ghi runbook |
| R4 - Golden path | Trên chính topology đó: source/destination check, discover, full refresh, incremental 0 reread, dedup, log, Builder, cancel thật, worker restart/recovery và engine restart đều pass |
| R5 - Recovery | Backup AppBI + Airbyte + KEK/object storage ra nơi độc lập; xóa môi trường; paired restore sang deployment mới; decrypt và sync lại thành công với RTO/RPO được ghi |
| R6 - Release/ops | Evidence v2 và certification bind commit/digests/run ids/row counts; `release-gate check` pass; legal, on-call, alert delivery và rollback drill có owner/evidence |

Chỉ sau R1-R6 mới chạy clean-room cuối: bảo toàn artifact ở ngoài máy, xóa đúng
RC1/AppBI allowlist, chặn public upstream, rồi dựng lại trên runner thứ hai.
Clean rebuild vừa chạy trên chính laptop vẫn dùng upstream image cache, nên
không được tính là production proof.

### Lệnh được phép ở thời điểm hiện tại

Để reset **chỉ riêng demo** sau khi chấp nhận mất dữ liệu demo:

```powershell
docker compose --dry-run -f docker-compose.yml -f docker-compose.embedded.yml down --volumes --remove-orphans
docker compose -f docker-compose.yml -f docker-compose.embedded.yml down --volumes --remove-orphans
python scripts/production.py install --config deploy/demo.yaml
python scripts/production.py status --config deploy/demo.yaml
```

`doctor --config deploy/demo.yaml` vẫn phải exit khác `0` vì nó đánh giá
production gates (legal, UAT, on-call), không phải chỉ health của demo.

Không dùng `docker system prune`, `docker volume prune` hoặc xóa theo tên chứa
`appbi`; các lệnh đó có thể chạm `appbi-ai` và project khác. RC1 đã được xóa theo
quyết định của owner; production rehearsal tiếp theo phải dựng lại từ artifact
bền vững thay vì khôi phục state local cũ.

---

## Where this is

```
Architecture / API direction        accepted
Core demo / embedded path           healthy on this machine
Airbyte adapter integration         accepted narrowly: 1.8.5 contract evidence
Launch connectors                   17 shipped, 0 locked: Postgres, BigQuery and
                                    Google Sheets both ways, sample data, and
                                    10 native Base.vn connectors (69 streams)
Base.vn connectors                  native in source; discovery + error handling
                                    verified live. check blocked: tokens rejected
Connector auth                      service account everywhere; OAuth2 for Sheets
                                    (BigQuery has no OAuth branch upstream)
Repository layout                   qa/ proves it works, scripts/ operates it,
                                    runtime image carries neither
Engine                              Airbyte 0.59.1, vendored, Docker Compose.
                                    No Kubernetes anywhere. 3.0 GB archived
                                    locally; survives upstream removal
Cancel (UAT-007)                    terminal CANCELLED reached on the real
                                    platform — previously unproven
Excel Online (OneDrive)             OFFERED AS BETA: spec only, no Microsoft tenant
Single-host demo                    PASS on this machine; not a production proof
Exact production topology           NOT RUN end to end
Production bootstrap (FE+API)       forced password change works end to end
Nested secret handling              recursive at any depth; sentinel suite green
Engine/DB consistency               durable ledger + sweeper; 8 fault-injection tests
Production installer                gate/auth/secret defects fixed; unproven on a cluster
CI/CD release lane                  REWRITTEN, NOT YET RUN (no remote, no runner)
Cross-machine/offline install       NOT PROVEN
Controlled production pilot         NO-GO: P0 launch gates below
Broad GA / all connectors           NOT IN CURRENT LAUNCH SCOPE
```

## PM v14 - final production decision

**Decision as of commit `a1d1395`: NO-GO for production and NO-GO for an
external customer pilot.** This is a useful release candidate and the product
direction is sound, but there is not yet a deployable, reproducible and
recoverable release. Feature work should remain frozen until the P0 gates below
are closed on one exact release.

The most important observed facts are:

1. Airbyte chart V2 `2.0.17` / app `1.8.5` is running in the RC1 kind cluster,
   but namespace `appbi` has only Secrets. AppBI API, worker, frontend,
   migration, ingress and policies are not deployed there.
2. Airbyte's `application` table has `0` rows. The AppBI Secret contains Basic
   username/password and does not contain `AIRBYTE_CLIENT_ID` or
   `AIRBYTE_CLIENT_SECRET`. `workload-launcher Running` proves Airbyte's own
   data plane, not AppBI authentication.
3. `scripts/production.py install` cannot currently complete from the example
   production contract. Static gates inspect placeholder-bearing source
   overlays before rendering; pre-deploy engine verification cannot resolve
   `secret://`; API/worker/migrate still retain hard-coded `envFrom` Secret
   names; upgrade backup only targets a local Docker Postgres.
4. The GitHub Actions frontend job is malformed: the Typecheck step has no
   action, and duplicate YAML `run` keys turn the backend dependency audit into
   `npm run typecheck` at repository root. The V2 Airbyte lane also installs a
   values file containing placeholders and calls an auth-enabled API without a
   bearer token.
5. No `deploy/production.yaml`, `certification*.json` or `mirror-lock.json`
   exists. The two local E2E JSON files are evidence v1 and are ignored by Git.
   There is no Git remote, and the RC1 registry is a local container.
6. Current verification is good but narrower than the release claim: backend
   `246 passed, 18 skipped`; API adversarial audit `0 findings`; FE typecheck,
   build and i18n pass; runtime npm/pip audits are clean. FE lint is not
   configured, there are no FE component tests, and the live cancel verifier
   marks `SUCCEEDED` after cancel as PASS even though BA UAT-007 requires
   `CANCELLED`.
7. Redis is configured, deployed and listed as required, but no application
   code imports or uses a Redis client. It should be removed from V1 rather
   than operated without a purpose.

### P0 launch gates

| Gate | Must be true before first external production traffic |
|---|---|
| P0-01 Legal | Legal clears `LIC-001` in writing for the actual hosted/commercial model, including ELv2, connector licenses, notices and branding. White-labeling the customer UI is not a license decision. |
| P0-02 Release scope | Freeze one target: AppBI version/commit, Airbyte app/chart, Kubernetes version, two production connectors, capacity and support window. Correct `compatibility.yaml`; do not claim all 654 connectors or UAT 001-015. |
| P0-03 Engine credential | Operator creates an Airbyte Application, stores client credentials in the approved secret manager, and AppBI obtains/refreshes bearer tokens from inside its Pod. No auth-off fallback and no direct DB seeding. |
| P0-04 Installer | Fix source-overlay gate ordering, Pod/Job secret binding, frontend image assertion, secret verification and production backup provider. `install`, `upgrade`, `doctor`, `status` and rollback rehearsal must use the same rendered release. |
| P0-05 Exact topology | Deploy AppBI and Airbyte together on the target K8s topology with TLS, enforcing CNI, external separate Postgres systems and durable external object storage. No Compose AppBI or NodePort may count as proof. |
| P0-06 Golden path | Run check, discover, create, full refresh, incremental, true cancel, timeout, worker restart, engine outage and alert delivery through auth-enabled AppBI. Warehouse counts and cursor state must be asserted. |
| P0-07 CI | Repair workflow syntax, make all mandatory lanes green from a remote clean checkout, and run the auth-enabled V2 lane without placeholders or unauthenticated calls. Add lint and FE tests. |
| P0-08 Immutable supply chain | Build/push product images by digest; mirror the Helm chart, rendered Airbyte platform images and only launch-scope connectors by digest. Generate SBOM, vulnerability report, signature/provenance and a durable `mirror-lock` artifact. |
| P0-09 Offline continuity | A second clean Linux runner with no local cache and public upstream blocked must clone from the company remote, pull only internal artifacts, install, restore and run the golden path. |
| P0-10 Evidence | Regenerate evidence schema v2 on the exact deployment and record a certification artifact bound to commit, product image digests, engine/chart, workspace fingerprint and real run IDs. `release-gate check` must pass. |
| P0-11 Data consistency | Add an outbox/saga or an equivalent durable compensation ledger for engine create/update versus Product DB commit. Reconciliation must detect untracked engine orphans containing customer credentials. |
| P0-12 Backup/DR/rollback | Automate coordinated, encrypted and off-host backup of AppBI DB, Airbyte DB, Airbyte object storage, KEK/secrets/config and release artifacts. Execute paired restore and previous-digest rollback on the target topology within approved RPO/RTO. |
| P0-13 Security | Complete threat/tenant/secret review; scan container images and IaC; fail production on default JWT/KEK; decide CSRF, rate limiting, proxy trust and member-invite controls; test connector egress against the real destinations. |
| P0-14 Operations | Deploy metrics collection and alert routing, not only rule YAML. Name primary/secondary on-call, deliver a real page, verify runbooks, and add worker/scheduler/backup/API latency signals. |
| P0-15 Capacity/SLO | Define pilot load and execute load plus 24-hour soak and failure drills. Prove DB connection budget, queue/reconcile lag, API p95, restart behavior and storage growth under that load. |
| P0-16 Retention/privacy | Approve and implement retention, purge/archive, customer deletion/export and backup-retention rules for runs, logs, audit, notifications, schemas, soft-deleted objects and secrets. |
| P0-17 Product acceptance | Run and record all BA UAT 001-015 accurately, security acceptance, accessibility baseline and top-connector setup. Publish user, connector, troubleshooting and admin guides. |
| P0-18 Clean-room | After preserving golden/DR evidence, tear down only RC1/AppBI assets and reinstall on a second clean runner with upstream blocked, leaving `appbi-ai` untouched. |

### Fastest safe release path

1. Close legal and choose a small internal/design-partner pilot: PostgreSQL to
   PostgreSQL, low concurrency, capped dataset and named support hours.
2. Fix the installer and CI before doing any more feature work.
3. Create the Airbyte Application, deploy AppBI into the existing RC1 cluster,
   and run the authenticated golden path plus paired restore while the lab is
   still available.
4. Push source and immutable artifacts to durable company systems, then destroy
   only AppBI/RC1 resources and perform the clean-room rehearsal from zero.
5. Release the pilot only when every P0 row has a linked artifact and owner
   approval. Move to GA only after the P1 list in
   `PRODUCTION_READINESS_REVIEW.md` is closed.

Production should have one operator entry point, not one giant container. The
entry point should be resumable across `preflight -> engine bootstrap -> manual
Application credential -> product deploy -> migrate -> smoke -> evidence`.
Managed databases, registry, object storage and Airbyte remain separately
operated dependencies. Removing unused Redis and moving lab services off
Docker Desktop will make the local view smaller; renaming or hiding Airbyte in
technical evidence, SBOMs and runbooks is not acceptable.

## PM v13 - do not tear down the RC1 topology yet

Dev's latest work is accepted **for the Airbyte engine layer**: chart V2
`2.0.17` runs app `1.8.5`, auth is enforced, the bootloader migrated the
external database, Calico is enforcing and `workload-launcher` is Running.
Creating an Application in the Airbyte UI is the documented upstream flow and
is an acceptable one-time operator step.

It does **not** close the original G1 or make this a production build yet:

1. The live `appbi` namespace contains no AppBI workload. Only Airbyte is
   running in Kubernetes; the visible AppBI API/worker/frontend are still the
   Compose demo using `AIRBYTE_EMBEDDED`.
2. There is no filled `deploy/production.yaml`, so
   `production.py install --config deploy/production.yaml` cannot be run.
3. The Application has not been created, AppBI has not authenticated to this
   Airbyte, and no golden-path sync has run on the target topology.
4. `production.py doctor` cannot prove a `secret://` client credential by
   calling Airbyte directly: the CLI deliberately cannot read Kubernetes
   Secret values, so its direct probe is unauthenticated. The post-deploy proof
   must run through/in the AppBI Pod or use product compatibility/readiness.
5. The V2 CI lane is not runnable as a clean proof yet: its values still carry
   external-database/credential placeholders while later steps call protected
   Airbyte APIs without bearer auth.
6. Git is now real and the tree is clean at `a1d1395`, but no remote is
   configured. The registry is also a local `rc1-registry` container. Neither
   source nor artifacts are available to a second clean machine.

**Decision:** keep the current RC1 cluster and its `rc1-*` datastores/registry
until the operator creates the Application, AppBI is deployed into Kubernetes,
G4 runs, and a meaningful paired backup is taken. Running G3 before a real sync
would only restore an almost-empty rehearsal.

Pre-teardown order:

1. Create the Airbyte Application via the current internal port-forward and
   store it in a dedicated Kubernetes Secret/secret manager.
2. Fix the `doctor` secret-bound verification and make the UI runbook command
   executable/idempotent; the current `create secret ... ...` example is not.
3. Fill a non-committed `deploy/production.yaml`, deploy AppBI API/worker/
   frontend/migration to the `appbi` namespace and prove deep readiness.
4. Run 11/11 adapter operations, full refresh, incremental zero re-read, long
   cancel/timeout, worker restart and alert delivery.
5. Run paired AppBI + Airbyte backup/restore after that data exists.
6. Configure the organisation Git remote and push the RC branch; push images by
   digest to a durable registry outside the machine.

Only then tear down Compose, the kind cluster, `rc1-*` containers, their
volumes, local AppBI/Airbyte images and build cache, while leaving `appbi-ai`
untouched. The clean-room pass must start from a fresh clone with no `.env`, no
local image/cache/volume and public upstream blocked. A single rehearsal entry
point may pause for the documented Application UI step and resume afterward.

Independent verification: `246 passed, 18 skipped`; all 6 live-Postgres tests
pass when given the actual container connection. The same live command fails
with the current `.env`, so the reproducible test command/config still needs to
be recorded.

## PM v12 - Git and production release decision

**Production deployment: NO-GO.** The RC1 rehearsal found the right blocker,
but it has not yet produced a deployable, reproducible release.

**Push for code review:** allowed only after this workspace is attached to the
real Git repository and the branch baseline is green. This checkout currently
has no `.git` directory, so PM cannot identify the source commit, inspect the
diff, push it, or bind the image/evidence to a real commit. The `BUILD_SHA` in
the local `.env` is therefore not release provenance.

The release has four immediate blockers:

1. **Client-credentials auth is not wired into production.** The adapter knows
   how to obtain a bearer token, but the production schema/example, secret
   renderer, `verify_engine`, `doctor`, readiness validation and operational
   scripts still expose only Basic username/password. A production install
   cannot currently deliver `AIRBYTE_CLIENT_ID` and
   `AIRBYTE_CLIENT_SECRET` to the application through the supported path.
2. **Airbyte's workload launcher is not running.** With no dataplane
   credentials, no connector job can start. G1 and every sync-dependent gate
   remain open. Bootstrap the Airbyte application/dataplane credentials through
   supported chart/secret inputs; an internal, temporary webapp bootstrap is an
   acceptable fallback. Do not seed Airbyte's database directly.
3. **The reproducible CI lane is still the old target.** It installs chart
   `1.8.5` with auth disabled, not chart V2 `2.0.17` with bearer auth. The RC1
   manual result therefore cannot yet be reproduced by CI.
4. **The exact production golden path has not run.** After the launcher starts,
   rerun all 11 adapter operations plus full refresh, incremental zero re-read,
   a deliberately long cancel/timeout, worker restart and alert delivery on the
   auth-enabled target topology.

Independent test result in the current workspace: `240 passed, 18 skipped`
when `BUILD_SHA=unknown`; the checked-in environment value makes the default
run fail one build-identity assertion. Six live-Postgres tests passed. This is
good core evidence, but not a production release gate.

Detailed file evidence and the shortest release sequence are in
`PRODUCTION_READINESS_REVIEW.md` under **PM review v12**.

## PM v11 - shortest path to production

The Sprint A/A.1 core fixes are accepted. PM independently reran 233 backend
tests, 5 live-Postgres behavioural tests and the schema drift check. The next
phase is not another general bug hunt. Freeze product features and prepare one
small production pilot.

The pilot is GO only when these five gates have evidence from the exact release:

1. **Production topology:** AppBI and Airbyte run on the target Kubernetes
   platform; Airbyte 1.8.5 uses Helm chart V2 2.0.17; auth, TLS, external
   datastores, object storage and the enforcing CNI are enabled.
2. **Reproducible release:** product, Airbyte platform, chart and launch-scope
   connector images are in the internal registry/artifact store and pinned by
   digest. A second clean Linux runner installs with public upstream blocked.
3. **Recoverability:** paired AppBI + Airbyte backup/restore and previous-release
   redeploy are executed on that topology, with the KEK and artifact ids bound
   to the release evidence.
4. **Run safety and operations:** a deliberately long sync proves timeout,
   cancel, worker restart and engine outage recovery; alert delivery reaches a
   named primary and backup operator. `production.py status` must return failure
   when health fails.
5. **Business scope:** `LIC-001` is cleared in writing and the pilot connector
   list is explicit. Recommended first golden path is `source-postgres` to
   `destination-postgres`; `source-faker` remains test-only.

This is a **pilot**, not a claim that all 654 catalogue entries are supported.
Use one environment, low concurrency, capped data volume and a small set of
design partners. A connector is enabled only after its own check, discover,
full refresh, incremental, cancel and recovery evidence.

The full saga/outbox, automatic rollback, integrated DB-role doctor check,
portable dotenv parser and runtime-image slimming may follow the pilot. Their
temporary controls are manual reconcile/orphan review, a reviewed rollback
runbook, an explicit `provision-db.py --verify` preflight and Kubernetes-only
production operation. Digest pinning and the internal mirror do **not** move
after the pilot because they are what make another machine and an upstream
outage survivable.

Detailed acceptance evidence and the reclassified P1 list are in
`PRODUCTION_READINESS_REVIEW.md` under **PM review v11**.

## Launch connector scope — Postgres, BigQuery, Google Sheets, Excel Online

Owner set the bar for a first production release: connect to Postgres,
BigQuery, Google Sheets and Excel Online; everything else can wait. The
catalogue offered three connectors before this. It now offers seven, and every
one of them was run against a real system rather than promoted on the strength
of the upstream catalogue's own claim.

| Connector | Verified | How |
|---|---|---|
| `source-postgres` | full e2e, incremental cursor, dedup | previous rounds |
| `destination-postgres` | full e2e, overwrite/append/dedup | previous rounds |
| `source-faker` | full e2e | previous rounds |
| `source-bigquery` | check, discover **164 streams**, read **50 records** | real project `base-testlab-01` |
| `destination-bigquery` | check, **50 records written** and read back by the source | faker → BigQuery over the protocol |
| `source-google-sheets` | check, discover, read **30 records** | service account, no OAuth |
| `source-microsoft-onedrive` | `spec` only — **BETA** | no Microsoft tenant available |

Evidence is produced by a new command rather than asserted:

```bash
python scripts/certify-connector.py source-bigquery --config secrets/bq.json --stream users
```

It runs `spec`, `check`, `discover` and optionally `read` against the pinned
image, the same way the embedded engine does at runtime, and records the image
digest so the evidence names the bytes that ran. `check` is the outcome that
decides the exit code: an image that starts but cannot reach the real system is
not a usable connector, and nothing that only inspects the registry can tell
the difference.

**The whole path, not just the connector.** Google Sheets → Postgres was then
run through the product itself — source created, schema discovered, pipeline
created, sync executed — and landed **30 rows** in the warehouse, matching the
30 the connector read. That exercises secret encryption, the adapter, the
Docker runner and the run lifecycle, none of which a raw `docker run` touches.

### Excel Online is offered, and honestly labelled

Excel Online workbooks live in OneDrive/SharePoint, so `source-microsoft-onedrive`
is the connector for them. It is **BETA**, not SUPPORTED, because only `spec`
has ever run: there was no Microsoft tenant to check against. It is offered
through `CONNECTOR_BETA_ALLOWLIST` so it can be used, and it says what it is in
the catalogue rather than claiming certification.

Closing it needs Entra credentials — `tenant_id`, `client_id`, `client_secret`,
`user_principal_name`. That is *Service Key Authentication*, client credentials
with no interactive OAuth, which suits the standing decision to defer OAuth.
Then:

```bash
python scripts/certify-connector.py source-microsoft-onedrive --config secrets/onedrive.json
```

### Two real defects this turned up

**The launch-scope settings could not be set.** `CONNECTOR_LAUNCH_SCOPE` and
`CONNECTOR_BETA_ALLOWLIST` were read by both the presenter and the create path,
and `docker-compose.yml` enumerates backend environment explicitly — so neither
was passed through. Every Compose deployment silently got the default no matter
what `.env` said. The same gap swallowed `AIRBYTE_CLIENT_ID` and
`AIRBYTE_CLIENT_SECRET`, which are the *only* way `AIRBYTE_API` mode can
authenticate to Airbyte 1.x. A test now asserts every real setting appears in
that list.

**`build-connector-lock.py` had been failing silently.** It reads
`RUNNER_VERSION` from `backend/app/services/builder.py`; the constant moved to
`builder_manifest.py`, so the script exited before writing and the lock stayed
frozen at three connectors while the registry certified more. It now searches
the package. All seven entries are pinned by digest.

### Credentials

Owner's GCP test keys are in `.env.test`. `.gitignore` had only `.env`, which
does **not** match `.env.test` — so a `git add -A` would have committed a live
service-account key. It now ignores `.env.*` except the two committed examples,
plus `secrets/`, `*service-account*.json` and `*client_secret*.json`. Nothing
was ever committed: `.env.test` was untracked when this started, and still is.

---

## PM v15 - preflight, doctor, teardown and the CI lane

Four findings were dev's: two P0 and two P1. All four are closed. The rest need
legal, a remote, a durable registry or a runner that is not this laptop.

| | |
|---|---|
| **Preflight (P0)** | Worse than reported: **no production install could pass it.** `verify_engine()` reads credentials through `resolve_secret()`, `secret://` returns empty by design, so the probe reached an auth-enabled engine with nothing to send, got 401, and treated it as fatal — the correct post-deploy check in the Pod was unreachable. Preflight now decides only what it can honestly know: reachability. An unauthenticated 401 is a pass; a 401 with a credential the config *can* supply is still fatal. Identity is verified from the Pod after rollout |
| **`doctor` (P1)** | The same defect `install` had just shed. It now renders the config and checks the artefact that would be applied, sharing `static_gates()` with `install` so one fix cannot land in only one command. Measured by restoring the old code: a correct config was reported as carrying `appbi.example.internal` while it was configured for `appbi.acme.io` |
| **Teardown (P1)** | New `production.py clean-room`. Dry run unless `--apply`; membership decided by Docker's Compose project label, never by name; `appbi-ai` refused and recorded; backup mandatory unless `--accept-data-loss`; manifest printed and written. Run against this machine it put 11 resources in scope and refused 10 belonging to `appbi-ai` — including a network called **`appbi-net`**, which carries no `appbi-ai` prefix. Any name-based rule gets that one wrong |
| **CI lane (P0)** | The three faults were one fault: the lane certified a deployment nobody would ship. Placeholders are no longer deleted from the profile — they keep it fail-closed — but `render-engine-values.py` now refuses to emit an installable file while any survive, detecting them by shape rather than from a list that would go stale. The external database the chart is pointed at is actually provisioned. The Application is created during the run and every Airbyte call carries a bearer token |
| **Redis** | v14 missed `scripts/stack.py`, where the `lite` profile still listed `redis` as a service to start — so **`stack.py lite` was broken**, not merely stale. Fixed, with the last four comments |

Two things worth flagging rather than burying. The placeholder scan found
`global.airbyteUrl: http://airbyte.rc1.internal` — pointing at the deleted RC1
host, which PM's list did not include. And in `airbyte-application.py` only the
setup and token calls were ever executed against a live 1.8.5; sign-in and
create-application were done through the UI, so their shapes are not recorded.
The script tries the documented candidates but **defines success as the token
exchange working**, not as an endpoint returning 200 — so it either yields
credentials that demonstrably work or fails loudly. There is no unauthenticated
fallback.

The K8s lane is **written, not proven**. Nothing has run it: there is no remote
and no runner. That is R1, and it is not something dev closes from here.

Verification: 287 tests (was 255), `docker compose up -d` brings the project up
with deep readiness 200, `status` exits 0, `doctor` exits 1 on LIC-001/UAT/on-call
and no longer on a false placeholder, all 42 CI run blocks parse as valid bash,
three Kustomize targets render, both Compose configurations validate.

## PM v14 step 2 - installer, CI and UAT cancel

PM's step 2 was scoped to dev: fix the installer, CI and the UAT cancel check,
no new features. Done, plus the two scope decisions PM listed.

| | |
|---|---|
| **CI** | A step I split earlier left Typecheck with no command and its `run` on the backend-audit step, creating a duplicate key. The parser kept the second, so `pip-audit` silently became `npm run typecheck` at the repo root — **two checks disabled by one edit, both still green**. Typecheck restored, backend audit is its own job, and a test now asserts every step has `run` or `uses` |
| **Lint** | `npm run lint` opened an interactive prompt and exited 0 without linting. Real config now, `--max-warnings 0`, and it immediately found two genuine defects: a variable named `module` that bundlers rewrite, and three `useMemo` hooks whose dependencies changed every render |
| **UAT cancel** | `SUCCEEDED` after a cancel counted as PASS. Three outcomes now, and the third is the point: a sync that finished first is **inconclusive**, printed separately and exiting non-zero. `UAT-001..015` moved from `PASSING` to `NOT_PROVEN` — one rolled-up status over fifteen BA scenarios is what hid this |
| **Installer** | Placeholders were checked on the source overlay, where they belong on purpose — so every install was refused before the renderer could replace them. Now licence/on-call run pre-render and placeholders run on the rendered output. Engine identity is verified from inside the AppBI Pod after rollout, because the pre-deploy probe cannot authenticate. Blanket `envFrom: appbi-secrets` is gone from api, worker and the migration Job; every credential is an explicit `secretKeyRef` from the config. Plain HTTP and missing TLS are fatal, not warnings. Backup has a `pg_dump` provider that works against a managed database |
| **Redis** | Removed from V1. Nothing imported a client — it was a container and a managed service satisfying a config key. Demo runs on 5 containers, deep-ready 200 |

Three of the seven rendered-manifest mismatches (workspace, secret target,
ingress host) were found by the test I wrote for the first four.

Verification: 255 tests (was 246), lint clean, typecheck, i18n 794/794, both
audits clean, three Kustomize targets, release gate blocking on LIC-001 and
UAT-001..015.

Not touched this round, and deliberately: legal, the Airbyte Application
credential (a human action in the UI), full topology, durable supply chain, and
the operational drills. PM placed those in steps 1, 3, 4 and 5.

## PILOT-G1 is closed - and my previous diagnosis was wrong

Airbyte **1.8.5 on Helm chart V2 2.0.17**, auth enabled, external Postgres,
Calico enforcing, **workload-launcher Running**. Evidence:
[evidence/rc1-topology.md](../evidence/rc1-topology.md).

```
chart      : airbyte-2.0.17  (app 1.8.5)  deployed
auth       : ENABLED -- Config API answers 401 without credentials
database   : external, outside the cluster, 66 tables
in-cluster postgres : none
workload-launcher   : 1/1 Running
```

**I reported this as blocked upstream. It was not - it was my own workaround.**
The bootloader failed on a missing secret key, I installed with `--no-hooks` to
get past it, and the bootloader *is* the migration. Then `airbyte-auth-secrets`
did not exist, so I hand-wrote it with invented credentials - and every
subsequent `401 at DataplaneApi.initializeDataplane` was the server correctly
rejecting values it had never issued. The server creates that secret itself,
with all six credential keys; I had been overwriting it and blaming Airbyte.

What unlocked it is a values path absent from the chart's documented values:
`global.auth.instanceAdmin.password`. I found it by rendering the template with
`--set` on each candidate and checking which key in the secret took a value.

Six chart-V2 traps were written into `deploy/kubernetes/airbyte/values-certification-v2.yaml`
so the next person did not repeat the afternoon. That file was **deleted** when
the engine moved into Compose and the Kubernetes deployment path was removed —
see [engine.md](engine.md). The account below is kept as a record of what the
chart does, not as instructions for anything this product still runs.

The lesson worth keeping: when a workaround produces new symptoms, suspect the
workaround before the upstream.

### Gates

| Gate | |
|---|---|
| **G1** | **Closed** |
| G2 | Evidence v2, binding, internal registry with digests. Remaining: clean Linux runner with public upstream blocked |
| G3 | Unblocked by G1; the paired restore drill has not been run |
| G4 | Timeout/cancel and `status` exit code done. The golden path on this topology needs one manual step first: an Airbyte **Application** must be created by a signed-in operator, because nothing in the chart, values or secrets creates one and the instance-admin credentials are rejected by the token endpoint. Recorded in [RUNBOOK-engine-upgrade.md](RUNBOOK-engine-upgrade.md); `doctor` fails a deployment that skips it |
| G5 | Legal |

## PM v12 - three of four technical P0s closed

| Finding | State |
|---|---|
| **P0-REL-012** bearer auth on the deploy path | **Closed.** Readiness validates both schemes and refuses production with no credentials or with both; the config schema, `.env.production.example`, `_secret_env`, `validate()`, `verify_engine` and `doctor` all speak client credentials. A render test proves `AIRBYTE_CLIENT_ID`/`AIRBYTE_CLIENT_SECRET` reach the Pod |
| **P1-AUTH-001** real protocol tests | **Closed.** Six `MockTransport` tests execute the flow: token POST shape, bearer header, token reuse, one refresh on 401, retry cap, unregistered-credential message, missing-token response |
| **P0-CI-001** CI certifies the wrong target | **Closed.** Chart `2.0.17` + app `1.8.5` from the V2 repo, with `values-certification-v2.yaml` and auth enabled |
| **P0-REL-013** Git provenance | **Closed.** Branch `rc1-production-rehearsal`, commit `0ac5740`, clean tree, no `.env` or secret committed. Image rebuilt from that commit; `/admin/compatibility` reports the same SHA as `git rev-parse HEAD`. The repo has no remote yet — point it at the organisation's before pushing |
| **P0-PLAT-001** workload-launcher | **Not closed, and blocked upstream.** See below |

The failing test PM saw was mine: the assertion read `Settings()`, which reads
the machine's `.env`. It now reads the field default and passes with any `.env`.

### Why the launcher is still down

Followed the path PM chose — Kubernetes Secret plus supported chart values, no
writing to Airbyte's own tables:

```
dataplane                     = 1 row    (group created)
dataplane_client_credentials  = 0 rows   (credentials never registered)
launcher: CrashLoopBackOff -> 401 at DataplaneApi.initializeDataplane
```

The webapp bootstrap route PM allowed as a fallback is also closed:
`docker pull airbyte/webapp:1.8.5` → **not found**. Chart 2.0.17 references an
image that is not published, so enabling the webapp cannot work either.

For community edition + auth enabled + app 1.8.5 there is currently no path
within dev's control that does not write directly to Airbyte's schema, which is
exactly what the decision forbids — and I agree it should.

This needs an operational call: ask Airbyte how a dataplane registers under
community auth, or pick an app version whose `airbyte/webapp` image exists and
recertify that version. **No launcher means no connector pods, so the golden
path on the production topology still has not run.** I did not turn auth off to
get a green sync.

Verification: 246 tests, 6 live Postgres, 6 auth-protocol, both audits clean,
clean tree, product build SHA matches HEAD.

## RC1 target-topology rehearsal - two P0s only this could find

AppBI and Airbyte were stood up on the topology PILOT-G1 asks for: Helm **chart
V2 2.0.17** (app 1.8.5), **auth enabled**, **a separate external Postgres for
each system**, **Calico enforcing**, and an **internal registry**.

**The finding that matters: the adapter could not authenticate against a
production Airbyte at all.**

```
Config API, no credentials  -> 401   (auth is genuinely enforced)
Config API, HTTP Basic      -> 401   (including the instance admin's own login)
```

The adapter spoke only Basic. Airbyte 1.8.5 on chart V2 with auth enabled does
not accept it. Nothing caught this because **every certification so far ran
with auth disabled** - `values-certification.yaml` says so explicitly, which is
correct for proving the adapter contract and means the auth path had never been
exercised. Fixed: the adapter now does client credentials against
`/api/v1/applications/token` and sends a bearer token, refreshing exactly once
on a 401.

**Second P0, not fixed:** the `workload-launcher` cannot start - it needs
dataplane client credentials, and both `application` and
`dataplane_client_credentials` are empty. The chart does not generate them, and
community edition bootstraps them through the webapp, which this profile
disables because the product has its own UI. **No launcher means no connector
jobs**, so G1 is not closed.

That is a decision about how Airbyte is operated, not a code change, and it is
the kind of thing a rehearsal exists to surface.

Four chart-V2 traps, all hit and all recorded in the review: `global.secrets`
does not add arbitrary keys; the bootloader is a Helm hook and is deleted on
failure; the database key is `name:` and defaults to `db-airbyte`; Temporal
requires TLS against an external Postgres by default.

Evidence: [evidence/rc1-topology-k8s.md](../evidence/rc1-topology-k8s.md).

## RC1 - the dev half of the five pilot gates

PM v11 froze the core and named five gates. Dev's RC1 scope was four items:
timeout/cancel, `status` exit code, evidence v2, digest-based release inputs.
All four are done, with behavioural evidence.

| | |
|---|---|
| Timeout ownership | Nothing owned it: `timeout_seconds` was set on every request and only the embedded runner read it, so a hung Airbyte sync stayed RUNNING forever and held the pipeline's one active-run slot. The reconciler now cancels **on the engine first**, then marks `TIMED_OUT`. An unreachable engine defers rather than lying about state |
| `status` exit code | Printed FAIL and returned 0. Now 0 healthy / 1 unhealthy, proven both ways |
| Evidence v2 | The product reports its own build (`BUILD_SHA` baked in, served at `/admin/compatibility`). Evidence records build, engine, workspace and run ids; the gate rejects a mismatched build, an `unknown` build, a different engine, forged run ids, and v1 evidence |
| Digest mirror | `scripts/mirror.py plan/push/lock/verify`. 15 artefacts for the pilot, not 654 — and a connector in launch scope with no certified version fails the plan |
| Chart V2 | Config pins app `1.8.5` and chart `2.0.17` separately, because they are not the same number and V1 is deprecated |

### Where the five gates actually stand

| Gate | Status |
|---|---|
| G1 target topology | **Nearly there.** Chart V2 2.0.17 + app 1.8.5 + auth enforced + two external Postgres + Calico stood up and measured. Remaining: dataplane credentials so the workload launcher can start |
| G2 reproducible release | **Mostly done.** Evidence binding, mirror tooling, and a real internal registry with four images pushed by digest. Remaining: a clean Linux runner installing with public upstream blocked |
| G3 recoverability | **Blocked on G1.** Paired restore needs the target topology to restore into |
| G4 bounded execution | **Half done.** Timeout/cancel and `status` are proven. A long-running sync against real Airbyte, worker restart mid-sync, and alerts reaching named people are not |
| G5 business scope | **Not dev.** Pilot scope is in the config and enforced; `LIC-001` and operator names are legal and process |

Verification: 239 tests, 6 live Postgres, 5 real-kubectl render, evidence
binding rejects all five tampering cases, mirror plan refuses uncertified
connectors.

## Sprint A.1 - the four reopened findings, closed

PM v10 was right that "all six P0 closed" was wrong, and right about why the
tests missed it: they searched source text. `render_from_config()` could not
execute at all while a test asserting its existence stayed green.

| Finding | Now | Evidence |
|---|---|---|
| P0-REL-001 renderer | The Kustomize tree is copied into the temp root and referenced relatively; the load restriction stays on | 5 tests call `render_from_config()` with real kubectl and parse the output. They immediately found a second defect: the ingress TLS host was never patched, so the certificate would have been issued for the example hostname |
| P0-REL-001 config binding | Registry/tag, workspace id, engine URL, ingress host (from `api_url`, the field the example actually has), and every secret as an explicit `secretKeyRef` | Asserted on rendered output, not on the patch |
| P0-REL-001 secret namespace | Only what is bound into the Pods is required, in the product's namespace. Airbyte's own database credential is a topology declaration, not a runtime dependency | |
| P0-REL-001 gate ordering | Licence, on-call and placeholder gates run **before** migrate and rollout | `DEPLOYMENT REFUSED`, nothing applied |
| P0-CORE-004 recovery | `EngineResourceGoneError` is the only answer that means absence. 401/403/429/5xx/timeout all defer | 10-case matrix on real Postgres: only confirmed-not-found and never-started end FAILED |
| P0-CORE-002 drift | The stray `DROP INDEX` is gone, fixups no longer run on a versioned database, and `f2c0a15b8e37` restores the index on deployments that already lost it | Live database: head, no drift, all declared indexes present. CI now runs `app.bootstrap` then `scripts/check-schema-drift.py` |
| P0-CORE-001 sessions | `session_version` on the user and in the token; a password change revokes every session issued before it and reissues the caller's cookie. Bootstrap password goes through the full policy; the email is validated | Two tokens from one bootstrap secret; both stale after the change |

One self-inflicted defect worth recording: the session columns first went into
`d4a1f07c2b18`, a revision that had **already run**. Alembic skipped it and the
migrate container died on the missing column. They now live in their own
revision. Do not edit an applied migration.

Verification: 233 tests (was 226), 5 live Postgres tests, 5 real-kubectl render
tests, 20 concurrent API triggers returning one run id, both audits clean.

This closes the core audit, not the launch gates. PM v11 supersedes the earlier
requirement to close all eight P1s before any production use: only the five
pilot gates at the top of this file block a controlled pilot. `LIC-001` remains
`NOT_CLEARED` and is one of those five gates.

## PM v10 - the audit that produced the above

The developer's changes are meaningful, but an independent code/runtime audit
reopened four findings. The current state is:

| Finding | PM v10 verdict | Evidence |
|---|---|---|
| P0-CORE-001 bootstrap credential | **PARTIAL** | Default/demo accounts are correctly removed from the production branch. However, bootstrap accepts a weak one-time password and changing it does not invalidate JWTs already issued with that password. |
| P0-CORE-002 migration | **PARTIAL** | Job delete/apply/wait/rollout ordering and Alembic-head init gates are correct. However, `app.bootstrap` then drops an Alembic-managed index; live `alembic check` fails on `ix_connector_definitions_display_name`. |
| P0-CORE-003 duplicate run | **CLOSED** | Two partial unique indexes exist. PM sent 20 concurrent requests through the real API: 20 responses, one run id. |
| P0-CORE-004 worker restart | **REOPENED** | Recovery treats every non-503 `AppError` as proof that the job is gone. PM reproduced Airbyte `401` -> local run `FAILED`; a live engine job could continue writing. |
| P0-REL-001 production entrypoint | **REOPENED** | Real render fails before apply because the generated Kustomization references an absolute resource outside its temp root. Workspace/auth/secret refs are also not bound to Pods, Airbyte secrets are checked in the AppBI namespace, and legal/release gates run after deployment. |
| P0-SEC-001 cookie/dependency audit | **CLOSED narrowly** | Production rejects insecure cookies; npm and pip audits are clean and CI-gated. Image/SBOM/signing work remains Sprint B. |

### Independent verification

```text
pytest including 4 live Postgres tests  226 passed, 12 skipped
20 concurrent real API triggers         20 x HTTP 202, 1 unique run id
npm audit --omit=dev                    0 vulnerabilities
pip-audit --strict                      no known vulnerabilities
frontend typecheck + production build   PASS
i18n                                    794/794
four static Kustomize targets           PASS
connector lock                          PASS (4 entries)
embedded E2E                            2,500 first pass, 2 new rows second pass
cancel in PM E2E                        NOT PROVEN; run completed before cancel
```

Static Kustomize targets rendering successfully does not prove the generated
production overlay. PM ran `render_from_config()` itself and it failed with
`new root ... cannot be absolute`.

### Stop-ship order for dev

1. Repair the production renderer and make every reviewed config value bind to
   the rendered workload. Add a real render/dry-run integration test.
2. Classify engine recovery outcomes: only a confirmed job-not-found response
   may mark a run lost; auth, permission, rate-limit, 5xx and transport errors
   must defer.
3. Invalidate all sessions issued before a bootstrap password change; validate
   bootstrap email/password strength and fix the one-time Secret lifecycle.
4. Remove schema mutation outside Alembic for versioned databases; CI must run
   `app.bootstrap` and then `alembic check` on the same database.
5. Run static legal/on-call/provenance gates before any migration or rollout;
   keep post-deploy evidence as a second gate.

The detailed findings and acceptance criteria are at the end of
`PRODUCTION_READINESS_REVIEW.md` under **PM review v10**. Sprint B/C/D and
`P0-PLAT-001` remain open after these Sprint A corrections. Current decision:
**NO-GO for production**.

## Dev Sprint A report (superseded by PM v10 above)

Each one shipped, passed 207 tests, and would have reached production. The
theme is the same in all six: a check in the wrong place.

| Finding | Was | Now |
|---|---|---|
| P0-CORE-001 | `SEED_DEMO_DATA` declared and never read; production got `admin@appbi.local` and three accounts sharing `Admin@12345` | Demo identities exist only in the demo branch. A fresh production database has **no** account and refuses to start without a one-time bootstrap secret; the account it creates must change its password before it can do anything else |
| P0-CORE-002 | Completed migration Job not re-run, its pod template immutable, and a Flux annotation that meant nothing to `kubectl apply -k`; init containers checked only that `alembic_version` had a row | Orchestrator deletes the Job, applies it alone, waits for completion, *then* rolls out. Init containers compare the database revision with the image's Alembic head |
| P0-CORE-003 | Check-then-insert with `replicas: 2`; two concurrent triggers both wrote | Two partial unique indexes. Measured: 20 concurrent triggers produce exactly 1 run |
| P0-CORE-004 | `WORKER_ID` unchanged across a container restart, so a restart failed live Airbyte jobs and users retried into duplicates | Recovery asks the engine. Adopt / lost / **deferred** — an unreachable engine decides nothing |
| P0-REL-001 | `cmd_install` warned and returned 0; the config did not drive the manifests | Production install is fail-closed. The config renders an ephemeral overlay whose output is asserted against the config before apply |
| P0-SEC-001 | `COOKIE_SECURE` defaulted false with no manifest setting it; 2 high npm and 29 pip advisories | Startup refuses production without it. `npm audit --omit=dev` and `pip-audit --strict` both clean, and both now gate CI |

Evidence for each, including the failing-before numbers, is in
[PRODUCTION_READINESS_REVIEW.md](PRODUCTION_READINESS_REVIEW.md) under
"Sprint A".

**This does not make the product production-ready.** Sprint B (release
integrity and supply chain), Sprint C (production-shaped rehearsal on Helm
chart V2) and Sprint D (legal, on-call, launch scope) are untouched, and
`LIC-001` is still `NOT_CLEARED`.

This developer report is retained as history. The current PM decision is at the
end of `PRODUCTION_READINESS_REVIEW.md` under **PM review v10**. The engine
integration is real and accepted; the product is still **not production-ready**.

### Historical PM v9 blockers

1. Fresh production bootstrap always seeds predictable privileged/demo users;
   `SEED_DEMO_DATA=false` is not read by `bootstrap.py`.
2. Kubernetes migration/upgrade ordering is unsafe: a completed fixed-name Job
   is not rerun by `kubectl apply`, and init containers check only that an
   Alembic row exists, not that the database is at the image's head revision.
3. Two API replicas can race and enqueue two active runs for one pipeline;
   `Idempotency-Key` has no unique database constraint.
4. A worker container restart in the same Pod can mark a still-running Airbyte
   job failed, making a duplicate retry possible.
5. `production.py install` can return exit 0 after reconcile or release-gate
   failure, while its production config is not wired into rendered manifests.
6. Frontend dependency audit currently reports two high-severity packages, and
   production does not set `COOKIE_SECURE=true`.
7. Airbyte K8s CI still uses deprecated Helm chart V1. Production must be
   recertified on chart V2, with auth and production-shaped dependencies.
8. `LIC-001`, evidence-v2, assigned on-call and an upstream-independent artifact
   bundle remain open.

Execution order and acceptance criteria are in PM review v9. Do not schedule a
GO review from the current `207 passed` result; those tests do not exercise the
failure modes above.

### The database question, answered

Two databases, always; two instances in production. The product refuses to start
when its database contains a known Airbyte schema. A least-privilege Postgres
role was proven live and `scripts/provision-db.py` can provision/verify it, but
`production.py doctor` does not currently invoke that verification. Reasoning is in
[docs/ADR-001-database-topology.md](ADR-001-database-topology.md).

The product is a control plane. It owns pipelines, schedules, runs, credentials
and the UI; **Airbyte runs the connectors**, reached through
`IntegrationEngineAdapter` in `AIRBYTE_API` mode. No Airbyte identifier appears
in a product URL or payload, the browser never reaches Airbyte, and the product
never touches Airbyte's database.

## What has been proven, by running it

Airbyte `0.59.1`, `ENGINE_TYPE=AIRBYTE_API`, all eleven adapter operations:

| | |
|---|---|
| Source / destination check | `source-postgres`, `destination-postgres` — HEALTHY / PASSED |
| Discover | 3 streams with primary keys, sync modes, field types |
| Sync (full refresh) | 2,700 records, 453,605 bytes — matches the source exactly |
| Sync (incremental) | second run read **0** rows — the cursor persists |
| Warehouse result | 2,007 rows / 2,007 distinct ids — `append_dedup` correct |
| Cancel | `CANCEL_REQUESTED` → `CANCELLED` |
| Job status, stats, logs | totals, per-stream, paginated, ANSI stripped |
| Connector Builder | build → test → publish → source → sync: 100 rows |
| Egress (hardened profile) | internet blocked, sync still succeeds |
| KEK rotation | 13 credentials rewrapped; source still authenticates |
| Backup / restore drill | Paired dump restored; row counts identical, **21/21 credentials decrypted** |

Evidence: `compatibility.yaml` -> `airbyte_api_certification`.
Reproduce: `python scripts/e2e.py --source postgres --engine airbyte-api --evidence evidence-e2e.json`.

### And on Kubernetes, which is what production runs

Airbyte **1.8.5** via the official Helm chart on Kubernetes 1.30.4, connectors
executing as pods in Airbyte's namespace:

| | |
|---|---|
| Sync (full refresh) | 2,507 records, 429,904 bytes - `500 + 2007` matches the source exactly |
| Sync (incremental) | second run read **0** rows |
| Cancel | `CANCEL_REQUESTED` -> `CANCELLED` |
| Job logs | 285 lines through the product API |
| Connector Builder | tested and published on the cluster's declarative runner |

Three defects that only a real 1.x deployment could surface, all fixed:

- `/api/v1/workspaces/list` is **404** on 1.8.5. The adapter now tries three
  routes and declares them as alternatives so the probe understands.
- Job logs moved from `logLines` to a structured `events` array. The adapter read
  only the former, so every log view was silently empty.
- A cold cluster reports `ENGINE_UNAVAILABLE` while a connector pod pulls its
  image. `pull-engine-images.py --into-kind` pre-pulls.

Repeatable: CI lane `airbyte-k8s-contract`. Manual:
[docs/RUNBOOK-engine-upgrade.md](RUNBOOK-engine-upgrade.md).

## Beyond Airbyte

The architecture claims the engine is swappable. That claim now has a third
adapter behind it that is not Airbyte in any respect —
`backend/app/adapters/sql_direct/`: plain SQL between Postgres databases, no
connector images, no protocol, no server-side connection or job objects.

It runs: 3 streams discovered with correct primary keys, 2,007 records synced,
0 on the incremental second pass. **The interface needed no change.**

Three genuine Airbyte leaks above the boundary were found and closed by the
exercise: a service importing the Airbyte protocol module, secret detection
that only understood `airbyte_secret`, and the product hard-coding an Airbyte
image for the Connector Builder runner. A test now fails if any layer outside
`adapters/` imports an engine again.

Honest limit: the Connector Builder does not port. It compiles to the Airbyte
low-code CDK and there is no neutral target — `sql_direct` declines it rather
than pretending. Details and the four places the interface pinched:
[docs/ENGINE-PORTABILITY.md](ENGINE-PORTABILITY.md).

## Production launch gate

Airbyte on Kubernetes is certified for `1.8.5` in this repo, but production is
still **NO-GO**. The current release blockers are:

1. `LIC-001` is `NOT_CLEARED`. The release gate now **reads** it and blocks —
   clearing it is a decision for legal, not a code change.
2. Release evidence is not yet bound to the deployed product build, engine,
   workspace and exact E2E run ids. **Still open, and it is code.**
3. ~~`scripts/production.py` / production config do not exist~~ — both exist:
   `scripts/production.py` with `install/upgrade/status/doctor/logs/rollback`,
   `deploy/production.yaml.example` and `deploy/demo.yaml`.
4. No single rehearsal has run AppBI K8s and Airbyte K8s together with
   production auth, managed datastores/object storage, TLS and enforcing CNI.
   **Still open, and it needs infrastructure rather than code.**
5. ~~The Airbyte connector policy is outside the rendered release overlay~~ —
   it now has `airbyte/base` + `overlays/production`, and the release gate
   renders and checks both overlays.
6. ~~651 `BETA` connectors are selectable~~ — the default launch scope is
   `SUPPORTED_ONLY`. 654 connectors are listed, 3 are selectable, and the
   create path returns `CONNECTOR_NOT_IN_LAUNCH_SCOPE` rather than relying on
   a greyed-out card.

If the production target is still `1.8.5`, run the production-shaped
certification again after the release gate is repaired. If the target moves to
a newer 1.x/2.x version, certification must be re-run.

Historical note from before the K8s certification:

Before the 1.8.5 run, certification was only on Compose 0.59.1 while
production would be Kubernetes 1.x/2.x.

That historical gap existed because 0.59.x is the last Airbyte line with a
Compose distribution, so the staging stack could not be the production target.
The 1.8.5 K8s certification now closes that specific gap; a move to any newer
1.x/2.x version must repeat the same gate.

The re-certification path is measurable rather than speculative:

```bash
python scripts/verify-engine-api.py --url <the real one>   # minutes
RUN_ENGINE_CONTRACT=1 pytest tests/test_adapter_contract.py # an hour
python scripts/e2e.py --engine airbyte-api --evidence evidence-e2e.json
python scripts/release-gate.py record --evidence evidence-e2e.json --out certification.json
python scripts/release-gate.py check certification.json
```

[docs/RUNBOOK-engine-upgrade.md](RUNBOOK-engine-upgrade.md).

## Closed since PM review v5

PM review v5 found a render-time manifest bug: `commonLabels` injected
`app.kubernetes.io/part-of: appbi-integration` into the external `kube-dns`
`podSelector`. That is now fixed with `labels.includeSelectors: false`,
rendered-output tests, and a Calico smoke run.

Product NetworkPolicy proof is now closed for the product namespace: DNS works,
an allowed database is reachable, internet and cloud metadata are blocked.

## Also open

| | Current state |
|---|---|
| Connector egress | Measured under Calico with a control. The production CIDR is now in an overlay the release gate renders and checks; cloud metadata still cannot be measured on kind, which the runbook says rather than claims |
| Airbyte API boundary | `airbyte-server-ingress` restricts the Config API to the product's api/worker pods, and `doctor` fails a production profile whose engine reports `auth: none`. **An auth-enabled certification run has not happened** |
| A real on-call rotation | Alert names no longer drift — the runbook's own copy of the rules is gone and a test compares the table against `alerts.yaml` both ways. Owners remain `TO BE ASSIGNED`, and the gate blocks on that |
| Disaster recovery | Mismatch detection is proven in both directions. A paired restore into a fresh product + Airbyte environment is **still not evidenced** |
| Database separation | The startup guard now scans every non-system schema, and `scripts/provision-db.py` provisions and verifies the least-privilege role — the SQL is no longer only in the ADR |
| One-command operations | `scripts/production.py` exists and was exercised from a wiped machine: no containers, no images, no `.env`. See below |

## Deployed from nothing, in one command

The product containers/volumes/images, Airbyte platform images, four pinned
connector images, certification cluster and `.env` were removed, and then:

```bash
python scripts/production.py install --config deploy/demo.yaml
```

Exit 0. This proved the demo profile on this machine, not a fully cold or
production-shaped machine: 37 other catalogue connector images remained and
could retain shared layers. It generated the encryption key and JWT secret, built five images,
started six containers, waited until the API was serving *and* the engine
answered, reconciled, and printed the URL. Re-running it kept the existing
secrets rather than orphaning the credentials in the database.

Two real defects surfaced by doing it rather than assuming it: the demo's
`env://` password reference resolved to nothing, so reconcile came back 401
while the install still reported success; and `doctor` run from a fresh shell
had the same gap. Both fixed — `env://` now falls back to `.env`.

## Running it

```bash
python scripts/stack.py lite       #  4 containers — API/schema work
python scripts/stack.py embedded   #  7 containers — local demo with UI
python scripts/stack.py airbyte    # 14 containers — real Airbyte, certification
python scripts/stack.py status     # what is running, and what it costs
python scripts/stack.py stop       # stop the Airbyte half, keep the product
```

The 14-container stack exists because it runs both the product and an Airbyte
deployment on one machine. It is for certification, not for editing a React
component.

`scripts/production.py` now provides the requested commands, but PM v9 found
that the production path is not yet idempotent or fail-closed: configuration is
not wired into rendered manifests, migration ordering is unsafe, and install
can return zero after release checks fail. Treat it as work in progress, not a
production entrypoint.

## Deploying

`deploy/kubernetes/` — plain manifests, `kubectl apply -k .`. API, worker,
migration job, frontend, NetworkPolicies, PDB, ingress. Postgres, Redis and
Airbyte are deliberately absent: the first two should be managed services, and
Airbyte has its own chart and lifecycle.

`base/` is the shape, `overlays/production/` supplies the environment. Apply
the overlay — the base holds a deliberately wrong database CIDR so applying it
by mistake fails closed.

**Applied to a real cluster** (kind, Kubernetes 1.30): migrations ran from
empty, API 2/2 and worker 1/1 Running with zero restarts, `/readyz` 200 while
`/readyz?deep=1` returned 503 because that cluster had no Airbyte — the
readiness split working exactly as designed.

**NetworkPolicy verified under Calico**, because kind's default CNI accepts
policies without enforcing them. From an API-labelled pod: DNS works, an
allowed database is reachable, internet and cloud metadata blocked.

Three defects those runs found that schema validation could not: a
`commonLabels` transformer that had silently rewritten the kube-dns selector
(DNS would have been blocked in any enforcing cluster), an init container
pinned to a `bitnami/kubectl` tag that does not exist, and an unset
`imagePullPolicy`. All fixed, all now covered by tests — including tests on the
**rendered** output, since every source-level check was green throughout.

Guarded by tests that the ConfigMap only names real settings, readiness is
never `?deep=1`, no pod gets a runtime socket or root, every image is one this
project builds, and the network policy is deny-by-default.

## Operations

| | |
|---|---|
| [Backup / restore](RUNBOOK-backup-restore.md) | `scripts/backup.py` — records which KEK each dump belongs to and refuses a mismatched restore |
| [Secret rotation](RUNBOOK-secret-rotation.md) | `scripts/rotate-kek.py` — rewraps data keys without decrypting a credential |
| [On-call](RUNBOOK-oncall.md) | `/metrics`, alert rules, and what each symptom means |
| [Egress](RUNBOOK-egress.md) | measured, per target, with `scripts/verify-egress.py` |
| [Airbyte workspace](RUNBOOK-airbyte-workspace.md) | why `AIRBYTE_WORKSPACE_ID` is configured and never guessed |
| [Engine upgrade](RUNBOOK-engine-upgrade.md) | how to certify a different Airbyte |
| [Engine portability](ENGINE-PORTABILITY.md) | what running on something other than Airbyte takes |

Health endpoints: `/healthz` liveness · `/readyz` load balancer · `/readyz?deep=1`
deploy gate. Do not point a load balancer at the deep one — it fails when the
engine is down, which would take the whole UI out during an engine outage.

## Gates

Per PR: backend tests (194 locally on 2026-08-24), frontend typecheck, i18n parity, secret scan,
Kubernetes manifest schema validation.
On merge to main: full engine contract, live UAT, migrations from empty,
supply-chain lock, API and UI audits.
Nightly / on demand: `airbyte-api-contract` — the real Airbyte, egress check,
and an unsigned JSON certification artifact. The `airbyte-k8s-contract` lane
does the same against Airbyte on Kubernetes.

A release requires `python scripts/release-gate.py check` to pass. It refuses
certification that is stale (>7 days), from a different commit, from a dirty
tree, from the wrong engine, or missing any of the eleven operations. PM v8
found that it does not yet bind the evidence to the live build/run, does not
check `LIC-001`, and does not inspect the separately shipped connector policy;
passing it is therefore necessary but not sufficient until those findings close.

The operations come from `compatibility.yaml` rather than a second list in the
gate — the two had already drifted, nine against eleven — and the evidence
comes from files the verifiers write (`scripts/e2e.py --evidence`). `--verified`
used to default to "all of them", which let an artifact assert its own
evidence; recording now fails without an evidence file.
