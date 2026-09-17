FROM quay.io/keycloak/keycloak@sha256:1f91ac24e8d68b8189d5d53a8381464c1db0fcff479348d5de973a86b63d621c

# Optimize before the runtime makes the image filesystem read-only. The H2 data
# directory and /tmp are separate, disposable emptyDirs in the owned test Pod.
ENV KC_DB=dev-file KC_HEALTH_ENABLED=true KC_CACHE=local
RUN /opt/keycloak/bin/kc.sh build --db=dev-file --health-enabled=true
LABEL shirokuma.dev.component="trino-identity-keycloak" \
      shirokuma.dev.scope="local-experiment-only" \
      shirokuma.dev.keycloak-version="26.7.4"
