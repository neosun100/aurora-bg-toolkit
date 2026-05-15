package com.aurora.bgtest.integration;

import org.testcontainers.DockerClientFactory;

/**
 * Detects whether Testcontainers can actually talk to the local Docker daemon.
 * If not (e.g. OrbStack rejecting old API version), tests that need real
 * containers are skipped via JUnit's {@code @EnabledIf}.
 *
 * <p>This is a pragmatic choice: a missing Docker environment is a property
 * of the developer's machine, not a regression in our code, and we don't want
 * the suite to fail noisily over it. CI runs on Linux with vanilla Docker
 * which is always reachable. The real-Aurora E2E tests in stage 15 cover
 * what Testcontainers cannot.
 */
public final class DockerAvailability {

    private DockerAvailability() {}

    private static volatile Boolean cached;

    public static boolean dockerIsReachable() {
        if (cached != null) return cached;
        synchronized (DockerAvailability.class) {
            if (cached != null) return cached;
            try {
                DockerClientFactory.instance().client().pingCmd().exec();
                cached = true;
            } catch (Throwable t) {
                cached = false;
            }
            return cached;
        }
    }
}
