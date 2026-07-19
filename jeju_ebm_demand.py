"""
제주 유동인구 기반 수요 예측 EBM 모델 (실데이터 연동)

타깃 grain: 원 설계는 읍면동x일이었으나, 실제 확보한
'제주 지역별 방문객' 데이터가 기준연월(YYYYMM) 단위로만 제공되어
읍면동x월로 재설계함 (사용자 확인 완료). 이에 따라 lag/이동평균 등
시계열 피처도 일 단위(lag1/7, ma7) 대신 월 단위(lag1=전월,
lag12=전년동월, ma3=3개월 이동평균)로 대응함.
"""

import logging
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import holidays
from interpret.glassbox import ExplainableBoostingRegressor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("jeju_ebm")

BASE_DIR = Path(__file__).resolve().parent
ARTIFACT_DIR = BASE_DIR / "artifacts"

ARRIVALS_CSV = BASE_DIR / "입도 관광객 추이(일별).csv"
TARGET_CSV = BASE_DIR / "제주 지역별 방문객.csv"
FLOW_CSV = BASE_DIR / "제주도방문내국인유동인구추이.csv"
# 기상청 ASOS 시간자료 (data.go.kr AsosDalyInfoService는 활용신청 승인 후 게이트웨이
# 전파 지연으로 403이 지속되어, 기상자료개방포털(data.kma.go.kr)에서 받은 시간자료
# CSV로 대체 (사용자 확인 완료). 일/월 단위 집계는 이 스크립트에서 직접 수행.
WEATHER_CSV = BASE_DIR / "OBS_ASOS_TIM_20260714225401.csv"

# 읍면동이 속한 시군구를 대표 ASOS 관측소로 매핑 (제주시/서귀포시 2개 지점으로 단순화)
SIGUNGU_TO_STATION = {"제주시": 184, "서귀포시": 189}

HEAVY_RAIN_MM = 20.0
STRONG_WIND_MS = 14.0
# 제주 관광 성수기: 여름휴가(7~8월), 연말연시~겨울방학(12~2월)
PEAK_MONTHS = {1, 2, 7, 8, 12}
NIGHT_HOURS = {22, 23, 0, 24, 1, 2, 3, 4, 5}
YOUNG_AGE_BRACKETS = {"20대", "30대"}

kr_holidays = holidays.KR()


# ---------------------------------------------------------------------------
# 1. 기상청 ASOS 시간자료(로컬 CSV) -> 일 집계 -> 월 집계
# ---------------------------------------------------------------------------

def load_weather_monthly() -> pd.DataFrame:
    df = pd.read_csv(WEATHER_CSV, encoding="cp949")
    df = df.rename(columns={
        "지점": "station", "일시": "dt", "기온(°C)": "temp",
        "강수량(mm)": "rain", "풍속(m/s)": "wind",
    })
    df = df[df["station"].isin(SIGUNGU_TO_STATION.values())].copy()
    df["dt"] = pd.to_datetime(df["dt"])
    df["rain"] = df["rain"].fillna(0.0)
    station_to_sigungu = {v: k for k, v in SIGUNGU_TO_STATION.items()}
    df["시군구명"] = df["station"].map(station_to_sigungu)
    df["date"] = df["dt"].dt.date
    df["yyyymm"] = df["dt"].dt.strftime("%Y%m")

    daily = df.groupby(["시군구명", "date"]).agg(
        rain_day=("rain", "sum"), wind_day=("wind", "max"),
    ).reset_index()
    daily["yyyymm"] = pd.to_datetime(daily["date"]).dt.strftime("%Y%m")
    daily_flags = daily.groupby(["시군구명", "yyyymm"]).agg(
        heavy_rain_days=("rain_day", lambda s: int((s >= HEAVY_RAIN_MM).sum())),
        strong_wind_days=("wind_day", lambda s: int((s >= STRONG_WIND_MS).sum())),
    ).reset_index()

    monthly = df.groupby(["시군구명", "yyyymm"]).agg(
        avg_temp=("temp", "mean"), rain_mm_sum=("rain", "sum"), max_wind_max=("wind", "max"),
    ).reset_index()
    out = monthly.merge(daily_flags, on=["시군구명", "yyyymm"], how="left")
    log.info(
        "[ASOS 기상(로컬 CSV)] 시간자료 %d행 -> 월 집계 %d행, 시군구=%s, 기간=%s~%s",
        len(df), len(out), list(SIGUNGU_TO_STATION), out["yyyymm"].min(), out["yyyymm"].max(),
    )
    return out


