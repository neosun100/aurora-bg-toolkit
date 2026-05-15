package com.aurora.bgtest.unit;

import com.aurora.bgtest.analysis.LogParser;
import com.aurora.bgtest.analysis.LogParser.DowntimeWindow;
import com.aurora.bgtest.analysis.LogParser.Event;
import com.aurora.bgtest.analysis.LogParser.Kind;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Verifies {@link LogParser} parses both the new SLF4J format and the legacy
 * V4 println format, and computes downtime windows correctly.
 */
class LogParserTest {

    @Test
    void parseNewSlf4jStyleLine() {
        String line = "2026-05-14 13:37:30.123 WARN  [bg-workload] " +
                "MixedWorkload - WRITE_FAIL seq=42 elapsed_ms=100 consecutive=1 err=SQLException: foo";
        Event ev = LogParser.parseLine(line);
        assertThat(ev).isNotNull();
        assertThat(ev.kind()).isEqualTo(Kind.WRITE_FAIL);
        assertThat(ev.consecutive()).isEqualTo(1);
        assertThat(ev.timestamp())
                .isEqualTo(LocalDateTime.of(2026, 5, 14, 13, 37, 30, 123_000_000));
    }

    @Test
    void parseLegacyV4PrintlnStyle() {
        String line = "[2026-05-14 13:41:03.456] WRITE_FAIL seq=99 elapsed_ms=30000 consecutive=5 err=ConnectionException";
        Event ev = LogParser.parseLine(line);
        assertThat(ev).isNotNull();
        assertThat(ev.kind()).isEqualTo(Kind.WRITE_FAIL);
        assertThat(ev.consecutive()).isEqualTo(5);
        assertThat(ev.timestamp().getYear()).isEqualTo(2026);
    }

    @Test
    void parseRecoveredLine() {
        String line = "[2026-05-14 13:37:34.000] WRITE_RECOVERED after 4 consecutive failures";
        Event ev = LogParser.parseLine(line);
        assertThat(ev).isNotNull();
        assertThat(ev.kind()).isEqualTo(Kind.WRITE_RECOVERED);
        assertThat(ev.consecutive()).isEqualTo(4);
    }

    @Test
    void unrelatedLineReturnsNull() {
        assertThat(LogParser.parseLine("STATS write_ok=10 read_ok=30")).isNull();
        assertThat(LogParser.parseLine("totally unrelated")).isNull();
    }

    @Test
    void singleWindowFromFailToRecovered() {
        // A 4-second downtime: fail at 13:37:30, recover at 13:37:34
        List<Event> events = List.of(
                ev("2026-05-14 13:37:30.000", Kind.WRITE_FAIL, 1),
                ev("2026-05-14 13:37:31.000", Kind.WRITE_FAIL, 2),
                ev("2026-05-14 13:37:33.500", Kind.WRITE_FAIL, 3),
                ev("2026-05-14 13:37:34.000", Kind.WRITE_RECOVERED, 3));
        List<DowntimeWindow> windows = LogParser.computeWindows(events);
        assertThat(windows).hasSize(1);
        DowntimeWindow w = windows.get(0);
        assertThat(w.kind()).isEqualTo(Kind.WRITE_FAIL);
        assertThat(w.durationMs()).isEqualTo(4000);
    }

    @Test
    void multipleIndependentWindows() {
        // Two separate downtime windows in the same log
        List<Event> events = List.of(
                ev("2026-05-14 13:37:30.000", Kind.WRITE_FAIL, 1),
                ev("2026-05-14 13:37:34.000", Kind.WRITE_RECOVERED, 1),
                ev("2026-05-14 13:42:00.000", Kind.WRITE_FAIL, 1),
                ev("2026-05-14 13:42:10.500", Kind.WRITE_RECOVERED, 1));
        List<DowntimeWindow> windows = LogParser.computeWindows(events);
        assertThat(windows).hasSize(2);
        assertThat(windows.get(0).durationMs()).isEqualTo(4000);
        assertThat(windows.get(1).durationMs()).isEqualTo(10500);
    }

    @Test
    void readAndWriteWindowsTrackedSeparately() {
        List<Event> events = List.of(
                ev("2026-05-14 10:00:00.000", Kind.READ_FAIL, 1),
                ev("2026-05-14 10:00:01.000", Kind.WRITE_FAIL, 1),
                ev("2026-05-14 10:00:02.000", Kind.READ_RECOVERED, 1),
                ev("2026-05-14 10:00:03.500", Kind.WRITE_RECOVERED, 1));
        List<DowntimeWindow> windows = LogParser.computeWindows(events);
        assertThat(windows).hasSize(2);
        DowntimeWindow read = windows.stream().filter(w -> w.kind() == Kind.READ_FAIL).findFirst().orElseThrow();
        DowntimeWindow write = windows.stream().filter(w -> w.kind() == Kind.WRITE_FAIL).findFirst().orElseThrow();
        assertThat(read.durationMs()).isEqualTo(2000);
        assertThat(write.durationMs()).isEqualTo(2500);
    }

    @Test
    void unrecoveredTrailingStreakBoundedByLastFail() {
        // 35-second TCP hang at the end of log, no recovery message
        List<Event> events = List.of(
                ev("2026-05-14 13:41:03.000", Kind.WRITE_FAIL, 1),
                ev("2026-05-14 13:41:08.000", Kind.WRITE_FAIL, 2),
                ev("2026-05-14 13:41:38.000", Kind.WRITE_FAIL, 3));
        List<DowntimeWindow> windows = LogParser.computeWindows(events);
        assertThat(windows).hasSize(1);
        assertThat(windows.get(0).durationMs()).isEqualTo(35_000);
    }

    @Test
    void parseEntireFileFromTempDir() throws IOException {
        Path tmp = Files.createTempFile("logparser-test-", ".log");
        try {
            Files.writeString(tmp, """
                    2026-05-14 13:37:00.000 INFO  STATS write_ok=10 read_ok=30
                    2026-05-14 13:37:30.000 WARN  WRITE_FAIL seq=1 consecutive=1 err=foo
                    2026-05-14 13:37:31.000 WARN  WRITE_FAIL seq=2 consecutive=2 err=foo
                    2026-05-14 13:37:34.000 INFO  WRITE_RECOVERED after 2 consecutive failures
                    2026-05-14 13:37:35.000 INFO  STATS write_ok=10 read_ok=30
                    """);
            List<DowntimeWindow> windows = LogParser.windowsFrom(tmp);
            assertThat(windows).hasSize(1);
            assertThat(windows.get(0).durationMs()).isEqualTo(4000);
        } finally {
            Files.deleteIfExists(tmp);
        }
    }

    private static Event ev(String ts, Kind kind, int consecutive) {
        return new Event(
                LocalDateTime.parse(ts, java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss.SSS")),
                kind, consecutive);
    }
}
