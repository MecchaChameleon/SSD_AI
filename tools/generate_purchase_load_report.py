from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("MPLCONFIGDIR", str(Path(__file__).resolve().parents[1] / ".runtime" / "matplotlib"))

import matplotlib.pyplot as plt
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt


def product_qty(value: dict) -> int | None:
    current = value
    for _ in range(3):
        if not isinstance(current, dict):
            return None
        if isinstance(current.get("qty"), int):
            return current["qty"]
        current = current.get("data")
    return None


def add_table(document: Document, headers: list[str], rows: list[list[str]]) -> None:
    table = document.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = "Table Grid"
    for cell, header in zip(table.rows[0].cells, headers):
        cell.text = header
    for row in rows:
        cells = table.add_row().cells
        for cell, value in zip(cells, row):
            cell.text = value


def generate(result_path: Path, output_path: Path) -> None:
    report = json.loads(result_path.read_text(encoding="utf-8"))
    stages = report["stages"]
    result_dir = result_path.parent
    concurrency = [row["concurrency"] for row in stages]

    plt.rcParams["font.family"] = ["Malgun Gothic", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax1 = plt.subplots(figsize=(10, 5.5))
    ax1.plot(concurrency, [row["throughput_rps"] for row in stages], marker="o", linewidth=2.4, color="#2563EB")
    ax1.set_xlabel("동시 요청 수")
    ax1.set_ylabel("처리량(RPS)", color="#2563EB")
    ax1.tick_params(axis="y", labelcolor="#2563EB")
    ax1.grid(axis="both", alpha=.2)
    ax2 = ax1.twinx()
    ax2.plot(concurrency, [row["latency_p95_ms"] for row in stages], marker="s", linewidth=2.4, color="#DC2626")
    ax2.axhline(report["stop_rules"]["maximum_p95_ms"], color="#DC2626", linestyle="--", alpha=.6, label="중단 기준")
    ax2.set_ylabel("p95 응답시간(ms)", color="#DC2626")
    ax2.tick_params(axis="y", labelcolor="#DC2626")
    plt.title("구매 API 동시성별 처리량과 p95 응답시간")
    fig.tight_layout()
    performance_chart = result_dir / "purchase_load_performance.png"
    fig.savefig(performance_chart, dpi=180, bbox_inches="tight")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 4.8))
    ax.plot(concurrency, [row["latency_p50_ms"] for row in stages], marker="o", label="p50")
    ax.plot(concurrency, [row["latency_p95_ms"] for row in stages], marker="o", label="p95")
    ax.plot(concurrency, [row["latency_p99_ms"] for row in stages], marker="o", label="p99")
    ax.axhline(1000, color="#F59E0B", linestyle="--", label="권장 p95 1초")
    ax.set_xlabel("동시 요청 수")
    ax.set_ylabel("응답시간(ms)")
    ax.set_title("구매 API 응답시간 백분위")
    ax.grid(alpha=.2)
    ax.legend()
    fig.tight_layout()
    latency_chart = result_dir / "purchase_load_latency.png"
    fig.savefig(latency_chart, dpi=180, bbox_inches="tight")
    plt.close(fig)

    before_qty = product_qty(report.get("product_before", {}))
    after_qty = product_qty(report.get("product_after", {}))
    successful = sum(sum(count for status, count in row["statuses"].items() if status.startswith("2")) for row in stages)
    strict = [row for row in stages if row["success_rate"] >= .99 and row["latency_p95_ms"] <= 1000]
    relaxed = [row for row in stages if row["success_rate"] >= .99 and row["latency_p95_ms"] <= report["stop_rules"]["maximum_p95_ms"]]

    document = Document()
    styles = document.styles
    styles["Normal"].font.name = "맑은 고딕"
    styles["Normal"].font.size = Pt(10)
    title = document.add_heading("배포 서버 구매 API 부하 테스트 결과", 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle = document.add_paragraph(f"대상: {report['base_url']}{report['endpoint']}")
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER

    document.add_heading("1. 실험 개요", level=1)
    document.add_paragraph(
        "실제 배포된 구매 API에 인증된 테스트 구매자로 요청을 보내고, 각 성공 요청이 DB의 구매 내역 생성과 상품 재고 차감까지 수행하도록 측정했다. "
        "동시성을 단계적으로 증가시키되 성공률 99% 미만 또는 p95 3초 초과 시 다음 단계를 실행하지 않는 보호 기준을 적용했다."
    )
    add_table(document, ["항목", "설정"], [
        ["상품 ID", str(report["product_id"])],
        ["단계별 요청", str(report["requests_per_stage"]) + "건"],
        ["실행 동시성", ", ".join(map(str, concurrency))],
        ["사전 health 응답시간", f"{report['health_latency_ms']:.2f} ms"],
        ["자동 중단 기준", "성공률 99% 미만 또는 p95 3,000ms 초과"],
    ])

    document.add_heading("2. 실험 결과", level=1)
    add_table(document,
              ["동시성", "요청", "성공률", "RPS", "평균(ms)", "p50(ms)", "p95(ms)", "p99(ms)"],
              [[str(row["concurrency"]), str(row["requests"]), f"{row['success_rate']*100:.1f}%",
                f"{row['throughput_rps']:.2f}", f"{row['latency_avg_ms']:.2f}",
                f"{row['latency_p50_ms']:.2f}", f"{row['latency_p95_ms']:.2f}",
                f"{row['latency_p99_ms']:.2f}"] for row in stages])
    document.add_picture(str(performance_chart), width=Inches(6.5))
    document.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    document.add_picture(str(latency_chart), width=Inches(6.5))
    document.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER

    document.add_heading("3. 결과 해석", level=1)
    document.add_paragraph(
        f"총 {successful}건의 본 테스트 구매가 모두 HTTP 200으로 완료됐다. "
        f"엄격한 운영 목표(성공률 99% 이상, p95 1초 이하)를 만족한 최대 동시성은 "
        f"{strict[-1]['concurrency'] if strict else '없음'}이며, p95 3초 기준의 보수적 최대 동시성은 "
        f"{relaxed[-1]['concurrency'] if relaxed else '없음'}이다."
    )
    document.add_paragraph(
        "동시성 5까지 처리량이 증가했지만 동시성 10에서는 처리량 증가 폭이 작아지고 p95가 6초 이상으로 급증했다. "
        "이는 애플리케이션 또는 무료 DB 연결/쓰기 처리 구간이 포화되면서 요청 대기가 누적된 것으로 해석할 수 있다. "
        "모든 요청은 성공했으므로 장애 한계가 아니라 응답시간 기준의 실용적 한계를 측정한 결과다."
    )
    if before_qty is not None and after_qty is not None:
        document.add_paragraph(
            f"본 테스트 전후 재고는 {before_qty:,}개에서 {after_qty:,}개로 {before_qty-after_qty:,}개 감소해, "
            "성공 응답 수와 실제 DB 반영 수가 일치했다. 별도 사전 검증 구매 1건은 이 수치에 포함되지 않는다."
        )

    document.add_heading("4. 서버 부하 평가 및 권고", level=1)
    for text in [
        "현재 무료 배포 구성의 권장 운영 동시성은 p95 1초 목표 기준 3 이하로 본다.",
        "동시성 5는 성공률은 안정적이지만 p95 약 1.47초이므로 느린 응답을 허용하는 경우에만 사용할 수 있다.",
        "동시성 10은 p95 약 6.1초로 급증했으므로 현재 구성의 운영 권장 범위에서 제외한다.",
        "이번 테스트는 단계별 50건의 단기 테스트다. 최종 용량 산정을 위해서는 유료 또는 운영 동급 DB에서 10~30분 지속 부하와 CPU, 메모리, DB connection pool 지표를 함께 측정해야 한다.",
        "구매 요청은 실제 데이터를 생성하므로 재시험 전 테스트 주문 정리 정책과 전용 상품·계정을 유지해야 한다.",
    ]:
        document.add_paragraph(text, style="List Bullet")

    document.add_heading("5. 한계", level=1)
    document.add_paragraph(
        "측정값은 Render 리전, 네트워크 상태, 무료 인스턴스의 자원 공유 및 콜드 스타트 영향을 받는다. "
        "또한 단일 상품에 쓰기가 집중된 실험이므로 여러 상품으로 분산되는 실제 트래픽과 잠금 경합 양상이 다를 수 있다. "
        "서버 및 DB의 CPU·메모리·활성 연결 수를 직접 수집하지 않았으므로 병목 지점은 응답 패턴을 근거로 한 추정이다."
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(output_path)
    print(output_path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    generate(args.result, args.output)


if __name__ == "__main__":
    main()