# ---------------------------------------------------------------------------
# 2. 입도 관광객 추이(일별) -> 월 집계
# ---------------------------------------------------------------------------

def load_arrivals_monthly() -> pd.DataFrame:
    df = pd.read_csv(ARRIVALS_CSV, encoding="utf-8-sig")
    df = df.rename(columns={
        "기준일자(YYYYMMDD)": "date_raw",
        "내국인방문객수": "domestic",
        "외국인방문객수": "foreign",
        "전체방문객수": "arrivals",
    })
    df["date"] = pd.to_datetime(df["date_raw"], format="%Y%m%d")
    df["yyyymm"] = df["date"].dt.strftime("%Y%m")
    monthly = df.groupby("yyyymm").agg(
        arrivals=("arrivals", "sum"),
        arrivals_domestic=("domestic", "sum"),
        arrivals_foreign=("foreign", "sum"),
    ).reset_index()
    log.info(
        "[입도 관광객] 원본 일별 %d행 -> 월 집계 %d행, 기간=%s~%s",
        len(df), len(monthly), df["date"].min().date(), df["date"].max().date(),
    )
    return monthly


# ---------------------------------------------------------------------------
# 3. 제주 지역별 방문객 (타깃)
# ---------------------------------------------------------------------------

def load_target() -> pd.DataFrame:
    df = pd.read_csv(TARGET_CSV, encoding="utf-8-sig")
    df = df.rename(columns={
        "기준연월": "yyyymm",
        "읍면동코드": "region_code",
        "시도명": "sido",
        "시군구명": "시군구명",
        "읍면동명": "region",
        "총 방문객수 (내국인+외국인)": "visitors",
    })
    df["yyyymm"] = df["yyyymm"].astype(str)
    n_regions = df["region"].nunique()
    n_months = df["yyyymm"].nunique()
    log.info(
        "[타깃: 지역별 방문객] %d행, 읍면동 %d개 x 월 %d개, 기간=%s~%s",
        len(df), n_regions, n_months, df["yyyymm"].min(), df["yyyymm"].max(),
    )
    return df[["yyyymm", "region_code", "시군구명", "region", "visitors"]]


# ---------------------------------------------------------------------------
# 4. 내국인 유동인구 추이 -> 달력상 월(1~12)별 night/young/female 비율 계절성 프로파일
#    (원본에 읍면동 구분이 없어 전체 제주 공통 피처로 사용. 원본 이력이 2018-01~2025-04로
#    타깃 기간(~2026-01)보다 짧아 해당월 실측값 대신 다년도 평균 계절 패턴으로 대체,
#    사용자 확인 완료)
# ---------------------------------------------------------------------------

def load_flow_ratios_by_month() -> pd.DataFrame:
    df = pd.read_csv(FLOW_CSV, encoding="utf-8-sig")
    df = df.rename(columns={"base_year_month": "yyyymm", "visit_population_count": "cnt"})
    df["yyyymm"] = df["yyyymm"].astype(str)
    df["month"] = df["yyyymm"].str[4:6].astype(int)
    df["hour"] = df["time_range"].str.replace("시", "", regex=False).astype(int)

    total = df.groupby("yyyymm")["cnt"].sum().rename("total_cnt")
    night = df[df["hour"].isin(NIGHT_HOURS)].groupby("yyyymm")["cnt"].sum().rename("night_cnt")
    young = df[df["age_range"].isin(YOUNG_AGE_BRACKETS)].groupby("yyyymm")["cnt"].sum().rename("young_cnt")
    female = df[df["sex"] == "여성"].groupby("yyyymm")["cnt"].sum().rename("female_cnt")

    monthly = pd.concat([total, night, young, female], axis=1).fillna(0).reset_index()
    monthly["month"] = monthly["yyyymm"].str[4:6].astype(int)
    monthly["night_ratio"] = monthly["night_cnt"] / monthly["total_cnt"]
    monthly["young_ratio"] = monthly["young_cnt"] / monthly["total_cnt"]
    monthly["female_ratio"] = monthly["female_cnt"] / monthly["total_cnt"]

    out = monthly.groupby("month")[["night_ratio", "young_ratio", "female_ratio"]].mean().reset_index()
    log.info(
        "[유동인구 계절 프로파일] 원본 %d행(전국 시간대x연령x성별, %s~%s) -> 달력월 1~12 평균 %d행",
        len(df), df["yyyymm"].min(), df["yyyymm"].max(), len(out),
    )
    return out


