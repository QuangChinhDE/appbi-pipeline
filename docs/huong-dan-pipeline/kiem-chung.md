# Biên bản kiểm chứng ngày 08 tháng 10 năm 2026

Biên bản này ghi lại những phần đã chạy thực tế khi biên soạn hướng dẫn. Số liệu dùng để chứng minh quy trình hoạt động trên máy kiểm tra tại thời điểm ghi nhận; không phải chỉ tiêu hiệu năng hoặc cam kết cho môi trường khác.

| Hạng mục | Kết quả kiểm chứng |
|---|---|
| Source code | Nhánh `master`, commit `3937ef9`; sau `git fetch`, HEAD trùng `origin/master` |
| Docker | Engine hoạt động, 12 CPU logic, khoảng 15,5 GiB RAM cấp cho Docker |
| Build backend | Thành công từ `backend/Dockerfile`, có runtime Transform |
| Build frontend | Thành công; Next.js compile và kiểm tra TypeScript hoàn tất |
| Migration | Service `migrate` thoát mã 0; Alembic ở head `c3a58f1d97e4` |
| Readiness sâu | `database.ok=true`, `engine.ok=true`, `transform_storage.ok=true` |
| Giao diện | Phục vụ tại `http://localhost:8080`, đăng nhập thành công bằng tài khoản local trong `.env` |
| Bài thực hành | Khám phá được 4 stream trong schema `shop`: `customers`, `order_items`, `orders`, `products` |
| Lần đồng bộ đầu | Thành công, 500 bản ghi, 138.676 byte, 23,8 giây |
| Lần đồng bộ sau khi đặt namespace | Thành công, 500 bản ghi, 138.676 byte, 12,7 giây |
| Đối chiếu đích | `SELECT count(*) FROM huong_dan.customers` trả 500 |
| dbt trong container | `dbt-core 1.12.3`, adapter Postgres `1.11.0`, adapter BigQuery `1.12.0` |
| Dự án Transform | Tạo `Huong dan Transform`, project `huong_dan_transform`, source schema `huong_dan`, development `analytics_dev`, production `analytics` |
| Parse Transform | Thành công; revision 1 gồm 14 file, trạng thái parse `OK` |
| Build Transform bản nháp | `SUCCEEDED` trong khoảng 9 giây; 2 model thành công, 4 test đạt, 0 node lỗi; relation tạo trong `analytics_dev` |
| Xác minh Release Transform | Release 1 được đóng băng đúng revision nhưng verification build thất bại với lỗi suy luận phụ thuộc `ref`; release ở `FAILED` và không được kích hoạt. Tình huống này được dùng trong hướng dẫn đọc Problems/Logs và phân biệt Draft với Release |

<!-- DOCX_PAGE_BREAK -->

| Hạng mục | Kết quả kiểm chứng |
|---|---|
| Dự án Builder | Tạo `Huong dan JSON API`, Base URL `https://jsonplaceholder.typicode.com`, stream `posts`, path `/posts`, primary key `id` |
| Test Builder | Thành công; cửa sổ test đọc 25 bản ghi, nhận diện 4 trường, ghi nhận 1 request và 14 dòng log |
| Phát hành Builder | Thành công; connector tùy chỉnh xuất hiện trong danh mục tạo Nguồn của workspace |
| Script Windows | Phát hiện và sửa xung đột biến `$Status` trong `run.ps1`; chạy lại `-NoBuild` kết thúc thành công |
| Máy mới chưa có `.env` | Chạy `run.sh --status` trong thư mục cô lập: script tạo `.env`, điền `admin@appbi.local` / `AppBI@123456`, sinh hai khóa hệ thống dài 44 ký tự và hai khóa khác nhau |
| Đổi tài khoản trong `.env` | Đổi tạm thành `owner@example.test` / `OwnerPass@123`, chạy `bash ./run.sh --no-build`; đăng nhập API trả HTTP 200. Khôi phục `.env`, chạy lại và tài khoản ban đầu đăng nhập HTTP 200 |

Trong lần chạy đầu, Windows từ chối publish cổng PostgreSQL `55433` vì cổng nằm trong dải hệ điều hành đã dành riêng `55342–55441`. Cấu hình local được chuyển sang `15433`, không thay đổi cổng `5432` bên trong mạng Docker. File `.env` trước khi đổi được lưu tại `.env.backups/env-before-manual-port-fix.bak`.

Một số Pipeline có từ trước trong workspace khác tự chạy theo lịch và có lỗi credential riêng. Lỗi kết nối cũ này không làm readiness của platform thất bại và không liên quan bài thực hành trong workspace `Huong dan Pipeline`. Hai lần chạy của `Huong dan Customers` đều kết thúc `SUCCEEDED`.

Ảnh trong tài liệu được chụp trực tiếp từ ứng dụng local sau khi build. Bộ ảnh bổ sung ghi lại wizard Transform, workbench và kết quả build, cùng luồng Builder từ tạo bản nháp, chạy thử, phát hành đến xuất hiện trong danh mục Nguồn. Không ảnh nào hiển thị mật khẩu hoặc khóa mã hóa.
