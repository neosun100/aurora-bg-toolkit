package com.aurora.bgtest.util;

import com.zaxxer.hikari.HikariDataSource;
import com.zaxxer.hikari.pool.HikariPool;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.lang.reflect.Field;

/**
 * Reflectively peeks the internal HikariPool for size diagnostics
 * (total / active / idle / threads-awaiting-connection).
 *
 * <p>HikariCP doesn't expose this on its public API, so we use reflection.
 * A failure here is not fatal — we silently skip and log nothing, since
 * pool stats are diagnostic, not functional.
 */
public final class PoolMonitor {

    private static final Logger LOG = LoggerFactory.getLogger(PoolMonitor.class);

    private PoolMonitor() {}

    public static void logStatus(HikariDataSource ds) {
        Snapshot snap = snapshot(ds);
        if (snap != null) {
            LOG.info("POOL total={} active={} idle={} waiting={}",
                    snap.total, snap.active, snap.idle, snap.waiting);
        }
    }

    public static Snapshot snapshot(HikariDataSource ds) {
        if (ds == null) return null;
        try {
            Field poolField = HikariDataSource.class.getDeclaredField("pool");
            poolField.setAccessible(true);
            HikariPool pool = (HikariPool) poolField.get(ds);
            if (pool == null) return null;
            return new Snapshot(
                    pool.getTotalConnections(),
                    pool.getActiveConnections(),
                    pool.getIdleConnections(),
                    pool.getThreadsAwaitingConnection());
        } catch (NoSuchFieldException | IllegalAccessException e) {
            return null;
        }
    }

    public record Snapshot(int total, int active, int idle, int waiting) {}
}
