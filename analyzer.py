"""
factors/analyzer.py - 整合分析引擎
功能：
  /分析 指令時，調用資料庫所有數據做完整分析
  並將分析結果存入 analysis_log
"""
import logging
from datetime import datetime
from database.db_manager import get_conn, query_df

logger = logging.getLogger(__name__)


def get_latest_price(stock_id: str) -> dict:
    """從資料庫取得最新股價"""
    sql = """
        SELECT date, open, high, low, close, volume
        FROM daily_price WHERE stock_id=?
        ORDER BY date DESC LIMIT 1
    """
    df = query_df(sql, (stock_id,))
    if df.empty:
        return {}
    r = df.iloc[0]
    return {
        "date": r["date"], "close": r["close"],
        "open": r["open"], "high": r["high"],
        "low": r["low"], "volume": r["volume"],
    }


def get_price_history(stock_id: str, days: int = 60) -> list:
    """從資料庫取得歷史收盤價"""
    sql = """
        SELECT date, close, volume
        FROM daily_price WHERE stock_id=?
        ORDER BY date DESC LIMIT ?
    """
    df = query_df(sql, (stock_id, days))
    if df.empty:
        return []
    return df["close"].tolist()


def calc_rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    closes = list(reversed(closes))
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = [d for d in deltas[-period:] if d > 0]
    losses = [abs(d) for d in deltas[-period:] if d < 0]
    avg_gain = sum(gains) / period if gains else 0.001
    avg_loss = sum(losses) / period if losses else 0.001
    return 100 - (100 / (1 + avg_gain / avg_loss))


def analyze_technical(stock_id: str) -> dict:
    """技術面分析（從資料庫）"""
    closes_raw = get_price_history(stock_id, 65)
    if len(closes_raw) < 20:
        return {"score": 0, "note": "技術面：資料不足（未達20日）", "detail": {}}

    current = closes_raw[0]
    ma20 = sum(closes_raw[:20]) / 20
    ma60 = sum(closes_raw[:60]) / 60 if len(closes_raw) >= 60 else ma20

    # 成交量
    sql_vol = "SELECT volume FROM daily_price WHERE stock_id=? ORDER BY date DESC LIMIT 20"
    df_vol = query_df(sql_vol, (stock_id,))
    volumes = df_vol["volume"].tolist() if not df_vol.empty else []
    vol_ma20 = sum(volumes[:20]) / 20 if len(volumes) >= 20 else 1
    vol_ratio = volumes[0] / vol_ma20 if volumes and vol_ma20 > 0 else 1

    rsi = calc_rsi(closes_raw)
    bias = (current - ma20) / ma20 * 100
    ret_5d = (current / closes_raw[5] - 1) * 100 if len(closes_raw) > 5 else 0

    score = 0
    notes = []

    if current > ma20 and ma20 > ma60:
        score += 15; notes.append("股價站上20MA且多頭排列(+15)")
    elif current > ma20:
        score += 8; notes.append("股價站上20MA(+8)")
    else:
        score -= 5; notes.append("股價在20MA之下(-5)")

    if 50 <= rsi <= 70:
        score += 10; notes.append(f"RSI={rsi:.0f}動能健康(+10)")
    elif rsi > 80:
        score -= 5; notes.append(f"RSI={rsi:.0f}超買(-5)")
    elif rsi < 40:
        score -= 5; notes.append(f"RSI={rsi:.0f}偏弱(-5)")
    else:
        notes.append(f"RSI={rsi:.0f}中性")

    if vol_ratio >= 1.5:
        score += 5; notes.append(f"量能放大{vol_ratio:.1f}倍(+5)")

    return {
        "score": score,
        "note": " | ".join(notes),
        "detail": {
            "close": current, "ma20": round(ma20, 2), "ma60": round(ma60, 2),
            "rsi": round(rsi, 1), "bias": round(bias, 2),
            "vol_ratio": round(vol_ratio, 2), "ret_5d": round(ret_5d, 2),
        }
    }


