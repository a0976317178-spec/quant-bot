"""
ml/train.py - 第四階段：LightGBM 訓練核心
Walk-Forward Validation（時間序列正確切割，避免洩漏）
"""
import pandas as pd
import numpy as np
import lightgbm as lgb
from sklearn.metrics import (
    classification_report, roc_auc_score, precision_score, recall_score
)
from sklearn.preprocessing import StandardScaler
import joblib
import os
import logging
from datetime import datetime
from config import MODELS_DIR

logger = logging.getLogger(__name__)


# ── 特徵欄位定義 ──────────────────────────────────
FEATURE_COLS = [
    # 量價因子
    "return_5d", "return_10d", "return_20d",
    "rsi", "atr_pct", "macd_hist_slope", "bias_20ma", "vol_ratio_20d",
    # 籌碼因子
    "foreign_consecutive_days", "foreign_net_ma5", "trust_net_ma5",
    # 基本面因子
    "yoy", "mom", "fundamental_score", "pe_ratio",
    # 宏觀因子
    "vix", "adl_daily",
]


def prepare_features(df: pd.DataFrame) -> tuple:
    """
    準備特徵矩陣 X 和標籤向量 y
    """
    # 只保留有標籤、有因子的樣本
    available_features = [c for c in FEATURE_COLS if c in df.columns]
    df_clean = df[available_features + ["label", "date"]].dropna()

    X = df_clean[available_features].values
    y = df_clean["label"].values.astype(int)
    dates = df_clean["date"].values

    return X, y, dates, available_features


def walk_forward_train(df: pd.DataFrame, n_splits: int = 5) -> dict:
    """
    Walk-Forward Validation
    正確的時間序列切割方式，避免未來資料洩漏到訓練集
    """
    X, y, dates, feature_names = prepare_features(df)
    n = len(X)
    split_size = n // (n_splits + 1)

    results = []
    models = []

    print(f"\n{'='*50}")
    print(f"Walk-Forward Validation | {n_splits} 折")
    print(f"{'='*50}")

    for fold in range(n_splits):
        train_end = split_size * (fold + 1)
        test_start = train_end
        test_end = min(train_end + split_size, n)

        X_train = X[:train_end]
        y_train = y[:train_end]
        X_test = X[test_start:test_end]
        y_test = y[test_start:test_end]

        # 樣本不平衡處理（正樣本通常只佔 10~20%）
        pos_weight = (y_train == 0).sum() / max((y_train == 1).sum(), 1)

        # LightGBM 參數
        params = {
            "objective": "binary",
            "metric": "auc",
            "boosting_type": "gbdt",
            "num_leaves": 31,
            "learning_rate": 0.05,
            "feature_fraction": 0.8,
            "bagging_fraction": 0.8,
            "bagging_freq": 5,
            "scale_pos_weight": pos_weight,
            "verbose": -1,
        }

        train_data = lgb.Dataset(X_train, label=y_train, feature_name=feature_names)
        valid_data = lgb.Dataset(X_test, label=y_test, reference=train_data)

        callbacks = [lgb.early_stopping(50, verbose=False), lgb.log_evaluation(period=-1)]

        model = lgb.train(
            params,
            train_data,
            num_boost_round=500,
            valid_sets=[valid_data],
            callbacks=callbacks,
        )

        # 預測（取機率分數）
        y_pred_proba = model.predict(X_test)
        y_pred = (y_pred_proba >= 0.5).astype(int)

        auc = roc_auc_score(y_test, y_pred_proba)
        precision = precision_score(y_test, y_pred, zero_division=0)
        recall = recall_score(y_test, y_pred, zero_division=0)

        result = {
            "fold": fold + 1,
            "train_size": train_end,
            "test_size": test_end - test_start,
            "auc": round(auc, 4),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
        }
        results.append(result)
        models.append(model)

        print(f"Fold {fold+1} | AUC: {auc:.4f} | Precision: {precision:.4f} | Recall: {recall:.4f}")

    # 用最後一折的模型作為最終模型（最貼近當前市場）
    final_model = models[-1]

    # 特徵重要性
    importance = pd.DataFrame({
        "feature": feature_names,
        "importance": final_model.feature_importance(importance_type="gain"),
    }).sort_values("importance", ascending=False)

    print(f"\n📊 Top 10 重要因子：")
    print(importance.head(10).to_string(index=False))

    # 儲存模型
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    model_path = os.path.join(MODELS_DIR, f"lgbm_{timestamp}.pkl")
    joblib.dump(final_model, model_path)
    print(f"\n💾 模型已儲存：{model_path}")

    avg_auc = np.mean([r["auc"] for r in results])
    print(f"\n🎯 平均 AUC: {avg_auc:.4f}")

    return {
        "model": final_model,
        "model_path": model_path,
        "results": results,
        "avg_auc": avg_auc,
        "feature_importance": importance.to_dict("records"),
        "feature_names": feature_names,
    }


if __name__ == "__main__":
    # 測試用假資料
    np.random.seed(42)
    n = 1000
    df_test = pd.DataFrame({
        "date": pd.date_range("2020-01-01", periods=n),
        "return_5d": np.random.randn(n) * 0.05,
        "return_10d": np.random.randn(n) * 0.07,
        "return_20d": np.random.randn(n) * 0.10,
        "rsi": np.random.uniform(20, 80, n),
        "atr_pct": np.random.uniform(0.01, 0.05, n),
        "macd_hist_slope": np.random.randn(n) * 0.01,
        "bias_20ma": np.random.randn(n) * 0.05,
        "vol_ratio_20d": np.random.uniform(0.5, 2.5, n),
        "foreign_consecutive_days": np.random.randint(-10, 10, n),
        "foreign_net_ma5": np.random.randn(n) * 1000,
        "trust_net_ma5": np.random.randn(n) * 500,
        "yoy": np.random.randn(n) * 20,
        "mom": np.random.randn(n) * 10,
        "fundamental_score": np.random.randint(0, 5, n),
        "pe_ratio": np.random.uniform(8, 30, n),
        "vix": np.random.uniform(12, 35, n),
        "adl_daily": np.random.randint(-500, 500, n),
        "label": np.random.choice([0, 1], n, p=[0.85, 0.15]),
    })

    result = walk_forward_train(df_test, n_splits=3)
