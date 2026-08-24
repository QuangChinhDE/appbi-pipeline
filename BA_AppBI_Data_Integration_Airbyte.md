---
title: "BA / SRS - AppBI Data Integration Platform"
subtitle: "Custom Frontend theo style AppBI, Airbyte làm Data Integration Engine"
author: "Product / BA Baseline"
date: "22/08/2026"
lang: vi-VN
---

# Mục lục nhanh

| Phần | Nội dung chính | Sections |
|---|---|---|
| A | Tầm nhìn, scope, kiến trúc, persona, UX foundation | 0-9 |
| B | Core product modules: Workspace, Connector, Source, Destination, Pipeline, Schema, Run | 10-17 |
| C | Monitoring, Alert, Audit, Secrets, Data Model, API và Airbyte Adapter | 18-27 |
| D | Scale, upgrade, security, licensing và error UX | 28-32 |
| E | Screen specification, FE/BE boundaries, migration vào AppBI, NFR, testing, UAT | 33-44 |
| F | Backlog, sprint plan, ADR, risk, production checklist và API examples | 45-52 |
| G | Connector certification, runbooks, retention, privacy, component/page patterns | 53-61 |
| H | Multi-tenancy, engine abstraction, scheduler, UX, locking, contracts, supportability | 62-84 |
| I | Final architecture, kết luận và các phụ lục go-live | 85-86 + Phụ lục |

**Cách đọc cho Dev Lead:** đọc 0-9 để hiểu ranh giới hệ thống; 10-32 để implement domain; 33-44 để hoàn thiện UX/quality; 45 trở đi dùng làm backlog, release gate và runbook.

# 0. Thông tin tài liệu

**Tên sản phẩm làm việc:** AppBI Data Integration Platform (có thể đổi brand sau).  
**Loại tài liệu:** BA + SRS + Product Functional Specification.  
**Phiên bản:** 1.0 - Baseline để triển khai V1 production-ready.  
**Ngày:** 22/08/2026.  
**Đối tượng đọc:** Product Owner, BA, UI/UX, Frontend, Backend, DevOps/SRE, QA, Security, Tech Lead.  
**Mục tiêu:** Sau khi đọc tài liệu này, đội dev phải xác định được sản phẩm cần làm gì, màn hình nào cần có, dữ liệu nào cần lưu, luồng nào phải chạy, trạng thái nào phải xử lý, boundary nào không được vi phạm và tiêu chí nào để được coi là hoàn thành.

> **Quyết định kiến trúc quan trọng nhất:** Airbyte được sử dụng như một **engine chạy phía sau**, không phải là backend nghiệp vụ mà frontend gọi trực tiếp. Frontend chỉ gọi Product Backend/BFF. Product Backend giao tiếp với Airbyte thông qua một lớp `AirbyteAdapter` có contract ổn định.

> **Quyết định thiết kế quan trọng nhất:** FE kế thừa design language hiện tại của `QuangChinhDE/appbi-ai`: Next.js App Router + TypeScript + Tailwind, bố cục sidebar workspace, Linear-inspired visual system, semantic color token (`surface-*`, `text-*`, `brand`, border, shadow), component nhỏ gọn và information density vừa phải.

## 0.1. Nguồn tham chiếu đã kiểm tra

- `https://github.com/airbytehq/airbyte` - Airbyte data movement platform, connector ecosystem và API-based orchestration.
- `https://github.com/airbytehq/airbyte-platform` - platform internals, server/API/workload architecture.
- `https://github.com/QuangChinhDE/appbi-ai` - nguồn tham khảo trực tiếp cho FE/UX và cấu trúc product hiện tại.
- `appbi-ai/frontend/package.json` - Next.js 14, React 18, TypeScript, Tailwind, TanStack Query, Lucide, Sonner, Radix.
- `appbi-ai/frontend/tailwind.config.js` - Linear-inspired semantic token, typography, radius, shadow.
- `appbi-ai/frontend/src/components/layout/Sidebar.tsx` - sidebar collapsible 56px/240px, grouped navigation theo user intent, permission gating.
- `appbi-ai/frontend/src/app/(main)/datasources/page.tsx` - pattern list page, filter, module overview, bulk action, toast, test connection.

## 0.2. Phạm vi tài liệu

Tài liệu này mô tả một sản phẩm Data Integration có thể dùng thực tế, bao gồm: authentication, workspace/multi-tenancy, source, destination, pipeline, schema discovery, stream selection, sync mode, scheduling, execution, retry/cancel, logs, monitoring, alerting, connector lifecycle, RBAC, audit, secrets, admin, API boundary, data model, scale, deployment, Airbyte upgrade strategy, QA/UAT và backlog thực thi.

Không coi đây là tài liệu “concept”. Các phần đánh dấu **MUST** là yêu cầu bắt buộc để V1 được nghiệm thu.

# 1. Tầm nhìn sản phẩm

## 1.1. Problem statement

Airbyte có năng lực data movement mạnh và hệ sinh thái connector lớn, nhưng nếu dùng nguyên UI/platform của Airbyte thì sản phẩm bị phụ thuộc vào UX, object model, versioning và cách triển khai của Airbyte. Mục tiêu của hệ thống là giữ năng lực data integration của Airbyte nhưng cung cấp trải nghiệm riêng, đơn giản hơn cho người dùng non-tech và đồng nhất với AppBI.

Sản phẩm phải trả lời được bốn câu hỏi của người dùng mà không yêu cầu họ hiểu internals của Airbyte:

1. Tôi đang lấy dữ liệu **từ đâu**?
2. Tôi đang đẩy dữ liệu **đến đâu**?
3. Pipeline nào đang chạy, chạy **khi nào**, lấy **những bảng/stream nào**?
4. Nếu có lỗi, tôi cần **làm gì tiếp theo**?

## 1.2. Product vision

Xây một “Integration Hub” mà người dùng có thể kết nối nguồn dữ liệu, chọn đích, tạo pipeline và vận hành hàng ngày qua giao diện thống nhất với AppBI. Airbyte chỉ là execution engine nằm phía sau và có thể được nâng cấp độc lập.

## 1.3. Mục tiêu kinh doanh

| ID | Mục tiêu | Kết quả kỳ vọng |
|---|---|---|
| BG-01 | Giảm thời gian tích hợp data source | User có thể tạo pipeline phổ biến trong <= 10 phút nếu credential đúng |
| BG-02 | Giảm phụ thuộc Airbyte UI | 100% user-facing flow đi qua FE của sản phẩm |
| BG-03 | Dễ nâng Airbyte | Nâng version engine chủ yếu sửa adapter/infra, không sửa toàn bộ FE |
| BG-04 | Dùng được cho non-tech | Thông báo lỗi có next action; không expose khái niệm nội bộ không cần thiết |
| BG-05 | Sẵn sàng scale | Control plane và data plane scale độc lập; có quota/concurrency control |
| BG-06 | Sẵn sàng thương mại | Có multi-tenancy, RBAC, audit, secret management, observability và legal/license gate |

## 1.4. Chỉ số sản phẩm đề xuất

| Metric | Target V1 |
|---|---:|
| Tỷ lệ create source thành công sau khi user bấm Test | >= 90% với credential hợp lệ |
| Tỷ lệ pipeline first sync thành công trong onboarding | >= 85% với connector healthy |
| Median time từ chọn connector đến pipeline created | <= 10 phút |
| API p95 của Product API cho request không trigger data job | <= 700 ms |
| Availability Product Control Plane | >= 99.9%/tháng |
| Tỷ lệ lỗi có human-readable remediation | >= 95% error class đã biết |
| Airbyte upgrade regression coverage | 100% core adapter contract tests |

# 2. Nguyên tắc kiến trúc và product guardrails

## 2.1. Guardrails bắt buộc

1. **FE MUST NOT gọi Airbyte trực tiếp.** Mọi request từ FE đi qua Product API/BFF.
2. **Product Backend MUST NOT SELECT/UPDATE Airbyte metadata DB.** Airbyte DB thuộc Airbyte.
3. **Không expose Airbyte ID ra URL/public API của sản phẩm.** Product dùng UUID riêng và mapping nội bộ.
4. **Credential plaintext không được lưu trong browser localStorage, log, audit log hoặc Product DB.** Chỉ giữ secret reference hoặc encrypted payload theo cơ chế chuẩn.
5. **Adapter phải là boundary duy nhất hiểu Airbyte-specific request/response.** Service nghiệp vụ gọi interface nội bộ, không gọi raw Airbyte SDK rải rác.
6. **Pin version.** Không deploy `latest`; phải biết chính xác Product version tương thích Airbyte engine version nào.
7. **Upgrade Airbyte phải qua staging + contract test + migration check + smoke sync.** Không auto-upgrade production.
8. **Mọi object phải có `workspace_id` và mọi query phải scope workspace.** Không dựa vào FE để đảm bảo tenant isolation.
9. **Mọi action thay đổi state quan trọng phải audit.** Tối thiểu create/update/delete/test/enable/disable/run/cancel/retry/credential rotate/permission change.
10. **Connector capability-driven UI.** FE chỉ hiển thị field/sync mode/CDC/cursor/PK mà connector hoặc catalog báo hỗ trợ.

## 2.2. Kiến trúc mục tiêu

![Kiến trúc mục tiêu](/mnt/data/ba_airbyte_assets/architecture.png)

### Component ownership

| Component | Owner | Trách nhiệm |
|---|---|---|
| Custom FE | Product team | UX, form, list, monitoring, settings, client validation |
| Product API/BFF | Product team | Auth, RBAC, tenant, business rule, orchestration, audit, normalized error |
| Product DB | Product team | Product object, mappings, policies, alert config, audit, usage |
| Secret Store | Product/infra | Credential, token, OAuth secret, encryption lifecycle |
| AirbyteAdapter | Product team | Mapping Product contract -> Airbyte API; compatibility per version |
| Airbyte Engine | Airbyte/upstream + infra | connector execution, discovery, data movement, job/workload orchestration |
| Airbyte Metadata DB | Airbyte | internal Airbyte state; product không truy cập trực tiếp |

# 3. Phạm vi V1, V1.1 và ngoài phạm vi

## 3.1. In scope - V1 bắt buộc

| Nhóm | Feature V1 |
|---|---|
| Identity | Login, logout, workspace switch, current user, session keep-alive |
| Workspace | Workspace, member, role, permission |
| Connector Catalog | Source/Destination connector catalog, search/filter, capability metadata |
| Source | Create/edit/test/disable/delete source, credential update, connection health |
| Destination | Create/edit/test/disable/delete destination |
| Pipeline | Create/update/enable/disable/delete, source-destination link, stream selection |
| Schema | Discover/re-discover, schema diff, field/stream config, cursor/PK where supported |
| Sync | Manual run, schedule, cancel, retry, run status, attempt detail |
| Monitoring | Overview health, runs list, failure detail, basic metrics |
| Alert | In-app notification; email/webhook adapter-ready; alert rules tối thiểu cho failed run |
| Security | Secret handling, RBAC, audit log, tenant isolation |
| Operations | Engine health, connector version pin, upgrade visibility |
| UX | Onboarding wizard, empty state, loading/error/skeleton, human-readable remediation |
| QA | Unit/integration/contract/e2e/UAT baseline |

## 3.2. V1.1 / ưu tiên ngay sau V1

- Pipeline template.
- Bulk enable/disable pipelines.
- Schema change approval policy.
- Webhook/Slack alert channel.
- Usage dashboard theo workspace/pipeline.
- Connector auto-update policy theo allowlist.
- Custom Connector management page.
- OAuth connector UX hoàn chỉnh cho connector phổ biến.
- SSO/OIDC nếu triển khai enterprise.

## 3.3. Out of scope V1

- Xây lại connector engine.
- Thay thế Airbyte scheduler/workload runtime.
- Data transformation engine kiểu dbt đầy đủ.
- Reverse ETL.
- Data catalog/governance sâu như lineage doanh nghiệp; chỉ lưu lineage cơ bản Source -> Pipeline -> Destination.
- Billing engine hoàn chỉnh.
- Cho customer truy cập raw Airbyte UI/API.
- Sửa sâu Airbyte source code nếu public API/capability hiện tại đã đáp ứng.

