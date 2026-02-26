"""
alert/daily_alert.py - 每日選股提醒（升級版）
新增功能：
  🥇 訊號追蹤表：自動記錄每次訊號，收盤後驗證漲跌
  🥈 52週高低點：標示現價在52週區間的位置
  🥉 族群強弱：掃描時同步計算各族群平均報酬
  🏅 動態評分權重：根據近期哪個因子最準，自動調整權重
  💳 融資餘額：爬取融資餘額變化，高融資=散戶擁擠=風險
"""
import json
import logging
import os
import threading
from datetime import datetime, timedelta
from database.db_manager import query_df, get_conn

logger = logging.getLogger(__name__)

MIN_VOLUME_LOTS = 3000
MIN_SCORE       = 60
MAX_RESULTS     = 50
MIN_PRICE       = 10.0
MAX_PRICE       = 2000.0
SIGNAL_LOG_PATH = "data/signal_log.jsonl"
WEIGHT_PATH     = "data/dynamic_weights.json"

def _ensure_dir():
    os.makedirs("data", exist_ok=True)

# ══ 🥇 訊號追蹤 ══════════════════════════════════════════

def save_signal(stock_id, name, score, close, date):
    _ensure_dir()
    record = {"stock_id": stock_id, "name": name, "score": score,
              "entry": close, "date": date, "verified": False, "ret_pct": None}
    with open(SIGNAL_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

def verify_signals() -> str:
    _ensure_dir()
    if not os.path.exists(SIGNAL_LOG_PATH):
        return ""
    target_date = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    records = []
    with open(SIGNAL_LOG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            try:
                obj = json.loads(line.strip())
                if obj.get("date") == target_date and not obj.get("verified"):
                    records.append(obj)
            except Exception:
                pass
    if not records:
        return ""
    results = []
    for rec in records:
        df = query_df("SELECT close FROM daily_price WHERE stock_id=? ORDER BY date DESC LIMIT 1", (rec["stock_id"],))
        if df.empty:
            continue
        current = float(df.iloc[0]["close"])
        ret = (current - rec["entry"]) / rec["entry"] * 100
        results.append({**rec, "current": current, "ret_pct": round(ret, 2)})
    if not results:
        return ""
    wins = [r for r in results if r["ret_pct"] > 0]
    wr = len(wins) / len(results) * 100
    lines = [f"\n📋 訊號驗證（{target_date}，3日後）勝率：{wr:.0f}%（{len(wins)}勝/{len(results)-len(wins)}敗）"]
    for r in sorted(results, key=lambda x: x["ret_pct"], reverse=True)[:8]:
        e = "✅" if r["ret_pct"] > 0 else "❌"
        lines.append(f"  {e} {r['stock_id']}{r['name']} ${r['entry']}→${r['current']} {r['ret_pct']:+.1f}%")
    all_records = []
    with open(SIGNAL_LOG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            try:
                obj = json.loads(line.strip())
                if obj.get("date") == target_date:
                    obj["verified"] = True
                all_records.append(obj)
            except Exception:
                pass
    with open(SIGNAL_LOG_PATH, "w", encoding="utf-8") as f:
        for obj in all_records:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    return "\n".join(lines)

# ══ 🥈 52週高低點 ═════════════════════════════════════════

def get_52w_position(stock_id, current) -> dict:
    try:
        df = query_df("SELECT MAX(high) as h, MIN(low) as l FROM daily_price WHERE stock_id=? AND date >= date('now', '-365 days')", (stock_id,))
        if df.empty or df.iloc[0]["h"] is None:
            return {}
        high52 = float(df.iloc[0]["h"])
        low52  = float(df.iloc[0]["l"])
        rng = high52 - low52
        if rng <= 0:
            return {}
        position = (current - low52) / rng * 100
        return {"high52": round(high52,2), "low52": round(low52,2), "position": round(position,1)}
    except Exception:
        return {}

def format_52w(pos) -> str:
    if not pos:
        return ""
    p = pos["position"]
    bar_filled = int(p / 10)
    bar = "▓" * bar_filled + "░" * (10 - bar_filled)
    note = "近高點" if p >= 80 else ("偏高" if p >= 60 else ("中間" if p >= 40 else ("偏低✨" if p >= 20 else "近低點✨")))
    return f"[{bar}]{p:.0f}%{note}"

# ══ 🥉 族群強弱 ═══════════════════════════════════════════

SECTOR_MAP = {
    "半導體": ["台積", "聯發", "聯電", "日月光"],
    "AI伺服器": ["廣達", "緯穎", "英業達", "鴻海"],
    "金融": ["富邦", "國泰", "中信", "玉山"],
    "電動車": ["和大", "貿聯", "信邦"],
    "傳產": ["台塑", "南亞", "中鋼"],
}

def get_sector_strength() -> dict:
    results = {}
    for sector, keywords in SECTOR_MAP.items():
        returns = []
        for kw in keywords:
            try:
                df = query_df("SELECT stock_id FROM stocks WHERE name LIKE ? LIMIT 2", (f"%{kw}%",))
                for _, row in df.iterrows():
                    pdf = query_df("SELECT close FROM daily_price WHERE stock_id=? ORDER BY date DESC LIMIT 6", (row["stock_id"],))
                    if len(pdf) >= 6:
                        returns.append((pdf.iloc[0]["close"] / pdf.iloc[5]["close"] - 1) * 100)
            except Exception:
                pass
        if returns:
            results[sector] = round(sum(returns) / len(returns), 2)
    return results

# ══ 🏅 動態權重 ═══════════════════════════════════════════

DEFAULT_WEIGHTS = {"ma_trend": 1.0, "rsi": 1.0, "volume": 1.0, "bias": 1.0, "momentum": 1.0}

def load_weights() -> dict:
    _ensure_dir()
    if not os.path.exists(WEIGHT_PATH):
        return DEFAULT_WEIGHTS.copy()
    try:
        with open(WEIGHT_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return DEFAULT_WEIGHTS.copy()

def update_weights():
    _ensure_dir()
    if not os.path.exists(SIGNAL_LOG_PATH):
        return
    try:
        wins = losses = 0
        with open(SIGNAL_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                obj = json.loads(line.strip())
                if obj.get("verified") and obj.get("ret_pct") is not None:
                    if obj["ret_pct"] > 2:
                        wins += 1
                    elif obj["ret_pct"] < -2:
                        losses += 1
        total = wins + losses
        if total < 5:
            return
        wr = wins / total
        weights = load_weights()
        if wr >= 0.6:
            weights["volume"]   = min(1.5, weights["volume"]   * 1.05)
            weights["momentum"] = min(1.5, weights["momentum"] * 1.05)
        else:
            weights["ma_trend"] = min(1.5, weights["ma_trend"] * 1.1)
            weights["bias"]     = max(0.5, weights["bias"]     * 0.9)
        avg = sum(weights.values()) / len(weights)
        weights = {k: round(v/avg, 3) for k, v in weights.items()}
        with open(WEIGHT_PATH, "w") as f:
            json.dump(weights, f, indent=2)
        logger.info(f"動態權重更新：{weights}")
    except Exception as e:
        logger.warning(f"更新權重失敗: {e}")

# ══ 💳 融資餘額 ═══════════════════════════════════════════

def get_margin_ratio(stock_id) -> dict:
    try:
        df = query_df("SELECT margin_balance, margin_change FROM margin_trading WHERE stock_id=? ORDER BY date DESC LIMIT 5", (stock_id,))
        if df.empty:
            return {}
        latest   = int(df.iloc[0]["margin_balance"])
        change5d = int(df["margin_change"].sum())
        avg      = df["margin_balance"].mean()
        ratio    = latest / avg if avg > 0 else 1.0
        return {"balance": latest, "change5d": change5d, "ratio": round(ratio, 2)}
    except Exception:
        return {}

def format_margin(mg) -> str:
    if not mg:
        return ""
    if mg["ratio"] > 1.3 and mg["change5d"] > 0:
        return f"⚠️融資偏高({mg['ratio']:.1f}x)"
    elif mg["ratio"] < 0.7:
        return f"✅融資低({mg['ratio']:.1f}x)"
    return ""

def crawl_margin_trading():
    import requests, time
    headers = {"User-Agent": "Mozilla/5.0"}
    today = datetime.now()
    for delta in range(10):
        date = today - timedelta(days=delta)
        if date.weekday() >= 5:
            continue
        date_str = date.strftime("%Y%m%d")
        date_db  = date.strftime("%Y-%m-%d")
        try:
            with get_conn() as conn:
                count = conn.execute("SELECT COUNT(*) FROM margin_trading WHERE date=?", (date_db,)).fetchone()[0]
                if count > 0:
                    continue
        except Exception:
            pass
        try:
            url = "https://www.twse.com.tw/exchangeReport/MI_MARGN"
            params = {"response": "json", "date": date_str, "selectType": "ALL"}
            resp = requests.get(url, params=params, headers=headers, timeout=15)
            data = resp.json()
            if data.get("stat") != "OK":
                time.sleep(0.5)
                continue
            records = []
            for row in data.get("data", []):
                try:
                    sid = str(row[0]).strip()
                    if not sid.isdigit() or len(sid) != 4:
                        continue
                    def pi(s): return int(str(s).replace(",","") or 0)
                    records.append((sid, date_db, pi(row[4]), pi(row[3])))
                except Exception:
                    continue
            if records:
                with get_conn() as conn:
                    conn.executemany("INSERT OR REPLACE INTO margin_trading (stock_id, date, margin_balance, margin_change) VALUES (?,?,?,?)", records)
                logger.info(f"融資 {date_db}：{len(records)} 筆")
            time.sleep(0.5)
        except Exception as e:
            logger.warning(f"融資爬取失敗 {date_db}: {e}")

# ══ 核心掃描 ══════════════════════════════════════════════

def _get_all_stock_ids() -> list:
    try:
        df = query_df("SELECT stock_id FROM stocks WHERE stock_id GLOB '[0-9][0-9][0-9][0-9]'")
        return df["stock_id"].tolist() if not df.empty else []
    except Exception as e:
        logger.error(f"取得股票清單失敗: {e}")
        return []

def _check_market_ok() -> tuple:
    try:
        from factors.analyzer import analyze_environment
        env    = analyze_environment()
        detail = env.get("detail", {})
        return env.get("market_ok", True), detail.get("ma60", 0), detail.get("current", 0), detail.get("vix", 15)
    except Exception:
        return True, 0, 0, 15

def _quick_score(stock_id, weights) -> dict:
    try:
        df = query_df("SELECT close, volume FROM daily_price WHERE stock_id=? ORDER BY date DESC LIMIT 65", (stock_id,))
        if len(df) < 20:
            return None
        closes  = df["close"].tolist()
        volumes = [int(v) // 1000 for v in df["volume"].tolist()]
        current   = closes[0]
        vol_today = volumes[0]
        if vol_today < MIN_VOLUME_LOTS or current < MIN_PRICE or current > MAX_PRICE:
            return None
        ma20 = sum(closes[:20]) / 20
        ma60 = sum(closes[:60]) / 60 if len(closes) >= 60 else ma20
        vol_ma20 = sum(volumes[:20]) / 20 if len(volumes) >= 20 else 1
        if current < ma20:
            return None
        from factors.analyzer import calc_rsi
        rsi = calc_rsi(closes)
        if rsi > 75:
            return None
        w = weights
        score = 0
        if current > ma20 and ma20 > ma60:
            score += int(25 * w.get("ma_trend", 1.0))
        elif current > ma20:
            score += int(12 * w.get("ma_trend", 1.0))
        if 45 <= rsi <= 65:
            score += int(20 * w.get("rsi", 1.0))
        elif 40 <= rsi <= 75:
            score += int(10 * w.get("rsi", 1.0))
        vol_ratio = vol_today / vol_ma20 if vol_ma20 > 0 else 1
        if vol_ratio >= 2.0:
            score += int(20 * w.get("volume", 1.0))
        elif vol_ratio >= 1.5:
            score += int(12 * w.get("volume", 1.0))
        elif vol_ratio >= 0.8:
            score += int(5  * w.get("volume", 1.0))
        bias = (current - ma20) / ma20 * 100
        if -3 <= bias <= 5:
            score += int(15 * w.get("bias", 1.0))
        elif 5 < bias <= 10:
            score += int(8  * w.get("bias", 1.0))
        elif bias > 15:
            score -= 10
        ret_5d = (current / closes[5] - 1) * 100 if len(closes) > 5 else 0
        if 0 < ret_5d <= 5:
            score += int(10 * w.get("momentum", 1.0))
        elif ret_5d > 5:
            score += int(5  * w.get("momentum", 1.0))
        name = ""
        with get_conn() as conn:
            row = conn.execute("SELECT name FROM stocks WHERE stock_id=?", (stock_id,)).fetchone()
            if row:
                name = row["name"]
        return {
            "stock_id": stock_id, "name": name, "score": score,
            "close": round(current, 2), "vol_today": vol_today,
            "vol_ratio": round(vol_ratio, 1), "rsi": round(rsi, 1),
            "ma20": round(ma20, 2), "ma60": round(ma60, 2),
            "bias": round(bias, 1), "ret_5d": round(ret_5d, 1),
            "entry_low": round(current*0.99,2), "entry_high": round(current*1.01,2),
            "stop_loss": round(current*0.95,2), "target": round(current*1.10,2),
        }
    except Exception as e:
        logger.debug(f"{stock_id} 評分失敗: {e}")
        return None

def run_daily_scan(mode: str = "close") -> str:
    now   = datetime.now().strftime("%Y-%m-%d %H:%M")
    today = datetime.now().strftime("%Y-%m-%d")
    market_ok, ma60, mkt_current, vix = _check_market_ok()
    if not market_ok:
        return (f"⚠️ 全市場掃描\n━━━━━━━━━━━━━━━\n📅 {now}\n"
                f"🚨 大盤跌破季線（{mkt_current:.0f} < {ma60:.0f}）VIX={vix:.1f}\n今日不推買進訊號")
    weights = load_weights()
    sector_data = {}
    def calc_sectors():
        nonlocal sector_data
        sector_data = get_sector_strength()
    t = threading.Thread(target=calc_sectors, daemon=True)
    t.start()
    all_ids    = _get_all_stock_ids()
    candidates = []
    for sid in all_ids:
        r = _quick_score(sid, weights)
        if r and r["score"] >= MIN_SCORE:
            candidates.append(r)
    candidates.sort(key=lambda x: (x["score"], x["vol_ratio"]), reverse=True)
    top = candidates[:MAX_RESULTS]
    t.join(timeout=10)
    for s in top:
        save_signal(s["stock_id"], s["name"], s["score"], s["close"], today)
    threading.Thread(target=update_weights, daemon=True).start()
    if not top:
        return (f"📊 全市場掃描\n━━━━━━━━━━━━━━━\n📅 {now}\n"
                f"今日無符合條件股票（掃描{len(all_ids)}支）")
    title = "🌅 早盤買進參考" if mode == "open" else "📊 收盤選股訊號"
    w_str = f"趨勢{weights.get('ma_trend',1):.1f}x 量{weights.get('volume',1):.1f}x 動能{weights.get('momentum',1):.1f}x"
    lines = [
        f"🔔 {title}",
        f"━━━━━━━━━━━━━━━",
        f"📅 {now}  VIX={vix:.1f}",
        f"掃描{len(all_ids)}支 → 符合{len(candidates)}支 → 顯示{len(top)}支",
        f"⚖️ 動態權重：{w_str}",
        f"",
    ]
    for s in top:
        emoji = "🔥" if s["score"] >= 80 else ("✅" if s["score"] >= 70 else "📈")
        grade = "強力" if s["score"] >= 80 else ("優質" if s["score"] >= 70 else "觀察")
        vol_note = f"爆量{s['vol_ratio']}x" if s["vol_ratio"] >= 2.0 else (f"放量{s['vol_ratio']}x" if s["vol_ratio"] >= 1.5 else "正常")
        vol_str  = f"{s['vol_today']//10000:.1f}萬" if s["vol_today"] >= 10000 else f"{s['vol_today']//1000:.1f}千"
        pos52    = get_52w_position(s["stock_id"], s["close"])
        pos52_str = f" 52W:{format_52w(pos52)}" if pos52 else ""
        mg     = get_margin_ratio(s["stock_id"])
        mg_str = f" {format_margin(mg)}" if format_margin(mg) else ""
        lines.append(
            f"{emoji}{s['stock_id']} {s['name']} {s['score']}分{grade}|"
            f"${s['close']}|{vol_str}({vol_note})|RSI={s['rsi']}|5日={s['ret_5d']:+.1f}%"
            f"{pos52_str}{mg_str}"
        )
    if sector_data:
        lines.append("")
        sorted_s = sorted(sector_data.items(), key=lambda x: x[1], reverse=True)
        lines.append("🏭 族群強弱：" + "  ".join(
            f"{'🔥' if v>3 else ('✅' if v>0 else '🔴')}{k}{v:+.1f}%" for k, v in sorted_s
        ))
    verification = verify_signals()
    if verification:
        lines.append(verification)
    lines += ["", "━━━━━━━━━━━━━━━", "輸入「分析 代號」看AI完整報告", "⚠️ 僅供參考，請自行判斷風險"]
    return "\n".join(lines)

def run_open_alert(bot_token: str, user_ids: list):
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
                logger.error(f"早盤提醒失敗 {uid}: {e}")
    asyncio.run(send())

def run_close_alert(bot_token: str, user_ids: list):
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
                logger.error(f"收盤提醒失敗 {uid}: {e}")
    asyncio.run(send())
