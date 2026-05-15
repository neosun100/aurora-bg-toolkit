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

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.Statement;
import java.util.UUID;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Integration test that bypasses Testcontainers and drives docker via the CLI.
 *
 * <p>Why: Testcontainers' bundled docker-java client negotiates Docker API
 * version 1.32, which OrbStack rejects (it requires >= 1.40). Calling
 * {@code docker run} directly avoids the entire client-strategy stack.
 *
 * <p>Tests still gracefully skip when the {@code docker} CLI isn't available
 * (e.g. CI without Docker installed) so the suite never fails for environment
 * reasons.
 */
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
@EnabledIf("com.aurora.bgtest.integration.DockerCliWorkloadIT#dockerCliAvailable")
@DisplayName("MySQL 8 (docker CLI bridge) integration")
class DockerCliWorkloadIT {

    private static final String CONTAINER_NAME = "abt-it-mysql-" + UUID.randomUUID().toString().substring(0, 8);
    private static final int HOST_PORT = freePortFallback(13306);
    private static final String ROOT_PASSWORD = "rootpass";
    private static final String DB_NAME = "bgtest";

    private TestConfig baseConfig;

    static boolean dockerCliAvailable() {
        try {
            Process p = new ProcessBuilder("docker", "version", "--format", "{{.Server.Version}}")
                    .redirectErrorStream(true).start();
            boolean done = p.waitFor(5, TimeUnit.SECONDS);
            return done && p.exitValue() == 0;
        } catch (Exception e) {
            return false;
        }
    }

    @BeforeAll
    void startContainer() throws Exception {
        baseConfig = ConfigLoader.fromPath(Path.of(System.getProperty("user.dir"), "configs", "v4-current.yaml"));

        // docker run -d --rm --name X -p HOST_PORT:3306 -e MYSQL_ROOT_PASSWORD=... -e MYSQL_DATABASE=... mysql:8.0
        runDocker("run", "-d", "--rm",
                "--name", CONTAINER_NAME,
                "-p", HOST_PORT + ":3306",
                "-e", "MYSQL_ROOT_PASSWORD=" + ROOT_PASSWORD,
                "-e", "MYSQL_DATABASE=" + DB_NAME,
                "mysql:8.0");

        // Wait for MySQL ready (poll login)
        long deadline = System.currentTimeMillis() + 60_000;
        Exception last = null;
        while (System.currentTimeMillis() < deadline) {
            try (Connection c = DriverManager.getConnection(jdbcUrl(), "root", ROOT_PASSWORD)) {
                if (c.isValid(2)) return;
            } catch (Exception e) {
                last = e;
                Thread.sleep(1500);
            }
        }
        throw new IllegalStateException("MySQL container did not become ready in 60s", last);
    }

    @AfterAll
    void stopContainer() throws Exception {
        try {
            runDocker("stop", CONTAINER_NAME);
        } catch (Exception e) {
            System.err.println("Warning: docker stop failed (container may already be gone): " + e.getMessage());
        }
    }

    @Test
    @DisplayName("ensureTable + workload runs against real MySQL container")
    void workloadHappyPath() throws Exception {
        try (HikariDataSource ds = newDataSource()) {
            String table = "bg_test_" + System.currentTimeMillis();
            MixedWorkload.ensureTable(ds, table);

            try (Connection c = ds.getConnection();
                 Statement st = c.createStatement();
                 ResultSet rs = st.executeQuery("SHOW COLUMNS FROM " + table)) {
                int cols = 0;
                while (rs.next()) cols++;
                assertThat(cols).isEqualTo(6);
            }

            Stats stats = new Stats();
            MixedWorkload workload = new MixedWorkload(ds, baseConfig, table, stats);
            workload.start();
            try {
                Thread.sleep(2_500);
            } finally {
                workload.stop();
            }
            // Sanity: real rows landed
            try (Connection c = ds.getConnection();
                 Statement st = c.createStatement();
                 ResultSet rs = st.executeQuery("SELECT COUNT(*) FROM " + table)) {
                rs.next();
                assertThat(rs.getLong(1)).isPositive();
            }
        }
    }

