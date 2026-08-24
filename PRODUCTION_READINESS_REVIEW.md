# Production Readiness Review - AppBI Data Integration / Airbyte

> **Doc [CURRENT_STATUS.md](CURRENT_STATUS.md) truoc.** Mot trang, la trang thai
> moi nhat, danh cho stakeholder va nguoi khong doc code.
>
> File nay la log review nhieu vong, doc theo thu tu thoi gian. Giu lai de truy
> vet quyet dinh; khong phai trang thai hien tai.
>
> **Trang thai hien tai trong block nay la historical** (cap nhat 2026-08-23,
> sau vong Sprint 5 / K8s manifest). Sau do dev da certify them Airbyte 1.8.5
> tren Kubernetes; doc `CURRENT_STATUS.md` va muc PM v8 o cuoi file truoc khi
> ra quyet dinh release.
>
> **Gate P0 cua PM - `AIRBYTE_API` chay that tren Airbyte self-managed: DA DAT.**
>
> Chay tren Airbyte platform `0.59.1` (compose staging, xem
> `docker-compose.airbyte.yml` de biet vi sao pin ban nay), voi
> `ENGINE_TYPE=AIRBYTE_API`:
>
> | Buoc | Ket qua |
> |---|---|
> | Source create + check | `source-postgres` 3.8.5 - HEALTHY / PASSED |
> | Destination create + check | `destination-postgres` 2.0.10 - HEALTHY / PASSED |
> | Discover | 3 stream, day du pk + sync mode + field type |
> | Connection create | 3 stream, tron full_refresh va incremental |
> | Sync lan 1 (full refresh) | 2.700 record, 453.605 byte - so dong khop source |
> | Sync lan 2 (incremental) | `orders` chi doc 7 dong moi -> cursor that su duoc luu |
> | Ket qua warehouse | 2.007 dong / 2.007 id phan biet -> `append_dedup` dung |
> | Job status + stats | Tong va per-stream deu map dung |
> | Job logs | Phan trang, da loc ANSI |
> | Connector Builder - test | `check` + `discover` chay tren Airbyte, pass |
> | Connector Builder - publish | Dang ky custom source definition, tao source that: HEALTHY / PASSED |
> | Connector Builder - sync | 100 dong tu connector tu build vao warehouse |
> | Cancel | `CANCEL_REQUESTED` -> `CANCELLED`, error code `RUN_CANCELLED` |
>
> Bang chung duoc ghi trong `compatibility.yaml` muc `airbyte_api_certification`.
> Toan bo adapter contract da chay that tren Airbyte; khong con muc nao trong
> danh sach do o trang thai chua verify.
>
> **Loi that tim ra nho chay that (khong phai review tren giay):**
>
> | Loi | Anh huong |
> |---|---|
> | `_is_lock_timeout` duoc goi nhung khong ton tai | `NameError` dung luc lock timeout xay ra - dung luc handler can chay |
> | Version endpoint sai (`POST /instance_configuration/get`) va bi `except: pass` nuot | Compatibility matrix bao engine version = null |
> | Log ANSI escape lot ra FE | Log hien `[46mplatform[0m >` nhu du lieu hong |
> | `records_synced = 0` bi map thanh `None` | Sync incremental thanh cong khong co dong moi hien thanh dau gach - trong nhu loi |
> | Manifest compiler khai bao primary key khong co trong schema | Airbyte tu choi catalog; connector tu build khong dung duoc |
> | `discover_schema` goi bang definition thay vi `sourceId` | Airbyte tra 500 NullPointerException |
> | `scripts/e2e.py` chet vi console encoding tren Windows | Chay that that bai trong nhin nhu san pham loi |
>
> **Con lai truoc khi noi "production-ready":**
>
> - Airbyte 0.59.1 Compose da certify, nhung production target la Kubernetes
>   1.x/2.x qua Helm/abctl. Phai chay lai certification tren dung cluster do.
> - Product K8s manifest/render bug cua PM v5 da duoc sua va prove bang Calico.
>   Nhung connector egress tren Airbyte worker/job namespace van chua duoc
>   exercise tren Kubernetes; Compose profile chi la bang chung cho staging.
> - Backup/restore da drill trong mot deployment, nhung cross-deployment restore
>   va real on-call rotation van con mo.

## PM review v7 - chien luoc Airbyte version / khong di lui ve 0.63.x - 2026-08-23

### 1. Ket luan PM

Khong nen lam lai theo huong Airbyte `0.63.x` de mong local Docker gon hon.
No nam dung giai doan Airbyte chuyen tu Compose sang Kubernetes/workloads, nen
neu dung Compose thi rui ro cao hon `0.59.1`, con neu dung Kubernetes thi minh
da co duong tot hon la certify 1.x/2.x bang Helm.

```text
Long-term engine target: Airbyte on Kubernetes, pinned Helm/app version
Local/dev target: AppBI lite/embedded only, hoac ket noi toi Airbyte K8s shared
Do not target: Airbyte 0.63.x/0.64.x Compose as production foundation
```

### 2. Vi sao 0.63.x khong lam he thong don gian hon

| Diem review | Evidence trong repo | PM decision |
|---|---|---|
| Platform Airbyte hien tai khong pull `latest`. | `docker-compose.airbyte.yml` pin `AIRBYTE_VERSION: ${AIRBYTE_VERSION:-0.59.1}` va moi image Airbyte platform dung bien do. `compatibility.yaml` cung ghi rule `never deploy latest`. | Khong can "lam lai" chi de het latest; neu co pull nang la connector/image cache issue, khong phai platform latest issue. |
| `0.63.x` la giai doan Airbyte deprecate Docker Compose. | Tai lieu Airbyte June/July 2024 noi chuyen sang Kubernetes/abctl, workloads ra voi Helm chart 0.390.0. Repo da verify `0.64.7` control plane len duoc Compose nhung connector job di qua workload launcher va doi `kubernetes.default.svc`. | `0.63.x/0.64.x` khong phai duong Compose production tot. Dung no co the lam kho hon vi control plane chay nhung sync job fail. |
| `0.59.1` Compose chi la certification/staging harness. | Comment trong `docker-compose.airbyte.yml` noi `0.59.1` predates workload launcher va worker start connector container tren Docker daemon. `docs/RUNBOOK-engine-upgrade.md` noi certification khong transfer sang production. | Giu neu can local real-Airbyte certification nhanh; khong lay lam production target. |
| Production path da co bang chung tot hon. | `compatibility.yaml` co `airbyte_api_certification_kubernetes` cho Airbyte `1.8.5` Helm/K8s, 11/11 operations. CI co lane `airbyte-k8s-contract`. | Chot 1.x/2.x pinned tren K8s lam huong chinh, roi nang version bang certification gate. |
| Connector image pull van can quan tri rieng. | `scripts/pull-engine-images.py` chi pull WANTED set Airbyte deployment se chay; `scripts/pull-connectors.sh --all` moi keo full catalog 650+ images. | Khong cho dev/mac dinh pull all. Dung image mirror/cache va pre-pull tap connector duoc certify. |

### 3. RCM dai han

1. **Dung Airbyte K8s pinned lam engine production.** Chon mot target ro:
   `1.8.5` da co cert trong repo, hoac chon version 2.x/current roi phai chay
   lai `airbyte-k8s-contract`, e2e, release-gate va update `compatibility.yaml`.
2. **Khong build AppBI production dua tren Airbyte 0.63.x Compose.** Neu muon
   nhanh hon, toi uu workflow dev va image cache; khong doi engine sang mot
   line chuyen tiep vua deprecate Compose vua them workload plane.
3. **Local dev mac dinh phai nhe.** `python scripts/stack.py lite` cho API/schema,
   frontend chay `npm run dev`; chi bat `airbyte` stack khi certification. Neu
   team co staging K8s shared thi local product tro thang vao engine do.
4. **Khong pull full Airbyte catalog trong default path.** Full catalog chi la
   cache/offline prep co chu dich. Release chi pre-pull connector set da certify
   hoac connector ma khach hang dung.
5. **Dung private/custom registry cho production.** Mirror platform images va
   connector images cua version da certify vao registry noi bo, pin digest/tag,
   cau hinh Helm `global.image.registry`, va record artifact bang `release-gate`.
6. **Moi lan nang Airbyte la mot release gate rieng.** Version Airbyte co the
   doi connector versions ma AppBI khong doi code; vi vay release artifact phai
   ghi engine app/chart version, connector versions actually ran, e2e evidence,
   va rollback note.

### 4. Next step cho dev

| Priority | Viec can lam | Ket qua mong doi |
|---|---|---|
| P0 | Chot `AIRBYTE_PRODUCTION_TARGET` trong docs/release checklist: 1.8.5 da certify hay version moi hon. | Khong con tranh luan 0.59/0.63/latest moi lan build. |
| P1 | Lam preset "external-k8s-engine" cho dev/staging: AppBI containers only, tro toi Airbyte K8s co san. | May dev khong phai bat 14 containers khi khong can. |
| P1 | Them guard/script canh bao neu ai chay `pull-connectors.sh --all`. | Tranh keo 650+ images vi nham tuong la can thiet. |
| P1 | Mirror/cache image theo certification artifact. | Khoi dong lan dau nhanh hon va khong phu thuoc DockerHub/upstream luc release. |
| P2 | Doi wording trong UI/docs: "Airbyte-compatible engine" o product surface, nhung technical compatibility van ghi ro Airbyte version. | White-label duoc ben ngoai ma van debug duoc luc su co. |

### 5. Production one-command bootstrap requirement

PM yeu cau sau khi code/infra gate chot xong, dev phai thiet ke mot entrypoint
duy nhat de operator production co the setup va start he thong bang mot lenh.
De xuat dung Python de giong `scripts/stack.py`, chay duoc tren Windows/Linux:

```text
python scripts/production.py install --config deploy/production.yaml
python scripts/production.py upgrade --config deploy/production.yaml
python scripts/production.py status
python scripts/production.py doctor
python scripts/production.py rollback --artifact <release-artifact>
```

Day khong nen la mot file `docker compose up` gom tat ca vao mot cuc lon. Dung
thiet ke la mot orchestrator idempotent: doc config, verify prerequisites, apply
AppBI, ket noi toi Airbyte K8s da pin, migrate DB, wait readiness, chay smoke
check, record release artifact va in URL can dung.

Minimum acceptance criteria cho file nay:

| Priority | Requirement | Fail-closed rule |
|---|---|---|
| P0 | Validate config production truoc khi apply: image registry/tag, DB/Redis URL, Airbyte URL/workspace, CIDR, secrets, storage, ingress. | Gap placeholder, `latest`, missing workspace, missing secret thi dung ngay. |
| P0 | Pin va verify engine target. | Airbyte app/chart version khac `compatibility.yaml` hoac artifact thi khong deploy tiep. |
| P0 | Tu dong chay migrations va wait `/readyz`, `/readyz?deep=1`. | Shallow ready nhung deep fail thi bao engine issue ro rang, khong claim deploy success. |
| P0 | Tao release artifact sau deploy. | Khong co artifact/evidence thi khong coi la production release. |
| P1 | Pre-pull/mirror image set theo connector da certify/duoc enable. | Khong duoc mac dinh pull full Airbyte catalog. |
| P1 | Co `doctor/status/logs` cho nguoi van hanh. | Operator khong phai mo tung container/pod de doan loi. |
| P1 | Co backup pre-upgrade va rollback instruction/artifact. | Upgrade khong duoc chay neu backup step fail. |
| P2 | Ho tro hai profile: `external-airbyte-k8s` cho production, `single-host-demo` cho demo noi bo. | Khong de demo profile bi dung nham lam production. |

Ket qua mong doi: sau nay len production, operator chi can dien mot file config
duoc review, chay mot lenh, va neu bat ky dieu kien production nao thieu thi
script dung som voi thong bao cu the. Day la DX/ops requirement, khong thay the
cho viec pin Airbyte va certify engine.

## PM review v6 sau Sprint 6 / Kustomize + Calico proof - 2026-08-23

### 1. Ket luan PM

PM da doc code/test/rendered manifest, khong chi doc report dev. Ket luan:
dev da dong dung cac finding PM v5 co the dong bang code/local infra.

```text
AIRBYTE_API architecture: accepted
Product K8s manifests + product NetworkPolicy: accepted
Release gate/runbook evidence path: accepted
Production launch: NOT GO until Airbyte itself is certified on K8s
```

### 2. Findings hien con mo

| Severity | Finding | Evidence PM doc/code check | PM decision / next step |
|---|---|---|---|
| P0 / Release blocker | Airbyte engine tren K8s 1.x/2.x van chua duoc certify live. | `compatibility.yaml:31-39` van chi co platform `0.59.1` va deployment `docker-compose.airbyte.yml (staging, single host)`. Dev report cung noi con thieu cluster co Airbyte. | Chua GO production. Can Airbyte K8s target that, pin chart/app version, chay endpoint probe, adapter contract, e2e `--evidence`, release gate, roi update `compatibility.yaml`. |
| P1 | Connector egress tren Airbyte namespace/job namespace van chua duoc prove. | Product policy da prove; nhung `docs/RUNBOOK-egress.md:162-168` noi connector pods run trong Airbyte namespace va policy/gateway cua chung chua exercise. | Truoc production co SaaS/external connectors, can NetworkPolicy/egress gateway/allowlist tren Airbyte job namespace va test bang connector pod that. |
| P2 | `overlays/production` la pattern tot, nhung gia tri trong repo van phai duoc thay bang env that luc release. | `deploy/kubernetes/overlays/production/egress-endpoints.yaml:7-8` dang co `10.42.7.0/28`; `kustomization.yaml` rewrite image ve `registry.internal/...`. Day co the la gia tri staging/example, PM khong co bang chung no la subnet/registry production that. | Release checklist phai yeu cau operator dien CIDR/registry/tag trong artifact, khong chi pass test "khac placeholder". |
| P2 | Cross-deployment restore van chua drill. | `CURRENT_STATUS.md` va dev report deu noi can Airbyte thu hai. | Chay restore sang Airbyte deployment khac de biet engine mappings/orphans duoc xu ly the nao. |
| P2 | On-call owner/escalation van la process gap. | Metrics/rules co, nhung chua co nguoi truc. | Gan pager owner, escalation path, silence/deploy policy truoc cutover. |
| P3 | Tai lieu historical van con lenh cu khong `--evidence`. | `rg` toan repo van thay historical examples trong `PRODUCTION_READINESS_REVIEW.md:1130` va `:1241`. Runbook/current docs da dung. | Khong blocker vi nam trong audit trail, nhung nen label historical ro hon hoac them note "khong copy command tu historical sections". |

### 3. PM accept / close tu v5

| Finding v5 | PM status | Bang chung PM da doi chieu |
|---|---|---|
| Kustomize inject label vao `kube-dns` selector | Closed | `deploy/kubernetes/base/kustomization.yaml:16-28` dung `labels.includeSelectors: false`; rendered overlay con `k8s-app: kube-dns` va khong con product label. |
| Test chi doc YAML nguon | Closed | `backend/tests/test_operations.py:457-573` render bang `kubectl kustomize` va assert selector DNS, registry image, namespace, placeholder CIDR. |
| K8s NetworkPolicy product chua prove tren enforcing CNI | Accepted theo evidence dev | `deploy/kubernetes/README.md:45-70` va `docs/RUNBOOK-egress.md:128-160` ghi Calico run: DNS works, DB allowed reachable, internet/metadata/unlisted blocked. PM local khong co cluster Calico de reproduce, nhung code/docs/evidence path hop ly. |
| CIDR placeholder | Closed ve code structure | Base giu `10.0.0.0/24`, overlay patch thanh `/28`, test chan placeholder va prefix qua rong. Can release-time env proof nhu finding P2 ben tren. |
| Runbook `--evidence` | Closed | `docs/RUNBOOK-engine-upgrade.md:53-58` da co `--evidence`; `release-gate.py record` local van refuse neu thieu evidence. |
| initContainers securityContext | Closed | `backend/tests/test_operations.py:328-334` check `initContainers + containers`. |
| `NEXT_PUBLIC_API_BASE` unused | Closed | `deploy/kubernetes/base/frontend.yaml` chi con `NODE_ENV`; comment giai thich ingress route `/api` truoc frontend pod. |
| README wording connector egress | Closed by PM review | `deploy/kubernetes/README.md:166-172` gio noi ro product K8s policy da chay under Calico; connector/Airbyte job-namespace policy moi la open. |

### 4. Verification PM vua chay

```text
python -m pytest tests -q                         -> 183 passed, 12 skipped
npm run typecheck                                 -> PASS
node ../scripts/check-i18n.mjs src                -> 794 vi keys, 794 en keys
python scripts/build-connector-lock.py --verify   -> PASS, 4 entries
kubectl kustomize deploy/kubernetes/base          -> renders
kubectl kustomize deploy/kubernetes/overlays/production -> renders; DNS selector clean
python scripts/stack.py status                    -> 11 containers, about 2.9 GiB
GET http://localhost:8010/readyz?deep=1           -> ready, AIRBYTE_API, DB+engine ok
kubeconform -v                                    -> not installed in PM local env
```

PM khong chay lai Calico cluster hay Airbyte K8s certification trong moi truong
nay. Do do minh accept code direction va product policy evidence, nhung release
production van **NO-GO** cho den khi Airbyte K8s certification co artifact.

## PM review v5 sau Sprint 5 / K8s manifest proof - 2026-08-23

### 1. Ket luan PM

Dev di dung huong. San pham da vuot xa PoC ban dau: product khong copy backend
Airbyte, ma delegate qua `AIRBYTE_API`; release gate da fail-closed bang
evidence; product K8s manifests da duoc ap dung tren kind; restore/KEK/alerts
co bang chung tot hon.

Nhung PM **chua GO production**.

```text
AIRBYTE_API architecture: accepted
Product K8s direction: accepted, nhung co P1 manifest/render fix truoc production
Production launch: NOT GO
```

Hai ly do chinh:

1. Airbyte engine tren Kubernetes 1.x/2.x van chua duoc certify live.
2. Rendered Kustomize manifest hien co kha nang lam hong DNS egress trong
   `NetworkPolicy`, test hien tai chua bat duoc.

### 2. Findings moi can dev xu ly

| Severity | Finding | Evidence | PM decision / next step |
|---|---|---|---|
| P0 / Release blocker | Airbyte engine K8s 1.x/2.x van chua duoc certify. | `compatibility.yaml:31-39` chi ghi `tested_platform_versions: ["0.59.1"]` va deployment `docker-compose.airbyte.yml`. `CURRENT_STATUS.md:67-74` cung ghi ro "likely is not certified". Tai lieu Airbyte chinh thuc hien tai huong deploy self-managed bang Kubernetes/Helm; `abctl` cung la kind + Helm cho local. | Chua release production. Can dung Airbyte K8s target that, pin chart/app version, chay `verify-engine-api.py`, `RUN_ENGINE_CONTRACT=1 pytest tests/test_adapter_contract.py`, `scripts/e2e.py --engine airbyte-api --evidence ...`, `release-gate.py record --evidence ... && check`, roi update `compatibility.yaml`. |
| P1 / Release blocker | `kubectl kustomize` dang mutate selector DNS cua `NetworkPolicy`; neu cluster co enforce NetworkPolicy, pod co the khong resolve duoc DNS. | `deploy/kubernetes/kustomization.yaml:16-17` dung `commonLabels`. YAML goc dung `podSelector.matchLabels: {k8s-app: kube-dns}` o `deploy/kubernetes/networkpolicy.yaml:51-55`, nhung PM chay `kubectl kustomize deploy/kubernetes` thay rendered selector thanh `app.kubernetes.io/part-of: appbi-integration` + `k8s-app: kube-dns`. Kube-dns pod gan nhu chac khong co label product nay. | Thay `commonLabels` bang label transformer/patch khong mutate external selectors, hoac patch rieng `NetworkPolicy`. Them test render output cua `kubectl kustomize`, assert DNS selector khong bi them label product. Sau do chay smoke tren cluster co CNI enforce NetworkPolicy: DNS ok, Airbyte API ok, DB/Redis ok, internet bi block. |
| P1 | K8s NetworkPolicy chua duoc prove tren moi truong production-like. | `deploy/kubernetes/README.md:29-32` noi run kind khong co Airbyte; `docs/RUNBOOK-egress.md:128-136` noi Kubernetes egress chua duoc exercise. Kind mac dinh co the accept NetworkPolicy object ma khong enforce tuy CNI. | Dung staging cluster co Calico/Cilium/managed CNI enforce NetworkPolicy. Test tu pod: DNS, DB, Redis, Airbyte API, blocked internet. Capture command/evidence vao release artifact. |
| P1 | Connector egress tren Airbyte namespace/job namespace van la open production item. | `deploy/kubernetes/networkpolicy.yaml:1-6` va `deploy/kubernetes/README.md:123-128` noi policy nay chi constrain product, khong constrain connectors. | Neu production co SaaS/external connectors, phai co NetworkPolicy/egress gateway/allowlist cho Airbyte worker/job namespace va chay bang connector pod that. Product preflight khong du de lam security boundary. |
| P2 | DB/Redis CIDR trong K8s manifest la placeholder, chua phai production overlay. | `deploy/kubernetes/networkpolicy.yaml:67-70` de `10.0.0.0/24` voi comment "Replace these CIDRs". | Tao overlay/values theo tung environment voi CIDR/service endpoints that. Khong merge production neu van la placeholder. |
| P2 | Runbook release gate con lenh cu, se fail neu operator copy-paste. | `scripts/release-gate.py:288-291` bat buoc `--evidence`. PM chay `python scripts/release-gate.py record --out certification.json` va script refuse dung. Nhung `docs/RUNBOOK-engine-upgrade.md:52-56` van ghi `e2e.py` khong `--evidence` va `release-gate.py record` khong `--evidence`. | Sua runbook va `CURRENT_STATUS.md:78-83` thanh lenh copy-paste dung: `e2e.py --evidence evidence-e2e.json`, `release-gate.py record --evidence evidence-e2e.json --out certification.json`, `release-gate.py check certification.json`. |
| P2 | Restore drill tot hon, nhung chua phu cross-deployment Airbyte. | `docs/RUNBOOK-backup-restore.md` ghi drill Compose 2026-08-23 va 21/21 credential decrypt, nhung cung noi chua drill restore sang Airbyte deployment khac. | Truoc production, chay restore sang staging Airbyte thu hai de biet mapping engine se xu ly ra sao. Day la DR blocker neu yeu cau RTO/RPO that. |
| P2 | On-call van la process gap. | `docs/RUNBOOK-oncall.md` va `deploy/monitoring/alerts.yaml` da co rule, nhung `CURRENT_STATUS.md:93` ghi chua ai carry pager. | Gan owner, escalation, alert route, silence/deploy policy. Metrics khong bang on-call. |
| P3 | Test Kubernetes doc/raw YAML chua bat bug render-time. | `_k8s_documents()` o `backend/tests/test_operations.py:264-272` doc tung file YAML va bo qua `kustomization.yaml`; vi vay test xanh khong thay bug `commonLabels`. | Them test render bang `kubectl kustomize` hoac script tuong duong trong CI. It nhat assert no product label injected into `kube-dns` selector, images da rewrite, namespace/labels dung. |
| P3 | Security test chua check `initContainers` cho no-root/read-only. | `backend/tests/test_operations.py:328-331` loop tren `spec["containers"]`; initContainers da duoc check image/pullPolicy o test khac, nhung chua check securityContext o day. | Mo rong guard sang `(initContainers + containers)` de tranh regression sau. |
| P3 | Frontend K8s env co dau hieu unused/misnamed. | `frontend/src/lib/api.ts` hard-code `BASE = '/api/v1'`; `frontend/next.config.js` dung `API_INTERNAL_URL`; `deploy/kubernetes/frontend.yaml:43` set `NEXT_PUBLIC_API_BASE`. | Khong blocker neu chi expose qua ingress. Nen don env/comment hoac them test/document rang frontend Service khong expose truc tiep; neu support direct service thi can `API_INTERNAL_URL` va egress toi API. |

### 3. PM accept / close trong vong nay

| Hang muc | Trang thai PM | Ly do |
|---|---|---|
| Huong tan dung BE Airbyte | Accepted | Repo dang delegate sang self-managed Airbyte qua API, khong phai copy code BE Airbyte vao product. |
| Release gate evidence-based | Accepted | `record` bat buoc `--evidence`; artifact khong con tu claim operation mac dinh. |
| Readiness split `/readyz` va `/readyz?deep=1` | Accepted | PM dong y: LB nen giu API trong service khi engine outage; deploy gate dung deep readiness. |
| Product K8s bootstrap/init container | Accepted co dieu kien | Migrate/API/worker da co wait-for-schema va shared image. Can fix render NetworkPolicy truoc production. |
| Backup/restore same-deployment + KEK decrypt | Accepted | Row counts + 21/21 credential decrypt la bang chung dung cho backup product/paired dump trong staging. |
| Alerts file + metric guard | Accepted | Alert rules co test metric reference; dev da sua bug dung `increase()` tren gauge. |
| Docker workflow footprint | Accepted | `stack.py lite/embedded/airbyte/status/stop` lam ro mode, khong toi uu bang cach quay lai embedded cho production. |

### 4. Next step PM uu tien

1. Sua Kustomize label mutation va them CI test tren rendered manifest.
2. Chay smoke K8s tren CNI co enforce NetworkPolicy, voi DB/Redis/Airbyte endpoint that.
3. Deploy/pin Airbyte K8s 1.x/2.x bang Helm/abctl tuy moi truong, roi chay full engine certification.
4. Update `compatibility.yaml` bang platform version/deployment/connector engine versions cua Airbyte K8s, kem release artifact.
5. Exercise connector egress tren Airbyte worker/job namespace, khong chi product namespace.
6. Sua runbook command `--evidence` de operator copy-paste la chay duoc.
7. Chay cross-deployment restore drill va gan on-call owner truoc khi production cutover.

### 5. Docker footprint / white-label ops note

PM dong y cam giac "Docker nhin qua nhieu" la van de DX/demo, nhung khong nen
toi uu bang cach gom hay copy backend Airbyte vao product. Self-managed Airbyte
can nhieu process rieng: server, worker, temporal, cron, object storage va init
job. Gop chung container se lam kho upgrade/certify hon va di nguoc muc tieu
"tan dung BE cua Airbyte".

Huong PM de xuat:

1. Mac dinh dev/UI dung `python scripts/stack.py lite` de chi chay product core;
   chi dung `stack.py airbyte` khi can cert/e2e voi engine that.
2. Trong production, khong chay Airbyte trong cung Docker project cua product.
   Airbyte nen la K8s namespace/Helm release rieng; AppBI chi hien API,
   worker, frontend/ingress, DB/Redis managed.
3. Neu can demo/local white-label, co the rename service/container tu
   `airbyte-*` sang `engine-*` / `appbi-engine-*` va doi internal DNS/env tuong
   ung. Day la cosmetic packaging, khong thay doi architecture.
4. Neu muon Docker Desktop khong hien image `airbyte/...`, phai mirror/tag lai
   image vao registry cua minh, vi cot Image lay tu image reference. Vi du
   `registry.internal/appbi/engine-server:0.59.1` tro toi digest cua
   `airbyte/server:0.59.1`. Lam duoc, nhung phai giu provenance/license va
   `compatibility.yaml` van ghi ro certified engine la Airbyte.
5. Khong doi ten trong tai lieu ky thuat thanh "engine rieng cua AppBI" neu ben
   duoi van la Airbyte. UI/customer-facing co the white-label; ops/release
   evidence phai trung thuc de debug va upgrade duoc.

### 6. Verification PM vua chay

```text
python -m pytest tests -q                         -> 177 passed, 12 skipped
npm run typecheck                                 -> PASS
node ../scripts/check-i18n.mjs src                -> 794 vi keys, 794 en keys
python scripts/build-connector-lock.py --verify   -> PASS, 4 entries
kubectl version --client                          -> v1.30.5, kustomize v5.0.4
kubectl kustomize deploy/kubernetes               -> renders, but reveals kube-dns selector mutation
kubeconform -v                                    -> not installed in PM local env
python scripts/release-gate.py record --out ...   -> refused as expected, --evidence required
```

PM khong chay live Airbyte K8s certification vi hien chua co cluster Airbyte
K8s target trong moi truong nay. Do do ket luan van la **NO-GO production**,
nhung huong dev dang lam la dung: finish infra certification, khong quay lai
embedded/demo path.

## PM review v4 sau Sprint 2 / ops hardening - 2026-08-23

### 1. Ket luan PM

PM accept rang dev da di dung huong va da xu ly gan het 8 item PM giao. San
pham hien khong con la PoC thuan: co Airbyte API mode that, release artifact,
readiness split, workspace runbook, egress profile, Docker workflow, version
evidence split, backup/secret/on-call runbook.

Quyet dinh release hien tai:

```text
AIRBYTE_API architecture: accepted
Compose staging readiness: mostly accepted
Production launch: NOT GO until certified on real Airbyte K8s 1.x/2.x
```

PM dong y voi 3 diem dev lam khac yeu cau ban dau:

- `/readyz` shallow cho load balancer va `/readyz?deep=1` cho deploy gate la
  dung ve mat van hanh. Bat LB fail khi Airbyte down se bien partial outage
  thanh total outage.
- Startup khong crash-loop khi engine dang boot la hop ly, mien la config sai
  van fatal va deep readiness/metrics bao ro.
- Voi Airbyte 0.59.1 Compose, forward proxy khong ep duoc neu khong inject
  env vao connector container. Harden bang network la cach enforce duoc trong
  profile nay.

