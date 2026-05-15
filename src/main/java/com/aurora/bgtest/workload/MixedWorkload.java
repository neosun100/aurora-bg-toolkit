package com.aurora.bgtest.workload;

import com.aurora.bgtest.config.TestConfig;
import com.aurora.bgtest.util.PoolMonitor;
import com.zaxxer.hikari.HikariDataSource;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import javax.sql.DataSource;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.Statement;
import java.util.Objects;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Drives a mixed read/insert/update workload against the database.
 *
 * <p>Behaviour matches the customer's V4 program:
 * <ul>
 *   <li>fixed thread pool of {@code workload.threads}</li>
 *   <li>each thread loops: pick op (weighted) → execute → sleep {@code intervalMs}</li>
 *   <li>per-second reporter logs aggregate counts</li>
 *   <li>structured log line {@code WRITE_FAIL ... consecutive=N} so that the log parser
 *       can compute downtime windows post-hoc</li>
 * </ul>
 */
public final class MixedWorkload {

    private static final Logger LOG = LoggerFactory.getLogger(MixedWorkload.class);

    private final HikariDataSource dataSource;
    private final TestConfig config;
    private final String tableName;
    private final Stats stats;
    private final WeightedOperationPicker picker;
    private final AtomicBoolean running = new AtomicBoolean(false);
    private ExecutorService workers;
    private ScheduledExecutorService reporter;

    public MixedWorkload(HikariDataSource dataSource, TestConfig config, String tableName, Stats stats) {
        this.dataSource = Objects.requireNonNull(dataSource);
        this.config = Objects.requireNonNull(config);
        this.tableName = Objects.requireNonNull(tableName);
        this.stats = Objects.requireNonNull(stats);
        this.picker = new WeightedOperationPicker(config.workload());
    }

    public void start() {
        if (!running.compareAndSet(false, true)) {
            throw new IllegalStateException("workload already running");
        }
        TestConfig.Workload wl = config.workload();
        workers = Executors.newFixedThreadPool(wl.threads(), r -> {
            Thread t = new Thread(r, "bg-workload");
            t.setDaemon(false);
            return t;
        });
        for (int i = 0; i < wl.threads(); i++) {
            workers.submit(this::runLoop);
        }
        startReporter();
        LOG.info("Workload started: {} threads, intervalMs={}, R:I:U={}:{}:{}, retry={}, reporter={}Hz",
                wl.threads(), wl.intervalMs(),
                wl.readWeight(), wl.insertWeight(), wl.updateWeight(),
                wl.retryEnabled() ? "on/" + wl.retryDelayMs() + "ms" : "off",
                wl.statsReporterHz());
    }

    public void stop() {
        if (!running.compareAndSet(true, false)) return;
        if (workers != null) workers.shutdownNow();
        if (reporter != null) reporter.shutdownNow();
    }

    public boolean isRunning() { return running.get(); }

    // ------------------------------------------------------------------
    // Worker loop
    // ------------------------------------------------------------------

    private void runLoop() {
        TestConfig.Workload wl = config.workload();
        while (running.get() && !Thread.currentThread().isInterrupted()) {
            try {
                OperationType op = picker.pick();
                switch (op) {
                    case READ -> doRead();
                    case INSERT -> doInsert();
                    case UPDATE -> doUpdate();
                }
            } catch (Exception unexpected) {
                LOG.error("unexpected loop error", unexpected);
            }
            if (wl.intervalMs() > 0) {
                try { Thread.sleep(wl.intervalMs()); }
                catch (InterruptedException ie) { Thread.currentThread().interrupt(); return; }
            }
        }
    }

    private void doInsert() {
        long seq = stats.nextWriteSeq();
        long t0 = System.nanoTime();
        int attempts = config.workload().retryEnabled() ? 2 : 1;
        for (int attempt = 0; attempt < attempts; attempt++) {
            try (Connection c = dataSource.getConnection();
                 PreparedStatement ps = c.prepareStatement(
                         "INSERT INTO " + tableName +
                         " (balance_id, write_ts, seq, version, update_time)" +
                         " VALUES (?, NOW(3), ?, 0, NOW(3))")) {
                long balanceId = System.currentTimeMillis() * 1000 + seq % 1000;
                ps.setLong(1, balanceId);
                ps.setInt(2, (int) seq);
                ps.executeUpdate();
                stats.setLastWrittenId(balanceId);
                stats.recordWriteOk();
                return;
            } catch (Exception e) {
                if (attempt + 1 < attempts) {
                    sleepQuiet(config.workload().retryDelayMs());
                    continue;
                }
                int fails = stats.recordWriteFail();
                long elapsedMs = (System.nanoTime() - t0) / 1_000_000;
                LOG.warn("WRITE_FAIL seq={} elapsed_ms={} consecutive={} err={}: {}",
                        seq, elapsedMs, fails, e.getClass().getSimpleName(), e.getMessage());
                if (fails == 1 || fails % 10 == 0) {
                    PoolMonitor.logStatus(dataSource);
                }
            }
        }
    }

