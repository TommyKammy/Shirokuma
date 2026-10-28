output "seaweedfs_s3_secret_name" {
  description = "Name of the OpenTofu-managed server configuration Secret in shirokuma-storage."
  value       = kubernetes_secret_v1.seaweedfs_s3_credentials.metadata[0].name
}

output "seaweedfs_s3_secret_namespace" {
  description = "Namespace containing the SeaweedFS server configuration Secret."
  value       = kubernetes_secret_v1.seaweedfs_s3_credentials.metadata[0].namespace
}

output "seaweedfs_s3_endpoint" {
  description = "Cross-namespace in-cluster S3 endpoint for application clients."
  value       = "http://seaweedfs-s3.shirokuma-storage.svc.cluster.local:8333"
}

output "polaris_postgresql_secret_name" {
  description = "Name of the OpenTofu-managed Polaris PostgreSQL credential Secret."
  value       = local.generation_mode ? kubernetes_secret_v1.polaris_generation_postgresql_credentials[0].metadata[0].name : kubernetes_secret_v1.polaris_postgresql_credentials[0].metadata[0].name
}

output "polaris_root_secret_name" {
  description = "Name of the OpenTofu-managed Polaris root credential-file Secret."
  value       = try(kubernetes_secret_v1.polaris_root_credentials["sales"].metadata[0].name, null)
}

output "warehouse_generation" {
  description = "Public generation metadata and full immutable SecretRefs only."
  value       = var.warehouse_generation
}

output "warehouse_generation_phase" {
  description = "Material phase for an opted-in generation, or null for legacy mode."
  value       = local.generation_mode ? var.warehouse_generation_phase : null
}

output "polaris_database_secret_names" {
  description = "Nonsecret per-database Secret names, realm, catalog and warehouse prefix; unprovisioned consumer names are null."
  value = { for key, database in local.polaris_databases : key => {
    realm             = database.realm
    catalog           = database.catalog
    prefix            = database.prefix
    root              = database.root_secret
    s3                = database.s3_secret
    jwt               = database.jwt_secret
    trino             = try(kubernetes_secret_v1.polaris_trino_credentials[key].metadata[0].name, null)
    iceberg_bootstrap = try(kubernetes_secret_v1.polaris_iceberg_bootstrap_credentials[key].metadata[0].name, null)
  } }
}

output "polaris_bootstrap_secret_name" {
  value = kubernetes_secret_v1.polaris_bootstrap_credentials.metadata[0].name
}

output "local_gitops_s3_secret_name" {
  value = kubernetes_secret_v1.local_gitops_s3_credentials.metadata[0].name
}
