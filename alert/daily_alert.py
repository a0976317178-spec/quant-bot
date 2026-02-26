"""
alert/daily_alert.py - 每日選股提醒
功能：
  1. 08:30 早盤提醒：昨收盤選出的買進訊號，供開盤參考
  2. 15:30 收盤提醒：今日收盤後最新訊號，供明日參考
  3. 過濾條件：成交量 >= 3000張、上市+上櫃、大盤季線之上
  4. 推送格式：評分+進場區間+停損停利
"""
import logging
from datetime import datetime
from database.db_manager import query_df, get_conn

logger = logging.getLogger(__name__)

# ── 設定 ─────────────────────────────────────────────────
MIN_VOLUME_LOTS   = 3000    # 最低成交量（張）
MIN_SCORE         = 60      # 最低評分
MAX_RESULTS       = 8       # 最多推送幾支
MIN_PRICE         = 10.0    # 過濾低價股（元）
MAX_PRICE         = 2000.0  # 過濾超高價股（非必要）


def _get_all_stock_ids() -> list:
    """取得全市場上市+上櫃股票代號"""
    try:
        df = query_df("SELECT stock_id FROM stocks WHERE stock_id GLOB '[0-9][0-9][0-9][0-9]'")
        if df.empty:
            return []
        return df["stock_id"].tolist()
    except Exception as e:
        logger.error(f"取得股票清單失敗: {e}")
        return []


def _check_market_ok() -> tuple:
    """
    確認大盤季線狀態
    回傳 (is_ok, ma60, current, vix)
    """
    try:
        from factors.analyzer import analyze_environment
        env = analyze_environment()
        market_ok = env.get("market_ok", True)
        ma60      = env.get("detail", {}).get("ma60", 0)
        current   = env.get("detail", {}).get("current", 0)
        vix       = env.get("detail", {}).get("vix", 15)
        return market_ok, ma60, current, vix
    except Exception as e:
        logger.warning(f"大盤狀態確認失敗，預設為健康: {e}")
        return True, 0, 0, 15


