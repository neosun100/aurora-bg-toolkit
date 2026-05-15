package com.aurora.bgtest;

import com.aurora.bgtest.config.ConfigLoader;
import com.aurora.bgtest.config.JdbcUrlBuilder;
import com.aurora.bgtest.config.TestConfig;
import com.aurora.bgtest.util.DnsUtil;
import com.aurora.bgtest.util.DnsWarmupThread;
import com.aurora.bgtest.util.PoolMonitor;
import com.aurora.bgtest.workload.MixedWorkload;
import com.aurora.bgtest.workload.Stats;
import com.zaxxer.hikari.HikariConfig;
import com.zaxxer.hikari.HikariDataSource;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.nio.file.Path;
import java.util.Properties;

/**
 * Main entry point for the Aurora Blue/Green downtime test.
 *
 * <h2>Usage</h2>
 * <pre>{@code
 * java -jar aurora-bg-toolkit-all.jar <config-yaml-path>
 * }</pre>
 *
 * <h2>Required environment variables</h2>
 * <ul>
 *   <li>{@code DB_ENDPOINT} — Aurora cluster endpoint hostname</li>
 *   <li>{@code DB_PASSWORD} — DB user password</li>
 * </ul>
 *
 * <h2>Optional environment variables</h2>
 * <ul>
 *   <li>{@code TABLE_SUFFIX} — appended to the configured table template (e.g. {@code ec2_3} for "EC2 wrapper 3.3.0")</li>
 *   <li>{@code WRAPPER_VERSION} — purely cosmetic, recorded in startup log</li>
 * </ul>
 *
 * <p>The program runs forever (until SIGTERM / SIGINT). The {@code stop-test.sh} script
 * sends SIGTERM and the JVM shutdown hook drains the pool cleanly.
 */
public final class BgDowntimeTest {

    private static final Logger LOG = LoggerFactory.getLogger(BgDowntimeTest.class);

    private BgDowntimeTest() {}

    public static void main(String[] args) throws Exception {
        // Enable timestamped log lines so analyze-logs.py / LogParser can correlate events.
        System.setProperty("org.slf4j.simpleLogger.showDateTime", "true");
        System.setProperty("org.slf4j.simpleLogger.dateTimeFormat", "yyyy-MM-dd HH:mm:ss.SSS");
        System.setProperty("org.slf4j.simpleLogger.defaultLogLevel", "info");

        if (args.length < 1) {
            System.err.println("Usage: BgDowntimeTest <config-yaml-path>");
            System.exit(1);
        }
        TestConfig config = ConfigLoader.fromPath(Path.of(args[0]));

        String endpoint = requireEnv("DB_ENDPOINT");
        String password = requireEnv("DB_PASSWORD");
        String tableSuffix = optionalEnv("TABLE_SUFFIX", "default");
        String wrapperVersion = optionalEnv("WRAPPER_VERSION", "unknown");

        String tableName = config.database().tableTemplate()
                .replace("${CONFIG}", config.name().replace('-', '_'))
                .replace("${SUFFIX}", tableSuffix.replace('-', '_'));

        LOG.info("=== Aurora BG Toolkit ==================================");
        LOG.info("config={} ({})", config.name(), config.description());
        LOG.info("endpoint={} port={} db={}",
                endpoint, config.database().port(), config.database().database());
        LOG.info("table={} wrapperVersion={}", tableName, wrapperVersion);
        LOG.info("java.version={}", System.getProperty("java.version"));
        DnsUtil.logResolve(endpoint);

        String jdbcUrl = JdbcUrlBuilder.build(
                endpoint, config.database().port(), config.database().database(),
                config.jdbc(), null);
        LOG.info("jdbcUrl={}", jdbcUrl);

        HikariConfig hikariConfig = buildHikariConfig(config, jdbcUrl, password);
        try (HikariDataSource dataSource = new HikariDataSource(hikariConfig)) {
            LOG.info("HikariCP pool created (max={}, minIdle={})",
                    config.hikari().maximumPoolSize(), config.hikari().minimumIdle());
            PoolMonitor.logStatus(dataSource);

            createTableWithRetry(dataSource, tableName);

            Stats stats = new Stats();
            MixedWorkload workload = new MixedWorkload(dataSource, config, tableName, stats);

            // Optional DNS warmup background thread (V7+ configs enable it)
            DnsWarmupThread warmup = null;
            if (config.dnsWarmup().enabled()) {
                warmup = new DnsWarmupThread(endpoint, config.dnsWarmup().intervalMs());
                warmup.start();
            }
            DnsWarmupThread finalWarmup = warmup;

            Runtime.getRuntime().addShutdownHook(new Thread(() -> {
                LOG.info("Shutdown hook: stopping workload");
                workload.stop();
                if (finalWarmup != null) finalWarmup.stop();
            }, "bg-shutdown-hook"));

            workload.start();
            // Block forever until the JVM is signalled
            Thread.currentThread().join();
        }
    }

    private static HikariConfig buildHikariConfig(TestConfig config, String jdbcUrl, String password) {
        TestConfig.Hikari h = config.hikari();
        HikariConfig hc = new HikariConfig();
        hc.setJdbcUrl(jdbcUrl);
        hc.setUsername(config.database().user());
        hc.setPassword(password);
        hc.setDriverClassName("software.amazon.jdbc.Driver");
        hc.setMaximumPoolSize(h.maximumPoolSize());
        hc.setMinimumIdle(h.minimumIdle());
        hc.setInitializationFailTimeout(h.initializationFailTimeout());
        hc.setConnectionTimeout(h.connectionTimeoutMs());
        hc.setIdleTimeout(h.idleTimeoutMs());
        hc.setMaxLifetime(h.maxLifetimeMs());
        hc.setKeepaliveTime(h.keepaliveTimeMs());
        hc.setValidationTimeout(h.validationTimeoutMs());
        if (h.connectionInitSql() != null) hc.setConnectionInitSql(h.connectionInitSql());
        if (h.connectionTestQuery() != null) hc.setConnectionTestQuery(h.connectionTestQuery());

        if (h.exceptionOverrideClassName() != null) {
            Properties props = new Properties();
            props.setProperty("exceptionOverrideClassName", h.exceptionOverrideClassName());
            hc.setDataSourceProperties(props);
        }
        return hc;
    }

    private static void createTableWithRetry(HikariDataSource ds, String tableName) throws Exception {
        int attempts = 30;
        Exception last = null;
        for (int i = 0; i < attempts; i++) {
            try {
                MixedWorkload.ensureTable(ds, tableName);
                LOG.info("table {} ready", tableName);
                return;
            } catch (Exception e) {
                last = e;
                LOG.warn("createTable retry {}/{}: {}", i + 1, attempts, e.getMessage());
                Thread.sleep(2000);
            }
        }
        throw new IllegalStateException("createTable failed after " + attempts + " attempts", last);
    }

    private static String requireEnv(String key) {
        String v = System.getenv(key);
        if (v == null || v.isBlank()) {
            throw new IllegalStateException("Required environment variable " + key + " is not set");
        }
        return v;
    }

    private static String optionalEnv(String key, String def) {
        String v = System.getenv(key);
        return (v == null || v.isBlank()) ? def : v;
    }
}
