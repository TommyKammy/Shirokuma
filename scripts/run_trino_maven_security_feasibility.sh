#!/usr/bin/env bash
set -euo pipefail

root="${GITHUB_WORKSPACE:?}"
temp="${RUNNER_TEMP:?}"
verify="python3 ${root}/scripts/verify_trino_maven_security_feasibility.py"
publisher="python3 ${root}/scripts/verify_trino_dependency_publisher.py"
parquet="python3 ${root}/scripts/remediate_parquet_jackson.py"
bun="python3 ${root}/scripts/prepare_trino_bun_input.py"
policy="${root}/bootstrap/trino/v483"
candidate="${root}/.trino-security-feasibility"
mkdir -p "${candidate}"

fetch_trino() {
  local suffix="$1" source="${temp}/trino-source-${suffix}"
  git init "${source}"
  git -C "${source}" remote add origin "${SOURCE_REPOSITORY}"
  git -C "${source}" fetch --depth=1 origin \
    "${SOURCE_COMMIT}" "refs/tags/${SOURCE_TAG}:refs/tags/${SOURCE_TAG}"
  test "$(git -C "${source}" rev-parse "refs/tags/${SOURCE_TAG}")" = "${SOURCE_TAG_OBJECT}"
  test "$(git -C "${source}" rev-parse "refs/tags/${SOURCE_TAG}^{}")" = "${SOURCE_COMMIT}"
  git -C "${source}" checkout --detach "${SOURCE_COMMIT}"
  test "$(git -C "${source}" rev-parse 'HEAD^{tree}')" = "${SOURCE_TREE}"
  ${verify} apply-trino --root "${root}" --checkout "${source}"
}

fetch_parquet() {
  local suffix="$1" source="${temp}/parquet-source-${suffix}"
  git init "${source}"
  git -C "${source}" remote add origin "${PARQUET_SOURCE_REPOSITORY}"
  git -C "${source}" fetch --depth=1 origin \
    "${PARQUET_SOURCE_COMMIT}" \
    "refs/tags/${PARQUET_RELEASE_TAG}:refs/tags/${PARQUET_RELEASE_TAG}" \
    "refs/tags/${PARQUET_RC_TAG}:refs/tags/${PARQUET_RC_TAG}"
  test "$(git -C "${source}" rev-parse "refs/tags/${PARQUET_RELEASE_TAG}")" = "${PARQUET_RELEASE_TAG_OBJECT}"
  test "$(git -C "${source}" rev-parse "refs/tags/${PARQUET_RC_TAG}")" = "${PARQUET_RC_TAG_OBJECT}"
  git -C "${source}" checkout --detach "${PARQUET_SOURCE_COMMIT}"
  test "$(git -C "${source}" rev-parse 'HEAD^{tree}')" = "${PARQUET_SOURCE_TREE}"
  ${parquet} prepare-source --checkout "${source}"
}

fetch_docker_java() {
  local suffix="$1" source="${temp}/docker-java-source-${suffix}"
  git init "${source}"
  git -C "${source}" remote add origin "${DOCKER_JAVA_SOURCE_REPOSITORY}"
  git -C "${source}" fetch --depth=1 origin \
    "${DOCKER_JAVA_SOURCE_COMMIT}" \
    "refs/tags/${DOCKER_JAVA_SOURCE_TAG}:refs/tags/${DOCKER_JAVA_SOURCE_TAG}"
  test "$(git -C "${source}" rev-parse "refs/tags/${DOCKER_JAVA_SOURCE_TAG}^{}")" = "${DOCKER_JAVA_SOURCE_COMMIT}"
  git -C "${source}" checkout --detach "${DOCKER_JAVA_SOURCE_COMMIT}"
  test "$(git -C "${source}" rev-parse 'HEAD^{tree}')" = "${DOCKER_JAVA_SOURCE_TREE}"
  ${verify} apply-docker-java --root "${root}" --checkout "${source}"
  docker run --rm --platform linux/arm64 \
    --user "$(id -u):$(id -g)" \
    --env HOME=/tmp/maven-home \
    --env MAVEN_CONFIG=/tmp/maven-home/.m2 \
    --env MAVEN_OPTS=-Duser.home=/tmp/maven-home \
    --env JAVA_TOOL_OPTIONS= \
    --volume "${source}:/workspace" \
    --volume "${policy}/settings.xml:/policy/settings.xml:ro" \
    --workdir /workspace \
    --entrypoint /usr/share/maven/bin/mvn \
    "${DOCKER_JAVA_BUILDER_IMAGE}" \
    --batch-mode --show-version --errors --strict-checksums \
    --ignore-transitive-repositories --settings /policy/settings.xml \
    versions:set -DnewVersion=3.7.1 -DgenerateBackupPoms=false \
    2>&1 | tee "${candidate}/docker-java-versions-${suffix}.log"
  ${publisher} audit-transfer-log \
    --log "${candidate}/docker-java-versions-${suffix}.log"
  docker run --rm --platform linux/arm64 \
    --user "$(id -u):$(id -g)" \
    --env HOME=/tmp/maven-home \
    --env MAVEN_CONFIG=/tmp/maven-home/.m2 \
    --env MAVEN_OPTS=-Duser.home=/tmp/maven-home \
    --env JAVA_TOOL_OPTIONS= \
    --volume "${source}:/workspace" \
    --volume "${policy}/settings.xml:/policy/settings.xml:ro" \
    --workdir /workspace \
    --entrypoint /usr/share/maven/bin/mvn \
    "${DOCKER_JAVA_BUILDER_IMAGE}" \
    --batch-mode --show-version --errors --strict-checksums \
    --ignore-transitive-repositories --settings /policy/settings.xml \
    -pl docker-java-transport-zerodep -am clean package -DskipTests \
    2>&1 | tee "${candidate}/docker-java-build-${suffix}.log"
  ${publisher} audit-transfer-log \
    --log "${candidate}/docker-java-build-${suffix}.log"
  ${verify} canonicalize-jar \
    --source "${source}/docker-java-transport-zerodep/target/docker-java-transport-zerodep-3.7.1.jar" \
    --output "${candidate}/docker-java-transport-zerodep-3.7.1-${suffix}.jar"
  ${verify} verify-reviewed-jar \
    --jar "${candidate}/docker-java-transport-zerodep-3.7.1-${suffix}.jar"
}

