from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable

import numpy as np

from app.pricing import PricingEngine
from app.schemas import PricingRequest
from app.weather import WeatherContext


KST = timezone(timedelta(hours=9))
PERSONAS = {
    "business": (1.18, 0.65, 0.10),
    "family": (1.05, 0.85, 0.12),
    "budget": (0.82, 1.45, 0.08),
    "impulse": (0.95, 1.05, 0.18),
    "loyal": (1.08, 0.75, 0.07),
}


@dataclass(frozen=True)
class BacktestConfig:
    start_date: str = "2024-01-01"
    end_date: str = "2026-08-29"
    sampled_days: int = 180
    products_per_day: int = 10
    seed: int = 42
    strategies: tuple[str, ...] = (
        "fixed",
        "rule_profit", "rule_balanced", "rule_sell_through",
        "bandit_profit", "bandit_balanced", "bandit_sell_through",
    )
    minimum_agents: int = 30_000
    cost_ratio_min: float = 0.30
    cost_ratio_max: float = 0.50
    train_ratio: float = 0.60
    minimum_evaluation_agents: int = 20_000


@dataclass
class ProductScenario:
    scenario_id: str
    day: date
    original_price: int
    minimum_price: int
    unit_cost: int
    inventory: int
    demand: float
    weather_risk: float
    outdoor: bool
    arrival_draws: list[tuple[str, float, float]]


class Policy:
    name = "base"

    def price(self, scenario: ProductScenario, remaining: int, step: int, total_steps: int) -> int:
        raise NotImplementedError

    def update(self, context: tuple[int, int], action: int, reward: float) -> None:
        pass


class FixedPolicy(Policy):
    name = "fixed"

    def price(self, scenario: ProductScenario, remaining: int, step: int, total_steps: int) -> int:
        return scenario.original_price


class ExistingRulePolicy(Policy):
    def __init__(self, root: Path, purpose: str = "BALANCED"):
        self.engine = PricingEngine(root)
        self.purpose = purpose
        self.name = "rule_" + purpose.lower()

    def price(self, scenario: ProductScenario, remaining: int, step: int, total_steps: int) -> int:
        now = datetime.combine(scenario.day, time(10), KST) + timedelta(minutes=step * 10)
        deadline = datetime.combine(scenario.day, time(22), KST)
        request = PricingRequest(
            productId=1,
            businessType="TOUR" if scenario.outdoor else "RESTAURANT",
            category="TOUR_REMAINDER" if scenario.outdoor else "SAME_DAY_INVENTORY",
            originalPrice=scenario.original_price,
            minimumPrice=scenario.minimum_price,
            currentPrice=scenario.original_price,
            totalQty=scenario.inventory,
            remainingQty=remaining,
            inventoryChange=remaining - scenario.inventory,
            availableStartAt=datetime.combine(scenario.day, time(10), KST),
            deadline=deadline,
            currentTime=min(now, deadline),
        )
        weather = WeatherContext(
            temperature=20 - scenario.weather_risk * 7,
            precipitation=scenario.weather_risk * 12,
            wind_speed=3 + scenario.weather_risk * 8,
            summary="simulation",
            source="SIMULATED",
        )
        result = self.engine.recommend(request, weather)
        return next(option.price for option in result.priceOptions if option.purpose == self.purpose)


