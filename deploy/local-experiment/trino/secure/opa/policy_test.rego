package shirokuma_test

import rego.v1

users := {"shirokuma_admin", "shirokuma_engineer", "shirokuma_viewer"}
procedures := {"rollback_to_snapshot", "set_current_snapshot", "optimize", "expire_snapshots", "arbitrary_future_procedure"}

request(user, operation, catalog, schema, procedure) := {
    "context": {"identity": {"user": user}},
    "action": {"operation": operation, "resource": {
        "table": {"catalogName": catalog, "schemaName": schema, "tableName": "orders"},
        "function": {"functionName": procedure},
    }},
}

test_known_users_normal_pass if {
    every user in users {
        every operation in ["SelectFromColumns", "InsertIntoTable", "CreateSchema", "FutureOperation"] {
            data.shirokuma.allow with input as request(user, operation, "finance", "marts", "")
        }
    }
}

test_unknown_user_denied if {
    every operation in ["SelectFromColumns", "ExecuteTableProcedure"] {
        not data.shirokuma.allow with input as request("unknown", operation, "iceberg", "raw", "rollback_to_snapshot")
    }
}

test_admin_table_procedures if {
    every catalog in ["iceberg", "finance"] {
        every schema in ["raw", "sandbox", "marts", "other"] {
            every procedure in procedures {
                data.shirokuma.allow with input as request("shirokuma_admin", "ExecuteTableProcedure", catalog, schema, procedure)
            }
        }
    }
}

test_admin_other_catalog_denied if {
    every catalog in ["system", "other", ""] {
        not data.shirokuma.allow with input as request("shirokuma_admin", "ExecuteTableProcedure", catalog, "raw", "optimize")
    }
}

test_engineer_raw_sandbox_allowed if {
    every schema in ["raw", "sandbox"] {
        every procedure in procedures {
            data.shirokuma.allow with input as request("shirokuma_engineer", "ExecuteTableProcedure", "iceberg", schema, procedure)
        }
    }
}

test_engineer_other_schema_denied if {
    every schema in ["marts", "staging", "tpch_tiny", "other", ""] {
        every procedure in procedures {
            not data.shirokuma.allow with input as request("shirokuma_engineer", "ExecuteTableProcedure", "iceberg", schema, procedure)
        }
    }
}

test_engineer_other_catalog_denied if {
    every catalog in ["finance", "system", "other", ""] {
        every schema in ["raw", "sandbox", "marts"] {
            every procedure in procedures {
                not data.shirokuma.allow with input as request("shirokuma_engineer", "ExecuteTableProcedure", catalog, schema, procedure)
            }
        }
    }
}

test_viewer_table_procedures_denied if {
    every catalog in ["iceberg", "finance", "other"] {
        every schema in ["raw", "sandbox", "marts", "other"] {
            every procedure in procedures {
                not data.shirokuma.allow with input as request("shirokuma_viewer", "ExecuteTableProcedure", catalog, schema, procedure)
            }
        }
    }
}

test_missing_identity_or_table_denied if {
    not data.shirokuma.allow with input as {}
    every user in users {
        not data.shirokuma.allow with input as {"context": {"identity": {"user": user}}, "action": {"operation": "ExecuteTableProcedure"}}
    }
}

test_api_allow if {
    data.system.authz.allow with input as {"method": "POST", "path": ["v1", "data", "shirokuma", "allow"]}
    data.system.authz.allow with input as {"method": "GET", "path": ["health"]}
}

test_api_deny if {
    every method in ["GET", "POST", "PUT", "PATCH", "DELETE"] {
        every path in [[], ["v1", "data"], ["v1", "policies"], ["v1", "policies", "guard"], ["v1", "query"], ["v1", "data", "shirokuma"], ["v1", "data", "shirokuma", "mask"], ["v1", "data", "shirokuma", "allow", "extra"]] {
            not data.system.authz.allow with input as {"method": method, "path": path}
        }
    }
    every method in ["GET", "PUT", "PATCH", "DELETE"] {
        not data.system.authz.allow with input as {"method": method, "path": ["v1", "data", "shirokuma", "allow"]}
    }
    every method in ["POST", "PUT", "PATCH", "DELETE"] {
        not data.system.authz.allow with input as {"method": method, "path": ["health"]}
    }
}
