# AppBI Data Pipeline

**Đưa dữ liệu từ các hệ thống bạn đang dùng về một kho dữ liệu, tự động và theo
lịch, rồi biến nó thành bảng báo cáo dùng được.**

Phát triển bởi **đội Data của Base.vn**.

- **Pipeline:** kết nối một **Nguồn**, chọn một **Đích**, chọn bảng cần lấy rồi
  đặt lịch. Dữ liệu tự về.
- **Transform:** viết model dbt ngay trong giao diện để biến dữ liệu thô thành
  bảng báo cáo. Có kiểm thử và có bản phát hành.
- **Connector Builder:** tự tạo connector cho một REST API chưa có trong danh
  mục, không cần lập trình.

> 📘 **Hướng dẫn đầy đủ:** [docs/huong-dan-pipeline/README.md](docs/huong-dan-pipeline/README.md).
> Tài liệu có ảnh chụp từng màn hình, bài thực hành và phần xử lý sự cố. README
> này chỉ tóm tắt; mỗi mục bên dưới ghi rõ cần đọc mục nào trong hướng dẫn để
> xem chi tiết.

---

## Kết nối được những gì

| Nhóm | Connector |
|---|---|
| **Base.vn** (14) | HRM, Tuyển dụng, Chấm công, Nghỉ phép, Lương, Quy trình, Yêu cầu, Dịch vụ, WeWork, Tài khoản, CRM Deals, CRM Leads, Base Table, Base Schedule |
| Bán hàng & marketing | KiotViet, Zalo Ads, Facebook Marketing, Google Ads, TikTok Marketing, Bing Ads |
| Kho dữ liệu & CSDL | Google BigQuery, PostgreSQL, Microsoft SQL Server, Google Sheets |

Chưa có thứ bạn cần thì dùng **Connector Builder** (xem mục 7.4 của hướng dẫn).

> **Với nguồn Base:** chọn đúng **tên miền** (`base.vn` hay `base.com.vn`), vì
> token của bản này bị bản kia từ chối với một lỗi trông y hệt token hết hạn.
> Mỗi ứng dụng Base cần **token riêng của nó**.

---

## Cài đặt trong 3 bước

**Cần có:** Git và Docker (Docker Desktop trên Windows, chạy Linux containers).
Không cần cài Node.js hay PostgreSQL trên máy.

| | Tối thiểu | Nên có |
|---|---|---|
| CPU | 2 nhân | 4 nhân |
| RAM | 8 GB | 16 GB |
| Đĩa trống | 15 GB | 30 GB |

```bash
git clone https://github.com/QuangChinhDE/appbi-pipeline.git
cd appbi-pipeline
./run.sh                 # Linux, macOS, Git Bash
```

```powershell
# Windows PowerShell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1
```

Script tự tạo `.env`, sinh khoá mã hoá riêng cho máy, build image, chạy
migration rồi khởi động toàn bộ. Lần đầu mất khoảng 5–15 phút.

Xong thì mở **http://localhost:8080** và đăng nhập bằng email và mật khẩu ghi
trong `.env` (`SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD`). Muốn đổi tài khoản thì
sửa hai dòng đó rồi chạy lại script.

**Kiểm tra cài đặt đã ổn:** `docker compose ps -a` phải cho thấy `migrate` thoát
mã 0 và các dịch vụ còn lại đang Up. Mở http://localhost:8080/readyz?deep=1 phải
trả về HTTP 200.

📖 Chi tiết: hướng dẫn mục **1–5** (chuẩn bị máy, file `.env`, cập nhật code,
khởi chạy và kiểm tra).

---

## Pipeline đầu tiên trong 5 phút

Bản cài sẵn có hai cơ sở dữ liệu mẫu để thực hành. Không cần tài khoản bên
ngoài:

1. **Nguồn dữ liệu → Thêm → PostgreSQL.** Host `postgres`, port `5432`, database
   `demo_source`, schema `shop`, user `demo_reader` / `demo_reader_pw`.
2. **Đích dữ liệu → Thêm → PostgreSQL.** Database `demo_warehouse`, user
   `demo_writer` / `demo_writer_pw`.
3. **Pipeline → Tạo.** Chọn Nguồn và Đích vừa tạo, chỉ tick bảng `customers`,
   để lịch thủ công, đặt **Mẫu namespace ở đích** là `huong_dan`.
4. Bấm **Chạy ngay**, rồi theo dõi trạng thái `QUEUED → RUNNING → SUCCEEDED` và
   số dòng của từng bảng.
5. Khi lần chạy thử đã đúng thì mới bật lịch: theo khoảng thời gian, hằng ngày
   hoặc cron.

> Trong container, `postgres:5432` là địa chỉ đúng. `localhost` bên trong
> connector là chính container đó, không phải máy của bạn.

📖 Chi tiết: hướng dẫn mục **6** (kèm ảnh từng bước và các chế độ đồng bộ).

---

## Dùng hằng ngày

- **Tổng quan:** cho biết pipeline nào hỏng, dữ liệu nào đã cũ, hôm nay về bao
  nhiêu dòng.
