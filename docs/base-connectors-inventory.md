# Base.vn connectors — stream inventory

Generated from `backend/app/connectors/base_vn`. Regenerate rather than edit.

`Status` compares against the original manifests in `docs/base-api/`.

## Base Account — `source-base-account`

Người dùng và đơn vị tổ chức. Danh bạ mà mọi ứng dụng Base khác tham chiếu tới.

`https://account.<domain>/extapi/v1/` · 2 streams

| Stream | Endpoint | Records at | PK | Incremental | Page | Parent | Status |
|---|---|---|---|---|---|---|---|
| `user` | `users` | `users` | `id` | — | no | — | +pk |
| `group` | `units` | `units` | `id` | — | no | — | +pk |

## Base HRM — `source-base-hrm`

Nhân sự và mọi thứ hệ thống HR ghi nhận: hợp đồng, bảo hiểm, vị trí, khen thưởng, quá trình công tác, lương.

`https://hrm.<domain>/extapi/v1/` · 25 streams

| Stream | Endpoint | Records at | PK | Incremental | Page | Parent | Status |
|---|---|---|---|---|---|---|---|
| `employee` | `employee/list` | `employees` | `id` | `last_update` → `updated_from` | yes | — | +pagination, +incremental |
| `office` | `office/list` | `offices` | `id` | `last_update` → `updated_from` | yes | — | +pagination |
| `team` | `team/list` | `teams` | `id` | `last_update` → `updated_from` | yes | — | +pagination |
| `area` | `area/list` | `areas` | `id` | `last_update` → `updated_from` | yes | — | +pagination |
| `position` | `position/list` | `positions` | `id` | `last_update` → `updated_from` | yes | — | +pagination |
| `position_type` | `position/types` | `types` | `id` | `last_update` → `updated_from` | yes | — | +pagination |
| `contract` | `contract/list` | `contracts` | `id` | `last_update` → `updated_from` | yes | — | +pagination |
| `contract_type` | `contract/types` | `types` | `id` | — | no | — | unchanged |
| `employee_type` | `employee/types` | `types` | `id` | `last_update` → `updated_from` | yes | — | +pagination |
| `insurance` | `insurance/list` | `insurances` | `id` | `last_update` → `updated_from` | yes | — | +pagination |
| `tax` | `tax/list` | `taxes` | `id` | `last_update` → `updated_from` | yes | — | +pagination |
| `employee_work` | `employee/works` | `works` | `id` | `last_update` → `updated_from` | yes | — | unchanged |
| `employee_legal` | `employee/legals` | `legals` | `id` | `last_update` → `updated_from` | yes | — | +incremental |
| `employee_relation` | `employee/relations` | `relations` | `id` | `last_update` → `updated_from` | yes | — | unchanged |
| `employee_education` | `employee/educations` | `educations` | `id` | `last_update` → `updated_from` | yes | — | unchanged |
| `career_record` | `career/records` | `records` | `id` | `last_update` → `updated_from` | yes | — | unchanged |
| `merit_record` | `merit/records` | `records` | `id` | `last_update` → `updated_from` | yes | — | unchanged |
| `merit_award` | `merit/awards` | `awards` | `id` | `last_update` → `updated_from` | yes | — | +pagination |
| `merit_cert` | `merit/certs` | `certs` | `id` | `last_update` → `updated_from` | yes | — | +pagination |
| `merit_rule` | `merit/rules` | `rules` | `id` | `last_update` → `updated_from` | yes | — | +pagination |
| `merit_type` | `merit/types` | `types` | `id` | — | no | — | unchanged |
| `merit_template` | `merit/templates` | `templates` | `id` | — | no | — | unchanged |
| `timesheet` | `timesheet/list` | `timesheets` | `id` | — | yes | — | +pagination |
| `payroll_cycle` | `payroll/cycles` | `cycles` | `id` | `last_update` → `updated_from` | yes | — | unchanged |
| `payroll_record` | `payroll/records` | `records` | `id` | `last_update` → `updated_from` | yes | — | unchanged |

## Base E-Hiring — `source-base-hiring`

Vị trí tuyển dụng, ứng viên, và quá trình phỏng vấn.

`https://hiring.<domain>/publicapi/v2/` · 8 streams