# 4. Đối tượng sử dụng và RBAC

## 4.1. Persona

| Persona | Nhu cầu | Hành vi chính |
|---|---|---|
| Workspace Owner | Quản lý workspace, member, chính sách, connector | Full access |
| Data Admin | Cấu hình source/destination/pipeline và xử lý lỗi | Build + operate |
| Operator | Theo dõi run, retry/cancel, nhận alert | Operate |
| Analyst | Xem pipeline, trạng thái và dữ liệu sync metadata | Read-only |
| Auditor/Security | Xem audit log, quyền, credential events | Read audit/security |
| Platform Admin | Vận hành engine/connector version toàn hệ thống | System-level admin, không phải tenant user thường |

## 4.2. Permission model đề xuất

Permission theo module + action để tương thích cách AppBI hiện có.

| Module | view | create | edit | operate | delete | full/admin |
|---|---:|---:|---:|---:|---:|---:|
| sources | ✓ | ✓ | ✓ | test | ✓ | share/secret policy |
| destinations | ✓ | ✓ | ✓ | test | ✓ | policy |
| pipelines | ✓ | ✓ | ✓ | run/cancel/retry | ✓ | advanced settings |
| monitoring | ✓ | - | - | ack incident | - | manage rules |
| alerts | ✓ | ✓ | ✓ | acknowledge | ✓ | channel config |
| audit | ✓ | - | - | - | - | export |
| members | ✓ | invite | edit role | - | remove | owner actions |
| settings | ✓ | - | edit | - | - | engine/admin settings |

### Rule

- Backend là nguồn quyết định cuối cùng của permission.
- FE permission gating chỉ để UX sạch hơn, không phải security boundary.
- Object-level permission V1 có thể inherit workspace; nếu sau này cần per-resource sharing thì bổ sung ACL nhưng không phá API contract.

# 5. Thuật ngữ chuẩn dùng trong UI

Không đưa toàn bộ thuật ngữ Airbyte lên UI. Tên hiển thị phải hướng user.

| Product term | Ý nghĩa | Airbyte equivalent / mapping |
|---|---|---|
| Source | Nơi lấy dữ liệu | Source |
| Destination | Nơi nhận dữ liệu | Destination |
| Pipeline | Cấu hình đồng bộ Source -> Destination | Connection |
| Data stream | Bảng/endpoint/stream chọn để sync | Stream |
| Run | Một lần thực thi pipeline | Job / Sync job |
| Attempt | Một lần thử bên trong Run | Attempt |
| Discover schema | Đọc danh sách stream/field từ Source | Discover catalog |
| Full refresh | Đồng bộ lại toàn bộ | Full refresh sync mode |
| Incremental | Chỉ lấy phần mới/thay đổi | Incremental sync mode |
| Cursor | Field xác định mốc incremental | Cursor field |
| Primary key | Field định danh record | Primary key |
| Engine | Airbyte runtime | Airbyte platform |

**UI không hiển thị mặc định:** workspaceId của Airbyte, sourceDefinitionId, destinationDefinitionId, actor ID, internal job payload, Temporal identifiers.

# 6. Information Architecture và Navigation

## 6.1. Sidebar đề xuất

Kế thừa pattern `Sidebar.tsx` của AppBI: collapsed `w-14`, expanded `w-60`, semantic groups theo intent.

**Top / Home**
- Overview

**BUILD**
- Sources
- Destinations
- Pipelines

**OPERATE**
- Runs
- Monitoring
- Alerts

**MANAGE**
- Connectors
- Audit Log

**Settings** nằm trong user/settings menu giống AppBI hiện tại, gồm Workspace, Members & Roles, Notifications, Engine & Compatibility (chỉ admin).

## 6.2. URL contract của FE

| Screen | Route |
|---|---|
| Overview | `/overview` |
| Sources | `/sources` |
| Create Source | `/sources/new` |
| Source detail | `/sources/[id]` |
| Destinations | `/destinations` |
| Create Destination | `/destinations/new` |
| Destination detail | `/destinations/[id]` |
| Pipelines | `/pipelines` |
| Create Pipeline | `/pipelines/new` |
| Pipeline detail | `/pipelines/[id]` |
| Run list | `/runs` |
| Run detail | `/runs/[id]` |
| Monitoring | `/monitoring` |
| Alerts | `/alerts` |
| Connector catalog | `/connectors` |
| Audit | `/audit` |
| Members/permission | `/settings/access` |
| Workspace settings | `/settings/workspace` |
| Engine settings | `/settings/engine` |

# 7. FE Design System Specification

## 7.1. Technology baseline

Khuyến nghị reuse đúng stack AppBI hiện tại:

- Next.js App Router.
- React + TypeScript.
- TailwindCSS semantic tokens.
- TanStack Query cho server state.
- Lucide icon.
- Sonner/toast abstraction cho notification.
- `clsx` + `tailwind-merge`/`cn` helper.
- Radix primitives khi cần popover/dialog/accessibility.

Không tạo một frontend stack thứ hai nếu module này nằm cùng AppBI.

## 7.2. Visual language

Reuse semantic token đã có trong `tailwind.config.js`:

- Surface: `surface-0`, `surface-1`, `surface-2`, `surface-3`.
- Text: `text-primary`, `text-secondary`, `text-tertiary`, `text-quaternary`.
- Brand: `brand`, `brand-hover`, `brand-active`, `brand-soft`.
- Status: success, warning, danger, info.
- Radius chủ đạo 6-8px; modal/card lớn tối đa 12px.
- Shadow nhỏ, không dùng card floating quá nhiều.
- Typography compact, ưu tiên caption/small/body để information density tốt.

## 7.3. Page patterns bắt buộc

### List Page

Reuse pattern tương đương `PageListLayout` + `ModuleOverview`:

1. Header: title, description, primary CTA.
2. Optional overview strip: health/statistics.
3. Search + filter + sort.
4. Table/list.
5. Bulk action khi chọn record.
6. Empty state có CTA.
7. Pagination hoặc infinite query theo quy mô.

### Detail Page

- Breadcrumb/back.
- Title + connector icon + health badge.
- Action group bên phải: Test / Run / Edit / More.
- Tabs cố định: Overview, Configuration/Schema, Runs/Activity, Settings tùy resource.
- Không mở modal khổng lồ cho cấu hình phức tạp; dùng page hoặc right drawer có section rõ.

### Wizard

Wizard tạo Source/Destination/Pipeline phải có stepper ở trên và sticky footer có Back / Continue / Save.

### Error UX

Mỗi lỗi production phải cố gắng trả về:

- `What happened` - lỗi gì.
- `What it affects` - ảnh hưởng Source/Pipeline/Run nào.
- `What to do next` - CTA hoặc hướng xử lý.
- `Technical details` - collapse, dành cho admin/dev.
- `trace_id` - để support tra log.

## 7.4. Responsive

- Desktop >= 1280px là primary target.
- Tablet 768-1279px hỗ trợ list/detail cơ bản.
- Mobile không cần wizard cấu hình phức tạp ở V1; phải xem monitoring/run/alert được.
- Sidebar mobile chuyển drawer.

# 8. System Context và kiến trúc logic

## 8.1. Request path

```text
Browser
  -> Nginx / Ingress
  -> Product FE / Product API
  -> Domain Service
  -> AirbyteAdapter
  -> Airbyte Public/Self-managed API
  -> Airbyte workload/connector runtime
  -> Source / Destination systems
```

## 8.2. Event path

Vì sync job chạy lâu, không giữ HTTP request mở đến khi job hoàn thành.

```text
POST /pipelines/{id}/runs
  -> Product API tạo run record trạng thái QUEUED
  -> AirbyteAdapter.triggerSync()
  -> lưu engine_job_id mapping
  -> trả 202 + product_run_id

Background reconciler / webhook/poller
  -> đọc trạng thái engine
  -> normalize status
  -> update pipeline_run
  -> emit notification/audit/metric
```

## 8.3. Polling và event strategy

V1 cho phép poll Airbyte job status qua background worker nếu Airbyte deployment chưa có event callback phù hợp. Không để mỗi browser tự poll Airbyte.

- Browser poll Product API hoặc dùng SSE/WebSocket sau này.
- Product background worker reconcile các run active.
- Poll interval adaptive: 3-5s cho run mới/đang chạy, tăng dần nếu job dài.
- Có timeout và stale-run detector.

# 9. End-to-end User Journeys

## 9.1. Journey A - First successful pipeline

![Happy path](/mnt/data/ba_airbyte_assets/pipeline_flow.png)

**Precondition:** user có `sources.create`, `destinations.create`, `pipelines.create`.

1. User vào Sources > Add Source.
2. Chọn connector theo search/category.
3. Form được render theo connector spec/capability.
4. User nhập credential.
5. Bấm Test connection.
6. Backend validate + gọi adapter check connection.
7. Nếu pass, tạo Source product object và mapping engine.
8. User tiếp tục Add Destination hoặc chọn Destination có sẵn.
9. Test destination.
10. Vào Create Pipeline, chọn Source + Destination.
11. Trigger schema discovery.
12. User chọn stream/table.
13. Chọn sync mode cho từng stream hoặc default.
14. Chọn schedule/manual.
15. Review summary.
16. Create pipeline.
17. Option “Run first sync now” mặc định bật.
18. System tạo Run và chuyển sang run detail/progress.
19. Thành công: show records/bytes/duration nếu engine cung cấp.
20. Thất bại: show normalized failure + remediation.

**Acceptance:** user không phải nhìn Airbyte UI ở bất kỳ bước nào.

## 9.2. Journey B - Credential expired

1. Scheduled run fail với classification `AUTHENTICATION`.
2. Pipeline health chuyển `Action required`.
3. Alert được tạo một lần theo dedup window.
4. User click alert -> Source detail > Credentials.
5. Credential hiện dạng masked/reference, không show plaintext.
6. User Update credentials -> Test.
7. Nếu pass, Source health Healthy.
8. CTA `Retry failed run`.
9. Retry tạo Product Run mới có `retry_of_run_id`.

## 9.3. Journey C - Schema changed

1. System re-discover theo manual action hoặc detection policy.
2. Lưu schema snapshot mới.
3. Diff với snapshot đang active.
4. Nếu additive/non-breaking và policy auto-accept -> update pipeline.
5. Nếu breaking/removed/type changed -> pipeline `NEEDS_REVIEW`, không âm thầm đổi.
6. User thấy diff theo Added / Removed / Changed.
7. User approve hoặc adjust stream selection.
8. Audit người approve + before/after hash.

## 9.4. Journey D - Run stuck

1. Run vượt `stale_threshold` theo connector/job class.
2. Monitoring đánh dấu `Possible stuck` nhưng không tự đổi engine status.
3. User/operator có CTA `Refresh`, `Cancel`, `Open technical details`.
4. Cancel phải idempotent.
5. Nếu cancel không phản hồi, tạo incident nội bộ/platform alert.

# 10. Module Specification - Authentication & Workspace

## 10.1. Login

Nếu tích hợp thẳng AppBI, reuse auth/session hiện tại. Nếu standalone, Product API vẫn phải cung cấp tương đương.

**Fields:** email, password; optional OAuth/SSO phase sau.  
**Rules:** lock/rate limit brute force; secure cookie; session refresh; logout invalidates server-side session/token strategy.

## 10.2. Workspace switch

Mỗi user có thể thuộc nhiều workspace. Workspace selector nằm ở user menu/top-level selector.

**MUST:** khi switch workspace, clear workspace-scoped React Query cache để tránh hiển thị data tenant cũ.

## 10.3. Workspace object

| Field | Type | Rule |
|---|---|---|
| id | UUID | Product-owned |
| name | string | 2-100 chars |
| slug | string | unique global hoặc tenant namespace |
| status | enum | ACTIVE, SUSPENDED, DELETED |
| airbyte_workspace_ref | opaque/encrypted mapping | backend only |
| timezone | IANA timezone | dùng cho schedule display |
| created_at | timestamptz | server-generated |

# 11. Module Specification - Connector Catalog

## 11.1. Mục tiêu

Catalog là lớp metadata normalized để FE biết connector nào có thể tạo, cần field gì, support sync mode nào và version nào đang dùng. Không gọi Airbyte registry trực tiếp từ browser.