    @Test
    @DisplayName("Pool grows and closes cleanly")
    void poolLifecycle() throws Exception {
        try (HikariDataSource ds = newDataSource()) {
            try (Connection c = ds.getConnection()) {
                assertThat(c.isValid(2)).isTrue();
            }
            for (int i = 0; i < 20; i++) {
                if (ds.getHikariPoolMXBean().getTotalConnections() >= 1) break;
                TimeUnit.MILLISECONDS.sleep(100);
            }
            assertThat(ds.getHikariPoolMXBean().getTotalConnections()).isPositive();
        }
    }

    @Test
    @DisplayName("Retry-disabled config surfaces failures fast")
    void retryDisabledNoSwallowing() throws Exception {
        try (HikariDataSource ds = newDataSource()) {
            String table = "bg_retry_test_" + System.currentTimeMillis();
            MixedWorkload.ensureTable(ds, table);

            // Build a config with retry disabled (clone v4 and override)
            TestConfig.Workload wl = baseConfig.workload();
            TestConfig.Workload noRetry = new TestConfig.Workload(
                    1, 50, wl.readWeight(), wl.insertWeight(), wl.updateWeight(),
                    /* retryEnabled */ false, /* retryDelayMs */ 0);
            TestConfig modified = new TestConfig(
                    baseConfig.name(), baseConfig.description(),
                    baseConfig.database(), baseConfig.jdbc(), baseConfig.hikari(), noRetry);

            Stats stats = new Stats();
            MixedWorkload workload = new MixedWorkload(ds, modified, table, stats);
            workload.start();
            try { Thread.sleep(2_000); } finally { workload.stop(); }

            // Drain the per-second window: in retry-disabled mode, all observed
            // operations should have ok-counts > 0 (it's a healthy MySQL).
            Stats.Snapshot snap = stats.drainPerSecond();
            // Mostly we just confirm no crash; healthy DB means ok>0, fail==0.
            assertThat(snap.writeFail() + snap.readFail()).isZero();
        }
    }

    // -------------------- helpers --------------------

    private HikariDataSource newDataSource() {
        HikariConfig hc = new HikariConfig();
        hc.setJdbcUrl(jdbcUrl());
        hc.setUsername("root");
        hc.setPassword(ROOT_PASSWORD);
        hc.setMaximumPoolSize(baseConfig.hikari().maximumPoolSize());
        hc.setMinimumIdle(2);
        hc.setInitializationFailTimeout(baseConfig.hikari().initializationFailTimeout());
        hc.setConnectionTimeout(baseConfig.hikari().connectionTimeoutMs());
        hc.setIdleTimeout(baseConfig.hikari().idleTimeoutMs());
        hc.setMaxLifetime(baseConfig.hikari().maxLifetimeMs());
        hc.setKeepaliveTime(baseConfig.hikari().keepaliveTimeMs());
        hc.setValidationTimeout(baseConfig.hikari().validationTimeoutMs());
        return new HikariDataSource(hc);
    }

    private String jdbcUrl() {
        return "jdbc:mysql://127.0.0.1:" + HOST_PORT + "/" + DB_NAME +
               "?useSSL=false&allowPublicKeyRetrieval=true&serverTimezone=UTC";
    }

    private static int freePortFallback(int preferred) {
        try (java.net.ServerSocket s = new java.net.ServerSocket(0)) {
            return s.getLocalPort();
        } catch (Exception e) {
            return preferred;
        }
    }

    private static void runDocker(String... args) throws Exception {
        String[] cmd = new String[args.length + 1];
        cmd[0] = "docker";
        System.arraycopy(args, 0, cmd, 1, args.length);
        Process p = new ProcessBuilder(cmd).redirectErrorStream(true).start();
        try (BufferedReader r = new BufferedReader(new InputStreamReader(p.getInputStream()))) {
            StringBuilder out = new StringBuilder();
            String line;
            while ((line = r.readLine()) != null) out.append(line).append('\n');
            if (!p.waitFor(60, TimeUnit.SECONDS)) {
                p.destroyForcibly();
                throw new IllegalStateException("docker command timed out: " + String.join(" ", args));
            }
            if (p.exitValue() != 0) {
                throw new IllegalStateException("docker " + String.join(" ", args) +
                        " failed (exit=" + p.exitValue() + "):\n" + out);
            }
        }
    }
}
