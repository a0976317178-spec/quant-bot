"""
factors/fundamental.py - 基本面因子（Fundamental）
爬取：月營收 YoY/MoM、近四季 EPS、本益比
資料來源：公開資訊觀測站
"""
import requests
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


def fetch_monthly_revenue(stock_id: str) -> pd.DataFrame:
    """
    爬取月營收資料（公開資訊觀測站）
    回傳含 YoY、MoM 的 DataFrame
    """
    url = "https://mops.twse.com.tw/nas/t21/sii/t21sc03_{year}_{month}_0.html"

    from datetime import datetime
    now = datetime.now()
    year = now.year - 1911  # 民國年
    month = now.month - 1 if now.month > 1 else 12

    try:
        resp = requests.get(
            url.format(year=year, month=month),
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10
        )
        resp.encoding = "big5"
        tables = pd.read_html(resp.text)

        for table in tables:
            # 找到包含股票代號的表格
            table.columns = [str(c) for c in table.columns]
            if len(table.columns) >= 8:
                table = table.dropna(subset=[table.columns[0]])
                match = table[table.iloc[:, 0].astype(str) == stock_id]
                if not match.empty:
                    row = match.iloc[0]
                    return {
                        "stock_id": stock_id,
                        "revenue_this_month": float(str(row.iloc[2]).replace(",", "") or 0),
                        "revenue_last_month": float(str(row.iloc[3]).replace(",", "") or 0),
                        "revenue_last_year_month": float(str(row.iloc[4]).replace(",", "") or 0),
                        "mom": float(str(row.iloc[5]).replace("%", "") or 0),  # 月增率
                        "yoy": float(str(row.iloc[6]).replace("%", "") or 0),  # 年增率
                    }
    except Exception as e:
        logger.error(f"爬取月營收失敗 {stock_id}: {e}")

    return {}


def fetch_eps_pe(stock_id: str) -> dict:
    """
    從 TWSE API 取得近四季 EPS 與本益比
    """
    url = f"https://www.twse.com.tw/exchangeReport/BWIBBU_d"
    params = {
        "response": "json",
        "stockNo": stock_id,
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()

        if data.get("stat") == "OK" and data.get("data"):
            latest = data["data"][0]
            return {
                "stock_id": stock_id,
                "pe_ratio": float(latest[4]) if latest[4] != "-" else None,
                "pb_ratio": float(latest[5]) if latest[5] != "-" else None,
                "dividend_yield": float(latest[2]) if latest[2] != "-" else None,
            }
    except Exception as e:
        logger.error(f"爬取本益比失敗 {stock_id}: {e}")

    return {}


def calc_fundamental_factors(stock_id: str) -> dict:
    """
    整合所有基本面因子
    """
    revenue_data = fetch_monthly_revenue(stock_id)
    pe_data = fetch_eps_pe(stock_id)

    factors = {}
    factors.update(revenue_data)
    factors.update(pe_data)

    # 基本面評分（簡易版）
    score = 0
    if factors.get("yoy", 0) > 10:
        score += 2
    elif factors.get("yoy", 0) > 0:
        score += 1

    if factors.get("mom", 0) > 5:
        score += 1

    factors["fundamental_score"] = score

    return factors


if __name__ == "__main__":
    result = calc_fundamental_factors("2330")
    print(result)
