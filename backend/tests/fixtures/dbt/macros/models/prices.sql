select {{ cents_to_dollars('amount_cents') }} as amount
from (select 1999 as amount_cents) as raw
