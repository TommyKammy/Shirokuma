with items as (
    select orderkey,
           sum(extendedprice * (1 - discount)) as net_item_sales_amount,
           count(*) as total_items
    from {{ ref('stg_tpch_lineitem') }}
    group by orderkey
)
select o.orderkey, o.custkey, o.orderdate, o.orderstatus, o.totalprice,
       coalesce(i.net_item_sales_amount, 0) as net_item_sales_amount,
       coalesce(i.total_items, 0) as total_items
from {{ ref('stg_tpch_orders') }} o
left join items i on o.orderkey = i.orderkey
