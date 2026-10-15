"""합성 페르소나 학습구간에서 가격 규칙 계수를 4단계로 보정한다.

평가구간은 계수 선택에 사용하지 않는다. 선택된 계수와 기존 계수를 동일한
시간순 홀드아웃에서 비교하고 artifacts/persona_rule_calibration.json에 저장한다.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass
from datetime import datetime, time
from pathlib import Path

import numpy as np

from app.pricing import PricingEngine
from simulation.backtest import KST, BacktestConfig, ProductScenario, _purchase_probability, generate_scenarios


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts" / "persona_rule_calibration.json"


@dataclass(frozen=True)
class Weights:
    intercept: float = 0.08
    low_demand: float = 0.12
    remaining: float = 0.10
    sales_velocity: float = 0.12
    elapsed: float = 0.15
    weather: float = 0.03
    outdoor_weather: float = 0.07
    weekend: float = 0.025
    peak_hour: float = 0.02


CURRENT = Weights()
NAMES = tuple(asdict(CURRENT))
BOUNDS = {
    "intercept": (0.03, 0.14),
    "low_demand": (0.04, 0.24),
    "remaining": (0.03, 0.22),
    "sales_velocity": (0.04, 0.24),
    "elapsed": (0.05, 0.30),
    "weather": (0.00, 0.10),
    "outdoor_weather": (0.00, 0.16),
    "weekend": (0.00, 0.08),
    "peak_hour": (0.00, 0.07),
}


def rounded_price(
    scenario: ProductScenario, remaining: int, step: int, total_steps: int,
    demand_percentile: float, w: Weights,
) -> int:
    remaining_ratio = remaining / max(1, scenario.inventory)
    # ExistingRulePolicy와 동일하게 10:00~22:00의 12시간 판매창을 사용한다.
    elapsed_minutes = min(step * 10, 12 * 60)
    elapsed = elapsed_minutes / (12 * 60)
    inventory_change = remaining - scenario.inventory
    sales_velocity = np.clip(-inventory_change, -10.0, 10.0) / 10.0
    hour = 10 + elapsed_minutes // 60
    peak = 1.0 if 11 <= hour <= 13 or 17 <= hour <= 20 else 0.0
    # ExistingRulePolicy가 만드는 WeatherContext를 PricingEngine._features가 변환하는 식과 동일하다.
    risk = scenario.weather_risk
    precipitation = risk * 12
    wind_speed = 3 + risk * 8
    temperature_risk = risk * 7
    weather_score = float(np.clip(
        min(precipitation, 20.0) / 20.0 * 0.5
        + max(wind_speed - 5.0, 0.0) / 15.0 * 0.3
        + min(temperature_risk, 15.0) / 15.0 * 0.2,
        0.0, 1.0,
    ))
    pressure = (
        w.intercept
        + (1.0 - demand_percentile) * w.low_demand
        + remaining_ratio * w.remaining
        - sales_velocity * w.sales_velocity
        + elapsed * w.elapsed
        + weather_score * (w.weather + w.outdoor_weather * float(scenario.outdoor))
        - float(scenario.day.weekday() >= 5) * w.weekend
        - peak * w.peak_hour
    )
    pressure = float(np.clip(pressure, 0.03, 0.65))
    position = float(np.clip((pressure - 0.03) / 0.62, 0.0, 1.0))
    raw = scenario.original_price - position * (scenario.original_price - scenario.minimum_price)
    if raw <= scenario.minimum_price + 1e-6:
        return scenario.minimum_price
    if raw >= scenario.original_price - 1e-6:
        return scenario.original_price
    return max(scenario.minimum_price, min(scenario.original_price, int(round(raw / 100.0) * 100)))


def evaluate(
    scenarios: list[ProductScenario], demand_by_day: dict, weights: Weights,
) -> dict[str, float]:
    revenue = gross_profit = unsold_loss = bookings = offers = initial_stock = unsold = 0
    for scenario in scenarios:
        remaining = scenario.inventory
        initial_stock += remaining
        total_steps = max(1, len(scenario.arrival_draws))
        for step, (persona, draw, noise) in enumerate(scenario.arrival_draws):
            if remaining <= 0:
                break
            price = rounded_price(
                scenario, remaining, step, total_steps, demand_by_day[scenario.day], weights,
            )
            probability = _purchase_probability(scenario, persona, price, noise)
            purchased = int(draw < probability)
            offers += 1
            bookings += purchased
            revenue += price * purchased
            gross_profit += max(0, price - scenario.unit_cost) * purchased
            remaining -= purchased
        unsold += remaining
        unsold_loss += remaining * scenario.unit_cost
    sell_through = (initial_stock - unsold) / max(1, initial_stock)
    net = gross_profit - unsold_loss
    # 콜드스타트의 균형 목적: 폐기 반영 순이익을 중심으로 매출과 소진을 함께 보상한다.
    score = net + 0.10 * revenue + 0.05 * sell_through * revenue
    return {
        "score": float(score),
        "revenue": int(revenue),
        "gross_profit": int(gross_profit),
        "unsold_loss": int(unsold_loss),
        "net_profit_after_waste": int(net),
        "bookings": int(bookings),
        "offers": int(offers),
        "conversion_rate": bookings / max(1, offers),
        "sell_through_rate": sell_through,
        "avg_price": revenue / max(1, bookings),
    }


def sample_weights(rng: random.Random, center: Weights | None, radius: float) -> Weights:
    values = {}
    current = asdict(center or CURRENT)
    for name in NAMES:
        low, high = BOUNDS[name]
        if center is None:
            value = rng.uniform(low, high)
        else:
            span = (high - low) * radius
            value = rng.uniform(max(low, current[name] - span), min(high, current[name] + span))
        values[name] = round(value, 5)
    return Weights(**values)


def main() -> None:
    config = BacktestConfig()
    scenarios = generate_scenarios(config)
    engine = PricingEngine(ROOT)
    demand_by_day = {
        scenario.day: engine.demand.context(
            None, datetime.combine(scenario.day, time(10), KST),
        ).percentile
        for scenario in scenarios
    }
    split = int(len(scenarios) * config.train_ratio)
    training, evaluation = scenarios[:split], scenarios[split:]
    rng = random.Random(20260916)
    best = CURRENT
    best_metrics = evaluate(training, demand_by_day, best)
    rounds = []
    for round_number, (candidates, radius) in enumerate(((70, 1.0), (70, 0.30), (70, 0.15), (70, 0.07)), 1):
        center = None if round_number == 1 else best
        round_best = best
        round_metrics = best_metrics
        for _ in range(candidates):
            candidate = sample_weights(rng, center, radius)
            result = evaluate(training, demand_by_day, candidate)
            if result["score"] > round_metrics["score"]:
                round_best, round_metrics = candidate, result
        best, best_metrics = round_best, round_metrics
        rounds.append({
            "round": round_number,
            "candidate_count": candidates,
            "radius": radius,
            "selected_weights": asdict(best),
            "training_metrics": best_metrics,
        })
        print(f"round={round_number} score={best_metrics['score']:.1f} net={best_metrics['net_profit_after_waste']:,}", flush=True)

    current_evaluation = evaluate(evaluation, demand_by_day, CURRENT)
    calibrated_evaluation = evaluate(evaluation, demand_by_day, best)
    payload = {
        "method": "four_stage_seeded_random_search_on_synthetic_persona_training_split",
        "selection_objective": "net_profit_after_waste + 0.10*revenue + 0.05*sell_through*revenue",
        "seed": 20260916,
        "scenario_seed": config.seed,
        "split_method": "chronological_60_train_40_evaluation",
        "training_scenarios": len(training),
        "evaluation_scenarios": len(evaluation),
        "rounds": rounds,
        "current_weights": asdict(CURRENT),
        "calibrated_weights": asdict(best),
        "holdout": {
            "current": current_evaluation,
            "calibrated": calibrated_evaluation,
            "net_profit_change_pct": (
                calibrated_evaluation["net_profit_after_waste"] / current_evaluation["net_profit_after_waste"] - 1
            ) * 100,
            "revenue_change_pct": (calibrated_evaluation["revenue"] / current_evaluation["revenue"] - 1) * 100,
            "sell_through_change_pct": (
                calibrated_evaluation["sell_through_rate"] / current_evaluation["sell_through_rate"] - 1
            ) * 100,
        },
        "limitations": [
            "Synthetic persona calibration does not prove real customer price elasticity.",
            "Recalibrate with observed offer, purchase, margin, and waste outcomes before automated rollout.",
        ],
    }
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"output={OUTPUT}")
    print(json.dumps(payload["holdout"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
