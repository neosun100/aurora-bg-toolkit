#!/usr/bin/env bash
# Install the AWS Advanced JDBC Wrapper jars (downloaded into lib/)
# into the local Maven repository so they can be referenced as normal
# Maven dependencies.
#
# Why: versions 3.3.0 / 4.0.0 / 4.0.1 are GitHub-only (NOT on Maven Central
# yet at time of writing). The original HSK engagement specifically tested
# 3.3.0 and 4.0.0, so we want those exact bytes for E2E reproducibility.
#
# Run once after cloning:
#   ./scripts/install-local-wrapper-jars.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LIB="$REPO_ROOT/lib"
GROUP="software.amazon.jdbc"
ARTIFACT="aws-advanced-jdbc-wrapper"

if ! command -v mvn >/dev/null 2>&1; then
    echo "ERROR: mvn not on PATH" >&2
    exit 1
fi

if [[ ! -d "$LIB" ]]; then
    echo "ERROR: $LIB does not exist" >&2
    exit 1
fi

echo "Installing wrapper jars from $LIB into local Maven repository..."

# Each jar's filename embeds its version: aws-advanced-jdbc-wrapper-X.Y.Z.jar
shopt -s nullglob
installed=0
for jar in "$LIB"/${ARTIFACT}-*.jar; do
    fname=$(basename "$jar")
    # Strip prefix and .jar to get the version
    version="${fname#${ARTIFACT}-}"
    version="${version%.jar}"

    echo "  -> ${GROUP}:${ARTIFACT}:${version}"
    mvn -q install:install-file \
        -Dfile="$jar" \
        -DgroupId="$GROUP" \
        -DartifactId="$ARTIFACT" \
        -Dversion="$version" \
        -Dpackaging=jar \
        -DgeneratePom=true
    installed=$((installed + 1))
done

if [[ $installed -eq 0 ]]; then
    echo "ERROR: no wrapper jars found in $LIB" >&2
    exit 1
fi

echo "Done. Installed $installed jar(s)."
echo
echo "Now you can build with any of:"
echo "  mvn package                           # uses the version in pom.xml (default profile)"
echo "  mvn package -Pwrapper-3.3"
echo "  mvn package -Pwrapper-4.0"
echo "  mvn package -Pwrapper-4.1"
