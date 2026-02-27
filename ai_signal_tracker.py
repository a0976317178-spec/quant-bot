"""
ai_signal_tracker.py - AI 自動信號追蹤器

功能：
  1. 每日收盤後掃描所有自選股，評分 >= 門檻自動記錄「買入信號」
  2. 信號進入 ai_signal_log 資料表追蹤後續績效
  3. 每天自動評估：哪些信號成功（隔日漲），哪些失敗
  4. 每週自我學習：分析信號成功率，調整門檻，讓 AI 越來越準
  5. 可手動查看信號清單、統計、績效報告

不需要人工記錄！系統自動完成買賣信號追蹤。
"""
import json
import logging
import os
from datetime import datetime, timedelta
from database.db_manager import get_conn, query_df

logger = logging.getLogger(__name__)

# 信號觸發門檻（可透過指令動態調整）
SIGNAL_CONFIG_FILE = "data/signal_config.json"
DEFAULT_CONFIG = {
    "buy_score_threshold": 65,    # 買入信號門檻分數
    "sell_score_threshold": 40,   # 賣出/清倉信號門檻
    "eval_days": [1, 3, 5, 10],   # 評估信號後幾天的表現
    "auto_close_days": 20,        # 超過幾天自動關閉信號（視為過期）
    "min_vol_ratio": 1.0,         # 最小量能倍數（避免低量騙線）
}


def _load_config() -> dict:
    os.makedirs("data", exist_ok=True)
    if os.path.exists(SIGNAL_CONFIG_FILE):
        try:
            with open(SIGNAL_CONFIG_FILE, "r") as f:
                cfg = DEFAULT_CONFIG.copy()
                cfg.update(json.load(f))
                return cfg
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def _save_config(cfg: dict):
    os.makedirs("data", exist_ok=True)
    with open(SIGNAL_CONFIG_FILE, "w") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════
# 資料庫操作
# ══════════════════════════════════════════

