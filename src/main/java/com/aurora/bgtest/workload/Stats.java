package com.aurora.bgtest.workload;

import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Per-iteration counters for read/insert/update operations.
 *
 * <p>All counters are split into "ok" and "fail" so that the per-second reporter
 * can compute success rate. Consecutive-failure counters help detect downtime
 * windows (a streak of failures bounded by the first WRITE_RECOVERED).
 */
public final class Stats {

    private final AtomicInteger writeOk = new AtomicInteger();
    private final AtomicInteger writeFail = new AtomicInteger();
    private final AtomicInteger readOk = new AtomicInteger();
    private final AtomicInteger readFail = new AtomicInteger();
    private final AtomicInteger consecutiveWriteFails = new AtomicInteger();
    private final AtomicInteger consecutiveReadFails = new AtomicInteger();
    private final AtomicLong writeSeq = new AtomicLong();
    private final AtomicLong readSeq = new AtomicLong();
    private final AtomicLong lastWrittenId = new AtomicLong();

    public void recordWriteOk() {
        writeOk.incrementAndGet();
        consecutiveWriteFails.set(0);
    }

    public int recordWriteFail() {
        writeFail.incrementAndGet();
        return consecutiveWriteFails.incrementAndGet();
    }

    public void recordReadOk() {
        readOk.incrementAndGet();
        consecutiveReadFails.set(0);
    }

    public int recordReadFail() {
        readFail.incrementAndGet();
        return consecutiveReadFails.incrementAndGet();
    }

    public long nextWriteSeq() { return writeSeq.incrementAndGet(); }
    public long nextReadSeq()  { return readSeq.incrementAndGet(); }
    public long lastWrittenId() { return lastWrittenId.get(); }
    public void setLastWrittenId(long id) { lastWrittenId.set(id); }

    public int consecutiveWriteFails() { return consecutiveWriteFails.get(); }
    public int consecutiveReadFails()  { return consecutiveReadFails.get(); }

    /** Snapshot the per-second window counters and reset them. */
    public Snapshot drainPerSecond() {
        return new Snapshot(
                writeOk.getAndSet(0),
                writeFail.getAndSet(0),
                readOk.getAndSet(0),
                readFail.getAndSet(0));
    }

    public record Snapshot(int writeOk, int writeFail, int readOk, int readFail) {
        public boolean hasFailures() { return writeFail > 0 || readFail > 0; }
    }
}
