locals {
  polaris_root_client_id      = "root"
  polaris_postgresql_database = "polaris"
  polaris_postgresql_username = "polaris"
  polaris_credential_generation = yamldecode(file(
    "${path.module}/../../deploy/gitops/clusters/local-lite/polaris-runtime-generation.yaml"
  )).data.POLARIS_CREDENTIAL_GENERATION
  polaris_runtime_generation = yamldecode(file(
    "${path.module}/../../deploy/gitops/clusters/local-lite/polaris-runtime-generation.yaml"
  )).data
  legacy_polaris_databases = {
    sales = {
      realm            = "SALES"
      catalog          = "shirokuma_l1"
      prefix           = "databases/sales/"
      root_secret      = "polaris-root-credentials"
      s3_secret        = "seaweedfs-s3-sales-credentials"
      jwt_secret       = "polaris-jwt-signing-key"
      trino_secret     = "polaris-trino-credentials"
      bootstrap_secret = "polaris-iceberg-bootstrap-credentials"
      jwt_generation   = local.polaris_runtime_generation.POLARIS_SALES_JWT_KEY_GENERATION
    }
    finance = {
      realm            = "FINANCE"
      catalog          = "finance"
      prefix           = "databases/finance/"
      root_secret      = "polaris-finance-root-credentials"
      s3_secret        = "seaweedfs-s3-finance-credentials"
      jwt_secret       = "polaris-finance-jwt-signing-key"
      trino_secret     = "polaris-finance-trino-credentials"
      bootstrap_secret = "polaris-finance-iceberg-bootstrap-credentials"
      jwt_generation   = local.polaris_runtime_generation.POLARIS_FINANCE_JWT_KEY_GENERATION
    }
  }
  generation_mode      = var.warehouse_generation != null
  generation_databases = try(var.warehouse_generation.databases, {})
  generation_shared    = try(var.warehouse_generation.shared, {})
  polaris_databases = local.generation_mode ? {
    for key, db in local.generation_databases : key => {
      realm            = db.realm
      catalog          = db.catalog
      prefix           = db.prefix
      root_secret      = db.root_secret.name
      s3_secret        = db.s3_secret.name
      jwt_secret       = db.jwt_secret.name
      trino_secret     = db.trino_secret.name
      bootstrap_secret = db.bootstrap_secret.name
      jwt_generation   = db.jwt_secret.version
    }
  } : local.legacy_polaris_databases
  generation_db_roles = {
    root_secret      = ["client_id", "client_secret", "realm"]
    trino_secret     = ["client_id", "client_secret", "realm"]
    bootstrap_secret = ["client_id", "client_secret", "realm"]
    jwt_secret       = ["private.pem", "public.pem"]
    s3_secret        = ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION", "S3_ENDPOINT", "S3_BUCKET", "S3_REGION", "S3_PATH_STYLE"]
  }
  generation_shared_roles = {
    postgres_secret          = ["database", "username", "password"]
    polaris_bootstrap_secret = [for db in values(local.generation_databases) : db.bootstrap_credentials_key]
    seaweedfs_server_secret  = ["s3.json"]
    trino_auth_secret        = ["password.db", "shared-secret", "tls.pem"]
    gitops_source_secret     = ["accesskey", "secretkey"]
    operator_secret          = ["access_key", "secret_key"]
  }
  generation_refs = concat(flatten([
    for key, db in local.generation_databases : [for role, expected in local.generation_db_roles : {
      ref = db[role], keys = expected, namespace = "shirokuma-dev"
    }]
    ]), [for role, ref in local.generation_shared : {
    ref       = ref, keys = try(local.generation_shared_roles[role], []),
    namespace = contains(["seaweedfs_server_secret", "operator_secret"], role) ? "shirokuma-storage" : (role == "gitops_source_secret" ? "flux-system" : "shirokuma-dev")
  }])
  generation_ref_ids        = [for entry in local.generation_refs : "${entry.ref.namespace}/${entry.ref.name}@${entry.ref.version}"]
  generation_bootstrap_refs = try(var.warehouse_generation.bootstrap_secret_refs, [])
  generation_deferred_refs  = try(var.warehouse_generation.deferred_secret_refs, [])
  generation_bootstrap_ids  = [for ref in local.generation_bootstrap_refs : "${ref.namespace}/${ref.name}@${ref.version}"]
  generation_deferred_ids   = [for ref in local.generation_deferred_refs : "${ref.namespace}/${ref.name}@${ref.version}"]
  generation_expected_deferred_refs = local.generation_mode ? concat(
    flatten([for db in values(local.generation_databases) : [db.trino_secret, db.bootstrap_secret]]),
    [local.generation_shared.trino_auth_secret]
  ) : []
  generation_expected_deferred_ids = [for ref in local.generation_expected_deferred_refs : "${ref.namespace}/${ref.name}@${ref.version}"]
  generation_supplied_ids          = nonsensitive(keys(var.warehouse_generation_secrets))
  generation_expected_ids          = var.warehouse_generation_phase == "bootstrap" ? concat(local.generation_bootstrap_ids, [for id in local.generation_deferred_ids : id if contains(local.generation_supplied_ids, id)]) : local.generation_ref_ids
  generation_data                  = { for key, secret in var.warehouse_generation_secrets : key => secret.data }
  generation_db_data = { for key, db in local.generation_databases : key => {
    for role in keys(local.generation_db_roles) : role => try(local.generation_data["${db[role].namespace}/${db[role].name}@${db[role].version}"], {})
  } }
  generation_shared_data = { for role, ref in local.generation_shared : role => try(local.generation_data["${ref.namespace}/${ref.name}@${ref.version}"], {}) }
  generation_annotations = local.generation_mode ? {
    "shirokuma.dev/generation-id"       = var.warehouse_generation.generation_id
    "shirokuma.dev/database-generation" = var.warehouse_generation.generation_id
  } : {}
  polaris_root_entries = {
    for key, database in local.legacy_polaris_databases : database.realm => {
      "client-id"     = local.polaris_root_client_id
      "client-secret" = try(var.polaris_database_credentials[key].root_client_secret, null)
    }
  }
}

