package com.aurora.bgtest.unit;

import com.aurora.bgtest.config.TestConfig;
import com.aurora.bgtest.workload.OperationType;
import com.aurora.bgtest.workload.WeightedOperationPicker;
import org.junit.jupiter.api.Test;

import java.util.EnumMap;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Verifies {@link WeightedOperationPicker} respects the configured
 * read/insert/update weights, deterministically.
 */
class WeightedOperationPickerTest {

    private static TestConfig.Workload weights(int r, int i, int u) {
        return new TestConfig.Workload(1, 100, r, i, u, false, 0);
    }

    @Test
    void boundaryRollsMapToExpectedTypes() {
        // weights 9:2:1 -> total 12
        WeightedOperationPicker p = new WeightedOperationPicker(weights(9, 2, 1));
        // [0..8] -> READ, [9..10] -> INSERT, [11] -> UPDATE
        for (int r = 0; r <= 8; r++) {
            assertThat(p.pick(r)).as("roll=" + r).isEqualTo(OperationType.READ);
        }
        for (int r = 9; r <= 10; r++) {
            assertThat(p.pick(r)).as("roll=" + r).isEqualTo(OperationType.INSERT);
        }
        assertThat(p.pick(11)).isEqualTo(OperationType.UPDATE);
    }

    @Test
    void allReadWeightYieldsOnlyReads() {
        WeightedOperationPicker p = new WeightedOperationPicker(weights(5, 0, 0));
        for (int r = 0; r < 5; r++) {
            assertThat(p.pick(r)).isEqualTo(OperationType.READ);
        }
    }

    @Test
    void rollOutOfRangeRejected() {
        WeightedOperationPicker p = new WeightedOperationPicker(weights(1, 1, 1));
        assertThatThrownBy(() -> p.pick(-1)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> p.pick(3)).isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void distributionOverManyRollsRoughlyMatchesWeights() {
        WeightedOperationPicker p = new WeightedOperationPicker(weights(9, 2, 1));
        int n = 100_000;
        Map<OperationType, Integer> counts = new EnumMap<>(OperationType.class);
        for (OperationType t : OperationType.values()) counts.put(t, 0);
        for (int i = 0; i < n; i++) {
            counts.merge(p.pick(), 1, Integer::sum);
        }
        // Expected proportions: 9/12=0.75, 2/12≈0.167, 1/12≈0.083
        // Allow ±2% tolerance which is comfortably outside RNG noise at n=100k.
        assertThat(counts.get(OperationType.READ) / (double) n).isBetween(0.73, 0.77);
        assertThat(counts.get(OperationType.INSERT) / (double) n).isBetween(0.15, 0.19);
        assertThat(counts.get(OperationType.UPDATE) / (double) n).isBetween(0.07, 0.10);
    }

    @Test
    void totalWeightExposed() {
        assertThat(new WeightedOperationPicker(weights(9, 2, 1)).total()).isEqualTo(12);
        assertThat(new WeightedOperationPicker(weights(1, 1, 1)).total()).isEqualTo(3);
    }
}
