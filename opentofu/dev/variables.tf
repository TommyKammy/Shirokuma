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


variable "polaris_database_credentials" {
  description = "Parent-supplied SALES and FINANCE credentials and stable unencrypted PKCS#8/SPKI RSA PEM keys."
  type = map(object({
    s3_access_key           = string
    s3_secret_key           = string
    root_client_secret      = string
    jwt_private_key_pem     = string
    jwt_public_key_pem      = string
    bootstrap_client_id     = optional(string)
    bootstrap_client_secret = optional(string)
    trino_client_id         = optional(string)
    trino_client_secret     = optional(string)
  }))
  sensitive = true
  nullable  = false

  validation {
    condition     = var.warehouse_generation != null || toset(keys(var.polaris_database_credentials)) == toset(["sales", "finance"])
    error_message = "Database credentials must have exactly sales and finance keys."
  }
  validation {
    condition = alltrue(flatten([
      for credentials in values(var.polaris_database_credentials) :
      [for value in [
        credentials.s3_access_key,
        credentials.s3_secret_key,
        credentials.root_client_secret,
        credentials.jwt_private_key_pem,
        credentials.jwt_public_key_pem
      ] : try(length(trimspace(value)) > 0, false)]
    ]))
    error_message = "Required database credential fields must be non-empty strings."
  }
  validation {
    condition = alltrue(flatten([
      for credentials in values(var.polaris_database_credentials) : [
        for pair in [
          [credentials.bootstrap_client_id, credentials.bootstrap_client_secret],
          [credentials.trino_client_id, credentials.trino_client_secret]
          ] : pair[0] == null ? pair[1] == null : (
          pair[1] == null ? false : alltrue([for value in pair : length(trimspace(value)) > 0])
        )
      ]
    ]))
    error_message = "Each bootstrap or Trino client pair must be both null or both non-empty strings."
  }
  validation {
    condition = alltrue([
      for credentials in values(var.polaris_database_credentials) : try(
        startswith(trimspace(credentials.jwt_private_key_pem), "-----BEGIN PRIVATE KEY-----") &&
        endswith(trimspace(credentials.jwt_private_key_pem), "-----END PRIVATE KEY-----") &&
        startswith(trimspace(credentials.jwt_public_key_pem), "-----BEGIN PUBLIC KEY-----") &&
        endswith(trimspace(credentials.jwt_public_key_pem), "-----END PUBLIC KEY-----"), false
      )
    ])
    error_message = "JWT keys require unencrypted PKCS#8 private and SPKI public PEM headers and footers."
  }
}

variable "warehouse_generation" {
  description = "Opt-in verified public generation metadata; null preserves the legacy two-database deployment."
  type        = any
  default     = null
  validation {
    condition = var.warehouse_generation == null ? true : try(
      var.warehouse_generation.version == 1 &&
      length(trimspace(var.warehouse_generation.generation_id)) > 0 &&
      length(keys(var.warehouse_generation.databases)) > 0 &&
      toset(var.warehouse_generation.derived_secret_roles) == toset(["polaris_bootstrap_secret", "seaweedfs_server_secret"]) &&
      length(var.warehouse_generation.derived_secret_roles) == 2,
      false
    )
    error_message = "Generation metadata requires version 1, a generation ID, databases and exactly the two declared derived roles."
  }
}

variable "warehouse_generation_secrets" {
  description = "Explicit private material keyed by namespace/name@version, including the two derived Secrets."
  type        = map(object({ data = map(string) }))
  sensitive   = true
  default     = {}
}

variable "warehouse_generation_phase" {
  description = "Explicit generation material stage; legacy null-generation deployments retain their existing behavior."
  type        = string
  default     = "full"
  nullable    = false
  validation {
    condition     = contains(["bootstrap", "full"], var.warehouse_generation_phase)
    error_message = "Warehouse generation phase must be bootstrap or full."
  }
}

variable "local_gitops_s3_credentials" {
  description = "Independent read-only local GitOps bucket credentials supplied by the parent."
  type = object({
    access_key = string
    secret_key = string
  })
  sensitive = true
  nullable  = false
  validation {
    condition = alltrue([
      for value in values(var.local_gitops_s3_credentials) : try(length(trimspace(value)) > 0, false)
    ])
    error_message = "Both GitOps credential fields must be non-empty strings."
  }
}
