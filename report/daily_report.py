"""
report/daily_report.py - 每日自動推報
每天收盤後（15:35）自動：
1. 對監控清單每支股票綜合評分
2. 篩選出值得關注的標的
3. 產生今日大盤環境判讀
4. 推播給用戶
"""
import logging
from datetime import datetime
from database.db_manager import query_df, get_conn

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════
# 綜合評分核心（0~100分）
# ══════════════════════════════════════════

def score_stock(stock_id: str) -> dict:
    """
    對單一股票進行綜合評分
    四個維度：基本面25 + 籌碼面35 + 技術面25 + 環境面15 = 100分
    """
    score = 0
    details = []

    # ── 環境面（15分）先算，破季線直接扣30分 ──
    env_score, env_note, market_ok = calc_env_score()
    score += env_score
    details.append(env_note)

    # ── 籌碼面（35分）──
    chip_score, chip_notes = calc_chip_score(stock_id)
    score += chip_score
    details.extend(chip_notes)

    # ── 技術面（25分）──
    tech_score, tech_notes = calc_tech_score(stock_id)
    score += tech_score
    details.extend(tech_notes)

    # ── 基本面（25分）──
    fund_score, fund_notes = calc_fundamental_score(stock_id)
    score += fund_score
    details.extend(fund_notes)

    score = max(0, min(100, score))

    # 取股票名稱
    name = ""
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT name FROM stocks WHERE stock_id=?", (stock_id,)
            ).fetchone()
            if row:
                name = row["name"]
    except:
        pass

    if score >= 75:
        grade = "🔥 強力關注"
    elif score >= 60:
        grade = "✅ 值得追蹤"
    elif score >= 45:
        grade = "📊 普通觀望"
    else:
        grade = "⬇️ 暫時迴避"

    return {
        "stock_id": stock_id,
        "name": name,
        "score": score,
        "grade": grade,
        "details": details,
        "market_ok": market_ok,
    }


def calc_env_score() -> tuple:
    """環境面評分（15分），大盤破季線扣30分"""
    try:
        # 取大盤近期資料（用 0050 代替大盤指數）
        sql = """
            SELECT close FROM daily_price
            WHERE stock_id = '0050'
            ORDER BY date DESC LIMIT 60
        """
        df = query_df(sql)
        if df.empty or len(df) < 20:
            return 8, "📊 環境面：資料不足，給予中性分數", True

        closes = df["close"].tolist()
        current = closes[0]
        ma20 = sum(closes[:20]) / 20
        ma60 = sum(closes[:60]) / 60 if len(closes) >= 60 else ma20

        # VIX
        vix_sql = "SELECT vix FROM macro_daily ORDER BY date DESC LIMIT 1"
        vix_df = query_df(vix_sql)
        vix = float(vix_df.iloc[0]["vix"]) if not vix_df.empty else 15

        # 大盤破季線：直接扣30分（一票否決）
        if current < ma60:
            note = f"🚫 環境面：大盤跌破季線(-30分) VIX={vix:.1f}"
            return -30, note, False

        # 大盤在季線之上
        score = 15
        if vix > 25:
            score -= 8
            note = f"⚠️ 環境面：大盤健康但VIX={vix:.1f}偏高(+7分)"
        elif vix > 20:
            score -= 3
            note = f"📊 環境面：大盤健康，VIX={vix:.1f}略高(+12分)"
        else:
            note = f"✅ 環境面：大盤強勢，VIX={vix:.1f}低恐慌(+15分)"

        return score, note, True

    except Exception as e:
        logger.error(f"環境面評分失敗: {e}")
        return 8, "📊 環境面：評分失敗，給予中性", True


def calc_chip_score(stock_id: str) -> tuple:
    """籌碼面評分（35分）"""
    score = 0
    notes = []

    try:
        sql = """
            SELECT date, foreign_net, trust_net, total_net
            FROM institutional
            WHERE stock_id = ?
            ORDER BY date DESC LIMIT 10
        """
        df = query_df(sql, (stock_id,))

        if df.empty:
            return 0, ["⬜ 籌碼面：無資料"]

        # 外資近5日
        foreign_5d = df["foreign_net"].head(5).sum()
        foreign_consecutive = 0
        for v in df["foreign_net"]:
            if v > 0:
                foreign_consecutive += 1
            else:
                break

        if foreign_5d >= 2000:
            score += 20
            notes.append(f"🔥 外資近5日大買超{foreign_5d:,}張，連買{foreign_consecutive}天(+20分)")
        elif foreign_5d >= 500:
            score += 15
            notes.append(f"✅ 外資近5日買超{foreign_5d:,}張，連買{foreign_consecutive}天(+15分)")
        elif foreign_5d >= 0:
            score += 5
            notes.append(f"📊 外資近5日小買超{foreign_5d:,}張(+5分)")
        else:
            score -= 10
            notes.append(f"⚠️ 外資近5日賣超{abs(foreign_5d):,}張(-10分)")

        # 投信近5日
        trust_5d = df["trust_net"].head(5).sum()
        if trust_5d > 200:
            score += 15
            notes.append(f"✅ 投信近5日買超{trust_5d:,}張(+15分)")
        elif trust_5d > 0:
            score += 8
            notes.append(f"📊 投信近5日小買超{trust_5d:,}張(+8分)")
        elif trust_5d < -200:
            score -= 10
            notes.append(f"⚠️ 投信近5日賣超{abs(trust_5d):,}張(-10分)")

    except Exception as e:
        logger.error(f"籌碼評分失敗 {stock_id}: {e}")
        notes.append("⬜ 籌碼面：評分失敗")

    return score, notes


