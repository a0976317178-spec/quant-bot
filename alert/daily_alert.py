"""
alert/daily_alert.py - 每日選股提醒（完整版 v3）
功能清單：
  🥇 訊號追蹤+驗證    🥈 52週高低點+突破判斷
  🥉 族群強弱比較     🏅 動態評分權重
  💳 融資餘額         🔵 外資連買天數
  📊 停利停損顯示     🏆 回測勝率標註
  👑 產業龍頭標記     💎 籌碼集中度
"""
import json, logging, os, threading
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
BACKTEST_PATH   = "data/backtest_winrate.json"

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
        results.append({**rec, "current": round(current,2), "ret_pct": round(ret,2)})
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

# ══ 🥈 52週高低點 + 突破判斷 ══════════════════════════════

def get_52w_position(stock_id, current, vol_ratio=1.0, rsi=50.0) -> dict:
    try:
        df = query_df("""
            SELECT MAX(high) as h, MIN(low) as l FROM daily_price
            WHERE stock_id=? AND date >= date('now', '-365 days')
        """, (stock_id,))
        if df.empty or df.iloc[0]["h"] is None:
            return {}
        high52 = float(df.iloc[0]["h"])
        low52  = float(df.iloc[0]["l"])
        rng = high52 - low52
        if rng <= 0:
            return {}
        position = (current - low52) / rng * 100

        # 突破判斷
        if position >= 85:
            if vol_ratio >= 1.5 and rsi <= 72:
                breakout = "突破"   # 真突破：爆量+RSI未超買
            elif rsi > 72:
                breakout = "追高"   # RSI過熱
            else:
                breakout = "測試"   # 量縮測試高點
        else:
            breakout = ""

        return {
            "high52":   round(high52, 2),
            "low52":    round(low52, 2),
            "position": round(position, 1),
            "breakout": breakout,
        }
    except Exception:
        return {}

def format_52w(pos) -> str:
    if not pos:
        return ""
    p = pos["position"]
    bar_filled = int(p / 10)
    bar = "▓" * bar_filled + "░" * (10 - bar_filled)
    note = (
        f"🚀突破新高" if pos.get("breakout") == "突破" else
        f"⚠️追高危險" if pos.get("breakout") == "追高" else
        f"🔍測試高點" if pos.get("breakout") == "測試" else
        "偏低✨" if p < 20 else
        "偏低✨" if p < 35 else
        "中間" if p < 65 else
        "偏高"
    )
    return f"[{bar}]{p:.0f}% {note}"

# ══ 🔵 外資連買天數 ═══════════════════════════════════════

def get_foreign_consecutive(stock_id) -> dict:
    try:
        df = query_df("""
            SELECT date, foreign_net FROM institutional
            WHERE stock_id=? ORDER BY date DESC LIMIT 10
        """, (stock_id,))
        if df.empty:
            return {}
        consecutive_buy  = 0
        consecutive_sell = 0
        total_net = 0
        for _, row in df.iterrows():
            net = int(row["foreign_net"])
            total_net += net
            if net > 0:
                if consecutive_sell == 0:
                    consecutive_buy += 1
            else:
                if consecutive_buy == 0:
                    consecutive_sell += 1
                else:
                    break
        return {
            "buy_days":  consecutive_buy,
            "sell_days": consecutive_sell,
            "net_10d":   total_net,
        }
    except Exception:
        return {}

def format_foreign(fg) -> str:
    if not fg:
        return ""
    if fg["buy_days"] >= 3:
        return f"🔵外資連買{fg['buy_days']}天"
    elif fg["buy_days"] >= 1:
        return f"外資買{fg['buy_days']}天"
    elif fg["sell_days"] >= 3:
        return f"🔴外資連賣{fg['sell_days']}天"
    return ""

# ══ 🏆 回測勝率 ═══════════════════════════════════════════

