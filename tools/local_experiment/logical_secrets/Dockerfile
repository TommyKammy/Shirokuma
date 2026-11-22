# Inert build definition. Build only with the fixed directory as context,
# --network=none and --pull=false; the official base must already be cached.
# Trino 484's hardened base image ships no grep, so the javac version check is
# pure shell; the secrets SPI jar follows the pinned airlift version (451).
FROM trinodb/trino:484@sha256:62dc5ad850a11fc95637b0e7aa9da46031145093cb0456cd099c3519298f268c AS compile
USER root
WORKDIR /tmp/logical-secrets-build
COPY src ./src
# Use the official image's JDK25 and Airlift451 SPI, without bundling upstream classes.
RUN version="$(javac -version 2>&1)" \
    && case "$version" in "javac 25"*) : ;; *) echo "unexpected javac: $version" >&2; false ;; esac \
    && test -f /usr/lib/trino/lib/io.airlift_secrets-spi-451.jar \
    && mkdir classes \
    && javac --release 25 -cp /usr/lib/trino/lib/io.airlift_secrets-spi-451.jar -d classes src/main/java/org/shirokuma/logical/secrets/*.java \
    && cp -R src/main/resources/. classes/ \
    && printf 'Manifest-Version: 1.0\nClass-Path: file:/usr/lib/trino/lib/io.airlift_secrets-spi-451.jar\n\n' > manifest.mf \
    && jar --create --file logical-secrets.jar --manifest manifest.mf -C classes .
# Root is only needed to compile; return to the base image's user.
USER trino

FROM scratch AS artifact
COPY --from=compile /tmp/logical-secrets-build/logical-secrets.jar /logical-secrets.jar
