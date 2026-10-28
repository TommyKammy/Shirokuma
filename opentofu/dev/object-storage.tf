locals {
  seaweedfs_s3_bucket = "shirokuma-lakehouse"
  seaweedfs_s3_config = jsonencode({
    identities = concat([
      {
        name = "shirokuma-local-lite-operator"
        credentials = [
          {
            accessKey = var.seaweedfs_s3_operator_access_key
            secretKey = var.seaweedfs_s3_operator_secret_key
          }
        ]
        actions = ["Admin"]
      },
      {
        name = "shirokuma-local-gitops"
        credentials = [{
          accessKey = var.local_gitops_s3_credentials.access_key
          secretKey = var.local_gitops_s3_credentials.secret_key
        }]
        actions = ["Read:shirokuma-gitops", "List:shirokuma-gitops"]
      }
      ], [for key, database in local.legacy_polaris_databases : {
        name = "shirokuma-${key}-s3"
        credentials = [{
          accessKey = try(var.polaris_database_credentials[key].s3_access_key, null)
          secretKey = try(var.polaris_database_credentials[key].s3_secret_key, null)
        }]
        actions = [for action in ["Read", "List", "Write", "Tagging"] :
          "${action}:${local.seaweedfs_s3_bucket}/${database.prefix}*"
        ]
    }])
  })
}

resource "kubernetes_secret_v1" "seaweedfs_s3_credentials" {
  metadata {
    name      = local.generation_mode ? local.generation_shared.seaweedfs_server_secret.name : "seaweedfs-s3-credentials"
    namespace = local.generation_mode ? local.generation_shared.seaweedfs_server_secret.namespace : kubernetes_namespace_v1.storage.metadata[0].name
    annotations = local.generation_mode ? merge(local.generation_annotations, { "shirokuma.dev/secret-version" = local.generation_shared.seaweedfs_server_secret.version }) : {
      "shirokuma.dev/s3-credential-generation" = var.seaweedfs_s3_credential_generation
    }
    labels = {
      "app.kubernetes.io/name"       = "seaweedfs"
      "app.kubernetes.io/component"  = "object-storage"
      "app.kubernetes.io/part-of"    = "shirokuma"
      "app.kubernetes.io/managed-by" = "OpenTofu"
    }
  }

  immutable = local.generation_mode ? true : null
  data = local.generation_mode ? local.generation_shared_data.seaweedfs_server_secret : {
    "s3.json" = local.seaweedfs_s3_config
  }

  type = "Opaque"

  lifecycle {
    precondition {
      condition = local.generation_mode ? true : length(distinct(concat([
        var.seaweedfs_s3_operator_access_key,
        var.local_gitops_s3_credentials.access_key
      ], [for key in keys(local.polaris_databases) : var.polaris_database_credentials[key].s3_access_key]))) == 4
      error_message = "Operator, SALES, FINANCE and GitOps S3 access keys must be distinct."
    }
    precondition {
      condition = !local.generation_mode || try(
        length(distinct(concat([
          local.generation_shared_data.operator_secret.access_key,
          local.generation_shared_data.gitops_source_secret.accesskey
        ], [for key in keys(local.generation_databases) : local.generation_db_data[key].s3_secret.AWS_ACCESS_KEY_ID]))) == length(local.generation_databases) + 2 &&
        alltrue([for key in keys(local.generation_databases) :
          local.generation_db_data[key].s3_secret.S3_ENDPOINT == var.warehouse_generation.s3_policy.endpoint &&
          local.generation_db_data[key].s3_secret.S3_BUCKET == var.warehouse_generation.s3_policy.bucket &&
          local.generation_db_data[key].s3_secret.S3_REGION == var.warehouse_generation.s3_policy.region &&
          local.generation_db_data[key].s3_secret.AWS_REGION == var.warehouse_generation.s3_policy.region &&
          local.generation_db_data[key].s3_secret.S3_PATH_STYLE == "true"
      ]), false)
      error_message = "Generation operator, GitOps and database S3 keys must be distinct and database S3 configuration must match the public policy."
    }
  }
}

resource "kubernetes_secret_v1" "seaweedfs_s3_database_credentials" {
  for_each = local.polaris_databases
  metadata {
    name      = each.value.s3_secret
    namespace = local.generation_mode ? local.generation_databases[each.key].s3_secret.namespace : kubernetes_namespace_v1.dev.metadata[0].name
    annotations = local.generation_mode ? merge(local.generation_annotations, { "shirokuma.dev/secret-version" = local.generation_databases[each.key].s3_secret.version }) : {
      "shirokuma.dev/s3-credential-generation" = var.seaweedfs_s3_credential_generation
    }
    labels = {
      "app.kubernetes.io/name"       = "seaweedfs"
      "app.kubernetes.io/component"  = "object-storage-client"
      "app.kubernetes.io/part-of"    = "shirokuma"
      "app.kubernetes.io/managed-by" = "OpenTofu"
    }
  }

  immutable = local.generation_mode ? true : null
  data = local.generation_mode ? local.generation_db_data[each.key].s3_secret : {
    AWS_ACCESS_KEY_ID     = var.polaris_database_credentials[each.key].s3_access_key
    AWS_SECRET_ACCESS_KEY = var.polaris_database_credentials[each.key].s3_secret_key
    AWS_REGION            = "us-east-1"
    S3_ENDPOINT           = "http://seaweedfs-s3.shirokuma-storage.svc.cluster.local:8333"
    S3_BUCKET             = local.seaweedfs_s3_bucket
    S3_REGION             = "us-east-1"
    S3_PATH_STYLE         = "true"
  }

  type = "Opaque"
}

resource "kubernetes_secret_v1" "local_gitops_s3_credentials" {
  metadata {
    name        = local.generation_mode ? local.generation_shared.gitops_source_secret.name : "shirokuma-local-source"
    namespace   = local.generation_mode ? local.generation_shared.gitops_source_secret.namespace : "flux-system"
    annotations = local.generation_mode ? merge(local.generation_annotations, { "shirokuma.dev/secret-version" = local.generation_shared.gitops_source_secret.version }) : null
  }
  immutable = local.generation_mode ? true : null
  data = local.generation_mode ? local.generation_shared_data.gitops_source_secret : {
    accesskey = var.local_gitops_s3_credentials.access_key
    secretkey = var.local_gitops_s3_credentials.secret_key
  }
  type = "Opaque"
}

resource "kubernetes_secret_v1" "generation_s3_operator" {
  for_each = local.generation_mode ? { operator_secret = local.generation_shared.operator_secret } : {}
  metadata {
    name        = each.value.name
    namespace   = each.value.namespace
    annotations = merge(local.generation_annotations, { "shirokuma.dev/secret-version" = each.value.version })
  }
  immutable = true
  data      = local.generation_shared_data[each.key]
  type      = "Opaque"
}
