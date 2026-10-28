# Inert build definition. Build only with the fixed directory as context,
# --network=none and --pull=false; the official base must already be cached.
FROM trinodb/trino:483@sha256:aa18e61b2e7776ab8641ba8baaa8687d0430894e88c639e61010cc46a994ab36 AS compile
USER root
WORKDIR /tmp/logical-secrets-build
COPY src ./src
# Use the official image's JDK25 and Airlift439 SPI, without bundling upstream classes.
RUN javac -version 2>&1 | grep '^javac 25' \
    && test -f /usr/lib/trino/lib/io.airlift_secrets-spi-439.jar \
    && mkdir classes \
    && javac --release 25 -cp /usr/lib/trino/lib/io.airlift_secrets-spi-439.jar -d classes src/main/java/org/shirokuma/logical/secrets/*.java \
    && cp -R src/main/resources/. classes/ \
    && printf 'Manifest-Version: 1.0\nClass-Path: file:/usr/lib/trino/lib/io.airlift_secrets-spi-439.jar\n\n' > manifest.mf \
    && jar --create --file logical-secrets.jar --manifest manifest.mf -C classes .

FROM scratch AS artifact
COPY --from=compile /tmp/logical-secrets-build/logical-secrets.jar /logical-secrets.jar