for suffix in a b; do
  fetch_trino "${suffix}"
  fetch_parquet "${suffix}"
  fetch_docker_java "${suffix}"
done
cmp "${candidate}/docker-java-transport-zerodep-3.7.1-a.jar" \
  "${candidate}/docker-java-transport-zerodep-3.7.1-b.jar"

build_repository() {
  local suffix="$1"
  local trino_source="${temp}/trino-source-${suffix}"
  local parquet_source="${temp}/parquet-source-${suffix}"
  local parquet_repository="${temp}/parquet-repository-${suffix}"
  local repository="${temp}/maven-repository-${suffix}"
  local bun_cache="${temp}/bun-cache-${suffix}"
  local bun_archive="${temp}/bun-linux-aarch64-${suffix}.zip"
  mkdir -p "${parquet_repository}" "${repository}" "${bun_cache}"
  docker run --rm --platform linux/arm64 \
    --user "$(id -u):$(id -g)" \
    --env HOME=/tmp/maven-home \
    --env MAVEN_CONFIG=/tmp/maven-home/.m2 \
    --env MAVEN_OPTS=-Duser.home=/tmp/maven-home \
    --env JAVA_TOOL_OPTIONS= \
    --volume "${parquet_source}:/workspace" \
    --volume "${parquet_repository}:/m2" \
    --volume "${policy}/maven-policy:/policy/.mvn:ro" \
    --volume "${policy}/settings.xml:/policy/settings.xml:ro" \
    --workdir /policy --entrypoint /usr/share/maven/bin/mvn \
    "${TRINO_BUILDER_IMAGE}" \
    --batch-mode --show-version --errors --strict-checksums \
    --ignore-transitive-repositories --settings /policy/settings.xml \
    -Dmaven.repo.local=/m2 \
    -Dproject.build.outputTimestamp="${PARQUET_OUTPUT_TIMESTAMP}" \
    --file /workspace/pom.xml -pl :parquet-jackson -am clean install -DskipTests \
    2>&1 | tee "${candidate}/parquet-transfer-${suffix}.log"
  ${publisher} audit-transfer-log --log "${candidate}/parquet-transfer-${suffix}.log"
  ${bun} download --url "${BUN_URL}" --archive "${bun_archive}"
  test "$(stat --format='%s' "${bun_archive}")" = "${BUN_ARCHIVE_SIZE}"
  echo "${BUN_ARCHIVE_SHA256}  ${bun_archive}" | sha256sum --check --strict
  ${bun} stage --archive "${bun_archive}" --repository "${repository}"
  ${parquet} stage-artifact --checkout "${parquet_source}" \
    --build-repository "${parquet_repository}" --target-repository "${repository}"
  local central_jar="${repository}/com/github/docker-java/docker-java-transport-zerodep/3.7.1/docker-java-transport-zerodep-3.7.1.jar"
  mkdir -p "$(dirname "${central_jar}")"
  curl --proto '=https' --tlsv1.2 --fail --silent --show-error \
    --output "${central_jar}" \
    https://repo.maven.apache.org/maven2/com/github/docker-java/docker-java-transport-zerodep/3.7.1/docker-java-transport-zerodep-3.7.1.jar
  ${verify} stage-jar --repository "${repository}" \
    --candidate "${candidate}/docker-java-transport-zerodep-3.7.1-${suffix}.jar"
  docker run --rm --platform linux/arm64 \
    --user "$(id -u):$(id -g)" \
    --env HOME=/tmp/maven-home \
    --env MAVEN_CONFIG=/tmp/maven-home/.m2 \
    --env MAVEN_OPTS=-Duser.home=/tmp/maven-home \
    --env JAVA_TOOL_OPTIONS= \
    --env CI=true \
    --env BUN_INSTALL_CACHE_DIR="${BUN_CACHE_DIRECTORY}" \
    --env BUN_CONFIG_REGISTRY="${BUN_REGISTRY}" \
    --volume "${trino_source}:/workspace" \
    --volume "${repository}:/m2" \
    --volume "${bun_cache}:${BUN_CACHE_DIRECTORY}" \
    --volume "${policy}/maven-policy:/policy/.mvn:ro" \
    --volume "${policy}/settings.xml:/policy/settings.xml:ro" \
    --workdir /policy --entrypoint /usr/share/maven/bin/mvn \
    "${TRINO_BUILDER_IMAGE}" \
    --batch-mode --show-version --errors --strict-checksums \
    --ignore-transitive-repositories --settings /policy/settings.xml \
    -Dmaven.repo.local=/m2 -Dproject.build.outputTimestamp=2026-07-18T00:36:39Z \
    --file /workspace/pom.xml \
    -pl ':trino-server,:trino-server-core,:trino-server-main,:trino-hdfs,:trino-iceberg' \
    -am clean install -DskipTests -Dmaven.source.skip=true -Dair.check.skip-all \
    2>&1 | tee "${candidate}/trino-transfer-${suffix}.log"
  ${publisher} audit-transfer-log --log "${candidate}/trino-transfer-${suffix}.log"
  ${verify} verify-trino --checkout "${trino_source}"
  ${verify} verify-jar --jar "${repository}/com/github/docker-java/docker-java-transport-zerodep/3.7.1/docker-java-transport-zerodep-3.7.1.jar"
  python3 "${root}/scripts/verify_trino_maven_feasibility.py" prune-vulnerable-inputs \
    --repository "${repository}" --root "${root}"
  python3 "${root}/scripts/package_trino_maven_dependencies.py" \
    prune-reactor-outputs --repository "${repository}"
}