def analyze_chips(stock_id: str) -> dict:
    """籌碼面分析（從資料庫）"""
    sql = """
        SELECT date, foreign_net, trust_net, total_net
        FROM institutional WHERE stock_id=?
        ORDER BY date DESC LIMIT 10
    """
    df = query_df(sql, (stock_id,))
    if df.empty:
        return {"score": 0, "note": "籌碼面：無三大法人資料", "detail": {}}

    f5 = df["foreign_net"].head(5).sum()
    t5 = df["trust_net"].head(5).sum()
    consecutive = 0
    for v in df["foreign_net"]:
        if v > 0:
            consecutive += 1
        else:
            break

    score = 0
    notes = []

    if f5 >= 2000:
        score += 20; notes.append(f"外資大買{f5:,}張連{consecutive}天(+20)")
    elif f5 >= 500:
        score += 15; notes.append(f"外資買{f5:,}張連{consecutive}天(+15)")
    elif f5 >= 0:
        score += 5; notes.append(f"外資小買{f5:,}張(+5)")
    else:
        score -= 10; notes.append(f"外資賣{abs(f5):,}張(-10)")

    if t5 > 200:
        score += 15; notes.append(f"投信買{t5:,}張(+15)")
    elif t5 > 0:
        score += 8; notes.append(f"投信小買{t5:,}張(+8)")
    elif t5 < -200:
        score -= 10; notes.append(f"投信賣{abs(t5):,}張(-10)")

    return {
        "score": score,
        "note": " | ".join(notes),
        "detail": {"foreign_5d": f5, "trust_5d": t5, "foreign_consecutive": consecutive}
    }


def analyze_fundamental(stock_id: str) -> dict:
    """基本面分析（從資料庫）"""
    sql = """
        SELECT year, month, revenue, yoy, mom
        FROM monthly_revenue WHERE stock_id=?
        ORDER BY year DESC, month DESC LIMIT 6
    """
    df = query_df(sql, (stock_id,))
    if df.empty:
        return {"score": 10, "note": "基本面：無月營收資料（給予中性分）", "detail": {}}

    latest_yoy = float(df.iloc[0]["yoy"])
    consecutive = sum(1 for _, r in df.iterrows() if float(r["yoy"]) > 0)
    score = 0
    notes = []

    if latest_yoy >= 30:
        score += 25; notes.append(f"月營收YoY+{latest_yoy:.0f}%爆發(+25)")
    elif latest_yoy >= 10:
        score += 18; notes.append(f"月營收YoY+{latest_yoy:.0f}%成長(+18)")
    elif latest_yoy >= 0:
        score += 10; notes.append(f"月營收YoY+{latest_yoy:.0f}%微增(+10)")
    else:
        score -= 10; notes.append(f"月營收YoY{latest_yoy:.0f}%衰退(-10)")

    if consecutive >= 4:
        score += 5; notes.append(f"連續{consecutive}月年增(+5)")

    return {
        "score": score,
        "note": " | ".join(notes),
        "detail": {"latest_yoy": latest_yoy, "consecutive_months": consecutive}
    }


def analyze_environment() -> dict:
    """環境面分析（大盤 + VIX）"""
    # 取 0050 代替大盤
    closes = get_price_history("0050", 65)
    if len(closes) < 20:
        return {"score": 8, "note": "環境面：資料不足給中性分", "detail": {}, "market_ok": True}

    current = closes[0]
    ma60 = sum(closes[:60]) / 60 if len(closes) >= 60 else current

    # VIX
    vix_df = query_df("SELECT vix FROM macro_daily ORDER BY date DESC LIMIT 1")
    vix = float(vix_df.iloc[0]["vix"]) if not vix_df.empty else 15.0

    if current < ma60:
        return {
            "score": -30, "market_ok": False,
            "note": f"大盤跌破季線(-30分) VIX={vix:.1f}",
            "detail": {"vix": vix, "ma60": round(ma60, 2)}
        }

    score = 15
    if vix > 25:
        score -= 8
        note = f"大盤健康但VIX={vix:.1f}偏高(+7)"
    elif vix > 20:
        score -= 3
        note = f"大盤健康VIX={vix:.1f}略高(+12)"
    else:
        note = f"大盤強勢VIX={vix:.1f}低恐慌(+15)"

    return {"score": score, "note": note, "market_ok": True, "detail": {"vix": vix}}


