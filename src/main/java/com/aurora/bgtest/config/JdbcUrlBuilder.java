package com.aurora.bgtest.config;

import java.util.ArrayList;
import java.util.List;

/**
 * Builds the JDBC URL from a {@link TestConfig.Jdbc} block plus runtime endpoint info.
 *
 * <p>This used to be String.format-based concatenation scattered across 5 .java files
 * in the original engagement; now it's one tested place.
 */
public final class JdbcUrlBuilder {

    private JdbcUrlBuilder() {}

    /**
     * Build a JDBC URL of the form:
     * <pre>jdbc:aws-wrapper:mysql://{endpoint}:{port}/{db}?{params...}</pre>
     *
     * @param endpoint cluster endpoint host (e.g. {@code test-01.cluster-xxx.us-east-1.rds.amazonaws.com})
     * @param port     database port
     * @param database database name
     * @param jdbc     parsed JDBC config block
     * @param clusterId optional clusterId override; if null, derived from first label of endpoint
     * @return fully composed JDBC URL
     */
    public static String build(String endpoint, int port, String database, TestConfig.Jdbc jdbc, String clusterId) {
        if (endpoint == null || endpoint.isBlank()) {
            throw new IllegalArgumentException("endpoint must not be blank");
        }

        String resolvedClusterId = (clusterId != null && !clusterId.isBlank())
                ? clusterId
                : endpoint.split("\\.")[0];

        List<String> params = new ArrayList<>(16);
        // Standard MySQL driver flags (matches customer style).
        params.add("serverTimezone=GMT%2B8");
        params.add("characterEncoding=utf8");
        params.add("useUnicode=true");
        params.add("useSSL=false");

        // Wrapper plugins (pipe-separated).
        if (!jdbc.wrapperPlugins().isEmpty()) {
            params.add("wrapperPlugins=" + String.join(",", jdbc.wrapperPlugins()));
        }

        // Optional timeouts — only emitted when set, so customer-baseline mode reproduces
        // the "no connectTimeout, TCP hangs 30s" behaviour faithfully.
        if (jdbc.connectTimeout() != null) {
            params.add("connectTimeout=" + jdbc.connectTimeout());
        }
        if (jdbc.socketTimeout() != null) {
            params.add("socketTimeout=" + jdbc.socketTimeout());
        }
        if (jdbc.failureDetectionTime() != null) {
            params.add("failureDetectionTime=" + jdbc.failureDetectionTime());
        }
        if (jdbc.failureDetectionInterval() != null) {
            params.add("failureDetectionInterval=" + jdbc.failureDetectionInterval());
        }
        if (jdbc.failureDetectionCount() != null) {
            params.add("failureDetectionCount=" + jdbc.failureDetectionCount());
        }

        // Cluster + Blue/Green identity (always emitted, derived from endpoint).
        params.add("clusterId=" + resolvedClusterId);
        params.add("bgdId=" + resolvedClusterId + "-bgd");

        if (jdbc.bgHighMs() != null) {
            params.add("bgHighMs=" + jdbc.bgHighMs());
        }
        if (jdbc.wrapperLoggerLevel() != null) {
            params.add("wrapperLoggerLevel=" + jdbc.wrapperLoggerLevel());
        }

        return "jdbc:aws-wrapper:mysql://" + endpoint + ":" + port + "/" + database
                + "?" + String.join("&", params);
    }
}
