"""
factors/macro.py - 宏觀因子（Macro）
計算：大盤 ADL 騰落線、VIX 恐慌指數
"""
import requests
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


def fetch_vix() -> float:
    """
    從 Yahoo Finance 取得 VIX 指數
    """
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        price = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
        return float(price)
    except Exception as e:
        logger.error(f"取得 VIX 失敗: {e}")
        return None


def fetch_twse_market_breadth(date: str = None) -> dict:
    """
    從證交所取得大盤漲跌家數（計算騰落線 ADL）
    """
    from datetime import datetime
    if date is None:
        date = datetime.now().strftime("%Y%m%d")

    url = "https://www.twse.com.tw/exchangeReport/MI_INDEX"
    params = {
        "response": "json",
        "date": date,
        "type": "MS",
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()

        if data.get("stat") == "OK":
            # 解析漲跌家數
            tables = data.get("tables", [])
            for table in tables:
                if "漲" in str(table.get("title", "")):
                    fields = table.get("fields", [])
                    rows = table.get("data", [])
                    if rows:
                        row = rows[0]
                        up = int(str(row[1]).replace(",", "")) if len(row) > 1 else 0
                        down = int(str(row[4]).replace(",", "")) if len(row) > 4 else 0
                        return {
                            "date": date,
                            "advancing": up,
                            "declining": down,
                            "adl_daily": up - down,  # 單日騰落值
                        }
    except Exception as e:
        logger.error(f"取得大盤漲跌家數失敗: {e}")

    return {}


def calc_adl(days: int = 20) -> pd.DataFrame:
    """
    計算過去 N 天的累計 ADL 騰落線
    """
    from datetime import datetime, timedelta
    import time

    records = []
    today = datetime.now()

    for i in range(days):
        date = today - timedelta(days=i)
        if date.weekday() >= 5:
            continue
        date_str = date.strftime("%Y%m%d")
        record = fetch_twse_market_breadth(date_str)
        if record:
            records.append(record)
        time.sleep(0.3)

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    # 累計 ADL
    df["adl_cumulative"] = df["adl_daily"].cumsum()

    # ADL 20日斜率（判斷市場強弱）
    df["adl_slope"] = df["adl_cumulative"].diff(5)

    return df


def get_macro_snapshot() -> dict:
    """
    取得當前宏觀指標快照
    """
    vix = fetch_vix()
    breadth = fetch_twse_market_breadth()

    # VIX 評分
    vix_signal = "中性"
    if vix:
        if vix < 15:
            vix_signal = "市場貪婪（低恐慌）"
        elif vix > 25:
            vix_signal = "市場恐慌（高風險）"
        else:
            vix_signal = "中性"

    return {
        "vix": vix,
        "vix_signal": vix_signal,
        "advancing": breadth.get("advancing", 0),
        "declining": breadth.get("declining", 0),
        "adl_daily": breadth.get("adl_daily", 0),
    }


if __name__ == "__main__":
    snapshot = get_macro_snapshot()
    print(snapshot)
