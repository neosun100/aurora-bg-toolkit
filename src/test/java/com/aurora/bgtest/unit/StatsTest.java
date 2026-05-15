package com.aurora.bgtest.unit;

import com.aurora.bgtest.workload.Stats;
import org.junit.jupiter.api.Test;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Verifies {@link Stats} accounting and concurrent correctness.
 *
 * <p>The core invariants:
 * <ul>
 *   <li>recordWriteOk resets consecutive write fails to 0.</li>
 *   <li>recordWriteFail increments and returns the new consecutive count.</li>
 *   <li>drainPerSecond resets the per-second window atomically.</li>
 *   <li>Counters survive multi-threaded contention.</li>
 * </ul>
 */
class StatsTest {

    @Test
    void writeOkResetsConsecutiveCounter() {
        Stats s = new Stats();
        s.recordWriteFail();
        s.recordWriteFail();
        s.recordWriteFail();
        assertThat(s.consecutiveWriteFails()).isEqualTo(3);
        s.recordWriteOk();
        assertThat(s.consecutiveWriteFails()).isZero();
    }

    @Test
    void readOkResetsConsecutiveCounter() {
        Stats s = new Stats();
        s.recordReadFail();
        s.recordReadFail();
        s.recordReadOk();
        assertThat(s.consecutiveReadFails()).isZero();
    }

    @Test
    void recordWriteFailReturnsNewCount() {
        Stats s = new Stats();
        assertThat(s.recordWriteFail()).isEqualTo(1);
        assertThat(s.recordWriteFail()).isEqualTo(2);
        assertThat(s.recordWriteFail()).isEqualTo(3);
    }

    @Test
    void drainSnapshotResetsCounters() {
        Stats s = new Stats();
        s.recordWriteOk();
        s.recordWriteOk();
        s.recordWriteFail();
        s.recordReadOk();

        Stats.Snapshot first = s.drainPerSecond();
        assertThat(first.writeOk()).isEqualTo(2);
        assertThat(first.writeFail()).isEqualTo(1);
        assertThat(first.readOk()).isEqualTo(1);
        assertThat(first.readFail()).isZero();
        assertThat(first.hasFailures()).isTrue();

        Stats.Snapshot second = s.drainPerSecond();
        assertThat(second.writeOk()).isZero();
        assertThat(second.writeFail()).isZero();
        assertThat(second.readOk()).isZero();
        assertThat(second.readFail()).isZero();
        assertThat(second.hasFailures()).isFalse();
    }

    @Test
    void sequenceCountersStrictlyIncreasing() {
        Stats s = new Stats();
        long a = s.nextWriteSeq();
        long b = s.nextWriteSeq();
        long c = s.nextWriteSeq();
        assertThat(a).isLessThan(b);
        assertThat(b).isLessThan(c);
    }

    @Test
    void lastWrittenIdRoundTrips() {
        Stats s = new Stats();
        assertThat(s.lastWrittenId()).isZero();
        s.setLastWrittenId(123L);
        assertThat(s.lastWrittenId()).isEqualTo(123L);
    }

    @Test
    void concurrentRecordsAccountedExactly() throws InterruptedException {
        int threads = 16;
        int perThread = 1000;
        Stats s = new Stats();
        ExecutorService pool = Executors.newFixedThreadPool(threads);
        CountDownLatch ready = new CountDownLatch(threads);
        CountDownLatch start = new CountDownLatch(1);
        AtomicInteger expectedFails = new AtomicInteger();

        for (int i = 0; i < threads; i++) {
            final int seed = i;
            pool.submit(() -> {
                ready.countDown();
                try { start.await(); } catch (InterruptedException ie) { return; }
                for (int j = 0; j < perThread; j++) {
                    if ((j + seed) % 5 == 0) {
                        s.recordWriteFail();
                        expectedFails.incrementAndGet();
                    } else {
                        s.recordWriteOk();
                    }
                }
            });
        }

        ready.await();
        start.countDown();
        pool.shutdown();
        assertThat(pool.awaitTermination(5, TimeUnit.SECONDS)).isTrue();

        Stats.Snapshot snap = s.drainPerSecond();
        int totalFail = snap.writeFail();
        int totalOk = snap.writeOk();
        assertThat(totalFail).isEqualTo(expectedFails.get());
        assertThat(totalOk + totalFail).isEqualTo(threads * perThread);
    }
}
