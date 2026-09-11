# AppBI Data Pipeline

**Đưa dữ liệu từ mọi hệ thống bạn đang dùng về một kho, tự động, theo lịch — rồi
biến nó thành bảng báo cáo dùng được.**

Phát triển bởi **đội Data của Base.vn**.

Hai việc, một sản phẩm:

- **Pipeline** — kết nối một **Nguồn**, chọn một **Đích**, tick những bảng cần
  lấy, đặt lịch. Dữ liệu tự về.
- **Transform** — viết model dbt ngay trong giao diện để biến dữ liệu thô thành
  bảng báo cáo, có kiểm thử và có bản phát hành.

Không viết script, không dựng cron, không ai phải nhớ chạy tay mỗi sáng.

---

## Mục lục

- [Sản phẩm này giải quyết việc gì](#sản-phẩm-này-giải-quyết-việc-gì)
- [Kết nối được những gì](#kết-nối-được-những-gì)
- [Cài đặt](#cài-đặt) ← **bắt đầu ở đây**
  - [Yêu cầu máy](#yêu-cầu-máy)
  - [Ba bước](#ba-bước)
  - [Chọn cấu hình phù hợp với máy của bạn](#chọn-cấu-hình-phù-hợp-với-máy-của-bạn)
  - [Các lệnh thường dùng](#các-lệnh-thường-dùng)
  - [Khi gặp trục trặc](#khi-gặp-trục-trặc)
- [Thử ngay bằng dữ liệu mẫu](#thử-ngay-bằng-dữ-liệu-mẫu)
- [Dùng hằng ngày](#dùng-hằng-ngày)
- [Tổ chức, workspace và phân quyền](#tổ-chức-workspace-và-phân-quyền)
- [Transform: từ dữ liệu thô sang bảng báo cáo](#transform-từ-dữ-liệu-thô-sang-bảng-báo-cáo)
- [Dành cho người vận hành](#dành-cho-người-vận-hành)
- [Bảo mật](#bảo-mật)
- [Cấu trúc mã nguồn](#cấu-trúc-mã-nguồn)

---

## Sản phẩm này giải quyết việc gì

Dữ liệu kinh doanh nằm rải rác: nhân sự ở một nơi, đơn hàng ở nơi khác, chi tiêu
quảng cáo ở nơi thứ ba. Muốn có một báo cáo nhìn được toàn cảnh thì phải gom
chúng lại — và việc gom đó thường là một mớ script chạy nhờ máy của ai đó.

AppBI làm phần gom ấy thành một sản phẩm có giao diện:

- **Kết nối một lần.** Điền thông tin đăng nhập, bấm Kiểm tra, lưu. Thông tin
  đăng nhập được mã hoá và không bao giờ hiện lại.
- **Chọn đúng thứ cần.** Xem trước các bảng và cột nguồn có, tick những gì muốn
  lấy. Đổi ý lúc nào cũng được.
- **Chạy theo lịch.** Mỗi giờ, mỗi ngày, hay bấm chạy ngay. Lần sau chỉ lấy phần
  thay đổi chứ không tải lại từ đầu.
- **Biết chuyện gì đang xảy ra.** Mỗi lần chạy có lịch sử, số dòng, thời gian và
  lý do khi hỏng — bằng tiếng Việt, kèm việc cần làm tiếp.
- **Biến dữ liệu thô thành thứ đọc được.** Dữ liệu vừa về thường chưa dùng ngay
  được: tên cột khó hiểu, cần join, cần tính toán. Transform lo phần đó.

---

## Kết nối được những gì

**Hệ thống Base.vn** — 12 connector do đội Data Base.vn viết và bảo trì:

| | |
|---|---|
| Nhân sự | HRM, Tuyển dụng, Chấm công, Nghỉ phép, Lương |
| Vận hành | Quy trình, Yêu cầu, Dịch vụ, WeWork, Tài khoản |
| Kinh doanh | CRM Deals, CRM Leads |

> **Base có hai bản cài, và token của bản này bị bản kia từ chối.** `base.vn` và
> `base.com.vn` là hai hệ thống riêng, tài khoản riêng — chỉ khách hàng biết
> mình đang ở bản nào. Mỗi Nguồn Base có ô **Tên miền Base** để chọn; sai ô đó
> thì Base trả về một lỗi **trông y hệt token hết hạn**, nên nếu token vừa lấy
> mà vẫn bị từ chối thì kiểm tra ô này *trước* khi đi cấp token mới. Đo thật:
> mười token của cùng một tài khoản đều bị từ chối trên một bản và đều chạy
> trên bản kia.
>
> Mỗi ứng dụng Base cần **token riêng của nó** — token của Workflow không đọc
> được HRM.
>
> Mười hai connector này được đánh dấu **beta**: chúng do đội Data viết và
> chạy được với tài khoản thật, nhưng con số đo đạc cho từng ứng dụng chưa đủ
> để gọi là đã chứng nhận. Nhãn sẽ lên khi có số đo, không lên khi code viết
> xong.

**Bán hàng & marketing**

- **KiotViet** — hàng hoá, đơn hàng, hoá đơn, khách hàng, tồn kho
- **Zalo Ads**, **Facebook Marketing**, **Google Ads**, **TikTok Marketing**,
  **Bing Ads**

**Kho dữ liệu và cơ sở dữ liệu**

- **Google BigQuery**, **PostgreSQL**, **Microsoft SQL Server** — cả hai chiều
- **Google Sheets** — đọc và ghi

Chưa có thứ bạn cần? **Trình tạo connector** ngay trong sản phẩm cho phép mô tả
một API bằng biểu mẫu — địa chỉ, cách xác thực, phân trang, cách lấy dữ liệu
tăng dần — rồi chạy thử và phát hành cho cả nhóm dùng. Không cần lập trình viên
và không cần chờ bản phát hành mới.

---

## Cài đặt

### Yêu cầu máy

**Chỉ cần Docker** để chạy sản phẩm. Không cần cài Node.js hay PostgreSQL — tất
cả chạy trong container.

*(Python trên máy chỉ dùng cho hai việc phụ: `run.sh` dùng nó để sinh khoá mã hoá
lần đầu — không có thì nó báo và bạn tự điền `SECRET_ENCRYPTION_KEY` vào `.env` —
và các script vận hành trong `scripts/`.)*

| | Tối thiểu | Nên có |
|---|---|---|
| CPU | 2 nhân | 4 nhân |
| RAM | 4 GB | 8 GB |
| Đĩa trống | 15 GB | 30 GB |

- **Linux / macOS**: Docker Engine 24+ kèm Docker Compose v2
- **Windows**: Docker Desktop, và chạy lệnh trong **Git Bash** hoặc PowerShell

> **RAM 4 GB thì chạy được không?** Được, nhưng phải chọn đúng cấu hình — xem
> [Chọn cấu hình phù hợp](#chọn-cấu-hình-phù-hợp-với-máy-của-bạn) bên dưới. Bản
> thân AppBI chỉ dùng khoảng **510 MB** lúc rảnh và **650–780 MB** khi đang chạy
> dbt; thứ nặng là Airbyte platform
> (**~1,9 GB**), và bạn không bắt buộc phải dùng nó.

### Ba bước

```bash
git clone https://github.com/QuangChinhDE/appbi-pipeline.git
cd appbi-pipeline
./run.sh                    # Linux, macOS, hoặc Git Bash trên Windows
```

Trên PowerShell (Windows) thì dùng `.\run.ps1` thay cho `./run.sh`.

Một lệnh là xong. **Không cần sửa gì trong `.env` trước khi chạy.** Nó tự làm:

1. Tạo `.env` từ `.env.example`, rồi **sinh `SECRET_ENCRYPTION_KEY` và
   `JWT_SECRET` riêng cho máy này**. Hai khoá này để trống trong
   `.env.example` là có chủ đích — chúng phải khác nhau ở mỗi bản triển khai.
2. Kiểm tra trước khi build: cổng có ai chiếm không, RAM có đủ cho số lần chạy
   song song đang đặt không. Cả hai đều báo *trước* khi build, kèm tên container
   đang giữ cổng và biến cần sửa.
3. Bổ sung vào `.env` những khoá mới có trong `.env.example` mà file của bạn
   chưa có — dành cho lần `git pull` sau. Giá trị bạn đã đặt không bị đụng tới.
4. Dựng image từ mã nguồn đang có, chạy migration, khởi động toàn bộ và đợi tới
   khi API thật sự phục vụ được.

Chạy lại bao nhiêu lần cũng được: khoá đã sinh **không bao giờ bị ghi đè** (ghi
đè là mất toàn bộ credential đã lưu), và `.env` được sao lưu vào
`.env.backups/` mỗi lần chạy.

Lần đầu mất khoảng **5–15 phút** (tải image, cài thư viện). Những lần sau nhanh
hơn nhiều vì Docker dùng lại cache.

> Nếu `run.sh` dừng lại và nói cổng nào đang bị chiếm: đó là một dự án khác trên
> máy bạn. Hoặc dừng nó, hoặc đổi biến mà thông báo nêu tên trong `.env`
> (`API_PORT`, `PROXY_PORT`, `FRONTEND_PORT`, `POSTGRES_PORT`) rồi chạy lại.
> Chưa có gì được build nên không mất thời gian.

Xong thì mở **http://localhost:8080** và đăng nhập:

```
admin@appbi.local  /  Admin@123456
```

> Mật khẩu mặc định là `Admin@123456`. **Đổi nó trước khi dùng cho việc thật** —
> đặt `SEED_ADMIN_PASSWORD` trong `.env` rồi chạy `./run.sh --clean`, hoặc đổi
> trong giao diện sau khi đăng nhập.

### Chọn cấu hình phù hợp với máy của bạn

Hai biến trong `.env` quyết định máy bạn cần bao nhiêu tài nguyên. Sửa xong thì
chạy lại `./run.sh` (cần dựng lại image, không chỉ khởi động lại).

#### `COMPOSE_FILE` — chạy những dịch vụ nào

| Muốn gì | Đặt `COMPOSE_FILE` thành | RAM |
|---|---|---|
| **Gọn nhất** — chỉ Pipeline, không Transform | `docker-compose.yml:docker-compose.embedded.yml` | ~560 MB |
| **Mặc định** — Pipeline + Transform | `docker-compose.yml:docker-compose.embedded.yml:docker-compose.transform.yml` | ~780 MB |
| **Đầy đủ Airbyte** — cần khi dùng Airbyte platform riêng | thêm `:docker-compose.airbyte.yml` | **+1,9 GB** |

#### `WITH_TRANSFORM` — có cài dbt vào image không

```bash
WITH_TRANSFORM=1    # mặc định, image backend 1,56 GB
WITH_TRANSFORM=0    # bỏ dbt, image backend 479 MB
```

dbt kéo theo pandas, pyarrow, numpy và bộ thư viện Google Cloud — hơn một
gigabyte. Nếu không dùng Transform thì tắt đi, image nhẹ hơn và build nhanh hơn
hẳn.

> Tắt Transform không ảnh hưởng gì tới phần còn lại: các màn hình Transform sẽ
> báo rõ "bản cài đặt này không kèm Transform" thay vì lỗi khó hiểu.

#### `ENGINE_TYPE` — chạy connector bằng cách nào

| Giá trị | Nghĩa | Phù hợp với |
|---|---|---|
| `AIRBYTE_EMBEDDED` | AppBI tự chạy connector qua Docker | máy nhỏ, môi trường phát triển *(mặc định)* |
| `AIRBYTE_API` | trỏ tới một Airbyte đã dựng sẵn ở nơi khác | production có sẵn Airbyte |

> ⚠️ **Cảnh báo bảo mật cho `AIRBYTE_EMBEDDED`:** chế độ này cần gắn Docker
> socket vào container. Container nào nói chuyện được với Docker daemon thì có
> thể khởi động container khác kèm ổ đĩa của máy chủ — tức là tương đương quyền
> root trên máy đó. Chấp nhận được cho máy phát triển; **đừng dùng cho máy chứa
> dữ liệu khách hàng thật hoặc chạy connector từ nguồn không tin cậy.** Khi đó
> hãy dùng `AIRBYTE_API`.

#### `CONNECTOR_MEMORY_LIMIT` và số lần chạy song song — cách tính RAM

Đây là chỗ dễ hết RAM nhất, và phép tính không hiển nhiên: **mỗi lần sync chạy
hai container**, một cho Nguồn và một cho Đích. Ngân sách là

```
MAX_CONCURRENT_RUNS_GLOBAL × 2 × CONNECTOR_MEMORY_LIMIT
    + ~550 MB cho toàn bộ AppBI (đã gồm Postgres)   ≤   RAM của máy
```

Con số 550 MB là **đo thật** khi bốn lần sync chạy song song: api 126 MB,
worker 123 MB, transform-worker 60 MB, frontend 46 MB, proxy 9 MB, postgres
186 MB. Mỗi container AppBI đều có trần riêng (gấp 3-4 lần mức đó, xem
`*_MEMORY_LIMIT` trong `.env.example`) để một connector ngốn RAM không kéo
`api` hay `worker` chết theo — khi hệ điều hành hết bộ nhớ, nó giết theo *kích
thước* chứ không theo *lỗi của ai*.

Với mặc định `4 × 2 × 2g`, riêng connector đã cần **16 GB**. Nên trên VM nhỏ
thì điều cần sửa là số lần chạy song song, không phải hạn mức — hai nhân CPU
thì chạy song song cũng không nhanh hơn, chỉ nhân RAM lên:

| Máy | `MAX_CONCURRENT_RUNS_GLOBAL` | `CONNECTOR_MEMORY_LIMIT` | Connector cần |
|---|---|---|---|
| 2 CPU / 4 GB | `1` | `1g` | ~2 GB |
| 2 CPU / 8 GB | `1` | `2g` | ~4 GB |
| 4 CPU / 16 GB | `2` | `2g` | ~8 GB |
| 8 CPU / 32 GB | `4` | `2g` | ~16 GB |

> **`2g` là con số đo được, không phải phỏng đoán.** Base Service trên một
> tenant thật phát ra 2.553 bản ghi nặng tổng cộng 729 MB — khoảng 326 KB mỗi
> ticket — và nó đọc 500 bản ghi mỗi trang, nên riêng một trang đã ~163 MB
> trước khi tính overhead của CDK. Với trần 1 GB, container nguồn bị giết ở
> bản ghi thứ 820 (`exit 137`); với 2 GB thì cùng lần đồng bộ đó chạy xong.
> Connector nào có bản ghi lớn — ticket, hồ sơ ứng viên, bảng lương — đều
> thuộc nhóm này.

Với 2 nhân thì chạy song song **không** nhanh hơn — nó chỉ nhân RAM lên.

> **Đừng để `CONNECTOR_MEMORY_LIMIT` trống.** Container connector khi đó không
> có trần và có thể lấy hết RAM máy; thứ dừng nó sẽ là OOM killer của hệ điều
> hành, mà nó có thể chọn `api` hoặc `worker` làm nạn nhân thay vì chính
> connector đó. Khi ấy lỗi hiện ra không liên quan gì tới nguyên nhân.

Destination BigQuery, Postgres và MSSQL của Airbyte chạy trên JVM và lấy heap
theo **tỷ lệ của trần container**. JVM mặc định chỉ lấy 25%, tức 256 MB với trần
1 GB — không đủ. `CONNECTOR_JAVA_OPTS=-XX:MaxRAMPercentage=75.0` là thứ làm cho
cái trần dùng được; đổi trần thì không phải đổi gì thêm.

#### `lookback_window` — khi bản ghi về muộn

Đồng bộ tăng dần chạy cửa sổ `[mốc lần trước, hiện tại]`. Một bản ghi có thời
điểm cập nhật *trước* mốc đó nhưng về *sau* lần đồng bộ lẽ ra phải lấy nó — lệch
giờ giữa máy chủ nguồn và máy chạy đồng bộ là đủ — sẽ không bao giờ được đọc
lại, vì tăng dần không nhìn lùi.

Mỗi Nguồn có ô **"Đọc lùi lại mỗi lần đồng bộ"**, mặc định `PT0S` (tắt). Bật
bằng một khoảng ISO-8601: `PT10M` là mười phút.

> **Chỉ bật khi đích ghi theo chế độ Append + khử trùng lặp.** Phần đọc chồng
> khi đó miễn phí vì trùng khoá chính sẽ bị khử. Với Append thuần, nó được ghi
> thêm một lần nữa thành bản ghi trùng. Đó là lý do mặc định là tắt: chế độ ghi
> ở đích là lựa chọn của từng workspace, không phải quyết định của connector.

#### Hai pipeline trên cùng một kho dữ liệu

Nếu hai pipeline có stream **trùng tên** — Base Service và Base Workflow đều có
stream `stage`, Base Service và Base Request đều có `group` — thì mặc định
chúng ghi vào **cùng một bảng** và phá bảng tạm (`<tên>_airbyte_tmp`) của nhau
khi hai lần chạy trùng thời điểm.

Có hai cách tách, dùng cách nào cũng được:

| Cách | Đặt ở đâu | Kết quả |
|---|---|---|
| **Tiền tố bảng** (`stream_prefix`) | Pipeline → Cài đặt → Cấu hình nâng cao | `service_stage` và `workflow_stage` trong cùng một schema |
| **Schema riêng** | Đích dữ liệu → `schema` | `base_service.stage` và `base_workflow.stage` |

**Tiền tố hoạt động ở cả hai engine.** Airbyte làm việc này ở tầng nền
(`NamespacingMapper` trong replication worker, không phải trong connector), và
`AIRBYTE_EMBEDDED` làm đúng như vậy: dựng **hai catalog** cho mỗi lần chạy —
Nguồn nhận tên nó đã phát hiện, Đích nhận tên mà bảng cần mang — rồi đổi tên
stream trên từng bản ghi đi qua.

Con trỏ tăng dần (`state`) luôn được lưu theo **tên của Nguồn**: Đích trả về
tên đã gắn tiền tố, và phần hoàn nguyên cắt nó đi trước khi ghi. Nếu không,
lần chạy sau sẽ đưa cho Nguồn một con trỏ mang tên stream mà nó chưa từng
phát ra.

`namespace_format` cũng hoạt động, với `${SOURCE_NAMESPACE}` là chỗ thay thế
namespace của Nguồn — giống hệt cú pháp của Airbyte.

> Trước đây `stream_prefix` bị lưu rồi **bỏ qua trong im lặng** ở chế độ
> `AIRBYTE_EMBEDDED`. Base Service và Base Workflow đã được đặt tiền tố đúng để
> khỏi đụng nhau, rồi vẫn ghi chung một bảng: 4 lần hỏng trong 36 lần chạy.

#### Ví dụ: VM 2 CPU / 4 GB RAM

Thêm `docker-compose.production.yml` vào cuối `COMPOSE_FILE`. Nó đặt sẵn
`restart: unless-stopped` cho mọi dịch vụ, hạ đồng thời xuống 1, và tắt dữ liệu
mẫu — đúng những thứ mà bản mặc định (vốn tinh chỉnh cho máy lập trình, nơi
luôn có người ngồi cạnh) không có.

```bash
# .env
COMPOSE_PATH_SEPARATOR=:
COMPOSE_FILE=docker-compose.yml:docker-compose.embedded.yml:docker-compose.transform.yml:docker-compose.production.yml
WITH_TRANSFORM=1
ENGINE_TYPE=AIRBYTE_EMBEDDED
CONNECTOR_MEMORY_LIMIT=2g
```

Overlay đã đặt hộ `MAX_CONCURRENT_RUNS_GLOBAL=1`, `WORKER_MAX_PARALLEL_SYNCS=1`,
`TRANSFORM_WORKER_MAX_PARALLEL=1` và `SEED_DEMO_DATA=0`. Cần khác đi thì ghi đè
trong `.env`.

> **Vì sao không tự sửa `docker-compose.yml`?** Sửa tay vào file gốc thì lần
> `git pull` sau sẽ xung đột. Overlay là file riêng, `git pull` không đụng tới.
> Một bản triển khai thực tế đã phải tự viết lại đúng file này — nên giờ nó nằm
> sẵn trong repo.

#### Khi connector bị giết vì hết bộ nhớ

Một trang dữ liệu được giữ nguyên trong bộ nhớ của container nguồn trước khi
ghi ra. Bản ghi càng to thì trang càng phải nhỏ. Có hai nấc, nấc dưới thắng:

| Đặt ở đâu | Phạm vi | Khi nào dùng |
|---|---|---|
| Ô **Số bản ghi mỗi lần gọi** trong form Nguồn | một nguồn | một tenant có bản ghi to bất thường |
| `CONNECTOR_DEFAULT_PAGE_SIZE` trong `.env` | cả cài đặt | máy nhỏ, muốn hạ hết |

Ví dụ đo được: ticket của Base Work trung bình ~326 KB, một trang 500 bản ghi
là ~163 MB — hạ xuống 100 thì bộ nhớ giảm khoảng năm lần, đổi lại số lần gọi
API tăng năm lần. Chỉ có tác dụng với những luồng mà connector tự điều khiển
được cỡ trang; luồng nào không khai báo thì giữ nguyên, vì cỡ trang cũng là
thứ CDK dùng để biết đã đọc hết.

> Đừng sửa `page_size` thẳng vào `backend/app/connectors/`. Bản vá đó mất sau
> mỗi lần cập nhật, và áp cho mọi tenant chứ không riêng tenant cần nó.

#### Lỗi tạm thời tự chạy lại

Nguồn từ chối vài phút rồi bình thường trở lại là chuyện thường. Sản phẩm tự
chạy lại, chờ lâu dần: 60s, 120s, 240s, tối đa 3 lần.

```bash
AUTO_RETRY_MAX_ATTEMPTS=3     # 0 là tắt hẳn
AUTO_RETRY_BASE_SECONDS=60
AUTO_RETRY_MAX_SECONDS=1800
```

Chỉ những lỗi được phân loại là **tạm thời** mới được chạy lại — mất kết nối
giữa chừng, hết giờ chờ, bị giới hạn tần suất. Token sai thì không: nó sẽ
không tự đúng lên, và thử lại ba lần chỉ làm chậm mất vài phút cái thông báo
nói cho bạn biết phải sửa gì. Lần chạy lại tự động mang nhãn `AUTO_RETRY` để
phân biệt với lần bạn tự bấm.

### Các lệnh thường dùng

| Lệnh | Việc |
|---|---|
| `./run.sh` | dựng lại và khởi động tất cả |
| `./run.sh --pull` | lấy mã mới nhất trước rồi làm như trên |
| `./run.sh --status` | đang chạy những gì |
| `./run.sh --logs api` | theo dõi nhật ký một dịch vụ |
| `./run.sh --stop` | dừng, không mất gì |
| `./run.sh --fresh` | tạo lại container từ đầu, **giữ** dữ liệu |
| `./run.sh --clean` | **xoá cơ sở dữ liệu** rồi dựng lại (có hỏi xác nhận) |
| `./run.sh --down` | xoá container và network, giữ dữ liệu |

**Luôn dùng `./run.sh` thay vì `docker compose up` từng dịch vụ.** Container chạy
mã đã được nướng sẵn vào image, nên khởi động lại một dịch vụ mà không dựng lại
image sẽ chạy mã cũ, và job migration thì không chạy — hỏng theo kiểu trông hệt
như lỗi sản phẩm.

### Khi gặp trục trặc

<details>
<summary><b>Port 8080 đã bị chiếm</b></summary>

Thông báo: `Ports are not available: ... :8080`

Đổi cổng trong `.env` rồi chạy lại:

```bash
PROXY_PORT=8090      # rồi mở http://localhost:8090
```

Muốn biết ai đang giữ cổng đó:

```bash
# Linux / macOS
lsof -i :8080
# Windows (PowerShell)
Get-Process -Id (Get-NetTCPConnection -LocalPort 8080).OwningProcess
```
</details>

<details>
<summary><b>Sync hỏng vì hết RAM, hoặc <code>exit 137</code></b></summary>

`exit 137` là 128+9: container bị kill. Nếu lần chạy không phải do bạn hủy và
cũng không quá thời gian, thì gần như luôn là connector vượt bộ nhớ.

Mỗi lần sync là **hai** container. Giảm số chạy song song trước, vì đó là thứ
nhân RAM lên:

```bash
# .env
MAX_CONCURRENT_RUNS_GLOBAL=1
MAX_CONCURRENT_RUNS_PER_WORKSPACE=1
```

Rồi `./run.sh`. Nếu vẫn hỏng mà máy còn RAM trống, nâng trần cho từng connector
(mặc định đã là `2g`):

```bash
CONNECTOR_MEMORY_LIMIT=3g
```

Muốn biết connector thực sự cần bao nhiêu: xem `bytes` của lần chạy chia cho
số bản ghi. Bản ghi ~300 KB trở lên, nhân với cỡ trang, là đã ra con số.

Xem thực tế container đang dùng bao nhiêu:

```bash
docker stats --no-stream
```

Nếu chính `api` hoặc `worker` bị chết thay vì connector, đó là dấu hiệu
`CONNECTOR_MEMORY_LIMIT` đang để trống: connector không có trần, và hệ điều
hành chọn nạn nhân theo bộ nhớ chứ không theo lỗi của ai.

Xem [cách tính RAM](#connector_memory_limit-và-số-lần-chạy-song-song--cách-tính-ram).
</details>

<details>
<summary><b>Hết dung lượng đĩa khi build</b></summary>

Docker giữ lại rất nhiều build cache. Dọn:

```bash
docker builder prune -f      # xoá cache build (an toàn, hay lấy lại nhiều GB nhất)
docker image prune -f        # xoá image mồ côi
```

Cần dọn mạnh hơn — **cẩn thận, `--volumes` sẽ xoá cả cơ sở dữ liệu**:

```bash
docker system prune -a --volumes
```
</details>

<details>
<summary><b>Build lâu hoặc treo ở bước cài thư viện</b></summary>

Lần đầu cần tải khoảng 1,5 GB thư viện Python. Nếu mạng chậm, đặt
`WITH_TRANSFORM=0` để bỏ qua dbt — image còn 479 MB và build nhanh hơn nhiều.
Cần Transform thì bật lại sau.
</details>

<details>
<summary><b>Quên mật khẩu đăng nhập</b></summary>

Mật khẩu nằm trong `.env`:

Mặc định là `Admin@123456` cho tài khoản `admin@appbi.local`. Nếu đã đổi bằng
`SEED_ADMIN_PASSWORD`:

```bash
grep SEED_ADMIN_PASSWORD .env
```

Ba tài khoản demo còn lại — `dataadmin@`, `operator@`, `analyst@` — luôn dùng
`Admin@123456` và **không** đổi theo `SEED_ADMIN_PASSWORD`. Trước khi dùng thật,
hãy xoá chúng hoặc đổi mật khẩu trong giao diện.
</details>

<details>
<summary><b><code>SECRET_ENCRYPTION_KEY</code> không dùng được / app không khởi động</b></summary>

Thông báo dạng `SECRET_ENCRYPTION_KEY is not usable` hoặc `... is N characters;
it has to be 44`.

Khoá phải là 32 byte dạng urlsafe-base64, tức đúng **44 ký tự**. `./run.sh` sinh
sẵn cho bạn, nên gặp lỗi này thường là vì `.env` được tạo tay hoặc giá trị bị sửa.

Nếu **chưa lưu Nguồn/Đích nào**, cứ sinh khoá mới:

```bash
openssl rand -base64 32 | tr '+/' '-_'
```

rồi dán vào `.env`. Không có `openssl` thì:

```bash
head -c 32 /dev/urandom | base64 | tr '+/' '-_'
```

Nếu **đã lưu credential rồi**, đừng sinh khoá mới — khoá cũ mới giải mã được
chúng. Tìm lại trong bản sao lưu:

```bash
ls -t .env.backups/          # bản gần nhất nằm trên cùng
grep SECRET_ENCRYPTION_KEY .env.backups/env-*.bak
```

Sản phẩm giờ từ chối khởi động khi khoá không dùng được, ở mọi môi trường. Trước
đây nó khởi động bình thường và chỉ hỏng lúc bạn lưu Nguồn đầu tiên — xa nguyên
nhân tới mức không ai đoán được.
</details>

<details>
<summary><b>Lỡ xoá hoặc sửa <code>.env</code></b></summary>

`SECRET_ENCRYPTION_KEY` mã hoá toàn bộ kho thông tin đăng nhập. **Sinh khoá mới
sẽ làm mọi thông tin đăng nhập đã lưu không giải mã được nữa** — phải nhập lại
từng Nguồn, từng Đích.

Vì vậy mỗi lần chạy `run.sh` đều tự sao lưu `.env` vào `.env.backups/`:

```bash
ls -t .env.backups/     # bản gần nhất nằm trên cùng
```
</details>

<details>
<summary><b>Windows: <code>./run.sh</code> báo lỗi</b></summary>

Dùng **Git Bash** (không phải CMD), hoặc dùng bản PowerShell:

```powershell
.\run.ps1
```
</details>

<details>
<summary><b>Muốn xem log chi tiết</b></summary>

```bash
./run.sh --logs api                 # API
./run.sh --logs transform-worker    # tiến trình chạy dbt
docker compose ps                   # tình trạng tất cả dịch vụ
```
</details>

---

## Thử ngay bằng dữ liệu mẫu

Cài đặt kèm sẵn hai cơ sở dữ liệu để chạy thử mà không cần đụng vào hệ thống
thật:

- **`demo_source`** — 500 khách hàng, 2.000 đơn hàng, 200 sản phẩm
- **`demo_warehouse`** — nơi dữ liệu chảy về

Ba bước trong giao diện:

1. **Nguồn dữ liệu → Thêm → PostgreSQL** — máy chủ `postgres`, cơ sở dữ liệu
   `demo_source`, schema `shop`, tài khoản `demo_reader` / `demo_reader_pw`
2. **Đích dữ liệu → Thêm → PostgreSQL** — cơ sở dữ liệu `demo_warehouse`, schema
   `public`, tài khoản `demo_writer` / `demo_writer_pw`
3. **Pipeline → Tạo** — chọn hai cái vừa tạo, tick bảng `customers` và `orders`,
   chọn đồng bộ tăng dần theo `updated_at`, đặt lịch, Tạo

Không muốn điền gì cả thì chọn nguồn **Sample Data** — nó tự sinh dữ liệu.

---

## Dùng hằng ngày

**Tổng quan** cho biết cái gì đang chạy, cái gì vừa hỏng, và bao nhiêu dòng đã về
hôm nay.

**Pipeline** là nơi làm việc chính. Mỗi pipeline có:

- *Trạng thái* — lần chạy gần nhất, lần kế tiếp, và mốc đồng bộ hiện tại (sửa
  được, nếu cần chạy lại từ một thời điểm khác)
- *Lịch sử chạy* — từng lần, từng bảng, bao nhiêu dòng
- *Schema* — bảng và cột nào đang lấy, đổi bất cứ lúc nào
- *Cài đặt* — lịch, tên, xoá

**Cảnh báo** báo khi có gì hỏng, qua email hoặc webhook, và nói rõ cần làm gì —
"Thông tin đăng nhập không còn hợp lệ" kèm nút mở đúng chỗ để sửa, chứ không phải
một dòng lỗi kỹ thuật.

Sản phẩm dùng tiếng Việt, có thể chuyển sang tiếng Anh.

---

## Tổ chức, workspace và phân quyền

Ba tầng, và mỗi tầng trả lời một câu hỏi khác nhau:

```
Tổ chức  ──sở hữu──▶  Workspace  ──chứa──▶  Nguồn / Pipeline / Transform
   │                      │
 OrgRole                Role
 "ai mở được             "làm được gì
  workspace nào"          bên trong"
```

### Tổ chức: ai mở được workspace nào

Tổ chức là đơn vị một khách hàng ký hợp đồng, và là nơi các workspace thuộc về.

| Vai trò tổ chức | Quyền |
|---|---|
| `ORG_OWNER` | Toàn quyền, kể cả xoá workspace và đổi vai trò Org Owner khác |
| `ORG_ADMIN` | Tạo workspace, quản trị thành viên tổ chức — không xoá được |
| `ORG_MEMBER` | Chỉ vào được những workspace được thêm vào |

**`ORG_OWNER` và `ORG_ADMIN` tự động là Owner trong mọi workspace của tổ chức**,
không cần thêm từng cái một. Đó là lý do tầng này tồn tại: trước đây một
workspace tạo hôm nay thì quản trị viên không thấy nó cho tới khi có người thêm
họ vào — và họ phải làm việc đó cho từng workspace.

Hai chốt an toàn: không hạ được **Org Owner cuối cùng** của tổ chức, và không
xoá được **workspace cuối cùng** — cả hai đều sẽ tạo ra một căn phòng khoá từ
bên ngoài, vì mọi màn hình đều dựng bối cảnh từ một workspace.

Xoá workspace **bị từ chối khi nó còn pipeline, nguồn, đích hay dự án
Transform**, kèm danh sách cụ thể. Xoá thẳng dòng workspace sẽ cascade mất
chúng mà không dọn tài nguyên phía engine — connection Airbyte vẫn chạy, secret
vẫn nằm trong kho. Xoá từng cái trước là đường đã dọn đúng.

### Workspace: làm được gì bên trong

Sáu vai trò, gán được cho từng thành viên của từng workspace:

| Vai trò | Dùng cho |
|---|---|
| `OWNER` | Toàn quyền, kể cả thành viên và tua lại dữ liệu |
| `DATA_ADMIN` | Tạo và vận hành nguồn / đích / pipeline / transform |
| `CONNECTOR_DEV` | Viết connector trong Builder — **không đụng pipeline** |
| `OPERATOR` | Chạy, hủy, thử lại — không sửa cấu hình |
| `ANALYST` | Chỉ xem, kèm xem dữ liệu Transform |
| `AUDITOR` | Xem cấu hình và nhật ký kiểm toán, **không xem dữ liệu** |

Ba quyền được tách riêng vì chúng chạm tới dữ liệu thật, và gộp chung là sai:

- **`reset`** — tua con trỏ đồng bộ, `dbt --full-refresh`, duyệt thay đổi schema
  làm mất stream. Những việc này *ghi đè dữ liệu đã giao vào kho đích*, khác
  hẳn "bấm Chạy". Chỉ Owner có.
- **`manage_credentials`** — đổi mật khẩu mà một kết nối dùng để đăng nhập. Đổi
  tên một nguồn là việc khác.
- **`view_data`** — đọc chính các bản ghi, không chỉ cấu hình và số đếm. Nhờ nó
  mà một Auditor rà soát được cấu hình của pipeline chở dữ liệu họ không được
  đọc.

Backend là nơi duy nhất quyết định; giao diện chỉ ẩn nút để màn hình không mời
người ta bấm thứ sẽ bị từ chối. `scripts/verify-permissions.py` kiểm tra đúng
điều đó trên một bản triển khai đang chạy:

```bash
python scripts/verify-permissions.py     --account owner@example.com:... --account analyst@example.com:...
```

Nó báo cả hai chiều — `HOLE` khi endpoint cho qua thứ ma trận cấm, và
`OVER-BLOCKED` khi ngược lại — và thoát mã 1 nên chặn được deploy.

---

## Transform: từ dữ liệu thô sang bảng báo cáo

Dữ liệu vừa đồng bộ về thường chưa dùng ngay được — tên cột khó hiểu, phải join
nhiều bảng, phải tính toán. **Transform** là nơi làm việc đó, bằng
[dbt](https://docs.getdbt.com/) chạy thật bên trong sản phẩm.

**Tệp dbt là bản gốc.** Mỗi Transform là một dự án dbt thật: `.sql`, `.yml`,
`dbt_project.yml` — mở được, sửa được, tải về được, đẩy lên Git được. AppBI
không giấu dbt sau một lớp trừu tượng nào.

### Bắt đầu nhanh

1. **Transform → Dự án mới** — chọn "Tạo dự án dbt mới", chọn kho dữ liệu, đặt
   tên. Xong là có một dự án dbt chuẩn kèm model mẫu.
2. **Bấm nút 🪄 "Tạo model từ bảng"** — chọn một bảng trong kho, tick những cột
   cần, đặt tên. AppBI viết ra tệp `.sql` và YAML đúng chuẩn dbt cho bạn.
3. **Gõ `dbt build` rồi bấm Chạy** — xem kết quả từng model, từng test.
4. **Xuất bản** — AppBI build thử bản đó trước; chỉ khi thành công mới đưa vào
   chạy thật.

Chưa biết dbt cũng dùng được: bước 2 là một biểu mẫu, và thứ nó tạo ra là tệp
dbt bình thường mà bạn sửa tay lúc nào cũng được.

### Những gì có sẵn

- **Trình soạn thảo** có gợi ý `ref()` / `source()`, báo lỗi ngay khi lưu
- **Preview** — xem thử kết quả một model trước khi ghi vào kho
- **Sơ đồ phụ thuộc** — model nào phụ thuộc model nào
- **Kết nối GitHub** — kéo về, sửa, commit, đẩy lên; hai chiều
- **Lịch chạy** và **bản phát hành** — bản nháp và bản đang chạy thật tách bạch

### Không cần Transform?

Đặt `WITH_TRANSFORM=0` và bỏ `docker-compose.transform.yml` khỏi `COMPOSE_FILE`.
Xem [Chọn cấu hình](#chọn-cấu-hình-phù-hợp-với-máy-của-bạn).

---

## Dành cho người vận hành

```bash
docker compose ps                  # tình trạng các dịch vụ
docker compose logs -f api         # nhật ký, có trace_id để lần theo
python scripts/backup.py           # sao lưu
python scripts/reconcile.py        # đối chiếu sau khi khôi phục
```

**Một lần chạy hỏng thì đọc ở đâu.** Dòng `run.terminal` trong log của worker
mang theo lý do, không chỉ trạng thái:

```bash
docker compose logs worker | grep run.terminal
```

```json
{"message": "run.terminal", "status": "FAILED", "records": 1319,
 "attempt": 2, "error_code": "CONNECTOR_STREAM_INTERRUPTED",
 "remediation": "RETRY_LATER", "error": "Kết nối tới nguồn bị ngắt giữa chừng.",
 "auto_retry_in": 120}
```

`auto_retry_in` cho biết sản phẩm đã tự hẹn chạy lại sau bao nhiêu giây, hay
`null` nếu lỗi này không đáng thử lại.

**Dọn đĩa.** Mỗi lần build để lại image cũ và cache; một máy triển khai vài
tháng có thể đọng vài GB không ai giữ. Docker không tự dọn:

```bash
docker image prune -af --filter "until=168h"   # image không container nào dùng, cũ hơn 7 ngày
docker builder prune -af --filter "until=168h" # cache build
docker system df                                # xem còn đọng bao nhiêu
```

Đặt vào cron hằng tuần là đủ. Đừng dùng `docker system prune -a --volumes`:
`--volumes` sẽ xoá cả `pgdata`, tức toàn bộ cơ sở dữ liệu.

| Địa chỉ | Trả lời | Dùng cho |
|---|---|---|
| `/healthz` | tiến trình còn sống không | liveness probe |
| `/readyz` | phục vụ được chưa | load balancer |
| `/readyz?deep=1` | toàn bộ chuỗi có khoẻ không | cổng kiểm khi deploy |
| `/metrics` | số liệu Prometheus | hệ thống giám sát nội bộ |

Đừng trỏ load balancer vào `?deep=1` — nó đỏ khi thành phần bên dưới trục trặc,
và sẽ rút mọi máy chủ khỏi vòng phục vụ đúng lúc người ta cần đọc lịch sử chạy để
biết chuyện gì đang xảy ra.

**Triển khai production:**

```bash
python scripts/production.py install --config deploy/demo.yaml
```

Kubernetes: xem `deploy/kubernetes/` — manifest Kustomize đầy đủ gồm API,
worker, job migration, giao diện, NetworkPolicy và ingress.

**Lưu trữ tệp dbt ở S3** (khuyến nghị cho production nhiều máy): đặt
`TRANSFORM_STORAGE_BACKEND=s3` và các biến `TRANSFORM_STORAGE_S3_*` trong `.env`.
Chạy thử tại chỗ bằng MinIO thì thêm `docker-compose.storage.yml` vào
`COMPOSE_FILE`.

---

## Bảo mật

- Thông tin đăng nhập được mã hoá bằng khoá riêng của từng cài đặt, lưu tách khỏi
  phần cấu hình còn lại, và **không bao giờ hiển thị lại** sau khi lưu — kể cả
  cho quản trị viên.
- Phân quyền hai tầng: vai trò tổ chức quyết định mở được workspace nào, vai
  trò workspace quyết định làm được gì bên trong. Ba quyền chạm tới dữ liệu
  thật — tua lại dữ liệu, đổi thông tin đăng nhập, xem chính các bản ghi — được
  tách riêng thay vì gộp vào quyền "sửa". Xem
  [Tổ chức, workspace và phân quyền](#tổ-chức-workspace-và-phân-quyền).
- Mọi thao tác chạm vào thông tin đăng nhập hay dữ liệu đều được ghi nhật ký kiểm
  toán, kèm giá trị trước và sau.
- Chỉ những địa chỉ được phép mới gọi ra ngoài được; nhật ký tự che thông tin
  nhạy cảm.
- Dự án dbt do người dùng viết chạy trong tiến trình riêng, **không nhận được**
  biến môi trường của AppBI — không thấy `DATABASE_URL`, khoá mã hoá hay khoá API
  nào.

**Trước khi dùng cho việc thật, nhớ:**

1. Đổi mật khẩu quản trị (`SEED_ADMIN_PASSWORD`, hoặc đổi trong giao diện)
2. Sao lưu `.env` — mất `SECRET_ENCRYPTION_KEY` là mất toàn bộ thông tin đăng
   nhập, không giải mã lại được. `run.sh` tự sao lưu vào `.env.backups/` mỗi
   lần chạy.
3. Đặt `APP_ENV=production` và `COOKIE_SECURE=true`. Ở chế độ production, sản
   phẩm **từ chối khởi động** nếu còn `SEED_DEMO_DATA=true` hoặc `JWT_SECRET`
   vẫn là giá trị ship kèm repository — hai thứ đều tạo ra tài khoản hoặc phiên
   mà bất kỳ ai đọc repo này cũng dựng lại được.
4. Cân nhắc `ENGINE_TYPE=AIRBYTE_API` thay vì `AIRBYTE_EMBEDDED`
   ([lý do](#engine_type--chạy-connector-bằng-cách-nào))

> `JWT_SECRET` và `SECRET_ENCRYPTION_KEY` **do `run.sh` sinh riêng cho từng
> máy** ngay lần chạy đầu, nên không có bước "đặt khoá" nào phải làm tay. Sản
> phẩm cũng từ chối khởi động nếu `SECRET_ENCRYPTION_KEY` không dùng được — ở
> mọi môi trường, không riêng production, vì đó là sai sót duy nhất im lặng cho
> tới lúc lưu Nguồn đầu tiên.

---

## Cấu trúc mã nguồn

```
backend/     API, bộ điều phối, Transform, và các connector do đội Data viết
frontend/    Giao diện Next.js
scripts/     Cài đặt, sao lưu, khôi phục, xoay khoá, vận hành
deploy/      Kubernetes, giám sát, cấu hình môi trường
docker/      Cấu hình nginx và khởi tạo cơ sở dữ liệu
```

Các tệp `docker-compose.*.yml` là những mảnh ghép tuỳ chọn — `COMPOSE_FILE`
trong `.env` quyết định dùng mảnh nào:

| Tệp | Thêm vào |
|---|---|
| `docker-compose.yml` | phần lõi: API, worker, giao diện, cơ sở dữ liệu *(luôn cần)* |
| `docker-compose.embedded.yml` | chạy connector ngay trên máy này |
| `docker-compose.transform.yml` | tiến trình chạy dbt |
| `docker-compose.airbyte.yml` | Airbyte platform đầy đủ |
| `docker-compose.storage.yml` | MinIO, để thử lưu trữ S3 tại chỗ |

---

<sub>Một sản phẩm của đội Data, Base.vn.</sub>
