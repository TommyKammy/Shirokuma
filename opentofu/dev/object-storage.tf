locals {
  seaweedfs_s3_bucket = "shirokuma-lakehouse"
  seaweedfs_s3_config = jsonencode({
    identities = [
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
        name = "shirokuma-lakehouse-application"
        credentials = [
          {
            accessKey = var.seaweedfs_s3_application_access_key
            secretKey = var.seaweedfs_s3_application_secret_key
          }
        ]
        actions = [
          "Read:${local.seaweedfs_s3_bucket}",
          "List:${local.seaweedfs_s3_bucket}",
          "Tagging:${local.seaweedfs_s3_bucket}",
          "Write:${local.seaweedfs_s3_bucket}",
        ]
      }
    ]
  })
}

resource "kubernetes_secret_v1" "seaweedfs_s3_credentials" {
  metadata {
    name      = "seaweedfs-s3-credentials"
    namespace = kubernetes_namespace_v1.dev.metadata[0].name
    annotations = {
      "shirokuma.dev/s3-credential-generation" = var.seaweedfs_s3_credential_generation
    }
    labels = {
      "app.kubernetes.io/name"       = "seaweedfs"
      "app.kubernetes.io/component"  = "object-storage"
      "app.kubernetes.io/part-of"    = "shirokuma"
      "app.kubernetes.io/managed-by" = "OpenTofu"
    }
  }

  data = {
    "s3.json" = local.seaweedfs_s3_config
  }

  type = "Opaque"

  lifecycle {
    precondition {
      condition = (
        var.seaweedfs_s3_operator_access_key !=
        var.seaweedfs_s3_application_access_key
      )
      error_message = "SeaweedFS operator and application access keys must be distinct."
    }
  }
}

resource "kubernetes_secret_v1" "seaweedfs_s3_application_credentials" {
  metadata {
    name      = "seaweedfs-s3-application-credentials"
    namespace = kubernetes_namespace_v1.dev.metadata[0].name
    annotations = {
      "shirokuma.dev/s3-credential-generation" = var.seaweedfs_s3_credential_generation
    }
    labels = {
      "app.kubernetes.io/name"       = "seaweedfs"
      "app.kubernetes.io/component"  = "object-storage-client"
      "app.kubernetes.io/part-of"    = "shirokuma"
      "app.kubernetes.io/managed-by" = "OpenTofu"
    }
  }

  data = {
    AWS_ACCESS_KEY_ID     = var.seaweedfs_s3_application_access_key
    AWS_SECRET_ACCESS_KEY = var.seaweedfs_s3_application_secret_key
    S3_ENDPOINT           = "http://seaweedfs-s3.shirokuma-dev.svc.cluster.local:8333"
    S3_BUCKET             = local.seaweedfs_s3_bucket
    S3_REGION             = "us-east-1"
    S3_PATH_STYLE         = "true"
  }

  type = "Opaque"
}
