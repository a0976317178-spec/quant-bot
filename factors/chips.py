"""
factors/chips.py - 籌碼面強化分析
新增：融資餘額、集保分散度、主力連買強度評分
"""
import requests
import pandas as pd
import logging
from datetime import datetime, timedelta
from database.db_manager import safe_request, get_conn, query_df

logger = logging.getLogger(__name__)


def fetch_margin_trading(stock_id: str, date: str = None) -> dict:
    """
    爬取融資融券餘額（證交所）
    融資增加但股價漲 = 主力出貨信號（危險）
    融資減少但股價漲 = 主力吃貨（健康）
    """
    if not date:
        date = datetime.now().strftime("%Y%m%d")

    url = "https://www.twse.com.tw/exchangeReport/MI_MARGN"
    params = {"response": "json", "date": date, "selectType": "ALL"}

    resp = safe_request(url, params)
    if not resp:
        return {}

    try:
        data = resp.json()
        if data.get("stat") != "OK":
            return {}

        for row in data.get("data", []):
            if row[0].strip() == stock_id:
                def to_int(v):
                    try: return int(str(v).replace(",", ""))
                    except: return 0

                margin_balance = to_int(row[6])    # 融資餘額（張）
                short_balance = to_int(row[12])    # 融券餘額（張）
                margin_change = to_int(row[4])     # 融資增減
                short_change = to_int(row[10])     # 融券增減

                return {
                    "stock_id": stock_id,
                    "date": date,
                    "margin_balance": margin_balance,
                    "short_balance": short_balance,
                    "margin_change": margin_change,
                    "short_change": short_change,
                    "short_ratio": round(short_balance / margin_balance * 100, 2) if margin_balance > 0 else 0,
                }
    except Exception as e:
        logger.error(f"融資融券爬取失敗 {stock_id}: {e}")

    return {}


def fetch_stock_distribution(stock_id: str) -> dict:
    """
    爬取集保戶股票分散統計（每週更新）
    散戶持股集中度 → 越分散越健康
    """
    url = "https://www.tdcc.com.tw/smWeb/QryStockAjax.do"
    params = {
        "scaDt": datetime.now().strftime("%Y%m%d"),
        "SqlMethod": "StockNo",
        "StockNo": stock_id,
        "REQ_TYPE": "",
    }

    try:
        resp = safe_request(url, params, method="POST")
        if not resp:
            return {}

        data = resp.json()
        rows = data if isinstance(data, list) else []

        total_holders = 0
        small_holders = 0   # 持股 1-999 張（散戶）
        big_holders = 0     # 持股 1000 張以上（大戶）

        for row in rows:
            holders = int(str(row.get("HolderCnt", 0)).replace(",", "") or 0)
            shares = str(row.get("Level", ""))
            total_holders += holders

            if "1-999" in shares or int(shares.split("-")[0].replace(",", "") or 0) < 1000:
                small_holders += holders
            else:
                big_holders += holders

        return {
            "stock_id": stock_id,
            "total_holders": total_holders,
            "small_holders": small_holders,
            "big_holders": big_holders,
            "big_holder_ratio": round(big_holders / total_holders * 100, 2) if total_holders > 0 else 0,
        }

    except Exception as e:
        logger.error(f"集保分散統計爬取失敗 {stock_id}: {e}")
        return {}


def calc_chips_score(stock_id: str, days: int = 20) -> dict:
    """
    計算綜合籌碼評分（0~100）
    """
    score = 50  # 基礎分
    details = []

    # 從資料庫取籌碼歷史
    sql = """
        SELECT date, foreign_net, trust_net, total_net
        FROM institutional
        WHERE stock_id = ?
        ORDER BY date DESC LIMIT ?
    """
    df = query_df(sql, (stock_id, days))

    if df.empty:
        return {"score": score, "grade": "資料不足", "details": ["籌碼歷史資料不足"]}

    # 1. 外資連買天數評分
    foreign_consecutive = 0
    for net in df["foreign_net"]:
        if net > 0:
            foreign_consecutive += 1
        else:
            break

    if foreign_consecutive >= 5:
        score += 20
        details.append(f"✅ 外資連買 {foreign_consecutive} 天（+20分）")
    elif foreign_consecutive >= 3:
        score += 10
        details.append(f"✅ 外資連買 {foreign_consecutive} 天（+10分）")
    elif foreign_consecutive == 0:
        foreign_sell = sum(1 for n in df["foreign_net"][:5] if n < 0)
        if foreign_sell >= 3:
            score -= 15
            details.append(f"⚠️ 外資近5日賣超 {foreign_sell} 天（-15分）")

    # 2. 投信買超評分
    trust_recent = df["trust_net"].head(5).sum()
    if trust_recent > 0:
        score += 10
        details.append(f"✅ 投信近5日買超 {trust_recent:,} 張（+10分）")
    elif trust_recent < 0:
        score -= 10
        details.append(f"⚠️ 投信近5日賣超 {abs(trust_recent):,} 張（-10分）")

    # 3. 三大法人合力評分
    total_recent = df["total_net"].head(3).sum()
    if total_recent > 500:
        score += 15
        details.append(f"✅ 三大法人近3日合計買超 {total_recent:,} 張（+15分）")
    elif total_recent < -500:
        score -= 15
        details.append(f"⚠️ 三大法人近3日合計賣超 {abs(total_recent):,} 張（-15分）")

    # 4. 融資評分
    margin = fetch_margin_trading(stock_id)
    if margin:
        if margin.get("margin_change", 0) < 0:
            score += 5
            details.append("✅ 融資減少（籌碼乾淨 +5分）")
        elif margin.get("margin_change", 0) > 1000:
            score -= 10
            details.append("⚠️ 融資大增（散戶追高風險 -10分）")

    score = max(0, min(100, score))

    if score >= 75:
        grade = "🔥 強烈買進"
    elif score >= 60:
        grade = "✅ 偏多"
    elif score >= 40:
        grade = "⬜ 中性"
    elif score >= 25:
        grade = "⚠️ 偏空"
    else:
        grade = "🚫 迴避"

    return {
        "stock_id": stock_id,
        "score": score,
        "grade": grade,
        "details": details,
        "foreign_consecutive": foreign_consecutive,
        "margin_data": margin,
    }
