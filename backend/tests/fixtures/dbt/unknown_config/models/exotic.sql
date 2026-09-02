{{ config(
    materialized='incremental',
    unique_key='order_id',
    on_schema_change='append_new_columns',
    contract={'enforced': false},
    some_future_config='preserve me',
    meta={'owner': 'finance', 'sla_hours': 4},
    persist_docs={'relation': true, 'columns': true}
) }}

select 1 as order_id, 2 as amount
