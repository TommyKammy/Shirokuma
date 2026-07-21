output "seaweedfs_s3_secret_name" {
  description = "Name of the OpenTofu-managed server configuration Secret in shirokuma-storage."
  value       = kubernetes_secret_v1.seaweedfs_s3_credentials.metadata[0].name
}

output "seaweedfs_s3_secret_namespace" {
  description = "Namespace containing the SeaweedFS server configuration Secret."
  value       = kubernetes_secret_v1.seaweedfs_s3_credentials.metadata[0].namespace
}

output "seaweedfs_s3_application_secret_name" {
  description = "Name of the bucket-scoped credential Secret in shirokuma-dev."
  value       = kubernetes_secret_v1.seaweedfs_s3_application_credentials.metadata[0].name
}

output "seaweedfs_s3_application_secret_namespace" {
  description = "Namespace containing the bucket-scoped application credential Secret."
  value       = kubernetes_secret_v1.seaweedfs_s3_application_credentials.metadata[0].namespace
}

output "seaweedfs_s3_endpoint" {
  description = "Cross-namespace in-cluster S3 endpoint for application clients."
  value       = "http://seaweedfs-s3.shirokuma-storage.svc.cluster.local:8333"
}

output "polaris_postgresql_secret_name" {
  description = "Name of the OpenTofu-managed Polaris PostgreSQL credential Secret."
  value       = kubernetes_secret_v1.polaris_postgresql_credentials.metadata[0].name
}

output "polaris_root_secret_name" {
  description = "Name of the OpenTofu-managed Polaris root credential-file Secret."
  value       = kubernetes_secret_v1.polaris_root_credentials.metadata[0].name
}