def calc_historical_winrate(stock_id, closes, volumes) -> str:
    """
    回測：過去所有類似訊號（站上MA20+放量）發出後3天的勝率
    """
    try:
        if len(closes) < 65:
            return ""
        wins = total = 0
        for i in range(60, len(closes)-3):
            window = closes[i:i+20]
            ma20 = sum(window) / 20
            vol_window = volumes[i:i+20]
            vol_ma = sum(vol_window) / 20 if vol_window else 1
            # 類似訊號條件
            if closes[i] > ma20 and volumes[i] > vol_ma * 1.3:
                ret_3d = (closes[i-3] / closes[i] - 1) * 100  # 注意：closes是倒序
                total += 1
                if ret_3d > 0:
                    wins += 1
        if total < 5:
            return ""
        wr = wins / total * 100
        return f"歷史勝率{wr:.0f}%({total}次)"
    except Exception:
        return ""

# ══ 👑 產業龍頭標記 ═══════════════════════════════════════

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

# ══ 💎 籌碼集中度 ═════════════════════════════════════════

def get_chip_concentration(stock_id) -> dict:
    """
    法人持股比例：外資+投信淨買超越多，籌碼越集中越安全
    """
    try:
        df = query_df("""
            SELECT foreign_net, trust_net, total_net FROM institutional
            WHERE stock_id=? ORDER BY date DESC LIMIT 20
        """, (stock_id,))
        if df.empty:
            return {}
        foreign_20d = int(df["foreign_net"].sum())
        trust_20d   = int(df["trust_net"].sum())
        total_20d   = int(df["total_net"].sum())
        return {
            "foreign_20d": foreign_20d,
            "trust_20d":   trust_20d,
            "total_20d":   total_20d,
        }
    except Exception:
        return {}

def format_chip(chip) -> str:
    if not chip:
        return ""
    t = chip["total_20d"]
    if t > 5000:
        return f"💎籌碼集中(法人20日+{t//1000:.0f}千張)"
    elif t > 1000:
        return f"法人買超(+{t//1000:.1f}千張)"
    elif t < -3000:
        return f"⚠️法人賣超({t//1000:.0f}千張)"
    return ""

# ══ 💳 融資餘額 ═══════════════════════════════════════════

def get_margin_ratio(stock_id) -> dict:
    try:
        df = query_df("""
            SELECT margin_balance, margin_change FROM margin_trading
            WHERE stock_id=? ORDER BY date DESC LIMIT 5
        """, (stock_id,))
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
                    conn.executemany("INSERT OR REPLACE INTO margin_trading (stock_id,date,margin_balance,margin_change) VALUES (?,?,?,?)", records)
                logger.info(f"融資 {date_db}：{len(records)} 筆")
            time.sleep(0.5)
        except Exception as e:
            logger.warning(f"融資爬取失敗 {date_db}: {e}")

# ══ 動態權重 ══════════════════════════════════════════════

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
                    if obj["ret_pct"] > 2:   wins += 1
                    elif obj["ret_pct"] < -2: losses += 1
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
    except Exception as e:
        logger.warning(f"更新權重失敗: {e}")

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

        # 歷史回測勝率
        winrate_str = calc_historical_winrate(stock_id, closes, volumes)

        return {
            "stock_id":    stock_id,
            "name":        name,
            "score":       score,
            "close":       round(current, 2),
            "vol_today":   vol_today,
            "vol_ratio":   round(vol_ratio, 1),
            "rsi":         round(rsi, 1),
            "ma20":        round(ma20, 2),
            "ma60":        round(ma60, 2),
            "bias":        round(bias, 1),
            "ret_5d":      round(ret_5d, 1),
            "entry_low":   round(current * 0.99, 2),
            "entry_high":  round(current * 1.01, 2),
            "stop_loss":   round(current * 0.95, 2),
            "target":      round(current * 1.10, 2),
            "winrate_str": winrate_str,
            "closes":      closes,
            "volumes":     volumes,
        }
    except Exception as e:
        logger.debug(f"{stock_id} 評分失敗: {e}")
        return None

