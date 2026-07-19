from pathlib import Path
from datetime import datetime
import os
import secrets
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from dotenv import load_dotenv

from .pricing import PricingEngine
from .schemas import PricingRequest, PricingResponse, StrategyRequest, StrategyResponse
from .weather import current_weather

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")
engine = PricingEngine(BASE_DIR)
app = FastAPI(title="Jeju Localtime AI Pricing", version="1.0.0")


def verify_service_key(x_ai_service_key: Annotated[str | None, Header()] = None):
    expected = os.getenv("AI_SERVICE_API_KEY", "")
    if expected and (not x_ai_service_key or not secrets.compare_digest(expected, x_ai_service_key)):
        raise HTTPException(status_code=401, detail="Invalid AI service key")


@app.get("/health")
def health():
    return {"status": "ok", "modelVersion": engine.model_version}


@app.post("/v1/pricing/recommendation", response_model=PricingResponse, dependencies=[Depends(verify_service_key)])
async def pricing_recommendation(request: PricingRequest):
    weather = await current_weather(request.latitude, request.longitude)
    region = await engine.demand.resolve_region(request.address, request.latitude, request.longitude)
    return engine.recommend(request, weather, resolved_region=region)


@app.post("/v1/pricing/strategy", response_model=StrategyResponse, dependencies=[Depends(verify_service_key)])
def pricing_strategy(request: StrategyRequest):
    now = datetime.now(request.deadline.tzinfo)
    minutes = max(0, int((request.deadline - now).total_seconds() // 60))
    if minutes <= 60:
        message = "마감이 임박했습니다. AI 추천가를 적용하고 잔여 수량 소진을 우선하세요."
    elif request.remainingQty >= 5:
        message = "잔여 수량이 많습니다. 10분 자동 가격을 켜고 판매 속도를 확인하세요."
    else:
        message = "현재 판매 흐름을 유지하면서 다음 자동 추천 주기를 확인하세요."
    return StrategyResponse(message=message)
