from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from app.pricing import PricingEngine
from app.schemas import PricingRequest
from app.weather import WeatherContext


def test_recommendation_is_bounded_and_explained():
    engine = PricingEngine(Path(__file__).resolve().parents[1])
    now = datetime.now(timezone.utc)
    request = PricingRequest(
        productId=1, businessType="RESTAURANT", category="SAME_DAY_INVENTORY",
        originalPrice=20000, minimumPrice=10000, currentPrice=18000,
        totalQty=20, remainingQty=15, inventoryChange=-3, availableStartAt=now - timedelta(hours=2),
        deadline=now + timedelta(hours=2), currentTime=now,
        address="제주시 애월읍", latitude=33.4, longitude=126.3,
    )
    result = engine.recommend(request, WeatherContext(temperature=18, precipitation=3, wind_speed=5, summary="test"))
    assert request.minimumPrice <= result.currentPrice <= request.originalPrice
    assert result.explanations
    assert result.priceTimeline
    features = {item.feature for item in result.explanations}
    assert features == {"elapsed_ratio", "inventory_state", "weather", "demand_percentile"}
    assert "weather" in features
    assert not {"rain_mm", "wind_speed", "temperature_gap"} & features
    assert result.explanationMethod == "POLICY_CONTRIBUTION"
    inventory = next(item for item in result.explanations if item.feature == "inventory_state")
    assert inventory.label == "잔여 수량 및 판매량"
    assert "잔여 15개 · 판매 5개" in inventory.displayValue
    demand = next(item for item in result.explanations if item.feature == "demand_percentile")
    assert demand.displayValue is None
    assert result.reason == ""


def test_policy_discount_reaches_configured_floor_and_ceiling():
    maximum_discount = np.array([[0.0, 1.0, 10.0, 1.0, 1.0, 0.0, 0.0, 1.0]])
    minimum_discount = np.array([[1.0, 0.0, -10.0, 0.0, 0.0, 1.0, 1.0, 0.0]])
    assert PricingEngine._discount_from_matrix(maximum_discount)[0] == 0.65
    assert PricingEngine._discount_from_matrix(minimum_discount)[0] == 0.03

    now = datetime.now(timezone.utc)
    request = PricingRequest(
        productId=2, originalPrice=19870, minimumPrice=10350, currentPrice=15000,
        totalQty=10, remainingQty=10, deadline=now + timedelta(hours=1), currentTime=now,
    )
    assert PricingEngine._price_from_matrix(request, maximum_discount)[0] == request.minimumPrice
    assert PricingEngine._price_from_matrix(request, minimum_discount)[0] == request.originalPrice
    assert PricingEngine._rounded_price(request, request.minimumPrice) == request.minimumPrice
    assert PricingEngine._rounded_price(request, request.originalPrice) == request.originalPrice