| Stream | Endpoint | Records at | PK | Incremental | Page | Parent | Status |
|---|---|---|---|---|---|---|---|
| `opening` | `opening/list` | `openings` | `id` | — | yes | — | unchanged |
| `pool` | `pool/list` | `pools` | `id` | — | no | — | unchanged |
| `dept` | `system/depts` | `depts` | `id` | — | no | — | unchanged |
| `office` | `system/offices` | `depts` | `id` | — | no | — | unchanged |
| `candidate` | `candidate/list` | `candidates` | `id` | — | yes | `opening`.opening_id | unchanged |
| `stage` | `stage/list` | `stages` | `id` | — | yes | `opening`.opening_id | unchanged |
| `contact` | `contact/list` | `contacts` | `id` | — | yes | `pool`.pool_id | unchanged |
| `interview` | `interview/list` | `interviews` | `id` | — | yes | — | unchanged |

## Base Workflow — `source-base-workflow`

Quy trình, các giai đoạn, và mọi công việc đang chạy trong đó.

`https://workflow.<domain>/extapi/v1/` · 3 streams

| Stream | Endpoint | Records at | PK | Incremental | Page | Parent | Status |
|---|---|---|---|---|---|---|---|
| `workflow` | `workflows/get` | `workflows` | `id` | `last_update` → `updated_from` | yes | — | unchanged |
| `job` | `jobs/get` | `jobs` | `id` | `last_update` → `updated_from` | yes | — | unchanged |
| `stage` | `workflow/stages` | `stages` | `id` | — | no | `workflow`.id | unchanged |

## Base Request — `source-base-request`

Đề xuất nhân viên gửi lên, và các nhóm tiếp nhận.

`https://request.<domain>/extapi/v1/` · 2 streams

| Stream | Endpoint | Records at | PK | Incremental | Page | Parent | Status |
|---|---|---|---|---|---|---|---|
| `request` | `request/list` | `requests` | `id` | `last_update` → `updated_from` | yes | — | **new** |
| `group` | `group/list` | `groups` | `id` | — | no | — | **new** |

## Base Service — `source-base-service`

Các hàng dịch vụ, giai đoạn xử lý, và toàn bộ ticket gửi tới.

`https://service.<domain>/extapi/v1/` · 5 streams

| Stream | Endpoint | Records at | PK | Incremental | Page | Parent | Status |
|---|---|---|---|---|---|---|---|
| `service` | `service/get.all` | `services` | `id` | — | no | — | unchanged |
| `group` | `group/get.all` | `groups` | `id` | — | no | — | unchanged |
| `compound` | `compound/get.all` | `compound_blocks` | `id` | — | no | — | unchanged |
| `stage` | `service/get.stages` | `stages` | `id` | — | no | `service`.service_id | unchanged |
| `ticket` | `ticket/get.all` | `tickets` | `id` | `last_update` → `last_update_from` | yes | `service`.service_id | +pk |

## Base WeWork — `source-base-wework`

Dự án, công việc, và các cấu trúc quanh chúng.

`https://wework.<domain>/extapi/v3/` · 6 streams

| Stream | Endpoint | Records at | PK | Incremental | Page | Parent | Status |
|---|---|---|---|---|---|---|---|
| `project` | `project/list` | `projects` | `id` | — | yes | — | endpoint, +pk |
| `dept` | `dept/list` | `depts` | `id` | — | no | — | unchanged |
| `task` | `task/project` | `tasks` | `id` | `last_update` → `updated_from` | yes | `project`.id | unchanged |
| `topic` | `topic/list` | `topics` | `id` | — | no | `project`.id | unchanged |
| `tasklist` | `project/get.full` | `tasklists` | `id` | — | no | `project`.id | unchanged |
| `milestone` | `project/get.full` | `milestones` | `id` | — | no | `project`.id | +pk |

## Base Timeoff — `source-base-timeoff`

Đơn nghỉ phép và nhóm chính sách áp dụng.

`https://timeoff.<domain>/extapi/v1/` · 2 streams

| Stream | Endpoint | Records at | PK | Incremental | Page | Parent | Status |
|---|---|---|---|---|---|---|---|
| `group` | `group/list` | `groups` | `id` | — | no | — | +pk |
| `timeoff` | `timeoff/list` | `timeoffs` | `id` | `last_update` → `updated_from` | yes | — | cursor |

