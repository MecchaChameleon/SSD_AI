from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.pricing import PricingEngine
from app.schemas import PricingRequest
from app.weather import WeatherContext, is_jeju, to_kma_grid


def _request() -> PricingRequest:
    now = datetime.now(timezone(timedelta(hours=9)))
    return PricingRequest(
        productId=1,
        businessType="RESTAURANT",
        category="SAME_DAY_INVENTORY",
        originalPrice=20000,
        minimumPrice=10000,
        currentPrice=18000,
        totalQty=20,
        remainingQty=15,
        inventoryChange=-2,
        availableStartAt=now - timedelta(hours=1),
        deadline=now + timedelta(hours=2),
        currentTime=now,
        address="제주특별자치도 제주시 애월읍",
        latitude=33.45,
        longitude=126.31,
    )


def test_jeju_coordinate_converts_to_expected_kma_area():
    assert is_jeju(33.50, 126.53)
    assert not is_jeju(37.57, 126.98)
    nx, ny = to_kma_grid(33.50, 126.53)
    assert 50 <= nx <= 55
    assert 35 <= ny <= 40


def test_daily_ebm_uses_current_date_region_and_weather_forecast():
    engine = PricingEngine(Path(__file__).resolve().parents[1])
    request = _request()
    weather = WeatherContext(
        temperature=22,
        precipitation=0,
        wind_speed=2,
        forecast_temperature=18,
        forecast_precipitation=10,
        forecast_wind_speed=8,
        summary="test",
    )
    demand = engine.demand.context(request.address, request.currentTime)
    assert demand.source == "DAILY_FLOW_EBM_3Y"
    assert demand.region == "애월읍"
    assert demand.basis_date == request.currentTime.date().isoformat()
    assert demand.predicted_visit_population is not None
    _, features = engine._features(request, weather, demand_context=demand)
    assert features["demand_percentile"] == demand.percentile
    assert features["weather"] > 0
    result = engine.recommend(request, weather)
    assert result.weather.currentTemperature == 22.0
    assert result.weather.forecastPrecipitation == 10.0
    explanation = next(item for item in result.explanations if item.feature == "demand_percentile")
    assert explanation.displayValue is None
    assert len(result.explanations) == 4
    assert result.explanationMethod == "POLICY_CONTRIBUTION"
    assert result.regionalDemand.source == "DAILY_FLOW_EBM_3Y"
    assert result.regionalDemand.trainingStartDate == "2023-06-02"


def test_daily_ebm_uses_jeju_aggregate_instead_of_constant_half_when_region_is_missing():
    engine = PricingEngine(Path(__file__).resolve().parents[1])
    request = _request().model_copy(update={"address": "제주특별자치도 제주시 중앙로 12"})

    demand = engine.demand.context(request.address, request.currentTime)

    assert demand.source == "DAILY_FLOW_EBM_3Y_AGGREGATE"
    assert demand.region == "제주 전체"
    assert demand.predicted_visit_population is not None
    assert demand.percentile != 0.5


def test_korean_legal_dong_name_maps_to_daily_ebm_region():
    engine = PricingEngine(Path(__file__).resolve().parents[1])
    request = _request().model_copy(update={"address": "제주특별자치도 제주시 이도이동 1176-1"})

    demand = engine.demand.context(request.address, request.currentTime)

    assert demand.source == "DAILY_FLOW_EBM_3Y"
    assert demand.region == "이도2동"


def test_kakao_admin_dong_response_maps_road_address_to_daily_ebm_region():
    engine = PricingEngine(Path(__file__).resolve().parents[1])

    region = engine.demand._region_from_kakao_documents([
        {"region_type": "B", "region_3depth_name": "노형동"},
        {"region_type": "H", "region_3depth_h_name": "노형동"},
    ])

    assert region == "노형동"