PM cung doi chieu voi tai lieu Airbyte chinh thuc ngay 2026-08-23: Airbyte
khuyen nghi/self-managed hien di theo Kubernetes + Helm; `abctl` cung tao kind
cluster va cai bang Helm. Vi vay blocker K8s certification la blocker that,
khong phai cau chu trong doc.

### 2. Findings con can sua truoc production gate

| Severity | Finding | Evidence | PM decision / next step |
|---|---|---|---|
| P1 | `release-gate.py` co the overstate bang chung operation. | `scripts/release-gate.py:44-54` chi require 9 operation, thieu `declarative_builder_test` va `declarative_connector_publish` trong khi `compatibility.yaml:41-54` claim 11 operation. `scripts/release-gate.py:150-153` noi script khong the suy luan cancel tu run list, nhung `scripts/release-gate.py:240` lai default `--verified` bang toan bo required operations. CI co chay contract/e2e truoc, nhung artifact tu than no van co the pass neu operator chi record sau mot vai run thanh cong. | Truoc khi goi release gate la "hard gate", doi default `--verified` thanh rong/fail-closed, them 2 Builder operation vao `REQUIRED_OPERATIONS`, va bat `record` nhan/kiem artifact output tu contract/e2e/cancel/builder thay vi tin default. |
| P1 | Airbyte K8s 1.x/2.x certification van la production blocker. | `CURRENT_STATUS.md:45-52` da ghi ro blocker. `verify-engine-api.py` chi chung minh endpoint ton tai, khong chung minh semantics. Tai lieu Airbyte hien tai khuyen nghi deploy bang Helm/Kubernetes va abctl cung dung kind+Helm. | Khong release production cho den khi co cluster that va chay lai: endpoint probe, adapter contract live, e2e, egress/K8s NetworkPolicy, release artifact tren dung commit. |
| P2 | `verify-engine-api.py` chua de reproduce tren local Compose tu host. | `scripts/verify-engine-api.py:85` default `http://localhost:8001`, trong khi `docker-compose.airbyte.yml:170-191` co y khong publish Airbyte API ra host. PM chay tu host bi connection refused, nen "24/24" khong reproduce duoc bang lenh mac dinh. | Them mode/lenh ro: chay probe trong network `appbi`, hoac `stack.py engine-api-probe`, hoac publish port chi trong debug profile. Runbook can ghi command copy-paste duoc cho Compose va cho K8s. |
| P2 | `/metrics` unauthenticated chi an toan neu API khong public. | `backend/app/api/metrics.py:121` expose `/metrics`; `docs/RUNBOOK-oncall.md:16-32` noi scrape internal; `docker/nginx/nginx.conf:31-49` khong proxy `/metrics`, nhung `docker-compose.yml:125-126` van publish API port truc tiep. | Production deployment phai dam bao API service/internal port khong public, hoac protect `/metrics` bang network policy/ingress allowlist/basic auth. Day la deployment requirement, khong nen de implicit. |
| P2 | Egress hardened profile moi prove internal DB use case, khong phai SaaS allowlist. | `scripts/verify-egress.py` PM chay default: control plane blocked, Postgres reachable, public internet reachable by design. `docker-compose.egress.yml` block Internet thi SaaS connectors dung lai. | Neu production can SaaS sources, phai co host firewall/egress gateway/K8s NetworkPolicy allowlist duoc chay that. Internal-only deployment co the accept hardened profile. |
| P2 | Backup AppBI da co, nhung Airbyte backup/restore van la external dependency. | `scripts/backup.py` chi backup Product DB; `docs/RUNBOOK-backup-restore.md` dung khi tach Airbyte lifecycle, nhung Airbyte DB/log/state backup la "Airbyte's own procedure". | Truoc production, chay restore drill gom ca Product DB + Airbyte state/logs/object storage, va ghi RPO/RTO. Khong chi co runbook tren giay. |

### 3. PM accept / close items tu v3

| Item | PM status | Ghi chu |
|---|---|---|
| Release artifact | Accept co dieu kien | Co script, co stale/dirty/wrong-engine/commit checks. Can sua P1 de artifact khong tu assert operation. |
| Engine readiness | Accepted | `/readyz` vs `/readyz?deep=1` la decision tot. Co `READINESS_REQUIRE_ENGINE` cho deployment muon strict. |
| Workspace runbook | Accepted | Co `airbyte-workspace.py` list/create/verify va auto workspace chi cho local single-workspace. |
| Docker workflow | Accepted | `stack.py` giam nham lan container project khac, co lite/embedded/airbyte/status/stop. |
| Version evidence split | Accepted | Co `engine_version` migration va `/admin/compatibility` tra `bundled_image`/`engine_image`. |
| KEK rotation | Accepted | Co `rotate-kek.py`, test rewrap, va live evidence 13 records. |
| Ops runbooks | Accepted cho Compose | Backup, secret rotation, on-call, egress, workspace, engine upgrade da co. K8s runbook can duoc thuc thi that. |

### 4. Verification PM vua chay

```text
python scripts/build-connector-lock.py --verify
-> PASS: connector lock OK (4 entries)

cd backend && python -m pytest tests -q
-> PASS: 164 passed, 12 skipped

cd frontend && npm run typecheck
-> PASS

cd frontend && node ../scripts/check-i18n.mjs src
-> PASS: 794 vi keys, 794 en keys

python scripts/stack.py status
-> 11 AppBI/Airbyte containers, about 2.5 GiB

python scripts/verify-egress.py --json
-> PASS on default profile: API/Redis/Airbyte server blocked, Postgres reachable,
   public Internet reachable by design, metadata endpoint blocked

python scripts/verify-engine-api.py --url http://localhost:8001 --json
-> NOT PROVEN from host: Airbyte API is intentionally internal-only, connection refused
```

PM khong chay lai full e2e/certification trong vong nay vi stack live da duoc
dev bao cao va local PM check tap trung vao code/test/static + mot so probe nhe.
Lan production release bat buoc phai co artifact CI/live tren target Airbyte
that.

## PM review v3 sau bao cao Airbyte API live - 2026-08-23

### 1. Ket luan PM

Dev da di dung huong. Vong nay da chuyen duoc product tu trang thai "PoC co
embedded runner" sang "da chung minh product noi duoc voi Airbyte self-managed
that qua `ENGINE_TYPE=AIRBYTE_API`". PM chap nhan dong **P0 gate ve integration
that voi Airbyte** dua tren:

- Evidence live trong muc current-status va `compatibility.yaml`.
- Overlay `docker-compose.airbyte.yml` pin Airbyte `0.59.1`, Airbyte server chi
  noi bo, Product API/worker noi qua Config API.
- CI co lane `airbyte-api-contract` chay contract/e2e voi `--engine airbyte-api`.
- Local verification PM vua chay xanh: connector lock, backend tests, frontend
  typecheck, i18n.

Nhung chua duoc goi la production-ready hoan chinh. Trang thai moi la:

```text
Architecture/API direction: accepted
P0 Airbyte real-engine proof: closed
Production launch gate: still open
```

### 2. Findings PM sau khi doc tai lieu va code

| Severity | Finding | Evidence | PM decision / next step |
|---|---|---|---|
| P0 | Dev da dung dung huong khi dung Airbyte API thay vi copy BE Airbyte. | `backend/app/adapters/airbyte_api/adapter.py`, `docker-compose.airbyte.yml`, `compatibility.yaml` da co certification live. | Tiep tuc lay `AIRBYTE_API` lam production path. `AIRBYTE_EMBEDDED` chi nen la local/demo/smoke path. |
| P1 | Nightly contract chua du de release production neu khong thanh release gate. | `.github/workflows/ci.yml` co `airbyte-api-contract` theo schedule/workflow_dispatch. | Moi ban release can yeu cau lan `airbyte-api-contract` moi nhat pass tren infra Airbyte that va luu artifact: Airbyte platform version, source/destination definition version, job id, logs, row counts. |
| P1 | Production target chua phai Airbyte 1.x/2.x. | Current certification la Airbyte `0.59.1` compose staging; tai lieu cung ghi 0.59.1 la compose-supported line cuoi. | Neu production dung Kubernetes/Helm/abctl thi phai pin 1.x/2.x va certify lai toan bo 11/11 operation tren ban do. Khong lay 0.59.1 compose lam bang chung cuoi cho production K8s. |
| P1 | Egress Internet cua connector van mo. | Dev do duoc product API/Redis/Docker host blocked, Postgres reachable; nhung bridge network van ra Internet. | Thiet ke egress o tang infra Airbyte worker: forward proxy, host firewall, Kubernetes NetworkPolicy/egress gateway. Product preflight chi la guardrail UX, khong phai bien gioi bao mat. |
| P1 | `/readyz` va startup chua check Airbyte reachable. | `backend/app/core/readiness.py` co `check_engine_reachable()`, nhung `enforce_at_startup()` chi check static config; `backend/app/main.py` `/readyz` moi check DB. | Production readiness nen fail neu DB ready nhung Airbyte engine down/unreachable, hoac it nhat them `/readyz?deep=1`/engine readiness dung cho orchestrator. |
| P1 | `AIRBYTE_WORKSPACE_ID` bootstrap chua ro trong compose staging. | `docker-compose.airbyte.yml` set `AIRBYTE_API_URL` nhung khong set workspace id; `readiness.py` yeu cau workspace id trong API mode. | Viet runbook/script lay/tao workspace id, luu vao secret/env deployment, va dua vao certification artifact. Khong de dev phu thuoc `.env` an tren may ca nhan. |
| P2 | Evidence connector version can tach ro product lock va Airbyte deployment. | `compatibility.yaml` lock `destination-postgres` product la `3.0.17`, nhung live certification ghi Airbyte dung `2.0.10`. | Khong nhat thiet sai vi API mode dung definition cua Airbyte deployment, nhung tai lieu can co cot "certified by Airbyte deployment" rieng de tranh claim nham. |
| P2 | Builder API mode chap nhan khong co record preview la dung, nhung can UI copy ro. | `test_declarative_read` tra `record_preview_supported: false`, chi chay check + discover tren runner Airbyte. | UI nen hien thong bao ro: API mode da verify check/discover tren Airbyte runner, sample rows khong hien vi preview khong duoc fake bang network/CDK cua product. |
| P2 | Tai lieu da co current-status nhung cac phan lich su ben duoi van de gay doc nham neu doc nhanh. | Cac muc cu van nhac PoC/embedded va connector count cu. | Giu audit trail, nhung nen them TOC hoac tach `CURRENT_STATUS.md` khi gui stakeholder/non-dev. |

### 3. Docker footprint review

PM dong y cam giac "chay qua nhieu Docker" la co co so, nhung day la cai gia
cua viec chay ca product stack va Airbyte stack tren cung mot may.

Kiem tra hien tai:

- Base product compose co 7 service: `postgres`, `redis`, `migrate`, `api`,
  `worker`, `frontend`, `proxy`.
- Embedded overlay khong them service moi, chi gan `ENGINE_TYPE=AIRBYTE_EMBEDDED`
  va mount docker socket cho `api`/`worker`.
- Airbyte API overlay dua final compose len 14 service, vi Airbyte compose can
  them `airbyte-server`, `airbyte-worker`, `temporal`, `minio`, `bootloader`,
  `storage-init`, `cron`.
- May hien tai dang chay 10 container lien quan AppBI/Airbyte va 4 container
  `appbi-ai-*` cua project khac. Rieng Airbyte server + worker dang dung khoang
  1.7GB RAM; container nang nhat hien lai la `appbi-ai-backend-1` cua project
  khac, khoang 2.3GB RAM.

Huong toi uu PM de xuat:

1. Khong toi uu bang cach quay lai embedded runner cho production. Lam vay se
   nhe Docker hon nhung di nguoc muc tieu tan dung Airbyte backend.
2. Tao lenh/profile ro rang:
   - `up-lite`: chi chay product can thiet cho FE/API dev.
   - `up-embedded`: local/demo nho, 7 service, co docker socket.
   - `up-airbyte-cert`: stack day du 14 service, chi dung khi certify Airbyte
     API hoac test sync that.
3. Local FE work nen uu tien chay frontend bang `npm run dev` va tro API ve
   staging/shared Airbyte, khong bat Airbyte full stack ca ngay.
4. Neu co staging Airbyte dung chung, local product chi can `api`, `worker`,
   `postgres`, `redis` hoac dung DB/Redis managed/shared tuy muc tieu test.
5. Production nen tach Airbyte sang Kubernetes/Helm/abctl hoac namespace rieng.
   AppBI production khong nen la mot compose gom ca Airbyte. Khi do product
   stack co the con: API, worker, frontend/static hosting, Redis, Product DB,
   ingress/proxy; Airbyte co ha tang rieng cua no.
6. Thay `postgres`, `redis`, `minio`, `proxy` local container bang managed
   service/ingress/object storage trong production neu ha tang cho phep. MinIO
   chi nen la local/staging convenience.
7. Them doc "stop safe" cho dev: dung `docker compose ... stop` voi stack khong
   can thiet; tranh `down -v` neu khong muon mat volume test.

### 4. Next steps PM giao dev

1. Bien `airbyte-api-contract` thanh release gate: co checklist/artifact truoc
   moi release, khong chi la nightly.
2. Pin va certify Airbyte production target 1.x/2.x tren Kubernetes/Helm/abctl.
3. Bo sung readiness engine: startup hoac `/readyz` production phai phan biet
   DB ready voi Airbyte engine ready.
4. Viet runbook `AIRBYTE_WORKSPACE_ID`: cach khoi tao, lay id, rotate/chuyen
   workspace, va noi luu secret/env.
5. Chot egress control design cho Airbyte worker layer va test lai tu connector
   network.
6. Lam gon Docker/dev workflow bang compose profiles hoac Makefile scripts:
   `up-lite`, `up-embedded`, `up-airbyte-cert`, `stop-airbyte`.
7. Tach ro version evidence trong `compatibility.yaml`: product bundled
   connector lock vs Airbyte deployment connector definition version da certify.
8. Them ops runbook con thieu: backup/restore, secret rotation, on-call alerts.

### 5. Verification PM vua chay

```text
python scripts/build-connector-lock.py --verify
-> PASS: connector lock OK (4 entries)

python -m pytest tests -q
-> PASS: 150 passed, 12 skipped

npm run typecheck
-> PASS

node ../scripts/check-i18n.mjs src
-> PASS: 794 vi keys, 794 en keys
```

PM khong chay lai live Airbyte contract trong vong review nay de tranh bat lai
toan bo stack nang tren may dev. Ket luan live dua tren evidence dev ghi trong
`compatibility.yaml`/current-status va doi chieu code/CI. Lan release that can
co artifact CI/live run di kem.

Ngay review: 2026-08-23  
Nguon doc: `BA_AppBI_Data_Integration_Airbyte.md`, `README.md`, `compatibility.yaml`, code backend/adapter hien tai.

## 1. Ket luan dieu hanh

Ky vong san pham ban dau la dung suc manh Airbyte connector/ecosystem, nhung thay UI bang AppBI-style custom FE. Dieu nay khong co nghia la copy source code BE Airbyte ve sua, va cung khong nen viet lai toan bo Airbyte worker.

Huong production dung theo BA la:

```text
Custom FE
  -> Product API / BFF
  -> IntegrationEngineAdapter
  -> Airbyte self-managed engine da pin version
  -> Airbyte source/destination connectors
```

Hien trang repo da co Product API/BFF, UI, registry connector, adapter boundary va mot vertical slice chay duoc sync that. Tuy nhien duong mac dinh hien tai la `AIRBYTE_EMBEDDED`: backend tu goi `docker run airbyte/source-*` va `airbyte/destination-*` qua Airbyte Protocol. Cach nay huu ich cho demo/PoC, nhung neu muc tieu production la tan dung day du "BE cua Airbyte" va ho tro rong Airbyte ecosystem, day khong nen la production path chinh.

Quyet dinh can chot voi dev:

- Production path nen uu tien `AIRBYTE_API` + Airbyte self-managed deployment pinned version.
- `AIRBYTE_EMBEDDED` chi nen giu cho local demo, smoke test nho, hoac fallback ky thuat da gioi han.
- Khong duoc claim "dung duoc moi source-destination Airbyte" theo nghia SLA. Nen chia thanh `SUPPORTED`, `BETA`, `BLOCKED`, va chi `SUPPORTED` moi co cam ket sau khi qua certification matrix.

## 2. BA dang yeu cau gi

BA dat ra cac guardrail quan trong:

- FE khong goi Airbyte truc tiep. Moi request di qua Product API/BFF.
- Product Backend khong doc/ghi truc tiep Airbyte metadata DB.
- Khong expose Airbyte internal ID ra URL/public API cua san pham.
- Airbyte-specific request/response chi nam trong `AirbyteAdapter`.
- Khong deploy `latest`; phai biet Product version tuong thich Airbyte platform/connector version nao.
- Upgrade Airbyte phai qua staging, contract test, migration check, smoke sync.
- Connector UI phai capability-driven, khong hard-code hanh vi.
- V1 khong fork Airbyte platform tru khi co blocker khong giai quyet duoc qua API/config/connector extension.

BA cung neu ro muc tieu cuoi: custom FE/AppBI UX, Product DB giu business truth va mapping, Airbyte giu execution truth cua engine.

## 3. Hien trang san pham trong repo

Nhung diem da lam dung huong:

- Co `IntegrationEngineAdapter` lam boundary giua domain Product va engine.
- Co hai implementation: `AIRBYTE_EMBEDDED` va `AIRBYTE_API`.
- Co Product-owned entities: sources, destinations, pipelines, runs, engine mappings, audit, secret store.
- Co connector registry bundle tu Airbyte OSS registry.
- Registry hien tai co 654 connector, khong co connector nao thieu version va khong co tag `latest` trong field `version`.
- Co 4 connector dang duoc mark `SUPPORTED`: `source-postgres`, `source-faker`, `source-file`, `destination-postgres`.
- Builder declarative manifest dung runner pinned `airbyte/source-declarative-manifest:7.28.2`.
- Docker build backend khong clone Airbyte source code.

Nhung diem dang the hien day van la PoC/vertical slice:

- Compose hien tai khong chay Airbyte platform/server. Mac dinh `ENGINE_TYPE=AIRBYTE_EMBEDDED`.
- `AIRBYTE_API` adapter co code, nhung chua thay staging Airbyte deployment/version pin di kem trong repo.
- `compatibility.yaml` de `tested_platform_versions: []`, tuc Airbyte platform version chua duoc certify.
- Chi 4 connector duoc certify; con lai `BETA`.
- Chua co pair-level compatibility matrix cho source-destination.
- OAuth connector UX/flow chua duoc hoan thien.
- `scripts/pull-connectors.sh` dang pull tat ca connector trong registry, khong chi connector da support; dieu nay rat nang va khong phai bang chung production support.
- `connector_registry.json` ghi `product_version: 2.0.0`, trong khi `compatibility.yaml` va runtime config la `1.0.0`.
- Tags da pin theo version nhung chua pin theo image digest, nen van con rui ro neu upstream image tag bi thay doi.

## 4. Vi sao khong nen dung embedded runner de claim support moi image

Chay connector image truc tiep qua Airbyte Protocol co the sync du lieu that, nhung no khong tu dong bang voi viec tan dung toan bo BE Airbyte.

Airbyte platform/BE production lam nhieu viec hon viec `docker run` connector:

- connector definition lifecycle, connector registry va metadata refresh;
- source/destination/connection lifecycle;
- job orchestration, workload scheduling, retry, cancel, attempt, logs;
- state management, incremental/CDC, refresh semantics;
- OAuth va auth flow phuc tap;
- connector-specific capability, env, resource, network va mount policy;
- multi-workspace, metadata DB, migration, upgrade compatibility;
- observability va operational runbooks;
- API/backward compatibility theo Airbyte version.

Neu tu viet runner rieng de cover tat ca image, team se phai tai tao rat nhieu hanh vi cua Airbyte platform. Do la huong ton kem, kho kiem chung, va rat de gay loi khi connector moi thay doi.

## 5. Kien truc production khuyen nghi

### 5.1. Chon Airbyte self-managed la engine chinh

Nen deploy mot Airbyte self-managed instance/cluster rieng cho staging va production:

- Pin Airbyte platform version.
- Pin deployment artifact: Docker Compose bundle, Helm chart, abctl version, hoac image tags tuy cach deploy.
- Airbyte API chi nam trong private network.
- Product API/BFF la gateway duy nhat cho custom FE.
- Product DB chi luu business truth, policy, audit, mapping den Airbyte refs.
- Airbyte metadata DB thuoc Airbyte, khong query truc tiep.

### 5.2. Product BFF van can thiet

Dung Airbyte BE khong co nghia FE goi Airbyte API truc tiep. Product BFF van giu:

- auth, RBAC, workspace scope;
- product-friendly IDs;
- normalized source/destination/pipeline model;
- audit va secret policy;
- UI-friendly error envelope;
- feature flags/certification;
- mapping Product object -> Airbyte resource.

### 5.3. Scheduler ownership

De ra production nhanh voi Airbyte BE, co the bat dau hybrid:

- Product luu schedule canonical de FE va business rule on dinh.
- Adapter translate schedule xuong Airbyte neu dung Airbyte-owned scheduling.
- Worker Product reconcile job/run ve Product model.
- Khi can quota/policy nang cao, Product scheduler co the trigger manual sync theo schedule canonical.

## 6. Dinh nghia lai "ho tro moi connector Airbyte"

Khong nen viet trong product: "ho tro moi Airbyte connector" theo nghia dam bao thanh cong E2E moi cap source-destination.

Nen dung 3 muc:

| Muc | Y nghia | UI/Support |
|---|---|---|
| `SUPPORTED` | Da qua certification E2E voi version pin va destination target | Mo mac dinh, co SLA/support |
| `BETA` | Co trong registry va co the tao thu, nhung chua QA du | Co notice ro, best-effort |
| `BLOCKED` | Known issue/license/security/compatibility | Khong cho tao moi |

Neu business muon "user da pull image nao thi dung duoc image do", nen dinh nghia la:

- Product co the discover/list/render spec cho connector trong Airbyte deployment.
- User/admin co the thu tao source/destination voi canh bao `BETA`.
- Chi nhung connector/cap sync da certify moi co cam ket production.

## 7. Gap analysis de tien toi production

| Area | Hien trang | Rui ro | Viec can lam |
|---|---|---|---|
| Airbyte platform | Chua co deployment/version pin trong repo; default embedded | Khong tan dung day du BE Airbyte | Chon self-managed deployment, pin version, dua vao staging/prod |
| Adapter path | Co `AIRBYTE_API` nhung chua la production default | Code path production chua duoc certify | Bien `AIRBYTE_API` thanh duong production chinh va test E2E |
| Connector coverage | 654 connector registry, 4 `SUPPORTED` | User tuong tat ca da duoc dam bao | An/gate `BETA`, mo rong certification theo nhom connector |
| Image pin | Pin tag, chua pin digest | Tag co the drift neu upstream thay doi | Tao `connector-lock` gom repo, tag, digest, spec_hash, test date |
| Compatibility matrix | Co 4 connector pin, platform version rong | Khong biet Airbyte BE version nao da pass | Ghi platform version, adapter contract, connector pair matrix |
| Catalog refresh | Registry bundle co san; script regenerate tu upstream | Moi lan regenerate co the drift | Regenerate co PR review, diff report, contract test bat buoc |
| OAuth | V1.1 theo BA | Nhieu SaaS connector khong dung duoc | Implement OAuth server-side, callback, token refresh, secret handling |
| Dynamic form | Co renderer theo spec, chua certify spec phuc tap | Connector spec oneOf/anyOf/nested/conditional co the loi | Add unsupported-spec detection va form validation matrix |
| Pair compatibility | Chua co source-destination matrix | Source pass, destination pass, nhung pipeline fail | Test theo cap source/destination va sync mode |
| State/retry | Embedded co state path cho demo | Khac voi Airbyte job semantics khi dung BE that | Map Airbyte job/state/retry/cancel day du qua API |
| Multi-tenancy | Product workspace co mapping concept | Airbyte workspace/tenant isolation can thiet | Map Product workspace -> Airbyte workspace/instance |
| Observability | Co logs/runs basic | Kho debug incident production | Standardize trace_id, engine job ref, log retention, metrics |
| Security | Co secret store/RBAC huong dung | Connector co SSRF/egress risk | Network egress policy, private networking, rate limit expensive ops |
| Scale | Docker Compose local | Sync nhieu se qua tai | Kubernetes/orchestrator, worker capacity, connector resource policy |
| Licensing | BA mark gate `LIC-001` not cleared | Rui ro commercial | Legal review Airbyte license/delivery model |

## 8. Roadmap de dev thuc hien

### Phase 0 - Chot kien truc

- PO/Tech Lead chot: production engine la Airbyte self-managed qua `AIRBYTE_API`.
- Ghi ADR: embedded runner chi la local/demo path, khong phai production guarantee.
- Chot danh sach connector target cho release dau tien.

### Phase 1 - Dung Airbyte platform staging

- Deploy Airbyte self-managed staging voi version pin.
- Cau hinh `ENGINE_TYPE=AIRBYTE_API`.
- Map Product workspace -> Airbyte workspace.
- Tao source, destination, connection, trigger sync, cancel, logs qua Airbyte API.
- `/api/v1/admin/compatibility` phai tra engine platform version that.

### Phase 2 - Connector lock va catalog governance

- Tao lock file cho connector effective versions/digests/spec_hash.
- Moi registry refresh phai co diff report.
- `latest` bi cam trong CI.
- Product version trong registry/compatibility/runtime phai dong nhat.
- Admin UI hien version, support level, certification, last tested.

### Phase 3 - Certification matrix

Moi connector claim `SUPPORTED` phai qua:

- spec render va backend validation;
- check source/destination;
- discover schema;
- full refresh;
- incremental neu connector/stream support;
- CDC neu claim support;
- overwrite/append/append_dedup tuy destination;
- cancel/retry/failure mapping;
- wrong credential;
- schema change;
- run log va audit;
- upgrade regression.

Nen bat dau voi 5-10 connector business-critical, khong mo rong lan man.

### Phase 4 - Production hardening

- RBAC/tenant isolation audit.
- Secret lifecycle va key rotation plan.
- SSRF/egress/network policy.
- Observability dashboard va alert.
- Backup/restore Product DB va Airbyte metadata DB.
- Runbook: Airbyte down, many runs stuck, connector regression, secret compromise.
- Load/concurrency test voi connector nang.
- Rollback rehearsal.

### Phase 5 - Mo rong ecosystem

- Cho admin enable `BETA` connector theo workspace.
- Thu thap telemetry connector failure.
- Promote BETA -> SUPPORTED dua tren certification va usage.
- Block connector co risk/license/security issue.

## 9. Release gates truoc khi noi "production-ready"

Khong release production neu cac gate sau chua dat:

- Airbyte platform version pinned va hien thi trong compatibility endpoint.
- Khong co tag `latest` trong connector effective versions.
- Connector lock co digest/spec_hash cho connector supported.
- Tat ca Product public API khong expose Airbyte internal IDs.
- Product Backend khong query Airbyte metadata DB.
- Contract suite pass tren staging Airbyte that.
- Top connectors pass E2E theo pair matrix.
- OAuth flow xong cho connector SaaS neu nam trong supported list.
- Tenant isolation tests pass.
- Secret leakage tests pass.
- Monitoring, alert, runbook, rollback ready.
- Legal/license gate co ket luan cho mo hinh commercial/internal.

## 10. Viec dev nen lam ngay

1. Sua communication noi bo: repo hien tai la PoC/vertical slice chay connector image that, chua phai full Airbyte BE production.
2. Doi production target sang `AIRBYTE_API` va lap task deploy Airbyte self-managed staging.
3. Cap nhat `compatibility.yaml` them Airbyte platform tested version khi co staging.
4. Tao `connector-lock` cho 4 connector supported hien tai, sau do mo rong dan.
5. Sua `scripts/pull-connectors.sh` de mac dinh chi pull `SUPPORTED` hoac cho option `--all`.
6. Dong nhat `product_version` giua runtime config, `compatibility.yaml`, va generated registry.
7. Gate UI: mac dinh chi show/muu tien `SUPPORTED`; `BETA` canh bao ro.
8. Viet ADR: khong fork Airbyte, khong copy BE, khong expose Airbyte API cho browser.
9. Tao certification template cho tung connector va tung pair source-destination.
10. Chay full contract/E2E tren `AIRBYTE_API` truoc khi them connector moi vao `SUPPORTED`.

## 11. Thong diep can noi ro voi stakeholder

Neu muon "tan dung BE Airbyte va dung duoc ecosystem Airbyte", san pham can dung Airbyte platform nhu engine production. Custom FE va Product BFF la lop trai nghiem, policy, audit, security va mapping; khong phai de thay the Airbyte worker/server.

Neu tiep tuc phat trien theo embedded runner va mo tat ca connector, rui ro production se rat cao: moi connector moi co the can nhung semantics cua Airbyte platform ma runner rieng chua tai tao. Huong do chi nen duoc xem la demo/local path hoac mot engine toi gian cho connector da certify rat he hep.

Muc tieu thuc te nen la: "AppBI cung cap custom FE/BFF tren Airbyte engine pinned, voi connector ecosystem co governance; connector nao certified thi support production, connector chua certified thi beta/best-effort."

---

# Phan hoi ky thuat (dev) - 2026-08-23

_(Lich su - da xu ly, giu lai de truy vet.)_

Da verify tung khang dinh trong tai lieu tren bang cach chay that, khong doc tinh.

