"""EBM 학습 결과를 대시보드용 JSON으로 추출한다 (시각화 HTML은 별도 생성)."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
from interpret.glassbox import ExplainableBoostingRegressor

import jeju_ebm_demand as m

BASE_DIR = Path(__file__).resolve().parent

FEATURE_LABELS = {
    "region": "읍면동",
    "month": "월",
    "quarter": "분기",
    "is_peak_season": "성수기 여부",
    "weekend_days": "주말일수",
    "holiday_days": "공휴일수",
    "avg_temp": "평균기온",
    "rain_mm_sum": "월 강수량 합",
    "max_wind_max": "월 최대풍속",
    "heavy_rain_days": "폭우일수(≥20mm)",
    "strong_wind_days": "강풍일수(≥14m/s)",
    "arrivals": "입도객(당월)",
    "arrivals_lag1": "입도객 lag1(전월)",
    "arrivals_lag12": "입도객 lag12(전년동월)",
    "arrivals_ma3": "입도객 ma3(3개월평균)",
    "night_ratio": "심야 유동인구 비율",
    "young_ratio": "청년층 유동인구 비율",
    "female_ratio": "여성 유동인구 비율",
    "visitors_lag1": "방문객 lag1(전월)",
    "visitors_lag12": "방문객 lag12(전년동월)",
    "visitors_ma3": "방문객 ma3(3개월평균)",
}


def label(name):
    return FEATURE_LABELS.get(name, name)


def main():
    df = m.build_dataset()
    train, test = m.time_split(df, test_frac=0.2)

    X_train, y_train = train[m.FEATURE_COLS], train[m.TARGET_COL]
    X_test, y_test = test[m.FEATURE_COLS], test[m.TARGET_COL]

    ebm = ExplainableBoostingRegressor(interactions=10, random_state=42)
    ebm.fit(X_train, y_train)

    preds_test = ebm.predict(X_test)
    mae = float(np.mean(np.abs(y_test.values - preds_test)))
    r2 = float(1 - np.sum((y_test.values - preds_test) ** 2) / np.sum((y_test.values - y_test.values.mean()) ** 2))
    mape_val = m.mape(y_test.values, preds_test)

    # 월별 실제 합계(train+test 전체) vs 월별 예측 합계(test만)
    actual_monthly = df.groupby("yyyymm")["visitors"].sum().sort_index()
    test_pred_df = test[["yyyymm"]].copy()
    test_pred_df["pred"] = preds_test
    pred_monthly = test_pred_df.groupby("yyyymm")["pred"].sum().sort_index()
    months_sorted = sorted(df["yyyymm"].unique())
    test_months = sorted(test["yyyymm"].unique())

    timeseries = {
        "months": months_sorted,
        "actual": [float(actual_monthly.get(mm, 0)) for mm in months_sorted],
        "test_months": test_months,
        "predicted": [float(pred_monthly.get(mm, 0)) for mm in test_months],
    }

    # 피처 중요도 top 12
    names = ebm.term_names_
    importances = ebm.term_importances()
    order = np.argsort(importances)[::-1][:12]
    feature_importance = [
        {"name": names[i], "label": label(names[i]), "importance": float(importances[i])}
        for i in order
    ]

    # 상위 연속형(수치) 피처 4개의 shape function (region 등 범주형 제외)
    ge = ebm.explain_global()
    shape_functions = []
    for i in order:
        fname = names[i]
        if fname == "region" or "x" in fname:  # 상호작용 텀 제외
            continue
        d = ge.data(int(i))
        if d["type"] != "univariate":
            continue
        bin_edges = list(d["names"])
        scores = list(d["scores"])
        if len(bin_edges) == len(scores):
            # 범주형(예: is_peak_season, month 등 정수 카테고리 취급 가능) -> 그대로 포인트로 사용
            xs = [float(v) for v in bin_edges]
        else:
            # 연속형: 구간 경계 -> 구간 중앙값으로 변환
            xs = [float((bin_edges[j] + bin_edges[j + 1]) / 2) for j in range(len(scores))]
        shape_functions.append({
            "name": fname, "label": label(fname),
            "x": xs, "y": [float(s) for s in scores],
        })
        if len(shape_functions) >= 4:
            break

    # 개별 예측 기여도 분해 (테스트 첫 행)
    sample_X = X_test.iloc[[0]]
    sample_y = float(y_test.iloc[0])
    local_expl = ebm.explain_local(sample_X, pd.Series([sample_y]))
    local_data = local_expl.data(0)
    contrib_names = local_data["names"]
    contrib_scores = local_data["scores"]
    contrib_order = np.argsort(np.abs(contrib_scores))[::-1][:12]
    local_explanation = {
        "region": str(sample_X["region"].iloc[0]),
        "yyyymm": str(test.iloc[0]["yyyymm"]),
        "actual": sample_y,
        "predicted": float(preds_test[0]),
        "contributions": [
            {"name": contrib_names[j], "label": label(str(contrib_names[j])), "value": float(contrib_scores[j])}
            for j in contrib_order
        ],
    }

    data = {
        "metrics": {
            "mae": mae, "mape": mape_val, "r2": r2,
            "n_rows": int(df.shape[0]), "n_cols": int(df.shape[1]),
            "train_months": [months_sorted[0], [mm for mm in months_sorted if mm not in test_months][-1]],
            "test_months": [test_months[0], test_months[-1]],
        },
        "timeseries": timeseries,
        "feature_importance": feature_importance,
        "shape_functions": shape_functions,
        "local_explanation": local_explanation,
        "sources": {
            "target": {"rows": 1075, "regions": 43, "months": 25, "period": "202401~202601"},
            "arrivals": {"rows": len(m.load_arrivals_monthly()), "period": "202401~202601"},
            "flow": {"rows": 46176, "period": "201801~202504"},
            "weather": {"rows": 26, "period": "202501~202601"},
        },
    }

    out_path = BASE_DIR / "dashboard_data.json"
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {out_path} ({out_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
