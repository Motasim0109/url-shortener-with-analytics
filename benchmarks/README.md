# Redis Redirect Latency Benchmark

Measured September 21, 2026. These are local synthetic HTTP measurements, not production
traffic, maximum server capacity, or a guarantee of the same improvement on another machine.

## Results

The existing functional suite passed: **20 tests in 1.97 seconds**.

The full benchmark sent **32,000 measured redirects**, plus 3,200 excluded warmup redirects.
Each mode/concurrency combination contains 8,000 requests pooled from four rounds.

| Concurrent clients | Lookup mode | Median (ms) | p95 (ms) | p99 (ms) | Throughput (requests/s) |
| --- | --- | ---: | ---: | ---: | ---: |
| 1 | PostgreSQL, Redis bypassed | 3.6901 | 4.6553 | 5.9853 | 261.81 |
| 1 | Warm Redis cache | 3.4593 | 4.6553 | 7.2334 | 275.07 |
| 8 | PostgreSQL, Redis bypassed | 13.4273 | 31.3614 | 45.9682 | 502.84 |
| 8 | Warm Redis cache | 11.5480 | 26.6394 | 42.9092 | 583.10 |

At concurrency 8, observed median latency decreased **14.0%**, p95 decreased **15.1%**,
and throughput increased **16.0%**. Latency reduction is `(baseline - cached) / baseline`.
Throughput increase is `(cached - baseline) / baseline`.

At concurrency 1, median latency decreased **6.3%**, but p95 was unchanged and p99 was worse.

Per-round median results at concurrency 8 demonstrate variability rather than only the best round:

| Round | PostgreSQL median (ms) | Redis median (ms) |
| --- | ---: | ---: |
| 1 | 13.3532 | 10.2702 |
| 2 | 12.5758 | 12.3251 |
| 3 | 13.3811 | 11.6215 |
| 4 | 14.3747 | 11.8068 |

All measured requests returned HTTP 307 with the expected Location. Redis-hit runs recorded
exactly one cache hit per measured request and zero misses. Each run persisted exactly 2,200
clicks (2,000 measured plus 200 warmup). No request failures were observed. A final database
check confirmed that the harness removed its generated users, links, and clicks.


The full experiment comprises 32,000 requests; the
concurrency-8 comparison specifically comprises 16,000 requests, split evenly between the modes.

## Method

- Windows 11, Intel Core i7-13700H (14 cores / 20 logical processors), Python 3.13.2.
- Docker Desktop reported 20 CPUs and 8,129,777,664 bytes of memory available to its engine.
- PostgreSQL 18.6 and Redis 7.4.11 run in separate Docker containers on the same laptop.
- Uvicorn 0.52.4, one worker, Windows host process, access logging disabled.
- FastAPI 0.141.1, Starlette 1.6.0, SQLAlchemy 2.0.52, psycopg 3.3.4, redis-py 6.4.0.
- Real HTTP/1.1 over loopback, persistent connections, no TLS, no redirect following.
- The client and API share the host; database and Redis access crosses Docker's published ports.
- Seeded 10,000 links and one user in a dedicated benchmark database using the application's models.
  Tables were created by Alembic migrations, not `create_all()`.
- Requests use 100 non-expiring hot links chosen with a deterministic random seed. The same
  request sequence is used for both modes in each round.
- The Redis cache is explicitly prewarmed with those 100 links and a 3,600-second TTL. This is
  a 100% cache-hit scenario, not a cold-cache or cache-miss benchmark.
- The database-only mode substitutes no-op cache helpers inside the benchmark server process.
  It retains the application's real SQLAlchemy lookup, response generation, and click commits.
  It does not simulate a Redis outage or include failed connection timeouts in the baseline.
- Both modes receive 200 warmup redirects before each measured batch. PostgreSQL is warmed too.
- Four rounds per concurrency, 2,000 requests per mode per round; mode order alternates to
  reduce order bias. Each mode starts a fresh Uvicorn process.
- Concurrency is 1 or 8 closed-loop clients without think time. The next request starts after
  the previous response completes. This is not a fixed arrival-rate or saturation load test.
- Per-request timing spans request send through response-body read. Worker health checks and
  connection setup happen outside those timings. Throughput uses measured batch wall time,
  including synchronization, rather than being inferred from latency.
- Percentiles are computed from pooled per-request samples, not averages of round percentiles.
  p95/p99 use nearest rank; p50 uses the median. There are no confidence-interval claims.
- Source fingerprints, versions, raw samples, per-round metrics, and validation counts are in
  [results.json](results.json). The preliminary 100-request smoke runs in
  [smoke-results.json](smoke-results.json) are excluded from the reported full-run metrics.

## Reproduce

Use the existing project virtual environment with its dependencies installed. This harness does
not require a new benchmark package and does not change application source files.

Start the isolated benchmark data services, waiting for readiness:

```powershell
docker compose -f benchmarks/compose.yaml -p url-shortener-benchmark up -d --wait
```

Run the HTTP benchmark from the project root. It applies migrations, generates its own data,
starts/stops temporary API processes, records timings, and removes its generated rows and cache keys:

```powershell
python benchmarks/redirect_latency.py --requests 2000 --rounds 4 --output benchmarks/results-new.json
```

The ports must be free: PostgreSQL `5434`, Redis `6381`, and the temporary API `8011`.
The harness fixes the database destination to `url_shortener_benchmark` and ignores `.env`.
Do not repoint these services at an existing database or shared Redis instance. Run only one
benchmark at a time. Do not interact with these benchmark services during the measurement.

Summarize any complete result file without starting services:

```powershell
python benchmarks/redirect_latency.py --summarize benchmarks/results.json
```

Stop only the benchmark services, preserving their schema in the dedicated volume:

```powershell
docker compose -f benchmarks/compose.yaml -p url-shortener-benchmark stop
```

The regular application Compose project and the pytest database are not modified by this harness.
