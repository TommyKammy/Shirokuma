output "seaweedfs_s3_secret_name" {
  description = "Name of the OpenTofu-managed server configuration Secret consumed by SeaweedFS."
  value       = kubernetes_secret_v1.seaweedfs_s3_credentials.metadata[0].name
}

output "seaweedfs_s3_application_secret_name" {
  description = "Name of the bucket-scoped credential Secret for lakehouse clients."
  value       = kubernetes_secret_v1.seaweedfs_s3_application_credentials.metadata[0].name
}
