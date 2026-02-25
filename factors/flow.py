"""
factors/flow.py - 籌碼因子（Flow）
爬取：外資連買天數、投信買超比例、主力分點集中度
資料來源：證交所公開資料（合規）
"""
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import logging

logger = logging.getLogger(__name__)


def fetch_institutional_investors(stock_id: str, date: str = None) -> dict:
    """
    爬取三大法人買賣超（證交所公開資料）
    stock_id: 股票代號，例如 '2330'
    date: 日期字串 'YYYYMMDD'，預設今天
    回傳：外資買賣超、投信買賣超、自營商買賣超
    """
    if date is None:
        date = datetime.now().strftime("%Y%m%d")

    url = "https://www.twse.com.tw/fund/T86"
    params = {
        "response": "json",
        "date": date,
        "selectType": "ALLBUT0999",
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()

        if data.get("stat") != "OK":
            return {}

        for row in data.get("data", []):
            if row[0] == stock_id:
                foreign_buy = int(row[2].replace(",", ""))
                foreign_sell = int(row[3].replace(",", ""))
                foreign_net = int(row[4].replace(",", ""))

                trust_net = int(row[10].replace(",", ""))
                dealer_net = int(row[14].replace(",", ""))

                return {
                    "date": date,
                    "stock_id": stock_id,
                    "foreign_net": foreign_net,    # 外資買賣超（張）
                    "trust_net": trust_net,         # 投信買賣超（張）
                    "dealer_net": dealer_net,       # 自營商買賣超（張）
                }
    except Exception as e:
        logger.error(f"爬取三大法人失敗 {stock_id}: {e}")

    return {}


def calc_flow_factors(stock_id: str, days: int = 30) -> pd.DataFrame:
    """
    計算近 N 天的籌碼因子
    回傳含以下欄位的 DataFrame：
    - foreign_consecutive_days: 外資連買/連賣天數（正=連買，負=連賣）
    - trust_vol_ratio: 投信買超佔總成交量比例
    - foreign_net_ma5: 外資買超5日均值
    """
    records = []
    today = datetime.now()

    # 回抓過去 N 個交易日
    for i in range(days):
        date = today - timedelta(days=i)
        # 跳過週末
        if date.weekday() >= 5:
            continue
        date_str = date.strftime("%Y%m%d")
        record = fetch_institutional_investors(stock_id, date_str)
        if record:
            records.append(record)
        time.sleep(0.3)  # 避免被封鎖

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    # 外資連買天數計算
    df["foreign_consecutive_days"] = 0
    consecutive = 0
    for i, row in df.iterrows():
        if row["foreign_net"] > 0:
            consecutive = consecutive + 1 if consecutive > 0 else 1
        elif row["foreign_net"] < 0:
            consecutive = consecutive - 1 if consecutive < 0 else -1
        else:
            consecutive = 0
        df.at[i, "foreign_consecutive_days"] = consecutive

    # 外資買超5日均值
    df["foreign_net_ma5"] = df["foreign_net"].rolling(5).mean()

    # 投信買超佔比（需搭配成交量資料，此處用近似值）
    df["trust_net_ma5"] = df["trust_net"].rolling(5).mean()

    return df


if __name__ == "__main__":
    df = calc_flow_factors("2330", days=20)
    print(df[["date", "foreign_net", "foreign_consecutive_days", "trust_net"]].tail(10))