# ---------------------------------------------------------------------------
# 캘린더 피처 (월 단위)
# ---------------------------------------------------------------------------

def calendar_features(yyyymm: pd.Series) -> pd.DataFrame:
    dt = pd.to_datetime(yyyymm, format="%Y%m")
    days_in_month = dt.dt.days_in_month
    month_starts = dt

    weekend_counts = []
    holiday_counts = []
    for start, periods in zip(month_starts, days_in_month):
        days = pd.date_range(start, periods=periods, freq="D")
        weekend_counts.append(int((days.weekday >= 5).sum()))
        holiday_counts.append(int(sum(d.date() in kr_holidays for d in days)))

    return pd.DataFrame({
        "month": dt.dt.month.values,
        "quarter": dt.dt.quarter.values,
        "is_peak_season": dt.dt.month.isin(PEAK_MONTHS).astype(int).values,
        "weekend_days": weekend_counts,
        "holiday_days": holiday_counts,
    })


# ---------------------------------------------------------------------------
# 데이터셋 조립
# ---------------------------------------------------------------------------

def build_dataset() -> pd.DataFrame:
    target = load_target()
    arrivals = load_arrivals_monthly()
    flow_by_month = load_flow_ratios_by_month()
    weather = load_weather_monthly()

    df = target.merge(weather, on=["yyyymm", "시군구명"], how="left")
    df = df.merge(arrivals, on="yyyymm", how="left")

    cal = calendar_features(df["yyyymm"])
    df = pd.concat([df.reset_index(drop=True), cal.reset_index(drop=True)], axis=1)
    df = df.merge(flow_by_month, on="month", how="left")

    df = df.sort_values(["region", "yyyymm"]).reset_index(drop=True)
    df["visitors_lag1"] = df.groupby("region")["visitors"].shift(1)
    df["visitors_lag12"] = df.groupby("region")["visitors"].shift(12)
    df["visitors_ma3"] = df.groupby("region")["visitors"].transform(lambda s: s.shift(1).rolling(3).mean())

    arrivals_sorted = arrivals.sort_values("yyyymm").reset_index(drop=True)
    arrivals_sorted["arrivals_lag1"] = arrivals_sorted["arrivals"].shift(1)
    arrivals_sorted["arrivals_lag12"] = arrivals_sorted["arrivals"].shift(12)
    arrivals_sorted["arrivals_ma3"] = arrivals_sorted["arrivals"].shift(1).rolling(3).mean()
    df = df.merge(
        arrivals_sorted[["yyyymm", "arrivals_lag1", "arrivals_lag12", "arrivals_ma3"]],
        on="yyyymm", how="left",
    )

    df["region"] = df["region"].astype("category")
    df = df.dropna().reset_index(drop=True)
    return df


FEATURE_COLS = [
    "region",
    "month", "quarter", "is_peak_season", "weekend_days", "holiday_days",
    "avg_temp", "rain_mm_sum", "max_wind_max", "heavy_rain_days", "strong_wind_days",
    "arrivals", "arrivals_lag1", "arrivals_lag12", "arrivals_ma3",
    "night_ratio", "young_ratio", "female_ratio",
    "visitors_lag1", "visitors_lag12", "visitors_ma3",
]
TARGET_COL = "visitors"


