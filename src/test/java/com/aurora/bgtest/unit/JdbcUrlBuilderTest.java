package com.aurora.bgtest.unit;

import com.aurora.bgtest.config.JdbcUrlBuilder;
import com.aurora.bgtest.config.TestConfig;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Verifies {@link JdbcUrlBuilder} composes URLs correctly.
 *
 * <p>The most important invariants:
 * <ul>
 *   <li>customer-baseline mode (connectTimeout=null) MUST omit the parameter,
 *       so the wrapper falls through to the default 30s TCP timeout — that's the bug.</li>
 *   <li>v4 mode emits all explicit timeouts.</li>
 *   <li>clusterId derives from endpoint's first label by default.</li>
 * </ul>
 */
class JdbcUrlBuilderTest {

    private static final String EP = "test-01.cluster-abcdef.us-east-1.rds.amazonaws.com";

    @Test
    void blankEndpointRejected() {
        assertThatThrownBy(() ->
                JdbcUrlBuilder.build("", 3306, "demo",
                        new TestConfig.Jdbc(List.of(), null, null, null, null, null, null, null), null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("endpoint");
    }

    @Test
    void customerBaselineUrlOmitsConnectTimeout() {
        TestConfig.Jdbc jdbc = new TestConfig.Jdbc(
                List.of("initialConnection", "auroraConnectionTracker", "failover2", "efm2", "bg"),
                50,
                /* connectTimeout */ null,
                /* socketTimeout */ null,
                null, null, null,
                "FINEST");
        String url = JdbcUrlBuilder.build(EP, 4488, "demo", jdbc, null);
        assertThat(url)
                .contains("jdbc:aws-wrapper:mysql://" + EP + ":4488/demo")
                .contains("wrapperPlugins=initialConnection,auroraConnectionTracker,failover2,efm2,bg")
                .contains("clusterId=test-01")
                .contains("bgdId=test-01-bgd")
                .contains("bgHighMs=50")
                .doesNotContain("connectTimeout=")
                .doesNotContain("socketTimeout=");
    }

    @Test
    void v4UrlEmitsAllExplicitTimeouts() {
        TestConfig.Jdbc jdbc = new TestConfig.Jdbc(
                List.of("failover2", "efm2", "bg"),
                50, 1000, 3000, 6000, 1000, 3, "FINEST");
        String url = JdbcUrlBuilder.build(EP, 4488, "demo", jdbc, null);
        assertThat(url)
                .contains("connectTimeout=1000")
                .contains("socketTimeout=3000")
                .contains("failureDetectionTime=6000")
                .contains("failureDetectionInterval=1000")
                .contains("failureDetectionCount=3")
                .contains("wrapperPlugins=failover2,efm2,bg");
    }

    @Test
    void emptyPluginListSkipsParam() {
        TestConfig.Jdbc jdbc = new TestConfig.Jdbc(
                List.of(), null, null, null, null, null, null, null);
        String url = JdbcUrlBuilder.build(EP, 4488, "demo", jdbc, null);
        assertThat(url).doesNotContain("wrapperPlugins=");
    }

    @Test
    void clusterIdOverrideRespected() {
        TestConfig.Jdbc jdbc = new TestConfig.Jdbc(
                List.of("bg"), null, null, null, null, null, null, null);
        String url = JdbcUrlBuilder.build(EP, 4488, "demo", jdbc, "custom-cluster");
        assertThat(url)
                .contains("clusterId=custom-cluster")
                .contains("bgdId=custom-cluster-bgd")
                .doesNotContain("clusterId=test-01");
    }

    @Test
    void parameterOrderIsStable() {
        TestConfig.Jdbc jdbc = new TestConfig.Jdbc(
                List.of("failover2", "efm2", "bg"),
                50, 1000, 3000, 6000, 1000, 3, "FINEST");
        String url1 = JdbcUrlBuilder.build(EP, 4488, "demo", jdbc, null);
        String url2 = JdbcUrlBuilder.build(EP, 4488, "demo", jdbc, null);
        assertThat(url1).isEqualTo(url2);
    }
}
