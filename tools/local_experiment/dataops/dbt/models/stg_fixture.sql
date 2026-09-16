{{ config(alias=var('relation_prefix') ~ '_stg') }}

select cast(id as bigint) as id, cast(name as varchar) as name
from {{ source('retained_smoke', 'fixture_v1') }}