## 11.2. Screen `/connectors`

**Header:** Connectors, search, filter Source/Destination, category, status.  
**Cards/list item:** icon, display name, type, release stage, current pinned version, available update, capabilities, last catalog refresh.  
**Admin actions:** Refresh metadata, View version history, Pin/Upgrade, Disable for workspace/system.

## 11.3. Connector metadata normalized

| Field | Description |
|---|---|
| connector_key | stable product key, e.g. `source-postgres` |
| engine_definition_id | backend-only mapping |
| display_name | `PostgreSQL` |
| connector_type | SOURCE / DESTINATION |
| icon_url/cache | icon |
| release_stage | alpha/beta/general nếu có |
| version | pinned version |
| latest_version | registry-discovered version |
| supports_oauth | bool |
| supports_incremental | bool/capability derived |
| supports_cdc | bool |
| supports_namespaces | bool |
| spec_schema | normalized dynamic form schema |
| status | ACTIVE / DISABLED / DEPRECATED |

## 11.4. Catalog cache

- Refresh theo schedule (ví dụ 6h) hoặc admin manual.
- Không để connector registry outage làm list Sources hiện tại không mở được.
- Lưu snapshot/hash để biết spec thay đổi.
- Khi connector spec thay đổi breaking, existing source không tự render lại form theo spec mới mà không migration strategy.

# 12. Module Specification - Sources

## 12.1. Source list `/sources`

### Table columns

| Column | Nội dung |
|---|---|
| Name | Tên do user đặt + connector icon |
| Type | PostgreSQL, MySQL, Google Ads... |
| Health | Healthy / Warning / Error / Not tested |
| Used by | số pipeline đang dùng |
| Last checked | thời điểm test/check gần nhất |
| Owner | người tạo/chủ quản |
| Updated | updated_at |
| Actions | Test, Edit, Duplicate (V1.1), Delete |

### Filters

Search name; connector type; health; owner; used/unused; updated range.

### Empty state

“Chưa có nguồn dữ liệu. Kết nối database, SaaS hoặc file/API để bắt đầu.” CTA `Add source`.

## 12.2. Create Source `/sources/new`

### Step 1 - Choose connector

- Search.
- Suggested connectors dựa vào tổ chức (optional).
- Category: Database, Marketing, CRM, File/Storage, Custom.
- Connector bị disabled không selectable, show reason.

### Step 2 - Configure

Form dynamic từ normalized connector spec.

Field renderer phải hỗ trợ ít nhất:

- text
- password/secret
- number
- boolean
- select/enum
- nested object/section
- array/list
- OAuth button
- advanced collapsible section

### Step 3 - Test

Bấm `Test connection`; save chỉ khi test pass, trừ khi workspace policy cho phép `Save without test` dành cho admin.

### Business rules

- Name unique trong workspace ở mức case-insensitive hoặc cho phép trùng nhưng UI cảnh báo; khuyến nghị unique để vận hành dễ.
- Password fields không repopulate plaintext khi edit.
- Khi edit không thay credential, payload phải phân biệt “unchanged” với empty string.
- Test có timeout riêng; UI phải giữ trạng thái `Testing...` và disable double-submit.
- Test endpoint idempotent về side effect sản phẩm.

## 12.3. Source detail `/sources/[id]`

Tabs:

**Overview**
- health card
- connector/version
- number of pipelines
- last successful run related
- last test
- owner/created/updated

**Configuration**
- non-secret config
- credential status (`Configured`, `Expired`, `Unknown`)
- edit button

**Pipelines**
- list pipeline sử dụng source

**Activity**
- audit events liên quan

### Actions

- Test connection
- Edit
- Disable/Enable
- Delete

## 12.4. Delete Source rule

Không hard-delete nếu còn active pipeline.

Flow:

1. User Delete.
2. Backend kiểm tra constraints.
3. Nếu đang được dùng -> HTTP 409 normalized error có danh sách pipeline refs.
4. Modal hiển thị dependency và CTA mở pipelines.
5. Chỉ delete khi no dependency hoặc force action dành cho admin đã disable/delete pipelines trước.

# 13. Module Specification - Destinations

Destination dùng cùng pattern Source, khác capability/form và impact.

## 13.1. Destination list

Columns: Name, Type, Health, Pipelines, Last checked, Owner, Updated, Actions.

## 13.2. Destination detail

Tabs: Overview, Configuration, Pipelines, Activity.

## 13.3. Destination-specific rules

- Test phải kiểm tra khả năng write cần thiết nếu connector hỗ trợ.
- Namespace/schema/database config phải hiển thị rõ ảnh hưởng naming.
- Khi credential/target path thay đổi, warning: pipeline lần sau có thể ghi sang vị trí mới.
- Destination đang được nhiều pipeline sử dụng: edit destructive config cần confirm impact.

# 14. Module Specification - Pipelines

## 14.1. Pipeline list `/pipelines`

Đây là màn hình chính vận hành.

### Columns

| Column | Description |
|---|---|
| Pipeline | name + source icon -> destination icon |
| Status | Enabled / Paused / Action required |
| Sync health | Healthy / Running / Failed / Never run |
| Schedule | Manual / Every N / Cron friendly label |
| Last run | time + status |
| Next run | computed/displayed |
| Streams | count selected |
| Owner | owner |
| Actions | Run now, Pause/Resume, Edit, More |

### Quick filters

- Failed
- Running
- Paused
- Never run
- Needs review
- Source type
- Destination type

## 14.2. Create Pipeline Wizard

### Step 1 - Basics

Fields:
- Pipeline name
- Source
- Destination
- optional description

Validation:
- source/destination ACTIVE.
- user có view/use permission.
- không cho source = invalid selected resource.

### Step 2 - Discover & select data

System trigger discover source schema.

UI tree/table:
- namespace
- stream/table
- sync capability
- primary key candidate
- cursor candidate
- selected checkbox

Functions:
- Select all.
- Search stream.
- Filter selected.
- Expand stream fields.
- Show unsupported reason.

### Step 3 - Sync behavior

Theo stream/capability, user chọn:

- Full refresh - overwrite/append tùy destination/Airbyte support.
- Incremental - append/deduped nếu supported.
- Cursor field nếu cần.
- Primary key nếu dedupe cần.

**Rule:** không đưa option mà engine/capability không hỗ trợ.

### Step 4 - Schedule

V1 support:

- Manual only.
- Every N minutes/hours/days theo policy min interval.
- Daily at HH:mm.
- Advanced cron chỉ cho role admin/data admin hoặc có validation + friendly preview.

Fields:
- schedule type
- timezone (default workspace timezone)
- start/next run preview
- concurrency behavior khi run trước chưa xong

Concurrency policy V1 mặc định: `DO_NOT_START_NEW_IF_ACTIVE`, tránh overlapping sync cùng pipeline.

### Step 5 - Review

Hiển thị:
- Source
- Destination
- selected stream count
- mode summary
- schedule
- warnings
- checkbox `Run first sync now` default ON nếu user có `operate`.

Create chỉ commit sau khi validation toàn bộ pass.

## 14.3. Pipeline detail `/pipelines/[id]`

### Header

- Name
- Source -> Destination visual
- Enabled/Paused badge
- Health badge
- `Run now` primary CTA
- Pause/Resume
- More: Edit, Re-discover schema, Delete

### Tab Overview

Cards:
- Last run
- Next run
- Success rate 7d/30d
- Average duration
- Records/bytes nếu available
- Current stream count

Section recent runs: 5-10 runs.

### Tab Data / Schema

- selected streams
- sync mode
- cursor
- primary key
- fields
- schema snapshot date
- schema change warning

### Tab Runs

Paginated run history.

### Tab Settings

- name/description
- schedule
- enable state
- advanced config
- retry policy display
- schema refresh policy

### Tab Activity

Audit events của pipeline.

## 14.4. Enable/Pause

- `Paused` nghĩa là scheduled trigger không tạo run mới.
- Manual `Run now` trên paused pipeline: mặc định không cho hoặc yêu cầu explicit override; V1 chọn **không cho**, CTA “Resume to run”.
- Pause không tự cancel run đang chạy. UI phải nói rõ.

## 14.5. Delete Pipeline

- Nếu run active: không delete, yêu cầu cancel/finish trước.
- Soft delete Product object trước nếu cần audit retention; engine delete sau qua saga.
- Nếu engine delete fail, state `DELETE_PENDING` và background retry; không giả vờ delete thành công hoàn toàn.

# 15. Schema Discovery và Schema Change Management

## 15.1. Schema snapshot model

Mỗi successful discover tạo snapshot immutable:

- snapshot_id
- source_id
- discovered_at
- catalog_hash
- normalized_catalog JSON
- engine_catalog_ref/hash
- status

Pipeline tham chiếu `active_schema_snapshot_id`.

## 15.2. Diff classification

| Change | Severity | Default V1 behavior |
|---|---|---|
| New stream | INFO | không auto-select |
| New optional field | INFO | auto-accept nếu engine handles và policy cho phép |
| Removed selected field | BREAKING | needs review |
| Removed selected stream | BREAKING | needs review |
| Type changed | WARNING/BREAKING | needs review |
| Cursor field removed | BREAKING | block run/config invalid |
| Primary key changed/removed | BREAKING | block dedupe mode |

## 15.3. Re-discover

Có ba trigger:
- user manual
- scheduled metadata refresh
- run failure gợi ý schema issue

Không chạy discover quá dày; có lock trên source và cache TTL.

# 16. Run / Job Management

## 16.1. Product Run state machine

```text
QUEUED
  -> STARTING
  -> RUNNING
      -> SUCCEEDED
      -> FAILED
      -> CANCEL_REQUESTED -> CANCELLED
      -> TIMED_OUT

QUEUED/STARTING có thể -> FAILED_TO_START
```

Trạng thái engine bất kỳ phải được map về enum Product. Raw engine status lưu technical metadata nhưng không sử dụng làm public contract.

## 16.2. Run list `/runs`

Columns:
- Run ID short
- Pipeline
- Trigger: manual/schedule/retry/system
- Status
- Started
- Duration
- Records/bytes
- Triggered by
- Error category nếu fail

Filters:
- status
- pipeline
- source/destination
- trigger
- date range
- error category

## 16.3. Run detail `/runs/[id]`

Header: Pipeline, status, duration, trigger, start/end, `Cancel` nếu active, `Retry` nếu failed/cancelled theo policy.

Sections:

1. Summary metrics.
2. Stream-level result nếu engine cung cấp.
3. Attempts timeline.
4. Error & remediation.
5. Technical log viewer.
6. Audit/context.

## 16.4. Attempt model

Một Run có thể có nhiều Attempt do engine/retry mechanism. Product UI không cần bắt user hiểu attempt ngay từ list, nhưng run detail phải hiển thị để debug.

## 16.5. Cancel

- Endpoint idempotent.
- Nếu already terminal -> return current state, không 500.
- State chuyển `CANCEL_REQUESTED` trước khi engine confirm.
- UI không ngay lập tức hiển thị Cancelled khi chưa confirm.

## 16.6. Retry

Retry tạo **Run mới**, không mutate lịch sử run cũ.

Fields:
- `retry_of_run_id`
- `trigger_type=RETRY`
- `triggered_by`

## 16.7. Failure categories normalized

| Category | Ví dụ | Remediation mặc định |
|---|---|---|
| AUTHENTICATION | token/password hết hạn | Update credentials + Test |
| NETWORK | timeout, DNS, firewall | Check host/network/allowlist |
| PERMISSION | thiếu read/write permission | Grant permission in source/destination |
| CONFIGURATION | config invalid | Open configuration |
| SCHEMA | field/table changed | Re-discover schema |
| RATE_LIMIT | API throttling | Retry later / adjust schedule |
| DESTINATION_WRITE | quota/storage/permission | Check destination |
| SOURCE_READ | query/read error | Check source load/permission |
| ENGINE | worker/internal Airbyte error | Retry; escalate with trace_id |
| CANCELLED | user/system cancel | No remediation |
| UNKNOWN | unmapped | Technical details + support trace |

# 17. Scheduling

## 17.1. Product schedule model