def time_split(df: pd.DataFrame, test_frac=0.2):
    months = sorted(df["yyyymm"].unique())
    n_test = max(1, int(round(len(months) * test_frac)))
    test_months = set(months[-n_test:])
    train = df[~df["yyyymm"].isin(test_months)].copy()
    test = df[df["yyyymm"].isin(test_months)].copy()
    log.info(
        "[시계열 분할] 전체 %d개월 중 train %d개월(%s~%s) / test %d개월(%s~%s)",
        len(months), len(months) - n_test, months[0], months[-n_test - 1],
        n_test, months[-n_test], months[-1],
    )
    return train, test


def mape(y_true, y_pred):
    y_true, y_pred = np.asarray(y_true, dtype=float), np.asarray(y_pred, dtype=float)
    mask = y_true != 0
    return float(np.mean(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask])) * 100)


def main():
    log.info("데이터셋 조립 시작")
    df = build_dataset()
    log.info("최종 데이터셋: %d행 x %d컬럼 (결측 lag/ma 제거 후)", df.shape[0], df.shape[1])

    train, test = time_split(df, test_frac=0.2)

    X_train, y_train = train[FEATURE_COLS], train[TARGET_COL]
    X_test, y_test = test[FEATURE_COLS], test[TARGET_COL]

    ebm = ExplainableBoostingRegressor(interactions=10, random_state=42)
    ebm.fit(X_train, y_train)

    # 실시간 가격 서버가 무거운 학습 파이프라인을 재실행하지 않도록 EBM 예측 수요를
    # 읍면동·달력월 백분위 아티팩트로 내보낸다.
    all_predictions = ebm.predict(df[FEATURE_COLS])
    predicted = df[["region", "month"]].copy()
    predicted["prediction"] = all_predictions
    profiles = {}
    for region, group in predicted.groupby("region", observed=True):
        monthly = group.groupby("month")["prediction"].mean().to_dict()
        ordered = sorted(monthly.values())
        profiles[str(region)] = {
            str(int(month)): (ordered.index(value) + 1) / max(1, len(ordered))
            for month, value in monthly.items()
        }
    ARTIFACT_DIR.mkdir(exist_ok=True)
    artifact_path = ARTIFACT_DIR / "demand_profile.json"
    artifact_path.write_text(json.dumps({
        "modelVersion": "jeju-ebm-demand-2026.07.1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "profiles": profiles,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("실시간 가격 서버용 EBM 수요 아티팩트 저장: %s", artifact_path)

    preds = ebm.predict(X_test)
    mae = float(np.mean(np.abs(y_test.values - preds)))
    r2 = float(1 - np.sum((y_test.values - preds) ** 2) / np.sum((y_test.values - y_test.values.mean()) ** 2))
    mape_val = mape(y_test.values, preds)

    print("\n=== 테스트 구간 평가 ===")
    print(f"MAE  : {mae:,.1f}")
    print(f"MAPE : {mape_val:.2f}%")
    print(f"R2   : {r2:.4f}")

    print("\n=== 피처 중요도 Top 12 (term_importances) ===")
    names = ebm.term_names_
    importances = ebm.term_importances()
    order = np.argsort(importances)[::-1][:12]
    for rank, idx in enumerate(order, 1):
        print(f"{rank:>2}. {names[idx]:<30} {importances[idx]:.4f}")

    print("\n=== 개별 예측 기여도 분해 (테스트 첫 행) ===")
    sample_X = X_test.iloc[[0]]
    sample_y = y_test.iloc[0]
    local_expl = ebm.explain_local(sample_X, pd.Series([sample_y]))
    local_data = local_expl.data(0)
    contrib_names = local_data["names"]
    contrib_scores = local_data["scores"]
    contrib_order = np.argsort(np.abs(contrib_scores))[::-1]
    region_val = sample_X["region"].iloc[0]
    yyyymm_val = test.iloc[0]["yyyymm"]
    print(f"대상: region={region_val}, yyyymm={yyyymm_val}, 실제값={sample_y:,.0f}, 예측값={preds[0]:,.0f}")
    for idx in contrib_order[:12]:
        print(f"  {contrib_names[idx]:<30} 기여도={contrib_scores[idx]:+.2f}")


if __name__ == "__main__":
    main()
