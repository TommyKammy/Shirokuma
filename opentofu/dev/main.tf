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

locals {
  bootstrap_images = jsondecode(file("${path.module}/bootstrap-images.json"))
}

resource "kubernetes_namespace_v1" "argocd" {
  metadata {
    name = "argocd"
  }
}

resource "kubernetes_namespace_v1" "dev" {
  metadata {
    name = "shirokuma-dev"
  }
}

resource "helm_release" "argocd" {
  name       = "argocd"
  namespace  = kubernetes_namespace_v1.argocd.metadata[0].name
  repository = "https://argoproj.github.io/argo-helm"
  chart      = "argo-cd"
  version    = "10.1.3"

  atomic          = true
  cleanup_on_fail = true
  timeout         = 900
  wait            = true

  values = [yamlencode({
    crds = {
      keep = false
    }
    global = {
      image = {
        repository = local.bootstrap_images.argocd.repository
        tag        = local.bootstrap_images.argocd.tag
      }
    }
    dex = {
      enabled = false
    }
    redis = {
      image = {
        repository = local.bootstrap_images.redis.repository
        tag        = local.bootstrap_images.redis.tag
      }
    }
  })]
}

resource "helm_release" "dev_root" {
  name      = "dev-root"
  namespace = helm_release.argocd.namespace
  chart     = "${path.module}/../../charts/dev-root"

  atomic          = true
  cleanup_on_fail = true
  timeout         = 300
  wait            = true

  values = [yamlencode({
    repository = {
      url      = var.repository_url
      revision = var.repository_revision
      path     = "deploy/gitops/dev"
    }
  })]

  depends_on = [helm_release.argocd, kubernetes_namespace_v1.dev]
}
