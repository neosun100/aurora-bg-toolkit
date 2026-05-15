# Root Cause Analysis — Why customer-baseline hangs 30+ seconds

## Symptom

Aurora MySQL Blue/Green switchover is supposed to take 2–5 seconds at the
application layer. The customer (HSK) saw spikes up to **57 seconds** — the
application would stop accepting writes for nearly a minute during what was
supposed to be a quick maintenance window.

The variance was extreme: same configuration, same workload, same cluster —
some rounds recovered in 4 seconds, others took 35 or 56 seconds with no
obvious pattern.

## The customer's configuration

```yaml
# Reproduced verbatim in configs/customer-baseline.yaml
jdbc:
  wrapperPlugins:
    - initialConnection
    - auroraConnectionTracker
    - failover2
    - efm2
    - bg
  bgHighMs: 50
  connectTimeout: null      # <-- ABSENT
  socketTimeout: null       # <-- ABSENT
  failureDetectionTime: null
  failureDetectionInterval: null
hikari:
  maximumPoolSize: 10
  minimumIdle: 5
  initializationFailTimeout: 1
```

Three things stand out, two of them important.

## What actually happens during a Blue/Green switchover

```
T = 0     Operator triggers switchover-blue-green-deployment
T + 0..10 PREPARATION phase (RDS internal: replication catch-up, snapshot)
T + 10..13  IN_PROGRESS phase begins
            └── bg plugin observes the state change
T + 13    bg plugin calls SuspendConnectRouting()
            └── all NEW connection attempts park in this routine
            └── all EXISTING connections are invalidated (marked broken)
            └── application starts seeing the first SQLException

            ──── DOWNTIME WINDOW BEGINS ────

T + 17    bg plugin releases SuspendConnectRouting (~4 s wait, hardcoded)
            └── HikariPool's connection adder thread tries to open new conns
                ├── if DNS already resolves to the Green IP → success → recovery
                └── if DNS still resolves to the old Blue IP → tries to TCP-connect
                    to a host that no longer accepts connections
```

Up to T+17 there is no surprising behaviour. The 4-second `SuspendConnectRouting`
window is documented and the customer expected ~5 seconds of downtime.

The surprise is what happens after T+17 if DNS hasn't propagated yet.

## The fork in the road at T + 17

When HikariPool tries to open a new connection to the (now stale) Blue IP:

### Path A — connectTimeout is set
```
TCP SYN sent ──→ no SYN-ACK comes back (host unreachable)
TCP SYN sent again
...
After connectTimeout milliseconds, JDBC throws ConnectException
HikariPool's adder catches, retries via wrapper → DNS now correct → success
```

If `connectTimeout = 1000 ms`, the retry happens 1 second after the failed
attempt. By that time DNS has propagated, the second attempt connects to the
Green IP, recovery completes, total downtime ≈ 5–7 seconds.

### Path B — customer-baseline (no connectTimeout)
```
TCP SYN sent ──→ no SYN-ACK
TCP layer: Linux kernel default tcp_syn_retries = 6
SYN sent at: t, t+1s, t+3s, t+7s, t+15s, t+31s
Kernel finally surrenders at ~63 s (or earlier if connectTimeout < 30 s)

But Hikari has connection-timeout: 5000 ms — that's a HikariPool-level wait
for an available connection from the pool, not a TCP-level connect timeout.
So while the kernel is happily retrying SYN, the pool waits 5 s, then throws
SQLTransientConnectionException to the workload.

The workload retries every 100 ms; every retry hits an empty pool;
every attempted pool refill hangs in the kernel SYN retry loop.

After ~30 s the kernel gives up, the adder thread re-queries DNS, gets the
Green IP, and finally connects.
```

That's the 30–35 second case (test-03 in the customer log).

But the customer's data showed cases up to **57 seconds**. That's the second
problem.

## Compounding factor — `auroraConnectionTracker` × wrapper 4.0.0

The `auroraConnectionTracker` plugin invalidates and **evicts** connections one
by one when it detects topology change. Under wrapper 4.0.0, its eviction
loop is sequential: evict conn 1, then refill (which may hang), then evict 2,
refill (may hang), and so on.

If all 5–10 connections in the pool need to be replaced and each refill hits
a 30-second TCP hang, the cumulative downtime balloons to 50+ seconds.

## The fix

```yaml
# v4-current.yaml — what we recommended to the customer
jdbc:
  wrapperPlugins:
    - failover2          # keep
    - efm2               # keep
    - bg                 # keep
    # initialConnection REMOVED (extra round-trip for role validation, not needed)
    # auroraConnectionTracker REMOVED (compounds the hang under wrapper 4.0.0)
  connectTimeout: 1000   # 1 s TCP guard — the critical fix
  socketTimeout: 3000
  failureDetectionTime: 6000
  failureDetectionInterval: 1000
  failureDetectionCount: 3
hikari:
  maximumPoolSize: 10
  minimumIdle: 10        # warm pool — refill is faster after suspend release
  initializationFailTimeout: -1
workload:
  retry:
    enabled: true        # one quick retry catches any straggler
    delayMs: 50
```

Result: downtime collapsed from **4–57 s** to **2.7–7.6 s**, and the spread
became much tighter (stdev dropped from >10 s to ~1.5 s).

## Why the hardcoded `SuspendConnectRouting = 4 s` window in the bg plugin?

This is the bg plugin's intentional grace period: after detecting that
switchover has started, it gives RDS 4 seconds to finish the actual swap
before it re-allows new connections. Shorter would mean clients connect to
a half-switched cluster and get errors. The 4 seconds is a floor for
Blue/Green downtime that no client-side optimisation can remove.

The reachable downtime floor is therefore approximately 4 s + DNS
propagation + new connection setup ≈ 4–5 s. v4 hits this floor most of
the time. The remaining variance up to ~7 s is the DNS propagation tail.

## Lessons applicable to other customers

1. **Always set `connectTimeout`** at the JDBC URL level. Anywhere from 1–5
   seconds. The cost of a false positive (one slow connection getting
   abandoned) is far less than the cost of a 30-second TCP hang.
2. **Avoid `auroraConnectionTracker`** unless you have a specific need. Its
   eviction loop interacts badly with wrapper 4.0.0 and makes things worse,
   not better.
3. **Warm the pool**: `minimumIdle = maximumPoolSize`. A cold pool means slow
   refill after suspend release.
4. **Application-level retry on first failure**: a 50ms retry catches most
   transient errors in the milliseconds-wide window between bg-plugin
   suspend release and the new connection becoming available.
5. **Use `failureDetectionTime` explicitly**: the wrapper default of 30
   seconds is much too long for a healthy production workload.
