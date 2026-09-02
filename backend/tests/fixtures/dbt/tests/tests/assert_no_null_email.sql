-- A singular test: passes when it returns no rows.
select id from {{ ref('customers') }} where email is null
