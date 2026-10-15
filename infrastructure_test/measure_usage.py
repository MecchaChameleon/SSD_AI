from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import statistics
import time
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx


ROOT = Path(__file__).resolve().parents[1]
BE_ROOT = ROOT.parent / "SSD_BE"


def load_env(path: Path) -> None:
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def pct(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * percentile + .999999)))
    return ordered[index]


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def endpoint_inventory() -> list[dict]:
    rows: list[dict] = []
    for source in sorted((BE_ROOT / "src/main/java/com/jejulocaltime/api/controller").glob("*Controller.java")):
        text = source.read_text(encoding="utf-8", errors="replace")
        class_base_match = re.search(r'@RequestMapping\("([^"]+)"\)[\s\S]{0,300}?class\s+', text)
        class_base = class_base_match.group(1) if class_base_match else ""
        for match in re.finditer(r'@(Get|Post|Put|Patch|Delete)Mapping(?:\("([^"]*)"\)|\([^)]*value\s*=\s*"([^"]*)"[^)]*\)|\s)', text):
            method = match.group(1).upper()
            suffix = match.group(2) or match.group(3) or ""
            path = (class_base.rstrip("/") + "/" + suffix.lstrip("/")).rstrip("/") or "/"
            category = "other"
            for key in ("auth", "products", "buyer", "seller", "notifications", "admin"):
                if f"/{key}" in path:
                    category = key
                    break
            rows.append({
                "controller": source.name,
                "method": method,
                "path": path,
                "category": category,
                "changes_state": method != "GET",
                "source": str(source.relative_to(BE_ROOT)),
                "confidence": "A. actual code inventory",
            })
    return rows


def collect_urls(value: object) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            if key.lower().endswith(("url", "urls")):
                found.extend(collect_urls(nested))
            else:
                found.extend(collect_urls(nested))
    elif isinstance(value, list):
        for nested in value:
            found.extend(collect_urls(nested))
    elif isinstance(value, str) and value.startswith(("http://", "https://")):
        found.append(value)
    return found