resource "kubernetes_secret_v1" "polaris_postgresql_credentials" {
  count = local.generation_mode ? 0 : 1
  metadata {
    name      = "polaris-postgresql-credentials"
    namespace = kubernetes_namespace_v1.dev.metadata[0].name
    annotations = {
      "shirokuma.dev/polaris-credential-generation" = local.polaris_credential_generation
    }
    labels = {
      "app.kubernetes.io/name"       = "polaris-postgresql"
      "app.kubernetes.io/component"  = "catalog-metadata"
      "app.kubernetes.io/part-of"    = "shirokuma"
      "app.kubernetes.io/managed-by" = "OpenTofu"
    }
  }

  data = {
    database = local.polaris_postgresql_database
    username = local.polaris_postgresql_username
    password = var.polaris_postgresql_password
  }

  type = "Opaque"

  lifecycle {
    ignore_changes = [data]
  }
}

moved {
  from = kubernetes_secret_v1.polaris_postgresql_credentials
  to   = kubernetes_secret_v1.polaris_postgresql_credentials[0]
}

resource "kubernetes_secret_v1" "polaris_generation_postgresql_credentials" {
  count = local.generation_mode ? 1 : 0
  metadata {
    name      = local.generation_shared.postgres_secret.name
    namespace = local.generation_shared.postgres_secret.namespace
    annotations = merge(local.generation_annotations, {
      "shirokuma.dev/secret-version" = local.generation_shared.postgres_secret.version
    })
    labels = {
      "app.kubernetes.io/name"       = "polaris-postgresql"
      "app.kubernetes.io/component"  = "catalog-metadata"
      "app.kubernetes.io/part-of"    = "shirokuma"
      "app.kubernetes.io/managed-by" = "OpenTofu"
    }
  }
  immutable = true
  data      = local.generation_shared_data.postgres_secret
  type      = "Opaque"

  lifecycle {
    precondition {
      condition = !local.generation_mode || try(
        toset(keys(local.generation_shared)) == toset(keys(local.generation_shared_roles)) &&
        length(local.generation_ref_ids) == length(distinct(local.generation_ref_ids)) &&
        length(local.generation_ref_ids) == length(distinct([for e in local.generation_refs : "${e.ref.namespace}/${e.ref.name}"])) &&
        toset(nonsensitive(keys(var.warehouse_generation_secrets))) == toset(local.generation_expected_ids) &&
        alltrue([for e in local.generation_refs :
          toset(keys(e.ref)) == toset(["namespace", "name", "version", "keys"]) &&
          e.ref.namespace == e.namespace && length(trimspace(e.ref.version)) > 0 &&
          endswith(e.ref.name, "-${e.ref.version}") && can(regex("^[a-z0-9][a-z0-9.-]*$", e.ref.name)) &&
          length(e.ref.keys) == length(e.keys) && toset(e.ref.keys) == toset(e.keys) &&
          (contains(local.generation_expected_ids, "${e.ref.namespace}/${e.ref.name}@${e.ref.version}") ?
          toset(nonsensitive(keys(local.generation_data["${e.ref.namespace}/${e.ref.name}@${e.ref.version}"]))) == toset(e.keys) : true)
        ]) &&
        alltrue([for key, db in local.generation_databases :
          length(trimspace(key)) > 0 && length(trimspace(db.realm)) > 0 &&
          length(trimspace(db.sql_catalog)) > 0 && length(trimspace(db.postgres_database)) > 0 &&
          db.bootstrap_credentials_key == "credentials.${substr(sha256(key), 0, 16)}.json"
      ]), false)
      error_message = "Generation DB metadata and complete immutable SecretRefs must match the exact private reference/version/key sets."
    }
    precondition {
      condition = !local.generation_mode || try(
        toset(local.generation_deferred_ids) == toset(local.generation_expected_deferred_ids) &&
        toset(local.generation_bootstrap_ids) == setsubtract(toset(local.generation_ref_ids), toset(local.generation_expected_deferred_ids)) &&
        length(local.generation_bootstrap_ids) + length(local.generation_deferred_ids) == length(local.generation_ref_ids) &&
        alltrue([for ref in concat(local.generation_bootstrap_refs, local.generation_deferred_refs) :
          contains([for entry in local.generation_refs : jsonencode(entry.ref)], jsonencode(ref))
      ]), false)
      error_message = "Bootstrap and deferred SecretRefs must exactly partition the complete declared role references, including full version and key metadata."
    }
    precondition {
      condition = !local.generation_mode || try(
        alltrue(flatten([for secret in values(local.generation_data) : [
          for value in values(secret) : length(trimspace(value)) > 0
          ]])) && alltrue([for key, db in local.generation_databases :
          local.generation_db_data[key].root_secret.client_id == "root" &&
          local.generation_db_data[key].root_secret.realm == db.realm &&
          alltrue([for role in ["trino_secret", "bootstrap_secret"] :
            contains(local.generation_expected_ids, "${db[role].namespace}/${db[role].name}@${db[role].version}") ?
            local.generation_db_data[key][role].realm == db.realm : true
          ]) &&
          length(distinct([for role in ["root_secret", "trino_secret", "bootstrap_secret"] :
            local.generation_db_data[key][role].client_id if contains(local.generation_expected_ids, "${db[role].namespace}/${db[role].name}@${db[role].version}")
          ])) == length([for role in ["root_secret", "trino_secret", "bootstrap_secret"] : role if contains(local.generation_expected_ids, "${db[role].namespace}/${db[role].name}@${db[role].version}")])
      ]), false)
      error_message = "Generation Secret data must be complete and nonempty, with root and service principal realms matching their database."
    }
  }
}

