"""제주데이터허브 3년 일별 읍면동 표본으로 캘린더 기반 수요 EBM을 학습한다."""

from __future__ import annotations

import asyncio
import argparse
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import holidays
import httpx
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from interpret.glassbox import ExplainableBoostingRegressor


BASE_DIR = Path(__file__).resolve().parent
RAW_PATH = BASE_DIR / "data" / "raw" / "jeju_daily_flow_3y.csv"
ARTIFACT_PATH = BASE_DIR / "artifacts" / "daily_demand_profile.json"
DATA_KEY = "Daaa1t3at3tt8a8DD3t55538t35Dab1t"
KST = timezone(timedelta(hours=9))
kr_holidays = holidays.KR()
log = logging.getLogger("daily_flow_ebm")

FEATURES = [
    "region", "city", "gender", "age_group", "month", "day", "day_of_year",
    "day_of_week", "is_weekend", "is_holiday",
]
CATEGORICAL = ["region", "city", "gender", "age_group"]


def _api_url() -> str:
    key = os.getenv("JEJU_DATA_HUB_KEY", "").strip()
    if not key:
        raise RuntimeError("JEJU_DATA_HUB_KEY is not configured")
    return f"https://open.jejudatahub.net/api/proxy/{DATA_KEY}/{key}"


async def _fetch(client: httpx.AsyncClient, start: date, end: date, semaphore: asyncio.Semaphore) -> list[dict]:
    params = {"startDate": start.strftime("%Y%m%d"), "endDate": end.strftime("%Y%m%d"), "limit": 100}
    async with semaphore:
        for attempt in range(3):
            try:
                response = await client.get(_api_url(), params=params)
                response.raise_for_status()
                return response.json().get("data", [])
            except Exception:
                if attempt == 2:
                    raise
                await asyncio.sleep(0.5 * (attempt + 1))
    return []


async def _latest_available_date(client: httpx.AsyncClient) -> date:
    cursor = datetime.now(KST).date().replace(day=1)
    for _ in range(18):
        next_month = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        end = next_month - timedelta(days=1)
        response = await client.get(_api_url(), params={
            "startDate": cursor.strftime("%Y%m%d"), "endDate": end.strftime("%Y%m%d"), "limit": 1,
        })
        response.raise_for_status()
        if int(response.json().get("totCnt", 0) or 0) > 0:
            # 월말부터 역순으로 실제 데이터가 있는 마지막 날을 찾는다.
            for offset in range(end.day):
                candidate = end - timedelta(days=offset)
                check = await client.get(_api_url(), params={
                    "startDate": candidate.strftime("%Y%m%d"), "endDate": candidate.strftime("%Y%m%d"), "limit": 1,
                })
                if int(check.json().get("totCnt", 0) or 0) > 0:
                    return candidate
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    raise RuntimeError("최근 18개월 안에서 제주 유동인구 데이터를 찾지 못했습니다")


async def download_three_year_sample() -> pd.DataFrame:
    async with httpx.AsyncClient(timeout=30.0) as client:
        latest = await _latest_available_date(client)
        first = latest - timedelta(days=3 * 365 - 1)
        windows = []
        cursor = first
        while cursor <= latest:
            end = min(latest, cursor + timedelta(days=2))
            windows.append((cursor, end))
            cursor = end + timedelta(days=1)
        semaphore = asyncio.Semaphore(8)
        chunks = await asyncio.gather(*(_fetch(client, start, end, semaphore) for start, end in windows))

    rows = [row for chunk in chunks for row in chunk]
    df = _normalize_rows(rows)
    return _save_raw(df, first, latest)


def _normalize_rows(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows).rename(columns={
        "baseDate": "date", "city": "city", "emd": "region", "gender": "gender",
        "ageGroup": "age_group", "resdPop": "resident_pop", "workPop": "worker_pop",
        "visitPop": "visit_pop",
    })
    required = ["date", "city", "region", "gender", "age_group", "resident_pop", "worker_pop", "visit_pop"]
    df = df[required].drop_duplicates().copy()
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df = df.sort_values(["date", "region", "gender", "age_group"]).reset_index(drop=True)
    return df


def _save_raw(df: pd.DataFrame, first: date, latest: date) -> pd.DataFrame:
    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(RAW_PATH, index=False, encoding="utf-8-sig")
    coverage = df["date"].nunique() / max(1, (latest - first).days + 1)
    log.info("수집 %d행, %d개 날짜, 날짜 커버리지 %.1f%%, 기간 %s~%s", len(df), df["date"].nunique(), coverage * 100, first, latest)
    return df