## A. Nhung diem PM noi dung - da xac nhan

| Khang dinh | Ket qua verify |
|---|---|
| `product_version` lech: registry `2.0.0` vs config/compatibility `1.0.0` | Dung |
| `tested_platform_versions: []` | Dung |
| `pull-connectors.sh` pull tat ca | Dung, va con te hon: lap ca 654 image roi in "All certified connector images are local" |
| Chua pin digest | Dung, khong co field `digest` |
| Compose khong co Airbyte platform, default embedded | Dung |
| `AIRBYTE_API` chua duoc certify | Dung - test duy nhat la `hasattr()`, chua tung chay that |

Finding quan trong nhat la muc cuoi: dang ton tai mot code path production chua bao gio duoc thuc thi.

## B. Ba diem can dieu chinh cach dat van de

1. **"Embedded chi la demo" dong khung sai.** Airbyte Protocol la contract cong khai co version; chay `source read | destination write` chinh la viec worker Airbyte lam o loi. Cai platform them vao la *van hanh* (job queue, OAuth broker, log storage, workspace isolation). Day la danh doi build-vs-operate, khong phai dung/sai.

2. **Chuyen sang `AIRBYTE_API` co chi phi chua duoc tinh trong tai lieu:** van hanh them Airbyte server + Temporal + Postgres rieng + MinIO, va **Connector Builder phai lam lai lop thuc thi** (inject manifest la co che embedded; qua API phai dang ky declarative source definition).

3. **Khong dich schedule xuong Airbyte.** ADR-004 da chot Product so huu schedule, quota va overlap policy xay tren do. Dich xuong se chia doi quyen so huu va pha ca hai.

## C. Bon lo hong tai lieu chua neu - deu da verify

1. **SSRF dang mo (nghiem trong nhat).** Builder chap nhan moi URL va connector container join chung network voi app:
   `http://169.254.169.254`, `http://postgres:5432`, `http://api:8000` deu ACCEPTED.
   Do module Connector Builder moi ship tao ra, moi hon nguon cua tai lieu nay.
2. **Khong co Alembic.** Schema tien hoa bang `create_all` + `SCHEMA_FIXUPS` viet tay. Voi du lieu that day la blocker cung.
3. **Docker socket = root tren host.** Ca `api` va `worker` mount `/var/run/docker.sock`, lai chay image community tuy y. Tai lieu nhac k8s cho *scale*, khong nhac *dac quyen*.
4. **KEK yeu duoc chap nhan im lang** - key sai chuan bi stretch bang SHA-256, chi log warning, chua co duong rotate.

## D. Da co san (tai lieu liet ke nhu viec can lam)

- Tier `SUPPORTED`/`BETA` da nam trong model va UI.
- Guardrail "khong lo Airbyte ID" va "khong query Airbyte metadata DB" da co test tu dong.
- Engine vocabulary chi ton tai trong `adapters/` - co test chan.

## E. Sprint 0 - da hoan thanh

| Viec | Trang thai |
|---|---|
| Egress policy hai lop (syntax khi save, resolve truoc khi gui) | Xong |
| Tach network `appbi-pipeline_connectors`; API va Redis khong attach | Xong, da verify live |
| `product_version` mot nguon (`compatibility.yaml`), co test chan lech | Xong |
| `pull-connectors.sh` mac dinh chi `SUPPORTED`, `--all` moi pull het | Xong |
| `connector-lock.json` co digest + spec_hash, CI chan drift | Xong |

Verify live sau khi tach network:

```text
connector -> api      : khong ket noi duoc
connector -> redis    : khong ket noi duoc
connector -> postgres : accepting connections   (dung, vi can credentials)
backend tests         : 124 passed, 19 skipped
live UAT              : 28/28
```

Con lai trong Sprint 0: tach `job-runner` de go docker.sock khoi api/worker, va duong rotate KEK. Alembic baseline.

## F. Quyet dinh da chot

- Production engine: **Airbyte self-managed qua `AIRBYTE_API`** (theo de xuat cua tai lieu nay).
- Bao mat: va toan bo trong Sprint 0, khong hoan sang phase sau.

---

# PM review sau cap nhat dev - 2026-08-23

_(Lich su - da xu ly, giu lai de truy vet.)_

## 1. Ket luan PM

Dev dang di dung huong ve mat engineering: da chuyen tu "pull tat ca connector" sang "lock/pull SUPPORTED", co `connector-lock.json` voi digest/spec_hash, co egress preflight, da tach network connector, va da chot dung Airbyte self-managed qua `AIRBYTE_API` lam production engine.

Nhung PM **chua accept la production-ready** va chua du co so de noi "dung duoc moi image source/destination Airbyte da pull". Trang thai dung hon hien tai la:

> Demo/vertical slice da co them guardrail tot. Huong production da dung, nhung production path chua duoc implement va certify end-to-end.

Rui ro lon nhat khong nam o UI nua, ma nam o viec production engine `AIRBYTE_API` van la mot decision tren tai lieu trong khi default runtime, CI va compose van dang chay theo embedded path.

## 2. Nhung diem PM accept sau update

| Hang muc | PM danh gia |
|---|---|
| `pull-connectors.sh` mac dinh chi pull `SUPPORTED`, co `--all` | Accept huong di. Giam rui ro moi lan build keo 654 image ngoai y muon. |
| `connector-lock.json` co digest/spec_hash | Accept huong di. `python scripts/build-connector-lock.py --verify` da pass local voi 5 entries. |
| Tach network connector | Accept mot phan. `api` va `redis` khong attach connector network, nhung `postgres` con attach va docker socket van la blocker rieng. |
| Egress policy cho Builder `base_url` | Accept mot phan. Da chan nhieu literal/private target, nhung chua phu het outbound URL cua connector. |
| `product_version` co test chan lech | Accept mot phan. Test guard da co, nhung runtime config van co default hard-code. |
| Phan hoi dev ve embedded | Accept ve mat ky thuat: embedded khong sai tuyet doi. Nhung ve product production, embedded chi nen la local/demo hoac certify rat hep, truoc khi co worker/queue/security/ops tuong duong Airbyte platform. |

## 3. Findings sau khi doc lai code

| Severity | Finding | Evidence trong repo | PM decision / next step |
|---|---|---|---|
| P0 | Production engine `AIRBYTE_API` chua duoc exercise nhu mot release path. | `docker-compose.yml:22` va `.env.example:26` default `AIRBYTE_EMBEDDED`; `compatibility.yaml:24-28` `tested_platform_versions: []`; CI engine contract chi boot embedded stack; `backend/app/adapters/airbyte_api/adapter.py:138` con fallback version `latest` khi metadata thieu tag. | Tao Airbyte self-managed staging co version pin ro rang, them CI/nightly contract suite voi `ENGINE_TYPE=AIRBYTE_API`, va khong mark production truoc khi pass. |
| P0 | Docker socket van mount vao app services. | `docker-compose.yml:98` va `docker-compose.yml:126` mount `/var/run/docker.sock`. | Hoan tat `job-runner` rieng hoac co isolation tuong duong; API/worker khong duoc giu docker.sock trong production. |
| P0 | Secret KEK con fallback/derive tu key sai chuan. | `backend/app/core/secrets.py:51-57` hash key sai chuan bang SHA-256 sau warning. | Production phai fail closed neu key khong dung 32-byte urlsafe-base64; them rotate/rewrap runbook va test. |
| P1 | Chua co migration system production. | `backend/app/bootstrap.py:77` dung `Base.metadata.create_all`; README va doc dev cung xac nhan chua Alembic. | Tao Alembic baseline truoc khi co du lieu that; moi schema change phai co migration forward test. |
| P1 | Egress policy moi cover Builder `base_url`, chua cover OAuth token endpoint. | `backend/app/services/builder.py:100` check `base_url`; `backend/app/services/builder.py:116-121` chi check `token_url` startswith http/https; `backend/app/services/builder.py:285-286` dua token_url vao `OAuthAuthenticator`. | Goi `egress.check_url_syntax/check_url` cho `auth.oauth.token_url` va bat test SSRF rieng. Neu connector co redirect/link pagination thi can policy runtime/proxy, khong chi preflight. |
| P1 | `source-file` dang bi lech certification. | `compatibility.yaml:66` ghi `BETA`, nhung `connector-lock.json` va `backend/tests/test_regressions.py:287-290` van coi `source-file` la `SUPPORTED`. | Chot lai: neu da certify thi sua compatibility ve `SUPPORTED`; neu chua certify thi bo khoi lock/test supported va UI mac dinh khong show nhu supported. |
| P1 | Claim "product_version mot nguon" chua dung hoan toan. | `compatibility.yaml:10` va `backend/app/core/config.py:20` cung co `1.0.0`; test guard co o `backend/tests/test_supply_chain.py:41-49`. | Chap nhan tam neu test guard bat buoc trong CI, nhung nen load runtime default tu compatibility hoac ghi ro day la "guarded duplicate", khong phai mot source. |
| P1 | Airbyte API catalog/version semantics chua duoc harden. | `backend/app/adapters/airbyte_api/adapter.py:138` fallback `latest`; `AIRBYTE_API` tested platform version rong. | Khi API khong tra version/tag ro rang thi mark connector `BETA/BLOCKED`, khong hien la production supported. |
| P1 | Connector Builder chua co production story khi dung `AIRBYTE_API`. | Dev note dung: embedded co the inject manifest truc tiep, API path can source definition lifecycle rieng. | Product decision: tam disable Builder run trong `AIRBYTE_API` mode, hoac thiet ke luong dang ky declarative source definition tren Airbyte staging. |
| P2 | Test local cho egress/builder con phu thuoc DB driver. | `python -m pytest tests/test_supply_chain.py -q` local: 19 pass, 2 fail vi host thieu `asyncpg` khi import builder. | Khong coi la product regression neu CI container du dependency, nhung nen tach test policy khoi DB import hoac dam bao dev bootstrap local chay duoc. |
| P2 | Digest drift verify phu thuoc image da pull local. | `scripts/build-connector-lock.py` chi compare digest khi co live digest; main CI co pull truoc verify. | Giu verify sau pull trong release CI; ghi ro PR backend test khong thay the release drift check. |

## 4. PM recommendation cho scope production

Khong nen dat muc tieu "support moi image Airbyte da pull" theo nghia production support. Docker pull thanh cong chi noi image ton tai, khong noi connector do chay dung voi UI/config/auth/sync mode/upgrade cua san pham.

Nen dat muc tieu thanh 3 tier:

| Tier | Y nghia product | Dieu kien |
|---|---|---|
| `SUPPORTED` | AppBI cam ket production support. | Pin version/digest/spec_hash, pass contract check/discover/read/write, pass pair E2E, co UAT va runbook. |
| `BETA` | Co the hien trong catalog co canh bao hoac admin opt-in. | Pull duoc image va doc spec, nhung chua co cam ket production. |
| `BLOCKED` | Khong cho tao connection. | License/security/SSRF/OAuth/spec/runtime issue chua xu ly. |

Voi ky vong ban dau "tan dung BE cua Airbyte va build lai FE", definition production nen la:

> AppBI FE/BFF so huu UX, policy, audit, permission, schedule va domain model; Airbyte self-managed so huu connector execution, worker/runtime, job logs thap tang va compatibility voi ecosystem.

Embedded runner chi nen giu cho local demo, dev diagnostics, hoac mot tap connector cuc hep da certify rieng.

## 5. Next step de dev lam tren cung file nay

### Sprint 0R - release blockers truoc khi lam them feature

1. Chot va sua lech `source-file` certification trong `compatibility.yaml`, registry generated, lock va test.
2. Hoan tat `job-runner` hoac isolation tuong duong de go docker.sock khoi `api`/`worker`.
3. Sua KEK production strict mode: key sai chuan phai fail, co command/runbook rotate va rewrap.
4. Tao Alembic baseline va migration test toi thieu.
5. Mo rong egress guard cho OAuth token URL va cac outbound URL runtime co the cau hinh duoc.
6. Tao staging Airbyte self-managed voi platform version pin trong `compatibility.yaml`.
7. Them CI/nightly lane `ENGINE_TYPE=AIRBYTE_API` chay contract suite tren staging Airbyte that.

### Sprint 1 - prove Airbyte API production path

1. Dung `AIRBYTE_API` de list source/destination definitions, create source/destination/connection, trigger sync, read status/logs.
2. Dam bao Product API khong leak Airbyte internal ID ra browser.
3. Ghi ro mapping Product entity -> Airbyte entity va lifecycle delete/archive.
4. Khong accept connector vao `SUPPORTED` neu chi pass embedded ma chua pass API mode.

### Sprint 2 - connector governance

1. Tao certification template cho tung connector va tung source-destination pair.
2. UI mac dinh chi uu tien `SUPPORTED`; `BETA` can opt-in va hien warning.
3. Upgrade connector phai qua lock refresh, spec diff, migration impact, contract test, smoke sync.
4. Bo sung license/security review cho connector truoc khi promote.

### Sprint 3 - ops production

1. Observability: run duration, failure rate, retry, queue lag, connector-level error taxonomy.
2. Backup/restore cho Product DB va Airbyte metadata/object store.
3. Runbook: Airbyte down, worker stuck, connector regression, secret compromise.
4. Load/concurrency test voi connector nang va dataset lon.

## 6. PM release decision hien tai

| Cau hoi | Tra loi |
|---|---|
| Dev co di dung huong khong? | Co. Huong supply-chain/security/Airbyte API la dung. |
| Co the noi production-ready chua? | Chua. Con P0/P1 blocker. |
| Co the noi dung duoc moi image Airbyte da pull chua? | Khong. Pull image chi la dieu kien thap nhat, khong phai certification. |
| Co nen tiep tuc polish UI connector luc nay khong? | Chi nen lam song song nhe; uu tien Sprint 0R va Airbyte API staging truoc. |
| PM co accept embedded lam production engine chinh khong? | Khong o scope hien tai. Embedded chi local/demo hoac limited certified runner. |

## 7. Verification PM vua chay

```text
python scripts/build-connector-lock.py --verify
-> PASS: connector lock OK (5 entries)

python -m pytest tests/test_supply_chain.py -q
-> 19 passed, 2 failed do local host thieu asyncpg khi import builder/db
```

Ket qua nay khong phu dinh bao cao CI cua dev, nhung PM khuyen dev lam cho cac test policy/security quan trong chay duoc doc lap hon tren may local de review nhanh khong bi chan boi DB driver.


---

# Dev response - Sprint 0R (2026-08-23)

_(Lich su - da xu ly, giu lai de truy vet.)_

Da verify tung finding cua PM bang cach chay that. **Khong finding nao sai.**

## 1. Da fix trong vong nay

| PM finding | Sev | Nguyen nhan goc | Da lam |
|---|---|---|---|
| `source-file` certification lech | P1 | `CURATED` gop hai khai niem: "co metadata viet tay" va "da certify". source-file co ca hai nen bi publish la SUPPORTED. | Generator doc certification tu `compatibility.yaml` (noi giu bang chung). source-file gio la BETA o ca registry, lock va test. Co test chan registry claim manh hon bang chung. |
| Fallback `latest` trong `AIRBYTE_API` | P1 | Default cua `.get()` | Doi thanh hang so `UNPINNED`; co test chan chuoi `"latest"` quay lai trong code. |
| KEK derive tu key sai chuan | P0 | Fail-open sau warning | Fail closed. Muon derive phai bat `ALLOW_DERIVED_ENCRYPTION_KEY` — mot deployment phai xin bang ten, khong nhan duoc do vo tinh. Docstring cu mo ta hanh vi da khong con dung, da sua. |
| Egress chua cover OAuth token URL | P1 | Moi cho kiem tra tu goi ten `base_url` bang tay | Them `outbound_urls()` — mot ham liet ke moi URL connector co the goi. Ca save-path va send-path deu duyet danh sach do, nen them URL moi la khong the quen. |
| Test policy phu thuoc DB driver | P2 | `app/core/db.py` tao engine **ngay khi import**, nen import mot enum cung keo Postgres driver. | Engine gio tao lazy. Va tach `builder_manifest.py` (thuan) khoi `builder.py` (persistence). |

## 2. Ket qua PM co the tu chay

```text
# tren may local, KHONG can asyncpg
python -m pytest backend/tests/test_supply_chain.py -q
-> 29 passed

python -m pytest backend/tests/test_builder.py backend/tests/test_builder_surface.py                  backend/tests/test_supply_chain.py -q
-> 63 passed
```

Truoc: 19 passed, 2 failed vi thieu `asyncpg`. Gio khong con test bao mat nao bi chan boi DB driver.

## 3. Diem PM danh gia "accept mot phan" - phan hoi

**"`product_version` van la guarded duplicate".** Dung. `compatibility.yaml` la nguon; `config.py` van co default hard-code va chi duoc test chan. De lam that su mot nguon thi runtime phai doc `compatibility.yaml`, nghia la copy file do vao image. Se lam cung Sprint 0R con lai, khong phai bay gio, va tam thoi ghi ro day la **guarded duplicate** chu khong claim la single source.

**"`postgres` con attach connector network".** Co chu y. Mot Postgres source *phai* toi duoc DB do, va no can credentials. Cai da cat la `api` va `redis`. Neu muon cat not thi phai tach demo database sang instance rieng — de xuat lam khi dung staging that o Phase 1.

**"Egress chua phu het outbound URL runtime".** Dung, va day la gioi han that: preflight khong the chan redirect hay URL sinh ra luc chay (vi du cursor pagination tra ve URL tuyet doi). Chi co egress proxy/network policy moi chan duoc. Da ghi vao muc gioi han thay vi claim da giai quyet.

## 4. Con lai trong Sprint 0R

| Viec | Trang thai |
|---|---|
| Tach `job-runner`, go docker.sock khoi api/worker | Chua lam |
| Alembic baseline + migration test | Chua lam |
| Runtime doc `product_version` tu compatibility.yaml | Chua lam |
| Staging Airbyte self-managed + CI lane `AIRBYTE_API` | Chua lam (Phase 1) |

## 5. Dong y voi ket luan cua PM

Khong claim production-ready. Trang thai dung la: **vertical slice chay that, da co guardrail supply-chain va egress, production path (`AIRBYTE_API`) chua duoc implement va certify.**


---

# Dev - Sprint 0R hoan tat (2026-08-23)

_(Lich su - da xu ly, giu lai de truy vet.)_

## 1. Docker socket - da xu ly, nhung khong bang `job-runner`

PM yeu cau "hoan tat `job-runner` **hoac co isolation tuong duong**". Truoc khi build, dev kiem tra lai mot gia dinh:

```text
grep -rn "docker_runner|DockerRunner" backend/app
-> chi xuat hien trong adapters/airbyte_protocol/
```

**Docker socket chi duoc dung boi duong embedded. `AIRBYTE_API` khong bao gio cham Docker** — Airbyte platform chay connector.

Vay build `job-runner` la di hardening mot duong ma chinh chung ta vua tuyen bo la demo-only. Do la cong suc dat sai cho. Cach xu ly dung theo quyet dinh da chot:

| | |
|---|---|
| `docker-compose.yml` (production) | **Khong mount docker.sock o bat ky service nao** |
| `docker-compose.embedded.yml` (local/demo) | Mount socket + set `ENGINE_TYPE=AIRBYTE_EMBEDDED`, phai chu dong dung |
| `.env` local | `COMPOSE_FILE` tro toi ca hai, nen dev van chay `docker compose up` binh thuong |
| Production env | Khong set `COMPOSE_FILE` -> khong bao gio co socket do vo tinh |
| API startup | Neu `AIRBYTE_EMBEDDED` ma khong co socket thi log `api.engine_unavailable` ngay luc boot, khong de user gap loi mo ho o lan test dau |

Neu sau nay muon chay embedded trong moi truong khong tin cay thi luc do `job-runner` moi dang gia. Hien tai no khong giai quyet van de nao dang ton tai.

Luu y: `COMPOSE_PATH_SEPARATOR=:` duoc pin trong `.env` vi Compose mac dinh `;` tren Windows — thieu dong do thi cau hinh chi chay dung mot nua team.

## 2. Alembic - baseline that, khong phai diff

Lan sinh dau tien ra mot **diff** (3 lenh) vi database da co san schema tu `create_all`. Da lam lai tren database rong:

```text
20 op.create_table
alembic upgrade head   (tren DB rong)  -> OK
alembic check                          -> No new upgrade operations detected
```

Autogenerate bat duoc **drift that** trong luc lam:
- `SCHEMA_FIXUPS` tao `ix_connector_definitions_display_name` va `ix_connector_definitions_owner` bang tay, trong khi model khong khai bao. Rieng `owner_workspace_id` co `index=True` nen thanh **hai index tren cung mot cot, hai ten khac nhau**.
- `support_level` co `DEFAULT 'community'` trong fixup nhung khong co `server_default` tren model.

Ca hai da duoc hoa giai ve phia model. Day dung la loai loi ma Alembic sinh ra de chan.

Bootstrap gio **stamp** revision head sau `create_schema`, nen database tao bang `create_all` van duoc Alembic theo doi va thay doi tiep theo di bang migration.

CI co lane moi: dung database rong, `alembic upgrade head && alembic check`. Migration lech voi model con te hon khong co migration — no tao cam giac schema dang duoc kiem soat.

## 3. Ket qua

```text
container : 130 passed, 21 skipped
host      : 34 passed  (test_supply_chain, khong can asyncpg)
live UAT  : 28/28
alembic   : upgrade tu rong OK, check sach
socket    : 0 trong docker-compose.yml
```

## 4. Con lai - dung nhu PM xep

| Viec | Trang thai |
|---|---|
| Runtime doc `product_version` tu compatibility.yaml | Chua — hien la **guarded duplicate**, co test chan |
| Staging Airbyte self-managed + pin platform version | Chua (Phase 1) |
| CI lane `ENGINE_TYPE=AIRBYTE_API` tren staging that | Chua (Phase 1) |
| Connector Builder tren duong `AIRBYTE_API` | Chua thiet ke |

## 5. Van giu nguyen ket luan

Khong claim production-ready. Sprint 0R da dong cac blocker ve supply chain, egress, privilege va migration. **Production path (`AIRBYTE_API`) van chua duoc implement va certify** — do la Phase 1.

---

# PM review v2 sau Sprint 0R - 2026-08-23

_(Lich su - da xu ly, giu lai de truy vet.)_

## 1. Ket luan PM moi

PM accept rang dev da xu ly dung huong phan lon Sprint 0R. Cac blocker cu ve supply-chain, `source-file`, KEK fail-open, OAuth token URL egress, local test phu thuoc DB driver va docker.sock trong base compose da co evidence tot hon ro ret.

Nhung ket luan release **khong doi**:

> Chua production-ready. Sprint 0R da lam san pham an toan hon, nhung production path `AIRBYTE_API` van chua duoc implement/certify end-to-end.

P0 bay gio gon lai hon: khong con la "repo qua mong manh de harden", ma la "chua co Airbyte self-managed staging + CI/live contract de chung minh production engine".

## 2. PM accept / close tu vong review truoc

| Hang muc | Trang thai PM | Evidence |
|---|---|---|
| `source-file` certification lech | Closed | Registry hien co 3 `SUPPORTED` (`source-postgres`, `destination-postgres`, `source-faker`) va `source-file` la `BETA`; `connector-lock.json` khong con lock `source-file`. |
| `connector-lock` | Closed cho Sprint 0R | `python scripts/build-connector-lock.py --verify` pass: `connector lock OK (4 entries)`. |
| Fallback `"latest"` trong `AIRBYTE_API` | Closed | `backend/app/adapters/airbyte_api/adapter.py:35` dung `UNPINNED`; test `backend/tests/test_supply_chain.py:384-397` chan fallback `"latest"`. |
| KEK fail-open | Closed cho Sprint 0R | `backend/app/core/secrets.py:56-65` fail closed neu key sai chuan; derive chi khi `ALLOW_DERIVED_ENCRYPTION_KEY=true`. |
| OAuth token URL egress | Closed cho save/test-read path | `backend/app/services/builder_manifest.py:91,117` va `backend/app/services/builder.py:105-106` deu walk outbound URL list. |
| Test policy phu thuoc DB driver | Closed | `backend/app/core/db.py:43-64` tao engine lazy; local test supply-chain da pass khong can asyncpg. |
| Docker socket trong base compose | Closed ve mat privilege default | `docker-compose.yml` khong con mount `/var/run/docker.sock`; socket chi nam trong `docker-compose.embedded.yml:21-33`. |
| Alembic baseline | Accept mot phan | Co `backend/migrations/versions/2fc7499a99b9_baseline_schema_as_shipped.py`; CI co `alembic upgrade head && alembic check`. Xem finding P1 ben duoi ve deployment path. |

## 3. Findings con mo sau khi doc code moi

| Severity | Finding | Evidence trong repo | PM decision / next step |
|---|---|---|---|
| P0 | `AIRBYTE_API` van chua duoc certify tren Airbyte self-managed that. | `compatibility.yaml:24-28` van `tested_platform_versions: []`; `backend/tests/test_adapter_contract.py` structural pass nhung live scenarios skip neu khong set `RUN_ENGINE_CONTRACT=1`; CI hien boot local embedded stack qua `.env.example`. | Phase 1 phai co Airbyte staging pinned version, `ENGINE_TYPE=AIRBYTE_API`, full adapter contract, e2e sync va log/status verification. Chua co gate nay thi chua noi production-ready. |
| P1 | Deployment migration path chua that su cat sang Alembic. | `docker-compose.yml:78` service `migrate` van chay `python -m app.bootstrap`; `backend/app/bootstrap.py:78-80` van `Base.metadata.create_all`; `backend/app/bootstrap.py:149` stamp thang `head`. | Production migrate command nen la `alembic upgrade head`. Bootstrap/create_all/stamp chi nen dung local hoac legacy adoption co check schema tuong duong. Neu sau nay co revision moi, stamp thang `head` co nguy co bo qua migration tren DB cu chua co `alembic_version`. |
| P1 | Production env con de boot sai engine qua default. | `docker-compose.yml:22` va `backend/app/core/config.py:41` default `AIRBYTE_EMBEDDED`; base compose lai khong co socket; `.env.example:47` tu dong include embedded overlay cho local. | Tao `.env.production.example` hoac deployment profile rieng: `APP_ENV=production` phai require `ENGINE_TYPE=AIRBYTE_API`, `AIRBYTE_API_URL`, `AIRBYTE_WORKSPACE_ID`; startup nen fail-fast thay vi chi log khi cau hinh sai. |
| P1 | `AIRBYTE_API` config validation chua fail-fast. | `backend/app/adapters/airbyte_api/adapter.py:70-78` co the khoi tao voi `base_url=""` va `workspace_ref=""`; `backend/app/main.py:41-48` chi check embedded thieu socket. | Them startup validation cho API mode: URL hop le, workspace id co gia tri, credentials/auth mode ro rang, health check dat thi moi coi engine ready. |
| P1 | Runtime egress policy cho connector thuc te van can Airbyte-worker/network policy. | Dev da ghi nhan redirect/dynamic URL runtime khong the preflight het; khi dung `AIRBYTE_API`, outbound traffic nam trong Airbyte worker deployment, khong phai Product API container. | Phase 1 phai thiet ke egress proxy/network policy tren Airbyte workers hoac infrastructure layer; Product preflight chi la UX/security guardrail ban dau. |
| P1 | Connector Builder tren `AIRBYTE_API` van chua co product decision. | Dev ghi `Connector Builder tren duong AIRBYTE_API: Chua thiet ke`. | Truoc production, hoac disable Builder publish/run khi `ENGINE_TYPE=AIRBYTE_API`, hoac lam lifecycle dang ky declarative source definition tren Airbyte staging. |
| P2 | `product_version` van la guarded duplicate. | `compatibility.yaml:10` va `backend/app/core/config.py:20` cung hard-code `1.0.0`. | Chap nhan tam trong Sprint 0R vi co test guard, nhung neu da goi "mot source" thi nen load tu compatibility hoac doi wording thanh "guarded duplicate". |
| P2 | Comment trong base compose con gay hieu nham. | `docker-compose.yml:7-10` van noi Worker chay connector qua host Docker daemon, trong khi socket da chuyen sang overlay local. | Sua comment de base compose duoc hieu la product stack khong co docker.sock; embedded la overlay local/demo. |
| P2 | Tai lieu co nhieu finding lich su da bi supersede nhung chua co current snapshot. | Dau file van co claim cu nhu "4 connector SUPPORTED" va `connector_registry.json` lech `product_version`; phan sau da ghi dev fix. | Them mot muc "Current status" o dau file hoac gan label `Historical` cho cac vong review cu, de stakeholder khong doc nham loi da dong thanh trang thai hien tai. |

## 4. PM release decision sau Sprint 0R

| Cau hoi | Tra loi PM |
|---|---|
| Dev co di dung huong khong? | Co, ro rang tot hon vong truoc. |
| Sprint 0R co the coi la hoan tat khong? | Gan hoan tat ve security/supply-chain, nhung Alembic deployment path can sua truoc production. |
| Co the mo production chua? | Chua. P0 `AIRBYTE_API` staging/certification van chua co. |
| Co the noi dung moi Airbyte image da pull khong? | Khong. Vẫn theo tier: 3 connector `SUPPORTED`, con lai `BETA` cho den khi certify. |
| Co can build `job-runner` ngay khong? | Khong neu production engine la `AIRBYTE_API` va embedded chi local/demo. Cach tach socket sang overlay la hop ly. |

## 5. Next step PM uu tien tiep theo

