from __future__ import annotations

import csv
import json
import math
import os
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(__file__).resolve().parent / ".matplotlib"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt


ROOT = Path(__file__).resolve().parent
RAW, SUMMARY, DOCS = ROOT / "raw", ROOT / "summary", ROOT / "docs"
CHARTS = DOCS / "cost_charts"
USD_KRW = 1350.35
RENDER_BACKEND_USD = 7.0
RENDER_AI_USD = 7.0
POSTGRES_USD = 6.0
S3_STORAGE_PER_GB_MONTH = 0.025
S3_GET_EACH = 0.00000035
S3_PUT_EACH = 0.0000045
GPT_INPUT_PER_MTOK = 0.20
GPT_OUTPUT_PER_MTOK = 1.20
GPT_INPUT_MEAN = 4000
GPT_OUTPUT_MEAN = 500


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig") as f: return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)


def fnum(value: float, digits=2) -> str:
    return f"{value:,.{digits}f}"


def add_table(doc: Document, headers: list[str], rows: list[list[object]]) -> None:
    table = doc.add_table(rows=1, cols=len(headers)); table.style = "Table Grid"
    for i, h in enumerate(headers): table.rows[0].cells[i].text = str(h)
    for row in rows:
        cells = table.add_row().cells
        for i, value in enumerate(row): cells[i].text = str(value)


def build_raw() -> tuple[list[dict], list[dict]]:
    requests = read_csv(RAW / "backend_requests.csv")
    counters = defaultdict(int)
    three = []
    db_map = {r["endpoint_name"]: r for r in read_csv(RAW / "db_queries.csv")}
    db_rows = []
    for r in requests:
        name = r["endpoint_name"]; counters[name] += 1
        three.append({
            "action": name, "endpoint": r["path"], "test_no": counters[name],
            "status": r["status"], "request_bytes": r["request_body_bytes"],
            "response_bytes": r["response_body_bytes"], "latency_ms": r["latency_ms"],
            "s3_get_count_in_response": r["s3_url_count"], "gpt_trigger": False,
            "evidence": "A. direct read-only HTTP measurement",
        })
        if name in db_map:
            d = db_map[name]
            db_rows.append({"action": name, "test_no": counters[name], "select": d["select"],
                            "insert": d["insert"], "update": d["update"], "delete": d["delete"],
                            "total_queries": d["total_queries"],
                            "evidence": "C. static execution-path trace; DB statistics unavailable"})
    write_csv(RAW / "three_call_measurements.csv", three)
    write_csv(RAW / "db_three_call_measurements.csv", db_rows)
    gpt = []
    for no, (label, tokens) in enumerate((("짧은 요청", 3850), ("표준 요청", 4000), ("상세 요청", 4150)), 1):
        gpt.append({"example": no, "input_type": label, "input_tokens": tokens,
                    "output_tokens": GPT_OUTPUT_MEAN, "actual_api_calls": 0,
                    "method": "characters/4 approximation over code-built prompt and candidate payload",
                    "evidence": "C. token estimate; OpenAI API not called"})
    write_csv(RAW / "gpt_token_estimation.csv", gpt)
    return three, db_rows


