"""Local HTTP benchmark; run from the project root, never against application data."""

import argparse
import hashlib
import http.client
import importlib.metadata
import json
import math
import os
import platform
import random
import socket
import statistics
import subprocess
import sys
import threading
import time
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PORT = 8011
DESTINATION = "https://example.com/benchmark"


def configure() -> None:
    os.environ.update(
        ENV_FILE=os.devnull,
        DATABASE_URL=(
            "postgresql+psycopg://benchmark:benchmark@127.0.0.1:5434/"
            "url_shortener_benchmark?connect_timeout=5"
        ),
        REDIS_URL="redis://127.0.0.1:6381/0",
        JWT_SECRET_KEY="benchmark-only-secret-not-for-production",
        REDIS_CACHE_TTL_SECONDS="3600",
    )


def serve(mode: str) -> None:
    import uvicorn

    import app.main as routes

    if mode == "database_only":
        # Remove Redis entirely from the baseline, not a slow Redis outage fallback.
        routes.get_cached_short_link = lambda short_code: None
        routes.cache_short_link = lambda short_code, short_link, ttl_seconds: None
    uvicorn.run(routes.app, host="127.0.0.1", port=PORT, access_log=False, log_level="warning")


@contextmanager
def server(mode: str) -> Generator[None]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", PORT))
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--serve", mode],
        cwd=ROOT,
    )
    try:
        deadline = time.monotonic() + 30
        while True:
            if process.poll() is not None:
                raise RuntimeError("Benchmark API exited during startup")
            connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=1)
            try:
                connection.request("GET", "/api/v1/health")
                response = connection.getresponse()
                if response.status == 200 and json.loads(response.read()) == {"status": "ok"}:
                    break
            except (OSError, http.client.HTTPException):
                if time.monotonic() >= deadline:
                    raise TimeoutError("Benchmark API did not start") from None
                time.sleep(0.1)
            finally:
                connection.close()
        yield
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def request_batch(codes: list[str], concurrency: int) -> tuple[list[float], float]:
    barrier = threading.Barrier(concurrency + 1)

    def worker(batch: list[str]) -> list[float]:
        connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
        times = []
        try:
            connection.request("GET", "/api/v1/health")
            response = connection.getresponse()
            response.read()
            if response.status != 200:
                raise RuntimeError("Health check failed")
            barrier.wait(timeout=20)
            for code in batch:
                start = time.perf_counter_ns()
                connection.request("GET", f"/{code}")
                response = connection.getresponse()
                response.read()
                elapsed = (time.perf_counter_ns() - start) / 1_000_000
                if response.status != 307 or response.getheader("Location") != DESTINATION:
                    raise RuntimeError(f"Unexpected redirect: {response.status}")
                times.append(elapsed)
            return times
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(worker, codes[i::concurrency]) for i in range(concurrency)]
        start = time.perf_counter()
        barrier.wait(timeout=20)
        samples = [value for future in futures for value in future.result()]
        duration = time.perf_counter() - start
    return samples, duration


def summary(samples: list[float]) -> dict[str, int | float]:
    ordered = sorted(samples)
    return {
        "requests": len(samples),
        "mean_ms": statistics.mean(samples),
        "p50_ms": statistics.median(samples),
        "p95_ms": ordered[math.ceil(len(samples) * 0.95) - 1],
        "p99_ms": ordered[math.ceil(len(samples) * 0.99) - 1],
    }


