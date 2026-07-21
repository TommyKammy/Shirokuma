locals {
  polaris_realm               = "POLARIS"
  polaris_root_client_id      = "root"
  polaris_postgresql_database = "polaris"
  polaris_postgresql_username = "polaris"
  polaris_credential_generation = yamldecode(file(
    "${path.module}/../../deploy/gitops/clusters/local-lite/polaris-runtime-generation.yaml"
  )).data.POLARIS_CREDENTIAL_GENERATION
  polaris_root_credentials = jsonencode({
    (local.polaris_realm) = {
      "client-id"     = local.polaris_root_client_id
      "client-secret" = var.polaris_root_client_secret
    }
  })
}

resource "kubernetes_secret_v1" "polaris_postgresql_credentials" {
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

resource "kubernetes_secret_v1" "polaris_root_credentials" {
  metadata {
    name      = "polaris-root-credentials"
    namespace = kubernetes_namespace_v1.dev.metadata[0].name
    annotations = {
      "shirokuma.dev/polaris-credential-generation" = local.polaris_credential_generation
    }
    labels = {
      "app.kubernetes.io/name"       = "polaris"
      "app.kubernetes.io/component"  = "catalog-bootstrap"
      "app.kubernetes.io/part-of"    = "shirokuma"
      "app.kubernetes.io/managed-by" = "OpenTofu"
    }
  }

  data = {
    "credentials.json" = local.polaris_root_credentials
    client_id          = local.polaris_root_client_id
    client_secret      = var.polaris_root_client_secret
    realm              = local.polaris_realm
  }

  type = "Opaque"

  lifecycle {
    ignore_changes = [data]
  }
}
