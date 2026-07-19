from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
from xml.sax.saxutils import escape


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "AI_추천가_시스템_기술문서.docx"
ARTIFACT = ROOT / "artifacts" / "daily_demand_profile.json"


def run(text: str, bold: bool = False, size: int | None = None, color: str | None = None) -> str:
    props = [
        '<w:rFonts w:ascii="Malgun Gothic" w:hAnsi="Malgun Gothic" w:eastAsia="맑은 고딕"/>',
    ]
    if bold:
        props.append("<w:b/>")
    if size:
        props.append(f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>')
    if color:
        props.append(f'<w:color w:val="{color}"/>')
    return f'<w:r><w:rPr>{"".join(props)}</w:rPr><w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def paragraph(text: str = "", style: str | None = None, *, bold: bool = False,
              size: int | None = None, color: str | None = None,
              align: str | None = None, before: int = 0, after: int = 100) -> str:
    ppr = []
    if style:
        ppr.append(f'<w:pStyle w:val="{style}"/>')
    if align:
        ppr.append(f'<w:jc w:val="{align}"/>')
    ppr.append(f'<w:spacing w:before="{before}" w:after="{after}" w:line="320" w:lineRule="auto"/>')
    return f'<w:p><w:pPr>{"".join(ppr)}</w:pPr>{run(text, bold, size, color)}</w:p>'


def bullet(text: str, level: int = 0) -> str:
    return paragraph(("    " * level) + "• " + text, after=50)


def code(text: str) -> str:
    lines = []
    for line in text.splitlines():
        lines.append(
            '<w:p><w:pPr><w:shd w:fill="F3F4F6"/><w:ind w:left="180" w:right="180"/>'
            '<w:spacing w:after="0" w:line="280" w:lineRule="auto"/></w:pPr>'
            f'{run(line, size=18, color="333333")}</w:p>'
        )
    return "".join(lines)


def page_break() -> str:
    return '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'


def table(headers: list[str], rows: list[list[str]], widths: list[int] | None = None) -> str:
    widths = widths or [int(9000 / len(headers))] * len(headers)

    def cell(value: str, width: int, header: bool = False) -> str:
        fill = '<w:shd w:fill="F3AB24"/>' if header else '<w:shd w:fill="FFFFFF"/>'
        color = "FFFFFF" if header else "222222"
        return (
            f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{fill}'
            '<w:tcMar><w:top w:w="90" w:type="dxa"/><w:left w:w="100" w:type="dxa"/>'
            '<w:bottom w:w="90" w:type="dxa"/><w:right w:w="100" w:type="dxa"/></w:tcMar></w:tcPr>'
            f'<w:p><w:pPr><w:spacing w:after="0"/></w:pPr>{run(value, bold=header, size=18, color=color)}</w:p></w:tc>'
        )

    border = (
        '<w:tblBorders><w:top w:val="single" w:sz="4" w:color="D9D9D9"/>'
        '<w:left w:val="single" w:sz="4" w:color="D9D9D9"/>'
        '<w:bottom w:val="single" w:sz="4" w:color="D9D9D9"/>'
        '<w:right w:val="single" w:sz="4" w:color="D9D9D9"/>'
        '<w:insideH w:val="single" w:sz="4" w:color="D9D9D9"/>'
        '<w:insideV w:val="single" w:sz="4" w:color="D9D9D9"/></w:tblBorders>'
    )
    result = [f'<w:tbl><w:tblPr><w:tblW w:w="9000" w:type="dxa"/>{border}</w:tblPr>']
    result.append('<w:tr>' + "".join(cell(v, widths[i], True) for i, v in enumerate(headers)) + '</w:tr>')
    for row in rows:
        result.append('<w:tr>' + "".join(cell(str(v), widths[i]) for i, v in enumerate(row)) + '</w:tr>')
    result.append('</w:tbl>')
    result.append(paragraph("", after=60))
    return "".join(result)


