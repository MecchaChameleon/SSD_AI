from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(p * len(ordered)) - 1)]


async def run_stage(client: httpx.AsyncClient, url: str, concurrency: int, requests: int,
                    method: str, headers: dict[str, str]) -> dict:
    semaphore = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    statuses: dict[str, int] = {}
    now = datetime.now(timezone.utc)
    payload = {
        "productId": 9001, "businessType": "RESTAURANT", "category": "SAME_DAY_INVENTORY",
        "originalPrice": 30000, "minimumPrice": 15000, "currentPrice": 26000,
        "totalQty": 20, "remainingQty": 12, "inventoryChange": -2,
        "availableStartAt": (now - timedelta(hours=1)).isoformat(),
        "deadline": (now + timedelta(hours=3)).isoformat(), "currentTime": now.isoformat(),
    }

    async def one() -> None:
        async with semaphore:
            started = time.perf_counter()
            try:
                response = await client.request(method, url, headers=headers, json=payload if method == "POST" else None)
                status = str(response.status_code)
            except httpx.RequestError as exc:
                status = "error:" + type(exc).__name__
            latencies.append((time.perf_counter() - started) * 1000)
            statuses[status] = statuses.get(status, 0) + 1

    started = time.perf_counter()
    await asyncio.gather(*(one() for _ in range(requests)))
    elapsed = time.perf_counter() - started
    successes = sum(count for status, count in statuses.items() if status.startswith("2"))
    return {
        "concurrency": concurrency, "requests": requests, "elapsed_seconds": elapsed,
        "throughput_rps": requests / max(elapsed, 1e-9), "success_rate": successes / requests,
        "latency_avg_ms": statistics.fmean(latencies), "latency_p50_ms": percentile(latencies, .50),
        "latency_p95_ms": percentile(latencies, .95), "latency_p99_ms": percentile(latencies, .99),
        "statuses": statuses,
    }


async def async_main(args: argparse.Namespace) -> Path:
    parsed = urlparse(args.base_url)
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if not local and not args.confirm_external:
        raise SystemExit("External server test requires --confirm-external (verify authorization first).")
    levels = [int(value) for value in args.concurrency.split(",")]
    if min(levels) < 1 or max(levels) > 500:
        raise SystemExit("Concurrency must be between 1 and 500.")
    endpoint = {
        "health": "/health",
        "actuator-health": "/actuator/health",
        "pricing": "/v1/pricing/recommendation",
    }[args.endpoint]
    method = "GET" if args.endpoint in {"health", "actuator-health"} else "POST"
    headers = {"X-AI-Service-Key": args.api_key} if args.api_key else {}
    async with httpx.AsyncClient(timeout=args.timeout) as client:
        rows = [await run_stage(client, args.base_url.rstrip("/") + endpoint, level,
                                args.requests_per_stage, method, headers) for level in levels]
    output = args.output / datetime.now().strftime("%Y%m%dT%H%M%S")
    output.mkdir(parents=True, exist_ok=False)
    report = {"base_url": args.base_url, "endpoint": endpoint, "stages": rows,
              "capacity_rule": "Highest stage with success_rate >= 0.99 and p95 <= target_p95_ms",
              "target_p95_ms": args.target_p95_ms}
    passing = [row for row in rows if row["success_rate"] >= .99 and row["latency_p95_ms"] <= args.target_p95_ms]
    report["estimated_safe_concurrency"] = passing[-1]["concurrency"] if passing else None
    (output / "load_test.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    with (output / "load_test.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = [key for key in rows[0] if key != "statuses"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: row[key] for key in fields} for row in rows)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Bounded staged load test for the deployed AI API")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--endpoint", choices=("health", "actuator-health", "pricing"), default="health")
    parser.add_argument("--concurrency", default="1,5,10,25,50")
    parser.add_argument("--requests-per-stage", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--target-p95-ms", type=float, default=1000)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--confirm-external", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("simulation/load-results"))
    args = parser.parse_args()
    result = asyncio.run(async_main(args))
    print(f"Load test complete: {result}")
    print((result / "load_test.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