1. Doi production migration path: `migrate` service/delivery script dung `alembic upgrade head`; bootstrap local/legacy khong duoc stamp `head` neu chua verify schema.
2. Tao production env/profile rieng: khong ke thua `.env.example` local, khong auto include `docker-compose.embedded.yml`, require `AIRBYTE_API`.
3. Them startup readiness cho `AIRBYTE_API`: validate URL/workspace/auth + health check; loi cau hinh phai fail ro ngay luc boot.
4. Dung Airbyte self-managed staging pinned version va dien `tested_platform_versions` vao `compatibility.yaml`.
5. Them CI/nightly lane chay full adapter contract voi `ENGINE_TYPE=AIRBYTE_API`; ket qua nay moi la gate production.
6. Chot policy Connector Builder trong API mode: disable truoc hay implement Airbyte declarative source definition lifecycle.
7. Thiet ke egress control o Airbyte worker layer cho production, khong chi Product preflight.
8. Don tai lieu: them "Current status" o dau file, link toi PM review v2 la trang thai moi nhat, giu cac section cu nhu audit trail.

## 6. Verification PM vua chay

```text
python scripts/build-connector-lock.py --verify
-> PASS: connector lock OK (4 entries)

python -m pytest backend/tests/test_supply_chain.py -q
-> PASS: 34 passed

python -m pytest backend/tests/test_builder.py backend/tests/test_builder_surface.py backend/tests/test_supply_chain.py -q
-> PASS: 74 passed

python -m pytest backend/tests/test_adapter_contract.py -q
-> 4 passed, 12 skipped
```

Luu y quan trong: `test_adapter_contract.py` xanh o structural layer, nhung 12 live scenarios bi skip. Day la bang chung rang adapter interface dung, khong phai bang chung `AIRBYTE_API` production da chay duoc.


# Dev - Sprint 2 sau PM review v3 (2026-08-23)

## 1. Tam tat 8 next step PM giao

| # | PM giao | Trang thai | Bang chung |
|---|---|---|---|
| 1 | `airbyte-api-contract` thanh release gate co artifact | Xong | `scripts/release-gate.py` (record/check) + step upload artifact trong CI |
| 2 | Pin va certify Airbyte 1.x/2.x tren K8s | **Chua chay duoc** - khong co cluster. Da lam cho no do duoc thay vi doan | `scripts/verify-engine-api.py` do 24 endpoint that; `docs/RUNBOOK-engine-upgrade.md` |
| 3 | Readiness phan biet DB ready vs engine ready | Xong | `/readyz` vs `/readyz?deep=1`, do that khi tat Airbyte |
| 4 | Runbook `AIRBYTE_WORKSPACE_ID` | Xong | `scripts/airbyte-workspace.py` + `docs/RUNBOOK-airbyte-workspace.md` |
| 5 | Egress control tang worker + test lai | Xong (Compose) | `docker-compose.egress.yml`, `scripts/verify-egress.py`, e2e van pass khi chan Internet |
| 6 | Gon Docker/dev workflow | Xong | `scripts/stack.py` lite/embedded/airbyte/status/stop |
| 7 | Tach version evidence product vs engine | Xong | Cot `engine_version` + `/admin/compatibility` tra ca hai |
| 8 | Ops runbook backup/restore, secret rotation, on-call | Xong | 3 runbook + `scripts/backup.py`, `scripts/rotate-kek.py`, `/metrics` |

## 2. Ba quyet dinh PM nen doc ky vi toi lam khac de xuat

**Readiness (item 3).** PM de xuat "fail neu DB ready nhung Airbyte down".
Toi khong lam vay o `/readyz` mac dinh. Ly do: load balancer doc `/readyz`; neu
no fail khi engine down thi **toan bo API bi rut khoi rotation** trong luc engine
su co - luc do khong ai doc duoc lich su chay, khong thay duoc canh bao, khong
acknowledge duoc alert. Su co mot phan bien thanh su co toan bo.

Nen tach lam hai cau hoi:
- `/readyz` - LB hoi: process nay phuc vu duoc khong? DB bat buoc, engine **bao
  cao nhung khong bat buoc**.
- `/readyz?deep=1` - deploy gate hoi: ca chuoi phu thuoc co khoe khong? Tat ca
  bat buoc.

Do that (tat Airbyte roi goi): shallow tra **200** voi `engine.ok=false`, deep
tra **503**. Ai muon nghiem ngat thi `READINESS_REQUIRE_ENGINE=true`.

**Startup.** Loi cau hinh -> chet (khong tu sua duoc). Engine chua toi ->
**khong chet**, chi log; vi deployment moi thuong boot song song voi engine, va
chet o day tao crash loop ma nguyen nhan trong nhu loi san pham.
`STARTUP_REQUIRE_ENGINE=true` cho ai muon fail-fast.

**Egress proxy (item 5).** PM de xuat forward proxy. Toi **khong** lam duoc tren
ban Airbyte nay va da kiem chung bang jar chu khong doc doc: 0.59.1 khong co co
che `JOB_DEFAULT_ENV` de bom `HTTP_PROXY` vao container connector. Nen thu chan
duoc thuc su la network: `docker-compose.egress.yml` dat network `connectors`
thanh `internal`. Do that: Internet blocked, metadata endpoint blocked, product
API/Redis blocked, Postgres van reachable - **va e2e van pass 2.507 record**.
Allowlist theo tung host van la viec cua host firewall / NetworkPolicy, da viet
rule cu the trong `docs/RUNBOOK-egress.md` nhung chua chay.

## 3. Loi that tim ra trong vong nay

| Loi | Anh huong neu khong sua |
|---|---|
| `migrate`, `api`, `worker` build ra **3 image khac nhau** | `docker compose build api worker` de `migrate` o image cu -> deploy chay migration bang code cu va bao thanh cong. Trieu chung quan sat duoc: cot moi khong ton tai sau khi da rebuild. Da gop lam 1 image + test chan regression |
| `/admin/compatibility` bao `destination-postgres:3.0.17` trong khi Airbyte chay `2.0.10` | Operator doc sai hoan toan cai gi sap chay. Day chinh la P2 PM neu, nhung no la bug chu khong chi la thieu cot tai lieu |
| `ConnectorMetadata.version` echo lai version cua product | Nguyen nhan goc cua tren |
| Chua co KEK rotation | Envelope encryption ton tai chinh de rotate re; khong co no thi doi master key = nhap lai toan bo credential |
| `scripts/stack.py` dem ca container cua project khac | Footprint review ra ket luan sai (2.3GB cua `appbi-ai-backend` khong phai cua minh) |

## 4. Do duoc, khong phai uoc luong

**Docker footprint** (`python scripts/stack.py status`): 10 container cua project
nay, **~2.4 GiB**. Airbyte server + worker chiem ~1.7GB trong so do. `stack.py
stop` tra lai ~1.7GB ma van giu product chay duoc.

**Connector version product vs engine** - da ghi vao `compatibility.yaml`:

| Connector | Product lock | Airbyte thuc chay |
|---|---|---|
| source-postgres | 3.8.5 | **3.4.1** |
| destination-postgres | 3.0.17 | **2.0.10** |
| source-faker | 7.2.1 | **6.1.0** |
| source-declarative-manifest | 7.28.2 | 7.28.2 |

Chi cai cuoi khop, vi day la runner do **product** chon. Ba cai con lai do
deployment chon - nen mot ban nang cap Airbyte co the doi hanh vi connector ma
repo nay khong doi dong nao. Do la ly do release gate ghi lai cai gi **da chay**
chu khong phai cai gi **da lock**.

**API surface** (`scripts/verify-engine-api.py`): 24/24 endpoint adapter goi deu
ton tai tren 0.59.1. Danh sach doc tu chinh file adapter, khong phai list chep
tay - list chep tay se lech am tham.

## 5. Con lai truoc production

1. **Certify tren Airbyte that cua production (K8s 1.x/2.x).** Day la thu duy
   nhat con chan. Quy trinh da executable, xem `docs/RUNBOOK-engine-upgrade.md`.
2. Egress allowlist theo host - da co rule, chua chay tren ha tang that.
3. Kubernetes NetworkPolicy - viet tu yeu cau, chua chay.
4. On-call that: `/metrics` + alert rule da co, chua ai truc.

## 6. Verification vong nay

```text
python -m pytest tests -q
-> 164 passed, 12 skipped

npx tsc --noEmit            -> PASS
node scripts/check-i18n.mjs -> PASS (794/794)
python scripts/build-connector-lock.py --verify -> PASS

alembic upgrade head tu DB rong -> 2 revision, alembic check: "No new upgrade operations detected"

python scripts/e2e.py --source postgres --engine airbyte-api
-> OK, 2507 record, sync 2 doc 0 dong (cursor giu)

python scripts/verify-egress.py                          -> PASS
python scripts/verify-egress.py --expect-internet-blocked -> PASS (profile hardened)
python scripts/verify-engine-api.py                       -> 24/24
python scripts/release-gate.py record && check            -> PASS
```


# Dev - Sprint 3 sau PM review v4 (2026-08-23)

## 1. P1 release gate - da sua, va PM dung

PM chi ra dung cho fail-open. Ba loi rieng biet:

1. `REQUIRED_OPERATIONS` hardcode 9 operation, `compatibility.yaml` claim 11.
   Hai list roi nhau tu luc nao khong ai biet - thieu ca hai Builder operation.
2. `--verified` default = toan bo required operations. Nghia la artifact tu
   assert bang chung cho chinh no.
3. Khong co duong nao de artifact nhan bang chung that tu cac lan chay.

Cach sua:

- **Bo list thu hai.** Gate doc `compatibility.yaml` de biet phai chung minh
  gi. Hai list khong the lech duoc nua. Huong dung: claim hep lai thi phai
  chung minh it lai, va viec claim hep lai nhin thay duoc khi review file do.
- **`--evidence` bat buoc.** `record` khong chay neu khong co file bang chung.
- **Verifier ghi bang chung.** `scripts/e2e.py --evidence out.json` ghi lai
  chinh xac nhung gi no da chay, ngay tai cho no chay xong.
- **e2e mo rong** de phu du 11/11: them cancel va Builder test/publish - ba
  operation truoc day khong co lan chay nao cham toi.

Do that:

```text
release-gate.py record            -> error: --evidence is required
e2e.py --evidence evidence.json   -> proved 11 operations
release-gate.py record --evidence -> ghi 11/11 tu file
release-gate.py check             -> BLOCKED: no commit recorded
```

Cancel co xu ly race that: neu run ket thuc truoc khi cancel toi noi, script
**khong** tinh la proved va noi ro. Gate se chan release thay vi nhan mot "co
le".

## 2. P2 khac PM neu

| PM finding | Da lam |
|---|---|
| `verify-engine-api.py` khong reproduce duoc tu host | Them `--in-network`: script tu chay lai chinh no trong container tren network `appbi`. PM chay `python scripts/verify-engine-api.py --in-network` -> 24/24. Bo default `localhost:8001` vi no tao ra "connection refused" khien nguoi doc tuong script hong |
| `/metrics` khong auth + API port public | Postgres, API va frontend deu bind `127.0.0.1`. Chi con nginx public. Co test chan regression, va test thu hai xac nhan nginx khong proxy `/metrics` |
| Airbyte backup la external dependency | Them muc Airbyte state + RPO/RTO vao `docs/RUNBOOK-backup-restore.md`, kem canh bao: restore mot ben ma khong restore ben kia tao mismatch **im lang** trong `engine_mappings`. Lenh dump da chay that (264KB) - lenh dau tien toi viet dung role `airbyte` va **that bai**, role do khong ton tai trong stack nay |
| Egress hardened chi prove internal-DB | Dong y, da ghi dung nhu vay. Allowlist theo host van chua chay tren ha tang that |
| K8s certification | Van la blocker. Khong co cluster |

## 3. Scale ngoai Airbyte - da chung minh bang code

Yeu cau: dam bao sau nay chay duoc engine khac ngoai Airbyte.

Van de: `IntegrationEngineAdapter` co 2 implementation nhung **ca hai deu la
Airbyte** - cung protocol, cung catalog shape, cung job model. Mot interface chi
co mot ho implementation phia sau thi chua duoc kiem chung la interface.

Nen viet **adapter thu ba khong lien quan gi toi Airbyte**:
`backend/app/adapters/sql_direct/` - Postgres sang Postgres bang SQL thuan.
Khong connector image, khong Airbyte Protocol, khong connection object phia
server, khong job service.

**Ket qua: interface khong phai doi gi.** 22/22 operation, khong them method,
khong doi signature.

Chay that:

```text
health      : HEALTHY sql-direct/1
discover    : 3 streams, pk=[['id']] [['id']] [['sku']]
sync 1      : SUCCEEDED 2007 records
sync 2      : SUCCEEDED 0 records      <- incremental state giu duoc
```

**Ba cho Airbyte da ro ri len tren boundary, phat hien nho bai tap nay:**

| Ro ri | Sua |
|---|---|
| `services/schema_service.py` import thang `adapters.airbyte_protocol` | Ham do chi hash mot JSON schema, khong biet gi ve engine. Chuyen thanh `DiscoveredStream.schema_hash`. Gio khong layer nao ngoai `adapters/` import engine |
| `services/catalog.py` chi hieu `airbyte_secret` | Hieu them `writeOnly`, `secret`, `format: password`. Spec khong phai Airbyte gio duoc ma hoa password thay vi luu plain |
| `services/builder.py` hardcode image `airbyte/source-declarative-manifest` | Adapter khai bao qua `declarative_runner()`. Engine khong chay duoc manifest tra `None` va publish fail co ly do |

Co test chan: bat ky file nao ngoai `adapters/` import engine implementation se
lam fail suite.

**Gioi han thanh that:** Connector Builder **khong** port duoc. No compile ra
Airbyte low-code CDK manifest, khong co format trung lap de compile ra.
`sql_direct` tra `None` va tu choi, thay vi gia vo. Noi ro con hon mot
abstraction gia vo roi fail sau.

**Bug tim duoc them:** `information_schema.table_constraints` tra **0 row** cho
user chi co SELECT - view do chi hien constraint cho *owner*. Ma least-privilege
reader chinh la account ma source nen dung. Hau qua: discover bao khong co
primary key, san pham khong cho dedup, khong co loi o dau ca. Do that voi
`demo_reader`: information_schema 0 row, `pg_catalog` 3 row. Airbyte
`source-postgres` cung dung pg_catalog - va do la ly do hai ban discover gio
khop nhau.

Chi tiet + 4 cho interface "chat" (connector spec, `check`, connection object,
job identity): `docs/ENGINE-PORTABILITY.md`.

## 4. Verification vong nay

```text
python -m pytest tests -q       -> 169 passed, 12 skipped
npx tsc --noEmit                -> PASS
node scripts/check-i18n.mjs src -> PASS 794/794
build-connector-lock --verify   -> PASS

e2e.py --engine airbyte-api --evidence  -> 11/11 operation proved
release-gate.py record (khong evidence) -> refused
release-gate.py record --evidence       -> 11/11
verify-egress.py                        -> PASS
verify-engine-api.py --in-network       -> 24/24
sql_direct live                         -> 2007 records, incremental giu
pg_dump airbyte                         -> 264KB
```


# Dev - Sprint 4: dong not phan con lai (2026-08-23)

## 1. Restore drill - da chay that, khong con la giay to

PM P2: "chay restore drill gom ca Product DB + Airbyte state, va ghi RPO/RTO.
Khong chi co runbook tren giay." Da chay:

| | |
|---|---|
| Dump doi (product + airbyte, cung mot cua so) | 350KB + 268KB |
| Restore vao scratch DB, `ON_ERROR_STOP=on` | khong loi |
| Row counts truoc/sau | 11 pipeline, 58 run, 26 source, 21 secret, 47 engine mapping - **giong het** |
| **Giai ma credential tu ban restore** | **21/21** |

Dong cuoi moi la dong quan trong. Row count chi chung minh SQL chay lai duoc;
giai ma duoc toan bo credential moi chung minh dump va key thuoc ve nhau - dung
cai that bai ma ca runbook nay ton tai de ngan.

**Chua drill:** restore sang mot Airbyte **khac**. Do la cho engine reference
thuc su vo, va can Airbyte thu hai. Ghi la con mo.

RPO 24h / RTO 2h da ghi vao `docs/RUNBOOK-backup-restore.md` kem cach reproduce.

## 2. Kubernetes - truoc gio khong co manifest nao ca

Day la lo hong lon nhat con lai va khong ai neu ra: production nham vao
Kubernetes nhung repo **khong co mot manifest nao** cho san pham. Runbook noi
"deploy bang Helm/abctl" nhung do la cho Airbyte; ban than san pham khong co gi.

`deploy/kubernetes/` - Kustomize thuan (khong Helm: san pham nay co 5 object va
2 thu thay doi giua cac moi truong, mot chart chi them ngon ngu template de che
dieu do).

| Co | Khong co, va co y |
|---|---|
| API deployment + service, worker, migrate job, frontend, 4 NetworkPolicy, PDB, ingress, RBAC | Postgres, Redis, Airbyte |

Postgres/Redis nen la managed service - chay database bang `Deployment` +
`emptyDir` la thu trong on cho toi luc node reschedule. Airbyte co chart va
lifecycle rieng; san pham chi can goi duoc API cua no.

Nhung quyet dinh dang doc:

- **readiness tro toi `/readyz`, khong phai `?deep=1`.** Co test chan. Neu tro
  vao deep, Airbyte down se rut sach pod khoi Service.
- **initContainer cho migrate job** - Kubernetes khong co `dependsOn`, va API
  khoi dong tren schema nua chung sinh ra loi trong nhu bug ung dung.
- **NetworkPolicy deny-by-default**, roi mo tung duong: DNS, Airbyte API,
  Postgres/Redis subnet. Khong co rule nao cho outbound chung - san pham khong
  goi Internet; connector co goi, va connector khong chay o day.
- **Khong pod nao co runtime socket hay chay root**, readOnlyRootFilesystem
  toan bo. Co test chan ca ba.
- **Worker `replicas: 1`, `Recreate`**, grace 120s de sync dang chay ket thuc.

Validate that: `kubeconform -strict -kubernetes-version 1.30.0` -> **16/16
valid**. `kubectl kustomize` render sach. Da them vao CI.

**Van chua apply len cluster that.** Do la blocker con lai, khong doi.

## 3. On-call - alert rules thanh file

Truoc do rules chi nam trong prose cua runbook. Gio la
`deploy/monitoring/alerts.yaml`, apply duoc.

Sua mot loi khi viet: `AppBIFailureWave` dung `increase()` tren
`appbi_runs_total`, ma metric do la **gauge** (row count doc luc scrape, RUNNING
len xuong). `increase()` danh cho counter. Doi sang delta tuong minh voi
`offset 1h`.

Them hai test:
- Alert rule khong duoc tham chieu metric ma app khong emit. Mot alert tren
  metric khong ton tai thi khong bao gio fire va cung khong bao gi ca - te hon
  la khong co alert, vi dashboard bao la da cover.
- Moi rule phai co `for:`. `appbi_engine_reachable` tut ve 0 moi lan Airbyte
  restart; page luc deploy la cach day nguoi ta bo qua pager.

## 4. Trang thai cuoi

```text
169 -> 175 test
Kubernetes manifest: 16/16 valid, 0 invalid
Restore drill: 21/21 credential giai ma duoc
```

Con lai truoc production - va chi con dung mot nhom:

1. **Chay that tren Airbyte K8s 1.x/2.x.** Endpoint probe, contract live, e2e,
   NetworkPolicy, release artifact. Quy trinh executable, thieu cluster.
2. Egress allowlist theo host - can firewall/gateway that.
3. Restore sang Airbyte khac - can Airbyte thu hai.
4. On-call that - co metric va rule, chua ai truc.

Ba muc sau deu can ha tang chu khong can code.


# Dev - Sprint 5: manifest da chay that tren cluster (2026-08-23)

Sprint 4 giao manifest Kubernetes nhung ghi "chua apply len cluster that". Toi
da khong dung o do: `kind` chay duoc bang Docker co san, nen da dung mot cluster
Kubernetes 1.30 that, deploy Postgres + Redis ben canh, load image san pham vao
va apply.

## Ket qua

```
appbi-migrate   Completed   0 restart   chay ca 2 migration tu DB rong
appbi-api       2/2 Running 0 restart   readiness pass
appbi-worker    1/1 Running 0 restart
```

Endpoint, goi tu trong pod:

| | |
|---|---|
| `/healthz` | 200 |
| `/readyz` | **200** |
| `/readyz?deep=1` | **503** |
| `/metrics` | `appbi_metrics_up 1.0` |

Hai dong giua la **bang chung song** cho quyet dinh ma PM ban dau de xuat khac:
cluster do khong co Airbyte, engine unreachable, va **ca hai pod API van o trong
Service**. Neu readiness tro vao `?deep=1` thi ca hai da bi rut ra, va khong ai
doc duoc lich su chay hay acknowledge alert. Gio khong con la lap luan nua.

## Hai loi ma schema validation khong the bat

`kubeconform` bao 16/16 valid. Cluster that bao khac:

| Loi | Trieu chung | Sua |
|---|---|---|
| initContainer dung `bitnami/kubectl:1.30` - **tag khong ton tai** (Bitnami da bo) | Ca 2 pod API `ImagePullBackOff` vo han | Doi sang wait dung **image cua chinh san pham**, hoi thang database xem `alembic_version` da co chua. Bo luon ServiceAccount + Role + RoleBinding ma no can |
| `imagePullPolicy` khong set | Default suy ra tu ten tag (`Always` cho `:latest`) - hanh vi doi tham lang theo cach tag | Set tuong minh `IfNotPresent` |

Loi thu nhat dang chu y: giai phap ban dau can mot image ben ngoai + 3 object
RBAC de mot container **theo doi mot Job**. Hoi database xem migration xong chua
thi khong can gi ca, va no kiem tra dung thu minh quan tam thay vi mot proxy cua
no. Ban dau toi viet phuc tap hon can thiet; cluster that chi ra dieu do.

Loi thu ba tim duoc: worker **khong co** init container nen crash-loop 3 lan cho
migration. Da them wait giong API - sau do 0 restart.

## Test chan regression

- Moi container (ke ca init) phai chay image ma **project nay build**.
- `imagePullPolicy` phai tuong minh.
- ConfigMap chi duoc dat setting ma app that su doc.
- readiness khong bao gio duoc la `?deep=1`.
- Khong pod nao co runtime socket hay root.
- NetworkPolicy phai deny-by-default.

## Con lai

Van la **certify tren Airbyte K8s that**. Lan chay nay chung minh manifest dung,
khong chung minh integration da duoc certify tren nen tang do - cluster kind
khong co Airbyte. Do la viec can ha tang, khong can code.

```text
175 -> 177 test
kubeconform: 13/13 valid (giam tu 16 vi da bo 3 object RBAC)
cluster that: migrate Completed, api 2/2, worker 1/1, 0 restart
```


# Dev - Sprint 6 sau PM review v5 (2026-08-23)

## 1. P1 kube-dns selector - PM bat dung, va no nghiem trong hon ve ngoai

PM chay `kubectl kustomize` va thay `commonLabels` tiem
`app.kubernetes.io/part-of: appbi-integration` vao **podSelector cua kube-dns**.
Xac nhan:

```
truoc: {'app.kubernetes.io/part-of': 'appbi-integration', 'k8s-app': 'kube-dns'}
sau  : {'k8s-app': 'kube-dns'}
```

Diem dang xau ho: chinh toi viet comment tren rule do rang "khong co DNS thi moi
rule khac se fail theo kieu trong nhu destination chet chu khong phai policy" —
roi tu tao ra dung tinh huong ay.

`commonLabels` cua Kustomize sua **ca selector**, khong chi metadata. Doi sang
`labels: [{includeSelectors: false}]`. Pod selector cua cac Deployment von da
day du nen khong mat gi.

**Nguyen nhan goc cua viec test khong bat duoc** (PM P3): `_k8s_documents()` doc
file YAML nguon, ma bug chi ton tai sau khi render. Da them 4 test chay
`kubectl kustomize` va assert tren **output**:
- khong label san pham nao duoc tiem vao selector tro toi pod cua nguoi khac
- rule DNS phai con dung `{k8s-app: kube-dns}`
- image phai rewrite ve registry, khong duoc `:latest`
- moi object phai co namespace

## 2. Chay that tren CNI co enforce (PM priority #2)

kind mac dinh dung kindnet: **nhan NetworkPolicy nhung khong enforce**. Nen mot
lan apply xanh tren do khong chung minh gi ve policy. Da dung cluster thu hai
voi **Calico**.

Tu pod gan nhan `app.kubernetes.io/name=appbi-api`:

| | |
|---|---|
| DNS | **works** |
| DB trong CIDR duoc phep | **reachable** |
| Internet | blocked |
| Cloud metadata | blocked |
| Redis khong nam trong rule | blocked |

Dong "DB reachable" quan trong khong kem dong "internet blocked": neu chi thay
moi thu bi chan thi khong phan biet duoc "policy dung" voi "Calico dang drop
tat ca". Phai chung minh ca duong cho phep.

**Mot phat hien khi lam:** `appbi-default-deny` select **moi pod trong
namespace**, nen Postgres chay *ben trong* `appbi` bi chan ingress va khong voi
toi duoc du dia chi da nam trong CIDR cho phep. Dung voi hinh dang production
(Postgres/Redis la managed service ngoai cluster), nhung se lam nguoi khac mat
thoi gian. Da ghi vao `docs/RUNBOOK-egress.md`, va lan verify that da dat DB o
namespace khac cho dung hinh dang production.

## 3. Cac finding con lai cua PM

| PM | Da lam |
|---|---|
| P2 CIDR placeholder | Tach `base/` va `overlays/production/`. Base giu CIDR sai co y (`10.0.0.0/24`) + khong co registry, nen apply nham thi fail closed. Test tu choi placeholder loi vao overlay da render, va tu choi prefix rong hon /16 |
| P2 runbook `--evidence` | Sua `RUNBOOK-engine-upgrade.md` va `CURRENT_STATUS.md`. Da grep: khong con lenh `record` nao thieu `--evidence` |
| P3 initContainers securityContext | Guard mo rong sang `initContainers + containers` |
| P3 test render-time | 4 test moi tren rendered output (muc 1) |
| P3 frontend env | `NEXT_PUBLIC_API_BASE` khong duoc doc o dau ca - da bo. Ingress route `/api` sang API Service truoc khi cham pod frontend, nen frontend khong can egress toi API. Da ghi ly do trong manifest |

## 4. Ve dinh huong san pham (muc 5 cua PM)

Dong y toan bo, va khong lam gi di nguoc:

- Khong gom Airbyte vao product image. `stack.py lite` van la duong mac dinh
  cho dev/UI; `stack.py airbyte` chi khi can cert.
- Production: Airbyte la Helm release/namespace rieng. `deploy/kubernetes/` chi
  co API, worker, frontend, ingress, policy - **khong** co Postgres, Redis hay
  Airbyte, va README noi ro vi sao.
- Ve white-label: neu doi ten `airbyte-*` thanh `engine-*` thi do la packaging.
  Toi khong doi, va dong y voi PM rang **ops/release evidence phai trung thuc**:
  `compatibility.yaml` ghi ro engine la Airbyte, phien ban nao, connector version
  nao that su chay. Doi ten trong tai lieu ky thuat se lam nguoi debug mat
  phuong huong va lam upgrade path bien mat.
- Adapter thu ba (`sql_direct`, Sprint 3) da chung minh boundary that su la
  boundary - neu sau nay muon engine khac thi khong phai viet lai san pham.

## 5. Con lai truoc GO

1. **Certify Airbyte tren K8s 1.x/2.x that.** Blocker duy nhat con lai can
   ha tang. Quy trinh da executable, lenh da copy-paste duoc.
2. Connector egress trong namespace cua Airbyte - can Airbyte tren K8s truoc.
3. Restore sang Airbyte thu hai - can Airbyte thu hai.
4. Gan on-call owner/escalation - la process, khong phai code.

## 6. Verification

```text
python -m pytest tests -q                      -> 183 passed, 12 skipped
kubectl kustomize base / overlays/production   -> ca hai render sach
kubeconform -strict (base + overlay)           -> valid
kind + Calico, pod dan nhan appbi-api          -> DNS ok, DB allow ok, internet blocked
kind 1.30 (cluster truoc)                      -> migrate Completed, api 2/2, worker 1/1, 0 restart
```

---

# Dev — Sprint 7 sau PM review v6 (2026-08-23)

## 0. Cau hoi kien truc: Postgres dung chung hay tach?

Cau tra loi: **tach — hai database, va tren production la hai instance.**
Viet day du trong [docs/ADR-001-database-topology.md](docs/ADR-001-database-topology.md).

Ly do quan trong nhat khong phai disk hay noisy neighbour, ma la dinh huong san
pham — dung y ma anh nhac lai: sau nay app se scale ra ngoai nhung gi Airbyte co.

Schema cua product **khong duoc phep tro thanh ban sao schema cua Airbyte**.
Hien tai no khong phai: engine identity chi ton tai o dung mot bang,
`engine_mappings`. Khong bang nao khac biet engine ton tai. Do la ly do adapter
thu ba (`sql_direct` — khong Airbyte Protocol, khong connector image, khong job
object phia server) cam vao duoc ma khong sua mot dong nao cua interface.

Dung chung database se lang le pha vo dieu do. Khi bang cua product nam canh bang
cua Airbyte, join qua lai tro nen **kha thi**, roi **tien**, roi
**load-bearing** — va ranh gioi khien engine thay duoc bien mat. Khong phai qua
mot quyet dinh nao ai do bao ve trong review, ma qua mot chuoi query rieng le
deu hop ly.

