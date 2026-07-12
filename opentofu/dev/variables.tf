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