Product lưu schedule normalized để FE không phụ thuộc Airbyte schedule representation.

| Field | Example |
|---|---|
| type | MANUAL / INTERVAL / DAILY / CRON |
| interval_seconds | 3600 |
| cron_expression | `0 2 * * *` |
| timezone | `Asia/Bangkok` |
| next_run_at | computed/cache |
| overlap_policy | SKIP_IF_RUNNING |
| enabled | true |

## 17.2. Validation

- Minimum interval cấu hình system, đề xuất 15 phút V1 nếu workload/cost cần bảo vệ.
- Cron parser backend validate.
- FE show 3 lần chạy tiếp theo để user xác nhận.
- DST/timezone phải dùng timezone-aware scheduler semantics.

# 18. Monitoring / Observability

## 18.1. Overview `/overview`

Mục tiêu là trả lời “hệ thống data integration của workspace hôm nay có ổn không?”.

Cards:
- Active pipelines
- Running now
- Failed last 24h
- Success rate 7d
- Sources needing attention
- Destination needing attention

Sections:
- Recent failures
- Running pipelines
- Recent successful runs
- Connector health/update notice

## 18.2. Monitoring `/monitoring`

### Health dimensions

- Pipeline success rate.
- Failure streak.
- Last successful sync age.
- Runtime anomaly (optional V1.1).
- Source health.
- Destination health.
- Engine health.

### Pipeline health derived state

| State | Rule example |
|---|---|
| HEALTHY | latest run success, no unresolved config warning |
| RUNNING | active run |
| WARNING | no success beyond expected freshness or non-breaking schema change |
| ACTION_REQUIRED | auth/config/schema breaking issue |
| FAILED | latest run failed; not yet remediated |
| PAUSED | pipeline disabled |
| NEVER_RUN | no run |

Không lưu health như single source of truth nếu có thể derive; có thể cache để query nhanh.

## 18.3. Engine health

Platform admin view:
- Airbyte API reachable
- version
- background reconciliation lag
- active jobs
- queue/workload metric nếu có
- connector catalog refresh status

Tenant user chỉ thấy generic “Integration service operational/degraded”, không lộ infra nội bộ.

# 19. Alerts & Notifications

## 19.1. V1 alert events

- Pipeline run failed.
- N consecutive failures.
- Source authentication error.
- Destination error.
- Schema breaking change.
- Pipeline has not succeeded for freshness threshold.
- Engine degraded (platform admin).

## 19.2. Alert rule

| Field | Description |
|---|---|
| id | UUID |
| workspace_id | scope |
| event_type | RUN_FAILED etc. |
| resource_scope | all pipelines / selected pipeline |
| threshold | e.g. 3 failures |
| channel | IN_APP; EMAIL/Webhook future |
| cooldown_seconds | dedup |
| enabled | bool |

## 19.3. Dedup

Không tạo 50 notification cho cùng một pipeline fail liên tục trong 5 phút.

Dedup key gợi ý: `workspace_id + resource_id + event_type + error_fingerprint` trong cooldown window.

# 20. Audit Log

## 20.1. Event bắt buộc

- login/logout (security log tùy hệ thống auth).
- create/update/delete source.
- test source/destination (không log secret).
- credential rotation event.
- create/update/delete/pause/resume pipeline.
- manual run/cancel/retry.
- schema approval.
- member invite/remove.
- role/permission change.
- connector version change.
- engine upgrade admin action.

## 20.2. Audit schema

| Field | Description |
|---|---|
| id | UUID |
| workspace_id | tenant |
| actor_type | USER/SYSTEM/API |
| actor_id | nullable for system |
| action | `pipeline.run.cancel` |
| resource_type | PIPELINE/SOURCE/... |
| resource_id | Product UUID |
| result | SUCCESS/FAILURE |
| before_summary | sanitized JSON |
| after_summary | sanitized JSON |
| ip/user_agent | security context nếu phù hợp |
| trace_id | correlation |
| created_at | timestamp |

**NEVER:** password, access token, secret JSON, full OAuth payload.

# 21. Secrets & Credentials

## 21.1. Storage options

Ưu tiên secret manager (Vault / cloud secrets). Nếu V1 phải lưu DB encrypted, cần envelope encryption và key rotation plan; không chỉ base64/one static string trong code.

## 21.2. Product DB lưu gì

- `secret_ref`
- credential metadata: last rotated, status, provider/account hint masked
- không lưu plaintext vào entity source/destination.

## 21.3. Edit flow

FE nhận:

```json
{
  "credentials": {
    "configured": true,
    "fields": {
      "password": "********",
      "token": "********"
    }
  }
}
```

FE submit phải dùng semantics:

- omitted -> unchanged
- explicit new secret -> replace
- empty string -> validation error hoặc explicit clear action nếu field nullable

# 22. Product Data Model

## 22.1. Entity relationship - logical

```text
Workspace
  1---N Members
  1---N Sources
  1---N Destinations
  1---N Pipelines
  1---N AlertRules
  1---N AuditEvents

Source 1---N Pipelines N---1 Destination
Pipeline 1---N PipelineStreams
Pipeline 1---N PipelineRuns
PipelineRun 1---N RunAttempts
Source 1---N SchemaSnapshots
```

## 22.2. `sources`

| Field | Type | Notes |
|---|---|---|
| id | uuid PK | public product id |
| workspace_id | uuid FK | indexed |
| name | varchar | unique(workspace,name) recommended |
| connector_key | varchar | product normalized key |
| engine_source_ref | encrypted/opaque varchar | backend only |
| configuration_json | jsonb | **non-secret only** |
| secret_ref | varchar nullable | secret manager reference |
| status | enum | ACTIVE/DISABLED/DELETE_PENDING/ERROR |
| health_status | enum/cache | derived/cache |
| last_test_at | timestamptz | |
| last_test_result | enum | |
| connector_version | varchar | pinned/effective |
| created_by | uuid | |
| created_at/updated_at | timestamptz | |
| deleted_at | nullable | soft-delete if retained |

## 22.3. `destinations`

Tương tự sources với `engine_destination_ref`.

## 22.4. `pipelines`

| Field | Type | Notes |
|---|---|---|
| id | uuid | |
| workspace_id | uuid | |
| name | varchar | |
| source_id | uuid | FK |
| destination_id | uuid | FK |
| engine_connection_ref | opaque | backend only |
| status | enum | ACTIVE/PAUSED/NEEDS_REVIEW/DELETE_PENDING |
| schedule_type | enum | |
| schedule_config | jsonb | normalized |
| overlap_policy | enum | |
| active_schema_snapshot_id | uuid | |
| last_run_id | uuid nullable | cache |
| next_run_at | timestamptz nullable | |
| created_by | uuid | |
| timestamps | | |

## 22.5. `pipeline_streams`

| Field | Type |
|---|---|
| id | uuid |
| pipeline_id | uuid |
| namespace | varchar nullable |
| stream_name | varchar |
| selected | bool |
| sync_mode | enum |
| destination_sync_mode | enum nullable |
| cursor_fields | jsonb |
| primary_key_fields | jsonb |
| config_json | jsonb |
| schema_hash | varchar |

Unique `(pipeline_id, namespace, stream_name)`.

## 22.6. `pipeline_runs`

| Field | Type |
|---|---|
| id | uuid |
| workspace_id | uuid |
| pipeline_id | uuid |
| engine_job_ref | opaque nullable |
| trigger_type | MANUAL/SCHEDULE/RETRY/SYSTEM |
| triggered_by | uuid nullable |
| retry_of_run_id | uuid nullable |
| status | enum |
| error_category | enum nullable |
| error_fingerprint | varchar nullable |
| error_summary | text nullable |
| started_at/ended_at | timestamptz |
| records_synced | bigint nullable |
| bytes_synced | bigint nullable |
| technical_metadata | jsonb sanitized |

## 22.7. `engine_mappings`

Nếu muốn tách mapping khỏi entity để hỗ trợ engine khác:

| Field | Type |
|---|---|
| id | uuid |
| workspace_id | uuid |
| product_resource_type | enum |
| product_resource_id | uuid |
| engine_type | `AIRBYTE` |
| engine_resource_type | SOURCE/DESTINATION/CONNECTION/JOB |
| engine_resource_ref | encrypted/opaque |
| engine_version | varchar |
| created_at | timestamptz |

Khuyến nghị dùng bảng mapping riêng nếu roadmap có khả năng multi-engine.

# 23. API / BFF Contract - Product-owned

## 23.1. API principles

- Prefix `/api/v1`.
- Resource ID là Product UUID.
- Consistent pagination.
- Idempotency key cho create/run action có rủi ro double-submit.
- Standard error envelope.
- `trace_id` luôn có trong response lỗi.

## 23.2. Error envelope

```json
{
  "error": {
    "code": "SOURCE_AUTHENTICATION_FAILED",
    "message": "Không thể đăng nhập vào nguồn dữ liệu.",
    "category": "AUTHENTICATION",
    "remediation": {
      "action": "UPDATE_CREDENTIALS",
      "resource_id": "product-source-uuid"
    },
    "technical_message": "...sanitized...",
    "trace_id": "trc_..."
  }
}
```

## 23.3. Core endpoints - Sources

```text
GET    /api/v1/sources
POST   /api/v1/sources
GET    /api/v1/sources/{id}
PATCH  /api/v1/sources/{id}
DELETE /api/v1/sources/{id}
POST   /api/v1/sources/{id}/test
POST   /api/v1/sources/{id}/discover
POST   /api/v1/sources/{id}/enable
POST   /api/v1/sources/{id}/disable
```

### Create Source example

```json
POST /api/v1/sources
{
  "name": "Production Postgres",
  "connector_key": "source-postgres",
  "configuration": {
    "host": "db.internal",
    "port": 5432,
    "database": "app"
  },
  "credentials": {
    "username": "reader",
    "password": "<secret>"
  },
  "test_before_save": true
}
```

Response không trả secret hoặc Airbyte ID.

## 23.4. Destinations

```text
GET/POST /api/v1/destinations
GET/PATCH/DELETE /api/v1/destinations/{id}
POST /api/v1/destinations/{id}/test
POST /api/v1/destinations/{id}/enable|disable
```

## 23.5. Pipelines

```text
GET    /api/v1/pipelines
POST   /api/v1/pipelines
GET    /api/v1/pipelines/{id}
PATCH  /api/v1/pipelines/{id}
DELETE /api/v1/pipelines/{id}
POST   /api/v1/pipelines/{id}/enable
POST   /api/v1/pipelines/{id}/pause
POST   /api/v1/pipelines/{id}/rediscover
GET    /api/v1/pipelines/{id}/schema-diff
POST   /api/v1/pipelines/{id}/schema-approve
```

## 23.6. Runs

```text
GET  /api/v1/runs
GET  /api/v1/runs/{id}
POST /api/v1/pipelines/{id}/runs
POST /api/v1/runs/{id}/cancel
POST /api/v1/runs/{id}/retry
GET  /api/v1/runs/{id}/logs
```

`POST /pipelines/{id}/runs` trả HTTP 202 khi trigger accepted.

## 23.7. Catalog

```text
GET /api/v1/connectors?type=source
GET /api/v1/connectors/{connector_key}
POST /api/v1/admin/connectors/refresh
POST /api/v1/admin/connectors/{key}/upgrade
```

# 24. AirbyteAdapter Contract

## 24.1. Interface đề xuất

```python
class IntegrationEngineAdapter(Protocol):
    def health(self) -> EngineHealth: ...
    def list_connector_metadata(self) -> list[ConnectorMetadata]: ...

    def create_source(self, request: EngineSourceCreate) -> EngineResourceRef: ...
    def update_source(self, ref, request) -> None: ...
    def delete_source(self, ref) -> None: ...
    def check_source(self, ref_or_config) -> ConnectionCheckResult: ...
    def discover_source(self, ref) -> DiscoveredCatalog: ...

    def create_destination(self, request) -> EngineResourceRef: ...
    def update_destination(self, ref, request) -> None: ...
    def delete_destination(self, ref) -> None: ...
    def check_destination(self, ref_or_config) -> ConnectionCheckResult: ...

    def create_connection(self, request) -> EngineResourceRef: ...
    def update_connection(self, ref, request) -> None: ...
    def delete_connection(self, ref) -> None: ...

    def trigger_sync(self, connection_ref) -> EngineJobRef: ...
    def get_job(self, job_ref) -> EngineJobStatus: ...
    def cancel_job(self, job_ref) -> EngineJobStatus: ...
    def get_job_logs(self, job_ref) -> EngineLogResult: ...
```