class BanditPolicy(Policy):
    """LinUCB contextual bandit that generalizes across continuous market features."""

    name = "bandit"
    # Relative positions inside each product's feasible price interval. The resulting discounts
    # are dynamic because originalPrice and minimumPrice differ for every product.
    price_positions = (0.00, 0.10, 0.20, 0.35, 0.50, 0.70, 1.00)

    def __init__(self, seed: int, purpose: str = "BALANCED"):
        self.purpose = purpose
        self.name = "bandit_" + purpose.lower()
        self.dimension = 18
        self.a_inv = [np.eye(self.dimension) for _ in self.price_positions]
        self.b = [np.zeros(self.dimension) for _ in self.price_positions]
        self.arm_offers = [0 for _ in self.price_positions]
        self.arm_purchases = [0 for _ in self.price_positions]
        self.persona_counts = {name: 0 for name in PERSONAS}
        self.observed_personas = 0
        self.last_action: int | None = None
        self.last_features: np.ndarray | None = None
        self.learning_enabled = True

    def _features(self, scenario: ProductScenario, remaining: int, step: int, total_steps: int,
                  action: int) -> np.ndarray:
        remaining_ratio = remaining / max(1, scenario.inventory)
        elapsed = step / max(1, total_steps - 1)
        sales_velocity = min(1.0, (scenario.inventory - remaining) / max(1, step + 1))
        cost_ratio = scenario.unit_cost / max(1, scenario.original_price)
        feasible_discount = 1.0 - scenario.minimum_price / scenario.original_price
        candidate_discount = feasible_discount * self.price_positions[action]
        candidate_margin = max(0.0, 1.0 - candidate_discount - cost_ratio)
        shares = [self.persona_counts[name] / max(1, self.observed_personas) for name in PERSONAS]
        arm_rate = self.arm_purchases[action] / max(1, self.arm_offers[action])
        return np.asarray([
            1.0, scenario.demand, remaining_ratio, elapsed, cost_ratio, feasible_discount,
            scenario.weather_risk, float(scenario.day.weekday() >= 5), float(scenario.outdoor),
            sales_velocity, *shares, arm_rate, candidate_discount, candidate_margin,
        ], dtype=float)

    def price(self, scenario: ProductScenario, remaining: int, step: int, total_steps: int) -> int:
        feasible_discount = 1.0 - scenario.minimum_price / scenario.original_price
        margin_floor = {"PROFIT": 0.20, "BALANCED": 0.15, "SELL_THROUGH": 0.12}[self.purpose]
        candidates = []
        alpha = 0.55 if self.learning_enabled else 0.0
        for action in range(len(self.price_positions)):
            features = self._features(scenario, remaining, step, total_steps, action)
            if features[-1] < margin_floor:
                continue
            theta = self.a_inv[action] @ self.b[action]
            uncertainty = math.sqrt(max(0.0, float(features @ self.a_inv[action] @ features)))
            candidates.append((float(theta @ features) + alpha * uncertainty, action, features))
        _, action, features = max(candidates, key=lambda row: row[0])
        self.last_action, self.last_features = action, features
        discount = feasible_discount * self.price_positions[action]
        raw = scenario.original_price * (1 - discount)
        return max(scenario.minimum_price, int(round(raw / 100) * 100))

    def observe(self, scenario: ProductScenario, price: int, purchased: int, step: int,
                total_steps: int, persona: str) -> None:
        margin = max(0, price - scenario.unit_cost) / max(1, scenario.original_price)
        revenue = price / max(1, scenario.original_price)
        urgency = step / max(1, total_steps - 1)
        if self.purpose == "PROFIT":
            reward = purchased * margin
        elif self.purpose == "SELL_THROUGH":
            reward = purchased * (0.40 * margin + 0.35 * revenue + 0.25 * (1.0 + 0.15 * urgency))
        else:
            reward = purchased * (0.55 * margin + 0.35 * revenue + 0.10)
        if self.learning_enabled and self.last_action is not None and self.last_features is not None:
            action, features = self.last_action, self.last_features
            inverse = self.a_inv[action]
            projected = inverse @ features
            self.a_inv[action] = inverse - np.outer(projected, projected) / (1.0 + features @ projected)
            self.b[action] += reward * features
            self.arm_offers[action] += 1
            self.arm_purchases[action] += purchased
            self.persona_counts[persona] += 1
            self.observed_personas += 1


def _random_dates(config: BacktestConfig, rng: random.Random) -> list[date]:
    start, end = date.fromisoformat(config.start_date), date.fromisoformat(config.end_date)
    if start > end:
        raise ValueError("start_date must not be after end_date")
    span = (end - start).days + 1
    if config.sampled_days > span:
        raise ValueError(f"sampled_days cannot exceed date range ({span})")
    return sorted(start + timedelta(days=n) for n in rng.sample(range(span), config.sampled_days))


def generate_scenarios(config: BacktestConfig) -> list[ProductScenario]:
    if not 0 <= config.cost_ratio_min <= config.cost_ratio_max <= 1:
        raise ValueError("cost ratios must satisfy 0 <= min <= max <= 1")
    rng = random.Random(config.seed)
    np_rng = np.random.default_rng(config.seed)
    scenarios = []
    for day in _random_dates(config, rng):
        seasonal = 0.18 * math.sin((day.timetuple().tm_yday - 170) / 365 * math.tau)
        weekend = 0.16 if day.weekday() >= 5 else 0.0
        for product_index in range(config.products_per_day):
            demand = min(1.0, max(0.05, 0.48 + seasonal + weekend + rng.gauss(0, 0.14)))
            original = rng.randrange(200, 1201) * 100
            minimum = int(round(original * rng.uniform(0.48, 0.72) / 100) * 100)
            unit_cost = int(round(minimum * rng.uniform(config.cost_ratio_min, config.cost_ratio_max) / 100) * 100)
            inventory = rng.randint(8, 45)
            arrivals = int(np_rng.poisson(inventory * (0.55 + demand * 1.15)))
            draws = []
            for _ in range(arrivals):
                persona = rng.choice(list(PERSONAS))
                draws.append((persona, rng.random(), rng.gauss(0, 0.09)))
            scenarios.append(ProductScenario(
                scenario_id=f"{day.isoformat()}-{product_index:03d}", day=day,
                original_price=original, minimum_price=minimum, unit_cost=unit_cost, inventory=inventory,
                demand=demand, weather_risk=rng.random(), outdoor=rng.random() < 0.35,
                arrival_draws=draws,
            ))
    return scenarios