def ensure_signal_table():
    """確保 ai_signal_log 資料表存在"""
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_signal_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_id TEXT NOT NULL,
                stock_name TEXT DEFAULT '',
                signal_type TEXT NOT NULL,      -- 'BUY' / 'SELL'
                signal_date TEXT NOT NULL,
                entry_price REAL DEFAULT 0,
                score INTEGER DEFAULT 0,
                tech_score INTEGER DEFAULT 0,
                chip_score INTEGER DEFAULT 0,
                fund_score INTEGER DEFAULT 0,
                env_score INTEGER DEFAULT 0,
                signal_reason TEXT DEFAULT '',
                status TEXT DEFAULT 'ACTIVE',   -- ACTIVE / WIN / LOSS / EXPIRED / CLOSED
                close_date TEXT,
                close_price REAL,
                pnl_pct REAL,
                hold_days INTEGER DEFAULT 0,
                ret_1d REAL,   -- 隔日報酬
                ret_3d REAL,   -- 3日報酬
                ret_5d REAL,   -- 5日報酬
                ret_10d REAL,  -- 10日報酬
                evaluated_at TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(stock_id, signal_date, signal_type)
            )
        """)
        # 信號統計快取表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signal_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_date TEXT NOT NULL,
                total_signals INTEGER DEFAULT 0,
                win_signals INTEGER DEFAULT 0,
                loss_signals INTEGER DEFAULT 0,
                win_rate REAL DEFAULT 0,
                avg_ret_1d REAL DEFAULT 0,
                avg_ret_5d REAL DEFAULT 0,
                best_signal TEXT DEFAULT '',
                worst_signal TEXT DEFAULT '',
                threshold_used INTEGER DEFAULT 65,
                suggested_threshold INTEGER DEFAULT 65,
                report_text TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)


# ══════════════════════════════════════════
# 核心：產生買入信號
# ══════════════════════════════════════════

def generate_buy_signals(stock_ids: list = None) -> list:
    """
    掃描股票，對達到門檻的股票產生買入信號
    回傳新增的信號列表
    """
    ensure_signal_table()
    cfg = _load_config()
    threshold = cfg["buy_score_threshold"]
    min_vol   = cfg["min_vol_ratio"]

    if stock_ids is None:
        try:
            from memory.daily_learning import load_watchlist
            stock_ids = load_watchlist()
        except Exception:
            stock_ids = []

    if not stock_ids:
        logger.warning("AI信號：自選股清單為空")
        return []

    today = datetime.now().strftime("%Y-%m-%d")
    new_signals = []

    for stock_id in stock_ids:
        try:
            from factors.analyzer import full_analysis
            result = full_analysis(stock_id)

            score      = result.get("total_score", 0)
            tech       = result.get("tech", {})
            vol_ratio  = tech.get("detail", {}).get("vol_ratio", 1.0)

            # 不符合門檻就跳過
            if score < threshold:
                continue
            if vol_ratio < min_vol:
                logger.info(f"AI信號跳過 {stock_id}：量能不足({vol_ratio:.1f}x < {min_vol}x)")
                continue
            # 大盤環境差也跳過
            if not result.get("market_ok", True):
                logger.info(f"AI信號跳過 {stock_id}：大盤環境差")
                continue

            entry_price  = result.get("close_price", 0)
            stock_name   = result.get("name", "")
            tech_score   = result.get("tech", {}).get("score", 0)
            chip_score   = result.get("chip", {}).get("score", 0)
            fund_score   = result.get("fund", {}).get("score", 0)
            env_score    = result.get("env",  {}).get("score", 0)
            tech_note    = result.get("tech", {}).get("note", "")
            chip_note    = result.get("chip", {}).get("note", "")

            reason = f"評分{score}分 | {tech_note[:40]} | {chip_note[:40]}"

            with get_conn() as conn:
                conn.execute("""
                    INSERT INTO ai_signal_log
                        (stock_id, stock_name, signal_type, signal_date,
                         entry_price, score, tech_score, chip_score,
                         fund_score, env_score, signal_reason, status)
                    VALUES (?, ?, 'BUY', ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE')
                    ON CONFLICT(stock_id, signal_date, signal_type) DO NOTHING
                """, (
                    stock_id, stock_name, today, entry_price,
                    score, tech_score, chip_score, fund_score, env_score, reason
                ))

            new_signals.append({
                "stock_id":   stock_id,
                "name":       stock_name,
                "score":      score,
                "price":      entry_price,
                "reason":     reason,
                "vol_ratio":  vol_ratio,
            })
            logger.info(f"AI買入信號：{stock_id} {score}分 ${entry_price}")

        except Exception as e:
            logger.error(f"產生信號失敗 {stock_id}: {e}")

    return new_signals


# ══════════════════════════════════════════
# 核心：評估歷史信號績效
# ══════════════════════════════════════════

def evaluate_signals() -> int:
    """
    每日評估歷史信號的實際報酬
    回傳本次評估筆數
    """
    ensure_signal_table()
    cfg      = _load_config()
    today    = datetime.now().strftime("%Y-%m-%d")
    evaluated = 0

    # 取得所有 ACTIVE 信號
    active = query_df("""
        SELECT id, stock_id, signal_date, entry_price, score
        FROM ai_signal_log
        WHERE status = 'ACTIVE'
        ORDER BY signal_date ASC
    """)

    if active.empty:
        return 0

    for _, sig in active.iterrows():
        stock_id    = sig["stock_id"]
        signal_date = sig["signal_date"]
        entry_price = float(sig["entry_price"])
        sig_id      = int(sig["id"])

        hold_days = (
            datetime.strptime(today, "%Y-%m-%d") -
            datetime.strptime(signal_date, "%Y-%m-%d")
        ).days

        # 超過 auto_close_days 自動關閉
        if hold_days >= cfg["auto_close_days"]:
            _close_signal(sig_id, "EXPIRED", today, entry_price, 0.0, hold_days)
            continue

        # 取得後續股價（1/3/5/10日）
        prices = {}
        for d in cfg["eval_days"]:
            target_date = (
                datetime.strptime(signal_date, "%Y-%m-%d") + timedelta(days=d)
            ).strftime("%Y-%m-%d")
            row = query_df("""
                SELECT close FROM daily_price
                WHERE stock_id=? AND date >= ? ORDER BY date ASC LIMIT 1
            """, (stock_id, target_date))
            if not row.empty:
                prices[d] = float(row.iloc[0]["close"])

        if not prices:
            continue  # 尚無後續資料，等下次再評估

        # 計算各期報酬
        rets = {}
        for d, price in prices.items():
            if entry_price > 0:
                rets[d] = round((price - entry_price) / entry_price * 100, 2)

        # 目前最新報酬（5日或最新可得）
        latest_days  = max(prices.keys())
        latest_price = prices[latest_days]
        latest_ret   = rets.get(latest_days, 0)

        # 判定結果（以5日報酬為準，>1%為WIN，<-2%為LOSS）
        ret5 = rets.get(5, rets.get(3, rets.get(1, 0)))
        if hold_days >= 5:
            status = "WIN" if ret5 > 1.0 else "LOSS"
        else:
            status = "ACTIVE"  # 不到5天，繼續追蹤

        # 更新資料庫
        with get_conn() as conn:
            conn.execute("""
                UPDATE ai_signal_log SET
                    ret_1d=?, ret_3d=?, ret_5d=?, ret_10d=?,
                    hold_days=?, status=?,
                    close_price=?, close_date=?,
                    pnl_pct=?, evaluated_at=datetime('now','localtime')
                WHERE id=?
            """, (
                rets.get(1), rets.get(3), rets.get(5), rets.get(10),
                hold_days, status,
                latest_price if status != "ACTIVE" else None,
                today if status != "ACTIVE" else None,
                latest_ret if status != "ACTIVE" else None,
                sig_id,
            ))
        evaluated += 1

    logger.info(f"AI信號評估完成：{evaluated} 筆")
    return evaluated


def _close_signal(sig_id: int, status: str, close_date: str,
                  close_price: float, pnl_pct: float, hold_days: int):
    with get_conn() as conn:
        conn.execute("""
            UPDATE ai_signal_log SET
                status=?, close_date=?, close_price=?,
                pnl_pct=?, hold_days=?,
                evaluated_at=datetime('now','localtime')
            WHERE id=?
        """, (status, close_date, close_price, pnl_pct, hold_days, sig_id))


# ══════════════════════════════════════════
# 核心：AI 信號自學習
# ══════════════════════════════════════════

def run_signal_self_learning(claude_client) -> str:
    """
    每週自學習：
    1. 分析近期信號成功率
    2. 找出哪些因子組合最準
    3. 用 Claude 生成洞察 + 建議調整門檻
    4. 自動調整信號門檻
    """
    ensure_signal_table()
    today = datetime.now().strftime("%Y-%m-%d")
    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

    # 取得近30天已評估的信號
    df = query_df("""
        SELECT stock_id, signal_date, score, tech_score, chip_score,
               fund_score, env_score, ret_1d, ret_5d, status
        FROM ai_signal_log
        WHERE status IN ('WIN','LOSS')
          AND signal_date >= ?
        ORDER BY signal_date DESC
    """, (cutoff,))

    if df.empty or len(df) < 5:
        return (
            "🧠 AI 信號自學習\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "⚠️ 近30天已評估信號不足5筆\n"
            "繼續累積資料中，自學習需要至少5筆已完成信號"
        )

    wins   = df[df["status"] == "WIN"]
    losses = df[df["status"] == "LOSS"]
    total  = len(df)
    win_rate = len(wins) / total * 100

    avg_ret_5d   = df["ret_5d"].mean() if "ret_5d" in df.columns else 0
    avg_win_ret  = wins["ret_5d"].mean() if not wins.empty else 0
    avg_loss_ret = losses["ret_5d"].mean() if not losses.empty else 0

    # 各因子與勝率的關係
    tech_corr = df["tech_score"].corr(df["ret_5d"]) if len(df) > 5 else 0
    chip_corr = df["chip_score"].corr(df["ret_5d"]) if len(df) > 5 else 0
    fund_corr = df["fund_score"].corr(df["ret_5d"]) if len(df) > 5 else 0

    # 高分（>=70）vs 中分（60-69）勝率對比
    high_score = df[df["score"] >= 70]
    mid_score  = df[(df["score"] >= 60) & (df["score"] < 70)]
    high_wr    = len(high_score[high_score["status"]=="WIN"]) / len(high_score) * 100 if not high_score.empty else 0
    mid_wr     = len(mid_score[mid_score["status"]=="WIN"]) / len(mid_score) * 100 if not mid_score.empty else 0

    # 建議門檻
    cfg = _load_config()
    current_threshold = cfg["buy_score_threshold"]
    suggested_threshold = current_threshold

    if win_rate < 45 and high_wr > mid_wr + 10:
        suggested_threshold = min(current_threshold + 5, 80)
    elif win_rate > 65 and current_threshold > 60:
        suggested_threshold = max(current_threshold - 5, 55)

    stats_text = (
        f"近30天信號統計：\n"
        f"總信號：{total} 個（{len(wins)}成功/{len(losses)}失敗）\n"
        f"整體勝率：{win_rate:.1f}%\n"
        f"平均5日報酬：{avg_ret_5d:.2f}%（勝:{avg_win_ret:.1f}% 敗:{avg_loss_ret:.1f}%）\n"
        f"高分(>=70)勝率：{high_wr:.1f}%（{len(high_score)}筆）\n"
        f"中分(60-69)勝率：{mid_wr:.1f}%（{len(mid_score)}筆）\n"
        f"因子相關性→ 技術:{tech_corr:.2f} 籌碼:{chip_corr:.2f} 基本:{fund_corr:.2f}\n"
        f"目前門檻：{current_threshold}分　建議調整為：{suggested_threshold}分"
    )

    # Claude 深度分析
    prompt = f"""
你是台股量化AI「量化師」的信號分析模組，正在進行週度自學習。

【信號績效數據】
{stats_text}

請完成以下分析（繁體中文）：

1. 📊【本週信號質量評估】（2~3點）
   - 勝率是否達標？（目標：>55%）
   - 哪個因子最能預測成功？
   - 有沒有發現什麼規律？

2. 🔧【門檻調整建議】
   - 是否應調整買入門檻？目前{current_threshold}分，建議{suggested_threshold}分
   - 說明理由

3. 💡【信號改進方向】（2個具體建議）
   - 例如：加入量能篩選、籌碼條件等

請簡潔有力，重點在可執行的改進。
"""
    try:
        from config import CLAUDE_SMART_MODEL
        resp = claude_client.messages.create(
            model=CLAUDE_SMART_MODEL,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        ai_insight = resp.content[0].text
    except Exception as e:
        ai_insight = f"（AI 分析失敗：{e}）"

    # 自動調整門檻
    if suggested_threshold != current_threshold:
        cfg["buy_score_threshold"] = suggested_threshold
        _save_config(cfg)
        logger.info(f"AI信號門檻自動調整：{current_threshold} → {suggested_threshold}")

    # 儲存統計
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO signal_stats
                (report_date, total_signals, win_signals, loss_signals,
                 win_rate, avg_ret_1d, avg_ret_5d, threshold_used,
                 suggested_threshold, report_text)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            today, total, len(wins), len(losses), round(win_rate, 1),
            round(df["ret_1d"].mean() if "ret_1d" in df.columns else 0, 2),
            round(avg_ret_5d, 2),
            current_threshold, suggested_threshold, ai_insight
        ))

    report = (
        f"🧠 AI 信號自學習報告\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📅 {today}\n"
        f"📊 近30天：{total}個信號  勝率 *{win_rate:.1f}%*\n"
        f"📈 平均5日報酬：{avg_ret_5d:+.2f}%\n"
    )
    if suggested_threshold != current_threshold:
        report += f"⚙️ 門檻自動調整：{current_threshold}→{suggested_threshold}分\n"
    report += f"━━━━━━━━━━━━━━━━━━\n{ai_insight}"

    return report


# ══════════════════════════════════════════
# 每日自動執行（由排程器呼叫）
# ══════════════════════════════════════════

def run_daily_signal_scan(claude_client=None) -> str:
    """
    每日收盤後執行：
    1. 產生新買入信號
    2. 評估歷史信號
    3. 回傳今日信號報告
    """
    ensure_signal_table()

    # Step 1：評估歷史信號
    evaluated = evaluate_signals()

    # Step 2：產生新買入信號
    new_signals = generate_buy_signals()

    today = datetime.now().strftime("%Y-%m-%d")
    cfg   = _load_config()

    if not new_signals:
        report = (
            f"🤖 *AI 每日信號掃描*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📅 {today}\n"
            f"🔍 門檻：{cfg['buy_score_threshold']} 分\n"
            f"📊 評估歷史信號：{evaluated} 筆\n\n"
            f"今日無股票達到信號門檻\n"
            f"_繼續監控中..._"
        )
        return report

    lines = [
        f"🤖 *AI 每日信號掃描*",
        f"━━━━━━━━━━━━━━━━━━",
        f"📅 {today}　門檻：{cfg['buy_score_threshold']}分",
        f"📊 評估歷史信號：{evaluated} 筆",
        f"🆕 今日新信號：{len(new_signals)} 支",
        f"━━━━━━━━━━━━━━━━━━",
    ]

    for sig in new_signals:
        lines.append(
            f"🔥 `{sig['stock_id']}` {sig['name']}\n"
            f"   評分 *{sig['score']}分*　現價 ${sig['price']}\n"
            f"   量能 {sig['vol_ratio']:.1f}x 均量\n"
            f"   {sig['reason'][:60]}"
        )

    lines.append(f"━━━━━━━━━━━━━━━━━━")
    lines.append(f"💡 輸入 `信號清單` 查看所有 AI 信號")
    lines.append(f"💡 輸入 `信號績效` 查看信號勝率統計")

    return "\n".join(lines)


# ══════════════════════════════════════════
# 查詢指令（供 main.py 使用）
# ══════════════════════════════════════════

def get_active_signals() -> str:
    """查看目前 ACTIVE 的買入信號"""
    ensure_signal_table()
    df = query_df("""
        SELECT stock_id, stock_name, signal_date, entry_price,
               score, hold_days, ret_1d, ret_5d, status
        FROM ai_signal_log
        WHERE status = 'ACTIVE'
        ORDER BY score DESC, signal_date DESC
        LIMIT 20
    """)

    if df.empty:
        return (
            "📋 *目前無 AI 主動信號*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "系統每日收盤後自動掃描\n"
            "評分達門檻的股票會自動記錄 📌"
        )

    lines = [f"📋 *AI 主動買入信號*  共 {len(df)} 筆\n"]
    for _, r in df.iterrows():
        ret_str = f"  目前報酬：{r['ret_5d']:+.1f}%" if pd.notna(r.get("ret_5d")) else ""
        lines.append(
            f"🔥 `{r['stock_id']}` {r['stock_name']}\n"
            f"   信號日 {r['signal_date']}  評分 *{r['score']}分*  進場 ${r['entry_price']}\n"
            f"   持倉 {int(r['hold_days'])} 天{ret_str}"
        )
    return "\n".join(lines)


def get_signal_performance() -> str:
    """查看 AI 信號整體績效統計"""
    ensure_signal_table()
    df = query_df("""
        SELECT status, score, ret_1d, ret_5d, hold_days, signal_date
        FROM ai_signal_log
        WHERE status IN ('WIN', 'LOSS', 'EXPIRED')
        ORDER BY signal_date DESC
    """)

    if df.empty or len(df) < 2:
        return (
            "📊 *AI 信號績效統計*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "⚠️ 已完成信號不足，繼續累積中\n"
            "每筆信號需 5 個交易日才能評估結果"
        )

    wins   = df[df["status"] == "WIN"]
    losses = df[df["status"] == "LOSS"]
    total  = len(df)
    wr     = len(wins) / total * 100

    avg5  = df["ret_5d"].mean()
    wr_emoji = "🟢" if wr >= 55 else ("🟡" if wr >= 45 else "🔴")

    cfg = _load_config()

    msg = (
        f"📊 *AI 信號績效統計*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔢 已完成信號：{total} 個\n"
        f"{wr_emoji} 整體勝率：*{wr:.1f}%*（{len(wins)}成功/{len(losses)}失敗）\n"
        f"📈 平均5日報酬：*{avg5:+.2f}%*\n"
    )

    if not wins.empty:
        msg += f"💚 勝時平均：{wins['ret_5d'].mean():+.2f}%\n"
    if not losses.empty:
        msg += f"❤️ 敗時平均：{losses['ret_5d'].mean():+.2f}%\n"

    msg += (
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⚙️ 目前買入門檻：*{cfg['buy_score_threshold']} 分*\n"
        f"_門檻每週根據績效自動調整_"
    )
    return msg


def get_signal_history(limit: int = 15) -> str:
    """查看歷史信號記錄"""
    ensure_signal_table()
    df = query_df("""
        SELECT stock_id, stock_name, signal_date, entry_price, close_price,
               score, ret_5d, hold_days, status
        FROM ai_signal_log
        ORDER BY signal_date DESC
        LIMIT ?
    """, (limit,))

    if df.empty:
        return "📋 尚無歷史信號記錄"

    lines = [f"📋 *AI 信號歷史記錄*（最近 {len(df)} 筆）\n"]
    for _, r in df.iterrows():
        if r["status"] == "WIN":
            emoji = "🟢"
        elif r["status"] == "LOSS":
            emoji = "🔴"
        elif r["status"] == "ACTIVE":
            emoji = "🔵"
        else:
            emoji = "⚪"

        ret_str = f"  {r['ret_5d']:+.1f}%" if pd.notna(r.get("ret_5d")) else ""
        lines.append(
            f"{emoji} `{r['stock_id']}` {r.get('stock_name','')}\n"
            f"   {r['signal_date']}  {r['score']}分  ${r['entry_price']}{ret_str}  {r['status']}"
        )
    return "\n".join(lines)
