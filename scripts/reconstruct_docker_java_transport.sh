#!/usr/bin/env bash
set -euo pipefail

suffix="${1:?suffix is required}"
source_dir="${2:?source directory is required}"
candidate_dir="${3:?candidate directory is required}"
root="${GITHUB_WORKSPACE:?}"
verify="python3 ${root}/scripts/verify_trino_maven_security_feasibility.py"
publisher="python3 ${root}/scripts/verify_trino_dependency_publisher.py"
policy="${root}/bootstrap/trino/v483"

test ! -e "${source_dir}"
mkdir -p "${candidate_dir}"
git init "${source_dir}"
git -C "${source_dir}" remote add origin "${DOCKER_JAVA_SOURCE_REPOSITORY}"
git -C "${source_dir}" fetch --depth=1 origin \
  "${DOCKER_JAVA_SOURCE_COMMIT}" \
  "refs/tags/${DOCKER_JAVA_SOURCE_TAG}:refs/tags/${DOCKER_JAVA_SOURCE_TAG}"
test "$(git -C "${source_dir}" rev-parse "refs/tags/${DOCKER_JAVA_SOURCE_TAG}^{}")" \
  = "${DOCKER_JAVA_SOURCE_COMMIT}"
git -C "${source_dir}" checkout --detach "${DOCKER_JAVA_SOURCE_COMMIT}"
test "$(git -C "${source_dir}" rev-parse 'HEAD^{tree}')" \
  = "${DOCKER_JAVA_SOURCE_TREE}"
${verify} apply-docker-java --root "${root}" --checkout "${source_dir}"

docker run --rm --platform linux/arm64 \
  --user "$(id -u):$(id -g)" \
  --env HOME=/tmp/maven-home \
  --env MAVEN_CONFIG=/tmp/maven-home/.m2 \
  --env MAVEN_OPTS=-Duser.home=/tmp/maven-home \
  --env JAVA_TOOL_OPTIONS= \
  --volume "${source_dir}:/workspace" \
  --volume "${policy}/settings.xml:/policy/settings.xml:ro" \
  --workdir /workspace \
  --entrypoint /usr/share/maven/bin/mvn \
  "${DOCKER_JAVA_BUILDER_IMAGE}" \
  --batch-mode --show-version --errors --strict-checksums \
  --ignore-transitive-repositories --settings /policy/settings.xml \
  versions:set -DnewVersion=3.7.1 -DgenerateBackupPoms=false \
  2>&1 | tee "${candidate_dir}/docker-java-versions-${suffix}.log"
${publisher} audit-transfer-log \
  --log "${candidate_dir}/docker-java-versions-${suffix}.log"

docker run --rm --platform linux/arm64 \
  --user "$(id -u):$(id -g)" \
  --env HOME=/tmp/maven-home \
  --env MAVEN_CONFIG=/tmp/maven-home/.m2 \
  --env MAVEN_OPTS=-Duser.home=/tmp/maven-home \
  --env JAVA_TOOL_OPTIONS= \
  --volume "${source_dir}:/workspace" \
  --volume "${policy}/settings.xml:/policy/settings.xml:ro" \
  --workdir /workspace \
  --entrypoint /usr/share/maven/bin/mvn \
  "${DOCKER_JAVA_BUILDER_IMAGE}" \
  --batch-mode --show-version --errors --strict-checksums \
  --ignore-transitive-repositories --settings /policy/settings.xml \
  -pl docker-java-transport-zerodep -am clean package -DskipTests \
  2>&1 | tee "${candidate_dir}/docker-java-build-${suffix}.log"
${publisher} audit-transfer-log \
  --log "${candidate_dir}/docker-java-build-${suffix}.log"

${verify} canonicalize-jar \
  --source "${source_dir}/docker-java-transport-zerodep/target/docker-java-transport-zerodep-3.7.1.jar" \
  --output "${candidate_dir}/docker-java-transport-zerodep-3.7.1-${suffix}.jar"
${verify} verify-reviewed-jar \
  --jar "${candidate_dir}/docker-java-transport-zerodep-3.7.1-${suffix}.jar"
