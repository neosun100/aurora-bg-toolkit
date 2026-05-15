package com.aurora.bgtest.workload;

/**
 * The three workload operation types that the mixed workload chooses among,
 * weighted by the configuration's {@code workload.weights} block.
 */
public enum OperationType {
    READ,
    INSERT,
    UPDATE
}
