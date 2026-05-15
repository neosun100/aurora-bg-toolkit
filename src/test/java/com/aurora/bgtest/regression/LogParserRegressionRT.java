package com.aurora.bgtest.regression;

import com.aurora.bgtest.analysis.LogParser;
import com.aurora.bgtest.analysis.LogParser.DowntimeWindow;
import com.aurora.bgtest.analysis.LogParser.Kind;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.nio.file.Path;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Regression tests using a real, anonymized log from the original HSK engagement.
 *
 * <p>The fixture is {@code samples/reference-logs/customer-baseline-ec2-wrapper4-trimmed.log}
 * — a trim of {@code logs/test-01_customer_20260514_135906/ec2_wrapper4.log}, which
 * was originally reported in {@code REPORT-customer-config.md} as showing
 * <strong>56.6s data write downtime</strong>.
 *
 * <p>If LogParser ever drifts and stops correctly identifying the FAIL→RECOVERED
 * windows in this real log, this test will catch it instantly.
 */
@DisplayName("LogParser regression — real customer-baseline log")
class LogParserRegressionRT {

    private static final Path FIXTURE = Path.of(System.getProperty("user.dir"),
            "samples", "reference-logs", "customer-baseline-ec2-wrapper4-trimmed.log");

    @Test
    @DisplayName("Recovers the exact two downtime windows the original report described")
    void identifiesBothWindows() throws Exception {
        List<DowntimeWindow> windows = LogParser.windowsFrom(FIXTURE);
        assertThat(windows)
                .as("Expected exactly one WRITE window + one READ window")
                .hasSize(2);

        DowntimeWindow write = windows.stream().filter(w -> w.kind() == Kind.WRITE_FAIL).findFirst().orElseThrow();
        DowntimeWindow read = windows.stream().filter(w -> w.kind() == Kind.READ_FAIL).findFirst().orElseThrow();

        // WRITE_FAIL streak in the source log:
        //   first:  14:00:48.112
        //   recover:14:01:39.354
        //   --> 51.242s
        assertThat(write.durationMs()).isBetween(51_000L, 52_000L);

        // READ_FAIL streak:
        //   first:  14:00:43.053
        //   recover:14:01:39.337
        //   --> 56.284s
        assertThat(read.durationMs()).isBetween(56_000L, 57_000L);
    }

    @Test
    @DisplayName("Total event count matches what the report described")
    void totalEventCount() throws Exception {
        List<LogParser.Event> events = LogParser.parse(FIXTURE);
        long writeFails = events.stream().filter(e -> e.kind() == Kind.WRITE_FAIL).count();
        long readFails = events.stream().filter(e -> e.kind() == Kind.READ_FAIL).count();
        long writeRec = events.stream().filter(e -> e.kind() == Kind.WRITE_RECOVERED).count();
        long readRec = events.stream().filter(e -> e.kind() == Kind.READ_RECOVERED).count();

        // Match what the underlying log actually contains:
        //   12 WRITE_FAIL + 1 WRITE_RECOVERED
        //   35 READ_FAIL  + 1 READ_RECOVERED
        assertThat(writeFails).isEqualTo(12);
        assertThat(readFails).isEqualTo(35);
        assertThat(writeRec).isEqualTo(1);
        assertThat(readRec).isEqualTo(1);
    }

    @Test
    @DisplayName("The legacy V4 println timestamp format is fully supported")
    void legacyTimestampFormat() throws Exception {
        // Every event in the fixture uses [yyyy-MM-dd HH:mm:ss.SSS] brackets.
        // If the parser regresses and rejects this format, no events would parse.
        List<LogParser.Event> events = LogParser.parse(FIXTURE);
        assertThat(events).hasSizeGreaterThanOrEqualTo(48);   // 12+35+1+1 = 49
    }
}