Nen tach khong phai de tiet kiem I/O. No la thu giu cho "Airbyte la engine hien
tai" dung, thay vi "Airbyte la kien truc".

### Da do, khong phai suy doan

Truoc khi co ADR nay, role cua product doc duoc **ca 47 bang** cua Airbyte tren
staging. Khong co gi phat hien ra neu mot service bat dau lam the.

Bay gio enforce o hai lop:

| Lop | Lam gi |
|---|---|
| Ung dung | `check_database_separation()` — refuse to start neu `DATABASE_URL` tro vao database co schema Airbyte. Fatal o **moi** environment, khong chi production |
| Postgres | role `appbi_product` khong co `CONNECT` tren database `airbyte` |

Do that: truoc — doc het 47 bang; sau — `psql -U appbi_product -d airbyte` tra ve
*"User does not have CONNECT privilege"*, con `reads pipelines: 11` van chay.

`REVOKE CONNECT ... FROM PUBLIC` la dong lam viec that. Postgres grant `CONNECT`
cho `PUBLIC` mac dinh, nen revoke rieng role thi khong doi gi ca.

## 1. P0 — Airbyte tren Kubernetes: DA CERTIFY

Airbyte **1.8.5**, Helm chart chinh thuc, kind Kubernetes **1.30.4**. Product
chay Compose, tro vao engine trong cluster. Connector chay **that** duoi dang pod
trong namespace `airbyte`, do workload launcher tao.

```
=== run detail ===
status      : SUCCEEDED     duration: 156.8 s
records     : 2507          bytes   : 429904
stream stats: [('customers', 500), ('orders', 2007)]

=== second sync — does the cursor hold? ===
  customers: 500 first, 0 second
  orders:   2007 first, 0 second

=== cancel ===  CANCEL_REQUESTED -> CANCELLED
=== builder === test ok=True | published custom-jsonplaceholder-posts rev 6
```

Row count khop tuyet doi voi source: 500 + 2007 = 2507.

### Ba thu chi tim ra bang cach chay that

**1. `/api/v1/workspaces/list` tra ve 404 tren 1.8.5.** Co tren 0.59.1, mat tren
1.8.5. Thay bang `list_by_organization_id` (can `organizationId`; community
edition dung UUID toan so 0) va `list_paginated` (can block `pagination` tuong
minh — thieu la 500, khong phai default). Adapter gio thu ca ba theo thu tu va
khai bao `ALTERNATIVE_ROUTE_GROUPS` de `verify-engine-api.py` khong bao 404 tren
mot thanh vien la adapter hong.

Day la toan bo ly do ton tai cua endpoint probing. Mat vai phut de tim; neu khong
se la mot production incident ngay lan resolve workspace dau tien.

**2. Job logs tren 1.8.5 hoan toan trong.** `logLines` van co nhung **rong**; log
that nam trong `events` — 562 object co cau truc. Adapter chi doc `logLines` nen
bao cao "khong co log". Day khong bao gio la error o dau ca: no la mot job khong
co log — dung luc mot nguoi dang debug sync loi va mo log view ra thay trang. Gio
doc ca hai shape, khong switch theo `version`.

Sau khi sua: 285 dong render qua product API, co timestamp/level/source.

**3. Cluster lanh timeout va trong giong nhu engine hong.** Lan chay dau that bai
voi `ENGINE_UNAVAILABLE / ReadTimeout` ngay o `check` dau tien. Engine hoan toan
khoe; **pod** connector dang keo 500MB image. `pull-engine-images.py` gio co
`--into-kind`.

Mot thu nua dang ghi lai: **`kubectl port-forward` khong phai la network.** Do
qua no thi mot nua endpoint bao >15s. Cung endpoint do tra loi trong **10ms** qua
duong that. port-forward mang mot connection va serialise sau no — do qua no la
do cai tunnel. Cach dung: `docker network connect` node vao network cua product,
dung NodePort.

### Connector versions 1.8.5 pin — lai khac lan nua

| | bundled | 0.59.1 chay | 1.8.5 chay |
|---|---|---|---|
| source-postgres | 3.8.5 | 3.4.1 | **3.6.35** |
| destination-postgres | 3.0.17 | 2.0.10 | **2.4.5** |
| source-faker | 7.2.1 | 6.1.0 | **6.2.24** |
| source-declarative-manifest | 7.28.2 | 7.28.2 | **7.28.2** |

Declarative runner khop o ca hai vi do la lua chon cua product, khong phai cua
engine.

### Lam lai duoc, khong phai chay tay mot lan

CI lane `airbyte-k8s-contract` (`.github/workflows/ci.yml`) dung nguyen chuoi
tren: kind -> Helm -> NodePort -> network connect -> pre-pull -> probe -> e2e ->
release gate -> upload artifact. Quy trinh tay o
[docs/RUNBOOK-engine-upgrade.md](docs/RUNBOOK-engine-upgrade.md).

## 2. P2 — Restore sang deployment thu hai: DA DRILL

Khong can dung san Airbyte thu hai — co san hai cai cung luc: 0.59.1 Compose
(database cua product duoc viet ra tu day) va 1.8.5 K8s. Tro product sang cai thu
hai ma khong dong vao database cua no **chinh la** kich ban restore.

```
30 of 30 resources are not on this engine
   (17 more belong to another engine implementation and were not checked)
  MISSING  SOURCE       AB Postgres Source
  MISSING  DESTINATION  AB Postgres Warehouse
  MISSING  PIPELINE     AB Shop Sync
```

Luu o `evidence/reconcile-cross-deployment.json`.

Truoc drill nay khong co cach nao biet duoc dieu do ngoai viec chay sync roi doi
no loi vai tieng sau. Gio co `resource_exists` tren adapter contract (ca ba
implementation), service `reconcile`, endpoint `GET /api/v1/engine/reconcile` va
`scripts/reconcile.py`.

Ba dieu drill day ra, khong cai nao hien nhien truoc do:

- **Product khong lam hong gi trong trang thai nay.** Doc database cua chinh no,
  hoi engine, bao cao. `/readyz?deep=1` van xanh — engine **that su** khoe, chi la
  khong co nhung resource do.
- **"Missing" va "thuoc engine khac" la hai cau tra loi khac nhau.** Ban dau
  report gop 17 dong cua adapter embedded vao danh sach missing va bao nguoi van
  hanh tao lai. Chung hoan toan binh thuong.
- **Engine khong tra loi thi khong bao cao gi ca.** Neu 5xx tinh la "absent", mot
  lan restart engine se bao mat sach moi resource — va hanh dong tiep theo tu bao
  cao do la pha huy.

## 3. P2 — CIDR/registry/tag: gio release gate chan

`release-gate.py record --overlay` render overlay va tu choi release neu con
placeholder cua repo. Chay tren repo hien tai:

```
'registry.internal/' (the example registry) is still in the rendered manifests
'appbi.example.internal' (the example ingress host) is still in the rendered manifests
```

Dung nhu mong doi: repo ship mot template, va gate tu choi certify template la
production. CIDR `10.0.0.0/24` **khong** bi bao — vi overlay da patch thanh
`10.42.7.0/28`. Nghia la check khong phai match bua.

## 4. P2 — On-call owner: gio la mot gate, khong phai mot y dinh

`docs/RUNBOOK-oncall.md` co bang ownership (primary, escalation 15/45 phut,
paging channel, gio hanh chinh), bang phan loai alert nao page / nao khong, va
chinh sach silence (luon co expiry; silence `AppBIMetricsCollectionFailing` la
outage chu khong phai mute).

Day la quyet dinh cua to chuc chu khong phai code, nen no duoc **check** thay vi
duoc doan: `release-gate.py check` fail khi con `TO BE ASSIGNED`. Hien tai:
`5 on-call role(s) still marked TO BE ASSIGNED`.

## 5. P1 — Connector egress trong namespace cua Airbyte

`deploy/kubernetes/airbyte/connector-networkpolicy.yaml` — de rieng khoi `base/`
va `overlays/` co chu y: day khong phai workload cua product, va nguoi van hanh
Airbyte release moi la nguoi apply.

Da apply len cluster that. Kiem tra selector:

```
airbyte-connector-egress  airbyte=job-pod  -> khop 9 connector pod
                                           -> khop 0 control-plane pod
```

Do la kiem tra dang gia nhat o day: mot NetworkPolicy khong khop pod nao la kieu
that bai im lang kinh dien, va mot policy khop nham server/worker se lam chet
engine chu khong phai chan connector.

Rule quan trong ma nguoi ta hay quen: connector pod **phai** goi duoc workload
API. Thieu rule do thi job khong fail — no treo den khi attempt timeout.

### Do duoi CNI co enforce, va co control

Cluster cua Airbyte dung kindnet — nhan NetworkPolicy va **khong** enforce. Nen
lan apply o tren chi chung minh selector. Cluster thu hai voi **Calico** chung
minh cac rule. Hai pod cung namespace, cung image, chi khac cai label:

| | pod co `airbyte=job-pod` | control, khong label |
|---|---|---|
| DNS | resolves | resolves |
| Workload API | reachable | reachable |
| Internet `:443` | reachable | reachable |
| Private `10.0.0.0/8` (kube API) | **blocked** | **reachable** |
| Cloud metadata | blocked | blocked |

Dong duy nhat chung minh duoc dieu gi la `10.0.0.0/8`: reachable tu pod khong
label, blocked tu connector pod. Nghia la **policy** dang chan chu khong phai
topology cua cluster — va carve-out RFC1918 ben trong rule `0.0.0.0/0` hoat dong:
connector ra duoc internet ma khong vi the ma vao duoc phan con lai cua mang noi
bo.

**Dong metadata o day khong chung minh gi ca, va can noi ro dieu do.** Ca hai pod
deu blocked vi kind khong route link-local — dung cai da lam phep do tren Docker
khong ket luan duoc. Tren node cloud that thi `169.254.169.254` **co** route, va
do moi la cho carve-out co gia tri. Ghi la "rule co va dung", khong phai "da do".

## 6. Reconcile duoc kiem ca hai chieu

Mot checker chi biet noi "missing" cung se pass drill o muc 2. Nen do ca chieu
duong: sau khi e2e tren K8s tao resource that tren engine 1.8.5, cung mot lenh
tra ve:

```
checked: 36 | present: 6 | missing: 30 | foreign: 17
```

6 present = 2 lan chay e2e tren K8s x (source + destination + pipeline).
30 missing = resource cua 0.59.1. 17 foreign = adapter embedded.

Roi tro nguoc lai 0.59.1, khong doi gi ngoai URL:

| Tro vao | present | missing | thuoc engine khac |
|---|---|---|---|
| Airbyte 1.8.5 (Kubernetes) | 6 | 30 | 17 |
| Airbyte 0.59.1 (Compose) | 30 | 6 | 17 |

Con so **doi cho chinh xac**. Dieu do chi xay ra duoc neu tool doc engine that
chu khong phai doan. Doi chieu truc tiep tung ref: cai bao present tra `200`,
cai bao missing tra `404 Could not find configuration for SOURCE_CONNECTION`.

Va khong regression tren 0.59.1: `verify-engine-api` van 26/26, job logs van ra
422 dong qua duong `logLines` cu, `/readyz?deep=1` xanh.

## 7. Verification vong nay

```text
backend: python -m pytest tests -q          -> 194 passed, 12 skipped
frontend: npm run typecheck                 -> PASS
i18n: node scripts/check-i18n.mjs src       -> 794 vi / 794 en, OK
connector lock: build-connector-lock --verify -> OK (4 entries)
kubectl kustomize base / overlays/production  -> ca hai render sach

Airbyte 1.8.5 tren Kubernetes 1.30.4 (Helm chart chinh thuc):
  verify-engine-api (in-network)            -> 26/26 present, 1 covered by alternative
  e2e --engine airbyte-api                  -> OK, 2507 records
  evidence-e2e-k8s.json                     -> 11/11 operations
  release-gate record                       -> engine AIRBYTE_API 1.8.5, 50 runs
  release-gate check                        -> BLOCKED (dung y do, xem duoi)

reconcile (cross-deployment)                -> 30 missing / 6 present / 17 foreign
```

`release-gate check` **co chan**, va do la ket qua dung:

```
RELEASE BLOCKED:
  - no commit recorded; the certified code is not identifiable
  - 5 on-call role(s) still marked TO BE ASSIGNED
  - the manifests still carry repository placeholders:
      'registry.internal/', 'appbi.example.internal'
```

Ba dong nay khong phai bug. Engine integration da certify — gate cho qua phan
do. Cai chua san sang la **gia tri trien khai**: repo dang ship mot template,
chua ai gan on-call, va day khong phai git repo nen khong co commit.

Nghia la ranh gioi cuoi cung khong con la "code chua chung minh duoc" ma la
"deployment chua duoc cau hinh" — va gate se tu tat khi ba thu do duoc dien.

## 8. Con lai truoc GO

| | Ai lam |
|---|---|
| Dien ownership vao `docs/RUNBOOK-oncall.md` | to chuc van hanh |
| Thay CIDR / registry / ingress host trong `overlays/production` | team ha tang |
| Apply `deploy/kubernetes/airbyte/connector-networkpolicy.yaml` voi CIDR that | nguoi van hanh Airbyte |
| Chay `release-gate check` sau khi xong ba muc tren | release manager |

Khong con muc nao trong danh sach nay la code.

---

# PM review v8 sau Sprint 7 - final production audit - 2026-08-24

## 1. Ket luan PM

PM da doi chieu report cua dev voi code, artifact, manifest, CI, runbook va BA.
Ket luan:

```text
Adapter AIRBYTE_API + Airbyte 1.8.5 K8s:  ACCEPTED trong pham vi da test
Reconcile engine mappings:                 ACCEPTED ve thiet ke
Commercial production release:            NO-GO
Ly do: legal + release integrity + production topology + bootstrap con mo
```

Bao cao dev dung khi noi engine integration da chay that. Bao cao khong dung khi
ket luan "khong con code": it nhat release gate, production entrypoint va
connector policy packaging van can code. Ngoai ra `LIC-001` dang `NOT_CLEARED`,
nen hien tai repo tu khai bao chi dung cho internal/PoC.

## 2. Finding theo muc do uu tien

| Severity | Finding | Evidence PM kiem tra | Dev/owner phai lam |
|---|---|---|---|
| P0 / Release blocker | Legal/license gate chua duoc clear va release gate bo qua no. | `compatibility.yaml` ghi `LIC-001: NOT_CLEARED`, note "internal and PoC use only"; `README.md` lap lai gioi han. `release-gate.py` chi doc operation certification, khong doc `release_gates`. | Legal/owner chot delivery model va license. Dev them check tat ca gate bat buoc trong `compatibility.yaml`; bat ky `NOT_CLEARED/FAILING` nao phai block commercial release. Khong tu doi status khi chua co owner chap thuan. |
| P0 / Release blocker | Evidence E2E khong rang buoc voi dung build/deployment dang release. | `e2e.py` chi ghi `produced_by`, `engine` va boolean operations. `release-gate.py` khong validate `engine` trong evidence, khong co evidence timestamp/run ids/workspace/deployment id; commit lay tu checkout local, con product/runs lay tu API khac. Latest 10 runs cung khong duoc link voi E2E. | Lam evidence schema v2 va bind mot cach fail-closed: build SHA do product endpoint tra ve, engine type/version, workspace fingerprint, deployment id, start/end UTC, run/job ids, connector images/digests va result tung operation. `record` chi pass neu evidence moi, cung build, cung engine/workspace va run ids ton tai tren deployment. |
| P0 / Release blocker | One-command production entrypoint da duoc yeu cau nhung chua ton tai. | `CURRENT_STATUS.md` va PM v7 da yeu cau; repo khong co `scripts/production.py` hay `deploy/production.yaml`. | Implement `install/upgrade/status/doctor/logs/rollback` theo acceptance criteria muc 5 PM v7. Config phai schema-validated, idempotent, khong chua plaintext secret; dung secret references. Install/upgrade phai goi release gate, migrate, wait deep-ready, reconcile va ghi artifact. |
| P0 / Production proof | Chua co mot lan chay full topology giong production. | Airbyte 1.8.5 + connector pods chay K8s, nhung product chay Compose qua NodePort. Lan product K8s lai khong co Airbyte. `values-certification.yaml` tat auth, dung Postgres/MinIO trong cluster va kind profile. | Tao pre-prod rehearsal gom AppBI K8s + Airbyte pinned K8s + auth bat + managed/external Postgres/Redis/object storage + CNI enforce + ingress/TLS, khong NodePort. Chay 11/11, backup/restore, policy controls va release gate tren cung deployment. |
| P0 / Security | Connector policy production van co placeholder va nam ngoai release gate. | `deploy/kubernetes/airbyte/connector-networkpolicy.yaml` van co `10.0.0.0/24`. `release-gate.py` chi render product overlay; CI Airbyte chi `apply/get` policy tren kind, noi NetworkPolicy khong enforce. | Tao `airbyte/base` + environment overlay hoac renderer nhan config that. `production.py` phai apply no; release gate phai render/check ca product va Airbyte policy. Them behavioral test duoi CNI enforce, co allow-control va deny-control. |
| P0 / Product scope | 11/11 adapter operations khong dong nghia 654 connector da san sang. | Registry co 598 source + 56 destination. Chi 3 connector la `SUPPORTED`; 651 la `BETA`, nhung presenter van dat `selectable=true` cho BETA. OAuth consent flow con duoc README xep V1.1. | Chot launch promise. Neu V1 chi support curated set, mac dinh chi cho chon `SUPPORTED`; BETA can feature flag/admin opt-in + canh bao. Neu ban "all connectors", phai co UAT matrix theo archetype va automated certification per connector/version, khong duoc dung mot E2E Postgres de dai dien. |
| P1 / Security | Airbyte Config API production boundary chua duoc certify. | Certification profile `auth.enabled: false`; adapter co basic auth nhung chua co E2E auth-enabled. Khong co ingress NetworkPolicy cho Airbyte server; file hien tai chi select connector job pods. | Certify voi auth bat. Giu server Service `ClusterIP`, khong public ingress/NodePort; them ingress policy chi cho AppBI api/worker va Airbyte internal components can thiet. `doctor` verify auth mode va exposure. |
| P1 / Architecture enforcement | Tuyen bo "hai instance production, Postgres tu choi ket noi" manh hon code/IaC hien co. | Startup guard chi scan mot so table trong schema `public`; same-instance chi warning va chi nhan DB ten `airbyte`. SQL tao/revoke role `appbi_product` chi nam trong ADR, khong co provisioning/IaC trong repo. | Dua topology vao production config/IaC va gate distinct DB service/resource ids. Provision least-privilege role bang script/IaC co test. Mo rong guard scan moi non-system schema/ownership marker; separation probe fail thi production doctor khong duoc pass. |
| P1 / DR evidence | Da prove mismatch detection, chua prove cross-deployment restore thanh cong. | `evidence/reconcile-cross-deployment.json` chi co mot report `30 missing / 0 present / 17 foreign`, khong co timestamp/deployment fingerprints/hai chieu. Doi endpoint sang Airbyte thu hai la reconcile scenario, khong phai restore product + Airbyte state sang moi truong moi. | Tach hai drill: (A) mismatch drill phai report missing an toan; (B) DR drill phai restore paired product DB + KEK + Airbyte DB/object storage vao fresh environment, reconcile 0 missing, decrypt all credentials va chay sync. Evidence luu ca hai deployment fingerprints va ket qua hai chieu. |
| P1 / Operations | On-call runbook dang drift voi artifact va production target. | Bang dau runbook dung ten `AppBIMetricsCollectionFailing`, `AppBIRunsStuck`; file rule that dung `AppBIMetricsDegraded`, `AppBIRunStuck`. Cac lenh incident van la `docker ps/logs/exec`, trong khi production la K8s + managed DB. | Chon file `deploy/monitoring/alerts.yaml` lam source of truth; generate/check ten alert trong runbook. Them runbook K8s production (`kubectl`, log selector, managed DB console/query), tach Compose thanh appendix staging. |
| P1 / Supply chain | Artifact moi ghi connector tags observed, khong ghi digest engine-run; platform image production/mirror chua co contract. | `connector-lock.json` co digest cho 4 bundled images, nhung Airbyte 1.8.5 chay tag khac va certification artifact chi ghi tag. | Mirror platform + enabled connector images vao registry noi bo, pin digest khi platform cho phep, luu manifest digest/SBOM/provenance trong release artifact va verify pull source trong `doctor`. |
| P2 / Documentation | Trang thai stakeholder bi stale va overstate. | `CURRENT_STATUS.md` van ghi ops closed, remaining only deployment values, connector egress/cross restore con mo theo noi dung cu; dev report lai noi da dong. Gate section con goi artifact la "signed" trong khi khong co signature. | Cap nhat `CURRENT_STATUS.md` theo PM v8; chi dung "signed" khi co ky/provenance that. Giu historical detail trong file review nay. |

## 3. Cac phan PM chap nhan

- `resource_exists` phan biet 4xx voi 5xx/transport; reconcile khong bien engine
  outage thanh "mat resource".
- Endpoint reconcile admin-only, scope workspace, gioi han concurrency 5 va khong
  expose engine ref ra public payload.
- Alternative workspace routes va hai shape job log co regression coverage.
- Engine K8s 1.8.5 da chay connector workload pods that va 11/11 operation da co
  evidence. Day la bang chung adapter tot, khong phai bang chung moi connector.
- Database boundary/ADR la huong kien truc dung; finding P1 o tren la ve muc
  enforce va reproducibility, khong phai dao nguoc quyet dinh tach DB.
- Test local PM chay lai: `194 passed, 12 skipped`; frontend typecheck, i18n
  `794/794`, connector lock va `kubectl kustomize base` deu pass.

## 4. Thu tu thuc thi de ve final

1. **Release integrity sprint:** evidence v2, product build SHA, bind run ids,
   check legal gates, unit test `release-gate.py`.
2. **Production packaging sprint:** `scripts/production.py`, config schema,
   secret references, product + Airbyte policy overlays, `doctor/status/logs`.
3. **Production-shaped rehearsal:** auth enabled, AppBI va Airbyte cung K8s,
   managed dependencies, enforcing CNI, TLS; chay E2E va tao artifact moi.
4. **DR + operations rehearsal:** paired restore vao fresh environment, alert
   delivery den pager that, runbook K8s duoc mot nguoi khac thuc hien.
5. **Product launch scope:** curated supported connectors hay all-catalog beta.
   UI, SLA va sales wording phai trung voi quyet dinh nay.
6. **GO review:** legal cleared, on-call assigned, placeholders zero, artifact
   bound to release commit/deployment, no P0/P1 open.

## 5. Definition of Done cho final production

Chi goi la production-ready khi mot release manager tren may sach co the:

```text
production.py install <reviewed-config>
  -> validates legal/config/secrets/engine/image/policies
  -> deploys or connects to pinned Airbyte
  -> migrates AppBI
  -> proves shallow + deep readiness
  -> runs scoped smoke/E2E
  -> reconciles mappings
  -> emits an evidence-bound release artifact
  -> prints status and operator URLs
```

Va tren fresh DR environment co the restore paired state, decrypt credentials,
reconcile khong missing, sync thanh cong, alert toi dung owner. Truoc diem do,
ket luan dung la **engine integration da qua gate; san pham thuong mai chua GO**.

---

# Dev — Sprint 8 sau PM review v8 (2026-08-24)

PM v8 dung o cho quan trong nhat: bao cao truoc do noi "khong con code" trong khi
release gate, production entrypoint va connector policy packaging deu con can
code. Duoi day la nhung gi da dong duoc bang code trong vong nay, va nhung gi
con lai — noi ro cai nao la code, cai nao la infra/nguoi.

## 1. P0 — Legal gate bi release gate bo qua: DA DONG

`release-gate.py` truoc chi doc `airbyte_api_certification`. `LIC-001:
NOT_CLEARED` nam trong **cung file do** va khong chan gi ca.

Gio `check_release_gates()` doc toan bo `release_gates` va fail-closed theo
allow-list (`PASSING/CLEARED/PASSED/NOT_APPLICABLE`), nen mot status ai do them
vao ma chua ai day script hieu se **chan**, khong phai lot.

```
RELEASE BLOCKED:
  - release gate LIC-001 is NOT_CLEARED: Airbyte licensing approved for the
    intended delivery model
```

Status cua LIC-001 khong duoc dev tu doi — do la quyet dinh cua legal/owner.

## 2. P0 — One-command production entrypoint: DA CO

`scripts/production.py` + `deploy/production.yaml.example` + `deploy/demo.yaml`.

```
install | upgrade | status | doctor | logs | rollback
```

Hai profile, va chung khong thay the nhau duoc:

| Profile | Cho | Ep buoc |
|---|---|---|
| `external-airbyte-k8s` | production | K8s, managed datastore, Airbyte da pin do nguoi khac van hanh |
| `single-host-demo` | mot may | Compose, tat ca local. **Tu choi** chay voi `app_env: production` |

Phan dang gia khong phai duong happy path ma la cac cho no **dung lai**. Do
that, tren template da dien day du roi lam hong tung thu:

```
floating tag         refused    (tag: latest -> certification vo nghia ma khong ai bao)
uncertified engine   refused    (platform_version khong co trong compatibility.yaml)
literal secret       refused    (secrets.* phai la secret:// | env:// | file://)
bad workspace        refused    (khong phai UUID)
shared database      refused    (engine.database_url_ref == datastores.database_url_ref)
```

Va template nguyen ban thi tu choi cai dat, liet ke **tung field** con
placeholder — khong phai "config invalid".

`doctor` kiem: config, `/healthz` + `/readyz` + `/readyz?deep=1`, engine version
that so voi version pin, **auth mode cua engine** (profile production fail neu
Airbyte dang `auth: none` — dung cai ma certification profile bat), database
separation, reconcile, release gates, on-call, placeholder ca hai overlay.

## 3. P0 — Connector policy: co base/overlay va nam trong release gate

Truoc: mot file phang, CIDR `10.0.0.0/24`, va `release-gate.py` chi render
overlay cua product.

Gio `deploy/kubernetes/airbyte/{base,overlays/production}`. Base van co CIDR sai
co y (fail closed); overlay patch bang JSON patch theo index — **khong** phai
strategic merge, vi `egress` la list khong co merge key nen strategic merge se
*them* rule thu ba va de nguyen `10.0.0.0/24` duoc allow ben canh subnet that.
Do dung la loi ma ca overlay nay sinh ra de tranh.

`release-gate.py record --engine-policy-overlay` render ca hai va bao rieng:

```
product : 'registry.internal/', 'appbi.example.internal' con trong rendered manifests
engine  : (sach)
```

Them `airbyte-server-ingress`: chi `appbi-api`/`appbi-worker` trong namespace
`appbi` va cac component cua chinh Airbyte moi goi duoc Config API port 8001.
Guardrail 1 truoc do la mot quy uoc; gio la mot object.

Co y **khong** dat egress policy len Airbyte control plane: no goi database,
object store, Temporal va K8s API cua chinh no, doan sai o do la lam chet engine
chu khong phai lam chat bao mat.

## 4. P0 — Launch scope: 654 connector, 3 duoc chung nhan

PM dung: 11/11 adapter operation khong co nghia 654 connector san sang. Presenter
truoc do dat `selectable=true` cho ca 651 BETA.

Gio co `CONNECTOR_LAUNCH_SCOPE` (mac dinh **`SUPPORTED_ONLY`**) va
`CONNECTOR_BETA_ALLOWLIST` cho admin bat rieng tung connector.

Quan trong: rule nam trong `settings.connector_is_offered()` va duoc goi o **ca
hai** cho — presenter *va* `catalog.require_usable()`, tuc la duong tao source/
destination. Mot cai the bi lam mo trong khi endpoint van nhan la trang tri,
khong phai launch scope. Goi API truc tiep gio nhan `CONNECTOR_NOT_IN_LAUNCH_SCOPE`.

## 5. P1 — Database separation: guard rong hon + provisioning that

Guard truoc chi quet schema `public`. `search_path` la connection setting; mot
deployment doi no se **lot** — dung tren database ma guard sinh ra de tu choi.
Gio quet moi non-system schema.

SQL tao role least-privilege truoc do chi nam trong ADR duoi dang code block
khong ai chay duoc. Gio la `scripts/provision-db.py` (idempotent) voi
`--verify` chay duoc tu `doctor`.

Do that tren stack:

```
provision -> resetting the password for role appbi_product
             granting on appbi_integration
             revoking on airbyte
             granting objects inside appbi_integration

verify (role cua product)  -> appbi_product cannot connect to airbyte
                              separation holds
verify (role admin)        -> SEPARATION NOT ENFORCED
                              - appbi is a superuser, so this check cannot
                                prove anything
                              - appbi may CONNECT to 'airbyte'
```

Chieu thu hai la cho dang gia: verify bang superuser se **luon** pass tren mot
deployment hong, nen script tu choi coi do la bang chung.

Mot bug that trong luc lam: `ALTER ROLE ... PASSWORD %s` bi Postgres tu choi —
utility statement khong nhan bind parameter. Dung `psycopg.sql.Literal` thay vi
f-string, vi f-string o day la injection point voi bat ky cai gi sinh password.

## 6. P1 — On-call runbook drift: DA DONG bang test

