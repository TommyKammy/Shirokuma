variable "kubeconfig_path" {
  description = "Path to the kubeconfig for the Colima k3s cluster."
  type        = string
  default     = "~/.kube/config"
}

variable "kube_context" {
  description = "Authoritative Kubernetes context for the local lab."
  type        = string
  default     = "colima-mac-studio-solo"
}

variable "seaweedfs_s3_operator_access_key" {
  description = "Static operator S3 access key injected through TF_VAR_seaweedfs_s3_operator_access_key."
  type        = string
  sensitive   = true
  nullable    = false

  validation {
    condition = (
      length(var.seaweedfs_s3_operator_access_key) >= 16 &&
      length(var.seaweedfs_s3_operator_access_key) <= 128 &&
      trimspace(var.seaweedfs_s3_operator_access_key) == var.seaweedfs_s3_operator_access_key &&
      can(regex("^[A-Za-z0-9._-]+$", var.seaweedfs_s3_operator_access_key))
    )
    error_message = "The SeaweedFS S3 access key must contain 16-128 ASCII letters, digits, dots, underscores, or hyphens."
  }
}

variable "seaweedfs_s3_operator_secret_key" {
  description = "Static operator S3 secret key injected through TF_VAR_seaweedfs_s3_operator_secret_key."
  type        = string
  sensitive   = true
  nullable    = false

  validation {
    condition = (
      length(var.seaweedfs_s3_operator_secret_key) >= 32 &&
      length(var.seaweedfs_s3_operator_secret_key) <= 256 &&
      trimspace(var.seaweedfs_s3_operator_secret_key) == var.seaweedfs_s3_operator_secret_key &&
      can(regex("^[^\\r\\n]+$", var.seaweedfs_s3_operator_secret_key))
    )
    error_message = "The SeaweedFS S3 secret key must contain 32-256 characters with no surrounding whitespace or line breaks."
  }
}

variable "seaweedfs_s3_application_access_key" {
  description = "Bucket-scoped lakehouse S3 access key injected through TF_VAR_seaweedfs_s3_application_access_key."
  type        = string
  sensitive   = true
  nullable    = false

  validation {
    condition = (
      length(var.seaweedfs_s3_application_access_key) >= 16 &&
      length(var.seaweedfs_s3_application_access_key) <= 128 &&
      trimspace(var.seaweedfs_s3_application_access_key) == var.seaweedfs_s3_application_access_key &&
      can(regex("^[A-Za-z0-9._-]+$", var.seaweedfs_s3_application_access_key))
    )
    error_message = "The SeaweedFS S3 application access key must contain 16-128 ASCII letters, digits, dots, underscores, or hyphens."
  }
}

variable "seaweedfs_s3_application_secret_key" {
  description = "Bucket-scoped lakehouse S3 secret key injected through TF_VAR_seaweedfs_s3_application_secret_key."
  type        = string
  sensitive   = true
  nullable    = false

  validation {
    condition = (
      length(var.seaweedfs_s3_application_secret_key) >= 32 &&
      length(var.seaweedfs_s3_application_secret_key) <= 256 &&
      trimspace(var.seaweedfs_s3_application_secret_key) == var.seaweedfs_s3_application_secret_key &&
      can(regex("^[^\\r\\n]+$", var.seaweedfs_s3_application_secret_key))
    )
    error_message = "The SeaweedFS S3 application secret key must contain 32-256 characters with no surrounding whitespace or line breaks."
  }
}

variable "seaweedfs_s3_credential_generation" {
  description = "Monotonic generation token shared by the Secret annotations and Flux pod template."
  type        = string
  default     = "1"

  validation {
    condition     = can(regex("^[1-9][0-9]*$", var.seaweedfs_s3_credential_generation))
    error_message = "The SeaweedFS S3 credential generation must be a positive decimal integer."
  }
}

variable "polaris_postgresql_password" {
  description = "Polaris metadata database password injected through TF_VAR_polaris_postgresql_password."
  type        = string
  sensitive   = true
  nullable    = false

  validation {
    condition = (
      length(var.polaris_postgresql_password) >= 32 &&
      length(var.polaris_postgresql_password) <= 256 &&
      trimspace(var.polaris_postgresql_password) == var.polaris_postgresql_password &&
      can(regex("^[^\\r\\n]+$", var.polaris_postgresql_password))
    )
    error_message = "The Polaris PostgreSQL password must contain 32-256 characters with no surrounding whitespace or line breaks."
  }
}

variable "polaris_root_client_secret" {
  description = "Polaris root client secret injected through TF_VAR_polaris_root_client_secret."
  type        = string
  sensitive   = true
  nullable    = false

  validation {
    condition = (
      length(var.polaris_root_client_secret) >= 32 &&
      length(var.polaris_root_client_secret) <= 256 &&
      trimspace(var.polaris_root_client_secret) == var.polaris_root_client_secret &&
      can(regex("^[^\\r\\n]+$", var.polaris_root_client_secret))
    )
    error_message = "The Polaris root client secret must contain 32-256 characters with no surrounding whitespace or line breaks."
  }
}
