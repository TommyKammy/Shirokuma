package system.authz

import rego.v1

default allow := false

allow if {
    input.method == "POST"
    input.path == ["v1", "data", "database_poc", "allow"]
}

allow if {
    input.method == "GET"
    input.path == ["health"]
}
