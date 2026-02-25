"""
ml/predict.py - 載入模型進行預測
"""
import pandas as pd
import numpy as np
import joblib
import os
import glob
import logging
from config import MODELS_DIR
from ml.train import FEATURE_COLS

logger = logging.getLogger(__name__)


def load_latest_model():
    """載入最新訓練的模型"""
    model_files = glob.glob(os.path.join(MODELS_DIR, "lgbm_*.pkl"))
    if not model_files:
        return None
    latest = sorted(model_files)[-1]
    logger.info(f"載入模型：{latest}")
    return joblib.load(latest)


def predict_stocks(df: pd.DataFrame, threshold: float = 0.60) -> pd.DataFrame:
    """
    對股票列表進行預測
    threshold: 只有機率超過此值才視為買進信號（預設 60%）
    回傳：加上 predict_proba 和 signal 欄位的 DataFrame
    """
    model = load_latest_model()
    if model is None:
        raise ValueError("找不到訓練好的模型，請先執行 ml/train.py")

    available_features = [c for c in FEATURE_COLS if c in df.columns]
    X = df[available_features].fillna(0).values

    probas = model.predict(X)
    df = df.copy()
    df["predict_proba"] = probas
    df["signal"] = (probas >= threshold).astype(int)

    return df


def screen_stocks(stocks_data: dict, threshold: float = 0.60) -> list:
    """
    對多支股票進行篩選，回傳符合條件的股票列表
    stocks_data: {stock_id: DataFrame}
    """
    candidates = []

    for stock_id, df in stocks_data.items():
        try:
            if df.empty:
                continue

            result = predict_stocks(df.tail(1), threshold)  # 只預測最新一天

            if result["signal"].iloc[0] == 1:
                proba = result["predict_proba"].iloc[0]
                candidates.append({
                    "stock_id": stock_id,
                    "probability": round(proba, 4),
                    "signal": "🔥 買進",
                })
        except Exception as e:
            logger.error(f"預測 {stock_id} 失敗：{e}")

    # 按機率排序
    candidates.sort(key=lambda x: x["probability"], reverse=True)
    return candidates