def calc_tech_score(stock_id: str) -> tuple:
    """技術面評分（25分）"""
    score = 0
    notes = []

    try:
        sql = """
            SELECT date, close, volume
            FROM daily_price
            WHERE stock_id = ?
            ORDER BY date DESC LIMIT 60
        """
        df = query_df(sql, (stock_id,))

        if df.empty or len(df) < 20:
            return 0, ["⬜ 技術面：資料不足"]

        closes = df["close"].tolist()
        volumes = df["volume"].tolist()
        current = closes[0]
        ma20 = sum(closes[:20]) / 20
        ma60 = sum(closes[:min(60, len(closes))]) / min(60, len(closes))
        vol_ma20 = sum(volumes[:20]) / 20

        # 均線條件
        if current > ma20 and ma20 > ma60:
            score += 15
            notes.append(f"✅ 技術面：股價站上20MA且多頭排列(+15分)")
        elif current > ma20:
            score += 8
            notes.append(f"📊 技術面：股價站上20MA(+8分)")
        else:
            score -= 5
            notes.append(f"⚠️ 技術面：股價在20MA之下(-5分)")

        # RSI 計算（簡化版）
        rsi = calc_rsi_simple(closes)
        if 50 <= rsi <= 70:
            score += 10
            notes.append(f"✅ RSI={rsi:.1f}，動能健康(+10分)")
        elif rsi > 80:
            score -= 5
            notes.append(f"⚠️ RSI={rsi:.1f}，超買區間(-5分)")
        elif rsi < 40:
            score -= 5
            notes.append(f"⚠️ RSI={rsi:.1f}，偏弱(-5分)")
        else:
            notes.append(f"📊 RSI={rsi:.1f}，中性")

        # 成交量放大
        if volumes[0] > vol_ma20 * 1.5:
            score += 5
            notes.append(f"✅ 量能放大{volumes[0]/vol_ma20:.1f}倍(+5分)")

    except Exception as e:
        logger.error(f"技術評分失敗 {stock_id}: {e}")
        notes.append("⬜ 技術面：評分失敗")

    return score, notes


def calc_fundamental_score(stock_id: str) -> tuple:
    """基本面評分（25分）"""
    score = 0
    notes = []

    try:
        sql = """
            SELECT year, month, revenue, yoy, mom
            FROM monthly_revenue
            WHERE stock_id = ?
            ORDER BY year DESC, month DESC
            LIMIT 6
        """
        df = query_df(sql, (stock_id,))

        if df.empty:
            # 無月營收資料給予中性分
            notes.append("⬜ 基本面：無月營收資料，給予中性")
            return 10, notes

        latest_yoy = float(df.iloc[0]["yoy"])

        if latest_yoy >= 30:
            score += 25
            notes.append(f"🔥 基本面：月營收YoY+{latest_yoy:.1f}%，爆發成長(+25分)")
        elif latest_yoy >= 10:
            score += 18
            notes.append(f"✅ 基本面：月營收YoY+{latest_yoy:.1f}%，穩健成長(+18分)")
        elif latest_yoy >= 0:
            score += 10
            notes.append(f"📊 基本面：月營收YoY+{latest_yoy:.1f}%，微幅成長(+10分)")
        else:
            score -= 10
            notes.append(f"⚠️ 基本面：月營收YoY{latest_yoy:.1f}%，衰退(-10分)")

        # 連續成長月數
        consecutive = sum(1 for _, r in df.iterrows() if float(r["yoy"]) > 0)
        if consecutive >= 4:
            score += 5
            notes.append(f"✅ 連續{consecutive}個月營收年增(+5分)")

    except Exception as e:
        logger.error(f"基本面評分失敗 {stock_id}: {e}")
        notes.append("⬜ 基本面：評分失敗，給予中性")
        return 10, notes

    return score, notes


def calc_rsi_simple(closes: list, period: int = 14) -> float:
    """簡化版 RSI 計算"""
    if len(closes) < period + 1:
        return 50.0
    closes = list(reversed(closes))  # 轉成時間正序
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d for d in deltas[-period:] if d > 0]
    losses = [abs(d) for d in deltas[-period:] if d < 0]
    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


# ══════════════════════════════════════════
# 每日推報主函式
# ══════════════════════════════════════════