## Base Payroll — `source-base-payroll`

Kỳ lương, các bảng lương trong kỳ, và bản ghi lương từng nhân viên.

`https://payroll.<domain>/extapi/v1/` · 3 streams

| Stream | Endpoint | Records at | PK | Incremental | Page | Parent | Status |
|---|---|---|---|---|---|---|---|
| `cycle` | `cycle/list` | `cycles` | `id` | — | yes | — | unchanged |
| `payroll` | `payroll/list` | `payrolls` | `id` | — | yes | `cycle`.cycle_id | unchanged |
| `record` | `record/list` | `records` | `id` | — | yes | `payroll`.payroll_id | unchanged |

## Base Income — `source-base-income`

Chứng từ doanh thu, tiền đã thu, và dữ liệu danh mục đi kèm.

`https://income.<domain>/extapi/v1/` · 13 streams

| Stream | Endpoint | Records at | PK | Incremental | Page | Parent | Status |
|---|---|---|---|---|---|---|---|
| `income` | `incomes/last.update` | `data.incomes` | `id` | `last_update` → `updated_from` | yes | — | unchanged |
| `inflow` | `inflows/last.update` | `data.inflows` | `id` | `last_update` → `updated_from` | yes | — | unchanged |
| `income_item_line` | `incomes/last.update` | `data.income_item_lines` | `id` | `last_update` → `updated_from` | yes | — | unchanged |
| `inflow_item_line` | `inflows/last.update` | `data.inflow_item_lines` | `id` | `last_update` → `updated_from` | yes | — | unchanged |
| `income_payment` | `incomes/last.update` | `data.payments` | `id` | `last_update` → `updated_from` | yes | — | +pagination |
| `inflow_payment` | `inflows/last.update` | `data.payments` | `id` | `last_update` → `updated_from` | yes | — | unchanged |
| `income_estimate` | `incomes/last.update` | `data.estimates` | `id` | `last_update` → `updated_from` | yes | — | unchanged |
| `income_customer` | `incomes/last.update` | `data.customers` | `id` | `last_update` → `updated_from` | yes | — | +pagination |
| `inflow_customer` | `inflows/last.update` | `data.customers` | `id` | `last_update` → `updated_from` | yes | — | +pagination |
| `inflow_code` | `inflowcodes/get` | `data.inflow_codes` | `id` | `last_update` → `updated_from` | no | — | unchanged |
| `inflow_code_group` | `inflowcodegroups/get` | `data.inflow_code_groups` | `id` | `last_update` → `updated_from` | no | — | unchanged |
| `revenue_unit` | `revenueunits/get` | `data.revenue_units` | `id` | — | no | — | unchanged |
| `revenue_center` | `revenuecenters/get` | `data.revenue_centers` | `id` | — | no | — | unchanged |

## Removed, and why

| Stream | Reason |
|---|---|
| `service.test` | Exact duplicate of `service.ticket`. Every sync crawled every ticket twice. |
| `payroll.test` | `GET /test`, empty extractor, no records. |
| `income.income_inflow` | `inflows` read from the incomes endpoint; `inflow` reads the endpoint that owns them. |
| `income.inflow_income` | `incomes` read from the inflows endpoint. |

## Totals

- **69 streams** across 10 connectors
- 42 unchanged, 25 improved, 2 new, 4 removed

## What changed, per stream

- `account.group` — +pk
- `account.user` — +pk
- `hrm.area` — +pagination
- `hrm.contract` — +pagination
- `hrm.employee_legal` — +incremental
- `hrm.employee_type` — +pagination
- `hrm.employee` — +pagination, +incremental
- `hrm.insurance` — +pagination
- `hrm.merit_award` — +pagination
- `hrm.merit_cert` — +pagination
- `hrm.merit_rule` — +pagination
- `hrm.office` — +pagination
- `hrm.position_type` — +pagination
- `hrm.position` — +pagination
- `hrm.tax` — +pagination
- `hrm.team` — +pagination
- `hrm.timesheet` — +pagination
- `income.income_customer` — +pagination
- `income.income_payment` — +pagination
- `income.inflow_customer` — +pagination
- `service.ticket` — +pk
- `timeoff.group` — +pk
- `timeoff.timeoff` — cursor
- `wework.milestone` — +pk
- `wework.project` — endpoint, +pk