def _purchase_probability(s: ProductScenario, persona: str, price: int, noise: float) -> float:
    willingness, sensitivity, _ = PERSONAS[persona]
    wtp = s.original_price * willingness * (0.72 + 0.48 * s.demand + noise)
    price_gap = (wtp - price) / max(1, s.original_price)
    logit = price_gap * 7 / sensitivity + (s.demand - 0.5) * 1.2
    if s.outdoor:
        logit -= s.weather_risk * 1.1
    return 1 / (1 + math.exp(-max(-20, min(20, logit))))


def run_backtest(config: BacktestConfig, output_dir: Path) -> Path:
    scenarios = generate_scenarios(config)
    generated_agents = sum(len(s.arrival_draws) for s in scenarios)
    if generated_agents < config.minimum_agents:
        raise ValueError(
            f"generated agents ({generated_agents:,}) is below minimum_agents ({config.minimum_agents:,}); "
            "increase --sampled-days or --products-per-day"
        )
    if not 0 < config.train_ratio < 1:
        raise ValueError("train_ratio must be between 0 and 1")
    split_index = max(1, min(len(scenarios) - 1, int(len(scenarios) * config.train_ratio)))
    training_scenarios = scenarios[:split_index]
    evaluation_scenarios = scenarios[split_index:]
    training_agents = sum(len(s.arrival_draws) for s in training_scenarios)
    evaluation_agents = sum(len(s.arrival_draws) for s in evaluation_scenarios)
    if evaluation_agents < config.minimum_evaluation_agents:
        raise ValueError(
            f"evaluation agents ({evaluation_agents:,}) is below minimum_evaluation_agents "
            f"({config.minimum_evaluation_agents:,})"
        )
    root = Path(__file__).resolve().parents[1]
    factories = {
        "fixed": lambda: FixedPolicy(),
        "existing_rule": lambda: ExistingRulePolicy(root, "BALANCED"),
        "bandit": lambda: BanditPolicy(config.seed, "BALANCED"),
        "rule_profit": lambda: ExistingRulePolicy(root, "PROFIT"),
        "rule_balanced": lambda: ExistingRulePolicy(root, "BALANCED"),
        "rule_sell_through": lambda: ExistingRulePolicy(root, "SELL_THROUGH"),
        "bandit_profit": lambda: BanditPolicy(config.seed, "PROFIT"),
        "bandit_balanced": lambda: BanditPolicy(config.seed, "BALANCED"),
        "bandit_sell_through": lambda: BanditPolicy(config.seed, "SELL_THROUGH"),
    }
    unknown = set(config.strategies) - factories.keys()
    if unknown:
        raise ValueError(f"unknown strategies: {sorted(unknown)}")
    run_id = datetime.now(KST).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    run_dir = output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    db = sqlite3.connect(run_dir / "backtest.sqlite3")
    db.executescript("""
        CREATE TABLE events (run_id TEXT, scenario_id TEXT, day TEXT, strategy TEXT, persona TEXT,
          offered_price INTEGER, purchased INTEGER, revenue INTEGER, remaining INTEGER,
          demand REAL, weather_risk REAL, purchase_probability REAL);
        CREATE TABLE summaries (strategy TEXT PRIMARY KEY, revenue INTEGER, gross_profit INTEGER,
          unsold_loss INTEGER, net_profit_after_waste INTEGER, unsold_units INTEGER,
          bookings INTEGER, offers INTEGER, conversion_rate REAL, sell_through_rate REAL, avg_price REAL);
    """)
    events = []
    summaries = []
    for strategy in config.strategies:
        policy = factories[strategy]()
        if isinstance(policy, BanditPolicy):
            for scenario in training_scenarios:
                remaining = scenario.inventory
                total_steps = max(1, len(scenario.arrival_draws))
                for step, (persona, draw, noise) in enumerate(scenario.arrival_draws):
                    if remaining <= 0:
                        break
                    price = policy.price(scenario, remaining, step, total_steps)
                    probability = _purchase_probability(scenario, persona, price, noise)
                    purchased = int(draw < probability)
                    remaining -= purchased
                    policy.observe(scenario, price, purchased, step, total_steps, persona)
            # Evaluation is a frozen policy: no exploration and no learning from holdout outcomes.
            policy.learning_enabled = False
        revenue = gross_profit = unsold_loss = bookings = offers = initial_stock = unsold = 0
        for scenario in evaluation_scenarios:
            remaining = scenario.inventory
            initial_stock += remaining
            total_steps = max(1, len(scenario.arrival_draws))
            for step, (persona, draw, noise) in enumerate(scenario.arrival_draws):
                if remaining <= 0:
                    break
                price = policy.price(scenario, remaining, step, total_steps)
                probability = _purchase_probability(scenario, persona, price, noise)
                purchased = int(draw < probability)
                reward = price * purchased
                offers += 1
                bookings += purchased
                revenue += reward
                gross_profit += max(0, price - scenario.unit_cost) * purchased
                remaining -= purchased
                if isinstance(policy, BanditPolicy):
                    policy.observe(scenario, price, purchased, step, total_steps, persona)
                events.append((run_id, scenario.scenario_id, scenario.day.isoformat(), strategy, persona,
                               price, purchased, reward, remaining, scenario.demand,
                               scenario.weather_risk, probability))
            unsold += remaining
            unsold_loss += remaining * scenario.unit_cost
        avg_price = revenue / max(1, bookings)
        net_profit_after_waste = gross_profit - unsold_loss
        summaries.append((strategy, revenue, gross_profit, unsold_loss, net_profit_after_waste, unsold,
                          bookings, offers, bookings / max(1, offers),
                          (initial_stock - unsold) / max(1, initial_stock), avg_price))
    db.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", events)
    db.executemany("INSERT INTO summaries VALUES (?,?,?,?,?,?,?,?,?,?,?)", summaries)
    db.commit()
    db.close()
    with (run_dir / "events.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("run_id", "scenario_id", "day", "strategy", "persona", "offered_price",
                         "purchased", "revenue", "remaining", "demand", "weather_risk", "purchase_probability"))
        writer.writerows(events)
    summary_payload = {row[0]: dict(zip(
        ("revenue", "gross_profit", "unsold_loss", "net_profit_after_waste", "unsold_units",
         "bookings", "offers", "conversion_rate", "sell_through_rate", "avg_price"), row[1:]
    )) for row in summaries}
    (run_dir / "summary.json").write_text(json.dumps({
        "run_id": run_id, "config": asdict(config), "scenario_count": len(scenarios),
        "generated_agents_per_strategy": generated_agents,
        "training_agents_per_bandit": training_agents,
        "evaluation_agents_per_strategy": evaluation_agents,
        "split_method": "chronological_scenario_split",
        "cost_assumption": {
            "method": "minimum_price_times_uniform_ratio",
            "ratio_min": config.cost_ratio_min,
            "ratio_max": config.cost_ratio_max,
            "note": "Replace with actual variable/contribution cost before business use.",
        },
        "unsold_assumption": "Same-day unsold units expire; unsold_loss = units * unit_cost.",
        "sampled_dates": sorted({s.day.isoformat() for s in scenarios}), "strategies": summary_payload,
        "limitations": [
            "Synthetic-agent outcomes compare policies but do not prove real customer demand.",
            "Purchase probabilities must be calibrated with production conversion data before rollout.",
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return run_dir


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run reproducible synthetic-user pricing backtests")
    parser.add_argument("--start-date", default="2024-01-01")
    parser.add_argument("--end-date", default="2026-08-29")
    parser.add_argument("--sampled-days", type=int, default=180)
    parser.add_argument("--products-per-day", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--strategies", default="fixed,rule_profit,rule_balanced,rule_sell_through,bandit_profit,bandit_balanced,bandit_sell_through")
    parser.add_argument("--minimum-agents", type=int, default=30_000)
    parser.add_argument("--cost-ratio-min", type=float, default=0.30)
    parser.add_argument("--cost-ratio-max", type=float, default=0.50)
    parser.add_argument("--train-ratio", type=float, default=0.60)
    parser.add_argument("--minimum-evaluation-agents", type=int, default=20_000)
    parser.add_argument("--output", type=Path, default=Path("simulation/results"))
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    config = BacktestConfig(args.start_date, args.end_date, args.sampled_days,
                            args.products_per_day, args.seed, tuple(args.strategies.split(",")), args.minimum_agents,
                            args.cost_ratio_min, args.cost_ratio_max, args.train_ratio, args.minimum_evaluation_agents)
    run_dir = run_backtest(config, args.output)
    print(f"Backtest complete: {run_dir}")
    print((run_dir / "summary.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