## 24.2. Rule adapter

- Adapter trả domain DTO normalized, không leak raw response ra service/FE.
- Mọi Airbyte exception được map sang engine error category.
- Raw response chỉ được lưu sanitized trong debug log có retention ngắn nếu cần.
- Adapter có `compatibility_version` để contract tests biết đang test bộ mapping nào.

## 24.3. Mapping Product -> Airbyte

| Product action | Adapter operation | Airbyte conceptual operation |
|---|---|---|
| Create Source | create_source | Create source |
| Test Source | check_source | Check source connection/config |
| Discover | discover_source | Discover catalog |
| Create Destination | create_destination | Create destination |
| Create Pipeline | create_connection | Create connection |
| Update streams/schedule | update_connection | Update connection/config |
| Run now | trigger_sync | Trigger sync job |
| Get Run | get_job | Get job/status |
| Cancel | cancel_job | Cancel job |
| Delete Pipeline | delete_connection | Delete connection |

**Không viết domain service kiểu:** `airbyteClient.connections.create(...)` trực tiếp. Chỉ adapter được làm việc này.

# 25. Transaction, Saga và Failure Recovery

Product DB và Airbyte không dùng chung transaction. Phải coi các action create/update/delete là distributed workflow.

## 25.1. Create Source saga

1. Validate Product request.
2. Store secret / obtain `secret_ref`.
3. Call Airbyte create/check theo implementation choice.
4. Nếu engine create success, write Product Source + mapping trong transaction.
5. Nếu Product DB fail sau engine create -> compensation delete engine resource hoặc mark orphan cleanup task.
6. Audit result.

## 25.2. Delete saga

- Product status -> `DELETE_PENDING`.
- Disable dependent triggers.
- Call engine delete.
- Success -> soft delete/DELETED.
- Failure -> keep `DELETE_PENDING`, retry async, admin visible.

## 25.3. Idempotency

Create source/pipeline và trigger run support `Idempotency-Key` để browser retry/network timeout không tạo duplicate.

# 26. Background Workers

V1 nên có Product worker riêng, không nhét mọi thứ vào web API process.

Jobs:

- Reconcile active Airbyte jobs.
- Schedule trigger nếu Product scheduler owns scheduling (quyết định implementation).
- Catalog refresh.
- Schema refresh.
- Alert evaluation.
- Cleanup orphan engine resources.
- Retry pending delete.
- Usage aggregation.

Nếu để Airbyte scheduler hoàn toàn quản schedule, Product vẫn cần reconciliation để hiển thị state; tuy nhiên Product schedule model phải giữ normalized config để UI ổn định.

# 27. Logging, Metrics, Tracing

## 27.1. Correlation

Mọi API request có `trace_id`. Khi Product gọi Airbyte, log mapping:

`trace_id -> product_resource_id -> engine_ref -> engine_job_ref`.

## 27.2. Structured log fields

- timestamp
- level
- service
- environment
- trace_id
- workspace_id (safe identifier)
- actor_id
- resource_type/id
- operation
- duration_ms
- result
- error_code

Không log credential/config secret.

## 27.3. Product metrics

- API request count/error/p95.
- adapter request count/error/latency by operation.
- active runs.
- queued runs.
- run success rate.
- run duration.
- reconciliation lag.
- alert count.
- catalog refresh age.
- engine health.

# 28. Scale & Deployment

![Scale model](/mnt/data/ba_airbyte_assets/scale.png)

## 28.1. V1 deployment

Có thể bắt đầu Docker Compose cho dev/staging tương tự AppBI, nhưng production scale nên chuẩn bị Kubernetes hoặc orchestrator tương đương khi số sync tăng.

### Services

- reverse proxy / ingress
- frontend
- product-api x N
- product-worker x N
- product-postgres
- redis/queue nếu chọn
- secret manager
- Airbyte deployment
- Airbyte metadata dependencies
- monitoring stack

## 28.2. Scale strategy

- FE/API stateless -> horizontal scale.
- Product worker scale theo queue/reconcile throughput.
- Airbyte workload/data plane scale theo concurrent sync và connector resource profile.
- Không tăng pod Product API để giải quyết throughput data movement.

## 28.3. Quota/concurrency

V1 cần config system-level:

- max concurrent runs toàn platform.
- max concurrent runs per workspace.
- max concurrent runs per pipeline = 1 mặc định.
- minimum schedule interval.
- per-connector safeguard nếu connector nặng.

Khi quota đầy, run giữ `QUEUED` với reason thay vì fail.

# 29. Airbyte Version & Connector Upgrade Strategy

## 29.1. Pinning

Production config phải lưu:

- Airbyte platform version.
- connector effective versions hoặc policy.
- Product adapter compatibility version.

## 29.2. Upgrade flow

```text
New Airbyte version
 -> release review / breaking change scan
 -> staging upgrade
 -> DB migration check
 -> adapter contract tests
 -> smoke connectors (Postgres -> Postgres/BQ etc.)
 -> representative production-like sync
 -> observe
 -> production rollout
 -> rollback plan
```

## 29.3. Contract test suite bắt buộc

1. engine health.
2. list connector/spec.
3. create source.
4. test source.
5. discover schema.
6. create destination.
7. test destination.
8. create pipeline/connection.
9. trigger sync.
10. read running status.
11. read terminal status.
12. cancel long job.
13. update pipeline.
14. delete resources.
15. failure mapping cho credential sai.

**Release gate:** không nâng production nếu core contract fail.

## 29.4. Fork policy

V1 **không fork Airbyte platform** trừ khi có blocker không thể xử lý qua API/config/connector extension.

Nếu buộc fork:

- upstream remote giữ nguyên.
- patch nhỏ, isolated.
- không rename package/restructure lớn.
- mỗi patch có reason + upstream issue/PR nếu có.
- target custom delta nhỏ để merge upstream dễ.

# 30. Security Requirements

## 30.1. Tenant isolation

- `workspace_id` lấy từ authenticated context, không tin body/query do client gửi nếu có thể derive.
- Repository/service query luôn filter workspace.
- Engine mapping cũng verify workspace ownership trước operation.
- Cross-tenant UUID enumeration trả 404 hoặc policy-safe response.

## 30.2. Credential security

- TLS in transit.
- Encryption at rest.
- Secret redaction.
- Rotate keys có kế hoạch.
- OAuth token refresh thực hiện server side.

## 30.3. API security

- Rate limit auth và expensive operations (test/discover/run).
- CSRF strategy nếu cookie auth.
- CORS strict same-origin/allowlist.
- SSRF protection đặc biệt với connector cho phép URL/host: validate policy, network egress rules.
- No arbitrary command execution từ connector config.

## 30.4. Network

Airbyte và Product API nên ở private network. Chỉ ingress cần public. Airbyte API không public trực tiếp cho end-user.

# 31. Licensing / Commercialization Gate

Airbyte codebase có nhiều license và platform portions có ELv2-related restrictions. Trước khi commercial launch phải thực hiện legal review cho mô hình kinh doanh cụ thể.

**BA requirement:** tạo release checklist item `LIC-001 - Airbyte licensing approved for intended delivery model`.

Không coi việc đổi logo hoặc giấu Airbyte UI là đủ để giải quyết vấn đề license. Nếu sản phẩm về bản chất cung cấp substantial Airbyte functionality như hosted/managed service, cần xác minh quyền/license thương mại phù hợp.

Đây là release gate, không phải blocker để làm technical PoC/internal use.

# 32. Error Handling UX Matrix

| Error | UI message | CTA | HTTP/domain code |
|---|---|---|---|
| Invalid credential | Không thể xác thực với nguồn dữ liệu | Update credentials | SOURCE_AUTHENTICATION_FAILED |
| Host unreachable | Không thể kết nối tới máy chủ | Check host/network | SOURCE_NETWORK_UNREACHABLE |
| Permission denied | Tài khoản không có đủ quyền | View required permissions | SOURCE_PERMISSION_DENIED |
| Discover timeout | Quá thời gian đọc schema | Retry discovery | SCHEMA_DISCOVERY_TIMEOUT |
| No stream selected | Chưa chọn dữ liệu để đồng bộ | Select data | PIPELINE_NO_STREAM_SELECTED |
| Invalid cursor | Cursor không hợp lệ | Edit sync settings | PIPELINE_CURSOR_INVALID |
| Destination write denied | Không thể ghi vào đích | Update destination | DESTINATION_PERMISSION_DENIED |
| Active run exists | Pipeline đang chạy | View active run | PIPELINE_ALREADY_RUNNING |
| Dependency exists on delete | Resource đang được sử dụng | View dependencies | RESOURCE_IN_USE |
| Engine unavailable | Dịch vụ đồng bộ đang tạm gián đoạn | Retry later | ENGINE_UNAVAILABLE |

# 33. UI Screen-by-Screen Acceptance Specification

## 33.1. Overview

**Must show:** 6 health KPIs, recent failures, running syncs, sources needing action, CTA add source/create pipeline.  
**Loading:** skeleton.  
**Empty:** onboarding checklist.  
**Error:** page-level retry; partial section error không làm trắng cả page.

## 33.2. Sources List

**Must:** search, health filter, connector filter, add source, row action, pagination, permission-aware CTA.  
**Bulk:** V1 optional; nếu có delete phải dependency check từng item.  
**Test:** action hiển thị spinner per row, không block toàn list.

## 33.3. Create Source

**Must:** dynamic form, masked secret, validation, test, cancel confirmation nếu dirty.  
**Back navigation:** không mất input trong session wizard nếu user quay lại step.

## 33.4. Pipeline Wizard

**Must:** discovery loading state; stream search; select all; unsupported state; capability-based mode; schedule preview; review.  
**Large schema:** virtualized list hoặc performant table khi > 1,000 streams/fields tùy design.

## 33.5. Pipeline Detail

**Must:** Run now, Pause/Resume, status, last/next run, tabs, failure remediation, run history.  
**Running:** auto-refresh Product API.  
**Failed:** error card đứng trước metrics không quan trọng.

## 33.6. Run Detail

**Must:** terminal/active status, attempts, log viewer, cancel/retry conditions, error category.  
**Logs:** pagination/stream chunk; không tải file log khổng lồ vào browser một lần.

# 34. Frontend State Management & Query Rules

## 34.1. TanStack Query key convention

```text
['workspace', workspaceId, 'sources', filters]
['workspace', workspaceId, 'source', sourceId]
['workspace', workspaceId, 'pipelines', filters]
['workspace', workspaceId, 'pipeline', pipelineId]
['workspace', workspaceId, 'runs', filters]
['workspace', workspaceId, 'run', runId]
```

Workspace switch -> invalidate/remove previous workspace cache.

## 34.2. Mutation UX

- optimistic update chỉ cho low-risk state (ví dụ ack notification).
- create/update engine resource không optimistic giả thành công.
- mutation error sử dụng normalized backend error.
- double click guarded.

## 34.3. Polling

Run active: refetch Product Run 3-5s. Stop khi terminal. Background tab có thể giảm frequency.

# 35. Backend Service Boundaries

Khuyến nghị nếu reuse AppBI: FastAPI + SQLAlchemy/Alembic/PostgreSQL để đồng nhất stack.

Suggested modules:

```text
backend/app/modules/integrations/
  api/
  schemas/
  models/
  repositories/
  services/
  adapters/airbyte/
  domain/
  errors/
  workers/
```

### Service classes

- `ConnectorCatalogService`
- `SourceService`
- `DestinationService`
- `PipelineService`
- `SchemaService`
- `RunService`
- `MonitoringService`
- `AlertService`
- `AuditService`
- `EngineCompatibilityService`

Repository không biết Airbyte; Adapter không query Product DB trực tiếp trừ config injection cần thiết.

# 36. Migration Strategy nếu tích hợp vào AppBI hiện tại

