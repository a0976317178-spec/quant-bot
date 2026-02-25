"""
factors/labeling.py - 第三階段：定義殺戮目標（Labeling）
目標：未來5日內 最高漲幅 > 5%，且最低跌幅 < 2%
"""
import pandas as pd
import numpy as np
from config import LABEL_CONFIG


def create_labels(df: pd.DataFrame) -> pd.DataFrame:
    """
    為每一天打上標籤
    df 必須包含：date, close, high, low

    標籤定義：
    1 = 完美獵物（未來N日最高漲幅 > target_up_pct，且從未跌破 stop_loss_pct）
    0 = 垃圾雜訊
    """
    df = df.copy().reset_index(drop=True)
    cfg = LABEL_CONFIG

    future_days = cfg["future_days"]        # 未來觀察天數（預設5）
    target_up = cfg["target_up_pct"]        # 目標漲幅（預設5%）
    stop_loss = cfg["stop_loss_pct"]        # 停損線（預設2%）

    labels = []

    for i in range(len(df)):
        # 還沒有未來 N 天資料的直接標 NaN
        if i + future_days >= len(df):
            labels.append(np.nan)
            continue

        entry_price = df.loc[i, "close"]
        future_slice = df.loc[i+1 : i+future_days]

        # 未來最高價（最大漲幅）
        max_high = future_slice["high"].max()
        max_return = (max_high - entry_price) / entry_price

        # 未來最低價（最大跌幅）
        min_low = future_slice["low"].min()
        max_drawdown = (entry_price - min_low) / entry_price

        # 標籤判斷
        if max_return >= target_up and max_drawdown < stop_loss:
            labels.append(1)  # 完美獵物 ✅
        else:
            labels.append(0)  # 垃圾雜訊 ❌

    df["label"] = labels

    # 統計標籤分佈
    valid = df["label"].dropna()
    positive_rate = valid.mean()
    print(f"✅ 標籤完成 | 總樣本: {len(valid)} | 正樣本率: {positive_rate:.2%}")

    return df


def get_label_stats(df: pd.DataFrame) -> dict:
    """
    回傳標籤統計資訊
    """
    valid = df["label"].dropna()
    return {
        "total_samples": len(valid),
        "positive_samples": int(valid.sum()),
        "negative_samples": int((valid == 0).sum()),
        "positive_rate": float(valid.mean()),
        "config": LABEL_CONFIG,
    }


if __name__ == "__main__":
    # 測試
    import yfinance as yf
    df = yf.download("2330.TW", start="2022-01-01", end="2024-01-01")
    df.columns = [c.lower() for c in df.columns]
    df = df.reset_index()
    df = create_labels(df)
    stats = get_label_stats(df)
    print(stats)
    print(df[["date", "close", "label"]].dropna().tail(20))
