"""제주 일별 유동인구 XGBoost 반복 실험과 PPT 비교표 생성.

마지막 90일은 최종 테스트로 한 번만 사용하고, 그 이전 90일 검증구간에서
4개 후보를 비교한다. 선택된 설정을 테스트 직전 전체 데이터로 재학습한 뒤
전월 평균, 최근 4회 동일요일 평균, 지역·인구 변수 제거 모델과 비교한다.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from xgboost import XGBRegressor


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_RAW = BASE_DIR / "data" / "raw" / "jeju_daily_flow_3y.csv"
ARTIFACT_DIR = BASE_DIR / "artifacts"
DOCS_DIR = BASE_DIR / "docs"

GROUP_COLS = ["region", "city", "gender", "age_group"]
CALENDAR_COLS = ["month", "day", "day_of_year", "day_of_week", "is_weekend", "is_holiday"]
CYCLICAL_COLS = [
    "month_sin", "month_cos", "doy_sin", "doy_cos", "dow_sin", "dow_cos", "trend_days",
]
INTERACTION_COLS = ["region_dow", "region_month", "demographic"]
TARGET = "visit_pop"


@dataclass(frozen=True)
class Experiment:
    name: str
    description: str
    features: tuple[str, ...]
    log_target: bool
    params: dict


def experiments() -> list[Experiment]:
    core = tuple(GROUP_COLS + CALENDAR_COLS)
    cyclical = tuple(GROUP_COLS + CALENDAR_COLS + CYCLICAL_COLS)
    interaction = tuple(GROUP_COLS + INTERACTION_COLS + CALENDAR_COLS + CYCLICAL_COLS)
    common = {
        "objective": "reg:squarederror",
        "tree_method": "hist",
        "random_state": 42,
        "n_jobs": -1,
    }
    return [
        Experiment(
            "round_1_core",
            "현재 EBM과 같은 범주·달력 피처의 XGBoost 기준모델",
            core,
            False,
            common | {
                "n_estimators": 600, "learning_rate": 0.05, "max_depth": 6,
                "min_child_weight": 5, "subsample": 0.90, "colsample_bytree": 0.90,
                "reg_alpha": 0.0, "reg_lambda": 2.0,
            },
        ),
        Experiment(
            "round_2_cyclical",
            "연중·요일 주기와 장기 추세를 추가하고 깊이를 확대",
            cyclical,
            False,
            common | {
                "n_estimators": 1_000, "learning_rate": 0.035, "max_depth": 8,
                "min_child_weight": 3, "subsample": 0.90, "colsample_bytree": 0.90,
                "reg_alpha": 0.05, "reg_lambda": 3.0,
            },
        ),
        Experiment(
            "round_3_interactions",
            "지역×요일·지역×월·인구집단 상호작용 범주를 추가",
            interaction,
            False,
            common | {
                "n_estimators": 1_400, "learning_rate": 0.025, "max_depth": 7,
                "min_child_weight": 2, "subsample": 0.92, "colsample_bytree": 0.95,
                "reg_alpha": 0.10, "reg_lambda": 4.0,
            },
        ),
        Experiment(
            "round_4_log_regularized",
            "큰 유동인구 값의 영향과 과적합을 줄이는 log1p 타깃·정규화 모델",
            interaction,
            True,
            common | {
                "n_estimators": 1_600, "learning_rate": 0.025, "max_depth": 6,
                "min_child_weight": 4, "subsample": 0.88, "colsample_bytree": 0.90,
                "reg_alpha": 0.20, "reg_lambda": 6.0,
            },
        ),
    ]


def load_dataset(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"일별 원본이 없습니다: {path}\n"
            "다른 PC의 data/raw/jeju_daily_flow_3y.csv를 같은 경로에 복사하거나 "
            "JEJU_DATA_HUB_KEY를 설정한 뒤 train_daily_flow_ebm.py로 다시 수집하세요."
        )
    df = pd.read_csv(path, encoding="utf-8-sig")
    required = {"date", "city", "region", "gender", "age_group", TARGET}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"필수 컬럼이 없습니다: {missing}")
    df = df.drop_duplicates().copy()
    df["date"] = pd.to_datetime(df["date"])
    df[TARGET] = pd.to_numeric(df[TARGET], errors="coerce")
    df = df.dropna(subset=["date", *GROUP_COLS, TARGET])
    df = df[df[TARGET] >= 0].sort_values(["date", *GROUP_COLS]).reset_index(drop=True)
    return add_features(df)


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    dt = out["date"]
    out["month"] = dt.dt.month
    out["day"] = dt.dt.day
    out["day_of_year"] = dt.dt.dayofyear
    out["day_of_week"] = dt.dt.weekday
    out["is_weekend"] = (dt.dt.weekday >= 5).astype(int)
    try:
        import holidays

        kr_holidays = holidays.KR()
        out["is_holiday"] = [int(value.date() in kr_holidays) for value in dt]
    except ImportError:
        out["is_holiday"] = 0
    out["month_sin"] = np.sin(2 * np.pi * out["month"] / 12)
    out["month_cos"] = np.cos(2 * np.pi * out["month"] / 12)
    out["doy_sin"] = np.sin(2 * np.pi * out["day_of_year"] / 365.25)
    out["doy_cos"] = np.cos(2 * np.pi * out["day_of_year"] / 365.25)
    out["dow_sin"] = np.sin(2 * np.pi * out["day_of_week"] / 7)
    out["dow_cos"] = np.cos(2 * np.pi * out["day_of_week"] / 7)
    out["trend_days"] = (dt - dt.min()).dt.days
    out["region_dow"] = out["region"].astype(str) + "|" + out["day_of_week"].astype(str)
    out["region_month"] = out["region"].astype(str) + "|" + out["month"].astype(str)
    out["demographic"] = out["gender"].astype(str) + "|" + out["age_group"].astype(str)
    for column in GROUP_COLS + INTERACTION_COLS:
        out[column] = out[column].astype(str)
    return out


def temporal_split(df: pd.DataFrame, days: int = 90) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    max_date = df["date"].max()
    test_start = max_date - pd.Timedelta(days=days - 1)
    validation_start = test_start - pd.Timedelta(days=days)
    train = df[df["date"] < validation_start].copy()
    validation = df[(df["date"] >= validation_start) & (df["date"] < test_start)].copy()
    test = df[df["date"] >= test_start].copy()
    if min(len(train), len(validation), len(test)) == 0:
        raise ValueError("학습·검증·테스트 3구간을 만들 데이터가 부족합니다.")
    return train, validation, test


def make_pipeline(feature_names: tuple[str, ...], params: dict) -> Pipeline:
    categorical = [name for name in feature_names if name in GROUP_COLS + INTERACTION_COLS]
    numeric = [name for name in feature_names if name not in categorical]
    transformer = ColumnTransformer(
        [
            ("categorical", OneHotEncoder(handle_unknown="ignore", min_frequency=2), categorical),
            ("numeric", "passthrough", numeric),
        ],
        remainder="drop",
    )
    return Pipeline([("features", transformer), ("model", XGBRegressor(**params))])


def fit_predict(
    experiment: Experiment, train: pd.DataFrame, evaluation: pd.DataFrame,
) -> tuple[Pipeline, np.ndarray]:
    pipeline = make_pipeline(experiment.features, experiment.params)
    y_train = train[TARGET].to_numpy(dtype=float)
    if experiment.log_target:
        y_train = np.log1p(y_train)
    pipeline.fit(train[list(experiment.features)], y_train)
    prediction = pipeline.predict(evaluation[list(experiment.features)])
    if experiment.log_target:
        prediction = np.expm1(prediction)
    return pipeline, np.clip(np.asarray(prediction, dtype=float), 0, None)


def metrics(actual: np.ndarray, prediction: np.ndarray) -> dict[str, float]:
    actual = np.asarray(actual, dtype=float)
    prediction = np.asarray(prediction, dtype=float)
    error = actual - prediction
    denominator = float(np.abs(actual).sum())
    nonzero = actual != 0
    return {
        "mae": float(mean_absolute_error(actual, prediction)),
        "rmse": float(math.sqrt(mean_squared_error(actual, prediction))),
        "wape_pct": float(np.abs(error).sum() / denominator * 100) if denominator else float("nan"),
        "mape_pct": float(np.mean(np.abs(error[nonzero] / actual[nonzero])) * 100) if nonzero.any() else float("nan"),
        "r2": float(r2_score(actual, prediction)),
        "rows": int(len(actual)),
    }


def aggregate_region_day(frame: pd.DataFrame, predictions: dict[str, np.ndarray]) -> pd.DataFrame:
    output = frame[["date", "region", TARGET]].copy()
    for name, values in predictions.items():
        output[name] = values
    return output.groupby(["date", "region"], as_index=False).sum(numeric_only=True)


def historical_baselines(history: pd.DataFrame, evaluation: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    all_rows = pd.concat([history, evaluation], ignore_index=True).sort_values("date")
    group_fallback = history.groupby(GROUP_COLS)[TARGET].mean().to_dict()
    global_fallback = float(history[TARGET].mean())
    previous_month_predictions: list[float] = []
    weekday_predictions: list[float] = []

    month_maps: dict[str, dict[tuple, float]] = {}
    weekday_history: dict[tuple, list[tuple[pd.Timestamp, float]]] = {}
    for row in all_rows.itertuples(index=False):
        key = tuple(getattr(row, name) for name in GROUP_COLS)
        weekday_key = (*key, row.date.weekday())
        weekday_history.setdefault(weekday_key, []).append((row.date, float(getattr(row, TARGET))))

    for row in evaluation.itertuples(index=False):
        key = tuple(getattr(row, name) for name in GROUP_COLS)
        fallback = float(group_fallback.get(key, global_fallback))
        previous_period = (row.date.to_period("M") - 1).strftime("%Y-%m")
        if previous_period not in month_maps:
            period = pd.Period(previous_period, freq="M")
            source = all_rows[all_rows["date"].dt.to_period("M") == period]
            month_maps[previous_period] = source.groupby(GROUP_COLS)[TARGET].mean().to_dict()
        previous_month_predictions.append(float(month_maps[previous_period].get(key, fallback)))

        weekday_key = (*key, row.date.weekday())
        candidates = [
            value for value_date, value in weekday_history.get(weekday_key, [])
            if row.date - pd.Timedelta(days=35) <= value_date < row.date
        ][-4:]
        weekday_predictions.append(float(np.mean(candidates)) if candidates else previous_month_predictions[-1])
    return np.asarray(previous_month_predictions), np.asarray(weekday_predictions)


def format_metric_row(
    name: str, values: dict, previous_month_mae: float | None = None,
    same_weekday_mae: float | None = None,
) -> list[str]:
    previous_improvement = (
        "-" if previous_month_mae is None else f"{(1 - values['mae'] / previous_month_mae) * 100:.1f}%"
    )
    weekday_improvement = (
        "-" if same_weekday_mae is None else f"{(1 - values['mae'] / same_weekday_mae) * 100:.1f}%"
    )
    return [
        name, f"{values['mae']:,.1f}", f"{values['wape_pct']:.2f}%",
        f"{values['mape_pct']:.2f}%", f"{values['r2']:.4f}",
        previous_improvement, weekday_improvement,
    ]


def markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    return "\n".join([
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
        *("| " + " | ".join(row) + " |" for row in rows),
    ])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--test-days", type=int, default=90)
    args = parser.parse_args()

    dataset = load_dataset(args.raw)
    train, validation, test = temporal_split(dataset, args.test_days)
    validation_results = []
    for index, experiment in enumerate(experiments(), 1):
        print(f"[{index}/4] {experiment.name}: {experiment.description}", flush=True)
        _, prediction = fit_predict(experiment, train, validation)
        row_score = metrics(validation[TARGET].to_numpy(), prediction)
        validation_aggregated = aggregate_region_day(validation, {"prediction": prediction})
        region_day_score = metrics(
            validation_aggregated[TARGET].to_numpy(), validation_aggregated["prediction"].to_numpy(),
        )
        validation_results.append({
            "round": index, "name": experiment.name, "description": experiment.description,
            "log_target": experiment.log_target, "features": list(experiment.features),
            "params": experiment.params,
            "validation_row": row_score,
            "validation_region_day": region_day_score,
        })
        print(json.dumps({"row": row_score, "region_day": region_day_score}, ensure_ascii=False), flush=True)

    best_result = min(validation_results, key=lambda value: value["validation_region_day"]["mae"])
    best = next(value for value in experiments() if value.name == best_result["name"])
    pretest = pd.concat([train, validation], ignore_index=True).sort_values("date")
    final_model, xgb_prediction = fit_predict(best, pretest, test)

    ablation_features = tuple(CALENDAR_COLS + CYCLICAL_COLS)
    ablation = Experiment(
        "calendar_only_ablation", "지역·성별·연령 공공변수 제거", ablation_features,
        best.log_target, best.params,
    )
    _, ablation_prediction = fit_predict(ablation, pretest, test)
    previous_month, same_weekday = historical_baselines(pretest, test)

    aggregated = aggregate_region_day(test, {
        "xgb": xgb_prediction,
        "calendar_only": ablation_prediction,
        "previous_month": previous_month,
        "same_weekday": same_weekday,
    })
    actual = aggregated[TARGET].to_numpy()
    test_metrics = {
        name: metrics(actual, aggregated[name].to_numpy())
        for name in ("previous_month", "same_weekday", "calendar_only", "xgb")
    }

    ARTIFACT_DIR.mkdir(exist_ok=True)
    DOCS_DIR.mkdir(exist_ok=True)
    model_path = ARTIFACT_DIR / "xgb_daily_demand_model.joblib"
    report_path = ARTIFACT_DIR / "xgb_daily_validation_report.json"
    table_path = DOCS_DIR / "PPT_XGB_성능표.md"
    joblib.dump({
        "model": final_model,
        "experiment": asdict(best),
        "training_start": pretest["date"].min().date().isoformat(),
        "training_end": pretest["date"].max().date().isoformat(),
    }, model_path)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "raw_path": str(args.raw),
        "data_rows": len(dataset),
        "date_range": [dataset["date"].min().date().isoformat(), dataset["date"].max().date().isoformat()],
        "split": {
            "train": [train["date"].min().date().isoformat(), train["date"].max().date().isoformat(), len(train)],
            "validation": [validation["date"].min().date().isoformat(), validation["date"].max().date().isoformat(), len(validation)],
            "test": [test["date"].min().date().isoformat(), test["date"].max().date().isoformat(), len(test)],
        },
        "validation_rounds": validation_results,
        "selected_round": best.name,
        "test_unit": "region_day_after_row_level_prediction",
        "test_metrics": test_metrics,
        "public_feature_effect": {
            "comparison": "calendar_only_ablation_vs_full_xgb",
            "mae_reduction_pct": (1 - test_metrics["xgb"]["mae"] / test_metrics["calendar_only"]["mae"]) * 100,
            "wape_reduction_pct": (1 - test_metrics["xgb"]["wape_pct"] / test_metrics["calendar_only"]["wape_pct"]) * 100,
        },
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    table_rows = [
        format_metric_row("전월 일평균", test_metrics["previous_month"]),
        format_metric_row("최근 4회 동일요일 평균", test_metrics["same_weekday"]),
        format_metric_row("지역·인구 변수 제외 XGBoost", test_metrics["calendar_only"]),
        format_metric_row(
            "공공데이터 기반 XGBoost", test_metrics["xgb"],
            test_metrics["previous_month"]["mae"], test_metrics["same_weekday"]["mae"],
        ),
    ]
    validation_rows = [[
        str(value["round"]), value["name"], f"{value['validation_region_day']['mae']:,.1f}",
        f"{value['validation_region_day']['wape_pct']:.2f}%",
        f"{value['validation_region_day']['r2']:.4f}",
    ] for value in validation_results]
    table_path.write_text(
        "# PPT 수요예측 성능표\n\n"
        f"- 최종 선택: `{best.name}`\n"
        f"- 테스트 기간: {test['date'].min().date()} ~ {test['date'].max().date()}\n"
        "- 후보 선택 및 최종 평가 단위: 원본 인구집단 행을 예측한 뒤 읍면동×일 합산\n\n"
        "## 4회 검증 반복\n\n" +
        markdown_table(["회차", "설정", "검증 MAE", "검증 WAPE", "검증 R²"], validation_rows) +
        "\n\n## 최종 테스트 및 PPT 표\n\n" +
        markdown_table([
            "예측 방식", "MAE", "WAPE", "MAPE", "R²",
            "전월평균 대비 개선", "동일요일 대비 개선",
        ], table_rows) +
        "\n\n## 공공 세부변수 기여\n\n"
        f"지역·인구 변수를 제외한 모델 대비 전체 XGBoost의 MAE 감소율은 "
        f"**{report['public_feature_effect']['mae_reduction_pct']:.1f}%**이다. "
        "타깃 자체도 공공데이터이므로 PPT에는 ‘공공데이터 전체 포함/제외’가 아니라 "
        "‘지역·인구 공공변수 포함/제외’로 표기한다.\n",
        encoding="utf-8",
    )
    print(f"selected={best.name}")
    print(f"model={model_path}")
    print(f"report={report_path}")
    print(f"ppt_table={table_path}")


if __name__ == "__main__":
    main()