AppBI README hiện mô tả Data Sources đã có PostgreSQL, Google Sheets, BigQuery, Snowflake, Airbyte, files. Khi thêm Integration Hub, cần tránh có hai khái niệm “Data Source” chồng nhau.

## 36.1. Product decision đề xuất

Tách rõ:

- **Integration Source/Destination/Pipeline** = layer di chuyển dữ liệu.
- **AppBI Data Source** = connection mà semantic/BI layer truy vấn.

Nếu Destination của pipeline là warehouse mà AppBI sử dụng, có thể tạo shortcut `Use this destination in AppBI` nhưng không đồng nhất hai entity trong DB một cách cứng.

## 36.2. Route collision

AppBI hiện có `/datasources`. Có hai phương án:

**Khuyến nghị:** Integration Hub nằm dưới `/integrations/*` nếu cùng một product shell:

```text
/integrations/overview
/integrations/sources
/integrations/destinations
/integrations/pipelines
/integrations/runs
```

Sidebar có nhóm `Data Integration`. Điều này tránh phá module Data Sources hiện hữu của BI.

Nếu sản phẩm mới standalone thì dùng routes ngắn `/sources`, `/pipelines` như tài liệu trên.

## 36.3. Reuse component

Nên reuse từ AppBI:

- Sidebar shell.
- Button/Badge/Input.
- PageListLayout.
- ModuleOverview.
- PaginatedCollection.
- BulkActionBar.
- OwnerBadge.
- permission hooks pattern.
- toast abstraction.
- i18n provider.

Không copy-paste component sang folder mới rồi fork style; nên dùng shared design system.

# 37. Internationalization

AppBI hiện có English/Vietnamese. Module mới phải đưa text qua i18n catalog ngay từ đầu.

Rules:

- Backend error trả `code` ổn định; FE translate message theo code khi appropriate.
- Technical message giữ English/raw sanitized.
- Date/time theo locale nhưng storage UTC.
- Schedule hiển thị timezone rõ.

# 38. Accessibility

- Keyboard navigation cho form/table/dialog.
- Focus ring theo brand token.
- Icon-only button có `aria-label`/tooltip.
- Status không chỉ dựa vào màu; có text/icon.
- Error liên kết với field.
- Minimum target size hợp lý cho mobile monitoring.
- Log viewer có selectable text và không trap keyboard.

# 39. Performance Requirements

## 39.1. FE

- Initial list screen usable <= 2.5s trên network nội bộ/standard broadband khi API healthy.
- Pagination server-side khi record > threshold.
- Schema large list không render hàng chục nghìn DOM node.
- Debounce search 250-400ms nếu server-side.

## 39.2. Backend

- Standard CRUD p95 <= 700ms không tính Airbyte external call.
- Test/discover/engine call có async timeout và explicit timeout category.
- List endpoints tránh N+1.
- Audit writes không được làm request treo lâu; nhưng critical audit không được bỏ mất.

# 40. Reliability / SLO

| Component | Target |
|---|---|
| Product API | 99.9% monthly |
| UI | tương ứng Product API/hosting |
| Run reconciliation | lag p95 < 30s |
| Alert creation after terminal failure | < 60s |
| Audit event persistence | >= 99.99% cho mutating actions |

Airbyte connector success không nằm hoàn toàn trong SLO Product vì phụ thuộc external source/destination; phải tách platform availability với integration outcome.

# 41. Testing Strategy

## 41.1. Unit tests

- domain validation.
- permission.
- status mapping.
- error mapping.
- schedule validation.
- schema diff.
- alert dedup.
- secret redaction.

## 41.2. Adapter integration/contract tests

Chạy với Airbyte version pin trong CI/staging. Đây là gate nâng version.

## 41.3. Backend integration tests

Test DB + mocked/real adapter cho:
- create saga.
- delete pending.
- run trigger.
- cancel idempotent.
- retry relation.
- tenant isolation.

## 41.4. FE component tests

- dynamic connector form.
- permission action hide/disable.
- status badge.
- wizard navigation.
- error remediation.

## 41.5. E2E

Playwright/Cypress tương đương:
- create source.
- create destination.
- pipeline wizard.
- run success.
- failure auth.
- retry.
- pause/resume.
- delete dependency.
- role restricted user.

# 42. UAT Test Cases - Release Gate

## UAT-001 Create source thành công

**Given** user có quyền create và connector healthy.  
**When** user nhập config đúng, Test pass và Save.  
**Then** Source xuất hiện list, health Healthy, không lộ Airbyte ID/secret.

## UAT-002 Credential sai

Test fail; Source không được coi là Healthy; UI show Authentication + Update credential guidance.

## UAT-003 Create pipeline full refresh

Discover > select 2 streams > full refresh > daily schedule > create > first sync. Pipeline và Run được tạo đúng.

## UAT-004 Incremental capability

Chỉ connector/stream support incremental mới có option. Cursor mandatory khi engine yêu cầu.

## UAT-005 Pipeline failed

Run fail -> status terminal Failed, error category, remediation, alert in-app.

## UAT-006 Retry

Retry failed run tạo run ID mới, link tới run cũ, không đổi lịch sử cũ.

## UAT-007 Cancel

Run active -> cancel -> Cancel requested -> Cancelled sau engine confirm. Double cancel không lỗi hệ thống.

## UAT-008 Pause

Pause pipeline -> không scheduled run mới. Existing active run tiếp tục trừ khi user cancel riêng.

## UAT-009 Delete source with dependencies

Delete source đang có pipeline -> bị chặn 409, modal liệt kê pipeline.

## UAT-010 Tenant isolation

User workspace A gọi UUID resource workspace B -> không đọc/sửa được; response policy-safe.

## UAT-011 Secret leakage

Network response, logs, audit, DB non-secret entity không có plaintext credential.

## UAT-012 Airbyte down

Product UI vẫn login/list object từ Product DB; action engine-dependent trả Engine unavailable chứ không crash toàn app.

## UAT-013 Schema breaking change

Cursor field removed -> pipeline needs review và không tự chạy với config invalid.

## UAT-014 Permission

Viewer không thấy Create/Delete/Run; backend direct API call vẫn bị 403.

## UAT-015 Upgrade regression

Staging Airbyte upgraded; 15 adapter contract scenarios pass trước production approval.

# 43. Definition of Done - Feature Level

Một story integration chỉ được Done khi đáp ứng đầy đủ:

1. BA acceptance criteria pass.
2. Backend permission check.
3. Workspace scope check.
4. Audit cho mutating action.
5. Error normalized + trace_id.
6. Secret redaction review nếu có credential/config.
7. Unit test.
8. API integration test.
9. FE loading/empty/error/success states.
10. i18n EN/VI.
11. Accessibility cơ bản.
12. Observability metric/log.
13. API documentation/OpenAPI updated.
14. Không expose Airbyte-specific object ra public contract ngoài adapter/admin debug.

# 44. Release Definition of Done - V1

V1 chỉ được coi là “sản phẩm dùng được” khi:

- Top 3-5 connector mục tiêu đã chạy E2E trong environment gần production.
- Auth/RBAC/tenant isolation security test pass.
- Secret leak scan/manual review pass.
- Backup/restore Product DB documented.
- Airbyte upgrade/rollback runbook có sẵn.
- Monitoring Product API + Airbyte health có dashboard.
- Alert platform failure có channel cho operator.
- UAT 001-015 pass.
- No P0/P1 bug.
- Legal/license gate được đánh dấu phù hợp với hình thức release (internal/PoC/commercial).

# 45. Implementation Backlog theo Epic

## EPIC 0 - Foundation

- INT-001 Product module skeleton / routes.
- INT-002 DB migrations base entities.
- INT-003 workspace scope middleware/dependency.
- INT-004 permission matrix.
- INT-005 audit framework.
- INT-006 secret abstraction.
- INT-007 normalized error envelope.
- INT-008 tracing/correlation.

## EPIC 1 - Airbyte Adapter

- INT-101 adapter interface.
- INT-102 auth/client config.
- INT-103 engine health.
- INT-104 connector metadata/spec normalization.
- INT-105 source operations.
- INT-106 destination operations.
- INT-107 discover.
- INT-108 connection/pipeline operations.
- INT-109 job trigger/status/cancel/log.
- INT-110 error mapper.
- INT-111 contract test suite.

## EPIC 2 - Connector Catalog

- catalog DB/cache.
- refresh worker.
- connector list UI.
- dynamic form schema renderer.
- version/status metadata.

## EPIC 3 - Sources & Destinations

- CRUD.
- test.
- list/detail.
- dependency delete.
- credentials update.
- health status.

## EPIC 4 - Pipeline Builder

- source/destination selector.
- discover workflow.
- stream selector.
- sync mode config.
- schedule.
- review/create saga.
- edit/rediscover/schema diff.

## EPIC 5 - Runs & Operations

- trigger run.
- run model.
- reconciler.
- run list/detail.
- logs.
- cancel/retry.
- run metrics.

## EPIC 6 - Monitoring & Alerts

- overview KPI.
- monitoring health.
- alert rules.
- notification center integration.
- failure remediation.

## EPIC 7 - Admin / Lifecycle

- members/roles integration.
- audit screen.
- engine status.
- connector version visibility.
- upgrade runbook/contract CI.

## EPIC 8 - Production Hardening

- rate limit.
- quota/concurrency.
- load test.
- security review.
- backup/restore.
- DR/runbook.
- UAT + bug burn-down.

# 46. Suggested Sprint/Phase Plan

## Phase A - Technical vertical slice

Mục tiêu: một pipeline Postgres -> destination test chạy end-to-end qua custom FE/BFF, không dùng Airbyte UI.

Deliver:
- adapter minimum.
- source/destination create/test.
- discover.
- pipeline create.
- run now.
- run status.

## Phase B - Product V1 core

- complete UI.
- schedule.
- error normalization.
- RBAC/audit/secrets.
- monitoring.

## Phase C - Production hardening

- scale/concurrency.
- alert.
- schema change.
- connector/version lifecycle.
- upgrade test.
- security/load/UAT.

# 47. Technical Decisions cần ghi ADR

Đội dev phải tạo Architecture Decision Record tối thiểu cho:

- ADR-001: Airbyte as external engine, no direct DB access.
- ADR-002: Product-owned IDs + engine mapping.
- ADR-003: Secret store strategy.
- ADR-004: Schedule ownership - Product scheduler hay Airbyte scheduler.
- ADR-005: Run reconciliation - polling/event.
- ADR-006: Connector catalog/spec caching.
- ADR-007: Schema snapshot and diff policy.
- ADR-008: Retry/cancel semantics.
- ADR-009: Airbyte version pin/upgrade.
- ADR-010: Integration Hub route/module relationship với AppBI Data Sources.

# 48. Quyết định đề xuất cho các ADR quan trọng

## ADR-004 Schedule ownership

**Khuyến nghị V1:** lưu schedule canonical ở Product DB; có thể translate xuống Airbyte scheduling nếu phù hợp, nhưng Product API là source of UX truth. Nếu sau này đổi scheduler, FE không đổi.

## ADR-005 Reconciliation

**Khuyến nghị V1:** background Product Worker polling adapter cho active jobs. Đơn giản, predictable, không phụ thuộc browser. Khi Airbyte/event infrastructure hỗ trợ tốt có thể thêm event-driven nhưng giữ reconciler làm safety net.

## ADR-006 Catalog

**Khuyến nghị:** normalized cache. Không render raw Airbyte JSON schema trực tiếp mà không adapter transform/versioning.

# 49. Risk Register

| Risk | Impact | Probability | Mitigation |
|---|---|---|---|
| Airbyte API breaking change | High | Medium | adapter + version pin + contract tests |
| Connector spec changes | High | High | spec cache + pin + migration UX |
| Secret leak | Critical | Medium | secret abstraction, redaction, security tests |
| Tenant data leak | Critical | Low/Medium | backend scope everywhere + test matrix |
| Long jobs cause API timeout | High | High | async run model + reconciliation |
| User sees raw technical error | Medium | High | normalized error taxonomy |
| Airbyte engine unavailable | High | Medium | graceful degradation + engine health |
| Schema drift breaks pipeline | High | High | snapshot/diff/review state |
| Fork diverges upstream | High | Medium | no-fork default, patch isolation |
| License model mismatch | Critical business | Medium | pre-commercial legal gate |
| AppBI Data Source naming collision | Medium | High | integration namespace/routes + clear terms |