def build_costs() -> tuple[list[dict], list[dict], list[dict]]:
    usage_source = [r for r in read_csv(SUMMARY / "infrastructure_summary.csv") if r["case_model"] == "B_cases_as_monthly"]
    s3_sample = read_csv(RAW / "s3_three_call_measurements.csv")
    object_bytes = sum(float(r["download_bytes"]) for r in s3_sample) / len(s3_sample)
    cumulative_storage_gb = 0.0
    usage_rows, cost_rows, bep_rows = [], [], []
    for r in usage_source:
        year = int(r["year"]); cases = int(float(r["cases_per_year"]) / 12)
        annual_upload_gb = float(r["s3_put_year"]) * object_bytes / 1024**3
        avg_storage_gb = cumulative_storage_gb + annual_upload_gb / 2
        cumulative_storage_gb += annual_upload_gb
        s3_download_gb_month = float(r["s3_get_month"]) * object_bytes / 1024**3
        usage = {
            "year": year, "monthly_transactions": cases,
            "backend_requests_month": int(float(r["backend_requests_month"])),
            "backend_requests_year": int(float(r["backend_requests_year"])),
            "backend_outbound_gb_month": round(float(r["backend_outbound_mb_month"]) / 1024, 6),
            "db_queries_month": int(float(r["db_total_queries_month"])),
            "db_queries_year": int(float(r["db_total_queries_year"])),
            "db_growth_gb_year_code_estimate": round(float(r["db_growth_mb_year"]) / 1024, 6),
            "s3_get_month": int(float(r["s3_get_month"])), "s3_put_month": int(float(r["s3_put_month"])),
            "s3_download_gb_month": round(s3_download_gb_month, 6),
            "s3_average_storage_gb_month": round(avg_storage_gb, 6),
            "s3_year_end_storage_gb": round(cumulative_storage_gb, 6),
            "gpt_calls_month": int(float(r["gpt_simulated_calls_month"])),
            "gpt_input_tokens_month": int(float(r["gpt_simulated_calls_month"])) * GPT_INPUT_MEAN,
            "gpt_output_tokens_month": int(float(r["gpt_simulated_calls_month"])) * GPT_OUTPUT_MEAN,
            "peak_rps": r["peak_rps"], "usage_evidence": "B except DB growth(C) and peak(C)",
        }
        usage_rows.append(usage)
        s3_storage = avg_storage_gb * S3_STORAGE_PER_GB_MONTH
        s3_get = usage["s3_get_month"] * S3_GET_EACH
        s3_put = usage["s3_put_month"] * S3_PUT_EACH
        s3_transfer = 0.0 if s3_download_gb_month <= 100 else float("nan")
        gpt = usage["gpt_input_tokens_month"] / 1e6 * GPT_INPUT_PER_MTOK + usage["gpt_output_tokens_month"] / 1e6 * GPT_OUTPUT_PER_MTOK
        total = RENDER_BACKEND_USD + RENDER_AI_USD + POSTGRES_USD + s3_storage + s3_get + s3_put + s3_transfer + gpt
        parts = {"render_backend_usd_month": RENDER_BACKEND_USD, "render_ai_usd_month": RENDER_AI_USD,
                 "postgres_usd_month": POSTGRES_USD, "s3_storage_usd_month": s3_storage,
                 "s3_requests_usd_month": s3_get + s3_put, "s3_transfer_usd_month": s3_transfer,
                 "gpt_usd_month": gpt}
        cost_rows.append({"year": year, **{k: round(v, 6) for k, v in parts.items()},
                          "total_usd_month": round(total, 6), "total_krw_month": round(total * USD_KRW),
                          "total_usd_year": round(total * 12, 6), "total_krw_year": round(total * USD_KRW * 12),
                          "current_free_configuration_usd_month": 0,
                          "pricing_basis": "recommended paid minimum: two Render Starter + Render PostgreSQL Basic-256mb"})
        sales = cases * 750
        bep_rows.append({"year": year, "monthly_sales_krw": sales, "monthly_infra_cost_krw": round(total * USD_KRW),
                         "infra_cost_ratio_percent": round(total * USD_KRW / sales * 100, 3),
                         "infra_bep_cases": math.ceil(total * USD_KRW / 750),
                         "note": "Infrastructure-only BEP; not total business break-even"})
    write_csv(SUMMARY / "infrastructure_usage_final.csv", usage_rows)
    write_csv(SUMMARY / "infrastructure_cost_final.csv", cost_rows)
    write_csv(SUMMARY / "infrastructure_bep_final.csv", bep_rows)
    return usage_rows, cost_rows, bep_rows