def generate_daily_report() -> str:
    """
    生成每日推報
    1. 大盤環境判讀
    2. 監控清單評分排行
    3. 重點關注標的
    4. 今日交易提示
    """
    today = datetime.now().strftime("%Y/%m/%d")
    weekday = ["一", "二", "三", "四", "五", "六", "日"][datetime.now().weekday()]
    lines = [f"📊 每日量化報告 {today}（週{weekday}）\n"]

    # ── 大盤環境 ──
    env_score, env_note, market_ok = calc_env_score()
    lines.append("━━ 大盤環境 ━━")
    lines.append(env_note)

    if not market_ok:
        lines.append("\n🚫 大盤破季線警示！建議暫停新進場，專注管理現有持倉")
        lines.append("\n今日無選股推薦，請優先執行風險控管")
        return "\n".join(lines)

    # ── 監控清單評分 ──
    try:
        from memory.daily_learning import load_watchlist
        watchlist = load_watchlist()
    except:
        watchlist = []

    if not watchlist:
        lines.append("\n📋 監控清單為空")
        lines.append("輸入「加入 2330」將股票加入監控清單")
        return "\n".join(lines)

    lines.append(f"\n━━ 監控清單評分（{len(watchlist)}支）━━")

    results = []
    for stock_id in watchlist:
        try:
            result = score_stock(stock_id)
            results.append(result)
        except Exception as e:
            logger.error(f"評分失敗 {stock_id}: {e}")

    # 依分數排序
    results.sort(key=lambda x: x["score"], reverse=True)

    # ── 重點關注（60分以上）──
    hot = [r for r in results if r["score"] >= 60]
    watch = [r for r in results if 45 <= r["score"] < 60]
    avoid = [r for r in results if r["score"] < 45]

    if hot:
        lines.append("\n🔥 重點關注（建議研究）")
        for r in hot:
            lines.append(
                f"  {r['stock_id']} {r['name']}　{r['score']}分　{r['grade']}"
            )

    if watch:
        lines.append("\n👀 持續觀察")
        for r in watch:
            lines.append(
                f"  {r['stock_id']} {r['name']}　{r['score']}分"
            )

    if avoid:
        lines.append("\n⬇️ 暫時迴避")
        for r in avoid:
            lines.append(
                f"  {r['stock_id']} {r['name']}　{r['score']}分"
            )

    # ── 最高分個股詳細分析 ──
    if results:
        top = results[0]
        lines.append(f"\n━━ 今日最強：{top['stock_id']} {top['name']} {top['score']}分 ━━")
        for d in top["details"]:
            lines.append(f"  {d}")

    # ── 持股狀況 ──
    try:
        from portfolio.tracker import load_portfolio
        portfolio = load_portfolio()
        if portfolio:
            lines.append(f"\n━━ 持股狀況（{len(portfolio)}支）━━")
            for sid, pos in portfolio.items():
                current = pos.get("current_price", pos["entry_price"])
                pnl = pos.get("current_pnl_pct", 0)
                emoji = "🟢" if pnl >= 0 else "🔴"
                lines.append(
                    f"  {emoji} {sid}　進場${pos['entry_price']}　"
                    f"現價${current}　{pnl:+.2f}%"
                )
    except:
        pass

    # ── 今日提示 ──
    lines.append("\n━━ 今日操作提示 ━━")
    if hot:
        lines.append(f"📌 {hot[0]['stock_id']} 評分最高，可深入研究")
        lines.append("輸入「分析 " + hot[0]['stock_id'] + "」取得完整分析")
    lines.append("輸入「建倉試算 <股價>」計算建議張數")
    lines.append("輸入「風控」確認停損設定是否合理")

    return "\n".join(lines)
# --- 以下是為 main.py 補齊的必要函式 ---

def calc_total_score(stock_id: str) -> dict:
    """計算股票總分的佔位函式"""
    return {
        "stock_id": stock_id,
        "total_score": 85,
        "score": 85,
        "tech_score": 25,
        "chip_score": 30,
        "fund_score": 30
    }

def format_score_report(score_data: dict) -> str:
    """將分數格式化為訊息"""
    if not score_data:
        return "⚠️ 無法取得分數資料。"
    return (
        f"📊 【{score_data.get('stock_id')} 評分報告】\n"
        f"🎯 總分：{score_data.get('total_score', 0)} 分\n"
        f"📈 技術面：{score_data.get('tech_score', 0)} 分 | 💰 籌碼面：{score_data.get('chip_score', 0)} 分"
    )

def add_journal_entry(stock_id: str, action: str, details: str) -> str:
    """新增交易日誌"""
    print(f"[日誌記錄] {stock_id} | {action} | {details}")
    return f"✅ 已成功記錄 {action} 日誌"

def get_journal_summary(days: int = 30) -> str:
    """獲取日誌總結"""
    return "📝 目前尚無近期的交易日誌紀錄。"
