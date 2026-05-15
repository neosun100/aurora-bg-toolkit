package com.aurora.bgtest.workload;

import com.aurora.bgtest.config.TestConfig;

import java.util.concurrent.ThreadLocalRandom;

/**
 * Picks an {@link OperationType} based on read/insert/update weights from
 * {@link TestConfig.Workload}.
 *
 * <p>Pure function; trivially unit-testable.
 */
public final class WeightedOperationPicker {

    private final int read;
    private final int readPlusInsert;
    private final int total;

    public WeightedOperationPicker(TestConfig.Workload wl) {
        this.read = wl.readWeight();
        this.readPlusInsert = wl.readWeight() + wl.insertWeight();
        this.total = wl.totalWeight();
    }

    public OperationType pick() {
        return pick(ThreadLocalRandom.current().nextInt(total));
    }

    /** Visible for testing — pick by an explicit roll instead of the thread-local RNG. */
    public OperationType pick(int roll) {
        if (roll < 0 || roll >= total) {
            throw new IllegalArgumentException("roll must be in [0," + total + ")");
        }
        if (roll < read) return OperationType.READ;
        if (roll < readPlusInsert) return OperationType.INSERT;
        return OperationType.UPDATE;
    }

    public int total() { return total; }
}
