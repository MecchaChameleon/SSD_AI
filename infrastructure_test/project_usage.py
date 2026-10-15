from __future__ import annotations

import csv
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "infrastructure_test"


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def load_successful_measurement() -> dict:
    path = HERE / "raw/measurement.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    required = ["products", "product_detail", "products_map", "buyer_purchases", "seller_products"]
    failed = [name for name in required if data["backend"].get(name, {}).get("successes", 0) == 0]
    if failed:
        raise SystemExit("Fresh JWT required; no successful measurements for: " + ", ".join(failed))
    return data


def gpt_estimate(measurement: dict, output_tokens: int) -> dict:
    source = (ROOT.parent / "SSD_BE/src/main/java/com/jejulocaltime/api/service/BuyerAiRecommendationService.java").read_text(
        encoding="utf-8", errors="replace")
    match = re.search(r'body\.put\("instructions",\s*"""([\s\S]*?)"""\);', source)
    instruction_chars = len(match.group(1)) if match else 0
    candidates_proxy_chars = int(measurement["backend"]["products"]["avg_response_bytes"])
    input_chars = instruction_chars + candidates_proxy_chars + 300
    input_tokens = math.ceil(input_chars / 4)
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "endpoint_or_feature": "/api/buyer/ai-recommendations",
        "triggering_user_action": "구매자 AI 상품 추천 실행",
        "actual_gpt_calls": 0,
        "simulated_gpt_calls_per_action": 1,
        "estimated_input_chars_per_call": input_chars,
        "estimated_input_tokens_per_call": input_tokens,
        "estimated_output_tokens_per_call": output_tokens,
        "cache": "none",
        "max_output_tokens_in_code": "not configured",
        "confidence": "B/C. code path + measured product payload proxy + token approximation",
    }


