from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Dict, Tuple

import numpy as np

from .demand import DemandContext, RegionalDemandProvider
from .schemas import PriceExplanation, PriceOption, PricePoint, PricingRequest, PricingResponse, RegionalDemandDetails, WeatherDetails
from .weather import WeatherContext


FEATURES = [
    "demand_percentile", "remaining_ratio", "inventory_change", "elapsed_ratio", "weather",
    "weekend", "peak_hour", "outdoor",
]
class PricingEngine:
    def __init__(self, data_dir: Path):
        self.demand = RegionalDemandProvider(data_dir / "제주 지역별 방문객.csv")
        self.model_version = f"{self.demand.model_version}+kma-jeju-pricing-2026.07.5"

    @staticmethod
    def _discount_from_matrix(matrix: np.ndarray) -> np.ndarray:
        demand, remaining, inventory_change, elapsed, weather, weekend, peak, outdoor = matrix.T
        sales_velocity = np.clip(-inventory_change, -10.0, 10.0) / 10.0
        discount = (
            0.08 + (1.0 - demand) * 0.12 + remaining * 0.10
            - sales_velocity * 0.12 + elapsed * 0.15
            + weather * (0.03 + 0.07 * outdoor)
            - weekend * 0.025 - peak * 0.02
        )
        return np.clip(discount, 0.03, 0.65)

    @classmethod
    def _price_from_matrix(cls, request: PricingRequest, matrix: np.ndarray) -> np.ndarray:
        """3~65% 정책 압력을 판매자가 허용한 최대가~최소가 전체 범위에 매핑한다."""
        pressure = cls._discount_from_matrix(np.asarray(matrix, dtype=float))
        position = np.clip((pressure - 0.03) / (0.65 - 0.03), 0.0, 1.0)
        price_range = max(0, request.originalPrice - request.minimumPrice)
        return request.originalPrice - position * price_range

    @staticmethod
    def _rounded_price(request: PricingRequest, value: float) -> int:
        # 경계에서는 100원 반올림보다 판매자가 설정한 정확한 최소·최대 가격을 우선한다.
        if value <= request.minimumPrice + 1e-6:
            return request.minimumPrice
        if value >= request.originalPrice - 1e-6:
            return request.originalPrice
        rounded = int(round(value / 100.0) * 100)
        return max(request.minimumPrice, min(request.originalPrice, rounded))

    def _features(
        self, request: PricingRequest, weather: WeatherContext, at_time=None,
        demand_context: DemandContext | None = None,
    ) -> Tuple[np.ndarray, Dict[str, float]]:
        now = at_time or request.currentTime
        start = request.availableStartAt or request.currentTime
        total_window = max(1.0, (request.deadline - start).total_seconds())
        elapsed = np.clip((now - start).total_seconds() / total_window, 0.0, 1.0)
        demand_context = demand_context or self.demand.context(request.address, now)
        demand = demand_context.percentile
        outdoor = 1.0 if request.category == "TOUR_REMAINDER" or request.businessType == "RENTAL_MOBILITY" else 0.0
        rain_risk = weather.precipitation
        wind_risk = weather.wind_speed
        temperature_risk = abs(weather.temperature - 20.0)
        weather_score = float(np.clip(
            min(rain_risk, 20.0) / 20.0 * 0.5
            + max(wind_risk - 5.0, 0.0) / 15.0 * 0.3
            + min(temperature_risk, 15.0) / 15.0 * 0.2,
            0.0, 1.0,
        ))
        values = {
            "demand_percentile": demand,
            "remaining_ratio": request.remainingQty / max(1, request.totalQty),
            "inventory_change": float(request.inventoryChange),
            "elapsed_ratio": float(elapsed),
            "weather": weather_score,
            "weekend": 1.0 if now.weekday() >= 5 else 0.0,
            "peak_hour": 1.0 if 11 <= now.hour <= 13 or 17 <= now.hour <= 20 else 0.0,
            "outdoor": outdoor,
        }
        return np.array([[values[name] for name in FEATURES]], dtype=float), values

    def _policy_contributions(
        self, request: PricingRequest, sample: np.ndarray, values: Dict[str, float],
        weather: WeatherContext, demand_context: DemandContext,
    ) -> list[PriceExplanation]:
        """해당 그룹을 중립값으로 되돌렸을 때와의 실제 가격 차이를 계산한다."""
        actual_price = float(self._price_from_matrix(request, sample)[0])

        def impact(neutral_values: dict[int, float]) -> float:
            neutral = sample.copy()
            for index, value in neutral_values.items():
                neutral[0, index] = value
            return actual_price - float(self._price_from_matrix(request, neutral)[0])

        demand_impact = impact({0: 0.5})
        inventory_impact = impact({1: 0.5, 2: 0.0})
        time_impact = impact({3: 0.5, 5: 0.0, 6: 0.0})
        weather_impact = impact({4: 0.0})

        sold_quantity = max(0, request.totalQty - request.remainingQty)
        items = [
            ("elapsed_ratio", "판매 경과 시간", values["elapsed_ratio"],
             f"판매 구간 {values['elapsed_ratio'] * 100:.1f}% 경과", time_impact),
            ("inventory_state", "잔여 수량 및 판매량", values["remaining_ratio"],
             f"잔여 {request.remainingQty}개 · 판매 {sold_quantity}개", inventory_impact),
            ("weather", "날씨", values["weather"], weather.summary, weather_impact),
            ("demand_percentile", "지역 수요", values["demand_percentile"], None, demand_impact),
        ]
        return [
            PriceExplanation(
                feature=feature, label=label, value=round(float(value), 3), displayValue=display,
                impact=round(float(impact), 1),
                direction="UP" if impact > 0 else "DOWN" if impact < 0 else "NEUTRAL",
            )
            for feature, label, value, display, impact in items
        ]

    def recommend(
        self, request: PricingRequest, weather: WeatherContext, resolved_region: str | None = None,
    ) -> PricingResponse:
        demand_address = resolved_region or request.address
        demand_context = self.demand.context(demand_address, request.currentTime)
        sample, values = self._features(request, weather, demand_context=demand_context)
        raw_price = float(self._price_from_matrix(request, sample)[0])
        recommended = self._rounded_price(request, raw_price)
        minutes_left = max(0, int((request.deadline - request.currentTime).total_seconds() // 60))
        method = "POLICY_CONTRIBUTION"
        explanations = self._policy_contributions(request, sample, values, weather, demand_context)
        timeline = []
        for offset in (0, 10, 20, 30):
            point_time = min(request.deadline, request.currentTime + timedelta(minutes=offset))
            point_demand = self.demand.context(demand_address, point_time)
            point_sample, _ = self._features(request, weather, point_time, point_demand)
            point_raw_price = float(self._price_from_matrix(request, point_sample)[0])
            point_price = self._rounded_price(request, point_raw_price)
            timeline.append(PricePoint(time=point_time.strftime("%H:%M"), price=point_price))
            if point_time >= request.deadline:
                break
        confidence = 0.86 if demand_context.source == "DAILY_FLOW_EBM_3Y" and request.totalQty > 0 else 0.72
        options = self._price_options(request, recommended, values, explanations)
        return PricingResponse(
            currentPrice=recommended,
            discountPct=round((1 - recommended / request.originalPrice) * 100, 1),
            minutesLeft=minutes_left,
            priceTimeline=timeline,
            confidence=confidence,
            modelVersion=self.model_version,
            reason="",
            explanationMethod=method,
            explanations=explanations,
            weatherSummary=weather.summary,
            weather=WeatherDetails(
                currentTemperature=round(weather.temperature, 1),
                currentPrecipitation=round(weather.precipitation, 1),
                currentWindSpeed=round(weather.wind_speed, 1),
                forecastTemperature=None if weather.forecast_temperature is None else round(weather.forecast_temperature, 1),
                forecastPrecipitation=round(weather.forecast_precipitation, 1),
                forecastWindSpeed=round(weather.forecast_wind_speed, 1),
                source=weather.source,
                observedAt=weather.observed_at,
            ),
            regionalDemand=RegionalDemandDetails(
                region=demand_context.region,
                percentile=round(demand_context.percentile, 4),
                predictedVisitPopulation=demand_context.predicted_visit_population,
                source=demand_context.source,
                basisDate=demand_context.basis_date,
                trainingStartDate=demand_context.training_start_date,
                trainingEndDate=demand_context.training_end_date,
            ),
            priceOptions=options,
        )

    def _price_options(
        self,
        request: PricingRequest,
        balanced_price: int,
        values: Dict[str, float],
        explanations: list[PriceExplanation],
    ) -> list[PriceOption]:
        """균형 추천가를 중심으로 목적별 가격안과 비확률성 판매 지수를 만든다."""
        upper_gap = max(0, request.originalPrice - balanced_price)
        lower_gap = max(0, balanced_price - request.minimumPrice)
        profit_price = self._rounded_price(request, balanced_price + upper_gap * 0.45)
        sell_price = self._rounded_price(request, balanced_price - lower_gap * 0.45)

        # 판매 가능성은 학습된 확률이 아니라 가격·수요·재고·시간 압력을 합친 0~100 추정 지수다.
        urgency = float(values["elapsed_ratio"])
        inventory = float(values["remaining_ratio"])
        demand = float(values["demand_percentile"])

        def likelihood(price: int) -> int:
            range_size = max(1, request.originalPrice - request.minimumPrice)
            price_discount_position = (request.originalPrice - price) / range_size
            score = 24 + demand * 25 + inventory * 16 + urgency * 18 + price_discount_position * 27
            return int(round(np.clip(score, 5, 95)))

        common_factors = [
            item.displayValue or item.label
            for item in sorted(explanations, key=lambda item: abs(item.impact), reverse=True)[:2]
        ]
        definitions = [
            ("PROFIT", "수익 우선", profit_price, "가격을 높여 건당 매출 확보를 우선"),
            ("BALANCED", "균형", balanced_price, "판매 가능성과 건당 매출의 균형"),
            ("SELL_THROUGH", "판매 우선", sell_price, "마감 전 잔여 수량 소진을 우선"),
        ]
        return [
            PriceOption(
                purpose=purpose,
                label=label,
                price=price,
                discountPct=round((1 - price / request.originalPrice) * 100, 1),
                salesLikelihoodIndex=likelihood(price),
                expectedRevenue=int(round(price * request.remainingQty * likelihood(price) / 100.0)),
                majorFactors=[purpose_factor, *common_factors],
            )
            for purpose, label, price, purpose_factor in definitions
        ]
