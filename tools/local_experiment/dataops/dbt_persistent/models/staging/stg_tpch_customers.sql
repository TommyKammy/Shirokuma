select custkey, name, address, nationkey, phone, acctbal, mktsegment
from {{ source('tpch_tiny', 'customer') }}
