package trino_test

import rego.v1
import data.trino

context(user) := {"context": {"identity": {"user": user}}}
request(user, operation, resource) := object.union(context(user), {
    "action": {"operation": operation, "resource": resource},
})
table := {"catalogName": "tpch", "schemaName": "tiny", "tableName": "nation", "columns": ["nationkey", "name"]}
column := {"catalogName": "tpch", "schemaName": "tiny", "tableName": "nation", "columnName": "name"}

test_known_read if {
    trino.allow with input as request("lab_admin", "SelectFromColumns", {"table": table})
}
test_unknown_user_denied if {
    not trino.allow with input as request("lab_denied", "ExecuteQuery", {})
}
test_write_operation_denied if {
    not trino.allow with input as request("lab_admin", "DropTable", {"table": table})
}
test_comment_denied if {
    not trino.allow with input as request("lab_admin", "SelectFromColumns", {"table": object.union(table, {"columns": ["comment"]})})
}
test_other_schema_denied if {
    not trino.allow with input as request("lab_admin", "SelectFromColumns", {"table": object.union(table, {"schemaName": "sf1"})})
}
test_row_filter if {
    trino.rowFilters == [{"expression": "nationkey = 0"}] with input as request("lab_row", "GetRowFilters", {"table": table})
    trino.rowFilters == [] with input as request("lab_admin", "GetRowFilters", {"table": table})
}
test_column_mask if {
    trino.columnMask == {"expression": "'MASKED'"} with input as request("lab_mask", "GetColumnMask", {"column": column})
    not trino.columnMask with input as request("lab_admin", "GetColumnMask", {"column": column})
}
test_api_administration_denied if {
    not data.system.authz.allow with input as {"method": "PUT", "path": ["v1", "policies", "trino"]}
    not data.system.authz.allow with input as {"method": "POST", "path": ["v1", "query"]}
    data.system.authz.allow with input as {"method": "POST", "path": ["v1", "data", "trino", "allow"]}
}