async def complete_missing_dates() -> pd.DataFrame:
    existing = pd.read_csv(RAW_PATH, encoding="utf-8-sig", parse_dates=["date"])
    first, latest = existing["date"].min().date(), existing["date"].max().date()
    present = set(existing["date"].dt.date)
    missing = [first + timedelta(days=offset) for offset in range((latest - first).days + 1)
               if first + timedelta(days=offset) not in present]
    log.info("누락 날짜 %d일 보충", len(missing))
    async with httpx.AsyncClient(timeout=30.0) as client:
        semaphore = asyncio.Semaphore(8)
        chunks = await asyncio.gather(*(_fetch(client, value, value, semaphore) for value in missing))
    additional = _normalize_rows([row for chunk in chunks for row in chunk])
    combined = pd.concat([existing, additional], ignore_index=True).drop_duplicates()
    combined = combined.sort_values(["date", "region", "gender", "age_group"]).reset_index(drop=True)
    return _save_raw(combined, first, latest)


def add_calendar_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    dt = pd.to_datetime(out["date"])
    out["month"] = dt.dt.month
    out["day"] = dt.dt.day
    out["day_of_year"] = dt.dt.dayofyear
    out["day_of_week"] = dt.dt.weekday
    out["is_weekend"] = (dt.dt.weekday >= 5).astype(int)
    out["is_holiday"] = [int(value.date() in kr_holidays) for value in dt]
    for column in CATEGORICAL:
        out[column] = out[column].astype("category")
    return out


def _prediction_grid(model: ExplainableBoostingRegressor, source: pd.DataFrame) -> tuple[dict, dict]:
    pairs = source[["gender", "age_group"]].drop_duplicates().astype(str).to_records(index=False)
    region_city = source[["region", "city"]].drop_duplicates().astype(str)
    profiles: dict[str, dict[str, float]] = {}
    predictions: dict[str, dict[str, int]] = {}
    calendar_rows = []
    leap_start = date(2024, 1, 1)
    for offset in range(366):
        value = leap_start + timedelta(days=offset)
        for weekday in range(7):
            for holiday in (0, 1):
                calendar_rows.append((value.month, value.day, value.timetuple().tm_yday, weekday, int(weekday >= 5), holiday))

    for row in region_city.itertuples(index=False):
        records = []
        keys = []
        for month, day, day_of_year, weekday, weekend, holiday in calendar_rows:
            key = f"{month:02d}{day:02d}|{weekday}|{holiday}"
            for gender, age_group in pairs:
                records.append({
                    "region": row.region, "city": row.city, "gender": gender, "age_group": age_group,
                    "month": month, "day": day, "day_of_year": day_of_year, "day_of_week": weekday,
                    "is_weekend": weekend, "is_holiday": holiday,
                })
                keys.append(key)
        frame = pd.DataFrame(records)
        for column in CATEGORICAL:
            frame[column] = pd.Categorical(frame[column], categories=source[column].cat.categories)
        frame["prediction"] = np.clip(model.predict(frame[FEATURES]), 0, None)
        frame["key"] = keys
        totals = frame.groupby("key", observed=True)["prediction"].sum()
        percentile = totals.rank(method="average", pct=True)
        profiles[str(row.region)] = {key: round(float(value), 4) for key, value in percentile.items()}
        predictions[str(row.region)] = {key: int(round(float(value))) for key, value in totals.items()}
    return profiles, predictions


def train(df: pd.DataFrame) -> dict:
    dataset = add_calendar_features(df)
    cutoff = dataset["date"].max() - pd.Timedelta(days=90)
    train_df, test_df = dataset[dataset["date"] < cutoff], dataset[dataset["date"] >= cutoff]
    model = ExplainableBoostingRegressor(interactions=8, random_state=42, max_rounds=3000)
    model.fit(train_df[FEATURES], train_df["visit_pop"])
    prediction = model.predict(test_df[FEATURES])
    mae = float(np.mean(np.abs(test_df["visit_pop"].to_numpy() - prediction)))
    profiles, predicted_visits = _prediction_grid(model, dataset)
    payload = {
        "modelVersion": "jeju-daily-flow-ebm-2026.07.1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "trainingStartDate": dataset["date"].min().date().isoformat(),
        "trainingEndDate": dataset["date"].max().date().isoformat(),
        "trainingRows": len(dataset),
        "testMae": round(mae, 2),
        "features": FEATURES,
        "profiles": profiles,
        "predictedVisitPopulation": predicted_visits,
    }
    ARTIFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT_PATH.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return payload


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv(BASE_DIR / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--complete-missing", action="store_true")
    args = parser.parse_args()
    df = asyncio.run(complete_missing_dates() if args.complete_missing else download_three_year_sample())
    result = train(df)
    print(json.dumps({key: result[key] for key in (
        "modelVersion", "trainingStartDate", "trainingEndDate", "trainingRows", "testMae"
    )}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
