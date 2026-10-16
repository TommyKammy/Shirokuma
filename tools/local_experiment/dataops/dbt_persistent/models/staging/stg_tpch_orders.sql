select orderkey, custkey, orderstatus, totalprice, orderdate, orderpriority
from {{ source('tpch_tiny', 'orders') }}