async def main_async(args: argparse.Namespace) -> None:
    load_env(args.env_file)
    base = os.environ["LOAD_TEST_BASE_URL"].rstrip("/")
    buyer = os.environ["LOAD_TEST_BUYER_TOKEN"]
    seller = os.environ["LOAD_TEST_SELLER_TOKEN"]
    product_id = int(os.environ["LOAD_TEST_PRODUCT_ID"])
    if urlparse(base).hostname not in {"localhost", "127.0.0.1", "::1"} and not args.confirm_external:
        raise SystemExit("External read-only measurement requires --confirm-external")

    raw_dir = args.output / "raw"
    write_csv(raw_dir / "endpoint_inventory.csv", endpoint_inventory())
    buyer_headers = {"Authorization": f"Bearer {buyer}"}
    seller_headers = {"Authorization": f"Bearer {seller}"}
    today = date.today().isoformat()
    targets = [
        ("products", "GET", "/api/products?page=0&size=20", buyer_headers),
        ("product_detail", "GET", f"/api/products/{product_id}", buyer_headers),
        ("products_map", "GET", "/api/products/map", buyer_headers),
        ("buyer_purchases", "GET", "/api/buyer/purchases?page=0&size=10", buyer_headers),
        ("seller_products", "GET", "/api/seller/products?page=0", seller_headers),
        ("auth_me", "GET", "/api/auth/me", buyer_headers),
        ("seller_dashboard", "GET", f"/api/seller/dashboard?date={today}", seller_headers),
        ("notifications", "GET", "/api/notifications?page=0&size=10", buyer_headers),
        ("notification_settings", "GET", "/api/users/me/notification-settings", buyer_headers),
        ("seller_profile", "GET", "/api/seller/profile", seller_headers),
    ]
    rows: list[dict] = []
    image_urls: set[str] = set()
    async with httpx.AsyncClient(timeout=args.timeout, follow_redirects=False) as client:
        for name, method, path, headers in targets:
            for _ in range(args.repetitions):
                started_at = datetime.now(timezone.utc)
                started = time.perf_counter()
                try:
                    response = await client.request(method, base + path, headers=headers)
                    elapsed = (time.perf_counter() - started) * 1000
                    response_bytes = len(response.content)
                    request_bytes = 0
                    status = response.status_code
                    error = ""
                    asset_url_count = 0
                    s3_url_count = 0
                    if response.is_success and "json" in response.headers.get("content-type", ""):
                        try:
                            response_urls = collect_urls(response.json())
                            image_urls.update(response_urls)
                            asset_url_count = len(response_urls)
                            s3_url_count = sum("amazonaws.com" in (urlparse(url).hostname or "") or
                                               "cloudfront.net" in (urlparse(url).hostname or "")
                                               for url in response_urls)
                        except ValueError:
                            pass
                except httpx.RequestError as exc:
                    elapsed = (time.perf_counter() - started) * 1000
                    response_bytes = 0
                    request_bytes = 0
                    status = 0
                    error = type(exc).__name__
                    asset_url_count = 0
                    s3_url_count = 0
                rows.append({
                    "timestamp_utc": started_at.isoformat(), "endpoint_name": name,
                    "method": method, "path": path, "status": status,
                    "request_body_bytes": request_bytes, "response_body_bytes": response_bytes,
                    "asset_url_count": asset_url_count, "s3_url_count": s3_url_count,
                    "latency_ms": round(elapsed, 3), "error": error,
                })
                await asyncio.sleep(args.pause)

        s3_rows: list[dict] = []
        for url in sorted(image_urls)[: args.max_image_gets]:
            host = urlparse(url).hostname or ""
            kind = "s3" if "amazonaws.com" in host or "cloudfront.net" in host else "non_s3_public_asset"
            started_at = datetime.now(timezone.utc)
            started = time.perf_counter()
            try:
                response = await client.get(url)
                status, size, error = response.status_code, len(response.content), ""
            except httpx.RequestError as exc:
                status, size, error = 0, 0, type(exc).__name__
            s3_rows.append({
                "timestamp_utc": started_at.isoformat(), "operation": "GET", "asset_kind": kind,
                "host": host, "status": status, "bytes": size,
                "latency_ms": round((time.perf_counter() - started) * 1000, 3), "error": error,
            })

    write_csv(raw_dir / "backend_requests.csv", rows)
    summaries: list[dict] = []
    for name in sorted({row["endpoint_name"] for row in rows}):
        selected = [row for row in rows if row["endpoint_name"] == name]
        latencies = [row["latency_ms"] for row in selected]
        statuses = Counter(str(row["status"]) for row in selected)
        elapsed_window = max(0.001, (
            datetime.fromisoformat(selected[-1]["timestamp_utc"]) - datetime.fromisoformat(selected[0]["timestamp_utc"])
        ).total_seconds() + selected[-1]["latency_ms"] / 1000)
        summaries.append({
            "endpoint_name": name, "method": selected[0]["method"], "path": selected[0]["path"],
            "requests": len(selected), "successes": sum(200 <= row["status"] < 300 for row in selected),
            "failures": sum(not (200 <= row["status"] < 300) for row in selected),
            "avg_request_bytes": round(statistics.fmean(row["request_body_bytes"] for row in selected), 2),
            "total_request_bytes": sum(row["request_body_bytes"] for row in selected),
            "avg_response_bytes": round(statistics.fmean(row["response_body_bytes"] for row in selected), 2),
            "total_response_bytes": sum(row["response_body_bytes"] for row in selected),
            "avg_latency_ms": round(statistics.fmean(latencies), 2), "p50_latency_ms": round(pct(latencies, .50), 2),
            "p95_latency_ms": round(pct(latencies, .95), 2), "p99_latency_ms": round(pct(latencies, .99), 2),
            "max_latency_ms": round(max(latencies), 2), "status_distribution": json.dumps(statuses, ensure_ascii=False),
            "avg_asset_urls": round(statistics.fmean(row["asset_url_count"] for row in selected), 3),
            "avg_s3_urls": round(statistics.fmean(row["s3_url_count"] for row in selected), 3),
            "average_rps": round(len(selected) / elapsed_window, 3),
            "confidence": "A. direct read-only HTTP measurement",
        })
    write_csv(raw_dir / "backend_latency.csv", summaries)
    write_csv(raw_dir / "s3_requests.csv", s3_rows or [{
        "timestamp_utc": datetime.now(timezone.utc).isoformat(), "operation": "GET", "asset_kind": "none_observed",
        "host": "", "status": "", "bytes": 0, "latency_ms": 0, "error": "No public asset URL observed",
    }])

    db_rows = [
        {"endpoint_name": "products", "select": 2, "insert": 0, "update": 0, "delete": 0, "transactions": 0},
        {"endpoint_name": "product_detail", "select": 1, "insert": 0, "update": 0, "delete": 0, "transactions": 0},
        {"endpoint_name": "products_map", "select": 1, "insert": 0, "update": 0, "delete": 0, "transactions": 0},
        {"endpoint_name": "buyer_purchases", "select": 2, "insert": 0, "update": 0, "delete": 0, "transactions": 0},
        {"endpoint_name": "seller_products", "select": 2, "insert": 0, "update": 0, "delete": 0, "transactions": 0},
        {"endpoint_name": "purchase", "select": 6, "insert": 2, "update": 1, "delete": 0, "transactions": 1},
    ]
    for row in db_rows:
        row["total_queries"] = row["select"] + row["insert"] + row["update"] + row["delete"]
        row["measurement_method"] = "Static execution-path trace; pg_stat_statements unavailable"
        row["confidence"] = "C. code-path estimate"
    write_csv(raw_dir / "db_queries.csv", db_rows)

    latency_by_name = {row["endpoint_name"]: row for row in summaries}
    measurement = {
        "measured_at_utc": datetime.now(timezone.utc).isoformat(), "base_url": base,
        "repetitions_per_read_endpoint": args.repetitions, "product_id": product_id,
        "backend": latency_by_name,
        "image_urls_observed": len(image_urls),
        "s3_gets_measured": sum(row["asset_kind"] == "s3" for row in s3_rows),
        "s3_rows": s3_rows,
        "unavailable": ["Render CPU average/peak", "Render RAM average/peak", "Render internal network I/O",
                        "PostgreSQL pg_stat_statements", "DB size/table/index size", "active/peak DB connections"],
    }
    (raw_dir / "measurement.json").write_text(json.dumps(measurement, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "backend_requests": len(rows),
                      "observed_asset_urls": len(image_urls), "measured_s3_gets": measurement["s3_gets_measured"]}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=ROOT / "simulation/.env.loadtest")
    parser.add_argument("--output", type=Path, default=ROOT / "infrastructure_test")
    parser.add_argument("--repetitions", type=int, default=20)
    parser.add_argument("--pause", type=float, default=.1)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--max-image-gets", type=int, default=10)
    parser.add_argument("--confirm-external", action="store_true")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