    private void doUpdate() {
        long id = stats.lastWrittenId();
        if (id <= 0) { doInsert(); return; }
        long seq = stats.nextWriteSeq();
        long t0 = System.nanoTime();
        try (Connection c = dataSource.getConnection();
             PreparedStatement ps = c.prepareStatement(
                     "UPDATE " + tableName + " SET version = IFNULL(version,0)+1, update_time = NOW(3) WHERE balance_id = ?")) {
            ps.setLong(1, id);
            int rows = ps.executeUpdate();
            if (rows == 0) { doInsert(); return; }
            stats.recordWriteOk();
        } catch (Exception e) {
            int fails = stats.recordWriteFail();
            long elapsedMs = (System.nanoTime() - t0) / 1_000_000;
            LOG.warn("WRITE_FAIL seq={} elapsed_ms={} consecutive={} err={}: {}",
                    seq, elapsedMs, fails, e.getClass().getSimpleName(), e.getMessage());
        }
    }

    private void doRead() {
        long seq = stats.nextReadSeq();
        long t0 = System.nanoTime();
        int attempts = config.workload().retryEnabled() ? 2 : 1;
        for (int attempt = 0; attempt < attempts; attempt++) {
            try (Connection c = dataSource.getConnection()) {
                long id = stats.lastWrittenId();
                if (id <= 0) {
                    try (Statement st = c.createStatement();
                         ResultSet rs = st.executeQuery("SELECT COUNT(*) FROM " + tableName)) {
                        rs.next();
                    }
                } else {
                    try (PreparedStatement ps = c.prepareStatement(
                            "SELECT * FROM " + tableName + " WHERE balance_id = ?")) {
                        ps.setLong(1, id);
                        try (ResultSet rs = ps.executeQuery()) {
                            rs.next();
                        }
                    }
                }
                stats.recordReadOk();
                return;
            } catch (Exception e) {
                if (attempt + 1 < attempts) {
                    sleepQuiet(config.workload().retryDelayMs());
                    continue;
                }
                int fails = stats.recordReadFail();
                long elapsedMs = (System.nanoTime() - t0) / 1_000_000;
                LOG.warn("READ_FAIL seq={} elapsed_ms={} consecutive={} err={}: {}",
                        seq, elapsedMs, fails, e.getClass().getSimpleName(), e.getMessage());
            }
        }
    }

    private static void sleepQuiet(long ms) {
        if (ms <= 0) return;
        try { Thread.sleep(ms); } catch (InterruptedException ie) { Thread.currentThread().interrupt(); }
    }

    private void startReporter() {
        reporter = Executors.newSingleThreadScheduledExecutor(r -> {
            Thread t = new Thread(r, "bg-stats-reporter");
            t.setDaemon(true);
            return t;
        });
        long periodMs = config.workload().statsReporterPeriodMs();
        reporter.scheduleAtFixedRate(() -> {
            Stats.Snapshot s = stats.drainPerSecond();
            if (s.hasFailures()) {
                LOG.warn("STATS write_ok={} write_fail={} read_ok={} read_fail={}",
                        s.writeOk(), s.writeFail(), s.readOk(), s.readFail());
                PoolMonitor.logStatus(dataSource);
            } else {
                LOG.info("STATS write_ok={} read_ok={}", s.writeOk(), s.readOk());
            }
        }, periodMs, periodMs, TimeUnit.MILLISECONDS);
    }

    /** Convenience: bootstrap the test table if it doesn't exist yet. */
    public static void ensureTable(DataSource ds, String tableName) throws Exception {
        try (Connection c = ds.getConnection(); Statement st = c.createStatement()) {
            st.executeUpdate(
                    "CREATE TABLE IF NOT EXISTS " + tableName + " (" +
                    " id BIGINT AUTO_INCREMENT PRIMARY KEY," +
                    " balance_id BIGINT NOT NULL," +
                    " write_ts DATETIME(3) NOT NULL," +
                    " seq INT NOT NULL," +
                    " version BIGINT DEFAULT 0," +
                    " update_time DATETIME(3)," +
                    " KEY idx_balance_id (balance_id)," +
                    " KEY idx_write_ts (write_ts)" +
                    ") ENGINE=InnoDB");
        }
    }
}
