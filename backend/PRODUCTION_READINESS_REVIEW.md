
---

# Dev — PILOT-G1 đóng: Airbyte 1.8.5 / chart V2 2.0.17, auth bật, external DB (2026-08-24)

```text
kubernetes : v1.31.0
cni        : calico v3.28.1, disableDefaultCNI -- NetworkPolicy được enforce
chart      : airbyte-2.0.17  (app 1.8.5)  deployed
auth       : ENABLED -- Config API trả 401 khi không có credential
database   : jdbc:postgresql://172.25.0.3:5432/airbyte  (external, ngoài cluster, 66 bảng)
appbi db   : một instance Postgres external THỨ HAI (ADR-001)
in-cluster postgres: không có -- postgresql.enabled=false

airbyte-server                 1/1 Running
airbyte-worker                 1/1 Running
airbyte-workload-launcher      1/1 Running   <-- blocker của vòng trước
airbyte-workload-api-server    1/1 Running
airbyte-temporal               1/1 Running
airbyte-cron                   1/1 Running
airbyte-connector-builder-server 1/1 Running
airbyte-minio                  1/1 Running
```

## Nguyên nhân thật của workload-launcher, và nó là lỗi của tôi

Vòng trước tôi kết luận đây là blocker upstream. **Sai.** Nguyên nhân là chính
cái workaround của tôi.

Chuỗi sự việc:

1. Bootloader chết ở `CreateContainerConfigError` vì thiếu key
   `instance-admin-password` trong secret của chart.
2. Tôi cài bằng `--no-hooks` để vượt qua nó. Bootloader **là** migration, nên
   schema không bao giờ được tạo đúng.
3. `airbyte-auth-secrets` không tồn tại, launcher không start. Tôi **tự tay tạo**
   secret đó với credential tôi bịa ra.
4. Từ đó trở đi launcher luôn 401 ở `DataplaneApi.initializeDataplane`, vì
   credential tôi ghi vào không phải cái server biết.

Sự thật: **server tự tạo `airbyte-auth-secrets`** với đủ sáu key
(`dataplane-client-id`, `dataplane-client-secret`, `instance-admin-client-id`,
`instance-admin-client-secret`, `instance-admin-password`,
`jwt-signature-secret`). Nó có sẵn RBAC để làm việc đó. Tôi đã liên tục ghi đè
lên thứ nó tạo ra, rồi kết luận là upstream không hỗ trợ.

Thứ mở khoá mọi thứ là một values path **không có trong tài liệu values của
chart**: `global.auth.instanceAdmin.password`. Tôi tìm ra nó bằng cách render
template với `--set` từng đường và xem key nào trong secret nhận giá trị:

```text
global.auth.instanceAdmin.password          -> ['AB_INSTANCE_ADMIN_PASSWORD', 'INITIAL_USER_PASSWORD']
global.auth.instanceAdmin.passwordSecretKey -> no effect
global.secrets.AB_INSTANCE_ADMIN_PASSWORD   -> no effect
```

Có nó thì bootloader chạy **với hooks**, migration vào đúng external DB, server
lên, server tạo secret, launcher đăng ký dataplane, và toàn bộ control plane
xanh.

## Sáu cái bẫy của chart V2, đã ghi vào values-certification-v2.yaml

Không cái nào nằm trong documented values. Ghi lại để lần sau không ai mất lại
một buổi:

1. `global.auth.instanceAdmin.password` mới là thứ đặt giá trị.
   `passwordSecretKey` chỉ **đặt tên** một key và không đặt giá trị vào đó;
   `global.secrets` không thêm key nào cả.
2. **Không** dùng `--no-hooks` để né bootloader. Bootloader chính là migration.
3. `postgresql.enabled: false`, nếu không database in-cluster sẽ lặng lẽ thắng
   external.
4. Field là `name:`, không phải `database:`.
5. Temporal mặc định đòi TLS với external Postgres:
   `pq: SSL is not enabled on the server`.
6. **Không** tự tay viết `airbyte-auth-secrets`. Server tạo nó.

## Chỗ tôi sai và nên nói rõ

Báo cáo trước nói "blocked upstream, không phải code". Điều đó không đúng, và
tôi đã kết luận quá sớm dựa trên một chuỗi triệu chứng do chính workaround của
mình gây ra. Bài học đúng ở đây không phải "chart khó" mà là: khi một workaround
tạo ra triệu chứng mới, nghi ngờ workaround trước khi nghi ngờ upstream.

## Năm gate

| Gate | |
|---|---|
| **G1** target topology | **ĐÓNG** — chart V2 2.0.17 / app 1.8.5 / auth enforced / external Postgres riêng cho mỗi hệ thống / Calico enforcing / launcher Running |
| G2 | evidence v2 + binding + registry nội bộ có digest. Còn: clean Linux runner với upstream bị chặn |
| G3 | mở khoá được rồi (cần topology chạy được sync) — chưa chạy |
| G4 | timeout/cancel + `status` exit code xong. Golden path + restart + alert trên topology này: chưa chạy |
| G5 | legal |
