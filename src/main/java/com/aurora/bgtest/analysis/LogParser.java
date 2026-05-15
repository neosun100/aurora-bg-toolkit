package com.aurora.bgtest.analysis;

import java.io.BufferedReader;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Parses test log files and computes downtime windows.
 *
 * <p>The structured log lines we look for are:
 * <ul>
 *   <li>{@code WRITE_FAIL ... consecutive=N ...} — every write failure</li>
 *   <li>{@code READ_FAIL ...} — every read failure</li>
 *   <li>{@code WRITE_RECOVERED after N consecutive failures} — written by V4 main</li>
 *   <li>per-second {@code STATS write_ok=N read_ok=N} lines (used for QPS recovery)</li>
 * </ul>
 *
 * <p>The downtime window is defined as the time between the first
 * {@code *_FAIL} of a streak and the first {@code *_RECOVERED} (or, if
 * recovery wasn't logged, the next {@code *_OK} statistic). This mirrors
 * the ad-hoc grep+awk loop the engagement used to read with by hand.
 */
public final class LogParser {

    private static final DateTimeFormatter[] TIMESTAMP_FORMATS = {
            // SLF4J simple logger default: "yyyy-MM-dd HH:mm:ss.SSS"
            DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss.SSS"),
            // Older V0..V4 plain printlns: "[yyyy-MM-dd HH:mm:ss.SSS]"
            DateTimeFormatter.ofPattern("[yyyy-MM-dd HH:mm:ss.SSS]")
    };

    /**
     * Pattern for the FAIL lines, capturing the timestamp and operation kind.
     * <p>Accepts both the new SLF4J format (no brackets) and the legacy V4
     * println format (square-bracket-wrapped timestamp).
     */
    private static final Pattern FAIL_PATTERN = Pattern.compile(
            "^\\[?(?<ts>\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2}\\.\\d{3})\\]?" +
            ".*?\\b(?<op>WRITE_FAIL|READ_FAIL)\\b" +
            ".*?\\bconsecutive=(?<count>\\d+)");

    private static final Pattern RECOVERED_PATTERN = Pattern.compile(
            "^\\[?(?<ts>\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2}\\.\\d{3})\\]?" +
            ".*?\\b(?<op>WRITE_RECOVERED|READ_RECOVERED)\\b" +
            ".*?after (?<count>\\d+) consecutive failures");

    private LogParser() {}

    /** Parse a single log file into an ordered list of events. */
    public static List<Event> parse(Path logFile) throws java.io.IOException {
        List<Event> events = new ArrayList<>();
        try (BufferedReader r = Files.newBufferedReader(logFile)) {
            String line;
            while ((line = r.readLine()) != null) {
                Event ev = parseLine(line);
                if (ev != null) events.add(ev);
            }
        }
        return events;
    }

    /** Parse a single log line; returns {@code null} if the line is not an event we care about. */
    public static Event parseLine(String line) {
        Matcher m = FAIL_PATTERN.matcher(line);
        if (m.find()) {
            Kind kind = m.group("op").startsWith("WRITE") ? Kind.WRITE_FAIL : Kind.READ_FAIL;
            return new Event(parseTs(m.group("ts")), kind, Integer.parseInt(m.group("count")));
        }
        m = RECOVERED_PATTERN.matcher(line);
        if (m.find()) {
            Kind kind = m.group("op").startsWith("WRITE") ? Kind.WRITE_RECOVERED : Kind.READ_RECOVERED;
            return new Event(parseTs(m.group("ts")), kind, Integer.parseInt(m.group("count")));
        }
        return null;
    }

    private static LocalDateTime parseTs(String s) {
        // Try formats in order; throw if none match.
        for (DateTimeFormatter f : TIMESTAMP_FORMATS) {
            try { return LocalDateTime.parse(s, f); } catch (Exception ignore) { /* next */ }
        }
        throw new IllegalArgumentException("unparseable timestamp: " + s);
    }

    /**
     * From an ordered event list, derive downtime windows: each window starts at
     * the first FAIL of a streak and ends at the next RECOVERED (or the last FAIL
     * if no RECOVERED followed).
     *
     * @return list of windows in chronological order
     */
    public static List<DowntimeWindow> computeWindows(List<Event> events) {
        List<DowntimeWindow> out = new ArrayList<>();
        LocalDateTime writeStart = null;
        LocalDateTime writeLast = null;
        LocalDateTime readStart = null;
        LocalDateTime readLast = null;

        for (Event e : events) {
            switch (e.kind()) {
                case WRITE_FAIL -> {
                    if (writeStart == null) writeStart = e.timestamp();
                    writeLast = e.timestamp();
                }
                case READ_FAIL -> {
                    if (readStart == null) readStart = e.timestamp();
                    readLast = e.timestamp();
                }
                case WRITE_RECOVERED -> {
                    if (writeStart != null) {
                        out.add(new DowntimeWindow(Kind.WRITE_FAIL, writeStart, e.timestamp()));
                        writeStart = null; writeLast = null;
                    }
                }
                case READ_RECOVERED -> {
                    if (readStart != null) {
                        out.add(new DowntimeWindow(Kind.READ_FAIL, readStart, e.timestamp()));
                        readStart = null; readLast = null;
                    }
                }
            }
        }
        // Trailing un-recovered streaks: bound by the last fail seen.
        if (writeStart != null && writeLast != null && !writeStart.equals(writeLast)) {
            out.add(new DowntimeWindow(Kind.WRITE_FAIL, writeStart, writeLast));
        }
        if (readStart != null && readLast != null && !readStart.equals(readLast)) {
            out.add(new DowntimeWindow(Kind.READ_FAIL, readStart, readLast));
        }
        return out;
    }

    /** Convenience: load a file and compute its downtime windows in one call. */
    public static List<DowntimeWindow> windowsFrom(Path logFile) throws java.io.IOException {
        return computeWindows(parse(logFile));
    }

    public enum Kind { WRITE_FAIL, READ_FAIL, WRITE_RECOVERED, READ_RECOVERED }

    public record Event(LocalDateTime timestamp, Kind kind, int consecutive) {}

    /** A bounded period during which writes (or reads) failed continuously. */
    public record DowntimeWindow(Kind kind, LocalDateTime start, LocalDateTime end) {
        public Duration duration() { return Duration.between(start, end); }
        public long durationMs() { return duration().toMillis(); }
    }
}
