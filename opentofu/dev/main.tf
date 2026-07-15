provider "helm" {
  kubernetes = {
    config_path    = pathexpand(var.kubeconfig_path)
    config_context = var.kube_context
  }
}

provider "kubernetes" {
  config_path    = pathexpand(var.kubeconfig_path)
  config_context = var.kube_context
}

resource "kubernetes_namespace_v1" "dev" {
  metadata {
    name = "shirokuma-dev"
    labels = {
      "app.kubernetes.io/part-of"    = "shirokuma"
      "app.kubernetes.io/managed-by" = "OpenTofu"
    }
  }
}

resource "kubernetes_namespace_v1" "storage" {
  metadata {
    name = "shirokuma-storage"
    labels = {
      "app.kubernetes.io/part-of"    = "shirokuma"
      "app.kubernetes.io/managed-by" = "OpenTofu"
    }
  }
}
