package shirokuma

import rego.v1

# Auxiliary guard: every request must also pass the unchanged file ACL.
default allow := false

known_users := {"shirokuma_admin", "shirokuma_engineer", "shirokuma_viewer"}

allow if {
    input.context.identity.user in known_users
    input.action.operation != "ExecuteTableProcedure"
}

allow if {
    input.context.identity.user == "shirokuma_admin"
    input.action.operation == "ExecuteTableProcedure"
    input.action.resource.table.catalogName in {"iceberg", "finance"}
}

allow if {
    input.context.identity.user == "shirokuma_engineer"
    input.action.operation == "ExecuteTableProcedure"
    input.action.resource.table.catalogName == "iceberg"
    input.action.resource.table.schemaName in {"raw", "sandbox"}
}