moved {
  from = kubernetes_secret_v1.polaris_root_credentials
  to   = kubernetes_secret_v1.polaris_root_credentials["sales"]
}

resource "kubernetes_secret_v1" "polaris_root_credentials" {
  for_each = local.polaris_databases
  metadata {
    name      = each.value.root_secret
    namespace = local.generation_mode ? local.generation_databases[each.key].root_secret.namespace : kubernetes_namespace_v1.dev.metadata[0].name
    annotations = local.generation_mode ? merge(local.generation_annotations, { "shirokuma.dev/secret-version" = local.generation_databases[each.key].root_secret.version }) : {
      "shirokuma.dev/polaris-credential-generation" = local.polaris_credential_generation
    }
    labels = {
      "app.kubernetes.io/name"       = "polaris"
      "app.kubernetes.io/component"  = "catalog-bootstrap"
      "app.kubernetes.io/part-of"    = "shirokuma"
      "app.kubernetes.io/managed-by" = "OpenTofu"
    }
  }

  immutable = local.generation_mode ? true : null
  data = local.generation_mode ? local.generation_db_data[each.key].root_secret : {
    "credentials.json" = jsonencode({ (each.value.realm) = local.polaris_root_entries[each.value.realm] })
    client_id          = local.polaris_root_client_id
    client_secret      = var.polaris_database_credentials[each.key].root_client_secret
    realm              = each.value.realm
  }

  type = "Opaque"
}

resource "kubernetes_secret_v1" "polaris_bootstrap_credentials" {
  metadata {
    name      = local.generation_mode ? local.generation_shared.polaris_bootstrap_secret.name : "polaris-bootstrap-credentials"
    namespace = local.generation_mode ? local.generation_shared.polaris_bootstrap_secret.namespace : kubernetes_namespace_v1.dev.metadata[0].name
    annotations = local.generation_mode ? merge(local.generation_annotations, { "shirokuma.dev/secret-version" = local.generation_shared.polaris_bootstrap_secret.version }) : {
      "shirokuma.dev/polaris-credential-generation" = local.polaris_credential_generation
    }
  }
  immutable = local.generation_mode ? true : null
  data = local.generation_mode ? local.generation_shared_data.polaris_bootstrap_secret : {
    "credentials.json" = jsonencode(local.polaris_root_entries)
  }
  type = "Opaque"
}

