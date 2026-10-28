package database_poc

import rego.v1

# Auxiliary guard only: every request must also pass the unchanged file ACL.
default allow := false

known_users := {"poc_admin", "sales_engineer", "sales_viewer", "finance_engineer", "finance_viewer"}

allow if {
    input.context.identity.user in known_users
    input.action.operation != "ExecuteTableProcedure"
}

allow if {
    input.context.identity.user == "poc_admin"
    input.action.operation == "ExecuteTableProcedure"
    input.action.resource.table.catalogName in {"sales", "finance"}
}
