from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

from simulation.purchase_load_test import load_env


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(p * len(ordered)) - 1)]


def summarize(records: list[dict], elapsed: float) -> dict:
    latencies = [row["latency_ms"] for row in records]
    successes = sum(row["success"] for row in records)
    statuses: dict[str, int] = {}
    endpoints: dict[str, dict] = {}
    for row in records:
        statuses[row["status"]] = statuses.get(row["status"], 0) + 1
        bucket = endpoints.setdefault(row["name"], {"requests": 0, "successes": 0, "latencies": []})
        bucket["requests"] += 1
        bucket["successes"] += row["success"]
        bucket["latencies"].append(row["latency_ms"])
    endpoint_summary = {}
    for name, bucket in endpoints.items():
        endpoint_summary[name] = {
            "requests": bucket["requests"],
            "success_rate": round(bucket["successes"] / bucket["requests"], 4),
            "latency_avg_ms": round(statistics.fmean(bucket["latencies"]), 2),
            "latency_p95_ms": round(percentile(bucket["latencies"], .95), 2),
        }
    return {
        "requests": len(records),
        "elapsed_seconds": round(elapsed, 3),
        "throughput_rps": round(len(records) / max(elapsed, 1e-9), 2),
        "success_rate": round(successes / max(len(records), 1), 4),
        "latency_avg_ms": round(statistics.fmean(latencies), 2) if latencies else 0,
        "latency_p50_ms": round(percentile(latencies, .50), 2),
        "latency_p95_ms": round(percentile(latencies, .95), 2),
        "latency_p99_ms": round(percentile(latencies, .99), 2),
        "statuses": statuses,
        "endpoints": endpoint_summary,
    }


async def run_stage(client: httpx.AsyncClient, base_url: str, buyer_token: str,
                    seller_token: str, product_id: int, users: int, duration: float,
                    purchase_ratio: float, think_min: float, think_max: float,
                    seed: int) -> dict:
    deadline = time.monotonic() + duration
    records: list[dict] = []
    buyer_headers = {"Authorization": f"Bearer {buyer_token}"}
    seller_headers = {"Authorization": f"Bearer {seller_token}"}

    async def virtual_user(user_number: int) -> None:
        rng = random.Random(seed + user_number)
        while time.monotonic() < deadline:
            draw = rng.random()
            if purchase_ratio and draw < purchase_ratio:
                name, method, path, headers, body = "purchase", "POST", "/api/buyer/purchases", buyer_headers, {"productId": product_id, "quantity": 1}
            elif draw < purchase_ratio + .40:
                name, method, path, headers, body = "products", "GET", "/api/products?page=0&size=20", buyer_headers, None
            elif draw < purchase_ratio + .65:
                name, method, path, headers, body = "product_detail", "GET", f"/api/products/{product_id}", buyer_headers, None
            elif draw < purchase_ratio + .80:
                name, method, path, headers, body = "products_map", "GET", "/api/products/map", buyer_headers, None
            elif draw < purchase_ratio + .90:
                name, method, path, headers, body = "buyer_purchases", "GET", "/api/buyer/purchases?page=0&size=10", buyer_headers, None
            else:
                name, method, path, headers, body = "seller_products", "GET", "/api/seller/products?page=0", seller_headers, None
            started = time.perf_counter()
            try:
                response = await client.request(method, base_url + path, headers=headers, json=body)
                status = str(response.status_code)
                success = int(200 <= response.status_code < 300)
            except httpx.RequestError as exc:
                status = "error:" + type(exc).__name__
                success = 0
            records.append({
                "name": name, "status": status, "success": success,
                "latency_ms": (time.perf_counter() - started) * 1000,
            })
            await asyncio.sleep(rng.uniform(think_min, think_max))

    started = time.perf_counter()
    await asyncio.gather(*(virtual_user(index) for index in range(users)))
    elapsed = time.perf_counter() - started
    return {"concurrent_users": users, **summarize(records, elapsed)}


async def async_main(args: argparse.Namespace) -> Path:
    load_env(args.env_file)
    base_url = os.environ.get("LOAD_TEST_BASE_URL", "").rstrip("/")
    buyer_token = os.environ.get("LOAD_TEST_BUYER_TOKEN", "")
    seller_token = os.environ.get("LOAD_TEST_SELLER_TOKEN", "")
    product_id = int(os.environ.get("LOAD_TEST_PRODUCT_ID", "0"))
    if not all((base_url, buyer_token, seller_token, product_id)):
        raise SystemExit("Load-test environment values are incomplete")
    if urlparse(base_url).hostname not in {"localhost", "127.0.0.1", "::1"} and not args.confirm_external:
        raise SystemExit("External test requires --confirm-external")
    levels = [int(value) for value in args.users.split(",")]
    if min(levels) < 1 or max(levels) > 300:
        raise SystemExit("Concurrent users must be between 1 and 300")

    async with httpx.AsyncClient(timeout=args.timeout, limits=httpx.Limits(max_connections=max(levels) + 20)) as client:
        health = await client.get(base_url + "/actuator/health")
        health.raise_for_status()
        stages = []
        for users in levels:
            row = await run_stage(client, base_url, buyer_token, seller_token, product_id, users,
                                  args.duration, args.purchase_ratio, args.think_min, args.think_max,
                                  args.seed + users * 1000)
            stages.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            if row["success_rate"] < args.stop_success_rate or row["latency_p95_ms"] > args.stop_p95_ms:
                break
            await asyncio.sleep(args.cooldown)
        recovery_started = time.perf_counter()
        recovery = await client.get(base_url + "/actuator/health")
        recovery_ms = (time.perf_counter() - recovery_started) * 1000

    output = args.output / datetime.now().strftime("%Y%m%dT%H%M%S")
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "scenario": "mixed" if args.purchase_ratio else "read_only",
        "purchase_ratio": args.purchase_ratio,
        "duration_seconds_per_stage": args.duration,
        "think_time_seconds": [args.think_min, args.think_max],
        "stop_rules": {"minimum_success_rate": args.stop_success_rate, "maximum_p95_ms": args.stop_p95_ms},
        "recovery_health_status": recovery.status_code,
        "recovery_health_latency_ms": round(recovery_ms, 2),
        "stages": stages,
    }
    (output / "concurrent_users.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Concurrent user test complete: {output}", flush=True)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Sustained concurrent virtual-user load test")
    parser.add_argument("--env-file", type=Path, default=Path("simulation/.env.loadtest"))
    parser.add_argument("--users", default="1,5,10,20,30,50,75,100")
    parser.add_argument("--duration", type=float, default=20)
    parser.add_argument("--purchase-ratio", type=float, default=0.0)
    parser.add_argument("--think-min", type=float, default=.5)
    parser.add_argument("--think-max", type=float, default=1.5)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--stop-success-rate", type=float, default=.99)
    parser.add_argument("--stop-p95-ms", type=float, default=3000)
    parser.add_argument("--cooldown", type=float, default=3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--confirm-external", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("simulation/load-results-users"))
    args = parser.parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
