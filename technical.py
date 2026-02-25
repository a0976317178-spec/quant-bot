"""
factors/technical.py - 量價因子（Technical）
計算：報酬率、RSI、ATR、MACD斜率、乖離率
"""
import pandas as pd
import numpy as np
import ta
from config import FACTOR_CONFIG


def calc_technical_factors(df: pd.DataFrame) -> pd.DataFrame:
    """
    輸入 OHLCV DataFrame，輸出含所有量價因子的 DataFrame
    必要欄位：open, high, low, close, volume
    """
    df = df.copy()
    cfg = FACTOR_CONFIG

    # ── 1. 過去 N 天報酬率 ─────────────────────────
    for window in cfg["return_windows"]:
        df[f"return_{window}d"] = df["close"].pct_change(window)

    # ── 2. RSI ────────────────────────────────────
    df["rsi"] = ta.momentum.RSIIndicator(
        close=df["close"], window=cfg["rsi_period"]
    ).rsi()

    # ── 3. ATR 波動率 ──────────────────────────────
    atr_indicator = ta.volatility.AverageTrueRange(
        high=df["high"],
        low=df["low"],
        close=df["close"],
        window=cfg["atr_period"],
    )
    df["atr"] = atr_indicator.average_true_range()
    df["atr_pct"] = df["atr"] / df["close"]  # 標準化

    # ── 4. MACD 柱狀圖斜率 ────────────────────────
    macd = ta.trend.MACD(
        close=df["close"],
        window_fast=cfg["macd_fast"],
        window_slow=cfg["macd_slow"],
        window_sign=cfg["macd_signal"],
    )
    df["macd_hist"] = macd.macd_diff()
    df["macd_hist_slope"] = df["macd_hist"].diff(3)  # 3日斜率

    # ── 5. 距離 20MA 的乖離率 ──────────────────────
    df["ma20"] = df["close"].rolling(cfg["ma_period"]).mean()
    df["bias_20ma"] = (df["close"] - df["ma20"]) / df["ma20"]

    # ── 6. 成交量相對均量 ──────────────────────────
    df["vol_ratio_20d"] = df["volume"] / df["volume"].rolling(20).mean()

    return df


if __name__ == "__main__":
    # 測試用假資料
    import yfinance as yf
    df = yf.download("2330.TW", start="2023-01-01", end="2024-01-01")
    df.columns = [c.lower() for c in df.columns]
    result = calc_technical_factors(df)
    print(result[["close", "rsi", "atr_pct", "macd_hist_slope", "bias_20ma"]].tail(10))