for suffix in a b; do
  build_repository "${suffix}"
done
for suffix in a b; do
  source="${temp}/trino-source-${suffix}"
  repository="${temp}/maven-repository-${suffix}"
  bun_cache="${temp}/bun-cache-${suffix}"
  docker run --rm --platform linux/arm64 --network none \
    --user "$(id -u):$(id -g)" \
    --env HOME=/tmp/maven-home \
    --env MAVEN_CONFIG=/tmp/maven-home/.m2 \
    --env MAVEN_OPTS=-Duser.home=/tmp/maven-home \
    --env JAVA_TOOL_OPTIONS= \
    --env CI=true \
    --env BUN_INSTALL_CACHE_DIR="${BUN_CACHE_DIRECTORY}" \
    --env BUN_CONFIG_REGISTRY="${BUN_REGISTRY}" \
    --volume "${source}:/workspace" \
    --volume "${repository}:/m2:ro" \
    --volume "${bun_cache}:${BUN_CACHE_DIRECTORY}:ro" \
    --volume "${policy}/maven-policy:/policy/.mvn:ro" \
    --volume "${policy}/settings.xml:/policy/settings.xml:ro" \
    --workdir /policy --entrypoint /usr/share/maven/bin/mvn \
    "${TRINO_BUILDER_IMAGE}" \
    --offline --batch-mode --show-version --errors --strict-checksums \
    --ignore-transitive-repositories --settings /policy/settings.xml \
    -Dmaven.repo.local=/m2 \
    -Daether.syncContext.named.basedir.locksDir=/tmp/maven-locks \
    -Dmaven.compiler.debuglevel=source,lines \
    -Dproject.build.outputTimestamp=2026-07-18T00:36:39Z \
    --file /workspace/pom.xml \
    -pl ':trino-server,:trino-server-core,:trino-server-main,:trino-hdfs,:trino-iceberg' \
    -am clean package -DskipTests -Dmaven.source.skip=true -Dair.check.skip-all
  output="${source}/core/trino-server/target/trino-server-483.tar.gz"
  ${publisher} verify-server-distribution --archive "${output}"
  sha256sum "${output}" | cut -d' ' -f1 > "${candidate}/offline-output-${suffix}.sha256"
  stat --format='%s' "${output}" > "${candidate}/offline-output-${suffix}.size"
  ${parquet} seal-artifact \
    --build-repository "${temp}/parquet-repository-${suffix}" \
    --target-repository "${repository}"
  ${verify} manifest-repository --repository "${repository}" \
    --output "${candidate}/maven-repository-${suffix}.json"
done
cmp "${candidate}/maven-repository-a.json" "${candidate}/maven-repository-b.json"
cmp "${candidate}/offline-output-a.sha256" "${candidate}/offline-output-b.sha256"
cmp "${candidate}/offline-output-a.size" "${candidate}/offline-output-b.size"