# ══ 主掃描流程 ════════════════════════════════════════════

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

    # 👑 標記各族群龍頭（同族群中評分最高的）
    sector_leaders = set()
    sector_best = {}
    for c in candidates:
        name = c["name"]
        for sector, keywords in SECTOR_MAP.items():
            if any(kw in name for kw in keywords):
                if sector not in sector_best or c["score"] > sector_best[sector]["score"]:
                    sector_best[sector] = c
    for s in sector_best.values():
        sector_leaders.add(s["stock_id"])

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
    for i, s in enumerate(top):
        is_leader = s["stock_id"] in sector_leaders
        score_emoji = "🔥" if s["score"] >= 80 else ("✅" if s["score"] >= 70 else "📈")
        crown = "👑" if is_leader else ""
        grade = "強力訊號" if s["score"] >= 80 else ("優質訊號" if s["score"] >= 70 else "觀察訊號")
        vol_note = f"爆量 {s['vol_ratio']}x" if s["vol_ratio"] >= 2.0 else (f"放量 {s['vol_ratio']}x" if s["vol_ratio"] >= 1.5 else "量能正常")
        vol_str  = f"{s['vol_today']//10000:.1f}萬張" if s["vol_today"] >= 10000 else f"{s['vol_today']//1000:.1f}千張"

        pos52 = get_52w_position(s["stock_id"], s["close"], s["vol_ratio"], s["rsi"])
        fg    = get_foreign_consecutive(s["stock_id"])
        mg    = get_margin_ratio(s["stock_id"])
        chip  = get_chip_concentration(s["stock_id"])

        breakout_tag = pos52.get("breakout", "") if pos52 else ""
        if breakout_tag == "突破":
            signal_tag = "🚀 突破新高，可積極追"
        elif breakout_tag == "追高":
            signal_tag = "⛔ 近高點量縮，風險高"
        elif breakout_tag == "測試":
            signal_tag = "🔍 測試高點，觀望為主"
        else:
            p = pos52.get("position", 50) if pos52 else 50
            signal_tag = "✨ 低檔布局區" if p < 35 else ""

        fg_fmt   = format_foreign(fg)
        mg_fmt   = format_margin(mg)
        chip_fmt = format_chip(chip)
        wr_str   = s.get("winrate_str", "")

        if pos52:
            p = pos52["position"]
            bar = "▓" * int(p/10) + "░" * (10 - int(p/10))
            pos52_line = f"📊 [{bar}] {p:.0f}%  低${pos52['low52']} ~ 高${pos52['high52']}"
        else:
            pos52_line = ""

        lines.append(f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄")
        lines.append(f"{score_emoji}{crown} {s['stock_id']} {s['name']}　{s['score']}分 {grade}")
        if signal_tag:
            lines.append(f"   {signal_tag}")
        lines.append(f"💰 現價 ${s['close']}　5日 {s['ret_5d']:+.1f}%　RSI {s['rsi']}")
        lines.append(f"📦 {vol_str}　{vol_note}")
        lines.append(f"🎯 進場 ${s['entry_low']} ～ ${s['entry_high']}")
        lines.append(f"🛡 停損 ${s['stop_loss']}（-5%）　停利 ${s['target']}（+10%）")
        if pos52_line:
            lines.append(f"   {pos52_line}")
        extras = [x for x in [fg_fmt, mg_fmt, chip_fmt] if x]
        if extras:
            lines.append("   " + "   ".join(extras))
        if wr_str:
            lines.append(f"   📈 {wr_str}")
        lines.append(f"   👉 分析 {s['stock_id']}")

    if sector_data:
        lines.append("")
        sorted_s = sorted(sector_data.items(), key=lambda x: x[1], reverse=True)
        lines.append("🏭 族群：" + "  ".join(
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
