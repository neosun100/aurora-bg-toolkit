package com.aurora.bgtest.util;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.net.InetAddress;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Periodically resolves the cluster endpoint to keep the JVM's DNS cache warm.
 *
 * <p>Why: After a Blue/Green switchover, the JVM resolver cache may still hold
 * the stale Blue IP for up to {@code networkaddress.cache.ttl} seconds (default
 * 30s on many JVMs). When the connection pool refills new connections, it
 * could end up connecting to the stale IP and hang.
 *
 * <p>This warmup thread re-resolves the endpoint at a configurable interval,
 * keeping the JVM resolver cache fresh so that recovery uses the new IP
 * immediately.
 *
 * <p>Logging: only logs a state change (IP set changes), not every successful
 * resolution — so a steady DNS keeps the log clean. Failures are logged at
 * WARN level.
 */
public final class DnsWarmupThread {

    private static final Logger LOG = LoggerFactory.getLogger(DnsWarmupThread.class);

    private final String hostname;
    private final int intervalMs;
    private final AtomicReference<String> lastObserved = new AtomicReference<>("");
    private ScheduledExecutorService executor;

    public DnsWarmupThread(String hostname, int intervalMs) {
        this.hostname = hostname;
        this.intervalMs = Math.max(100, intervalMs);
    }

    public synchronized void start() {
        if (executor != null) return;
        executor = Executors.newSingleThreadScheduledExecutor(r -> {
            Thread t = new Thread(r, "bg-dns-warmup");
            t.setDaemon(true);
            return t;
        });
        executor.scheduleAtFixedRate(this::resolveOnce, 0, intervalMs, TimeUnit.MILLISECONDS);
        LOG.info("DNS warmup started: host={} intervalMs={}", hostname, intervalMs);
    }

    public synchronized void stop() {
        if (executor == null) return;
        executor.shutdownNow();
        executor = null;
    }

    private void resolveOnce() {
        try {
            InetAddress[] addrs = InetAddress.getAllByName(hostname);
            StringBuilder sb = new StringBuilder();
            for (InetAddress a : addrs) sb.append(a.getHostAddress()).append(',');
            String now = sb.toString();
            String prev = lastObserved.getAndSet(now);
            if (!prev.equals(now)) {
                LOG.info("DNS warmup: {} -> {}", hostname, now);
            }
        } catch (Exception e) {
            LOG.warn("DNS warmup resolve failed for {}: {}", hostname, e.getMessage());
        }
    }
}
