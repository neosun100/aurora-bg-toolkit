package com.aurora.bgtest.config;

import java.util.List;
import java.util.Objects;

/**
 * Test configuration POJO. Loaded from YAML by {@link ConfigLoader}.
 *
 * <p>This is a record-like immutable structure. Construction is delegated to
 * the loader; downstream code only reads these values.
 */
public final class TestConfig {

    private final String name;
    private final String description;
    private final Database database;
    private final Jdbc jdbc;
    private final Hikari hikari;
    private final Workload workload;
    private final DnsWarmup dnsWarmup;

    public TestConfig(String name, String description,
                      Database database, Jdbc jdbc, Hikari hikari, Workload workload,
                      DnsWarmup dnsWarmup) {
        this.name = Objects.requireNonNull(name, "config.name is required");
        this.description = description == null ? "" : description;
        this.database = Objects.requireNonNull(database, "database section is required");
        this.jdbc = Objects.requireNonNull(jdbc, "jdbc section is required");
        this.hikari = Objects.requireNonNull(hikari, "hikari section is required");
        this.workload = Objects.requireNonNull(workload, "workload section is required");
        this.dnsWarmup = dnsWarmup == null ? DnsWarmup.disabled() : dnsWarmup;
    }

    /** Backwards-compatible 6-arg constructor; treats DNS warmup as disabled. */
    public TestConfig(String name, String description,
                      Database database, Jdbc jdbc, Hikari hikari, Workload workload) {
        this(name, description, database, jdbc, hikari, workload, DnsWarmup.disabled());
    }

    public String name() { return name; }
    public String description() { return description; }
    public Database database() { return database; }
    public Jdbc jdbc() { return jdbc; }
    public Hikari hikari() { return hikari; }
    public Workload workload() { return workload; }
    public DnsWarmup dnsWarmup() { return dnsWarmup; }

    // ------------------------------------------------------------------
    // Nested config sections
    // ------------------------------------------------------------------

    public static final class Database {
        private final int port;
        private final String database;
        private final String tableTemplate;
        private final String user;

        public Database(int port, String database, String tableTemplate, String user) {
            this.port = port;
            this.database = Objects.requireNonNull(database);
            this.tableTemplate = Objects.requireNonNull(tableTemplate);
            this.user = Objects.requireNonNull(user);
        }
        public int port() { return port; }
        public String database() { return database; }
        public String tableTemplate() { return tableTemplate; }
        public String user() { return user; }
    }

    /**
     * JDBC connection-level options. {@code null} means "do not append to URL"
     * (i.e. let the wrapper / driver use its default).
     */
    public static final class Jdbc {
        private final List<String> wrapperPlugins;
        private final Integer bgHighMs;
        private final Integer bgIncreasedMs;
        private final Integer bgConnectTimeoutMs;
        private final Integer connectTimeout;
        private final Integer socketTimeout;
        private final Integer failureDetectionTime;
        private final Integer failureDetectionInterval;
        private final Integer failureDetectionCount;
        private final String wrapperLoggerLevel;

        public Jdbc(List<String> wrapperPlugins,
                    Integer bgHighMs,
                    Integer bgIncreasedMs,
                    Integer bgConnectTimeoutMs,
                    Integer connectTimeout, Integer socketTimeout,
                    Integer failureDetectionTime, Integer failureDetectionInterval, Integer failureDetectionCount,
                    String wrapperLoggerLevel) {
            this.wrapperPlugins = wrapperPlugins == null ? List.of() : List.copyOf(wrapperPlugins);
            this.bgHighMs = bgHighMs;
            this.bgIncreasedMs = bgIncreasedMs;
            this.bgConnectTimeoutMs = bgConnectTimeoutMs;
            this.connectTimeout = connectTimeout;
            this.socketTimeout = socketTimeout;
            this.failureDetectionTime = failureDetectionTime;
            this.failureDetectionInterval = failureDetectionInterval;
            this.failureDetectionCount = failureDetectionCount;
            this.wrapperLoggerLevel = wrapperLoggerLevel;
        }

        /** Backwards-compatible 8-arg constructor (no bg-extended fields). */
        public Jdbc(List<String> wrapperPlugins,
                    Integer bgHighMs,
                    Integer connectTimeout, Integer socketTimeout,
                    Integer failureDetectionTime, Integer failureDetectionInterval, Integer failureDetectionCount,
                    String wrapperLoggerLevel) {
            this(wrapperPlugins, bgHighMs, null, null,
                    connectTimeout, socketTimeout,
                    failureDetectionTime, failureDetectionInterval, failureDetectionCount,
                    wrapperLoggerLevel);
        }

        public List<String> wrapperPlugins() { return wrapperPlugins; }
        public Integer bgHighMs() { return bgHighMs; }
        public Integer bgIncreasedMs() { return bgIncreasedMs; }
        public Integer bgConnectTimeoutMs() { return bgConnectTimeoutMs; }
        public Integer connectTimeout() { return connectTimeout; }
        public Integer socketTimeout() { return socketTimeout; }
        public Integer failureDetectionTime() { return failureDetectionTime; }
        public Integer failureDetectionInterval() { return failureDetectionInterval; }
        public Integer failureDetectionCount() { return failureDetectionCount; }
        public String wrapperLoggerLevel() { return wrapperLoggerLevel; }
    }

