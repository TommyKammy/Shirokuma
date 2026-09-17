package trino

import rego.v1

# These names are synthetic client-selected headers, NOT authenticated identities.
users := {"lab_admin", "lab_row", "lab_mask", "lab_both"}
default allow := false

allow if {
    input.context.identity.user in users
    input.action.operation == "ExecuteQuery"
}

allow if {
    input.context.identity.user in users
    input.action.operation == "AccessCatalog"
    input.action.resource.catalog.name == "tpch"
}

allow if {
    input.context.identity.user in users
    input.action.operation == "SelectFromColumns"
    table := input.action.resource.table
    table.catalogName == "tpch"
    table.schemaName == "tiny"
    table.tableName == "nation"
    every column in table.columns { column in {"nationkey", "name"} }
}

rowFilters := [{"expression": "nationkey = 0"}] if {
    input.context.identity.user in {"lab_row", "lab_both"}
    table := input.action.resource.table
    table.catalogName == "tpch"
    table.schemaName == "tiny"
    table.tableName == "nation"
} else := []

columnMask := {"expression": "'MASKED'"} if {
    input.context.identity.user in {"lab_mask", "lab_both"}
    column := input.action.resource.column
    column.catalogName == "tpch"
    column.schemaName == "tiny"
    column.tableName == "nation"
    column.columnName == "name"
}
