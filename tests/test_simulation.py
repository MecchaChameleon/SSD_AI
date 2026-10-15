import json
import sqlite3

from simulation.backtest import BacktestConfig, generate_scenarios, run_backtest


def test_random_dates_are_reproducible_and_in_range():
    config = BacktestConfig("2025-01-01", "2025-01-31", 5, 2, 7, ("fixed",), 0, .30, .50, .60, 0)
    first = generate_scenarios(config)
    second = generate_scenarios(config)
    assert [(row.scenario_id, row.arrival_draws) for row in first] == [
        (row.scenario_id, row.arrival_draws) for row in second
    ]
    assert len({row.day for row in first}) == 5
    assert all(row.day.year == 2025 and row.day.month == 1 for row in first)


def test_backtest_persists_csv_json_and_sqlite(tmp_path):
    config = BacktestConfig("2025-02-01", "2025-02-10", 2, 1, 11, ("fixed", "bandit"), 0, .30, .50, .60, 0)
    run_dir = run_backtest(config, tmp_path)
    assert (run_dir / "events.csv").exists()
    payload = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert set(payload["strategies"]) == {"fixed", "bandit"}
    with sqlite3.connect(run_dir / "backtest.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] > 0
        assert db.execute("SELECT COUNT(*) FROM summaries").fetchone()[0] == 2