resource "kubernetes_secret_v1" "polaris_jwt_signing_key" {
  for_each = local.polaris_databases
  metadata {
    name      = each.value.jwt_secret
    namespace = local.generation_mode ? local.generation_databases[each.key].jwt_secret.namespace : kubernetes_namespace_v1.dev.metadata[0].name
    annotations = local.generation_mode ? merge(local.generation_annotations, { "shirokuma.dev/secret-version" = local.generation_databases[each.key].jwt_secret.version }) : {
      "shirokuma.dev/polaris-jwt-key-generation" = each.value.jwt_generation
    }
  }
  immutable = local.generation_mode ? true : null
  data = local.generation_mode ? local.generation_db_data[each.key].jwt_secret : {
    "private.pem" = var.polaris_database_credentials[each.key].jwt_private_key_pem
    "public.pem"  = var.polaris_database_credentials[each.key].jwt_public_key_pem
  }
  type = "Opaque"
}

resource "kubernetes_secret_v1" "polaris_trino_credentials" {
  for_each = {
    for key, database in local.polaris_databases : key => database
    if local.generation_mode ? contains(local.generation_expected_ids, "${local.generation_databases[key].trino_secret.namespace}/${local.generation_databases[key].trino_secret.name}@${local.generation_databases[key].trino_secret.version}") : try(nonsensitive(
      var.polaris_database_credentials[key].trino_client_id != null &&
      var.polaris_database_credentials[key].trino_client_secret != null
    ), false)
  }
  metadata {
    name        = each.value.trino_secret
    namespace   = local.generation_mode ? local.generation_databases[each.key].trino_secret.namespace : kubernetes_namespace_v1.dev.metadata[0].name
    annotations = local.generation_mode ? merge(local.generation_annotations, { "shirokuma.dev/secret-version" = local.generation_databases[each.key].trino_secret.version }) : null
  }
  immutable = local.generation_mode ? true : null
  data = local.generation_mode ? local.generation_db_data[each.key].trino_secret : {
    client_id     = var.polaris_database_credentials[each.key].trino_client_id
    client_secret = var.polaris_database_credentials[each.key].trino_client_secret
    realm         = each.value.realm
  }
  type = "Opaque"
}

resource "kubernetes_secret_v1" "polaris_iceberg_bootstrap_credentials" {
  for_each = {
    for key, database in local.polaris_databases : key => database
    if local.generation_mode ? contains(local.generation_expected_ids, "${local.generation_databases[key].bootstrap_secret.namespace}/${local.generation_databases[key].bootstrap_secret.name}@${local.generation_databases[key].bootstrap_secret.version}") : try(nonsensitive(
      var.polaris_database_credentials[key].bootstrap_client_id != null &&
      var.polaris_database_credentials[key].bootstrap_client_secret != null
    ), false)
  }
  metadata {
    name        = each.value.bootstrap_secret
    namespace   = local.generation_mode ? local.generation_databases[each.key].bootstrap_secret.namespace : kubernetes_namespace_v1.dev.metadata[0].name
    annotations = local.generation_mode ? merge(local.generation_annotations, { "shirokuma.dev/secret-version" = local.generation_databases[each.key].bootstrap_secret.version }) : null
  }
  immutable = local.generation_mode ? true : null
  data = local.generation_mode ? local.generation_db_data[each.key].bootstrap_secret : {
    client_id     = var.polaris_database_credentials[each.key].bootstrap_client_id
    client_secret = var.polaris_database_credentials[each.key].bootstrap_client_secret
    realm         = each.value.realm
  }
  type = "Opaque"
}

resource "kubernetes_secret_v1" "generation_trino_auth" {
  for_each = local.generation_mode ? { for role, ref in local.generation_shared : role => ref if role == "trino_auth_secret" && contains(local.generation_expected_ids, "${ref.namespace}/${ref.name}@${ref.version}") } : {}
  metadata {
    name        = each.value.name
    namespace   = each.value.namespace
    annotations = merge(local.generation_annotations, { "shirokuma.dev/secret-version" = each.value.version })
  }
  immutable = true
  data      = local.generation_shared_data[each.key]
  type      = "Opaque"
}
