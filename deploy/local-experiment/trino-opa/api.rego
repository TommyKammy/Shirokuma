package system.authz

import rego.v1

# Only the three fixed decision APIs are exposed on same-Pod loopback.
# Policy/data administration endpoints remain denied.
default allow := false

allow if {
    input.method == "POST"
    input.path in {
        ["v1", "data", "trino", "allow"],
        ["v1", "data", "trino", "rowFilters"],
        ["v1", "data", "trino", "columnMask"],
    }
}

allow if {
    input.method == "GET"
    input.path == ["health"]
}