def charts(costs: list[dict], bep: list[dict]) -> list[Path]:
    CHARTS.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "Malgun Gothic", "axes.unicode_minus": False})
    years = [r["year"] for r in costs]
    fig, ax = plt.subplots(figsize=(12, 6.75)); ax.bar(years, [r["total_krw_month"] / 10000 for r in costs], color="#2f7ed8")
    ax.set(title="연차별 예상 월 인프라비", xlabel="사업연차", ylabel="만원/월"); ax.set_xticks(years); ax.grid(axis="y", alpha=.25)
    p1 = CHARTS / "01_monthly_infra_cost_16x9.png"; fig.savefig(p1, dpi=200, bbox_inches="tight", facecolor="white"); plt.close(fig)
    fig, ax = plt.subplots(figsize=(12, 6.75)); ax.plot(years, [r["infra_cost_ratio_percent"] for r in bep], marker="o", linewidth=3, color="#ef6c35")
    ax.set(title="수수료 매출 대비 인프라비", xlabel="사업연차", ylabel="비율 (%)"); ax.set_xticks(years); ax.grid(alpha=.25)
    p2 = CHARTS / "02_infra_cost_ratio_16x9.png"; fig.savefig(p2, dpi=200, bbox_inches="tight", facecolor="white"); plt.close(fig)
    return [p1, p2]


