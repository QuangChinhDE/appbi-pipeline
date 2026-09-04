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

Một lệnh là xong. Nó tự làm những việc sau:

1. Tạo `.env` từ `.env.example` và **tự sinh khoá mã hoá** cho bạn
2. Dựng image từ mã nguồn đang có
3. Chạy migration cơ sở dữ liệu
4. Khởi động toàn bộ và đợi tới khi API thật sự phục vụ được

Lần đầu mất khoảng **5–15 phút** (tải image, cài thư viện). Những lần sau nhanh
hơn nhiều vì Docker dùng lại cache.

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

#### Ví dụ: VM 2 CPU / 4 GB RAM

```bash
# .env
COMPOSE_PATH_SEPARATOR=:
COMPOSE_FILE=docker-compose.yml:docker-compose.embedded.yml:docker-compose.transform.yml
WITH_TRANSFORM=1
ENGINE_TYPE=AIRBYTE_EMBEDDED
MAX_CONCURRENT_RUNS_GLOBAL=1     # quan trọng: connector khá nặng
```

`MAX_CONCURRENT_RUNS_GLOBAL` mặc định là 4. Trên máy 4 GB, bốn connector chạy
cùng lúc sẽ hết RAM — đặt về `1` (nhiều nhất là `2`).

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
- Phân quyền theo vai trò, phạm vi theo workspace.
- Mọi thao tác chạm vào thông tin đăng nhập hay dữ liệu đều được ghi nhật ký kiểm
  toán, kèm giá trị trước và sau.
- Chỉ những địa chỉ được phép mới gọi ra ngoài được; nhật ký tự che thông tin
  nhạy cảm.
- Dự án dbt do người dùng viết chạy trong tiến trình riêng, **không nhận được**
  biến môi trường của AppBI — không thấy `DATABASE_URL`, khoá mã hoá hay khoá API
  nào.

**Trước khi dùng cho việc thật, nhớ:**

1. Đổi mật khẩu quản trị (`SEED_ADMIN_PASSWORD`, hoặc đổi trong giao diện)
2. Đặt `JWT_SECRET` thành một chuỗi ngẫu nhiên
3. Sao lưu `.env` — mất `SECRET_ENCRYPTION_KEY` là mất toàn bộ thông tin đăng nhập
4. Cân nhắc `ENGINE_TYPE=AIRBYTE_API` thay vì `AIRBYTE_EMBEDDED`
   ([lý do](#engine_type--chạy-connector-bằng-cách-nào))

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
