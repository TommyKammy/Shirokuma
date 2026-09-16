select count(*) as actual_rows
from {{ ref('mart_fixture') }}
having count(*) <> {{ 3 if var('exercise_failure', false) else 2 }}
