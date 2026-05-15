package com.aurora.bgtest.unit;

import com.aurora.bgtest.config.ConfigLoader;
import com.aurora.bgtest.config.TestConfig;
import org.junit.jupiter.api.Test;

import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Verifies all shipped configs parse cleanly via {@link ConfigLoader}.
 *
 * <p>This is a smoke test for the YAML files in {@code configs/}, run on every
 * {@code mvn test}. Catches typos / missing keys instantly.
 */
class ShippedConfigsParseTest {

    private static final Path CONFIGS = Path.of(System.getProperty("user.dir"), "configs");

    @Test
    void customerBaselineParses() throws Exception {
        TestConfig c = ConfigLoader.fromPath(CONFIGS.resolve("customer-baseline.yaml"));
        assertThat(c.name()).isEqualTo("customer-baseline");
        assertThat(c.jdbc().connectTimeout()).isNull();   // <-- the root-cause-omission
        assertThat(c.jdbc().wrapperPlugins())
                .containsExactly("initialConnection", "auroraConnectionTracker", "failover2", "efm2", "bg");
        assertThat(c.workload().retryEnabled()).isFalse();
    }

    @Test
    void v1OptimizedParses() throws Exception {
        TestConfig c = ConfigLoader.fromPath(CONFIGS.resolve("v1-optimized.yaml"));
        assertThat(c.jdbc().connectTimeout()).isEqualTo(3000);
        assertThat(c.jdbc().wrapperPlugins()).containsExactly("failover2", "efm2", "bg");
    }

    @Test
    void v2TighterTimeoutParses() throws Exception {
        TestConfig c = ConfigLoader.fromPath(CONFIGS.resolve("v2-tighter-timeout.yaml"));
        assertThat(c.jdbc().connectTimeout()).isEqualTo(2000);
        assertThat(c.hikari().minimumIdle()).isEqualTo(10);
        assertThat(c.workload().retryEnabled()).isTrue();
        assertThat(c.workload().retryDelayMs()).isEqualTo(50);
    }

    @Test
    void v3AggressiveTimeoutParses() throws Exception {
        TestConfig c = ConfigLoader.fromPath(CONFIGS.resolve("v3-aggressive-timeout.yaml"));
        assertThat(c.jdbc().connectTimeout()).isEqualTo(1000);
    }

    @Test
    void v4CurrentParses() throws Exception {
        TestConfig c = ConfigLoader.fromPath(CONFIGS.resolve("v4-current.yaml"));
        assertThat(c.jdbc().failureDetectionTime()).isEqualTo(6000);
        assertThat(c.jdbc().failureDetectionInterval()).isEqualTo(1000);
        assertThat(c.jdbc().failureDetectionCount()).isEqualTo(3);
        assertThat(c.hikari().minimumIdle()).isEqualTo(10);
        assertThat(c.hikari().maximumPoolSize()).isEqualTo(10);
        assertThat(c.hikari().initializationFailTimeout()).isEqualTo(-1L);
    }

    @Test
    void v5ExperimentalParses() throws Exception {
        TestConfig c = ConfigLoader.fromPath(CONFIGS.resolve("v5-experimental.yaml"));
        assertThat(c.hikari().maximumPoolSize()).isEqualTo(20);
        assertThat(c.hikari().connectionTestQuery()).isNull();   // explicitly disabled in v5
        assertThat(c.workload().retryDelayMs()).isEqualTo(25);
    }

    @Test
    void v6AggressiveParses() throws Exception {
        TestConfig c = ConfigLoader.fromPath(CONFIGS.resolve("v6-aggressive.yaml"));
        assertThat(c.jdbc().connectTimeout()).isEqualTo(500);
        assertThat(c.jdbc().bgHighMs()).isEqualTo(20);
        assertThat(c.jdbc().failureDetectionTime()).isEqualTo(3000);
        assertThat(c.jdbc().failureDetectionInterval()).isEqualTo(500);
        assertThat(c.dnsWarmup().enabled()).isFalse();   // v6 doesn't enable DNS warmup
    }

    @Test
    void v7DnsWarmupParses() throws Exception {
        TestConfig c = ConfigLoader.fromPath(CONFIGS.resolve("v7-dns-warmup.yaml"));
        assertThat(c.dnsWarmup().enabled()).isTrue();
        assertThat(c.dnsWarmup().intervalMs()).isEqualTo(1000);
        assertThat(c.jdbc().connectTimeout()).isEqualTo(500);
    }

    @Test
    void v9TunedParses() throws Exception {
        TestConfig c = ConfigLoader.fromPath(CONFIGS.resolve("v9-tuned.yaml"));
        // H2: no init/test queries
        assertThat(c.hikari().connectionInitSql()).isNull();
        assertThat(c.hikari().connectionTestQuery()).isNull();
        // H3: bg-extended fields
        assertThat(c.jdbc().bgConnectTimeoutMs()).isEqualTo(5000);
        assertThat(c.jdbc().bgIncreasedMs()).isEqualTo(500);
        // H5: longer maxLifetime
        assertThat(c.hikari().maxLifetimeMs()).isEqualTo(300000);
        // 10Hz STATS reporter
        assertThat(c.workload().statsReporterHz()).isEqualTo(10);
        assertThat(c.workload().statsReporterPeriodMs()).isEqualTo(100L);
    }
}