def main() -> None:
    assumptions = json.loads((HERE / "assumptions.json").read_text(encoding="utf-8"))
    measurement = load_successful_measurement()
    backend = measurement["backend"]
    mix = assumptions["request_mix"]
    general_mix = assumptions["general_read_mix"]
    db_rows = {row["endpoint_name"]: row for row in csv.DictReader(
        (HERE / "raw/db_queries.csv").open(encoding="utf-8-sig"))}
    gpt = gpt_estimate(measurement, assumptions["gpt_output_tokens_per_call"])
    write_csv(HERE / "raw/gpt_simulation.csv", [gpt])

    purchase_request_bytes = len(json.dumps({"productId": measurement["product_id"], "quantity": 1}, separators=(",", ":")).encode())
    purchase_response_bytes = backend["buyer_purchases"]["avg_response_bytes"]
    gpt_request_bytes = gpt["estimated_input_chars_per_call"]
    gpt_response_bytes = gpt["estimated_output_tokens_per_call"] * 4
    rows: list[dict] = []
    infrastructure: list[dict] = []
    for case_model, multiplier, label in [
        ("A_cases_as_annual", 1, "Case 수치를 연간 거래량으로 적용"),
        ("B_cases_as_monthly", 12, "Case 수치를 월 거래량으로 적용 후 연간 환산"),
    ]:
        cumulative_s3_storage = 0
        cumulative_db_growth = 0
        for year in assumptions["years"]:
            annual_cases = year["cases"] * multiplier
            annual_requests = annual_cases / mix["purchase"]
            annual_general = annual_requests * mix["general_read"]
            annual_gpt = annual_requests * mix["gpt_feature"]
            endpoint_counts = {name: annual_general * ratio for name, ratio in general_mix.items()}

            outbound = sum(endpoint_counts[name] * backend[name]["avg_response_bytes"] for name in endpoint_counts)
            outbound += annual_cases * purchase_response_bytes + annual_gpt * gpt_response_bytes
            inbound = annual_cases * purchase_request_bytes + annual_gpt * gpt_request_bytes
            avg_p95 = sum(general_mix[name] * backend[name]["p95_latency_ms"] for name in general_mix)

            selects = inserts = updates = deletes = 0.0
            for name, count in endpoint_counts.items():
                trace = db_rows[name]
                selects += count * int(trace["select"])
                inserts += count * int(trace["insert"])
                updates += count * int(trace["update"])
                deletes += count * int(trace["delete"])
            purchase_trace = db_rows["purchase"]
            selects += annual_cases * int(purchase_trace["select"])
            inserts += annual_cases * int(purchase_trace["insert"])
            updates += annual_cases * int(purchase_trace["update"])
            deletes += annual_cases * int(purchase_trace["delete"])

            s3_gets = sum(endpoint_counts[name] * backend[name].get("avg_s3_urls", 0) for name in endpoint_counts)
            measured_s3_bytes = [r["bytes"] for r in measurement.get("s3_rows", []) if r.get("asset_kind") == "s3" and r.get("status") == 200]
            avg_s3_download = sum(measured_s3_bytes) / len(measured_s3_bytes) if measured_s3_bytes else 0
            s3_download = s3_gets * avg_s3_download
            products_created = year["stores"] * assumptions["products_created_per_store_per_year"]
            s3_puts = products_created * assumptions["images_per_product"]
            s3_upload = s3_puts * assumptions["average_image_upload_bytes"]
            cumulative_s3_storage += s3_upload
            db_growth = annual_cases * assumptions["estimated_purchase_db_growth_bytes"]
            cumulative_db_growth += db_growth

            active_avg_rps = annual_requests / (365 * assumptions["active_hours_per_day"] * 3600)
            busy_rps = active_avg_rps * assumptions["busy_to_active_average_factor"]
            peak_rps = active_avg_rps * assumptions["peak_to_active_average_factor"]
            peak_users = peak_rps * assumptions["average_user_request_interval_seconds"]
            month_divisor = 12
            total_queries = selects + inserts + updates + deletes
            row = {
                "case_model": case_model, "model_description": label, "year": year["year"],
                "stores": year["stores"], "buyers": year["buyers"], "cases_per_year": round(annual_cases),
                "backend_requests_month": round(annual_requests / month_divisor),
                "backend_requests_year": round(annual_requests),
                "backend_inbound_mb_month": round(inbound / month_divisor / 1_000_000, 4),
                "backend_outbound_mb_month": round(outbound / month_divisor / 1_000_000, 4),
                "backend_inbound_mb_year": round(inbound / 1_000_000, 4),
                "backend_outbound_mb_year": round(outbound / 1_000_000, 4),
                "average_rps_24h": round(annual_requests / (365 * 86400), 6),
                "normal_active_rps": round(active_avg_rps, 6), "busy_rps": round(busy_rps, 6),
                "peak_rps": round(peak_rps, 6), "peak_concurrent_users": round(peak_users, 3),
                "representative_read_p95_ms": round(avg_p95, 2),
                "db_select_month": round(selects / month_divisor), "db_insert_month": round(inserts / month_divisor),
                "db_update_month": round(updates / month_divisor), "db_delete_month": round(deletes / month_divisor),
                "db_total_queries_month": round(total_queries / month_divisor),
                "db_total_queries_year": round(total_queries),
                "db_growth_mb_year": round(db_growth / 1_000_000, 4),
                "db_cumulative_growth_mb": round(cumulative_db_growth / 1_000_000, 4),
                "s3_get_month": round(s3_gets / month_divisor), "s3_get_year": round(s3_gets),
                "s3_put_month": round(s3_puts / month_divisor), "s3_put_year": round(s3_puts),
                "s3_download_mb_month": round(s3_download / month_divisor / 1_000_000, 4),
                "s3_download_mb_year": round(s3_download / 1_000_000, 4),
                "s3_upload_mb_year": round(s3_upload / 1_000_000, 4),
                "s3_cumulative_storage_mb": round(cumulative_s3_storage / 1_000_000, 4),
                "gpt_simulated_calls_month": round(annual_gpt / month_divisor, 2),
                "gpt_simulated_calls_year": round(annual_gpt, 2), "gpt_actual_calls": 0,
                "gpt_input_tokens_month": round(annual_gpt * gpt["estimated_input_tokens_per_call"] / month_divisor),
                "gpt_output_tokens_month": round(annual_gpt * gpt["estimated_output_tokens_per_call"] / month_divisor),
                "cpu_peak": "D. unavailable", "memory_peak": "D. unavailable",
                "db_peak_connections": "D. unavailable", "current_db_storage": "D. unavailable",
            }
            rows.append(row)
            infrastructure.append(row)
            write_csv(HERE / f"summary/{case_model}_year{year['year']}_summary.csv", [row])
    write_csv(HERE / "summary/infrastructure_summary.csv", infrastructure)
    (HERE / "summary/projection.json").write_text(json.dumps({
        "generated_at_utc": datetime.now(timezone.utc).isoformat(), "assumptions": assumptions,
        "gpt": gpt, "results": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(HERE / "summary/infrastructure_summary.csv")


if __name__ == "__main__":
    main()
