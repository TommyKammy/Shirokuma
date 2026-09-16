select id, name_upper
from {{ ref('mart_fixture') }}
where (id = 1 and name_upper <> 'SHIROKUMA')
   or (id = 2 and name_upper <> 'POLARIS')
   or id not in (1, 2)
