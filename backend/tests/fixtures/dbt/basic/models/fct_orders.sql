{{ config(materialized='table') }}

select order_id, amount from {{ ref('stg_orders') }}
