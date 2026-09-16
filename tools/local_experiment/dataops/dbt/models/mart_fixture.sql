{{ config(alias=var('relation_prefix') ~ '_mart') }}

select id, upper(name) as name_upper
from {{ ref('stg_fixture') }}
