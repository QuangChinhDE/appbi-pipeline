{{ config(materialized='view') }}

select 1 as order_id, 100 as amount, 'paid' as status
