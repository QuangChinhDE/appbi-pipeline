# RC1 target topology - observed 2026-08-24T10:52:52Z

## What is running
kubernetes  : v1.31.0
cni         : calico v3.28.1, disableDefaultCNI -- NetworkPolicy is enforced, not merely accepted
chart       : airbyte-2.0.17  (app 1.8.5) status deployed
auth        : ENABLED -- Config API answers 401 without credentials
airbyte db  : external Postgres, own instance, outside the cluster (66 tables migrated)
appbi db    : external Postgres, a SECOND instance (ADR-001)
registry    : internal, images pushed by digest

  airbyte-connector-builder-server-9975d74fb-hlcxm 1/1    Running
  airbyte-cron-67f89c9798-dhvsj                  1/1    Running
  airbyte-minio-0                                1/1    Running
  airbyte-server-67447665fc-4mdmd                1/1    Running
  airbyte-temporal-678d47f7bf-dnmkv              1/1    Running
  airbyte-webapp-76964f798-9gtdb                 0/1    ErrImagePull
  airbyte-worker-6bd64c6677-wdzhf                1/1    Running
  airbyte-workload-api-server-766b6d7cb4-pmch9   1/1    Running
  airbyte-workload-launcher-744457775b-xcfqx     0/1    CrashLoopBackOff

## Measured
  Config API, no credentials    -> 401   (auth is genuinely enforced)
  Config API, HTTP Basic        -> 401   (1.8.5 does NOT accept Basic)
  /api/v1/applications/token    -> 400 'Invalid client id or token'
  application table             -> 0 rows
  dataplane_client_credentials  -> 0 rows

## Internal registry
  - appbi/backend
  - appbi/frontend
  - appbi-mirror/destination-postgres
  - appbi-mirror/source-postgres