Runbook goi `AppBIMetricsCollectionFailing` va `AppBIRunsStuck`; file rule that
khai bao `AppBIMetricsDegraded` va `AppBIRunStuck`. Khong co gi fail. Mot nguoi
truc tim ten alert tren trang nay trong he thong alerting se khong thay gi —
dung luc te nhat.

Nguyen nhan goc: runbook **giu ban sao rieng** cua rule. Da xoa ban sao, chi tro
toi `deploy/monitoring/alerts.yaml`, va them test so bang alert trong runbook voi
file rule theo **ca hai chieu** — ten khong ton tai la fail, ma rule khong duoc
runbook huong dan cung la fail.

## 7. Con lai — va cai nao la code

| PM v8 | Trang thai | Con lai la gi |
|---|---|---|
| LIC-001 | code da chan | quyet dinh cua legal/owner |
| Evidence v2 bind build/run ids | **chua lam** | code |
| production.py | xong | dien config that |
| Full production topology rehearsal | **chua lam** | infra: cluster that, managed Postgres/Redis/S3, TLS, CNI enforce |
| Connector policy packaging | xong | dien CIDR that |
| Launch scope | code xong | quyet dinh thuong mai: curated hay all-catalog |
| Airbyte auth-enabled certification | **chua lam** | infra + mot vong e2e |
| DR paired restore vao fresh env | **chua lam** | infra + mot vong drill |
| Supply chain digest/mirror | **chua lam** | code + registry noi bo |

Ba muc `chua lam` lon nhat deu can ha tang that (cluster, managed datastore,
registry noi bo), khong phai code trong repo nay. Noi ro de khong lap lai loi
cua bao cao truoc: **khong** ket luan "chi con dien gia tri".

## 8. Do that: xoa sach roi dung day lai bang mot lenh

Xoa toan bo dau vet cua project tren may: 19 container, 13 volume, image cua
product, image platform cua Airbyte, cluster certification, va ca `.env`. Sau do:

```bash
python scripts/production.py install --config deploy/demo.yaml
```

Exit 0. Sinh encryption key + JWT secret, build 5 image, chay 6 container, doi
den khi API **that su** phuc vu va engine tra loi, reconcile, in URL. Chay lai
lan hai giu nguyen secret cu — regenerate key se lam mo côi toan bo credential
trong database.

Roi chay sync that tren stack vua dung: **2500 record**, lan hai cursor giu
dung, job logs ra day du.

### Hai defect that ma chi chay moi lo ra

1. `env://APPBI_DEMO_PASSWORD` cua demo profile khong resolve duoc, nen
   reconcile tra 401 — **trong khi install van bao thanh cong**. Do la kieu
   that bai te nhat: mot buoc kiem tra lang le khong chay.
2. `doctor` chay tu shell moi cung dinh y het, vi no khong doc `.env`.

Ca hai da sua: `env://` doc `.env` khi bien khong co trong environment.

### Va mot thu dung nhu mong doi

Release gate **tu choi** ghi artifact tu demo stack:

```
!! engine is AIRBYTE_EMBEDDED, not AIRBYTE_API. Certification against the
   embedded runner is not evidence for a production release.
```

`doctor` tra ve exit 1 voi dung hai ly do that su con lai:

```
NOT PRODUCTION READY
  - release gate LIC-001 is NOT_CLEARED
  - 5 on-call role(s) still marked TO BE ASSIGNED
```

## 9. Verification vong nay

```text
backend: python -m pytest tests -q            -> 207 passed, 12 skipped
frontend: npm run typecheck                   -> PASS
i18n: node scripts/check-i18n.mjs src         -> OK
connector lock                                -> OK (4 entries)
kustomize x4 (product base/overlay, airbyte base/overlay) -> render sach
clean-room install (khong container/image/.env) -> exit 0
e2e tren stack vua dung                       -> 2500 records, cursor giu
launch scope qua API that                     -> 654 listed / 3 selectable
POST /sources voi connector BETA              -> CONNECTOR_NOT_IN_LAUNCH_SCOPE
provision-db --verify (role product)          -> separation holds
provision-db --verify (role admin)            -> tu choi, dung
release-gate check                            -> BLOCKED vi LIC-001 + on-call
```

---

# PM review v9 - audit core production va kha nang dung doc lap upstream (2026-08-24)

## 1. Ket luan PM

**NO-GO production.** Bao cao Sprint 8 da dong dung mot so finding cu, nhung
ket luan `production.py xong, chi con dien config` la sai. Lan review nay doc
duong thuc thi that, manifest render, migration, run lifecycle, bootstrap va
supply chain; khong chi doc comment cua dev.

| Pham vi | Ket luan |
|---|---|
| Adapter Airbyte 1.8.5, 11/11 operations | **Chap nhan co gioi han** |
| Demo Compose tren may hien tai | **PASS cho demo** |
| Clean install production Kubernetes | **Chua duoc chung minh** |
| Upgrade/rollback production | **Khong an toan** |
| Core chong duplicate/recovery | **Con P0** |
| Security launch gate | **Con P0** |
| Chay tren may/cluster khac | **Chua duoc chung minh** |
| Dung khi mat GitHub/Airbyte registry | **Chua san sang** |
| Pham vi connector | 654 liet ke, **chi 3 selectable/supported** |
| Commercial/legal | **NO-GO, LIC-001 NOT_CLEARED** |

`207 passed` van la tin hieu tot, nhung 12 live test bi skip va khong co test
nao bao phu cac race/restart/upgrade duoi day. So test khong thay the release
criteria.

## 2. Bang chung PM tu chay lai

```text
pytest                         207 passed, 12 skipped
frontend typecheck             PASS
i18n                           794/794
kustomize x4                   PASS
connector lock verify          PASS (4 entries)
demo health/deep readiness     200/200; 6 containers healthy
production doctor (demo cfg)   exit 1: LIC-001 + 5 on-call roles
release-gate check             exit 1: no certification.json
npm audit --omit=dev           exit 1: 2 high-severity packages
git rev-parse                  fail: workspace hien tai khong co .git
```

PM cung monkeypatch dung cac dependency ben ngoai de chi test control flow cua
`cmd_install`: reconcile tra mismatch va release gate tra exit 1, nhung
`cmd_install` van in `done` va return **0**. Day la bang chung hanh vi, khong
phai suy doan tu comment.

## 3. P0 - bat buoc sua truoc khi rehearsal production

### P0-CORE-001 - Fresh production tao tai khoan mat khau mac dinh

**Evidence**

- `backend/app/bootstrap.py:224-305` luon tao platform admin va cac user
  dataadmin/operator/analyst. Admin dung default trong `config.py`; ba user con
  lai hard-code `Admin@12345`.
- `SEED_DEMO_DATA` chi duoc khai bao o `backend/app/core/config.py:153`; khong co
  code nao doc no.
- Manifest production dat `SEED_DEMO_DATA=false`, nhung gia tri nay khong co tac
  dung. Secret command trong `deploy/kubernetes/README.md:114-121` cung khong
  cap seed password.
- `deploy/production.yaml.example` khai bao operator khac voi user bootstrap, nen
  fresh production co the login reconcile that bai roi install van bao xong.

**Risk:** mot database production rong se co tai khoan dac quyen voi credential
doan duoc. Day la stop-ship security issue.

**Acceptance criteria**

1. `seed_demo_data=false` khong tao bat ky demo user/workspace nao.
2. Production DB rong phai fail closed neu chua co co che bootstrap admin duoc
   phe duyet; khong duoc fallback sang password trong code.
3. Bootstrap admin dung one-time secret hoac IdP; bat doi password/revoke secret
   sau lan dau. Khong tao ba role demo trong production.
4. Them integration test tren DB rong voi `APP_ENV=production` va
   `SEED_DEMO_DATA=false`, assert zero default credential.

### P0-CORE-002 - Migration Job khong dam bao chay khi upgrade

**Evidence**

- `deploy/kubernetes/base/migrate-job.yaml` dung ten co dinh `appbi-migrate`.
  Annotation `kustomize.toolkit.fluxcd.io/force` chi co y nghia voi Flux; lenh
  hien tai la `kubectl apply -k`.
- Job Completed khong tu chay lai khi apply. Neu image/template thay doi,
  Kubernetes Job Pod template la immutable va apply co the bi tu choi.
- `scripts/production.py:465-483` apply tat ca resource cung luc, khong wait
  `job/appbi-migrate` Complete truoc rollout.
- Init container o `api.yaml:47-61` va `worker.yaml:45-59` chi check
  `count(*) from alembic_version`. DB o revision cu van pass ngay.
- `deploy/kubernetes/README.md:152` noi re-apply se re-run Job; nhan dinh nay sai.

**Risk:** code moi co the chay tren schema cu, hoac upgrade dung vi immutable
Job. Clean install tu DB rong khong bao phu failure mode nay.

**Acceptance criteria**

1. Migration artifact co ten theo release/revision hoac orchestrator xoa/tao Job
   mot cach ro rang; khong dua vao annotation cua controller khong su dung.
2. Apply migration rieng, wait `condition=complete`, fail thi in log va dung;
   sau do moi rollout API/worker/frontend.
3. Init/deploy gate so sanh DB revision voi Alembic head cua image, khong chi
   check co row.
4. Co test upgrade N-1 -> N tren DB co data, test rerun cung release, va test
   migration failure khong rollout code moi.

### P0-CORE-003 - Hai API replica co the tao hai sync cung pipeline

**Evidence**

- Production co `api replicas: 2`.
- `runs.trigger()` query idempotency va active run theo kieu check-then-insert
  (`runs.py:193-232`).
- `pipeline_runs.idempotency_key` chi la index thuong, khong unique
  (`models/run.py:45`, baseline migration line 427).
- `pipelines.active_run()` khong lock; khong co partial unique constraint bao
  dam mot active run/pipeline.
- Khong co concurrent integration test cho `Idempotency-Key` hay two-trigger.

**Risk:** hai request song song co the tao hai Airbyte job ghi vao cung
destination, gay duplicate, overwrite race hoac cursor/state sai.

**Acceptance criteria**

1. Unique partial constraint `(workspace_id, idempotency_key)` khi key khong
   null; conflict phai tra lai run cu.
2. Database-level invariant mot active run moi pipeline, hoac transaction/
   advisory lock tren pipeline. Khong chi them query check.
3. Scheduler cung phai dung cung invariant; sau do moi duoc tang worker replica.
4. Test Postgres that voi 20 request dong thoi: dung 1 run va dung 1 engine sync.

### P0-CORE-004 - Restart worker co the bao FAILED trong khi Airbyte van chay

**Evidence**

- `WORKER_ID = hostname-pid` (`worker.py:46`). Trong container restart cung Pod,
  hostname thuong giu nguyen va process lai la PID 1.
- Startup goi `fail_orphans(session, WORKER_ID)`.
- `fail_orphans()` danh FAILED moi active run co `claimed_by == worker_id`, ke
  ca khi heartbeat con moi va da co `engine_job_ref` (`runs.py:502-524`).
- Reconciler chi doc active status, nen run da bi FAILED se khong duoc sua lai;
  Airbyte job cu van co the ghi du lieu. User thay FAILED va co the retry.

**Acceptance criteria**

1. Recovery cua `AIRBYTE_API` phai hoi engine cho moi active
   `engine_job_ref` va tiep tuc reconcile; khong fail theo owner identity.
2. Chi `STARTING` khong co engine ref va qua lease moi duoc coi lost.
3. Restart worker that giua full refresh va incremental; assert mot engine job,
   product ket thuc dung status, state/cursor khong mat.
4. Them chaos test kill container/Pod va rollout worker trong luc sync.

### P0-REL-001 - Production entrypoint fail-open va config khong phai source of truth

**Evidence**

- `cmd_install()` chi warn khi reconcile mismatch, bo qua return cua artifact/
  gate, luon return 0 (`production.py:615-657`). PM da reproduce return 0 khi
  mapping mismatch va release gate exit 1.
- `install_k8s()` chi render/apply static overlay. Cac field `product.registry`,
  `product.image`, `product.tag`, `engine.url`, namespace, workspace/auth/secret
  refs va datastore refs trong config khong duoc render vao manifest.
- Overlay van hard-code `registry.internal/appbi/*:1.0.0`, host/CIDR mau va
  Airbyte service URL. Dien `production.yaml` khong thay doi cac gia tri nay.
- `verify_engine()` goi URL trong config, trong khi Pod co the noi voi URL khac
  trong ConfigMap. Gate co the verify engine A roi deploy Pod noi engine B.
- Production auth refs la `secret://`, nhung installer khong verify Secret object
  va key ton tai. Direct engine probe khong gui Basic auth, nen auth-enabled
  target khong di qua dung path.

**Acceptance criteria**

1. Mot config tao ra ephemeral Kustomize overlay/rendered manifest duy nhat.
   Assert rendered image digest, namespace, ingress host, engine URL, workspace,
   secret names/keys va CIDR khop config truoc apply.
2. Production `install/upgrade` return non-zero neu reconcile khong consistent,
   khong record artifact, release gate fail, secret thieu, deep readiness fail.
3. Demo co the warning; production khong co warning-only cho release invariant.
4. Verify engine qua chinh AppBI Pod/admin compatibility endpoint sau deploy, de
   check dung auth va dung network path ma product su dung.
5. Them test control flow cho moi failure branch, khong chi test validator.

### P0-SEC-001 - Cookie production va dependency vulnerability gate

**Evidence**

- Session cookie dung `secure=settings.cookie_secure`; default la `false`.
  Manifest production khong set `COOKIE_SECURE=true`.
- `npm audit --omit=dev` tren lockfile hien tai bao 2 package high-severity:
  `next@14.2.35` va PostCSS version do Next keo theo.
- CI khong co `npm audit`, `pip-audit`, image scan, SBOM hay signature gate.

**Acceptance criteria**

1. `APP_ENV=production` phai tu choi start neu `COOKIE_SECURE` khong true va
   ingress la HTTPS; them test cookie co `Secure`, `HttpOnly`, `SameSite`.
2. Nang Next/PostCSS len ban da fix, regression test, `npm audit --omit=dev`
   khong con high/critical.
3. Them backend dependency va container image scan; high/critical co SLA va
   exception co owner/expiry, khong im lang.

### P0-PLAT-001 - Chung nhan tren Helm packaging da deprecated

**Evidence**

- CI va runbook dang dung `https://airbytehq.github.io/helm-charts` va
  `--version 1.8.5` (`ci.yml:235-255`). Day la Helm chart V1.
- Airbyte da danh dau repo nay deprecated/no fixes. Tai lieu hien tai dung chart
  V2 repo `/charts`; Airbyte app `1.8.5` tuong ung chart `2.0.17`, khong phai
  chart version `1.8.5`.
- Certification hien tai tat auth, dung in-cluster Postgres/MinIO, con product
  chay Compose qua NodePort. No chung minh adapter, chua chung minh production.

Nguon official:

