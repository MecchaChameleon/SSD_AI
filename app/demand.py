from __future__ import annotations

import csv
import json
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Dict, List

import holidays
import httpx


kr_holidays = holidays.KR()

REGION_ALIASES = {
    "일도일동": "일도1동", "일도이동": "일도2동",
    "이도일동": "이도1동", "이도이동": "이도2동",
    "삼도일동": "삼도1동", "삼도이동": "삼도2동",
    "용담일동": "용담1동", "용담이동": "용담2동",
}


@dataclass
class DemandContext:
    percentile: float
    predicted_visit_population: int | None
    region: str | None
    source: str
    basis_date: str
    training_start_date: str | None = None
    training_end_date: str | None = None


class RegionalDemandProvider:
    """3년 일별 EBM 아티팩트에서 현재 날짜·요일·공휴일·읍면동 수요를 조회한다."""

    def __init__(self, csv_path: Path):
        self._daily_profiles: Dict[str, Dict[str, float]] = {}
        self._daily_predictions: Dict[str, Dict[str, int]] = {}
        self._monthly_profiles: Dict[str, Dict[int, float]] = {}
        self._regions: List[str] = []
        self.training_start_date: str | None = None
        self.training_end_date: str | None = None
        self.model_version = "historical-demand-fallback"
        daily_path = csv_path.parent / "artifacts" / "daily_demand_profile.json"
        legacy_path = csv_path.parent / "artifacts" / "demand_profile.json"
        if daily_path.exists():
            self._load_daily_artifact(daily_path)
        elif legacy_path.exists():
            self._load_legacy_artifact(legacy_path)
        else:
            self._load_csv(csv_path)

    def _load_daily_artifact(self, path: Path) -> None:
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.model_version = payload.get("modelVersion", "jeju-daily-flow-ebm")
        self.training_start_date = payload.get("trainingStartDate")
        self.training_end_date = payload.get("trainingEndDate")
        self._daily_profiles = payload.get("profiles", {})
        self._daily_predictions = payload.get("predictedVisitPopulation", {})
        self._regions = sorted(self._daily_profiles, key=len, reverse=True)

    def _load_legacy_artifact(self, path: Path) -> None:
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.model_version = payload.get("modelVersion", "jeju-ebm-demand")
        self._monthly_profiles = {
            region: {int(month): float(value) for month, value in months.items()}
            for region, months in payload.get("profiles", {}).items()
        }
        self._regions = sorted(self._monthly_profiles, key=len, reverse=True)

    def _load_csv(self, csv_path: Path) -> None:
        grouped = defaultdict(lambda: defaultdict(list))
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                region = row.get("읍면동명", "").strip()
                yyyymm = str(row.get("기준연월", ""))
                visitors = float(row.get("총 방문객수 (내국인+외국인)", 0) or 0)
                if region and len(yyyymm) >= 6:
                    grouped[region][int(yyyymm[4:6])].append(visitors)
        for region, months in grouped.items():
            averages = {month: mean(values) for month, values in months.items()}
            ordered = sorted(averages.values())
            self._monthly_profiles[region] = {
                month: (ordered.index(value) + 1) / max(1, len(ordered))
                for month, value in averages.items()
            }
        self._regions = sorted(self._monthly_profiles, key=len, reverse=True)

    def _region(self, address: str | None) -> str | None:
        address = address or ""
        matched = next((candidate for candidate in self._regions if candidate in address), None)
        if matched:
            return matched
        return next(
            (region for alias, region in REGION_ALIASES.items() if alias in address and region in self._daily_profiles),
            None,
        )

    def _region_from_kakao_documents(self, documents: list[dict]) -> str | None:
        """카카오 좌표→행정구역 응답에서 학습 데이터에 존재하는 읍면동을 고른다."""
        ordered = sorted(documents, key=lambda item: item.get("region_type") != "H")
        for item in ordered:
            for field in ("region_3depth_h_name", "region_3depth_name"):
                matched = self._region(item.get(field))
                if matched:
                    return matched
        return None

    async def resolve_region(
        self, address: str | None, latitude: float | None, longitude: float | None,
    ) -> str | None:
        """주소 우선, 도로명 주소라 읍면동이 없으면 좌표 역지오코딩으로 보완한다."""
        matched = self._region(address)
        if matched or latitude is None or longitude is None:
            return matched
        api_key = os.getenv("KAKAO_REST_API_KEY") or os.getenv("EXPO_PUBLIC_KAKAO_REST_API_KEY")
        if not api_key:
            return None
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(
                    "https://dapi.kakao.com/v2/local/geo/coord2regioncode.json",
                    params={"x": longitude, "y": latitude},
                    headers={"Authorization": f"KakaoAK {api_key}"},
                )
                response.raise_for_status()
            return self._region_from_kakao_documents(response.json().get("documents", []))
        except (httpx.HTTPError, ValueError, TypeError):
            return None

    def _daily_context(self, region: str | None, current_time: datetime, basis_date: str) -> DemandContext:
        is_holiday = int(current_time.date() in kr_holidays)
        key = f"{current_time.month:02d}{current_time.day:02d}|{current_time.weekday()}|{is_holiday}"
        if region:
            percentile = self._daily_profiles.get(region, {}).get(key)
            prediction = self._daily_predictions.get(region, {}).get(key)
            if percentile is not None:
                return DemandContext(
                    percentile=float(percentile),
                    predicted_visit_population=int(prediction) if prediction is not None else None,
                    region=region,
                    source="DAILY_FLOW_EBM_3Y",
                    basis_date=basis_date,
                    training_start_date=self.training_start_date,
                    training_end_date=self.training_end_date,
                )

        # 기존 도로명 주소처럼 읍면동을 복원할 수 없는 데이터도 0.5 상수로 버리지 않고,
        # 같은 날짜·요일·공휴일 조건의 제주 전체 과거 EBM 결과를 사용한다.
        percentiles = [profile[key] for profile in self._daily_profiles.values() if key in profile]
        predictions = [profile[key] for profile in self._daily_predictions.values() if key in profile]
        return DemandContext(
            percentile=float(mean(percentiles)) if percentiles else 0.5,
            predicted_visit_population=sum(int(value) for value in predictions) if predictions else None,
            region="제주 전체",
            source="DAILY_FLOW_EBM_3Y_AGGREGATE",
            basis_date=basis_date,
            training_start_date=self.training_start_date,
            training_end_date=self.training_end_date,
        )

    def context(self, address: str | None, current_time: datetime) -> DemandContext:
        region = self._region(address)
        basis_date = current_time.date().isoformat()
        if self._daily_profiles:
            return self._daily_context(region, current_time, basis_date)
        percentile = float(self._monthly_profiles.get(region, {}).get(current_time.month, 0.5)) if region else 0.5
        return DemandContext(percentile, None, region, "MONTHLY_HISTORICAL_FALLBACK", basis_date)

    def percentile(self, address: str | None, month: int) -> float:
        # 이전 호출부 호환용. 일별 추론은 context()를 사용한다.
        region = self._region(address)
        return float(self._monthly_profiles.get(region, {}).get(month, 0.5)) if region else 0.5