def _quick_score(stock_id: str) -> dict:
    """
    快速評分（比 full_analysis 快，只查必要數據）
    重點過濾：成交量、均線、RSI
    """
    try:
        # 取最近65天收盤價和成交量
        sql = """
            SELECT date, close, volume
            FROM daily_price
            WHERE stock_id = ?
            ORDER BY date DESC LIMIT 65
        """
        df = query_df(sql, (stock_id,))
        if len(df) < 20:
            return None

        closes  = df["close"].tolist()
        volumes = [int(v) // 1000 for v in df["volume"].tolist()]  # 轉為張

        current   = closes[0]
        vol_today = volumes[0]

        # ① 成交量過濾（最重要）
        if vol_today < MIN_VOLUME_LOTS:
            return None

        # ② 價格過濾
        if current < MIN_PRICE or current > MAX_PRICE:
            return None

        ma5  = sum(closes[:5])  / 5
        ma20 = sum(closes[:20]) / 20
        ma60 = sum(closes[:60]) / 60 if len(closes) >= 60 else ma20
        vol_ma20 = sum(volumes[:20]) / 20 if len(volumes) >= 20 else 1

        # ③ 基本趨勢過濾（站上均線）
        if current < ma20:
            return None

        # RSI
        from factors.analyzer import calc_rsi
        rsi = calc_rsi(closes)

        # RSI 過濾（排除超買）
        if rsi > 75:
            return None

        # 評分
        score = 0

        # 均線多頭排列
        if current > ma20 and ma20 > ma60:
            score += 25
        elif current > ma20:
            score += 12

        # RSI 動能
        if 45 <= rsi <= 65:
            score += 20
        elif 40 <= rsi < 45 or 65 < rsi <= 75:
            score += 10

        # 量能
        vol_ratio = vol_today / vol_ma20 if vol_ma20 > 0 else 1
        if vol_ratio >= 2.0:
            score += 20   # 爆量
        elif vol_ratio >= 1.5:
            score += 12   # 放量
        elif vol_ratio >= 0.8:
            score += 5    # 正常量

        # 乖離率（不能太偏）
        bias = (current - ma20) / ma20 * 100
        if -3 <= bias <= 5:
            score += 15   # 剛突破均線，最佳買點
        elif 5 < bias <= 10:
            score += 8
        elif bias > 15:
            score -= 10   # 漲過頭

        # 5日動能
        ret_5d = (current / closes[5] - 1) * 100 if len(closes) > 5 else 0
        if 0 < ret_5d <= 5:
            score += 10
        elif ret_5d > 5:
            score += 5

        # 取股票名稱
        name = ""
        with get_conn() as conn:
            row = conn.execute(
                "SELECT name FROM stocks WHERE stock_id=?", (stock_id,)
            ).fetchone()
            if row:
                name = row["name"]

        # 計算建議進場區間
        entry_low  = round(current * 0.99, 1)   # 現價 -1%
        entry_high = round(current * 1.01, 1)   # 現價 +1%
        stop_loss  = round(current * 0.95, 1)   # -5%
        target     = round(current * 1.10, 1)   # +10%

        return {
            "stock_id":    stock_id,
            "name":        name,
            "score":       score,
            "close":       current,
            "vol_today":   vol_today,
            "vol_ratio":   round(vol_ratio, 1),
            "rsi":         round(rsi, 1),
            "ma20":        round(ma20, 2),
            "ma60":        round(ma60, 2),
            "bias":        round(bias, 1),
            "ret_5d":      round(ret_5d, 1),
            "entry_low":   entry_low,
            "entry_high":  entry_high,
            "stop_loss":   stop_loss,
            "target":      target,
        }

    except Exception as e:
        logger.debug(f"{stock_id} 評分失敗: {e}")
        return None


def run_daily_scan(mode: str = "close") -> str:
    """
    執行全市場掃描
    mode: "open"  = 08:30 早盤提醒
          "close" = 15:30 收盤提醒
    回傳格式化的 Telegram 訊息
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    logger.info(f"開始全市場掃描 [{mode}] {now}")

    # 確認大盤狀態
    market_ok, ma60, mkt_current, vix = _check_market_ok()

    # 大盤破季線 → 不推買進訊號
    if not market_ok:
        return (
            f"⚠️ 全市場掃描結果\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📅 {now}\n\n"
            f"🚨 大盤跌破季線（{mkt_current:.0f} < {ma60:.0f}）\n"
            f"VIX={vix:.1f}，市場恐慌\n\n"
            f"今日不推買進訊號，建議空手觀望\n"
            f"等大盤重回季線之上再進場"
        )

    # 取得所有股票
    all_ids = _get_all_stock_ids()
    if not all_ids:
        return "❌ 無法取得股票清單，請先執行「更新清單」"

    logger.info(f"開始掃描 {len(all_ids)} 支股票...")

    # 快速掃描
    candidates = []
    for sid in all_ids:
        result = _quick_score(sid)
        if result and result["score"] >= MIN_SCORE:
            candidates.append(result)

    # 排序：評分優先，同分則量能優先
    candidates.sort(key=lambda x: (x["score"], x["vol_ratio"]), reverse=True)
    top = candidates[:MAX_RESULTS]

    logger.info(f"掃描完成：{len(candidates)} 支符合條件，取前 {len(top)} 支")

    if not top:
        return (
            f"📊 全市場掃描結果\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📅 {now}\n"
            f"大盤：季線之上 VIX={vix:.1f}\n\n"
            f"今日無符合條件的股票\n"
            f"（掃描 {len(all_ids)} 支，最低評分 {MIN_SCORE} 分，成交量 >{MIN_VOLUME_LOTS} 張）"
        )

    # 組成訊息
    if mode == "open":
        title = "🌅 早盤買進參考"
        subtitle = "以下為昨收訊號，供今日開盤參考"
    else:
        title = "📊 收盤選股訊號"
        subtitle = "以下為今日收盤訊號，供明日操作參考"

    lines = [
        f"🔔 {title}",
        f"━━━━━━━━━━━━━━━",
        f"📅 {now}",
        f"大盤：季線之上 VIX={vix:.1f}",
        f"掃描：{len(all_ids)}支 → 符合：{len(candidates)}支",
        f"{subtitle}",
        f"",
    ]

    for i, s in enumerate(top):
        # 評分等級
        if s["score"] >= 80:
            emoji = "🔥"
            grade = "強力訊號"
        elif s["score"] >= 70:
            emoji = "✅"
            grade = "優質訊號"
        else:
            emoji = "📈"
            grade = "觀察訊號"

        # 量能說明
        if s["vol_ratio"] >= 2.0:
            vol_note = f"爆量{s['vol_ratio']}倍"
        elif s["vol_ratio"] >= 1.5:
            vol_note = f"放量{s['vol_ratio']}倍"
        else:
            vol_note = f"量能正常"

        vol_display = (
            f"{s['vol_today']//10000:.1f}萬張" if s['vol_today'] >= 10000
            else f"{s['vol_today']//1000:.1f}千張" if s['vol_today'] >= 1000
            else f"{s['vol_today']}張"
        )

        lines += [
            f"{emoji} {s['stock_id']} {s['name']}  {s['score']}分 {grade}",
            f"   現價 ${s['close']}  量 {vol_display}（{vol_note}）",
            f"   RSI={s['rsi']}  乖離={s['bias']:+.1f}%  5日={s['ret_5d']:+.1f}%",
            f"   進場區間：${s['entry_low']}~${s['entry_high']}",
            f"   停損：${s['stop_loss']}（-5%）　停利：${s['target']}（+10%）",
            f"   輸入「分析 {s['stock_id']}」看完整報告",
            f"",
        ]

    lines += [
        f"━━━━━━━━━━━━━━━",
        f"⚠️ 訊號僅供參考，請搭配「分析」指令確認後再操作",
        f"成交量門檻：>{MIN_VOLUME_LOTS}張  評分門檻：>{MIN_SCORE}分",
    ]

    return "\n".join(lines)


def run_open_alert(bot_token: str, user_ids: list):
    """08:30 早盤提醒"""
    import asyncio
    from telegram import Bot

    async def send():
        bot = Bot(token=bot_token)
        report = run_daily_scan(mode="open")
        for uid in user_ids:
            try:
                for i in range(0, len(report), 4000):
                    await bot.send_message(chat_id=uid, text=report[i:i+4000])
            except Exception as e:
                logger.error(f"早盤提醒推送失敗 {uid}: {e}")

    asyncio.run(send())


def run_close_alert(bot_token: str, user_ids: list):
    """15:30 收盤提醒"""
    import asyncio
    from telegram import Bot

    async def send():
        bot = Bot(token=bot_token)
        report = run_daily_scan(mode="close")
        for uid in user_ids:
            try:
                for i in range(0, len(report), 4000):
                    await bot.send_message(chat_id=uid, text=report[i:i+4000])
            except Exception as e:
                logger.error(f"收盤提醒推送失敗 {uid}: {e}")

    asyncio.run(send())
