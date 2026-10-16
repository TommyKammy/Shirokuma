select orderkey, partkey, suppkey, linenumber, quantity, extendedprice,
       discount, tax, returnflag, linestatus, shipdate
from {{ source('tpch_tiny', 'lineitem') }}
