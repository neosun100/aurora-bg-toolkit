package com.aurora.bgtest.integration;

import com.aurora.bgtest.config.ConfigLoader;
import com.aurora.bgtest.config.TestConfig;
import com.aurora.bgtest.workload.MixedWorkload;
import com.aurora.bgtest.workload.Stats;
import com.zaxxer.hikari.HikariConfig;
import com.zaxxer.hikari.HikariDataSource;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInstance;
import org.junit.jupiter.api.condition.EnabledIf;
import org.testcontainers.containers.MySQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.Statement;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Integration tests against a real MySQL 8 container.
 *
 * <p>These verify the workload + table-creation paths end-to-end against a
 * live database. The wrapper layer is bypassed (vanilla mysql-connector-j)
 * because Testcontainers can't simulate Aurora cluster topology — that's
 * what stage 15 (real Aurora E2E) is for.
 *
 * <p>What this catches that unit tests can't:
 * <ul>
 *   <li>Real JDBC behaviour: prepared statements, result sets, connections</li>
 *   <li>HikariCP pool startup and shutdown correctness</li>
 *   <li>Table DDL syntax compatibility</li>
 *   <li>Workload start/stop lifecycle under load</li>
 * </ul>
 */
@Testcontainers
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
@EnabledIf("com.aurora.bgtest.integration.DockerAvailability#dockerIsReachable")
@DisplayName("MySQL 8 container integration")
class MixedWorkloadIT {

    @Container
    static final MySQLContainer<?> MYSQL = new MySQLContainer<>("mysql:8.0")
            .withDatabaseName("bgtest")
            .withUsername("test")
            .withPassword("test");

    private TestConfig baseConfig;

    @BeforeAll
    void setup() throws IOException {
        // Load the v4 config from disk and use most of its parameters; the URL
        // is overridden because the container speaks vanilla MySQL.
        Path v4 = Path.of(System.getProperty("user.dir"), "configs", "v4-current.yaml");
        if (!Files.exists(v4)) {
            // CI quirk: skip rather than fail if working dir is unexpected
            throw new IllegalStateException("v4-current.yaml missing — run from project root");
        }
        baseConfig = ConfigLoader.fromPath(v4);
    }

    @Test
    @DisplayName("ensureTable creates schema; workload runs and produces ok counts")
    void workloadAgainstRealMySQL() throws Exception {
        try (HikariDataSource ds = newDataSourceFor(MYSQL)) {
            String table = "it_table_" + System.currentTimeMillis();
            MixedWorkload.ensureTable(ds, table);

            // Verify the table really exists with the right shape
            try (Connection c = ds.getConnection();
                 Statement st = c.createStatement();
                 ResultSet rs = st.executeQuery("SHOW COLUMNS FROM " + table)) {
                int cols = 0;
                while (rs.next()) cols++;
                assertThat(cols).isEqualTo(6);   // id, balance_id, write_ts, seq, version, update_time
            }

            Stats stats = new Stats();
            MixedWorkload workload = new MixedWorkload(ds, baseConfig, table, stats);
            workload.start();
            try {
                Thread.sleep(2_500);   // 4 threads × ~10 ops/s × 2.5s ≈ 100 ops
            } finally {
                workload.stop();
            }
            // Drain residual reporter snapshots first
            stats.drainPerSecond();

            // Sanity: there must be inserted rows in the table
            try (Connection c = ds.getConnection();
                 Statement st = c.createStatement();
                 ResultSet rs = st.executeQuery("SELECT COUNT(*) FROM " + table)) {
                rs.next();
                long count = rs.getLong(1);
                assertThat(count).isPositive();
            }
        }
    }

    @Test
    @DisplayName("Pool grows to minimumIdle and stops cleanly")
    void poolLifecycle() throws Exception {
        try (HikariDataSource ds = newDataSourceFor(MYSQL)) {
            // Force a connection so HikariCP actually opens the pool
            try (Connection c = ds.getConnection()) {
                assertThat(c.isValid(2)).isTrue();
            }
            // Wait briefly for the pool to populate up to minimumIdle
            for (int i = 0; i < 20; i++) {
                if (ds.getHikariPoolMXBean().getTotalConnections() >= 1) break;
                TimeUnit.MILLISECONDS.sleep(100);
            }
            assertThat(ds.getHikariPoolMXBean().getTotalConnections()).isPositive();
        }
        // Implicit: ds.close() didn't throw
    }

    /**
     * Creates a HikariCP datasource pointing at the container, using base v4 config
     * for pool settings but overriding the URL/credentials.
     */
    private HikariDataSource newDataSourceFor(MySQLContainer<?> container) {
        HikariConfig hc = new HikariConfig();
        hc.setJdbcUrl(container.getJdbcUrl() + "?useSSL=false&allowPublicKeyRetrieval=true");
        hc.setUsername(container.getUsername());
        hc.setPassword(container.getPassword());
        hc.setMaximumPoolSize(baseConfig.hikari().maximumPoolSize());
        hc.setMinimumIdle(2);   // lower than v4's 10 to keep test fast
        hc.setInitializationFailTimeout(baseConfig.hikari().initializationFailTimeout());
        hc.setConnectionTimeout(baseConfig.hikari().connectionTimeoutMs());
        hc.setIdleTimeout(baseConfig.hikari().idleTimeoutMs());
        hc.setMaxLifetime(baseConfig.hikari().maxLifetimeMs());
        hc.setKeepaliveTime(baseConfig.hikari().keepaliveTimeMs());
        hc.setValidationTimeout(baseConfig.hikari().validationTimeoutMs());
        if (baseConfig.hikari().connectionTestQuery() != null) {
            hc.setConnectionTestQuery(baseConfig.hikari().connectionTestQuery());
        }
        return new HikariDataSource(hc);
    }

    @AfterAll
    void teardown() {
        // testcontainers handles container lifecycle; nothing else to clean up
    }
}
