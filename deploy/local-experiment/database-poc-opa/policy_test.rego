package database_poc_test

import rego.v1

users := {"poc_admin", "sales_engineer", "sales_viewer", "finance_engineer", "finance_viewer"}

request(user, operation, catalog, schema, procedure) := {
    "context": {"identity": {"user": user}},
    "action": {"operation": operation, "resource": {
        "table": {"catalogName": catalog, "schemaName": schema, "tableName": "orders"},
        "function": {"functionName": procedure},
    }},
}

test_known_users_normal_pass if {
    every user in users {
        data.database_poc.allow with input as request(user, "SelectFromColumns", "sales", "raw", "")
    }
}

test_unknown_denied if {
    every operation in ["SelectFromColumns", "ExecuteTableProcedure"] {
        not data.database_poc.allow with input as request("unknown", operation, "sales", "raw", "rollback_to_snapshot")
    }
}

test_admin_table_procedures if {
    every catalog in ["sales", "finance"] {
        data.database_poc.allow with input as request("poc_admin", "ExecuteTableProcedure", catalog, "marts", "rollback_to_snapshot")
    }
}

test_admin_other_catalog_denied if {
    every catalog in ["system", "iceberg", "other", ""] {
        not data.database_poc.allow with input as request("poc_admin", "ExecuteTableProcedure", catalog, "raw", "optimize")
    }
}

test_nonadmin_table_procedures_denied if {
    every user in users - {"poc_admin"} {
        every catalog in ["sales", "finance"] {
            every schema in ["raw", "sandbox", "marts", "staging"] {
                every procedure in ["rollback_to_snapshot", "set_current_snapshot", "optimize", "expire_snapshots", "arbitrary_future_procedure"] {
                    not data.database_poc.allow with input as request(user, "ExecuteTableProcedure", catalog, schema, procedure)
                }
            }
        }
    }
}

test_api_allow if {
    data.system.authz.allow with input as {"method": "POST", "path": ["v1", "data", "database_poc", "allow"]}
    data.system.authz.allow with input as {"method": "GET", "path": ["health"]}
}

test_api_deny if {
    every method in ["GET", "POST", "PUT", "PATCH", "DELETE"] {
        every path in [[], ["v1", "data"], ["v1", "policies"], ["v1", "policies", "guard"], ["v1", "query"], ["v1", "data", "database_poc"], ["v1", "data", "database_poc", "mask"], ["v1", "data", "database_poc", "allow", "extra"]] {
            not data.system.authz.allow with input as {"method": method, "path": path}
        }
    }
    every method in ["GET", "PUT", "PATCH", "DELETE"] {
        not data.system.authz.allow with input as {"method": method, "path": ["v1", "data", "database_poc", "allow"]}
    }
    every method in ["POST", "PUT", "PATCH", "DELETE"] {
        not data.system.authz.allow with input as {"method": method, "path": ["health"]}
    }
}
