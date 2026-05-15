package com.aurora.bgtest.util;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.net.InetAddress;

/**
 * DNS resolution helper used to verify whether a cluster endpoint resolves to
 * the new (Green) IP after a Blue/Green switch. Used in pre-flight + recovery logging.
 */
public final class DnsUtil {

    private static final Logger LOG = LoggerFactory.getLogger(DnsUtil.class);

    private DnsUtil() {}

    public static void logResolve(String hostname) {
        try {
            long t0 = System.nanoTime();
            InetAddress[] addrs = InetAddress.getAllByName(hostname);
            long ms = (System.nanoTime() - t0) / 1_000_000;
            StringBuilder sb = new StringBuilder("DNS ").append(hostname).append(" -> ");
            for (InetAddress a : addrs) sb.append(a.getHostAddress()).append(' ');
            sb.append('(').append(ms).append("ms)");
            LOG.info(sb.toString());
        } catch (Exception e) {
            LOG.warn("DNS resolve FAILED for {}: {}", hostname, e.getMessage());
        }
    }
}
