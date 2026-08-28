# AppBI Data Integration

**Đưa dữ liệu từ mọi hệ thống bạn đang dùng về một kho, tự động, theo lịch.**

Phát triển bởi **đội Data của Base.vn**.

Bạn kết nối một **Nguồn**, chọn một **Đích**, chọn những bảng cần lấy, đặt lịch —
và dữ liệu tự về. Không viết script, không dựng cron, không ai phải nhớ chạy tay
mỗi sáng.

---

## Sản phẩm này giải quyết việc gì

Dữ liệu kinh doanh nằm rải rác: nhân sự ở một nơi, đơn hàng ở nơi khác, chi tiêu
quảng cáo ở nơi thứ ba. Muốn có một báo cáo nhìn được toàn cảnh thì phải gom
chúng lại — và việc gom đó thường là một mớ script chạy nhờ máy của ai đó.

AppBI Data Integration làm phần gom ấy thành một sản phẩm có giao diện:

- **Kết nối một lần.** Điền thông tin đăng nhập, bấm Kiểm tra, lưu. Thông tin
  đăng nhập được mã hoá và không bao giờ hiện lại.
- **Chọn đúng thứ cần.** Xem trước các bảng và cột nguồn có, tick những gì muốn
  lấy. Đổi ý lúc nào cũng được.
- **Chạy theo lịch.** Mỗi giờ, mỗi ngày, hay bấm chạy ngay. Lần sau chỉ lấy phần
  thay đổi chứ không tải lại từ đầu.
- **Biết chuyện gì đang xảy ra.** Mỗi lần chạy có lịch sử, số dòng, thời gian và
  lý do khi hỏng — bằng tiếng Việt, kèm việc cần làm tiếp.

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

## Bắt đầu

Cần Docker và khoảng 20 GB đĩa trống.

```bash
python scripts/production.py install --config deploy/demo.yaml
```

Một lệnh. Nó tự sinh khoá mã hoá, dựng và khởi động toàn bộ, đợi tới khi thật sự
phục vụ được, rồi in ra địa chỉ.

Mở **http://localhost:8080**, đăng nhập bằng `admin@appbi.local`.

Chạy lại lệnh đó lần nữa không hỏng gì: khoá cũ được giữ nguyên, vì sinh khoá mới
sẽ làm cả kho thông tin đăng nhập không giải mã được nữa.

### Thử ngay bằng dữ liệu mẫu

Cài đặt kèm sẵn hai cơ sở dữ liệu để chạy thử mà không cần đụng vào hệ thống
thật:

- **`demo_source`** — 500 khách hàng, 2.000 đơn hàng, 200 sản phẩm
- **`demo_warehouse`** — nơi dữ liệu chảy về

Ba bước trong giao diện:

1. **Nguồn dữ liệu → Thêm → PostgreSQL** — máy chủ `postgres`, cơ sở dữ liệu
   `demo_source`, schema `shop`, tài khoản `demo_reader` / `demo_reader_pw`
2. **Đích dữ liệu → Thêm → PostgreSQL** — cơ sở dữ liệu `demo_warehouse`, schema
   `analytics`, tài khoản `demo_writer` / `demo_writer_pw`
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

Triển khai lên Kubernetes: xem `deploy/kubernetes/` — manifest Kustomize đầy đủ
gồm API, worker, job migration, giao diện, NetworkPolicy và ingress.

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

---

## Cấu trúc mã nguồn

```
backend/     API, bộ điều phối, và các connector do đội Data Base.vn viết
frontend/    Giao diện Next.js
scripts/     Cài đặt, sao lưu, khôi phục, xoay khoá, vận hành
deploy/      Kubernetes, giám sát, cấu hình môi trường
docker/      Cấu hình nginx và khởi tạo cơ sở dữ liệu
```

Bộ kiểm thử, tài liệu nội bộ và hồ sơ kiểm chứng phát hành nằm trên máy của đội
phát triển, không nằm trong kho mã — xem `.gitignore`. Không có thứ nào trong đó
cần thiết để dựng hay chạy sản phẩm.

---

<sub>Một sản phẩm của đội Data, Base.vn.</sub>
