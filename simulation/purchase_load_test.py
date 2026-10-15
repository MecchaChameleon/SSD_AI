from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx


def load_env(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"Missing load-test environment file: {path}")
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[min(len(ordered) - 1, math.ceil(p * len(ordered)) - 1)]


async def request_json(client: httpx.AsyncClient, method: str, url: str,
                       token: str, json_body: dict | None = None) -> httpx.Response:
    return await client.request(
        method, url, headers={"Authorization": f"Bearer {token}"}, json=json_body
    )


async def run_stage(client: httpx.AsyncClient, base_url: str, buyer_token: str,
                    product_id: int, concurrency: int, requests: int) -> dict:
    semaphore = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    statuses: dict[str, int] = {}
    errors: dict[str, int] = {}

    async def one() -> None:
        async with semaphore:
            started = time.perf_counter()
            status = "error:unknown"
            try:
                response = await request_json(
                    client, "POST", f"{base_url}/api/buyer/purchases", buyer_token,
                    {"productId": product_id, "quantity": 1},
                )
                status = str(response.status_code)
                if response.status_code >= 400:
                    try:
                        body = response.json()
                        message = str(body.get("message") or body.get("error") or "HTTP error")
                    except (ValueError, AttributeError):
                        message = response.text[:160] or "HTTP error"
                    errors[message] = errors.get(message, 0) + 1
            except httpx.RequestError as exc:
                status = "error:" + type(exc).__name__
                errors[str(exc)[:160]] = errors.get(str(exc)[:160], 0) + 1
            latencies.append((time.perf_counter() - started) * 1000)
            statuses[status] = statuses.get(status, 0) + 1

    started = time.perf_counter()
    await asyncio.gather(*(one() for _ in range(requests)))
    elapsed = time.perf_counter() - started
    successes = sum(count for status, count in statuses.items() if status.startswith("2"))
    return {
        "concurrency": concurrency,
        "requests": requests,
        "elapsed_seconds": round(elapsed, 4),
        "throughput_rps": round(requests / max(elapsed, 1e-9), 2),
        "success_rate": round(successes / requests, 4),
        "latency_avg_ms": round(statistics.fmean(latencies), 2),
        "latency_p50_ms": round(percentile(latencies, .50), 2),
        "latency_p95_ms": round(percentile(latencies, .95), 2),
        "latency_p99_ms": round(percentile(latencies, .99), 2),
        "statuses": statuses,
        "errors": errors,
    }


async def async_main(args: argparse.Namespace) -> Path:
    load_env(args.env_file)
    base_url = os.environ.get("LOAD_TEST_BASE_URL", "").rstrip("/")
    buyer_token = os.environ.get("LOAD_TEST_BUYER_TOKEN", "")
    seller_token = os.environ.get("LOAD_TEST_SELLER_TOKEN", "")
    product_id_raw = os.environ.get("LOAD_TEST_PRODUCT_ID", "")
    if not all((base_url, buyer_token, seller_token, product_id_raw)):
        raise SystemExit("LOAD_TEST_BASE_URL, both tokens, and LOAD_TEST_PRODUCT_ID are required")
    parsed = urlparse(base_url)
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"} and not args.confirm_external:
        raise SystemExit("External purchase test requires --confirm-external")
    product_id = int(product_id_raw)
    levels = [int(value) for value in args.concurrency.split(",")]
    if min(levels) < 1 or max(levels) > 200:
        raise SystemExit("Concurrency must be between 1 and 200")

    async with httpx.AsyncClient(timeout=args.timeout) as client:
        health_started = time.perf_counter()
        health = await client.get(f"{base_url}/actuator/health")
        health_ms = (time.perf_counter() - health_started) * 1000
        health.raise_for_status()
        product = await request_json(client, "GET", f"{base_url}/api/seller/products/{product_id}", seller_token)
        product.raise_for_status()
        before = product.json()
        stages: list[dict] = []
        for level in levels:
            row = await run_stage(client, base_url, buyer_token, product_id, level, args.requests_per_stage)
            stages.append(row)
            print(json.dumps(row, ensure_ascii=False))
            if row["success_rate"] < args.stop_success_rate or row["latency_p95_ms"] > args.stop_p95_ms:
                break
            await asyncio.sleep(args.cooldown_seconds)
        after_response = await request_json(client, "GET", f"{base_url}/api/seller/products/{product_id}", seller_token)
        after = after_response.json() if after_response.is_success else {"status": after_response.status_code}

    output = args.output / datetime.now().strftime("%Y%m%dT%H%M%S")
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "endpoint": "/api/buyer/purchases",
        "product_id": product_id,
        "requests_per_stage": args.requests_per_stage,
        "health_latency_ms": round(health_ms, 2),
        "stop_rules": {"minimum_success_rate": args.stop_success_rate, "maximum_p95_ms": args.stop_p95_ms},
        "product_before": before,
        "product_after": after,
        "stages": stages,
    }
    (output / "purchase_load_test.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output / "purchase_load_test.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        fields = [key for key in stages[0] if key not in {"statuses", "errors"}]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: row[key] for key in fields} for row in stages)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Bounded staged load test that creates real purchases")
    parser.add_argument("--env-file", type=Path, default=Path("simulation/.env.loadtest"))
    parser.add_argument("--concurrency", default="1,3,5,10,20,40,80")
    parser.add_argument("--requests-per-stage", type=int, default=50)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--stop-success-rate", type=float, default=.99)
    parser.add_argument("--stop-p95-ms", type=float, default=3000)
    parser.add_argument("--cooldown-seconds", type=float, default=2)
    parser.add_argument("--confirm-external", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("simulation/load-results-purchase"))
    args = parser.parse_args()
    result = asyncio.run(async_main(args))
    print(f"Purchase load test complete: {result}")


if __name__ == "__main__":
    main()