- [Airbyte Helm V1 deprecated](https://github.com/airbytehq/helm-charts)
- [Airbyte Helm chart V2 migration and version mapping](https://github.com/airbytehq/airbyte/blob/master/docs/platform/deploying-airbyte/chart-v2-community.mdx)
- [Current Airbyte deployment guide](https://github.com/airbytehq/airbyte/blob/master/docs/platform/deploying-airbyte/deploying-airbyte.md)

**Acceptance criteria:** vendor/pin chart V2 by digest, recertify 1.8.5 on chart
2.0.17 (hoac target moi duoc quyet dinh) voi auth enabled, external DB/object
storage, AppBI K8s, TLS va CNI that. Evidence phai ghi rieng chart version va app
version.

## 4. P1 - core/operations phai dong truoc GO

| ID | Finding | Viec can lam |
|---|---|---|
| P1-RUN-001 | `RUN_TIMEOUT_SECONDS` duoc tao trong request nhung Airbyte API adapter bo qua; khong co janitor cancel/timeout job. | Dinh nghia timeout ownership, cancel engine, terminalize dung va test job treo. |
| P1-SAGA-001 | Create source/destination/connection goi engine truoc local commit. Timeout sau khi Airbyte da tao se de orphan; reconcile chi doc mapping da ton tai. | Them operation/outbox state + product correlation id; retry/adopt/compensate co idempotency. |
| P1-OPS-001 | `production.py upgrade` goi `backup.py`, nhung script chi `docker exec` local Postgres va khong backup Airbyte. | Provider cho managed snapshot/pg_dump, paired Airbyte backup, artifact id; abort upgrade neu thieu. |
| P1-OPS-002 | `rollback` chi in huong dan; `status` return 0 ke ca endpoint FAIL. | Dat ten dung (`rollback-plan`) hoac implement; status/automation phai co exit code dung. |
| P1-DOC-001 | Doctor noi verify DB role nhung code khong goi `provision-db.py --verify`; separation check bang substring co the in `ok` khi deep probe da fail. | Dung structured readiness + actual role verification, test negative path. |
| P1-NET-001 | Airbyte same-namespace ingress/egress dung `podSelector: {}` va mo 8001 cho connector job pods. | Chung minh port nao connector can, selector cu the; auth defense-in-depth; test connector khong doc Config API. |
| P1-PORT-001 | `.env` parser cat tai `#`; password/token hop le co `#` bi doi. | Dung parser dotenv chuan va test Windows/Linux/CRLF/quoted values. |
| P1-IMG-001 | Backend production image van chua Docker CLI va tests; base images/packages chi pin tag, khong digest/hash. | Tach production/embedded target, slim runtime, pin digest va dependency hashes. |

## 5. Neu Airbyte/GitHub sau nay khong con cho tai

### Hien tai phu thuoc o dau

Runtime cua product **khong git clone backend Airbyte**. Day la diem dung: tiep tuc
giu Airbyte nhu engine rieng sau adapter, khong copy source vao product.

Nhung fresh install/DR/build van phu thuoc upstream:

1. Airbyte Helm chart tu GitHub Pages.
2. Airbyte platform va connector images tu public OCI/Docker Hub.
3. Registry metadata tu `connectors.airbyte.com` khi regenerate catalogue.
4. Python/Node/base image va Docker apt repo khi build product.
5. GitHub Actions va binary kubeconform tai tu GitHub.

Neu chi Git repo Airbyte bi private, may dang chay voi image da pull co the van
chay. Neu registry/chart/tag bi xoa hoac outbound bi chan, may moi va DR **khong
duoc dam bao**. Rủi ro lon hon `git pull` la artifact availability va license.

### Goi `Upstream Independence` bat buoc

1. Mirror vao internal OCI registry tat ca product images, Airbyte platform
   images, **chi connector trong launch scope**, base images; deploy bang digest.
2. Luu chart V2 `.tgz`/OCI artifact, values schema va SHA256 trong artifact
   store noi bo. Production khong `helm repo add` public.
3. Luu source tarball, LICENSE/NOTICE va SBOM dung version da ship. Khong xoa/
   che notice. Airbyte hien cong bo ELv2 cho phan lon repo/connectors va MIT cho
   protocol; `LIC-001` van can legal ket luan theo delivery model:
   [official license summary](https://github.com/airbytehq/airbyte/blob/master/docs/community/licenses/README.md).
4. Mirror Python wheels/npm cache/Docker packages; build co lock + hashes, khong
   can Internet.
5. Tao signed release bundle/manifest gom image digest, chart digest, connector
   digest, migration head, config schema, SBOM, source commit va evidence.
6. Release gate doc provenance tu OCI label/attestation, khong bat production
   host phai co `.git`. Workspace hien tai khong co `.git`, nen gate hien tai
   khong the tao artifact hop le.
7. Quarterly test tren fresh runner voi DNS/Internet upstream bi block: install,
   restore, check/discover/full+incremental/cancel, restart worker va upgrade.
8. Duy tri internal Git mirror cua chinh AppBI va CI runner noi bo; GitHub outage
   khong duoc lam mat kha nang build/rollback.

Khong mirror ca 654 connector theo mac dinh: tren may review hien tai Docker da
chiem khoang 62 GB image va 21 GB build cache, du da chi co mot phan catalogue.
Mirror theo launch scope va on-demand tier de chi phi/attack surface co gioi han.

## 6. Pham vi san pham: ky vong cu chua dat

Muc tieu ban dau la dung moi source/destination Airbyte da cung cap. Hien tai
product liet ke 654 nhung chi **3** connector duoc selectable va supported. Day
la quyet dinh an toan, nhung khong duoc marketing/noi bo goi la `support all
Airbyte connectors`.

Khuyen nghi lau dai:

- Tier 1: certified/supported, co E2E va SLA.
- Tier 2: customer-validated allowlist, co owner va compatibility record.
- Tier 3: catalogue preview, khong selectable.
- Moi connector moi/upgrade phai qua automated certification pipeline; khong
  suy ra support chi vi image pull thanh cong.

## 7. Thu tu giao viec cho dev

### Sprint A - stop-ship core

1. P0-CORE-001 bootstrap credential + secure cookie + dependency CVE.
2. P0-CORE-002 migration/upgrade ordering va N-1 -> N test.
3. P0-CORE-003 database invariants cho idempotency/active run.
4. P0-CORE-004 worker restart recovery voi live Airbyte job.
5. P0-REL-001 config-to-rendered-manifest va fail-closed install.

### Sprint B - release va supply chain

1. Evidence v2 bind image digest, chart/app version, workspace, run ids va
   rendered manifest hash.
2. Internal OCI/chart/dependency mirror, SBOM, signature, CVE gate.
3. Production backup provider + paired restore artifact.
4. Air-gapped clean-room test tren Linux amd64; Windows la operator client, ARM
   phai duoc test hoac tuyen bo unsupported.

### Sprint C - production-shaped rehearsal

1. Airbyte 1.8.5 tren Helm chart V2 2.0.17 hoac version target moi.
2. AppBI va Airbyte cung Kubernetes, auth enabled, managed Postgres rieng,
   Redis, object storage, TLS, enforcing CNI va internal registry.
3. Test: install, upgrade, parallel trigger, worker restart giua sync, node
   drain, engine outage/recovery, registry outage, backup/restore va alert pager.
4. Tao artifact moi va de nguoi khac dev chay runbook tu dau.

### Sprint D - business GO

1. Legal clear `LIC-001`, quyet dinh delivery/white-label bang van ban.
2. Gan du 5 on-call roles va test alert delivery.
3. Chot launch scope 3 connector hay roadmap tiered; wording FE/sales/SLA khop.
4. GO review chi khi release gate pass va khong con P0/P1.

## 8. Definition of Done v9

Chi production-ready khi mot release manager tren fresh environment, khong co
Git checkout va khong truy cap public upstream, co the dung **mot reviewed
config + signed release bundle** de:

```text
preflight -> verify cluster/resources/secrets/digests/license gates
migrate   -> exact Alembic head, wait complete before rollout
deploy    -> rendered config equals reviewed config
verify    -> auth + shallow/deep ready + engine identity
exercise  -> check/discover/full/incremental/cancel on supported connectors
recover   -> worker restart does not duplicate or lose engine jobs
upgrade   -> backup ids recorded; N-1 -> N passes
evidence  -> signed artifact binds build, chart, config, workspace and run ids
```

**PM decision v9:** tiep tuc dung kien truc adapter va Airbyte nhu engine rieng;
khong viet lai, khong copy backend Airbyte vao repo. Tam dung moi tuyen bo
production-ready. Dev phai dong Sprint A truoc khi xin them production
infrastructure cho Sprint C, vi cluster that khong sua duoc cac P0 core hien tai.

---

# Dev — Sprint A sau PM review v9 (2026-08-24)

PM v9 dung o tat ca cac diem. Dac biet dung o cho nay: bao cao Sprint 8 ket
luan "khong con code", va do la sai. Nam finding P0 duoi day deu la code, deu
da ship, va deu di qua 207 test ma khong bi bat.

Diem chung cua ca nam: **mot check dat sai cho**. Credential quyet dinh boi code
thay vi boi deployment; invariant khang dinh bang SELECT thay vi bang index;
ownership suy dien thay vi hoi; warning o cho phai la failure; va mot config mo
ta mot deployment ma no khong tao ra.

## P0-CORE-001 — Production tao tai khoan mat khau mac dinh

`SEED_DEMO_DATA` duoc khai bao va **khong dong nao doc no**. Manifest production
dat `false` va van nhan `admin@appbi.local` cung ba tai khoan
`dataadmin/operator/analyst` dung chung `Admin@12345`.

Da sua:

| | |
|---|---|
| `seed()` | tach hai nhanh: `_seed_demo()` va `_bootstrap_admin()` |
| Demo identities | chi ton tai trong nhanh demo; test assert chung khong reachable tu nhanh production |
| `APP_ENV=production` + `SEED_DEMO_DATA=true` | `BootstrapRefused`, va startup check cung chan |
| DB production rong, khong co secret | **tu choi start**, khong fallback |
| `BOOTSTRAP_ADMIN_PASSWORD` = mat khau demo | tu choi |
| Tai khoan tao ra | `password_change_required=True` |

Do that tren DB rong voi `APP_ENV=production SEED_DEMO_DATA=false`:

```text
khong co secret        -> BootstrapRefused: "...Refusing rather than falling back
                          to a default, because a privileged account with a
                          guessable password is worse than a deployment that
                          will not start."
co secret that         -> accounts created: 1
                          ('ops@acme.io', is_platform_admin=True,
                           password_change_required=True)
                          default demo accounts: 0
```

Va forced change duoc enforce o `request_context` — chokepoint moi endpoint
non-auth di qua, khong phai tung route (route ai do quen chinh la cho no hong):

```text
login                       -> van duoc
GET /api/v1/pipelines       -> PASSWORD_CHANGE_REQUIRED
GET /api/v1/overview        -> PASSWORD_CHANGE_REQUIRED
change-password (yeu)       -> "Mat khau phai co it nhat 12 ky tu..."
change-password (dat)       -> ok, flag now False
GET /api/v1/pipelines       -> ALLOWED
```

## P0-CORE-002 — Migration khong dam bao chay khi upgrade

Ba loi rieng biet, moi cai deu du de code moi chay tren schema cu:

1. `kustomize.toolkit.fluxcd.io/force` chi co nghia voi Flux. Project dung
   `kubectl apply -k`. Annotation nay lam ca hai van de duoi trong nhu da xu ly.
2. Job da Completed khong duoc apply chay lai, va pod template cua Job la
   immutable nen apply image moi len Job cu bi **tu choi**.
3. Init container chi check `count(*) from alembic_version` — DB o revision cu
   pass ngay lap tuc.

Da sua: bo annotation; init container doc **Alembic head cua chinh image** bang
`ScriptDirectory` roi so voi `version_num` trong DB; `production.py` delete Job,
apply rieng, `wait --for=condition=complete`, in log neu fail, **roi moi** rollout.
Test assert thu tu do (`wait` phai dung truoc `rollout`).

Do that N-1 -> N tren DB co data:

```text
upgrade c82c6e3a8fb7 -> d4a1f07c2b18
revision: d4a1f07c2b18
existing rows kept: 1
backfilled default: False
invariants: ['uq_pipeline_active_run', 'uq_run_idempotency_key']
rerun same release: no-op
alembic check: No new upgrade operations detected
```

`deploy/kubernetes/README.md` truoc noi "re-apply se re-run Job" — da sua thanh
dung, kem lenh tay cho ai khong dung orchestrator.

## P0-CORE-003 — Hai API replica tao hai run

Check-then-insert dung voi mot replica va sai voi hai. Production chay
`replicas: 2`.

Migration `d4a1f07c2b18` them hai **partial unique index**:

- `uq_run_idempotency_key (workspace_id, idempotency_key) WHERE idempotency_key IS NOT NULL`
- `uq_pipeline_active_run (pipeline_id) WHERE status IN (QUEUED, STARTING, RUNNING, CANCEL_REQUESTED)`

Partial la bat buoc: unique thuong tren `pipeline_id` se cam mot pipeline co qua
mot run **trong lich su**.

`trigger()` giu SELECT (de co error message tot) nhung gio bat `IntegrityError`:
trung idempotency key -> tra ve run da thang (dung hop dong cua idempotency
key); trung active run -> `PIPELINE_ALREADY_RUNNING`.

Do that tren Postgres, dung acceptance criteria cua PM:

```text
20 concurrent trigger, cung idempotency key -> 1 run created, 1 won, 19 lost
active run thu hai (khong co key)           -> refused
run da ket thuc (SUCCEEDED/FAILED)          -> van insert duoc (khong cam lich su)
```

## P0-CORE-004 — Restart worker bao FAILED trong khi Airbyte van chay

`WORKER_ID = hostname-pid`. Container restart giu nguyen hostname va lai la
PID 1, nen `claimed_by == worker_id` khop **moi** run dang chay. Restart danh
FAILED job Airbyte hoan toan khoe; Airbyte tiep tuc ghi; user thay FAILED va
retry -> job thu hai vao cung destination.

Ownership la cau hoi sai. `recover_orphans()` hoi engine:

| Engine tra loi | Ket qua |
|---|---|
| Job con day (200) | **adopted** — chi tra `claimed_by`, giu nguyen status, reconciler tiep tuc |
| Job khong ton tai (4xx) | **lost** — FAILED, that su khong con gi chay |
| Engine khong tra loi (5xx/transport) | **deferred** — khong ket luan gi |

Deferred la phan quan trong: neu 5xx tinh la "mat", mot lan restart engine se
bao mat sach resource, va hanh dong tiep theo tu bao cao do la pha huy.

Run khong co `engine_job_ref` chi bi FAILED khi **da qua lease** — mot run dang
duoc claim ngay luc do cung chua co ref.

Do that voi fake engine tra ba kieu:

```text
adopted=1 lost=2 deferred=2
live  (engine con)      -> RUNNING   (truoc day: FAILED)
down  (engine im lang)  -> RUNNING
fresh (dang claim)      -> STARTING
gone  (engine noi khong)-> FAILED
never (chua toi engine) -> FAILED
```

## P0-REL-001 — Installer fail-open va config khong dieu khien manifest

PM reproduce duoc: reconcile mismatch + release gate exit 1, `cmd_install` van
in `done` va return 0. Da sua, va do lai bang chinh kich ban do:

```text
PRODUCTION profile
  reconcile mismatch + gate exit 1     exit=1
  reconcile ok, no artifact recorded   exit=1
  everything healthy                   exit=0
DEMO profile
  reconcile mismatch + gate exit 1     exit=0   (warning-only, co chu y)
```

Config gio **sinh ra** manifest. Truoc day `install_k8s` apply overlay cua repo
nguyen ban: dien `product.registry`, `product.tag`, `engine.url` khong doi gi
ca, va `verify_engine()` verify engine A trong khi Pod tro toi engine B.

`render_from_config()` tao overlay ephemeral (temp dir, khong ghi vao repo — mot
overlay sinh tu dong nam trong git la nguon su that thu hai cho drift), roi
`assert_rendered_matches()` kiem **output da render** truoc khi apply: namespace,
image, `AIRBYTE_API_URL`, `COOKIE_SECURE`, `SEED_DEMO_DATA`, ingress host,
placeholder. Cung bai hoc voi kube-dns selector: kiem output, khong tin patch.

Them `assert_secrets_exist()`: `secret://` phai tro toi Secret va key co that.
Kubernetes khong fail apply khi thieu key — Pod treo o
`CreateContainerConfigError`, doc nhu loi image.

## P0-SEC-001 — Cookie va dependency CVE

`COOKIE_SECURE` mac dinh `false` va khong manifest nao dat `true`. Startup gio
tu choi `APP_ENV=production` neu thieu, va ConfigMap production dat san.

Dependency:

```text
npm audit --omit=dev   truoc: 2 high (next 14.2.35, postcss)
                       sau:   found 0 vulnerabilities
pip-audit --strict     truoc: 29 known vulnerabilities in 5 packages
                       sau:   No known vulnerabilities found
```

Frontend: Next 14 -> **15.5.23** (14.2.35 la ban 14.x cuoi, khong duoc va).
postcss len 8.5.26 (direct devDependency), `sharp` override ^0.35.3. Khong nhay
Next 16: npm de xuat no chi vi transitive dep, va hai major cho mot van de
khong thuoc ve major nao ca. Typecheck + build pass.

Backend: fastapi 0.115.6 -> 0.141.1 (keo starlette moi), cryptography 44 -> 50,
python-multipart 0.0.20 -> 0.0.32. **Bo `python-jose`** thay bang `PyJWT`:
jose gan nhu khong con duoc maintain va keo theo `ecdsa`, ma advisory cua ecdsa
khong co fix version. Chi ba call site dung no.

CI gio co `npm audit --omit=dev --audit-level=high` va `pip-audit --strict`.

## Con lai — va cai nao la code

| PM v9 | Trang thai |
|---|---|
| P0-CORE-001..004, P0-REL-001, P0-SEC-001 | **dong** |
| P0-PLAT-001 Helm chart V2 2.0.17 | chua — can cluster + mot vong certification |
| P1-RUN-001 timeout ownership | chua — code |
| P1-SAGA-001 outbox/compensate | chua — code |
| P1-OPS-001 backup provider | chua — code + managed snapshot API |
| P1-OPS-002 rollback/status exit code | chua — code |
| P1-DOC-001 doctor goi provision-db --verify | chua — code |
| P1-NET-001 selector connector cu the | chua — can do bang connector pod that |
| P1-PORT-001 dotenv parser | chua — code |
| P1-IMG-001 tach production image | chua — code |
| Sprint B/C/D | chua bat dau |

Khong ket luan "chi con dien gia tri". Sprint A xong; Sprint B tro di van con
nguyen.

---

# PM review v10 - kiểm tra độc lập Sprint A (2026-08-24)

## 1. Kết luận điều hành

**NO-GO production. Sprint A chưa đóng.** Dev đã sửa đúng hướng và hai finding
được PM chấp nhận, nhưng review code cùng các phép tái hiện thật đã mở lại bốn
finding. Điểm quan trọng nhất: `222/226 tests pass` không chứng minh được
production installer, vì test hiện tại chủ yếu kiểm tra source text hoặc render
các overlay tĩnh, không gọi đường render động mà production sử dụng.

| Finding | Verdict PM v10 |
|---|---|
| P0-CORE-001 default/demo credentials | **PARTIAL** - lỗi gốc đã sửa, session bootstrap chưa an toàn hoàn chỉnh |
| P0-CORE-002 migration ordering | **PARTIAL** - orchestration đúng, schema drift vẫn tồn tại |
| P0-CORE-003 duplicate active run | **CLOSED** |
| P0-CORE-004 restart recovery | **REOPENED** |
| P0-REL-001 production entrypoint/config | **REOPENED** |
| P0-SEC-001 cookie + package audit | **CLOSED trong phạm vi package** |
| P0-PLAT-001 chart V2/production topology | **OPEN, không thay đổi** |

## 2. Findings theo mức độ

### P0-REL-001 - Production installer hiện không render được

**Evidence chạy thật**

- `scripts/production.py:460` resolve overlay thành đường dẫn tuyệt đối.
- `scripts/production.py:471` ghi đường dẫn đó vào `resources` của một
  Kustomization nằm trong `TemporaryDirectory`.
- `scripts/production.py:508` gọi `kubectl kustomize <temp-dir>` với load
  restriction mặc định.
- PM gọi chính `render_from_config()` với một config production đã điền và nhận:

```text
error: accumulating resources from
'D:/Appv2/appbi-pipeline/deploy/kubernetes/overlays/production':
new root ... cannot be absolute
```

Nghĩa là production install dừng trước secret check/migration/apply. Bốn target
tĩnh render xanh không bao phủ đường này. `test_the_config_produces_the_manifests`
ở `backend/tests/test_production_core.py:305-329` chỉ tìm tên function/string
trong source, nên vẫn xanh khi function thật không chạy được.

### P0-REL-001 - Config vẫn chưa là source of truth

Ngay cả sau khi sửa lỗi render tuyệt đối, binding hiện tại vẫn thiếu:

1. Patch động chỉ ghi `AIRBYTE_API_URL`, `APP_ENV`, `COOKIE_SECURE` và
   `SEED_DEMO_DATA` (`production.py:482-494`).
2. `engine.workspace_id` không được render/đối chiếu với
   `AIRBYTE_WORKSPACE_ID` mà Pod thật nhận từ Secret.
3. `engine.auth.*`, datastore refs, KEK/JWT refs và bootstrap refs không được
   chuyển thành `valueFrom.secretKeyRef`; Pod vẫn `envFrom: appbi-secrets` và
   `appbi-bootstrap` hard-code trong manifest.
4. `assert_secrets_exist()` gom mọi `secret://` rồi kiểm tra toàn bộ trong
   `product.namespace` (`production.py:597-618`). PM tái hiện thấy cả
   `airbyte-auth` và `airbyte-secrets` đều bị hỏi ở namespace `appbi`, dù config
   khai báo engine namespace riêng.
5. `verify_engine()` gọi endpoint không có Basic auth (`production.py:716-745`),
   nên config auth-enabled chưa đi qua đúng đường production.
6. File mẫu dùng `product.api_url`; renderer chỉ patch ingress khi có field
   `product.ingress_host`, field không có trong file mẫu.
7. `certified_platform_versions()` vẫn trộn certification Compose `0.59.1` và
   Kubernetes `1.8.5`, nên production profile có thể chấp nhận version chỉ được
   chứng nhận trên Compose.

**Risk:** operator có thể verify engine/workspace/secret A nhưng Pod chạy với
engine/workspace/secret B. Đây chính là lỗi source-of-truth mà P0-REL-001 yêu cầu
đóng.

### P0-REL-001 - Release gate chạy sau khi đã deploy

`cmd_install()` gọi `install_k8s()` ở `production.py:868-872`, sau đó mới
reconcile, record artifact và check release gate ở `production.py:873-910`.
Với `LIC-001=NOT_CLEARED`, workload vẫn có thể migrate và rollout rồi command
mới trả exit 1. Exit code đúng nhưng không phải fail-closed deployment.

**Acceptance criteria cho toàn bộ P0-REL-001**

1. Copy cây Kustomize cần thiết vào temp rồi thêm overlay động trong cùng root,
   hoặc dùng một cơ chế render có cấu trúc khác; không tắt load restriction.
2. Có integration test gọi `render_from_config()` bằng `kubectl` thật trên
   Windows và Linux, parse output và assert không còn placeholder.
3. Config schema phải reject unknown/missing fields và bind đầy đủ namespace,
   image digest, ingress/API host, engine URL/version/workspace/auth, datastore,
   runtime secrets, bootstrap secret và network values vào rendered output.
4. Secret reference phải có namespace rõ ràng. Chỉ Pod ở đúng namespace mới
   được tham chiếu Secret; secret chỉ dùng để so sánh topology không được giả
   vờ là runtime dependency của AppBI.
5. Verify sau deploy qua chính AppBI Pod/admin compatibility endpoint để chứng
   minh đúng auth, workspace và network path mà runtime sử dụng.
6. Chạy static gates `LIC`, on-call, config, provenance, artifact digest trước
   migration/rollout. Post-deploy reconcile/E2E/evidence là gate thứ hai.
7. File config mẫu phải chạy được sau khi thay placeholder, không cần biết thêm
   field ẩn như `ingress_host`.

### P0-CORE-004 - Lỗi auth bị hiểu là engine đã mất job

`recover_orphans()` chỉ defer `EngineUnavailableError`; mọi `AppError` còn lại
đều gọi `_mark_lost()` (`backend/app/services/runs.py:600-613`). Trong adapter,
mọi HTTP 4xx được gom thành `EngineOperationError` (`airbyte_api/adapter.py:204-214`).
Do đó 401, 403 hoặc 429 không có nghĩa "job không tồn tại" nhưng vẫn đánh run
`FAILED`.

PM tái hiện bằng Postgres thật và fake adapter trả HTTP 401:

```text
counts = {'adopted': 0, 'lost': 1, 'deferred': 0}
final  = FAILED
```

Airbyte job có thể vẫn chạy và ghi destination; user thấy FAILED rồi retry, quay
lại đúng duplicate-write risk của finding ban đầu.

**Acceptance criteria**

1. Adapter phải có kết quả/exception riêng cho `JOB_NOT_FOUND`; chỉ kết quả đó
   mới được `_mark_lost()`.
2. 401/403 là auth/config incident, 429 là deferred/backoff, 5xx/transport là
   deferred. Generic `EngineOperationError` không được suy ra "lost".
3. Test bảng 200-running, 200-terminal, confirmed-not-found, 401, 403, 429, 500,
   timeout; assert chỉ confirmed-not-found thành FAILED.
4. Chaos test restart worker giữa sync thật, đồng thời rotate/break engine auth;
   sau khi auth phục hồi chỉ có một engine job và run được reconcile terminal.

### P0-CORE-002 - Runtime bootstrap làm schema lệch khỏi Alembic head

Phần delete/apply/wait/rollout và init gate theo Alembic head là sửa đúng. Tuy
nhiên `migrate_schema()` chạy `alembic upgrade head` rồi gọi
`apply_schema_fixups()`. `SCHEMA_FIXUPS` lại chứa:

```sql
DROP INDEX IF EXISTS "ix_connector_definitions_display_name"
```

Trong khi model khai báo `display_name index=True` và baseline migration tạo
chính index đó. Trên database demo đang ở head `d4a1f07c2b18`, PM chạy:

```text
alembic current -> d4a1f07c2b18 (head)
alembic check   -> FAILED: add_index ix_connector_definitions_display_name
```

CI hiện chạy `alembic upgrade head && alembic check`, không chạy
`python -m app.bootstrap` ở giữa, nên không thấy runtime vừa xóa index.

**Acceptance criteria**

1. Database đã versioned chỉ được thay schema bằng Alembic. Di chuyển/remove
   manual fixups; không để startup sửa schema ngoài migration history.
2. Với legacy unversioned adoption, fixup phải có đường riêng, có test riêng và
   không chạy lại sau khi đã stamp/versioned.
3. CI trên cùng một DB phải chạy `app.bootstrap`, assert current=head, rồi
   `alembic check`; assert index thực tế tồn tại.
4. Lặp N-1 -> N bằng chính production orchestrator, không chỉ gọi Alembic trực
   tiếp; migration failure phải chứng minh workload cũ còn phục vụ và code mới
   chưa rollout.

### P0-CORE-001 - Default account đã sửa, one-time bootstrap chưa hoàn chỉnh

PM chấp nhận các phần sau:

- `SEED_DEMO_DATA=false` đi vào nhánh production thật.
- DB production rỗng không có secret thì `BootstrapRefused`.
- Có secret thì chỉ tạo một platform admin, không tạo account `@appbi.local`.
- `request_context` chặn route nghiệp vụ khi `password_change_required=true`.

Nhưng còn hai lỗ hổng:

1. Bootstrap password chỉ bị so với đúng chuỗi demo, không chạy
   `password_problems()`. Một secret rất yếu khác vẫn được chấp nhận; email cũng
   chưa được validate trước khi tạo user.
2. JWT chứa `sub/ws/iat/exp/jti` nhưng không có session/password version.
   `change_password()` đổi hash và clear flag nhưng không revoke token cũ, không
   phát cookie mới. Hai người có thể login bằng bootstrap secret; sau khi người
   thứ nhất đổi password, token cũ của người thứ hai lập tức có full quyền vì DB
   flag đã thành false. Comment "Any session issued before this point" hiện
   không có code thực thi.

Ngoài ra runbook bảo xóa `appbi-bootstrap` Secret sau lần đầu, nhưng config mẫu
vẫn tham chiếu nó và `assert_secrets_exist()` sẽ yêu cầu nó ở mọi lần upgrade.

**Acceptance criteria**

1. Validate bootstrap email và policy password trước khi insert.
2. Thêm `session_version`/`password_changed_at` vào user và token; mọi token phát
   trước password change phải bị 401. Change-password phát session cookie mới.
3. Test thật với hai session tạo từ one-time password; session cũ không dùng
   được sau khi đổi password.
4. Thiết kế lifecycle Secret rõ: cần ở fresh install, có thể xóa sau bootstrap,
   và upgrade sau đó vẫn pass mà không sửa config bằng tay.

### P0-CORE-003 - CLOSED

Hai partial unique index đúng mục tiêu. Ngoài test raw SQL của dev, PM gọi real
API 20 lần đồng thời cùng `Idempotency-Key`: cả 20 trả HTTP 202 và cùng một run
id. Finding duplicate active run được đóng. Nên bổ sung phép thử này vào suite
để giữ coverage ở service/API layer, vì test live hiện tại chỉ insert raw SQL.

### P0-SEC-001 - CLOSED trong phạm vi đã cam kết

PM chạy lại và xác nhận:

```text
npm audit --omit=dev --audit-level=high -> 0 vulnerabilities
pip-audit --strict -r requirements.txt  -> No known vulnerabilities found
frontend typecheck                      -> PASS
frontend production build               -> PASS
```

Production startup cũng reject `COOKIE_SECURE=false`. Container scan, SBOM,
signature, digest pin và internal mirror vẫn là Sprint B/P1-IMG, không được suy
ra đã đóng từ package audit.

## 3. Verification PM đã chạy

| Check | Kết quả |
|---|---|
| Full pytest + `RUN_CORE_LIVE=1` | 226 passed, 12 skipped |
| Live Postgres concurrent API trigger | 20 response, 1 unique run id |
| npm/pip audit | clean |
| Typecheck/build/i18n | pass; 794/794 translation keys |
| Four repository Kustomize targets | pass |
| Generated production Kustomize | **fail trước apply** |
| Connector lock | 4 entries, pass |
| Demo `status` / deep ready | 200 / 200 |
| Embedded E2E độc lập | 2,500 rows first run; 2 newly inserted rows on incremental run |
| Cancel in PM E2E | not proven; sync completed before cancel took effect |
| Live Alembic current | head `d4a1f07c2b18` |
| Live Alembic drift check | **fail: missing display-name index** |
| Release gate | fail as expected: no artifact; `LIC-001` remains NOT_CLEARED |

`12 skipped` gồm các lane cần engine/infrastructure riêng; vì vậy kết quả này
không thay thế auth-enabled Airbyte K8s certification hoặc production rehearsal.

## 4. Thứ tự giao việc tiếp theo

### Sprint A.1 - đóng lại stop-ship

1. P0-REL-001 renderer + full config binding + pre-deploy static gate.
2. P0-CORE-004 explicit job-not-found taxonomy và recovery matrix.
3. P0-CORE-002 xóa schema drift, bootstrap-then-alembic CI.
4. P0-CORE-001 session invalidation và bootstrap lifecycle.
5. Behavioral tests cho bốn đường trên; hạn chế source-string assertions cho
   control flow quan trọng.

### Sau Sprint A.1

Giữ nguyên thứ tự PM v9:

1. P1 timeout ownership, saga/outbox, production backup provider,
   rollback/status exit code, doctor DB verify, connector selector, dotenv
   parser và image slimming.
2. Sprint B evidence-v2, digest/SBOM/signing, internal mirrors và air-gapped
   clean-room.
3. Sprint C chart V2 + auth-enabled production-shaped rehearsal.
4. Sprint D legal `LIC-001`, on-call owner, connector launch scope và GO review.

**PM decision v10:** tiếp tục kiến trúc adapter và Airbyte như engine riêng.
Không viết lại hoặc copy backend Airbyte. Không gọi Sprint A đã đóng và không
đưa sản phẩm ra production cho đến khi bốn P0 mở lại có evidence hành vi, sau đó
mới tiếp tục Sprint B/C/D.

---

# Dev — Sprint A.1 sau PM review v10 (2026-08-24)

PM v10 đúng, và đúng ở chỗ khó chịu nhất: báo cáo "đã đóng toàn bộ 6 P0" là
sai. Bốn finding được mở lại là lỗi thật, và lý do chúng lọt qua cũng đúng như
PM nói — test của tôi phần lớn kiểm tra **chuỗi trong source**, nên chúng vẫn
xanh trong khi hàm thật không chạy nổi.

Bài học cụ thể: `test_the_config_produces_the_manifests` tìm tên hàm trong file
và pass, còn `render_from_config()` thì lỗi ngay dòng đầu tiên gọi kustomize.

## 1. P0-REL-001 — renderer không chạy được

Tôi tái hiện trước khi sửa, đúng như PM:

```text
error: accumulating resources from
'D:/Appv2/appbi-pipeline/deploy/kubernetes/overlays/production':
new root ... cannot be absolute
```

Đã sửa: **copy cây Kustomize vào temp root** rồi sinh overlay bên cạnh, tham
chiếu bằng đường dẫn tương đối (`shutil.copytree` + `os.path.relpath`). Không
tắt load restriction — nó tồn tại có lý do.

Test mới `backend/tests/test_production_render.py` gọi `render_from_config()`
thật với kubectl thật, parse output. 5 test, không có assertion nào dựa trên
chuỗi source.

Ngay khi test đó chạy thật, nó bắt được **một lỗi nữa tôi chưa thấy**: tôi patch
`/spec/rules/0/host` nhưng không patch `/spec/tls/0/hosts`, nên certificate vẫn
xin cho `appbi.example.internal`. Deployment lên được và mọi trình duyệt từ chối.

## 2. P0-REL-001 — config giờ mới thật sự điều khiển deployment

| Trường | Trước | Sau |
|---|---|---|
| `product.registry/image/tag` | overlay hard-code `registry.internal/...` | overlay copy bị **gỡ block `images:`**, config là nguồn duy nhất |
| `engine.workspace_id` | chỉ trong Secret, Pod không thấy cái đã verify | render vào ConfigMap và assert |
| `engine.auth.*`, KEK/JWT, datastore | `envFrom` — Secret nào trùng tên thì thắng | từng key bind bằng `secretKeyRef` |
| ingress host | chỉ đọc `ingress_host`, file mẫu lại dùng `api_url` | lấy hostname từ `api_url`, patch cả rule lẫn TLS |
| `DATABASE_URL_SYNC` | không có trong config mẫu | thêm |

Kết quả render thật từ file mẫu (chỉ thay placeholder, không cần field ẩn):

```text
AIRBYTE_API_URL        https://airbyte.internal.acme.io
AIRBYTE_WORKSPACE_ID   8b8a2621-7f31-46f3-82e6-36774a9ff3a6
COOKIE_SECURE          true
SEED_DEMO_DATA         false
image                  registry.acme.io/backend:1.0.0
ingress host           ['appbi.acme.io']
env SECRET_ENCRYPTION_KEY <- appbi-secrets/SECRET_ENCRYPTION_KEY
env DATABASE_URL          <- appbi-secrets/DATABASE_URL
env AIRBYTE_API_PASSWORD  <- appbi-secrets/AIRBYTE_API_PASSWORD
```

**Secret namespace:** `assert_secrets_exist()` trước gom mọi `secret://` rồi hỏi
tất cả trong `product.namespace` — kể cả `engine.database_url_ref`, tức
credential database của chính Airbyte, thứ AppBI **không được phép** đọc
(ADR-001). Giờ tập bắt buộc = đúng những gì được bind vào Pod. Secret chỉ dùng
để so sánh topology không bị coi là runtime dependency.

**Bootstrap secret lifecycle:** runbook bảo xóa sau lần đầu, nên thiếu nó là
`warn`, không phải `Stop`. Upgrade sau đó pass mà không phải sửa config.

## 3. P0-REL-001 — static gate chạy trước khi deploy

Trước: `install_k8s()` chạy xong rồi mới check release gate, nên `LIC-001`
NOT_CLEARED vẫn migrate + rollout rồi mới return 1. Exit code đúng, deployment
đã xảy ra rồi.

Giờ mọi thứ quyết định được mà không cần cluster thì quyết định trước khi chạm
cluster:

```text
DEPLOYMENT REFUSED
  - release gate LIC-001 is NOT_CLEARED
  - 5 on-call role(s) still marked TO BE ASSIGNED
  - 'registry.internal/' still in the rendered manifests of the product
  - 'appbi.example.internal' still in the rendered manifests of the product
```

Reconcile / artifact / evidence vẫn ở sau — chúng mô tả cái đã deploy nên không
thể chạy sớm hơn.

## 4. P0-CORE-004 — chỉ "confirmed not found" mới là mất

PM tái hiện: Airbyte trả 401, AppBI kết luận job đã mất, `lost=1 final=FAILED`.

Gốc rễ: adapter gom **mọi 4xx** thành `EngineOperationError`, còn recovery coi
mọi thứ không phải `EngineUnavailableError` là "job không còn".

Đã thêm `EngineResourceGoneError` (subclass của `EngineOperationError`, nên
handler cũ vẫn đúng) và phân loại tại đúng chỗ HTTP được dịch thành ý nghĩa:

| HTTP | Nghĩa | Recovery |
|---|---|---|
| 404 / "Could not find configuration" | resource thật sự không còn | **lost** |
| 401 / 403 | auth-config incident | deferred |
| 429 | backpressure | deferred |
| 5xx / transport | engine không trả lời | deferred |
| 400 / 422 | request sai | deferred |

Ma trận PM yêu cầu, chạy trên Postgres thật:

```text
running      -> RUNNING    terminal    -> RUNNING
notfound     -> FAILED     unauthorized-> RUNNING
forbidden    -> RUNNING    ratelimited -> RUNNING
server       -> RUNNING    timeout     -> RUNNING
malformed    -> RUNNING    never       -> FAILED
counts: adopted=2 lost=2 deferred=6
```

`resource_exists()` và `reconcile()` cũng siết theo cùng nguyên tắc: chỉ
confirmed-absence trả `False`; mọi thứ khác thành "engine không trả lời".

## 5. P0-CORE-002 — schema drift

PM tái hiện: `alembic current` = head, `alembic check` = FAILED thiếu
`ix_connector_definitions_display_name`.

Hai nguyên nhân, sửa cả hai:

1. `SCHEMA_FIXUPS` chứa `DROP INDEX IF EXISTS ix_connector_definitions_display_name`
   — ngay dưới một comment nói rằng catalogue **được** browse theo tên. Câu lệnh
   làm ngược lại chính comment của nó. Đã xóa.
2. `apply_schema_fixups()` chạy sau `alembic upgrade head` trên **mọi** boot.
   Database đã versioned thì Alembic sở hữu schema; DDL tay chạy sau migration
   chính là cách schema thật khác với lịch sử migration. Fixups giờ chỉ còn trên
   một đường: adopt database có bảng nhưng chưa có migration history.

Sửa code không tự phục hồi index đã bị xóa ở các deployment hiện có — baseline
đã chạy rồi nên không gì tạo lại nó. Thêm revision `f2c0a15b8e37` tạo lại bằng
`CREATE INDEX IF NOT EXISTS`.

Trên chính database demo đang chạy:

```text
trước:  head/current = e1b93c7a4d22, alembic check FAILED (added index ...display_name)
sau :   head/current = f2c0a15b8e37, drift: none, indexes: all declared indexes exist
```

CI giờ chạy `python -m app.bootstrap` (đường production thật) rồi
`scripts/check-schema-drift.py` — kiểm head, `alembic check`, **và** đối chiếu
từng index model khai báo với `pg_indexes`. Gọi Alembic trực tiếp không bao giờ
thấy được lỗi này.

## 6. P0-CORE-001 — session invalidation và policy

Hai lỗ hổng PM chỉ ra:

1. Bootstrap password chỉ bị so với đúng chuỗi demo. Giờ chạy `password_problems()`
   đầy đủ, và email đi qua `validate_email()` trước khi tạo user.
2. `change_password()` đổi hash, clear flag, và có một comment nói session cũ đã
   bị vô hiệu — **không có dòng code nào làm việc đó**. Hai người dùng chung
   one-time secret: người thứ nhất đổi password xong, token của người thứ hai
   lập tức có full quyền vì flag vừa được clear.

Đã thêm `users.session_version` + `password_changed_at`; token mang `sv`;
`current_user` so sánh và trả 401 `SESSION_REVOKED` nếu lệch. `change_password`
tăng `session_version` và **phát cookie mới** cho chính người gọi.

Test thật: hai token phát từ cùng một secret, sau khi đổi password cả hai đều
lệch version, token mới thì hợp lệ.

### Một lỗi tôi tự gây ra và phải sửa lại

Ban đầu tôi thêm hai cột vào revision `d4a1f07c2b18` — revision **đã chạy** trên
database demo. Alembic thấy version row và bỏ qua, nên cột không bao giờ được
tạo; container migrate chết với `column users.session_version does not exist`.
Đã tách sang revision riêng `e1b93c7a4d22`. Không sửa migration đã apply.

## 7. Verification

```text
pytest                                  233 passed, 17 skipped   (trước: 226/12)
RUN_CORE_LIVE=1 live Postgres           5 passed
  - production bootstrap: 0 default credentials
  - 20 concurrent insert -> 1 run
  - second active run refused
  - recovery matrix 10 trường hợp
  - session revocation
render tests (kubectl thật)             5 passed
20 concurrent API trigger cùng key      20 x HTTP 202, 1 distinct run id
alembic drift trên DB demo              head, no drift, all indexes present
npm audit --omit=dev                    0 vulnerabilities
pip-audit --strict                      No known vulnerabilities found
demo /readyz, /readyz?deep=1            200 / 200
```

## 8. Còn lại

Sprint A.1 đóng bốn finding mở lại. **Không** đóng được:

| | |
|---|---|
| P0-PLAT-001 | chart V2 2.0.17, auth-enabled, production topology — cần cluster |
| P1 (8 mục) | timeout ownership, saga/outbox, backup provider, rollback/status exit code, doctor DB verify, connector selector, dotenv parser, image slimming |
| Sprint B | evidence v2, digest/SBOM/signing, internal mirror, air-gapped clean-room |
| Sprint C | production-shaped rehearsal |
| Sprint D | LIC-001, on-call, launch scope, GO review |

Cancel vẫn chưa được chứng minh trong e2e — sync xong trước khi cancel kịp có
hiệu lực, và e2e từ chối tính đó là bằng chứng. Đó là hành vi đúng của công cụ,
không phải bằng chứng cho tính năng.

---

# PM review v11 - chốt đường ra production sớm (2026-08-24)

## 1. Kết luận điều hành

Vòng này không mở thêm một cuộc audit lỗi tổng quát. Bốn finding của PM v10 đã
được kiểm tra lại ở mức hành vi và **được chấp nhận đóng**:

```text
backend tests                         233 passed, 17 skipped
real-kubectl production render        5 passed
live Postgres core matrix             5 passed
schema on the running demo DB         head f2c0a15b8e37, no drift,
                                      all declared indexes present
```

PM không chạy lại toàn bộ Airbyte E2E trong vòng này vì không có thay đổi adapter
happy-path mới; evidence Airbyte trước đó vẫn có giá trị cho core adapter. Cancel
và production topology vẫn phải được chứng minh trong release candidate mới.

**Quyết định:** core đủ để feature-freeze và bước vào production-shaped
rehearsal. Sản phẩm **chưa GO production hôm nay**, nhưng cũng không cần đóng toàn
bộ tám P1 rồi mới được pilot. Hướng ngắn nhất là một **controlled production
pilot**, không phải GA và không phải lời hứa hỗ trợ toàn bộ catalogue.

## 2. Năm gate duy nhất chặn production pilot

| Gate | Bằng chứng bắt buộc trên đúng release candidate |
|---|---|
| PILOT-G1 Target topology | AppBI + Airbyte chạy trên target Kubernetes; Airbyte app 1.8.5 dùng Helm chart V2 2.0.17; auth, TLS, external Postgres riêng, Redis/object storage và CNI enforcing đều bật. Không dùng Compose hoặc NodePort làm bằng chứng production. |
| PILOT-G2 Reproducible release | Product images, Airbyte platform images, chart và connector trong launch scope nằm ở registry/artifact store nội bộ, pin bằng digest. Evidence v2 bind product digest, chart/app version, rendered-manifest hash, workspace id và run ids. Một Linux runner thứ hai install được khi public GitHub/OCI bị block. |
| PILOT-G3 Recoverability | Backup/restore **cặp** AppBI DB + Airbyte DB/state trên target topology; decrypt credential sau restore; redeploy previous product digest; reconcile xong không có mapping mồ côi hoặc foreign ngoài danh sách đã duyệt. |
| PILOT-G4 Bounded execution + operator | Một sync cố ý chạy lâu chứng minh timeout ownership, cancel thật, worker restart và engine outage/recovery. Alert phải tới primary + backup có tên. `production.py status` phải exit non-zero khi health/deep-readiness fail. |
| PILOT-G5 Business scope | Legal/owner clear `LIC-001` bằng văn bản cho đúng delivery model. Chốt user đầu tiên, support window, data/concurrency cap và connector allowlist. |

Đây là các gate có thể làm mất khả năng triển khai, phục hồi, kiểm soát job hoặc
quyền thương mại. Không thêm issue nhỏ vào danh sách này nếu nó không thay đổi
quyết định GO/NO-GO.

## 3. Phạm vi pilot PM khuyến nghị

1. Một production environment, một nhóm nội bộ hoặc 1-3 design partners.
2. Golden path đầu tiên: `source-postgres` -> `destination-postgres`.
3. `source-faker` chỉ là test fixture, không tính là production connector.
4. Concurrency thấp, quota dữ liệu rõ, maintenance window rõ và chưa cam kết
   BA SLO 99.9% cho đến khi có số liệu vận hành thật.
5. Không auto-upgrade Airbyte hoặc connector. Mọi version được pin và đi qua
   staging/release evidence mới.
6. Nếu khách đầu tiên cần BigQuery, Snowflake hoặc connector khác, chọn đúng
   connector đó làm Tier 1 kế tiếp và certify; không mở 651 connector còn lại.

BA đã nói rõ V1 cần top 3-5 connector gần production, không phải mọi entry trong
catalogue. Vì vậy pilot một golden path là cách học nhanh; GA chỉ diễn ra sau
khi có 3-5 connector thực sự cần cho thị trường và có evidence riêng.

## 4. Phân loại lại backlog còn mở

### Làm trước pilot

| Hạng mục cũ | Phần thật sự cần trước pilot |
|---|---|
| P0-PLAT-001 | Helm chart V2 + auth-enabled + exact production topology rehearsal. |
| P1-RUN-001 | Timeout owner rõ, cancel engine, terminal state đúng và test job treo. |
| P1-OPS-001 | Production backup provider hoặc managed snapshot workflow cho cả hai hệ thống; paired restore drill. Không bắt buộc mọi provider được code sẵn. |
| P1-OPS-002 | `status` trả exit code đúng và previous-digest redeploy được chạy thật. Automatic rollback button chưa bắt buộc. |
| P1-NET-001 | Trên target CNI, connector vẫn sync được nhưng không gọi được Config API nếu không có credential, không chạm metadata/private ranges ngoài allowlist. |
| P1-IMG-001 + Sprint B | Pin digest, internal mirror, image scan có policy và evidence v2. Slim image và full signing có thể theo sau. |
| Sprint D | Legal, primary/backup operator, alert delivery và launch scope. |

### Có thể làm sau pilot, trước broad GA

| Hạng mục | Lý do có thể hoãn có kiểm soát |
|---|---|
| P1-SAGA-001 full outbox/saga | Pilot ít tenant có thể dùng manual reconcile + orphan inventory sau mỗi create/update và không auto-retry mutation. Trước GA phải có idempotent adopt/compensate vì thao tác tăng lên sẽ vượt khả năng vận hành tay. |
| Automatic rollback | Pilot cho phép restore + redeploy theo runbook đã drill. GA nên tự động hóa phần không cần quyết định dữ liệu. |
| Doctor gọi DB-role verify | Trước pilot chạy `provision-db.py --verify` như preflight bắt buộc; sau đó tích hợp vào `doctor` để không còn bước tay. |
| P1-PORT-001 dotenv parser | Production Kubernetes không dùng `.env` làm contract. Sửa trước khi hỗ trợ single-host operator như một deployment production. |
| Runtime image slimming | Hiện production Pod non-root, không mount socket và audit dependency clean. Slim sau pilot; digest pin và image scan vẫn phải làm trước pilot. |
| Full signing/SLSA và mirror toàn dependency toolchain | Pilot cần immutable internal artifacts của đúng launch scope. Mức supply-chain cao hơn và full air-gapped build là gate trước broad GA. |

Biện pháp hoãn saga chỉ hợp lệ khi pilot nhỏ. Nếu launch ngay cho nhiều tenant,
cho phép retry tự động create/update hoặc không có người chạy orphan review, mục
này tự động quay lại thành blocker.

## 5. “Một file run production” nên có nghĩa gì

Không gom AppBI, Airbyte, database và object storage thành một Compose lớn chỉ
để Docker Desktop trông ít service. Airbyte hiện được thiết kế để deploy trên
Kubernetes bằng Helm; tài liệu hiện tại dùng chart repo V2 `/charts`, và Helm
chart version không đồng nhất với Airbyte app version:
[official deployment guide](https://github.com/airbytehq/airbyte/blob/master/docs/platform/deploying-airbyte/deploying-airbyte.md),
[chart V2 mapping](https://github.com/airbytehq/airbyte/blob/master/docs/platform/deploying-airbyte/chart-v2-community.mdx).

Một lệnh production đúng nghĩa là release orchestrator chạy chuỗi sau trên hạ
tầng đã được provision bằng Terraform/cloud tooling:

```text
preflight -> verify internal artifacts/digests/secrets/datastores/engine auth
          -> migrate AppBI -> rollout AppBI -> doctor
          -> golden-path sync/cancel -> record evidence -> release verdict
```

`scripts/production.py` đã là nền của entrypoint đó, nhưng hiện chưa phải
zero-touch whole-platform installer: nó coi Airbyte và managed datastores là
external dependencies. Pilot không cần phá boundary này. Release pipeline có
thể gọi Helm/Terraform trước rồi gọi `production.py`; về sau bọc hai lớp bằng
một top-level command hoặc GitOps workflow.

## 6. Airbyte version và rủi ro upstream

Không chuyển sang `latest` và cũng không đổi app version chỉ vì có bản mới.
Đường ít rủi ro nhất là giữ Airbyte app 1.8.5 đã qua adapter contract, đổi đúng
packaging sang chart V2 2.0.17 rồi certify lại. Tài liệu Airbyte hiện nêu chart
V1 không còn được hỗ trợ từ Airbyte 2.1 và deployment mới phải dùng V2.

Nếu GitHub hoặc public registry không còn truy cập được, runtime đang chạy có
thể tiếp tục, nhưng fresh install/DR sẽ fail nếu chart/image chỉ tồn tại upstream.
Vì vậy mirror **đúng platform và connector launch scope**, không mirror toàn 654
connector. Lưu chart package, image digests, source/NOTICE/LICENSE và evidence
trong artifact store nội bộ.

License là gate kinh doanh, không phải bug dev. Airbyte công bố phần lớn repo và
connectors theo ELv2, protocol theo MIT; FAQ nói rõ giới hạn quanh managed ELT/ETL
service và việc expose trực tiếp UI/API. PM không tự suy diễn delivery model này
hợp lệ: legal phải quyết định:
[official license summary](https://github.com/airbytehq/airbyte/blob/master/docs/community/licenses/README.md),
[official ELv2 FAQ](https://github.com/airbytehq/airbyte/blob/master/docs/community/licenses/license-faq.md).

## 7. Hai release candidate thay cho thêm nhiều sprint

### RC1 - production rehearsal

1. Infrastructure/DevOps dựng target topology và internal registry.
2. Dev đóng phần tối thiểu: timeout/cancel, status exit code, evidence v2 và
   digest-based release inputs.
3. Chạy install/upgrade/outage/restart/cancel/restore trên đúng topology.
4. Không sửa FE hoặc thêm connector trong RC1.

### RC2 - controlled pilot

1. Sửa đúng finding phát sinh từ RC1 nếu nó thuộc năm pilot gates.
2. Legal clear, assign primary/backup, test pager.
3. Cài RC2 từ clean Linux runner qua internal artifacts.
4. Chạy golden path, record evidence, freeze digest và mở pilot.

## 8. PM acceptance checklist cho GO pilot

```text
[ ] LIC-001 CLEARED cho delivery model thực tế
[ ] Airbyte app/chart/auth + AppBI topology đúng target và được pin
[ ] clean runner cài được khi public upstream bị block
[ ] source-postgres -> destination-postgres full + incremental = đúng row count
[ ] long-running sync timeout/cancel thật; restart không duplicate/lost
[ ] paired restore + credential decrypt + reconcile pass
[ ] previous digest redeploy pass
[ ] connector cannot read Config API/private network outside allowlist
[ ] primary + backup nhận alert và dùng runbook thành công
[ ] release evidence bind digest/config/workspace/run ids và gate PASS
```

**PM decision v11:** chưa GO ngay hôm nay. GO có điều kiện cho controlled pilot
ngay khi mười checkbox trên pass. Không chờ support mọi connector, không copy/fork
Airbyte backend, không nâng version tự phát và không để backlog cải tiến sau pilot
làm chậm năm gate quyết định ra mắt.

---

# Dev — RC1 dev scope against PM v11 (2026-08-24)

PM v11 giao đúng bốn việc cho dev trong RC1: **timeout/cancel, status exit code,
evidence v2, digest-based release inputs**. Không sửa FE, không thêm connector.
Dưới đây là bốn việc đó cộng phần tooling cho PILOT-G2 mà dev làm được không cần
cluster.

## 1. PILOT-G4 — timeout ownership và cancel thật

`EngineSyncRequest.timeout_seconds` được set trên mọi request và **chỉ embedded
runner đọc nó**. Airbyte không có per-job deadline để nhận, nên adapter API bỏ
qua — một sync treo sẽ ở `RUNNING` vĩnh viễn, giữ luôn active-run slot duy nhất
của pipeline, và không ai được báo.

Product sở hữu deadline nên product thực thi nó. `enforce_timeouts()` chạy trong
reconciler loop, và **thứ tự là trọng tâm**:

1. Bảo engine cancel.
2. Chỉ sau đó mới chuyển run sang `TIMED_OUT`.

Đảo thứ tự lại chính là hình dạng duplicate-write của finding worker recovery:
product tin không có gì đang chạy trong khi Airbyte vẫn ghi vào destination.

Engine không trả lời thì run **không** bị đánh terminal — thử lại vòng sau. Một
timeout không gấp đến mức đáng nói dối về trạng thái.

Đo trên Postgres thật:

```text
counts = {'timed_out': 1, 'deferred': 1}
hung         -> TIMED_OUT   và engine nhận cancel ('engine-job-1')
engine-down  -> RUNNING     (engine im lặng, không kết luận)
healthy      -> RUNNING     (còn trong deadline)
```

## 2. PILOT-G4 — `status` exit code

`status` in `FAIL` rồi return 0. Mọi thứ tiêu thụ nó — deploy pipeline, cron
check, `&&` của một người — đọc deployment hỏng thành khỏe.

```text
stack khỏe   -> exit 0
api dừng     -> NOT HEALTHY
                - /healthz answered nothing
                - /readyz answered nothing
                - /readyz?deep=1 answered nothing
             -> exit 1
```

## 3. PILOT-G2 — evidence v2 bind vào deployment

Evidence v1 ghi operation nào pass và **không ghi gì về cái nó pass trên đó**.
Release gate khi ấy bind certification vào commit đọc từ working tree của release
manager — thứ không phải deployment, và production host thì không có checkout.

Thêm build identity mà **process tự khai** (`BUILD_SHA`/`BUILD_DIGEST`/`BUILD_TIME`
là ARG của Dockerfile, phục vụ qua `/admin/compatibility`). Mặc định là
`unknown`, và gate từ chối `unknown` — một ad-hoc local build không được phép
sinh release evidence.

Evidence v2 ghi: build, engine type/version, workspace fingerprint, run ids,
connector images, mốc thời gian. Gate so bốn thứ với deployment đang release:

| Kịch bản | Gate nói |
|---|---|
| evidence khớp | no problems |
| build khác | produced against build other-build-, this deployment is running rc1-7ed1c12d |
| build `unknown` | a build with no identity; release images must be built by the pipeline |
| engine khác | produced against engine AIRBYTE_API, this deployment runs AIRBYTE_EMBEDDED |
| run ids bịa | names 1 run id(s) this deployment does not have: 00000000 |
| evidence v1 | records no deployment identity; re-run e2e |

Run ids là thứ không copy file được: chúng phải tồn tại trên chính deployment.

## 4. PILOT-G2 — mirror nội bộ theo digest

`scripts/mirror.py` với `plan | push | lock | verify`.

`plan` in đúng thứ sẽ copy và **lý do**, để scope review được trước khi kéo gì:

```text
15 artefact(s)
  product          2  (backend, frontend)
  engine-platform 10  (Airbyte 1.8.5 control plane)
  connectors       2  source-postgres:3.6.35, destination-postgres:2.4.5
  chart            1  airbyte 2.0.17
```

Hai điều cố ý:

- **Không mirror 654 connector.** Chỉ launch scope. Version lấy từ
  `connector_versions_observed` trong compatibility matrix, không phải danh sách
  thứ hai chép tay — danh sách thứ hai luôn là cái không ai đọc lại trước release.
- **Connector trong scope mà chưa certify thì plan fail.** Thử thêm
  `source-hubspot`: exit 1, `IN LAUNCH SCOPE BUT NEVER CERTIFIED`.

`lock`/`verify` dùng digest, không dùng tag: tag là con trỏ upstream di chuyển
được, và certification ghi trên một tag đã bị dời thì không certify gì cả.

## 5. Chart V2 và pilot scope vào config

```yaml
engine:
  platform_version: "1.8.5"
  chart:
    repository: oci://ghcr.io/airbytehq/helm-charts
    name: airbyte
    version: "2.0.17"
pilot:
  connectors: [source-postgres, destination-postgres]
```

App version và chart version là **hai số khác nhau** — app 1.8.5 ship dưới dạng
chart 2.0.17 — và repo chart V1 đã deprecated. Ghi cả hai là cách duy nhất để
artifact nói được cái gì đã deploy. `source-faker` cố ý vắng mặt: nó là test
fixture, mirror nó là tuyên bố nó là production connector.

## 6. Verification

```text
pytest                              239 passed, 17 skipped   (trước: 233)
RUN_CORE_LIVE=1 live Postgres       6 passed  (thêm timeout/cancel matrix)
real-kubectl render                 5 passed
evidence binding, 5 kịch bản sai    tất cả bị bắt
mirror plan (launch scope)          15 artefact, uncertified -> exit 1
status exit code                    0 khi khỏe, 1 khi api dừng
e2e embedded                        2500 records, evidence v2, build rc1-7ed1c12dfa44
```

## 7. Năm gate — trạng thái thật

| Gate | Dev đã làm | Còn thiếu |
|---|---|---|
| **G1** target topology | config pin chart V2 2.0.17 + app 1.8.5; renderer sinh manifest từ config | **Cluster thật**: AppBI + Airbyte cùng K8s, auth/TLS/CNI/external datastore. Cần infra, không phải code |
| **G2** reproducible release | evidence v2 + binding; `mirror.py` plan/push/lock/verify theo digest | Chạy `push` vào registry nội bộ thật; cài lại từ **Linux runner thứ hai** khi chặn public upstream |
| **G3** recoverability | — | Paired backup/restore AppBI + Airbyte trên target topology; redeploy previous digest. Cần G1 |
| **G4** bounded execution | timeout/cancel có evidence; `status` exit code đúng | Long-running sync **trên Airbyte thật**; worker restart giữa sync; alert tới primary + backup có tên |
| **G5** business scope | pilot scope đã vào config và được enforce | LIC-001 (legal), operator names, support window — không phải việc của dev |

Phần dev làm được không cần hạ tầng đã xong. Ba gate còn lại (G1, G3, và nửa sau
của G4) chặn ở **cluster thật + registry nội bộ**, đúng như PM v11 mô tả trong
RC1 mục 1: "Infrastructure/DevOps dựng target topology và internal registry."

---

# Dev — RC1 target-topology rehearsal (2026-08-24)

Đây là lần đầu AppBI và Airbyte được dựng trên topology mà PM yêu cầu ở
PILOT-G1: Helm **chart V2 2.0.17** (app 1.8.5), **auth bật**, **external
Postgres riêng cho từng hệ thống**, **Calico enforcing**, và **internal
registry**. Không Compose, không NodePort làm bằng chứng.

Kết quả quan trọng nhất không phải là "nó chạy". Là hai thứ chỉ lộ ra khi dựng
thật, và một trong hai là stop-ship cho pilot.

## 1. Finding P0 — adapter không xác thực được với Airbyte có auth

```text
Config API, không credential   -> 401   (auth thật sự được enforce)
Config API, HTTP Basic         -> 401   (kể cả email/password của instance admin)
```

Adapter chỉ nói **Basic**. Airbyte 1.8.5 chart V2 với `auth.enabled: true`
**không chấp nhận Basic** trên Config API. Nghĩa là trước hôm nay, product
không thể nói chuyện với một Airbyte production nào cả.

Lý do không ai bắt được: **mọi lần certify trước đều chạy với auth tắt.**
`values-certification.yaml` ghi rõ `auth.enabled: false`, và điều đó đúng cho
việc chứng minh adapter contract — nhưng nó cũng có nghĩa là đường auth chưa
bao giờ được đi qua. PM v9 đã gọi tên rủi ro này (P1-SEC "certify với auth
bật"); đây là hậu quả cụ thể.

**Đã sửa.** Adapter hỗ trợ client-credentials: POST `client_id`/`client_secret`
tới `/api/v1/applications/token`, dùng kết quả làm `Authorization: Bearer`.
Token lazy-fetch, tái sử dụng, refresh **đúng một lần** khi gặp 401 — retry vô
hạn trên credential sai sẽ đốt chính auth endpoint của deployment, một outage
tệ hơn cái 401.

Basic được giữ lại vì 0.59.x chấp nhận nó và lane certification trên Compose
vẫn chạy trên đó. `_build_auth()` chọn theo config, có test cho cả ba nhánh.

## 2. Finding P0 — bật auth trên chart V2 không phải một flag

`workload-launcher` không khởi động được:

```text
Failed to initialize data-plane
Caused by: ClientException: Client error : 401 Unauthorized
```

Nó cần `DATAPLANE_CLIENT_ID`/`DATAPLANE_CLIENT_SECRET`, và trong deployment này
chúng rỗng:

```text
DATAPLANE_CLIENT_ID           = <empty>
DATAPLANE_CLIENT_SECRET       = <empty>
application table             = 0 rows
dataplane_client_credentials  = 0 rows
```

Chart không sinh chúng. `AB_AUTH_SECRET_CREATION_ENABLED=true` có trong env
nhưng bảng vẫn rỗng. Đây là vòng lặp: cần credential để gọi API, mà API là chỗ
tạo credential — và community edition bootstrap nó qua **webapp**, thứ profile
này tắt đi vì product có UI riêng.

**Hệ quả thẳng thắn: connector job chưa chạy được trên topology này.** Không có
workload-launcher thì không có sync. G1 vì vậy **chưa đóng**.

Đây chính xác là loại rủi ro mà rehearsal sinh ra để tìm, và nó không thể tìm
được bằng cách đọc code hay chạy thêm unit test.

## 3. Những gì đã dựng và đo được

| | |
|---|---|
| Kubernetes | v1.31.0 |
| CNI | Calico v3.28.1, `disableDefaultCNI` — NetworkPolicy được **enforce**, không phải chỉ được chấp nhận |
| Helm chart | **2.0.17**, app version **1.8.5** — đúng mapping PM dẫn, xác nhận từ chính repo V2 |
| Auth | bật, và chứng minh được là đang enforce (401) |
| Airbyte DB | Postgres **external**, instance riêng, ngoài cluster, bootloader migrate xong **66 bảng** |
| AppBI DB | Postgres external **thứ hai** — ADR-001 là hai instance, không phải hai database |
| Redis | external |
| Registry | internal, `appbi/backend`, `appbi/frontend`, `appbi-mirror/source-postgres`, `appbi-mirror/destination-postgres` đã push kèm digest |
| Control plane | server, worker, temporal, cron, workload-api-server, connector-builder-server, minio: **Running**. workload-launcher: **CrashLoopBackOff** (mục 2) |

Bốn thứ phải sửa để chart V2 khởi động được, ghi lại vì lần sau sẽ gặp lại:

1. `global.secrets` **không** thêm key tuỳ ý vào secret của chart — secret có
   một tập key cố định. Trỏ `instanceAdmin.passwordSecretKey` vào một key không
   tồn tại làm bootloader chết ở `CreateContainerConfigError`.
2. Bootloader là **Helm hook**, bị xoá khi fail — nên không có gì để debug và
   không patch được sau đó. Phải `--no-hooks` rồi chạy nó như một Pod thường.
3. Database key là `name:`, không phải `database:`; và chart mặc định tên DB là
   `db-airbyte`, không phải giá trị mình đặt.
4. Temporal mặc định **bật TLS** với external Postgres:
   `pq: SSL is not enabled on the server`. Phải tắt `POSTGRES_TLS_ENABLED` /
   `SQL_TLS_ENABLED` hoặc bật SSL trên Postgres.

## 4. Trạng thái năm gate sau vòng này

| Gate | Trước | Sau |
|---|---|---|
| **G1** topology | chưa có cluster | **gần xong**: chart V2 2.0.17 + app 1.8.5 + auth enforced + external DB riêng + Calico. Còn lại: dataplane credentials để workload-launcher chạy |
| **G2** reproducible | tooling xong | **thêm**: registry nội bộ có thật, 4 image đã push kèm digest. Còn lại: clean Linux runner với upstream bị chặn |
| **G3** recoverability | chặn bởi G1 | vẫn chặn — paired restore cần một topology chạy được sync |
| **G4** bounded execution | timeout/cancel + status xong | phần trên Airbyte thật vẫn chặn bởi G1 |
| **G5** business | scope đã vào config | không đổi — legal |

## 5. Verification

```text
pytest                          240 passed, 18 skipped
RUN_CORE_LIVE=1 live Postgres   6 passed
real-kubectl render             5 passed
adapter auth (3 nhánh)          client-credentials / basic / none
chart V2 2.0.17 -> app 1.8.5    xác nhận từ repo airbytehq/charts
airbyte external DB             66 bảng sau bootloader
internal registry               4 repository
```

## 6. Đề xuất cho PM

Hai finding ở mục 1 và 2 nên được coi là **một hạng mục G1 duy nhất**: "certify
với auth bật". Mục 1 đã sửa xong phía product. Mục 2 là quyết định vận hành
Airbyte — cần chốt cách bootstrap application credentials cho community edition
không có webapp, hoặc chấp nhận bật webapp nội bộ chỉ để bootstrap rồi tắt.

Không đề nghị hạ yêu cầu auth để pilot chạy được. Chạy pilot với Config API
không xác thực sẽ làm guardrail 1 thành vô nghĩa, và đó là thứ khó sửa lại sau
khi đã có khách hàng.

---

# PM review v12 - quyết định Git và production release (2026-08-24)

## Kết luận điều hành

Dev đã làm đúng khi không tắt auth để lấy một kết quả sync xanh. Buổi rehearsal
đã tìm ra đúng một stop-ship của production topology. Tuy nhiên, **chưa đủ điều
kiện chạy production** và thư mục hiện tại cũng **chưa thể được PM xác nhận để
push/release**, vì nó không có metadata Git.

Có thể đưa code lên một branch để review sau khi khôi phục đúng repository và
remote. Không được merge/tag/deploy như production candidate cho đến khi bốn
finding P0 dưới đây đóng bằng evidence của cùng một commit và image digest.

## Finding theo mức độ ưu tiên

| Mức | Finding | Bằng chứng code/hệ thống | Điều kiện đóng |
|---|---|---|---|
| P0-REL-012 | Bearer auth mới chỉ xong trong adapter, chưa xong trên đường deploy production. | `backend/app/core/config.py` và `backend/app/adapters/airbyte_api/adapter.py` có client credentials. Tuy nhiên `deploy/production.yaml.example`, `.env.production.example`, `scripts/production.py::_secret_env`, `verify_engine`, `doctor` và `backend/app/core/readiness.py` vẫn chỉ bind/validate Basic username-password. | Config schema có mode client-credentials; secret refs render thành `AIRBYTE_CLIENT_ID`/`AIRBYTE_CLIENT_SECRET`; readiness, install, doctor, workspace/verify scripts cùng dùng một auth mode. Rendered-manifest test phải chứng minh secret đến đúng pod. |
| P0-PLAT-001 | `workload-launcher` đang CrashLoopBackOff do thiếu dataplane credentials. | `backend/evidence/rc1-topology.md` ghi `application=0`, `dataplane_client_credentials=0`; không có launcher thì không có connector job, nên golden path chưa chạy. | Bootstrap credential bằng input/Secret được chart hỗ trợ. Nếu cần, bật webapp nội bộ tạm thời để bootstrap rồi tắt. Không ghi trực tiếp vào Airbyte DB. Launcher Ready và connector pod chạy được. |
| P0-REL-013 | Không có Git provenance trong workspace đang review. | `git status` trả về `not a git repository`; trong khi `.env` tự khai `BUILD_SHA=rc1-7ed1c12dfa44`. PM không thể đối chiếu diff, commit, remote hay artifact. | Clone/attach đúng repo và remote; commit tất cả thay đổi trên branch; build image từ clean commit; `/admin/compatibility`, evidence v2 và digest cùng trỏ về commit đó; release gate chạy từ clean tree. Không commit `.env`/secret. |
| P0-CI-001 | Lane K8s hiện tại không certify target vừa rehearsal. | `.github/workflows/ci.yml` pin `AIRBYTE_CHART_VERSION: 1.8.5`, dùng chart V1 và values auth-disabled. Nó không tái hiện chart V2 `2.0.17` + app `1.8.5` + bearer auth. | CI lane pin riêng chart `2.0.17` và app `1.8.5`, bootstrap auth/dataplane, assert auth thật sự bật, và chạy 11/11 operations cùng golden path. |
| P1-AUTH-001 | Test auth chưa chứng minh protocol thật. | `backend/tests/test_production_core.py` chủ yếu kiểm tra class/tuple và source text; chưa thực thi token POST, bearer header, refresh một lần, invalid credential hay retry cap. | Thêm fake server/MockTransport test thực thi trọn luồng; sau đó certify live tất cả API read/write. Kiểm tra token hết hạn và tránh block async event loop khi fetch token. |

## Xác minh độc lập của PM

```text
git status                              FAIL: not a git repository
pytest backend/tests (env hiện tại)     239 pass, 1 fail, 18 skip
BUILD_SHA=unknown pytest backend/tests  240 pass, 18 skip
RUN_CORE_LIVE=1 live Postgres           6 pass
```

Lỗi pytest mặc định đến từ `.env` đang gán một RC1 SHA trong một thư mục không có
Git, trong khi test mong `unknown`. Đây không phải lỗi core, nhưng là bằng chứng
rằng release identity chưa được tạo bằng pipeline đáng tin cậy.

## Quyết định về dataplane credential

PM chốt hướng vận hành: **không seed trực tiếp các bảng nội bộ của Airbyte**. Ưu
tiên Kubernetes Secret + chart values được upstream hỗ trợ cho application/admin
và dataplane client credentials. Nếu phiên bản community bắt buộc tạo credential
qua UI, webapp chỉ được bật nội bộ trong bước bootstrap có runbook, không expose
cho khách hàng, sau đó tắt và lưu credential vào secret manager. Cách này giữ
được ranh giới ADR-001 và tránh phụ thuộc schema DB nội bộ của Airbyte.

## Thứ tự ngắn nhất để release

1. Khôi phục đúng Git repository/remote, tạo branch RC, không đưa `.env` hay
   secret vào commit.
2. Wire client credentials end-to-end qua production config, Secret, pod,
   readiness, `verify_engine`, `doctor` và các script vận hành.
3. Bootstrap dataplane credential theo supported path; chứng minh launcher Ready
   và connector pod khởi tạo thành công.
4. Nâng CI lane lên chart V2 `2.0.17`, auth enabled; chạy 11/11 và golden path
   Postgres full/incremental/cancel trên image build từ commit RC.
5. Trên cùng topology, đóng G2 clean-runner/upstream-blocked, G3 paired restore,
   G4 long-sync/restart/alert và G5 `LIC-001` + on-call owner.
6. Record evidence v2, release gate xanh, tag bất biến và deploy theo digest.

Không cần quay lại săn lỗi nhỏ tổng quát. Sáu bước trên là đường trực tiếp tới
pilot production; mỗi finding đều có khả năng làm deployment không khởi động,
không chạy được connector, hoặc không thể tái lập trên máy khác.