def build_document() -> str:
    meta = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    regions = len(meta.get("profiles", {}))
    generated = datetime.now().strftime("%Y-%m-%d")
    body: list[str] = []

    body += [
        paragraph("제주 로컬타임", align="center", size=22, color="B67805", before=900, after=120),
        paragraph("AI 추천가 시스템 기술 문서", align="center", bold=True, size=38, after=240),
        paragraph("3년 일일 유동인구 EBM 수요예측과 실시간 동적 가격 정책", align="center", size=24, color="555555", after=500),
        paragraph(f"문서 기준일  {generated}", align="center", size=20, color="777777"),
        paragraph(f"운영 모델  {meta['modelVersion']} + kma-jeju-pricing-2026.07.5", align="center", size=20, color="777777"),
        page_break(),
        paragraph("문서 목적", "Heading1"),
        paragraph("이 문서는 현재 구현된 AI 추천가 영역만을 대상으로 데이터, 모델, 추론, 가격 산출, 설명, 자동 적용, 배포 및 운영 구조를 정리한다. 기획안이 아니라 저장소의 현재 코드와 생성된 학습 아티팩트를 기준으로 작성했다."),
        paragraph("핵심 정의", "Heading2"),
        paragraph("현재 시스템은 완전한 종단 간 가격 예측 모델이 아니다. 과거 제주 유동인구로 학습한 EBM이 지역수요를 예측하고, 현재 날씨·재고·실제 판매량·최근 재고 변화·판매 경과 시간을 정책 공식에 넣어 판매자 설정 범위 안의 가격을 계산하는 하이브리드 AI 가격 시스템이다.", bold=True),
        table(["구성 요소", "구현 방식", "역할"], [
            ["지역수요", "EBM 머신러닝", "읍면동·날짜 조건의 예상 방문인구와 수요 백분위 산출"],
            ["날씨", "실시간 외부 API", "현재 기온·강수·풍속을 가격 위험점수로 변환"],
            ["재고·판매", "서비스 DB 실시간 데이터", "잔여수량, 유효 결제 판매량, 최근 변화량 반영"],
            ["최종 가격", "설명 가능한 정책 공식", "정상가와 최저가 사이의 추천가 계산"],
            ["설명", "중립 조건 대비 원 단위 기여", "각 요인이 가격을 몇 원 올리거나 내렸는지 표시"],
        ], [1800, 2200, 5000]),
        paragraph("1. 전체 처리 흐름", "Heading1"),
        code("제주데이터허브 3년 일일 유동인구\n        ↓ 수집·정제·달력 피처 생성\nExplainable Boosting Machine 학습\n        ↓ 지역×날짜×요일×공휴일 예측 프로필 생성\ndaily_demand_profile.json\n        ↓ 온라인 조회\n지역수요 + 현재 날씨 + 재고/판매 + 판매시간\n        ↓ 가격 정책 엔진\n판매자 정상가~최저가 범위의 AI 추천가\n        ↓ Render 백엔드 적용\n구매자 메뉴 현재 판매가 갱신"),
        paragraph("온라인 요청 경로", "Heading2"),
        bullet("판매자 프론트가 GET /api/seller/products/{id}/price를 호출한다."),
        bullet("Render의 Spring 백엔드가 상품·판매자·주문 DB에서 최신 입력값을 구성한다."),
        bullet("백엔드는 Cloudflare Tunnel을 통해 로컬 FastAPI의 POST /v1/pricing/recommendation을 호출한다."),
        bullet("FastAPI는 지역수요 아티팩트와 현재 제주 날씨를 결합해 추천가와 설명값을 반환한다."),
        bullet("자동 가격이 켜진 ACTIVE 상품은 추천 조회 시 변경된 가격을 즉시 current_price에 적용한다."),
        bullet("프론트는 약 5초 주기로 변경을 감지한다. 10분 스케줄러는 화면이 열려 있지 않을 때의 보조 갱신이다."),
        page_break(),
        paragraph("2. 학습 데이터", "Heading1"),
        paragraph("2.1 데이터 출처", "Heading2"),
        table(["항목", "내용"], [
            ["제공처", "제주데이터허브 Open API"],
            ["데이터 성격", "제주 읍면동 단위 일일 내국인 유동인구 표본"],
            ["인증 환경변수", "JEJU_DATA_HUB_KEY"],
            ["원본 저장 위치", "SSD_AI/data/raw/jeju_daily_flow_3y.csv"],
            ["현재 학습 기간", f"{meta['trainingStartDate']} ~ {meta['trainingEndDate']}"],
            ["현재 학습 행 수", f"{meta['trainingRows']:,}행"],
            ["고유 날짜", "1,095일"],
            ["읍면동", f"{regions}개"],
            ["테스트 MAE", f"{meta['testMae']:,}"],
        ], [2200, 6800]),
        paragraph("API는 최근 데이터가 존재하는 마지막 날짜를 찾고 그 날짜부터 3×365일 범위를 수집한다. 요청당 100행 제한을 고려해 3일 구간으로 나누고 최대 8개 요청을 병렬 처리한다. 누락 날짜는 --complete-missing 옵션으로 일별 재조회한다."),
        paragraph("중요: 현재 51,300행은 API가 반환한 표본이다. 전체 제주 인구 원장을 의미하지 않으며 API 제한과 제공 방식의 영향을 받는다.", bold=True, color="9C4A00"),
        paragraph("2.2 원본 컬럼", "Heading2"),
        table(["원본 필드", "내부 컬럼", "현재 사용"], [
            ["baseDate", "date", "피처 생성 기준 날짜"],
            ["city", "city", "학습 피처"],
            ["emd", "region", "학습 피처 및 온라인 지역키"],
            ["gender", "gender", "학습 피처"],
            ["ageGroup", "age_group", "학습 피처"],
            ["resdPop", "resident_pop", "수집·저장만 하며 현재 EBM 피처에서는 제외"],
            ["workPop", "worker_pop", "수집·저장만 하며 현재 EBM 피처에서는 제외"],
            ["visitPop", "visit_pop", "예측 타깃"],
        ], [1800, 2200, 5000]),
        paragraph("2.3 정제", "Heading2"),
        bullet("필수 8개 컬럼만 선택하고 중복 행을 제거한다."),
        bullet("baseDate를 YYYYMMDD 형식 날짜로 변환한다."),
        bullet("날짜·지역·성별·연령대 순으로 정렬한다."),
        bullet("region, city, gender, age_group을 범주형으로 변환한다."),
        bullet("현재 코드에는 결측치 대체, 이상치 제거, 표본 가중치 보정이 별도로 없다."),
        paragraph("3. EBM 지역수요 모델", "Heading1"),
        paragraph("3.1 모델", "Heading2"),
        table(["설정", "값"], [
            ["알고리즘", "interpret.glassbox.ExplainableBoostingRegressor"],
            ["타깃", "visit_pop"],
            ["interactions", "8"],
            ["max_rounds", "3,000"],
            ["random_state", "42"],
            ["검증 분리", "마지막 90일을 테스트, 이전 기간을 학습"],
            ["현재 버전", meta["modelVersion"]],
        ], [2500, 6500]),
        paragraph("EBM은 각 피처의 비선형 효과와 제한된 피처 간 상호작용을 학습하는 설명 가능한 부스팅 모델이다. 현재 시스템에서는 최종 상품가격이 아니라 방문인구를 예측하는 데 사용한다."),
        paragraph("3.2 학습 피처", "Heading2"),
        table(["피처", "설명"], [
            ["region", "읍면동"], ["city", "제주시·서귀포시"], ["gender", "성별"],
            ["age_group", "연령대"], ["month", "월"], ["day", "일"],
            ["day_of_year", "연중 일자"], ["day_of_week", "요일(월=0)"],
            ["is_weekend", "주말 여부"], ["is_holiday", "대한민국 공휴일 여부"],
        ], [2600, 6400]),
        paragraph("3.3 온라인 아티팩트 생성", "Heading2"),
        paragraph("온라인 요청마다 EBM 객체를 로드해 추론하지 않는다. 학습 완료 후 모든 지역과 달력 조건을 미리 예측해 JSON 조회표를 만든다."),
        code("조합 = 읍면동 × 366개 달력일 × 요일 7개 × 공휴일 여부 2개 × 성별·연령대 조합\n키   = MMDD|weekday|holiday\n값   = 성별·연령대 예측 방문인구 합계 + 동일 지역 내 수요 백분위"),
        table(["아티팩트 항목", "설명"], [
            ["profiles", "지역별·달력조건별 수요 백분위(0~1)"],
            ["predictedVisitPopulation", "지역별·달력조건별 예측 방문인구 합계"],
            ["trainingStartDate / EndDate", "학습 데이터 기간"],
            ["testMae", "마지막 90일 테스트 MAE"],
            ["features", "학습 피처 목록"],
        ], [3000, 6000]),
        paragraph("지역수요 백분위는 같은 읍면동의 전체 달력조건 예측값 안에서 순위를 계산한다. 따라서 동일 읍면동·동일 날짜에는 하루 동안 값이 고정되며 구매, 재고, 현재 날씨가 바뀌어도 지역수요 순위 자체는 바뀌지 않는다."),
        paragraph("프론트의 ‘상위 N%’는 round((1 - percentile) × 100)으로 표시한다. 예를 들어 percentile=0.12이면 상위 88%이다. 이는 높은 수요가 아니라 해당 지역의 과거 조건 중 비교적 낮은 구간이라는 뜻이다."),
        paragraph("3.4 주소와 지역 매칭", "Heading2"),
        bullet("주소 문자열에 학습 읍면동이 있으면 직접 매칭한다."),
        bullet("일도일동→일도1동 등 법정동/행정동 표기 차이를 별칭으로 정규화한다."),
        bullet("도로명 주소에 읍면동이 없으면 latitude/longitude와 KAKAO_REST_API_KEY로 좌표→행정동 역지오코딩한다."),
        bullet("끝까지 지역을 찾지 못하면 같은 날짜조건의 모든 제주 지역 EBM 결과를 평균·합산한 ‘제주 전체’ fallback을 사용한다."),
        page_break(),
        paragraph("4. 실시간 입력 데이터", "Heading1"),
        table(["입력", "출처", "가격 반영"], [
            ["정상가·최저가", "상품 DB / 판매자 설정", "추천가가 움직일 수 있는 절대 범위"],
            ["잔여수량", "product.remaining_quantity", "많을수록 할인 압력 증가"],
            ["실제 판매량", "유효 결제 주문 SUM(quantity)", "화면 표시 및 유효 총수량 구성"],
            ["최근 재고 변화", "현재 잔여−직전 AI 관측 잔여", "최근 판매속도 또는 재입고 감지"],
            ["판매 시작·마감", "상품 DB", "판매 경과율 계산"],
            ["현재 시각", "Render 백엔드", "요일·시간대·마감 잔여시간"],
            ["주소·좌표", "상품/판매자 프로필", "읍면동 수요와 제주 날씨 위치"],
            ["현재 날씨", "기상청, 실패 시 Open-Meteo", "날씨 위험점수"],
            ["상품 업종·유형", "상품 DB", "야외 상품 날씨 민감도"],
        ], [1800, 3000, 4200]),
        paragraph("실제 판매량 정의", "Heading2"),
        paragraph("재고를 판매자가 늘리거나 줄여도 판매량이 0으로 잘리지 않도록 단순히 최초수량−잔여수량을 사용하지 않는다. 백엔드는 payment_status='PAID'이고 status가 REQUESTED 또는 COMPLETED인 주문의 quantity를 합산한다. 거절·환불 주문은 제외된다."),
        code("sold_quantity = SUM(reservation.quantity)\nWHERE payment_status = 'PAID'\n  AND status IN ('REQUESTED', 'COMPLETED')\n\npricing_total_quantity = remaining_quantity + sold_quantity"),
        paragraph("최근 재고 변화량", "Heading2"),
        code("inventory_change = 현재 잔여수량 - 직전 AI 적용 시 관측수량\n음수: 재고 감소(최근 판매 발생)\n양수: 재고 증가(재입고 또는 판매자 수량 상향)\n최초 관측: 0"),
        paragraph("5. 실시간 제주 날씨", "Heading1"),
        bullet("제주 범위: 위도 32.9~33.7, 경도 125.9~127.1"),
        bullet("WGS84 좌표를 기상청 5km Lambert 격자(nx, ny)로 변환한다."),
        bullet("DATA_GO_KR_KEY로 기상청 초단기실황(getUltraSrtNcst)을 조회한다."),
        bullet("초단기예보도 내부적으로 조회하지만 현재 가격 피처와 화면은 현재 실황만 사용한다."),
        bullet("기상청 결과는 동일 격자·기준시각 기준 600초 캐시한다."),
        bullet("기상청 실패 시 Open-Meteo 현재값, 그마저 실패하면 기본 WeatherContext를 사용한다."),
        paragraph("날씨 위험점수", "Heading2"),
        code("weather_score = clip(\n  min(강수량, 20) / 20 × 0.50\n  + max(풍속-5, 0) / 15 × 0.30\n  + min(|기온-20|, 15) / 15 × 0.20,\n  0, 1\n)"),
        paragraph("강수 50%, 강풍 30%, 20°C에서 벗어난 기온 20%로 구성된다. 프론트의 별도 ‘실시간 제주 날씨’ 카드는 제거됐지만 ‘이 가격에 영향을 준 요인’의 날씨 기여와 실제 가격 계산에는 계속 사용된다."),
        page_break(),
        paragraph("6. 가격 산출", "Heading1"),
        paragraph("6.1 입력 피처", "Heading2"),
        table(["내부 피처", "범위/정의"], [
            ["demand_percentile", "EBM 지역수요 백분위 0~1"],
            ["remaining_ratio", "잔여수량 / (잔여수량+실제판매량)"],
            ["inventory_change", "현재 잔여−직전 관측 잔여"],
            ["elapsed_ratio", "판매시작부터 마감까지 경과 비율 0~1"],
            ["weather", "현재 날씨 위험점수 0~1"],
            ["weekend", "토·일이면 1"],
            ["peak_hour", "11~13시 또는 17~20시면 1"],
            ["outdoor", "TOUR_REMAINDER 또는 RENTAL_MOBILITY면 1"],
        ], [2700, 6300]),
        paragraph("6.2 할인 압력 공식", "Heading2"),
        code("sales_velocity = clip(-inventory_change, -10, 10) / 10\n\ndiscount_pressure =\n    0.08\n  + (1 - demand_percentile) × 0.12\n  + remaining_ratio × 0.10\n  - sales_velocity × 0.12\n  + elapsed_ratio × 0.15\n  + weather_score × (0.03 + 0.07 × outdoor)\n  - weekend × 0.025\n  - peak_hour × 0.02\n\ndiscount_pressure = clip(discount_pressure, 0.03, 0.65)"),
        table(["조건", "가격 방향", "이유"], [
            ["지역수요가 낮음", "가격 하락", "수요 부족 보완"],
            ["잔여 재고가 많음", "가격 하락", "재고 소진 유도"],
            ["최근 판매가 빠름", "가격 상승", "과도한 할인 방지"],
            ["재입고/재고 증가", "가격 하락", "늘어난 재고 부담 반영"],
            ["마감 임박", "가격 하락", "남은 판매시간 감소"],
            ["악천후", "가격 하락", "수요 위험 보완"],
            ["야외 상품 악천후", "더 큰 하락", "날씨계수 0.10, 일반 상품 0.03"],
            ["주말·피크시간", "가격 상승", "자연 수요가 높다고 가정"],
        ], [2300, 1700, 5000]),
        paragraph("중요: 위 계수는 EBM이 학습한 가격 가중치가 아니다. 서비스 운영 정책으로 코드에 설정된 계수다. EBM이 학습하는 부분은 지역 방문수요뿐이다.", bold=True, color="9C4A00"),
        paragraph("6.3 판매자 가격범위 매핑", "Heading2"),
        code("position = clip((pressure - 0.03) / (0.65 - 0.03), 0, 1)\n추천가 = 정상가 - position × (정상가 - 최저가)"),
        bullet("pressure=0.03이면 판매자 정상가에 도달한다."),
        bullet("pressure=0.65이면 판매자 최저가에 도달한다."),
        bullet("중간값은 가격범위 안에 선형 매핑한다."),
        bullet("중간 가격은 100원 단위로 반올림하며 경계에서는 정확한 정상가·최저가를 우선한다."),
        bullet("Render 백엔드에서도 다시 최저가~정상가로 clamp하므로 범위를 벗어나지 않는다."),
        paragraph("6.4 계산 예시", "Heading2"),
        paragraph("정상가 20,000원, 최저가 10,000원, 지역수요 0.21, 잔여비율 0.75, 최근 재고변화 -2, 판매경과 0.50, 날씨 0.10, 평일·비피크·일반상품을 가정한다."),
        code("sales_velocity = 0.2\npressure = 0.08 + 0.79×0.12 + 0.75×0.10 - 0.2×0.12 + 0.5×0.15 + 0.1×0.03\n         ≈ 0.304\nposition = (0.304-0.03)/0.62 ≈ 0.442\n추천가 = 20,000 - 0.442×10,000 ≈ 15,600원"),
        paragraph("7. 가격 설명", "Heading1"),
        paragraph("현재 SHAP은 사용하지 않는다. explanationMethod는 POLICY_CONTRIBUTION이다. 각 요인을 중립으로 되돌린 가격과 실제 가격을 비교해 원 단위 영향금액을 계산한다."),
        table(["화면 요인", "중립 조건"], [
            ["판매 경과 시간", "elapsed=0.5, weekend=0, peak=0"],
            ["잔여 수량 및 판매량", "remaining=0.5, inventory_change=0"],
            ["날씨", "weather=0"],
            ["지역 수요", "demand_percentile=0.5"],
        ], [3000, 6000]),
        code("impact = 실제 조건 가격 - 해당 요인을 중립화한 가격\n양수: 해당 요인이 가격을 올림\n음수: 해당 요인이 가격을 내림"),
        paragraph("막대그래프는 네 영향금액 중 절대값이 가장 큰 항목을 100%로 정규화한다. 따라서 막대 길이는 원 단위 절대 크기의 상대 비교이며 확률이나 모델 중요도가 아니다."),
        paragraph("중립 기준가격", "Heading2"),
        paragraph("프론트는 현재 추천가에서 네 영향금액 합계를 빼고 100원 단위로 반올림해 ‘중립 기준’ 가격을 표시한다. 정상가 20,000원·최저가 10,000원일 때 모든 조건의 중립 압력은 현재 공식상 0.265이므로 약 16,200원이다. 이는 판매자 설정값이나 학습된 시장가격이 아니라 설명용 정책 기준이다."),
        page_break(),
        paragraph("8. API", "Heading1"),
        paragraph("8.1 상태 확인", "Heading2"),
        code("GET /health\n응답: { status: 'ok', modelVersion: '...' }"),
        paragraph("8.2 추천가", "Heading2"),
        code("POST /v1/pricing/recommendation\nHeader: X-AI-Service-Key: <AI_SERVICE_API_KEY>"),
        table(["요청 그룹", "필드"], [
            ["식별", "productId, sellerProfileId"],
            ["상품", "businessType, category"],
            ["가격", "originalPrice, minimumPrice, currentPrice"],
            ["수량", "totalQty(잔여+실제판매), remainingQty, inventoryChange"],
            ["시간", "availableStartAt, deadline, currentTime"],
            ["위치", "address, latitude, longitude"],
            ["기타", "footTrafficLevel(현재 가격 공식에서는 미사용)"],
        ], [2200, 6800]),
        table(["응답", "설명"], [
            ["currentPrice", "현재 추천가"], ["discountPct", "정상가 대비 할인율"],
            ["minutesLeft", "마감까지 남은 분"], ["priceTimeline", "0/10/20/30분 시간경과 시나리오"],
            ["explanations", "4개 요인의 원 단위 영향"], ["weather", "현재/내부 예보 데이터와 출처"],
            ["regionalDemand", "지역, 백분위, 예측 방문인구, 학습기간"],
            ["modelVersion", "수요모델+가격정책 버전"],
        ], [2400, 6600]),
        paragraph("priceTimeline은 같은 현재 날씨를 유지하고 시간·날짜 조건만 앞으로 이동한 단기 시나리오다. 확정 미래가격이나 진짜 미래 날씨 예측으로 해석하면 안 된다."),
        paragraph("9. 자동 가격 적용", "Heading1"),
        bullet("판매자가 토글을 켜면 즉시 추천가를 계산해 적용한다."),
        bullet("AI 추천 화면은 약 5초마다 최신 추천을 조회한다."),
        bullet("자동가격이 켜진 ACTIVE 상품은 추천 조회와 같은 백엔드 트랜잭션에서 current_price를 변경한다."),
        bullet("가격이 변경되면 product_price_history에 AI_RECOMMENDATION으로 이전·변경 가격을 기록한다."),
        bullet("ai_last_observed_quantity를 저장해 다음 요청의 최근 재고변화를 계산한다."),
        bullet("10분 스케줄러는 판매자 화면이 열려 있지 않을 때도 갱신하기 위한 안전망이다."),
        bullet("구매자 홈은 약 5초 주기로 상품을 다시 받아 변경된 메뉴가격을 표시한다."),
        paragraph("AI 호출 실패 시 예외를 반환하고 기존 상품가격을 보존한다. 로컬 PC, FastAPI 또는 Cloudflare Tunnel이 중단되면 자동가격 갱신도 중단된다."),
        paragraph("10. 실행·배포·보안", "Heading1"),
        table(["환경변수", "사용 위치", "용도"], [
            ["JEJU_DATA_HUB_KEY", "학습", "3년 일일 유동인구 수집"],
            ["DATA_GO_KR_KEY", "실시간 추론", "기상청 초단기실황·예보"],
            ["KAKAO_REST_API_KEY", "실시간 추론", "좌표→읍면동 역지오코딩"],
            ["AI_SERVICE_API_KEY", "AI/Render", "X-AI-Service-Key 인증"],
            ["AI_SERVICE_BASE_URL", "Render", "Cloudflare Tunnel의 AI 주소"],
        ], [2600, 2000, 4400]),
        code("학습\n.python\\python.exe train_daily_flow_ebm.py --complete-missing\n\n테스트\n.python\\python.exe -m pytest -q\n\n로컬 실행 및 터널\nstart_ai_tunnel.cmd"),
        bullet("start_ai_tunnel.cmd는 FastAPI 재시작, Cloudflared 실행, Quick Tunnel URL 탐지를 수행한다."),
        bullet("프론트 .env에 있는 기존 Kakao REST 키를 로컬 실행 시 재사용할 수 있다."),
        bullet("Quick Tunnel URL은 재시작 시 바뀔 수 있으므로 Render AI_SERVICE_BASE_URL을 갱신해야 한다."),
        bullet(".env 값은 문서, 저장소, 로그에 포함하지 않는다."),
        paragraph("11. 장애 대응과 fallback", "Heading1"),
        table(["장애", "동작"], [
            ["정확한 읍면동 매칭 실패", "좌표 기반 Kakao 역지오코딩"],
            ["주소·좌표 모두 불충분", "제주 전체 동일 날짜조건 EBM aggregate"],
            ["기상청 장애", "Open-Meteo 현재 날씨"],
            ["모든 날씨 API 장애", "기본 WeatherContext"],
            ["일일 EBM 아티팩트 없음", "레거시 월별 아티팩트/CSV fallback"],
            ["AI 서버 또는 터널 장애", "백엔드 오류, 기존 판매가 유지"],
        ], [3000, 6000]),
        paragraph("12. 검증", "Heading1"),
        bullet("SSD_AI pytest: 현재 7개 테스트 통과"),
        bullet("제주 좌표→기상청 격자 변환 및 제주 범위 검증"),
        bullet("일일 EBM 지역·날짜 조회와 제주 전체 fallback 검증"),
        bullet("법정동 별칭 및 Kakao 행정동 응답 매칭 검증"),
        bullet("가격 설명 4개 요인 및 POLICY_CONTRIBUTION 응답 검증"),
        bullet("Spring 테스트에서 실제 유효 결제 판매량 전달, 자동가격 즉시 적용, 판매자 가격범위 clamp 검증"),
        paragraph("13. 현재 한계", "Heading1"),
        table(["한계", "영향"], [
            ["최종 가격은 학습 모델이 아니라 정책식", "실제 매출·전환율로 최적 계수를 학습한 상태가 아님"],
            ["플랫폼 판매이력 미학습", "상품별 가격탄력성·구매확률·폐기비용을 직접 예측하지 않음"],
            ["지역수요는 일 단위", "같은 지역·날짜에서는 하루 동안 순위가 고정"],
            ["지역 효과가 유사한 경우 동률 발생", "여러 읍면동이 같은 상위 퍼센트로 보일 수 있음"],
            ["API 표본 51,300행", "성별·연령별 전체 모집단을 완전 수집했다고 볼 수 없음"],
            ["resident/work population 미사용", "수집은 하지만 현재 EBM 피처에 포함되지 않음"],
            ["footTrafficLevel 미사용", "요청에는 있으나 가격 공식에 들어가지 않음"],
            ["currentPrice 미사용", "이전 가격을 기준으로 점진 이동하지 않고 매번 범위에서 재계산"],
            ["예보 미사용", "내부 수집은 하지만 가격과 화면은 현재 날씨만 사용"],
            ["Quick Tunnel 의존", "로컬 PC가 꺼지거나 URL이 바뀌면 운영 중단"],
            ["중립 기준 역산", "clipping이 강한 경계에서는 요인 합과 완전히 가법적이지 않을 수 있음"],
        ], [3100, 5900]),
        paragraph("14. 고도화 제안", "Heading1"),
        bullet("가격·노출·조회·장바구니·구매·취소 데이터를 상품 단위로 적재한다."),
        bullet("가격별 구매확률과 판매수량을 예측해 가격탄력성 모델을 구축한다."),
        bullet("목표함수를 예상매출, 마감 전 판매율, 잔여폐기비용의 가중합으로 정의한다."),
        bullet("현재 정책계수는 안전 제약으로 유지하고 학습 모델이 범위 내 최적가격을 제안하도록 단계적으로 전환한다."),
        bullet("시간대별 지역수요가 필요하면 시간 단위 유동인구 이력을 확보해 별도 EBM을 재학습한다."),
        bullet("지역별 단순 절대 방문인구 외에 지역 내 계절성, 업종별 수요, 이벤트·축제 피처를 추가한다."),
        bullet("Quick Tunnel을 고정 도메인 Cloudflare Tunnel 또는 상시 AI 서버로 교체한다."),
        bullet("추천가 적용 전후의 매출·소진율을 A/B 테스트해 정책계수를 검증한다."),
        paragraph("권장 최종 목표", "Heading2"),
        code("예상매출 = 추천가격 × 예상판매수량\n\n최적화 목표 = 예상매출 최대화\n             + 마감 전 판매율 최대화\n             - 잔여재고·폐기비용 최소화"),
        paragraph("부록 A. 현재 운영 모델과 레거시 모델 구분", "Heading1"),
        table(["구분", "현재 운영", "레거시/실험"], [
            ["파일", "train_daily_flow_ebm.py", "jeju_ebm_demand.py"],
            ["데이터 단위", "읍면동×일 표본", "읍면동×월"],
            ["기간/행", f"{meta['trainingStartDate']}~{meta['trainingEndDate']}, {meta['trainingRows']:,}행", "559행 중심의 월 단위 실험"],
            ["온라인 사용", "daily_demand_profile.json을 우선 사용", "일일 아티팩트 없을 때만 fallback 가능"],
            ["주요 피처", "지역·성별·연령·달력", "lag1·lag12·이동평균·월 기상 등"],
        ], [1800, 3600, 3600]),
        paragraph("현재 서비스 설명자료에서는 레거시 월 단위 모델의 MAE, 피처 중요도, lag 피처를 현재 운영 가격모델의 성능이나 입력으로 제시하면 안 된다.", bold=True, color="9C4A00"),
        paragraph("부록 B. 주요 파일", "Heading1"),
        table(["파일", "역할"], [
            ["train_daily_flow_ebm.py", "데이터 수집, 전처리, EBM 학습, 일일 아티팩트 생성"],
            ["artifacts/daily_demand_profile.json", "온라인 지역수요 조회표"],
            ["app/demand.py", "주소·좌표 지역 판정과 수요 컨텍스트"],
            ["app/weather.py", "기상청/Open-Meteo 현재 날씨"],
            ["app/pricing.py", "가격 공식과 요인별 기여 계산"],
            ["app/main.py", "FastAPI 엔드포인트와 인증"],
            ["start_ai_tunnel.cmd/.ps1", "로컬 AI와 Cloudflare Tunnel 실행"],
            ["ProductPricingService.java", "DB 입력 구성, AI 호출, 자동가격 적용, 가격 이력"],
            ["AIRecommendationScreen.tsx", "추천가·지역수요·영향그래프 표시와 변경 감지"],
        ], [3200, 5800]),
        paragraph("— 문서 끝 —", align="center", size=18, color="888888", before=500),
    ]

    sect = (
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1080" w:right="1080" w:bottom="1080" w:left="1080" '
        'w:header="500" w:footer="500" w:gutter="0"/></w:sectPr>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body>' + "".join(body) + sect + '</w:body></w:document>'
    )