    public static final class Hikari {
        private final int maximumPoolSize;
        private final int minimumIdle;
        private final long initializationFailTimeout;
        private final int connectionTimeoutMs;
        private final int idleTimeoutMs;
        private final int maxLifetimeMs;
        private final int keepaliveTimeMs;
        private final int validationTimeoutMs;
        private final String connectionInitSql;
        private final String connectionTestQuery;
        private final String exceptionOverrideClassName;

        public Hikari(int maximumPoolSize, int minimumIdle, long initializationFailTimeout,
                      int connectionTimeoutMs, int idleTimeoutMs, int maxLifetimeMs,
                      int keepaliveTimeMs, int validationTimeoutMs,
                      String connectionInitSql, String connectionTestQuery,
                      String exceptionOverrideClassName) {
            this.maximumPoolSize = maximumPoolSize;
            this.minimumIdle = minimumIdle;
            this.initializationFailTimeout = initializationFailTimeout;
            this.connectionTimeoutMs = connectionTimeoutMs;
            this.idleTimeoutMs = idleTimeoutMs;
            this.maxLifetimeMs = maxLifetimeMs;
            this.keepaliveTimeMs = keepaliveTimeMs;
            this.validationTimeoutMs = validationTimeoutMs;
            this.connectionInitSql = connectionInitSql;
            this.connectionTestQuery = connectionTestQuery;
            this.exceptionOverrideClassName = exceptionOverrideClassName;
        }
        public int maximumPoolSize() { return maximumPoolSize; }
        public int minimumIdle() { return minimumIdle; }
        public long initializationFailTimeout() { return initializationFailTimeout; }
        public int connectionTimeoutMs() { return connectionTimeoutMs; }
        public int idleTimeoutMs() { return idleTimeoutMs; }
        public int maxLifetimeMs() { return maxLifetimeMs; }
        public int keepaliveTimeMs() { return keepaliveTimeMs; }
        public int validationTimeoutMs() { return validationTimeoutMs; }
        public String connectionInitSql() { return connectionInitSql; }
        public String connectionTestQuery() { return connectionTestQuery; }
        public String exceptionOverrideClassName() { return exceptionOverrideClassName; }
    }

    public static final class Workload {
        private final int threads;
        private final int intervalMs;
        private final int readWeight;
        private final int insertWeight;
        private final int updateWeight;
        private final boolean retryEnabled;
        private final int retryDelayMs;
        private final int statsReporterHz;

        public Workload(int threads, int intervalMs,
                        int readWeight, int insertWeight, int updateWeight,
                        boolean retryEnabled, int retryDelayMs,
                        int statsReporterHz) {
            if (threads < 1) throw new IllegalArgumentException("threads must be >= 1");
            if (readWeight + insertWeight + updateWeight <= 0) {
                throw new IllegalArgumentException("workload weights must sum to > 0");
            }
            if (statsReporterHz < 1 || statsReporterHz > 100) {
                throw new IllegalArgumentException("statsReporterHz must be in [1, 100]");
            }
            this.threads = threads;
            this.intervalMs = intervalMs;
            this.readWeight = readWeight;
            this.insertWeight = insertWeight;
            this.updateWeight = updateWeight;
            this.retryEnabled = retryEnabled;
            this.retryDelayMs = retryDelayMs;
            this.statsReporterHz = statsReporterHz;
        }

        /** Backwards-compatible 7-arg constructor (1 Hz reporter). */
        public Workload(int threads, int intervalMs,
                        int readWeight, int insertWeight, int updateWeight,
                        boolean retryEnabled, int retryDelayMs) {
            this(threads, intervalMs, readWeight, insertWeight, updateWeight,
                    retryEnabled, retryDelayMs, 1);
        }

        public int threads() { return threads; }
        public int intervalMs() { return intervalMs; }
        public int readWeight() { return readWeight; }
        public int insertWeight() { return insertWeight; }
        public int updateWeight() { return updateWeight; }
        public int totalWeight() { return readWeight + insertWeight + updateWeight; }
        public boolean retryEnabled() { return retryEnabled; }
        public int retryDelayMs() { return retryDelayMs; }
        public int statsReporterHz() { return statsReporterHz; }
        public long statsReporterPeriodMs() { return 1000L / statsReporterHz; }
    }

    /** Optional background DNS warmup. Disabled by default for backwards compatibility. */
    public static final class DnsWarmup {
        private final boolean enabled;
        private final int intervalMs;

        public DnsWarmup(boolean enabled, int intervalMs) {
            this.enabled = enabled;
            this.intervalMs = intervalMs <= 0 ? 1000 : intervalMs;
        }

        public static DnsWarmup disabled() { return new DnsWarmup(false, 1000); }

        public boolean enabled() { return enabled; }
        public int intervalMs() { return intervalMs; }
    }
}
