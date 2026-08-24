# PILOT-G1 target topology - COMPLETE  (2026-08-24T15:06:34Z)

kubernetes : v1.31.0
cni        : calico v3.28.1, disableDefaultCNI -- NetworkPolicy enforced
chart      : airbyte-2.0.17  (app 1.8.5)  deployed
auth       : ENABLED -- Config API answers 401 without credentials
database   : jdbc:postgresql://172.25.0.3:5432/airbyte (external, outside the cluster)
appbi db   : a SECOND external Postgres instance (ADR-001)
in-cluster postgres: none -- postgresql.enabled=false

## Control plane
  airbyte-connector-builder-server-6bff9d9fd6-d2j88 1/1    Running
  airbyte-cron-586794d74d-lswk2                  1/1    Running
  airbyte-minio-0                                1/1    Running
  airbyte-server-55bdb8d4f9-8vddt                1/1    Running
  airbyte-temporal-68cd9c485-7wc64               1/1    Running
  airbyte-worker-58d677f86c-rgkr7                1/1    Running
  airbyte-workload-api-server-97bc78f94-qdnp8    1/1    Running
  airbyte-workload-launcher-84774c978b-n72qq     1/1    Running

## Measured
  Config API without credentials      -> 401
  external database tables            -> 66 (migrated by the bootloader)
  workload-launcher                   -> Running (connector jobs can be launched)