# 50. Non-functional Acceptance Checklist

## Security

- [ ] No plaintext secret in Product DB entity.
- [ ] No secret in client response after save.
- [ ] Tenant boundary tested.
- [ ] RBAC backend tested.
- [ ] SSRF/network policy reviewed.
- [ ] Audit critical actions.

## Reliability

- [ ] Product works read-only when Airbyte down.
- [ ] Run state recovers after Product API restart.
- [ ] Reconciler resumes active jobs.
- [ ] Delete saga retries.
- [ ] Idempotency tested.

## Operability

- [ ] Health endpoints.
- [ ] Product metrics.
- [ ] Adapter metrics.
- [ ] Runbook for stuck jobs.
- [ ] Runbook for Airbyte upgrade/rollback.
- [ ] Backup/restore tested.

## UX

- [ ] All screens loading/empty/error.
- [ ] Human-readable error.
- [ ] EN/VI.
- [ ] Keyboard/basic accessibility.
- [ ] Consistent AppBI visual tokens.

# 51. API Response Examples

## 51.1. Pipeline summary

```json
{
  "id": "9ee5d7d4-...",
  "name": "Orders to Warehouse",
  "status": "ACTIVE",
  "health": "HEALTHY",
  "source": {
    "id": "src-product-uuid",
    "name": "Production Postgres",
    "connector_key": "source-postgres"
  },
  "destination": {
    "id": "dst-product-uuid",
    "name": "Analytics Warehouse",
    "connector_key": "destination-bigquery"
  },
  "schedule": {
    "type": "DAILY",
    "time": "02:00",
    "timezone": "Asia/Bangkok"
  },
  "last_run": {
    "id": "run-product-uuid",
    "status": "SUCCEEDED",
    "ended_at": "2026-08-22T02:12:44Z"
  },
  "next_run_at": "2026-08-23T02:00:00+07:00"
}
```

## 51.2. Run active

```json
{
  "id": "run-product-uuid",
  "pipeline_id": "pipeline-product-uuid",
  "status": "RUNNING",
  "trigger_type": "MANUAL",
  "started_at": "2026-08-22T01:00:00Z",
  "progress": {
    "records_synced": 125000,
    "bytes_synced": 73400320
  },
  "actions": {
    "can_cancel": true,
    "can_retry": false
  }
}
```

# 52. Validation Matrix - Connector Form

Dynamic form renderer phải hiểu metadata field chuẩn:

| Metadata | UI behavior |
|---|---|
| required | label `*`, backend enforce |
| secret | password input, never echo |
| enum | select/radio tùy count |
| default | prefill non-secret default |
| min/max | number validation |
| pattern | client hint + backend validation |
| description | helper text |
| advanced | collapse under Advanced |
| oneOf | conditional section |
| deprecated | warning, không dùng cho new config nếu possible |

Nếu raw Airbyte spec có cấu trúc không map được, adapter phải log/flag `UNSUPPORTED_FORM_SCHEMA` và connector không được coi production-supported trước khi renderer support.

# 53. Connector Certification trong sản phẩm

Không phải connector Airbyte nào tồn tại cũng nên mở cho customer ngay.

Product thêm classification riêng:

- `SUPPORTED` - đã QA E2E.
- `BETA` - cho phép dùng nhưng có notice.
- `HIDDEN` - engine có nhưng FE không expose.
- `BLOCKED` - known issue/security/license/compatibility.

Đây là lớp Product-owned, không phụ thuộc release stage upstream.

## Certification checklist

- Create/test.
- Discover.
- Full refresh.
- Incremental nếu support.
- Representative data volume.
- Credential rotation.
- Error mapping.
- Schema change.
- Connector version pinned.

# 54. Operational Runbooks bắt buộc

## 54.1. Airbyte API unavailable

- Confirm Product API health.
- Check adapter health metric.
- Check Airbyte control plane endpoint.
- Stop new manual triggers nếu degraded.
- Existing product data vẫn read-only.
- Notify operator.

## 54.2. Many runs stuck

- Check workload capacity/queue.
- Check external source/destination common outage.
- Check engine version/connector rollout.
- Apply concurrency limit.
- Avoid mass retry until root cause understood.

## 54.3. Connector upgrade regression

- Roll back connector version/pin.
- Disable auto-upgrade.
- Identify impacted pipelines by connector_key/version.
- Notify affected workspace if needed.

## 54.4. Secret compromise

- Rotate Product encryption/secret credentials according to scope.
- Invalidate leaked token.
- Audit access.
- Determine affected source/destination.
- Do not expose compromised value in incident ticket/log.

# 55. Data Retention

Suggested defaults, configurable later:

| Data | Retention |
|---|---|
| Pipeline run summary | 12-24 months |
| Technical logs cached by Product | 7-30 days; prefer engine/object storage |
| Audit | >= 12 months, enterprise configurable |
| Notifications | 90-180 days |
| Schema snapshots | retain last N + referenced snapshots |
| Deleted resource metadata | 30-90 days soft delete if policy permits |
| Secrets | until rotated/deleted; deletion immediate from active secret store |

# 56. Data Privacy

- Data payload thực tế không nên đi qua Product API nếu Airbyte có thể source -> destination trực tiếp.
- Product lưu metadata, config sanitized, operational metrics; không lưu bản sao record data chỉ để monitoring.
- Log sample data disabled by default hoặc redacted.
- Connector-specific PII exposure trong logs phải được review.

# 57. FE Component Inventory đề xuất

```text
components/integrations/
  ConnectorIcon.tsx
  ConnectorPicker.tsx
  DynamicConnectorForm.tsx
  ConnectionHealthBadge.tsx
  PipelineStatusBadge.tsx
  RunStatusBadge.tsx
  ResourceHeader.tsx
  SourceDestinationPath.tsx
  StreamSelector.tsx
  StreamConfigDrawer.tsx
  ScheduleEditor.tsx
  SchedulePreview.tsx
  SchemaDiffViewer.tsx
  RunMetrics.tsx
  RunTimeline.tsx
  LogViewer.tsx
  ErrorRemediationCard.tsx
  EngineHealthBanner.tsx
```

Reuse component shared hiện có thay vì tạo Button/Input/Modal/Table mới.

# 58. FE Page Layout Examples bằng text

## 58.1. Pipelines List

```text
┌───────────────────────────────────────────────────────────────┐
│ Pipelines                                  [+ Create pipeline] │
│ 24 pipelines · 21 healthy · 2 failed · 1 paused              │
├───────────────────────────────────────────────────────────────┤
│ [Search...] [Status v] [Source v] [Destination v] [Sort v]   │
├───────────────────────────────────────────────────────────────┤
│ Orders sync      Postgres → BigQuery   Healthy   Daily 02:00  │
│ Ads ingestion    Google Ads → BQ       Failed    Every 6h    │
│ CRM backup       HubSpot → S3          Running   Every 1h    │
└───────────────────────────────────────────────────────────────┘
```

## 58.2. Pipeline Detail

```text
← Pipelines
Orders to Warehouse                   [Run now] [Pause] [•••]
Postgres → BigQuery    ● Healthy

[Overview] [Data & Schema] [Runs] [Settings] [Activity]

Last run        Next run        Success 7d       Streams
Success 12m     Tomorrow 02:00  98.6%            18

Recent runs...
```

# 59. Product API vs Admin/Debug API

Public Product API không expose engine ref. Tuy nhiên platform admin có thể cần debug.

Có thể tạo admin-only endpoint:

`GET /api/v1/admin/resources/{product_id}/engine-debug`

Response sanitized gồm engine type, version, opaque ref masked/partial, last adapter call metadata. Endpoint này phải audit và không dùng bởi FE customer bình thường.

# 60. Compatibility Matrix

Duy trì file/config versioned trong repo:

```yaml
product_version: 1.0.0
airbyte:
  tested_platform_versions:
    - "<pinned-version>"
  adapter_contract_version: "1"
connectors:
  source-postgres:
    supported_versions: ["..."]
    certification: SUPPORTED
  destination-bigquery:
    supported_versions: ["..."]
    certification: SUPPORTED
```

Không cần hard-code mọi connector nếu registry lớn, nhưng top supported connector phải có explicit certification/pin policy.

# 61. CI/CD Gates

Pull request affecting adapter/integration domain:

- lint/typecheck.
- unit tests.
- migration check.
- secret scanning.
- OpenAPI diff.
- adapter mock tests.

Release candidate:

- contract suite với Airbyte real staging.
- E2E top connectors.
- schema migration forward test.
- rollback rehearsal khi version upgrade lớn.

# 62. Product Decisions chưa cần block V1 nhưng phải để extension point

1. Billing/usage metering.
2. Customer-provided worker/data plane.
3. Per-tenant dedicated Airbyte workspace/instance.
4. Custom connectors marketplace.
5. Transformations.
6. CDC advanced tuning.
7. Data residency multi-region.
8. Customer-managed secrets.
9. SSO/SCIM.
10. Private networking/VPN/SSH tunnel configuration.

Data model/API không nên hard-code giả định khiến các extension này không thể thêm.

# 63. Multi-tenancy Strategy - đề xuất

V1 có thể dùng một Airbyte deployment phục vụ nhiều Product workspace, nhưng mapping phải rõ. Tùy capability/license/operational model, mỗi Product workspace có thể map 1:1 Airbyte workspace.

**Khuyến nghị:** Product Workspace -> Airbyte Workspace mapping 1:1 nếu self-managed architecture cho phép. Điều này đơn giản hóa isolation logical và cleanup, nhưng Product API vẫn không được tin Airbyte workspace làm security boundary duy nhất.

Khi enterprise cần hard isolation, có thể map workspace/tenant -> dedicated Airbyte deployment thông qua `engine_instance_id` trong mapping layer.

# 64. Engine Instance Abstraction

Để scale/enterprise, thêm abstraction sớm:

`engine_instances`

| Field | Meaning |
|---|---|
| id | Product UUID |
| engine_type | AIRBYTE |
| name | internal |
| base_url_ref | config/secret |
| version | platform version |
| status | HEALTHY/DEGRADED/OFFLINE |
| region | optional |
| capacity_class | optional |

Workspace mapping có `engine_instance_id`. Như vậy sau này chia tenant sang cluster khác không đổi FE object IDs.

# 65. Scheduler Ownership - implementation detail

Có hai lựa chọn kỹ thuật hợp lệ:

### Option A - Airbyte owns schedule trigger

Product translate schedule xuống engine. Product worker reconcile runs.

**Ưu:** ít scheduler code.  
**Nhược:** schedule semantics phụ thuộc version/engine, khó global quota/custom policy hơn.

### Option B - Product owns schedule trigger

Product scheduler tạo manual sync trigger đúng thời điểm; Airbyte connection đặt manual-like schedule.

**Ưu:** toàn quyền quota, audit, multi-engine, consistent schedule UX.  
**Nhược:** phải vận hành scheduler đáng tin cậy.

**Khuyến nghị khi product hóa nghiêm túc:** Option B hoặc hybrid tiến tới B. Nếu V1 muốn nhanh có thể A nhưng Product schedule contract vẫn normalized để chuyển sau.

# 66. Monitoring Freshness

Pipeline có expected freshness từ schedule. Đề xuất derived rule:

`freshness_deadline = expected_next_success + grace_period`.

Nếu deadline qua mà không có success -> Warning/Action Required và alert theo policy.

Grace period tùy schedule, ví dụ max(30 phút, 0.5 * interval) với ceiling cấu hình.

# 67. Data Quality Boundary

V1 Integration Hub chỉ đảm bảo “data movement job execution”, không tuyên bố dữ liệu đúng về business semantics. Data quality check thuộc AppBI Observability hoặc module sau.

UI wording:

- `Sync succeeded` = job hoàn tất theo engine.
- Không dùng `Data is correct`.

# 68. UX cho Non-tech User

Các setting kỹ thuật phải được progressive disclosure:

