package com.aurora.bgtest.unit;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Smoke test for stage 2: verifies that JUnit 5 + AssertJ are wired correctly.
 * Will be deleted once real unit tests land in stage 5.
 */
class SkeletonSmokeTest {

    @Test
    void junit5AndAssertJWired() {
        assertThat(2 + 2).isEqualTo(4);
    }
}
