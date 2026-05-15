# Bundled `aws-advanced-jdbc-wrapper` jars

These jars are not on Maven Central (only on GitHub Releases at the time of writing).
They're checked into the repo so the build is reproducible without network.

## What's here

| Jar | Source | Original engagement |
|---|---|---|
| `aws-advanced-jdbc-wrapper-3.3.0.jar` | [GitHub release 3.3.0](https://github.com/aws/aws-advanced-jdbc-wrapper/releases/tag/3.3.0) | Customer's primary tested version |
| `aws-advanced-jdbc-wrapper-4.0.0.jar` | [GitHub release 4.0.0](https://github.com/aws/aws-advanced-jdbc-wrapper/releases/tag/4.0.0) | Customer's terminal version |
| `aws-advanced-jdbc-wrapper-4.0.1.jar` | [GitHub release 4.0.1](https://github.com/aws/aws-advanced-jdbc-wrapper/releases/tag/4.0.1) | Latest bugfix (NEW) |

## Setup (run once after cloning)

```bash
./scripts/install-local-wrapper-jars.sh
```

This installs all three jars into the local Maven repository so they
look like normal Maven dependencies.

## Building against a specific version

```bash
mvn package                    # default = 4.0.0
mvn package -Pwrapper-3.3      # 3.3.0
mvn package -Pwrapper-4.1      # 4.0.1 (latest)
mvn package -Pwrapper-mvncentral  # 2.6.0 (Maven Central fallback if you can't run install-local-wrapper-jars.sh)
```

## Verifying integrity

The checksums below are what we downloaded from GitHub on 2026-05-15.

```
$ shasum -a 256 lib/*.jar
```

(The build does not verify checksums automatically; this is intentional —
verification is a one-time review step at jar-update time.)
