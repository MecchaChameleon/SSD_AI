from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class PricingRequest(BaseModel):
    productId: int
    sellerProfileId: Optional[int] = None
    businessType: Optional[str] = None
    category: Optional[str] = None
    originalPrice: int = Field(gt=0)
    minimumPrice: int = Field(gt=0)
    currentPrice: int = Field(gt=0)
    totalQty: int = Field(ge=0)
    remainingQty: int = Field(ge=0)
    inventoryChange: int = 0
    availableStartAt: Optional[datetime] = None
    deadline: datetime
    currentTime: datetime
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    footTrafficLevel: Optional[str] = None


class PricePoint(BaseModel):
    time: str
    price: int


class PriceExplanation(BaseModel):
    feature: str
    label: str
    value: float
    displayValue: Optional[str] = None
    impact: float
    direction: str


class WeatherDetails(BaseModel):
    currentTemperature: float
    currentPrecipitation: float
    currentWindSpeed: float
    forecastTemperature: Optional[float] = None
    forecastPrecipitation: float
    forecastWindSpeed: float
    source: str
    observedAt: Optional[str] = None


class RegionalDemandDetails(BaseModel):
    region: Optional[str] = None
    percentile: float
    predictedVisitPopulation: Optional[int] = None
    source: str
    basisDate: str
    trainingStartDate: Optional[str] = None
    trainingEndDate: Optional[str] = None


class PriceOption(BaseModel):
    purpose: str
    label: str
    price: int
    discountPct: float
    salesLikelihoodIndex: int
    expectedRevenue: int
    majorFactors: List[str]


class PricingResponse(BaseModel):
    currentPrice: int
    discountPct: float
    minutesLeft: int
    priceTimeline: List[PricePoint]
    confidence: float
    modelVersion: str
    reason: str
    explanationMethod: str
    explanations: List[PriceExplanation]
    weatherSummary: str
    weather: WeatherDetails
    regionalDemand: RegionalDemandDetails
    priceOptions: List[PriceOption]


class StrategyRequest(BaseModel):
    productId: int
    category: Optional[str] = None
    remainingQty: int = 0
    deadline: datetime
    footTrafficLevel: Optional[str] = None


class StrategyResponse(BaseModel):
    message: str