def styles_xml() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Malgun Gothic" w:hAnsi="Malgun Gothic" w:eastAsia="맑은 고딕"/><w:sz w:val="20"/><w:szCs w:val="20"/></w:rPr></w:rPrDefault></w:docDefaults>
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:after="100" w:line="320" w:lineRule="auto"/></w:pPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="320" w:after="160"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:color w:val="111111"/><w:sz w:val="30"/><w:szCs w:val="30"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="240" w:after="120"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:color w:val="B67805"/><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr></w:style>
</w:styles>'''


def write_docx() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>'''
    rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>'''
    document_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    core = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>제주 로컬타임 AI 추천가 시스템 기술 문서</dc:title><dc:creator>Localtime AI Team</dc:creator>
  <dc:subject>EBM 지역수요 및 동적 가격 산출</dc:subject><dc:description>현재 구현 코드 기준 AI 기술 문서</dc:description>
  <dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>
</cp:coreProperties>'''
    app = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>Microsoft Office Word</Application></Properties>'''

    with ZipFile(OUTPUT, "w", ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", build_document())
        zf.writestr("word/styles.xml", styles_xml())
        zf.writestr("word/_rels/document.xml.rels", document_rels)
        zf.writestr("docProps/core.xml", core)
        zf.writestr("docProps/app.xml", app)
    print(OUTPUT)


if __name__ == "__main__":
    write_docx()