**Basic:** Name, Source, Destination, Tables, Frequency.  
**Advanced:** sync mode chi tiết, cursor, PK, namespace behavior, retries, cron.

User không cần hiểu `connection`, `actor definition`, `workload launcher`, `Temporal`.

Error technical details collapse mặc định.

# 69. UX cho Technical User

Technical user vẫn cần:

- raw-ish sanitized connector error.
- run attempt logs.
- schema field details.
- cursor/PK.
- connector version.
- trace ID.
- advanced schedule.

Do đó không “đơn giản hóa” bằng cách giấu luôn thông tin debug; chỉ đặt nó đúng layer.

# 70. Suggested Product Copy

| Context | Copy đề xuất |
|---|---|
| Create source | `Kết nối nguồn dữ liệu` |
| Test | `Kiểm tra kết nối` |
| Test success | `Kết nối thành công` |
| Pipeline | `Luồng đồng bộ` hoặc giữ `Pipeline` nếu product audience quen |
| Run now | `Đồng bộ ngay` |
| Pause | `Tạm dừng lịch chạy` |
| Rediscover | `Làm mới cấu trúc dữ liệu` |
| Schema breaking | `Cấu trúc nguồn đã thay đổi và cần bạn xác nhận` |
| Auth failed | `Thông tin đăng nhập không còn hợp lệ` |
| Engine down | `Dịch vụ đồng bộ đang tạm gián đoạn` |

Có thể giữ English technical term trong tooltip.

# 71. Checklist giao việc cho Dev Lead

Dev Lead trước khi bắt đầu code phải chốt:

- module path tích hợp AppBI hay standalone.
- Airbyte version pin.
- top connector V1.
- secret manager choice.
- Airbyte workspace mapping.
- scheduler ownership.
- polling interval/reconciliation worker.
- DB migration naming.
- RBAC mapping vào permission framework hiện có.
- environment: local/staging/prod.

Không cần chờ toàn bộ connector list mới bắt đầu; vertical slice dùng 1 source + 1 destination trước.

# 72. Acceptance Criteria theo Epic

## Adapter Epic

- Raw Airbyte client chỉ xuất hiện trong adapter package.
- 15 contract scenarios pass.
- Error mapping có fallback UNKNOWN.
- Timeout/circuit behavior documented.

## Source/Destination Epic

- CRUD + test + dependency + secret semantics pass.
- No Airbyte ID in public payload.
- All actions tenant scoped/audited.

## Pipeline Epic

- discovery, select, sync mode, schedule, create/edit/pause/delete.
- schema snapshot persisted.
- capability-driven options.

## Run Epic

- async 202 trigger.
- reconcile after API restart.
- cancel idempotent.
- retry creates new run.
- logs sanitized.

## Monitoring Epic

- failure visible <= 60s after engine terminal state.
- alert dedup.
- overview handles partial outage.

# 73. Suggested Database Indexes

Minimum indexes:

- sources `(workspace_id, status)`.
- sources `(workspace_id, connector_key)`.
- destinations same.
- pipelines `(workspace_id, status)`.
- pipelines `(workspace_id, source_id)` and destination.
- pipeline_runs `(workspace_id, pipeline_id, started_at desc)`.
- pipeline_runs `(status, started_at)` cho reconciler active.
- audit `(workspace_id, created_at desc)`.
- alerts `(workspace_id, status, created_at desc)`.
- schema snapshots `(source_id, discovered_at desc)`.

Không index mọi JSONB field mặc định; chỉ khi query pattern chứng minh cần.

# 74. Concurrency / Locking Rules

- Một source discover active tại một thời điểm.
- Một pipeline create/update critical section tránh double mapping.
- Một pipeline max one active run mặc định.
- Credential update và Test có version check để tránh user A overwrite user B.
- Use optimistic version field `version`/`updated_at` cho edit conflict.

Nếu conflict, API 409 `RESOURCE_MODIFIED`; FE yêu cầu refresh.

# 75. Soft Delete / Resource Lifecycle

Entity status chuẩn:

```text
ACTIVE
DISABLED/PAUSED
DELETE_PENDING
DELETED
ERROR (chỉ khi lifecycle operation lỗi, không thay health)
```

Health và lifecycle status phải tách. Ví dụ Source có `status=ACTIVE` nhưng `health=ERROR_AUTH`.

# 76. Data Contracts cho FE

FE không tự derive business status từ raw fields phức tạp nếu backend có thể chuẩn hóa.

Backend trả:

```json
{
  "status": "ACTIVE",
  "health": {
    "level": "ERROR",
    "code": "AUTHENTICATION",
    "label": "Action required",
    "last_checked_at": "..."
  },
  "available_actions": ["EDIT_CREDENTIALS", "TEST"]
}
```

`available_actions` hữu ích để FE không duplicate toàn bộ permission/state logic; backend vẫn validate lại khi action gọi.

# 77. Delete Constraint Contract

Chuẩn hóa dependency response để reuse pattern `DeleteConstraintModal` của AppBI:

```json
{
  "error": {
    "code": "RESOURCE_IN_USE",
    "message": "Source đang được 3 pipeline sử dụng.",
    "constraints": [
      {"type": "PIPELINE", "id": "...", "name": "Orders sync"}
    ],
    "trace_id": "..."
  }
}
```

# 78. Search / Filter / Pagination Contract

List API pattern:

```text
GET /api/v1/pipelines?q=orders&status=FAILED&limit=50&cursor=...
```

Response:

```json
{
  "items": [],
  "page": {
    "next_cursor": "...",
    "has_more": false
  },
  "summary": {
    "total": 24,
    "failed": 2
  }
}
```

Cursor pagination tốt hơn offset khi run history lớn; CRUD list nhỏ có thể offset nhưng API convention nên thống nhất dần.

# 79. API Timeout Policy

| Operation | Suggested timeout |
|---|---:|
| CRUD Product DB | 10s upper bound |
| Source/Destination test | 60-120s connector-dependent |
| Discover | 2-5 phút async nếu cần |
| Trigger run | 15-30s để engine accept, không chờ run finish |
| Get run | 10s |
| Logs | pagination/chunk |

Nếu discover có thể dài, chuyển thành async Operation resource thay vì giữ request nhiều phút.

# 80. Long-running Operation abstraction

Đề xuất generic `operations` cho Test/Discover/Upgrade nếu cần async:

```text
POST /sources/{id}/discover -> 202 operation_id
GET /operations/{id}
```

State: PENDING/RUNNING/SUCCEEDED/FAILED. FE có common operation progress component.

V1 có thể sync test nếu connector nhanh, nhưng architecture nên sẵn sàng async.

# 81. Dashboard/Reporting usage

Integration module nên expose Product API metrics để AppBI Overview có thể dùng, nhưng không bắt đầu bằng cách query thẳng Airbyte DB.

Ví dụ dataset nội bộ:
- runs per day
- success rate
- records moved
- duration
- top failing connector

Có thể aggregate từ Product `pipeline_runs`.

# 82. Compliance / Audit considerations

Nếu bán enterprise, chuẩn bị extension:

- immutable audit export.
- SSO/SCIM.
- custom retention.
- data residency.
- customer-managed key.
- private networking.
- IP allowlist.

V1 schema nên có `created_by`, `updated_by`, timestamps đầy đủ để không phải retrofit.

# 83. Supportability

Mỗi error screen cần copyable:

- Product resource ID.
- Run ID.
- trace ID.
- timestamp.
- connector/version.

Không cần show engine internal ID cho end user; support admin có debug mapping.

# 84. Product Documentation cần đi kèm release

Ngoài BA này, trước go-live cần:

1. User Guide: Connect source -> destination -> pipeline.
2. Connector setup guide cho top connectors.
3. Troubleshooting guide.
4. Admin guide: roles/secrets/engine.
5. Upgrade runbook.
6. Incident runbook.
7. API docs nếu public Product API.

# 85. Final Architecture Recommendation

Kiến trúc chuẩn để triển khai:

```text
AppBI-style Custom FE
        |
        v
Product API / BFF
  |-- Auth / RBAC / Workspace
  |-- Source / Destination / Pipeline domain
  |-- Run / Monitoring / Alert
  |-- Audit / Secret references
        |
        v
IntegrationEngineAdapter
        |
        v
Airbyte self-managed engine (pinned version)
        |
        +--> source connectors
        +--> destination connectors
```

Product-owned DB giữ business truth và mapping; Airbyte giữ execution truth của engine. Background reconciler đồng bộ trạng thái execution về Product model.

# 86. Kết luận cho Dev

Đội dev không nên bắt đầu bằng việc xóa Airbyte FE rồi sửa `airbyte-server` thành backend riêng. Hướng triển khai đúng là dựng một vertical slice qua Product API và adapter, sau đó mở rộng module.

Nếu thực hiện đúng tài liệu này, kết quả V1 phải đạt được:

- User chỉ thấy giao diện AppBI-style.
- User tạo Source/Destination/Pipeline hoàn chỉnh.
- Sync thật chạy bằng Airbyte.
- Run được theo dõi, cancel/retry.
- Lỗi có remediation.
- Không lộ secret/Airbyte internal ID.
- Workspace/RBAC/audit hoạt động.
- Engine có thể nâng version với contract test và adapter boundary.
- Hệ thống có nền để scale từ Docker-based early stage lên multi-instance/Kubernetes mà không đổi product model.

---

# Phụ lục A - Priority Matrix MoSCoW

| Requirement | Priority |
|---|---|
| Custom FE, no Airbyte UI for user | MUST |
| Product BFF/Adapter boundary | MUST |
| Sources/Destinations/Pipelines | MUST |
| Test connection | MUST |
| Discover + stream selection | MUST |
| Run + status + retry/cancel | MUST |
| Schedule | MUST |
| RBAC/tenant/audit/secrets | MUST |
| Monitoring basic | MUST |
| Schema change handling | MUST for production |
| Connector catalog/version visibility | SHOULD |
| Email/webhook alert | SHOULD |
| Custom connector UI | SHOULD/V1.1 |
| Usage/billing | COULD/V2 |
| Transformations | COULD/V2 |
| Multi-engine | COULD, architecture-ready |
| Direct Airbyte API to customer | WON'T |

# Phụ lục B - Status Color Semantics

Dùng semantic token, không hard-code page-specific color.

| Meaning | Token |
|---|---|
| Healthy/success | `success` / `success-soft` |
| Warning/action soon | `warning` |
| Failure/destructive | `danger` |
| Running/info | `info` hoặc brand |
| Paused/neutral | `text-tertiary` + `surface-2` |

# Phụ lục C - Sample Normalized Domain Events

```text
source.created
source.test.succeeded
source.test.failed
source.credentials.updated
source.schema.discovered
pipeline.created
pipeline.enabled
pipeline.paused
pipeline.schema.review_required
run.queued
run.started
run.succeeded
run.failed
run.cancel_requested
run.cancelled
run.retry_created
connector.upgrade.started
connector.upgrade.completed
engine.health.degraded
```

# Phụ lục D - Go-live Decision Checklist

- Product vertical slice stable.
- Top connectors certified.
- Security review complete.
- Tenant isolation proof/test report.
- Secret architecture approved.
- Contract tests green against production-pinned Airbyte.
- Backup/restore tested.
- Monitoring and alert live.
- On-call/runbook ready.
- User guide ready.
- License/commercial model reviewed.
- Rollback version documented.

# Phụ lục E - Source References

1. Airbyte repository: `https://github.com/airbytehq/airbyte`
2. Airbyte platform repository: `https://github.com/airbytehq/airbyte-platform`
3. Airbyte API reference: `https://reference.airbyte.com/`
4. AppBI repository: `https://github.com/QuangChinhDE/appbi-ai`
5. AppBI frontend package: `frontend/package.json`
6. AppBI Tailwind design tokens: `frontend/tailwind.config.js`
7. AppBI sidebar: `frontend/src/components/layout/Sidebar.tsx`
8. AppBI datasource page pattern: `frontend/src/app/(main)/datasources/page.tsx`

**Ghi chú:** Airbyte API/version/connector capability là dependency thay đổi theo thời gian. Dev phải pin version và dùng contract test; không dựa vào tài liệu BA như một cam kết rằng raw Airbyte endpoint sẽ bất biến.
