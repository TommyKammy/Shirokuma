select c.custkey, c.name as customer_name, n.name as nation_name,
       r.name as region_name, c.acctbal, c.mktsegment
from {{ ref('stg_tpch_customers') }} c
join {{ source('tpch_tiny', 'nation') }} n on c.nationkey = n.nationkey
join {{ source('tpch_tiny', 'region') }} r on n.regionkey = r.regionkey
