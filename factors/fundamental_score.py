"""
factors/fundamental_score.py - 基本面自動評分（0~100分）
評分維度：月營收成長、EPS趨勢、本益比位階、ROE
"""
import requests
import logging
from datetime import datetime
from database.db_manager import safe_request, query_df

logger = logging.getLogger(__name__)


def fetch_eps_history(stock_id: str) -> list:
    """取得近四季 EPS"""
    url = f"https://www.twse.com.tw/exchangeReport/BWIBBU_d"
    params = {"response": "json", "stockNo": stock_id}

    resp = safe_request(url, params)
    if not resp:
        return []

    try:
        data = resp.json()
        if data.get("stat") == "OK":
            return data.get("data", [])[:8]
    except Exception as e:
        logger.error(f"EPS 爬取失敗 {stock_id}: {e}")
    return []


def calc_fundamental_score(stock_id: str) -> dict:
    """
    計算基本面綜合評分（0~100）
    """
    score = 50
    details = []

    # 從資料庫取月營收
    sql = """
        SELECT year, month, revenue, yoy, mom
        FROM monthly_revenue
        WHERE stock_id = ?
        ORDER BY year DESC, month DESC
        LIMIT 6
    """
    rev_df = query_df(sql, (stock_id,))

    if not rev_df.empty:
        # 1. 月營收 YoY（年增率）
        latest_yoy = rev_df.iloc[0]["yoy"]
        if latest_yoy >= 30:
            score += 20
            details.append(f"✅ 月營收 YoY +{latest_yoy:.1f}%（爆發成長 +20分）")
        elif latest_yoy >= 10:
            score += 12
            details.append(f"✅ 月營收 YoY +{latest_yoy:.1f}%（穩健成長 +12分）")
        elif latest_yoy >= 0:
            score += 5
            details.append(f"📊 月營收 YoY +{latest_yoy:.1f}%（微幅成長 +5分）")
        elif latest_yoy >= -10:
            score -= 8
            details.append(f"⚠️ 月營收 YoY {latest_yoy:.1f}%（小幅衰退 -8分）")
        else:
            score -= 20
            details.append(f"🚫 月營收 YoY {latest_yoy:.1f}%（嚴重衰退 -20分）")

        # 2. 連續成長月數
        consecutive_growth = 0
        for _, row in rev_df.iterrows():
            if row["yoy"] > 0:
                consecutive_growth += 1
            else:
                break
        if consecutive_growth >= 6:
            score += 10
            details.append(f"✅ 連續 {consecutive_growth} 個月營收年增（+10分）")
        elif consecutive_growth >= 3:
            score += 5
            details.append(f"✅ 連續 {consecutive_growth} 個月營收年增（+5分）")

        # 3. 月增率 MoM
        latest_mom = rev_df.iloc[0]["mom"]
        if latest_mom >= 10:
            score += 8
            details.append(f"✅ 月營收 MoM +{latest_mom:.1f}%（月份加速 +8分）")
        elif latest_mom < -15:
            score -= 5
            details.append(f"⚠️ 月營收 MoM {latest_mom:.1f}%（月份下滑 -5分）")

    # 4. 本益比位階評分
    eps_data = fetch_eps_history(stock_id)
    if eps_data:
        try:
            pe = float(eps_data[0][4]) if eps_data[0][4] != "-" else None
            if pe:
                if pe < 10:
                    score += 15
                    details.append(f"✅ 本益比 {pe:.1f}x（低估 +15分）")
                elif pe < 15:
                    score += 8
                    details.append(f"✅ 本益比 {pe:.1f}x（合理偏低 +8分）")
                elif pe < 25:
                    score += 0
                    details.append(f"📊 本益比 {pe:.1f}x（合理）")
                elif pe < 40:
                    score -= 8
                    details.append(f"⚠️ 本益比 {pe:.1f}x（偏高 -8分）")
                else:
                    score -= 15
                    details.append(f"🚫 本益比 {pe:.1f}x（過度高估 -15分）")
        except:
            pass

    score = max(0, min(100, score))

    if score >= 75:
        grade = "🔥 基本面優異"
    elif score >= 60:
        grade = "✅ 基本面良好"
    elif score >= 40:
        grade = "📊 基本面普通"
    elif score >= 25:
        grade = "⚠️ 基本面偏弱"
    else:
        grade = "🚫 基本面差"

    return {
        "stock_id": stock_id,
        "score": score,
        "grade": grade,
        "details": details,
    }