def summarize_file(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    expected_runs = len(data["method"]["concurrency"]) * data["method"]["rounds"] * 2
    if len(data["runs"]) != expected_runs:
        raise ValueError("Incomplete benchmark results; do not report these as a full run")
    for concurrency in data["method"]["concurrency"]:
        for mode in ("database_only", "redis_hit"):
            runs = [
                row
                for row in data["runs"]
                if row["concurrency"] == concurrency and row["mode"] == mode
            ]
            samples = [sample for row in runs for sample in row["samples_ms"]]
            seconds = sum(row["duration_seconds"] for row in runs)
            print(
                json.dumps(
                    {
                        "concurrency": concurrency,
                        "mode": mode,
                        **summary(samples),
                        "requests_per_second": len(samples) / seconds,
                    }
                )
            )


def benchmark(args: argparse.Namespace) -> None:
    from alembic.config import Config
    from sqlalchemy import delete, func, insert, select, text

    from alembic import command
    from app.cache import CachedShortLink, cache_short_link, redis_client, short_link_cache_key
    from app.database import engine
    from app.models import ClickEvent, ShortLink, User

    if (engine.url.host, engine.url.port, engine.url.database) != (
        "127.0.0.1",
        5434,
        "url_shortener_benchmark",
    ):
        raise RuntimeError("Refusing to benchmark a non-benchmark database")
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    redis_client.ping()
    run_id = uuid4().hex
    codes = [f"{run_id[:4]}{i:06d}" for i in range(args.links)]
    hot_codes = codes[:100]
    rows = []
    user_id = None
    output = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "environment": {
            "os": platform.platform(),
            "python": platform.python_version(),
            "logical_cpus": os.cpu_count(),
            "processor": platform.processor(),
            "packages": {
                p: importlib.metadata.version(p)
                for p in ("fastapi", "starlette", "sqlalchemy", "psycopg", "redis", "uvicorn")
            },
            "redis": redis_client.info("server")["redis_version"],
            "source_sha256": {
                str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted((ROOT / "app").glob("*.py"))
            },
        },
        "method": {
            "seed_links": args.links,
            "hot_links": len(hot_codes),
            "requests_per_mode_per_round": args.requests,
            "rounds": args.rounds,
            "concurrency": [1, 8],
            "warmup_requests_per_run": 200,
            "cache_ttl_seconds": 3600,
            "uvicorn_workers": 1,
            "transport": "real localhost HTTP/1.1, keep-alive, redirects not followed",
            "load": "closed-loop, no think time; client and server on same Windows host",
            "baseline": (
                "Redis calls bypassed in benchmark process; normal SQL lookup + click commit"
            ),
            "cached": "prewarmed Redis hits + normal PostgreSQL click commit",
            "data_services": "PostgreSQL/Redis in Docker Desktop, published localhost ports",
        },
        "runs": rows,
    }
    try:
        with engine.begin() as connection:
            output["environment"]["postgresql"] = connection.scalar(text("SELECT version()"))
            if connection.scalar(select(func.count()).select_from(ShortLink)) != 0:
                raise RuntimeError("Benchmark database must have no existing links")
            user_id = connection.scalar(
                insert(User)
                .values(
                    email=f"benchmark-{run_id}@example.com", hashed_password="unused-benchmark-user"
                )
                .returning(User.id)
            )
            connection.execute(
                insert(ShortLink),
                [
                    {"owner_id": user_id, "short_code": code, "destination_url": DESTINATION}
                    for code in codes
                ],
            )
        with engine.connect() as connection:
            records = connection.execute(
                select(ShortLink.id, ShortLink.short_code, ShortLink.destination_url).where(
                    ShortLink.owner_id == user_id
                )
            ).all()
        record_by_code = {record.short_code: record for record in records}
        with engine.begin() as connection:
            connection.execute(text("ANALYZE short_links"))
        for concurrency in (1, 8):
            for round_number in range(1, args.rounds + 1):
                modes = ["database_only", "redis_hit"]
                if (round_number + concurrency) % 2:
                    modes.reverse()
                rng = random.Random(20260921 + round_number)
                workload = [rng.choice(hot_codes) for _ in range(args.requests)]
                for mode in modes:
                    # Reset only benchmark click rows between modes; preserve the same link data.
                    with engine.begin() as connection:
                        connection.execute(
                            delete(ClickEvent).where(
                                ClickEvent.short_link_id.in_([record.id for record in records])
                            )
                        )
                    for code in hot_codes:
                        record = record_by_code[code]
                        cache_short_link(
                            code,
                            CachedShortLink(
                                id=record.id,
                                destination_url=record.destination_url,
                                expires_at=None,
                            ),
                            3600,
                        )
                    with server(mode):
                        request_batch(hot_codes * 2, concurrency)
                        before = redis_client.info("stats")
                        samples, duration = request_batch(workload, concurrency)
                        after = redis_client.info("stats")
                    hits = after["keyspace_hits"] - before["keyspace_hits"]
                    misses = after["keyspace_misses"] - before["keyspace_misses"]
                    expected_hits = args.requests if mode == "redis_hit" else 0
                    if hits != expected_hits or misses != 0:
                        raise AssertionError(f"Unexpected cache statistics: {hits=}, {misses=}")
                    with engine.connect() as connection:
                        clicks = connection.scalar(select(func.count()).select_from(ClickEvent))
                    if clicks != args.requests + 200:
                        raise AssertionError(f"Click count mismatch: {clicks}")
                    row = {
                        "mode": mode,
                        "round": round_number,
                        "concurrency": concurrency,
                        **summary(samples),
                        "duration_seconds": duration,
                        "requests_per_second": len(samples) / duration,
                        "redis_hits": hits,
                        "redis_misses": misses,
                        "clicks_including_warmup": clicks,
                        "errors": 0,
                        "samples_ms": samples,
                    }
                    rows.append(row)
                    print(
                        json.dumps({k: v for k, v in row.items() if k != "samples_ms"}), flush=True
                    )
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")
    finally:
        if user_id is not None:
            with engine.begin() as connection:
                ids = select(ShortLink.id).where(ShortLink.owner_id == user_id)
                connection.execute(delete(ClickEvent).where(ClickEvent.short_link_id.in_(ids)))
                connection.execute(delete(ShortLink).where(ShortLink.owner_id == user_id))
                connection.execute(delete(User).where(User.id == user_id))
        redis_client.delete(*(short_link_cache_key(code) for code in hot_codes))
        redis_client.close()
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", choices=["database_only", "redis_hit"])
    parser.add_argument("--summarize", type=Path, help="Summarize saved results without services")
    parser.add_argument("--links", type=int, default=10000)
    parser.add_argument("--requests", type=int, default=2000)
    parser.add_argument("--rounds", type=int, default=4)
    parser.add_argument("--output", type=Path, default=ROOT / "benchmarks/results.json")
    args = parser.parse_args()
    if args.summarize:
        summarize_file(args.summarize)
        return
    if args.links < 100 or args.requests < 100 or args.rounds < 1:
        parser.error("Require at least 100 links, 100 requests, and one round")
    os.chdir(ROOT)
    configure()
    if args.serve:
        serve(args.serve)
    else:
        benchmark(args)


if __name__ == "__main__":
    main()