def full_analysis(stock_id: str) -> dict:
    """
    完整分析並存入資料庫
    回傳所有維度的分析結果
    """
    tech  = analyze_technical(stock_id)
    chip  = analyze_chips(stock_id)
    fund  = analyze_fundamental(stock_id)
    env   = analyze_environment()

    total = max(0, min(100,
        tech["score"] + chip["score"] + fund["score"] + env["score"]
    ))

    if total >= 75:   grade = "🔥 強力關注"
    elif total >= 60: grade = "✅ 值得追蹤"
    elif total >= 45: grade = "📊 普通觀望"
    else:             grade = "⬇️ 暫時迴避"

    # 取股票名稱和現價
    name = ""
    close_price = 0.0
    with get_conn() as conn:
        row = conn.execute("SELECT name FROM stocks WHERE stock_id=?", (stock_id,)).fetchone()
        if row:
            name = row["name"]
    price_info = get_latest_price(stock_id)
    close_price = price_info.get("close", 0.0)

    # 查詢歷史勝率
    win_info = None
    with get_conn() as conn:
        row2 = conn.execute(
            "SELECT win_rate, total_trades, profit_factor FROM win_rate_db WHERE stock_id=?",
            (stock_id,)
        ).fetchone()
        if row2:
            win_info = dict(row2)

    # 查詢建議策略參數
    strategy = None
    with get_conn() as conn:
        row3 = conn.execute(
            "SELECT best_stop_loss, best_take_profit, best_hold_days FROM strategy_params WHERE stock_id=?",
            (stock_id,)
        ).fetchone()
        if row3:
            strategy = dict(row3)

    # 存入分析日誌
    summary = f"T:{tech['score']} C:{chip['score']} F:{fund['score']} E:{env['score']}"
    today = datetime.now().strftime("%Y-%m-%d")
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO analysis_log
                (stock_id, date, score, tech_score, chip_score,
                 fund_score, env_score, close_price, summary)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(stock_id, date) DO UPDATE SET
                score=excluded.score, tech_score=excluded.tech_score,
                chip_score=excluded.chip_score, fund_score=excluded.fund_score,
                env_score=excluded.env_score, close_price=excluded.close_price,
                summary=excluded.summary
        """, (stock_id, today, total, tech["score"], chip["score"],
              fund["score"], env["score"], close_price, summary))

    return {
        "stock_id": stock_id, "name": name,
        "total_score": total, "grade": grade,
        "tech": tech, "chip": chip, "fund": fund, "env": env,
        "close_price": close_price,
        "win_info": win_info,
        "strategy": strategy,
        "market_ok": env["market_ok"],
        "date": today,
    }


def format_analysis_report(result: dict) -> str:
    """格式化分析報告（Telegram 用）"""
    r = result
    lines = [
        f"分析報告：{r['stock_id']} {r['name']}",
        f"現價：${r['close_price']}  日期：{r['date']}",
        f"══════════════════════",
        f"綜合評分：{r['total_score']} 分  {r['grade']}",
        f"",
        f"技術面（{r['tech']['score']}分）",
        f"  {r['tech']['note']}",
        f"",
        f"籌碼面（{r['chip']['score']}分）",
        f"  {r['chip']['note']}",
        f"",
        f"基本面（{r['fund']['score']}分）",
        f"  {r['fund']['note']}",
        f"",
        f"環境面（{r['env']['score']}分）",
        f"  {r['env']['note']}",
        f"══════════════════════",
    ]

    # 歷史勝率
    if r.get("win_info"):
        w = r["win_info"]
        lines.append(f"歷史勝率：{w['win_rate']:.0f}%（{w['total_trades']}次交易）")
        lines.append(f"盈虧比：{w['profit_factor']:.1f}x")

    # 建議策略
    if r.get("strategy"):
        s = r["strategy"]
        lines.append(f"建議停損：{s['best_stop_loss']*100:.0f}%  停利：{s['best_take_profit']*100:.0f}%")
        lines.append(f"建議持有：{s['best_hold_days']} 天")

    # 操作建議
    lines.append("")
    if not r["market_ok"]:
        lines.append("⚠️ 大盤破季線，建議暫停進場")
    elif r["total_score"] >= 70:
        lines.append(f"✅ 建議操作：輸入「建倉試算 {r['close_price']}」計算張數")
        lines.append(f"進場後輸入「買進 {r['stock_id']} 1 {r['close_price']}」開始追蹤")
    elif r["total_score"] >= 55:
        lines.append("📊 評分尚可，可小量試單，嚴守停損")
    else:
        lines.append("⬇️ 評分偏低，建議等待更好進場點")

    return "\n".join(lines)
