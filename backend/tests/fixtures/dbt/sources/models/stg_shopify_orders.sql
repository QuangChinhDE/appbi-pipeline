select * from {{ source('shopify', 'orders') }}
