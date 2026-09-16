# Local experiment only: upstream binary, not the hardened publication candidate.
FROM docker.io/library/maven@sha256:7e461cec477077c1d9e50b13df8aef9018764410f4c4cd7c34803f10c4c99e4c

LABEL org.opencontainers.image.title="Shirokuma local Trino 483 experiment" \
      shirokuma.dev.scope="local-experiment-only" \
      shirokuma.dev.upstream-archive-sha256="4f3978428f26f36398c94b85a3e03b5301394919c8a4271b497b0fcd1698d0cb"

COPY --chown=10000:10000 trino-server-483/ /opt/trino/
ENV HOME=/var/trino
WORKDIR /opt/trino
USER 10000:10000
EXPOSE 8080
ENTRYPOINT ["/opt/trino/bin/launcher", "run", "--etc-dir=/etc/trino", "--data-dir=/var/trino"]