- **Pipeline:** mỗi pipeline có trạng thái, lịch sử chạy theo từng bảng, phần
  schema để đổi bảng/cột cần lấy, và phần cài đặt lịch.
- **Cảnh báo:** báo qua email hoặc webhook khi có gì hỏng, kèm việc cần làm
  tiếp.
- **Lỗi tạm thời** (mất kết nối, bị giới hạn tần suất) được tự chạy lại tối đa 3
  lần. Token sai thì không chạy lại.
- **Ngôn ngữ:** chuyển tiếng Việt / tiếng Anh trong menu tài khoản.

📖 Chi tiết: hướng dẫn mục **7.1–7.2**.

## Transform: từ dữ liệu thô sang bảng báo cáo

Mỗi Transform là một dự án dbt thật, gồm `.sql`, `.yml` và `dbt_project.yml`.
Có bốn cách bắt đầu: tạo dự án mới, nối repository GitHub, tải lên file ZIP,
hoặc nhập các file SQL bạn đang có.

Trình tự làm việc: **lưu → parse → preview → build → xuất bản**. Build bản nháp
ghi vào schema phát triển. Chỉ bản phát hành đã xác minh (`ACTIVE`) mới chạy thật
và mới đặt lịch được.

📖 Chi tiết: hướng dẫn mục **7.3** (màn hình làm việc, các lệnh dbt, phát hành,
xử lý lỗi).

## Connector Builder

Khai báo Base URL, cách xác thực và từng stream (path, phân trang, đồng bộ tăng
dần), rồi **chạy thử**, kiểm tra các tab Bản ghi / Schema / Request / Log, và
**phát hành**. Connector vừa phát hành xuất hiện trong danh mục Nguồn của
workspace.

📖 Chi tiết: hướng dẫn mục **7.4** (kèm checklist trước khi phát hành).

## Người dùng và phân quyền

Tổ chức quản lý người dùng; workspace quản lý tài nguyên. Có sáu vai trò dựng
sẵn: Owner, Data Admin, Connector Dev, Operator, Analyst, Auditor. Vai trò chỉ là
điểm bắt đầu, quản trị viên chỉnh tiếp quyền theo từng khu vực. Ba quyền chạm
vào dữ liệu thật được tách riêng: `reset`, `manage_credentials`, `view_data`.
Quản trị tổ chức nằm ở `/admin`.

📖 Chi tiết: hướng dẫn mục **7.5**.

---

## Lệnh thường dùng

| Việc | Linux / Git Bash | Windows PowerShell |
|---|---|---|
| Dựng lại và khởi động | `./run.sh` | `.\run.ps1` |
| Lấy code mới rồi chạy | `git pull --ff-only` rồi `./run.sh` | `git pull --ff-only` rồi `.\run.ps1` |
| Tạo lại container, giữ dữ liệu | `./run.sh --fresh` | `.\run.ps1 -Fresh` |
| Xem trạng thái | `./run.sh --status` | `docker compose ps -a` |
| Xem log | `./run.sh --logs api` | `docker compose logs -f api` |
| Dừng, không mất gì | `./run.sh --stop` | `docker compose stop` |

> ⚠️ `--clean` / `-Clean` **xoá toàn bộ cơ sở dữ liệu**. Đừng dùng nó để sửa lỗi
> khởi động.
>
> Luôn chạy lại script sau `git pull`. `docker compose restart` không build code
> mới.

📖 Chi tiết: hướng dẫn mục **8–9**.

## Khi gặp trục trặc

| Triệu chứng | Xem mục |
|---|---|
| Docker không chạy, cổng bị chiếm, build lỗi | 11.1–11.3 |
| Migration thất bại, trang 502, bị đẩy về trang đăng nhập | 11.4–11.6 |
| Nguồn không kết nối được, lần chạy chờ quá lâu | 11.7–11.8 |
| Connector thoát mã 137 (hết RAM) | 11.9: giảm số lần chạy song song trước |
| Lỗi giải mã credential | 11.10: **không** sinh khoá mới, khôi phục khoá cũ |

---

## Sao lưu và bảo mật

- **`SECRET_ENCRYPTION_KEY` trong `.env` phải đi cùng bản sao cơ sở dữ liệu.**
  Mất khoá này là mất mọi thông tin đăng nhập đã lưu, không giải mã lại được.
  Script tự sao lưu `.env` vào `.env.backups/` mỗi lần chạy.
- Thông tin đăng nhập được mã hoá và không bao giờ hiển thị lại sau khi lưu.
- **Không bao giờ commit** `.env`, token, khoá service-account hay mật khẩu.
- Trước khi dùng cho người dùng thật: đổi mật khẩu quản trị, đặt
  `APP_ENV=production`, tắt dữ liệu demo, xem `.env.production.example` và thư
  mục `deploy/`.

📖 Chi tiết: hướng dẫn mục **10** (sao lưu, phục hồi) và **12** (triển khai thật).

---

<sub>Một sản phẩm của đội Data, Base.vn.</sub>
