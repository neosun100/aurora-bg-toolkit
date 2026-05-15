## What this PR does

<!-- Brief summary of the change. Reference any related issues. -->

## Type of change

- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [ ] Documentation update

## Validation

- [ ] `mvn -B verify` passes locally
- [ ] If I changed `LogParser` or its tests, the regression fixture still produces 51.2s/56.3s
- [ ] If I changed a shipped YAML, `ShippedConfigsParseTest` covers the new shape
- [ ] If I changed `JdbcUrlBuilder`, `customerBaselineUrlOmitsConnectTimeout` invariant still holds
- [ ] Documentation updated if behaviour changed

## E2E impact

<!-- Does this change affect the AWS E2E test plan? Cost? Cluster sizing?
     If yes, describe what to watch for in the next E2E run. -->
