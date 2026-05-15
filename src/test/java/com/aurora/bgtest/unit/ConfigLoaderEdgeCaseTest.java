package com.aurora.bgtest.unit;

import com.aurora.bgtest.config.ConfigLoader;
import com.aurora.bgtest.config.TestConfig;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Edge cases for {@link ConfigLoader}: missing keys, wrong types, duplicate keys,
 * null-vs-absent semantics for the optional JDBC parameters.
 */
class ConfigLoaderEdgeCaseTest {

    @Test
    void missingNameRejected() {
        String yaml = """
                description: missing name
                database: {port: 3306, database: x, tableTemplate: "t", user: u}
                jdbc: {wrapperPlugins: []}
                hikari: {maximumPoolSize: 1, minimumIdle: 1, initializationFailTimeout: 0,
                  connectionTimeoutMs: 1000, idleTimeoutMs: 1000, maxLifetimeMs: 1000,
                  keepaliveTimeMs: 1000, validationTimeoutMs: 1000}
                workload: {threads: 1, intervalMs: 100, weights: {read: 1, insert: 1, update: 1}}
                """;
        assertThatThrownBy(() -> ConfigLoader.fromString(yaml))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("'name'");
    }

    @Test
    void duplicateKeysRejected() {
        String yaml = """
                name: dup
                name: dup2
                """;
        assertThatThrownBy(() -> ConfigLoader.fromString(yaml));
    }

    @Test
    void portWrongTypeRejected() {
        String yaml = """
                name: t
                database: {port: "not-a-number", database: x, tableTemplate: "t", user: u}
                jdbc: {wrapperPlugins: []}
                hikari: {maximumPoolSize: 1, minimumIdle: 1, initializationFailTimeout: 0,
                  connectionTimeoutMs: 1000, idleTimeoutMs: 1000, maxLifetimeMs: 1000,
                  keepaliveTimeMs: 1000, validationTimeoutMs: 1000}
                workload: {threads: 1, intervalMs: 100, weights: {read: 1, insert: 1, update: 1}}
                """;
        assertThatThrownBy(() -> ConfigLoader.fromString(yaml))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("port");
    }

    @Test
    void absentConnectTimeoutBecomesNull() {
        // Key intentionally not in the YAML
        String yaml = """
                name: t
                database: {port: 3306, database: x, tableTemplate: "t", user: u}
                jdbc:
                  wrapperPlugins: [bg]
                  bgHighMs: 50
                hikari: {maximumPoolSize: 1, minimumIdle: 1, initializationFailTimeout: 0,
                  connectionTimeoutMs: 1000, idleTimeoutMs: 1000, maxLifetimeMs: 1000,
                  keepaliveTimeMs: 1000, validationTimeoutMs: 1000}
                workload: {threads: 1, intervalMs: 100, weights: {read: 1, insert: 1, update: 1}}
                """;
        TestConfig c = ConfigLoader.fromString(yaml);
        assertThat(c.jdbc().connectTimeout()).isNull();
        assertThat(c.jdbc().socketTimeout()).isNull();
    }

    @Test
    void zeroWeightSumRejectedByWorkloadRecord() {
        String yaml = """
                name: t
                database: {port: 3306, database: x, tableTemplate: "t", user: u}
                jdbc: {wrapperPlugins: []}
                hikari: {maximumPoolSize: 1, minimumIdle: 1, initializationFailTimeout: 0,
                  connectionTimeoutMs: 1000, idleTimeoutMs: 1000, maxLifetimeMs: 1000,
                  keepaliveTimeMs: 1000, validationTimeoutMs: 1000}
                workload: {threads: 1, intervalMs: 100, weights: {read: 0, insert: 0, update: 0}}
                """;
        assertThatThrownBy(() -> ConfigLoader.fromString(yaml))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("weights");
    }

    @Test
    void retrySectionDefaultsApplied() {
        String yaml = """
                name: t
                database: {port: 3306, database: x, tableTemplate: "t", user: u}
                jdbc: {wrapperPlugins: []}
                hikari: {maximumPoolSize: 1, minimumIdle: 1, initializationFailTimeout: 0,
                  connectionTimeoutMs: 1000, idleTimeoutMs: 1000, maxLifetimeMs: 1000,
                  keepaliveTimeMs: 1000, validationTimeoutMs: 1000}
                workload: {threads: 1, intervalMs: 100, weights: {read: 1, insert: 1, update: 1}}
                """;
        TestConfig c = ConfigLoader.fromString(yaml);
        assertThat(c.workload().retryEnabled()).isFalse();
        assertThat(c.workload().retryDelayMs()).isZero();
    }

    @Test
    void initializationFailTimeoutNegativeOneAccepted() {
        String yaml = """
                name: t
                database: {port: 3306, database: x, tableTemplate: "t", user: u}
                jdbc: {wrapperPlugins: []}
                hikari: {maximumPoolSize: 1, minimumIdle: 1, initializationFailTimeout: -1,
                  connectionTimeoutMs: 1000, idleTimeoutMs: 1000, maxLifetimeMs: 1000,
                  keepaliveTimeMs: 1000, validationTimeoutMs: 1000}
                workload: {threads: 1, intervalMs: 100, weights: {read: 1, insert: 1, update: 1}}
                """;
        TestConfig c = ConfigLoader.fromString(yaml);
        assertThat(c.hikari().initializationFailTimeout()).isEqualTo(-1L);
    }
}
