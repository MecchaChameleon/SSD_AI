from __future__ import annotations

import asyncio
import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx


ROOT = Path(__file__).resolve().parent


def find_existing_s3_url() -> str:
    candidates = sorted((ROOT.parent / "simulation/load-results-purchase").glob("*/purchase_load_test.json"), reverse=True)
    for path in candidates:
        data = json.loads(path.read_text(encoding="utf-8"))
        urls = data.get("product_before", {}).get("data", {}).get("imageUrls", [])
        for url in urls:
            if "amazonaws.com" in urlparse(url).netloc:
                return url
    raise SystemExit("No previously observed S3 object URL found; no S3 request sent.")


async def main() -> None:
    url = find_existing_s3_url()
    rows = []
    async with httpx.AsyncClient(timeout=30) as client:
        for test_no in range(1, 4):
            started = time.perf_counter()
            response = await client.get(url)
            rows.append({
                "action": "existing_product_image_get",
                "test_no": test_no,
                "operation": "GET",
                "host": urlparse(url).netloc,
                "status": response.status_code,
                "download_bytes": len(response.content),
                "download_mb": round(len(response.content) / 1024**2, 6),
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                "object_mutated": False,
                "measured_at_utc": datetime.now(timezone.utc).isoformat(),
                "evidence": "A. direct GET of previously observed existing object",
            })
    out = ROOT / "raw/s3_three_call_measurements.csv"
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0]); writer.writeheader(); writer.writerows(rows)
    print(out)


if __name__ == "__main__":
    asyncio.run(main())