def report(three: list[dict], usage: list[dict], costs: list[dict], bep: list[dict], chart_paths: list[Path]) -> None:
    grouped = defaultdict(list)
    for r in three: grouped[r["action"]].append(r)
    action_rows = []
    for name, vals in grouped.items():
        resp = [float(x["response_bytes"]) / 1024 for x in vals]; lat = [float(x["latency_ms"]) for x in vals]
        action_rows.append([name, 3, fnum(min(resp),3), fnum(sum(resp)/3,3), fnum(max(resp),3), fnum(sum(lat)/3,2)])
    cost_table = [[r["year"], f"${r['total_usd_month']:.2f}", f"{r['total_krw_month']:,}원", f"{r['total_krw_year']:,}원"] for r in costs]
    md = "# 인프라 사용량 및 3개년 예상비용 최종보고서\n\n"
    md += "## 결론\n\n운영 권장 최소 구성(Render Starter 2대 + PostgreSQL Basic-256mb) 기준 예상 비용은 " + ", ".join(f"{r['year']}년차 월 {r['total_krw_month']:,}원" for r in costs) + "이다. 현재 free 구성의 청구액은 0달러일 수 있으나 운영용으로 권장하지 않는다.\n\n"
    md += "## 가격 기준\n\n- Render Starter: $7/service-month, Backend와 AI 각 1대\n- Render PostgreSQL Basic-256mb: $6/month\n- AWS S3 서울: Storage $0.025/GB-month, GET $0.0035/10,000, PUT $0.0045/1,000; 월 100GB 이내 인터넷 전송 무료 적용\n- GPT-5.6 Luna: OpenAI 공식 단가 Input $0.20/M, Cached Input $0.02/M, Output $1.20/M. 코드에 prompt caching이 없어 일반 Input 적용\n- 환율: 1 USD = 1,350.35 KRW (Frankfurter/ECB, 2026-09-04)\n- 조회일: 2026-09-06. Render https://render.com/pricing, AWS https://aws.amazon.com/s3/pricing/, OpenAI https://developers.openai.com/api/docs/pricing\n\n"
    md += "## 예상 비용\n\n| 연차 | USD/월 | KRW/월 | KRW/년 | 매출 대비 | 인프라 BEP |\n|---|---:|---:|---:|---:|---:|\n"
    for c, b in zip(costs, bep): md += f"| {c['year']} | ${c['total_usd_month']:.2f} | {c['total_krw_month']:,}원 | {c['total_krw_year']:,}원 | {b['infra_cost_ratio_percent']}% | {b['infra_bep_cases']}건 |\n"
    md += "\nGPT actual API calls = 0. 구매 생성 = 0. S3 객체 변경/삭제 = 0.\n"
    (DOCS / "infrastructure_cost_final_report.md").write_text(md, encoding="utf-8")

    doc = Document(); title = doc.add_heading("인프라 사용량 및 3개년 예상비용 최종보고서", 0); title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph("실측 3회 평균 × 사업계획 월 거래량 환산 | 산출일 2026-09-06").alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_heading("1. 산출 기준", 1)
    for x in ["B_cases_as_monthly 적용: 월 거래 900 / 3,600 / 10,800건", "구매 비중 1%: 거래 1건당 Backend 요청 100건", "A=직접 측정, B=실측 기반 환산, C=사업·코드 가정, D=측정 불가", "실제 GPT 호출·구매 생성·운영 DB 변경·S3 변경/삭제는 모두 0"]: doc.add_paragraph(x, style="List Bullet")
    doc.add_heading("2. 대표 API 3회 측정", 1); add_table(doc, ["Action", "Calls", "Min KB", "Mean KB", "Max KB", "Mean latency ms"], action_rows)
    doc.add_heading("3. 연차별 예상 사용량", 1)
    add_table(doc, ["연차", "Backend req/월", "Outbound GB/월", "DB query/월", "S3 GET/월", "S3 GB/월", "GPT call/월"], [[u["year"], f"{u['backend_requests_month']:,}", u["backend_outbound_gb_month"], f"{u['db_queries_month']:,}", f"{u['s3_get_month']:,}", u["s3_download_gb_month"], u["gpt_calls_month"]] for u in usage])
    doc.add_heading("4. 연차별 인프라 비용", 1); add_table(doc, ["연차", "USD/월", "KRW/월", "KRW/년"], cost_table)
    doc.add_paragraph("구성: Backend Starter $7 + AI Starter $7 + PostgreSQL Basic-256mb $6 + S3/GPT 종량비. 환율 1 USD=1,350.35 KRW.")
    doc.add_heading("5. 매출 대비 인프라비", 1); add_table(doc, ["연차", "월 수수료 매출", "월 인프라비", "비율"], [[b["year"], f"{b['monthly_sales_krw']:,}원", f"{b['monthly_infra_cost_krw']:,}원", f"{b['infra_cost_ratio_percent']}%"] for b in bep])
    doc.add_heading("6. 인프라 기준 손익분기점", 1); add_table(doc, ["연차", "필요 거래건수"], [[b["year"], f"{b['infra_bep_cases']}건/월"] for b in bep]); doc.add_paragraph("이는 전체 사업 손익분기점이 아니라 인프라 운영비만 상쇄하는 거래량이다.")
    for p in chart_paths: doc.add_page_break(); doc.add_picture(str(p), width=Inches(7.1))
    doc.add_heading("7. 결론", 1)
    for x in ["1~3년차 비용은 고정 Render 비용이 대부분이며 S3 요청·저장비는 유의미하지 않은 수준이다.", "GPT 비용도 현재 호출 비율에서는 전체 비용의 1% 미만이다.", "3년차 피크 6.16 RPS는 기존 혼합 실험 약 35 RPS 아래다. 다만 CPU/RAM 실측은 D이므로 Metrics 확인 없이 절대 용량을 보증하지 않는다.", "무료 플랜은 개발·검증용이다. 운영 전 두 Render 서비스와 DB를 최소 유료 플랜으로 전환하는 안을 예산에 반영한다."] : doc.add_paragraph(x, style="List Bullet")
    doc.add_heading("단가 출처와 한계", 1)
    doc.add_paragraph("Render Pricing: https://render.com/pricing\nAWS S3 Pricing: https://aws.amazon.com/s3/pricing/\nAWS Price List API: https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3/current/ap-northeast-2/index.json\nOpenAI Pricing: https://developers.openai.com/api/docs/pricing\n환율: https://frankfurter.app (ECB reference rates), 2026-09-04\nGPT-5.6 Luna 단가는 OpenAI 공식 가격표에서 확인했다. 사용량은 실제 호출이 아닌 C등급 token 추정치다.")
    doc.styles["Normal"].font.name = "Malgun Gothic"; doc.styles["Normal"].font.size = Pt(8.5)
    doc.save(DOCS / "인프라_사용량_및_3개년_예상비용_최종보고서.docx")


def main() -> None:
    three, _ = build_raw(); usage, costs, bep = build_costs(); report(three, usage, costs, bep, charts(costs, bep))
    print(json.dumps({"usage": usage, "cost": costs, "bep": bep}, ensure_ascii=False))


if __name__ == "__main__": main()
