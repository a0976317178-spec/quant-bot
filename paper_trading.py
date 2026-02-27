"""
paper_trading.py - AI 自動模擬交易引擎

功能：
  1. AI 看到高評分信號 → 自動模擬「買進」（不動真錢）
  2. 追蹤每筆模擬倉位的損益
  3. 自動觸發停損（-5%）或停利（+10%）模擬出場
  4. 每週統計模擬勝率，驗證策略是否可行
  5. 當模擬勝率達標（>55% + 20筆以上），系統推送「可考慮實盤」提醒

用途：先讓 AI 跑幾個月模擬，確認策略真的有效再投入真金白銀。
"""
import json
import logging
import os
from datetime import datetime, timedelta
import pandas as pd
from database.db_manager import get_conn, query_df

logger = logging.getLogger(__name__)

PAPER_CONFIG_FILE = "data/paper_config.json"
DEFAULT_CONFIG = {
    "enabled": True,                # 是否啟用模擬交易
    "capital": 1_000_000,           # 模擬本金（元）
    "max_positions": 5,             # 最大同時持倉數
    "position_pct": 0.15,           # 每次投入 15%
    "stop_loss_pct": 0.05,          # 停損 5%
    "take_profit_pct": 0.10,        # 停利 10%
    "min_score": 65,                # 最低進場評分
    "min_vol_ratio": 1.0,           # 最低量能倍數
    "max_hold_days": 20,            # 最長持有天數
    "live_threshold_wr": 55.0,      # 達到此勝率建議實盤
    "live_threshold_trades": 20,    # 達到此交易筆數建議實盤
}


# ══════════════════════════════════════════
# 設定管理
# ══════════════════════════════════════════

def load_config() -> dict:
    os.makedirs("data", exist_ok=True)
    if os.path.exists(PAPER_CONFIG_FILE):
        cfg = DEFAULT_CONFIG.copy()
        try:
            with open(PAPER_CONFIG_FILE) as f:
                cfg.update(json.load(f))
        except Exception:
            pass
        return cfg
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict):
    os.makedirs("data", exist_ok=True)
    with open(PAPER_CONFIG_FILE, "w") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════
# 資料表確認
# ══════════════════════════════════════════

def ensure_tables():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_id    TEXT NOT NULL,
                stock_name  TEXT DEFAULT '',
                action      TEXT NOT NULL,         -- BUY / SELL
                price       REAL NOT NULL,
                shares      INTEGER DEFAULT 1,
                amount      REAL DEFAULT 0,        -- 投入金額
                score       INTEGER DEFAULT 0,
                signal_reason TEXT DEFAULT '',
                stop_loss   REAL DEFAULT 0,
                take_profit REAL DEFAULT 0,
                entry_date  TEXT,
                exit_date   TEXT,
                exit_price  REAL,
                exit_reason TEXT DEFAULT '',       -- STOP_LOSS / TAKE_PROFIT / TIMEOUT / MANUAL
                pnl_pct     REAL,
                pnl_amount  REAL,
                hold_days   INTEGER DEFAULT 0,
                status      TEXT DEFAULT 'OPEN',   -- OPEN / CLOSED
                triggered_by TEXT DEFAULT 'AI',    -- AI / MANUAL
                created_at  TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(stock_id, entry_date)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_summary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_date   TEXT NOT NULL,
                total_trades  INTEGER DEFAULT 0,
                open_trades   INTEGER DEFAULT 0,
                closed_trades INTEGER DEFAULT 0,
                wins          INTEGER DEFAULT 0,
                losses        INTEGER DEFAULT 0,
                win_rate      REAL DEFAULT 0,
                total_pnl     REAL DEFAULT 0,
                avg_win_pct   REAL DEFAULT 0,
                avg_loss_pct  REAL DEFAULT 0,
                profit_factor REAL DEFAULT 0,
                current_capital REAL DEFAULT 0,
                ready_for_live INTEGER DEFAULT 0,  -- 0/1
                report_text   TEXT DEFAULT '',
                created_at    TEXT DEFAULT (datetime('now','localtime'))
            )
        """)


# ══════════════════════════════════════════
# 核心：AI 自動模擬買進
# ══════════════════════════════════════════

def auto_paper_buy(stock_id: str, price: float, score: int,
                   stock_name: str = "", reason: str = "") -> dict | None:
    """
    AI 自動模擬買進
    符合條件才買，同一股票同一天不重複
    """
    ensure_tables()
    cfg   = load_config()
    today = datetime.now().strftime("%Y-%m-%d")

    if not cfg["enabled"]:
        return None

    # 確認不超過最大持倉數
    open_count = query_df(
        "SELECT COUNT(*) as cnt FROM paper_trades WHERE status='OPEN'"
    ).iloc[0]["cnt"]
    if open_count >= cfg["max_positions"]:
        logger.info(f"模擬倉位已滿（{open_count}/{cfg['max_positions']}），跳過 {stock_id}")
        return None

    # 計算模擬買進金額
    invest_amount = cfg["capital"] * cfg["position_pct"]
    cost_per_lot  = price * 1000
    shares        = max(1, int(invest_amount / cost_per_lot))
    actual_amount = shares * cost_per_lot

    stop_loss   = round(price * (1 - cfg["stop_loss_pct"]), 2)
    take_profit = round(price * (1 + cfg["take_profit_pct"]), 2)

    try:
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO paper_trades
                    (stock_id, stock_name, action, price, shares, amount,
                     score, signal_reason, stop_loss, take_profit,
                     entry_date, status, triggered_by)
                VALUES (?,?,  'BUY', ?,?,?,  ?,?,?,?,  ?, 'OPEN', 'AI')
                ON CONFLICT(stock_id, entry_date) DO NOTHING
            """, (
                stock_id, stock_name, price, shares, actual_amount,
                score, reason[:100], stop_loss, take_profit, today
            ))

        logger.info(f"📝 模擬買進：{stock_id} ${price} ×{shares}張  評分{score}分")
        return {
            "stock_id":   stock_id,
            "name":       stock_name,
            "price":      price,
            "shares":     shares,
            "amount":     actual_amount,
            "stop_loss":  stop_loss,
            "take_profit": take_profit,
            "score":      score,
        }
    except Exception as e:
        logger.error(f"模擬買進失敗 {stock_id}: {e}")
        return None


# ══════════════════════════════════════════
# 核心：每日自動評估持倉
# ══════════════════════════════════════════

def daily_paper_evaluation() -> list:
    """
    每天收盤後評估所有 OPEN 模擬倉位：
    - 觸及停損 → 自動模擬停損出場
    - 觸及停利 → 自動模擬停利出場
    - 超過最大持有天數 → 強制出場
    回傳本次出場的交易列表
    """
    ensure_tables()
    cfg   = load_config()
    today = datetime.now().strftime("%Y-%m-%d")

    opens = query_df("""
        SELECT id, stock_id, price, shares, amount,
               stop_loss, take_profit, entry_date
        FROM paper_trades WHERE status='OPEN'
    """)

    if opens.empty:
        return []

    closed = []

    for _, pos in opens.iterrows():
        stock_id    = pos["stock_id"]
        entry_price = float(pos["price"])
        pos_id      = int(pos["id"])
        entry_date  = pos["entry_date"]
        hold_days   = (
            datetime.strptime(today, "%Y-%m-%d") -
            datetime.strptime(entry_date, "%Y-%m-%d")
        ).days

        # 取最新收盤價
        row = query_df("""
            SELECT close FROM daily_price
            WHERE stock_id=? ORDER BY date DESC LIMIT 1
        """, (stock_id,))
        if row.empty:
            continue

        current_price = float(row.iloc[0]["close"])
        pnl_pct       = (current_price - entry_price) / entry_price * 100
        pnl_amount    = (current_price - entry_price) * int(pos["shares"]) * 1000

        # 判斷出場條件
        exit_reason = None
        if current_price <= float(pos["stop_loss"]):
            exit_reason = "STOP_LOSS"
        elif current_price >= float(pos["take_profit"]):
            exit_reason = "TAKE_PROFIT"
        elif hold_days >= cfg["max_hold_days"]:
            exit_reason = "TIMEOUT"

        if exit_reason:
            with get_conn() as conn:
                conn.execute("""
                    UPDATE paper_trades SET
                        status='CLOSED', exit_date=?, exit_price=?,
                        exit_reason=?, pnl_pct=?, pnl_amount=?, hold_days=?
                    WHERE id=?
                """, (today, current_price, exit_reason,
                      round(pnl_pct, 2), round(pnl_amount, 0), hold_days, pos_id))

            closed.append({
                "stock_id":    stock_id,
                "entry_price": entry_price,
                "exit_price":  current_price,
                "pnl_pct":     round(pnl_pct, 2),
                "pnl_amount":  round(pnl_amount, 0),
                "hold_days":   hold_days,
                "exit_reason": exit_reason,
            })
            logger.info(
                f"📊 模擬出場：{stock_id} {exit_reason} "
                f"${entry_price}→${current_price} {pnl_pct:+.1f}%"
            )

    return closed


# ══════════════════════════════════════════
# 主流程：每日完整模擬交易週期
# ══════════════════════════════════════════

def run_daily_paper_trading(claude_client=None) -> str:
    """
    每日完整模擬交易流程（排程器 15:45 呼叫）：
    1. 評估現有倉位（停損/停利/到期）
    2. 掃描新買入信號
    3. 自動建立新模擬倉位
    4. 回傳日報
    """
    ensure_tables()
    cfg   = load_config()
    today = datetime.now().strftime("%Y-%m-%d")

    if not cfg["enabled"]:
        return "⏸️ 模擬交易已暫停"

    # Step 1：評估現有倉位
    closed_today = daily_paper_evaluation()

    # Step 2：掃描新信號
    new_positions = []
    try:
        from memory.daily_learning import load_watchlist
        from factors.analyzer import full_analysis

        watchlist = load_watchlist()
        open_stocks = {
            r["stock_id"] for _, r in
            query_df("SELECT stock_id FROM paper_trades WHERE status='OPEN'").iterrows()
        } if not query_df("SELECT stock_id FROM paper_trades WHERE status='OPEN'").empty else set()

        for stock_id in watchlist:
            if stock_id in open_stocks:
                continue  # 已持倉不重複買
            try:
                result = full_analysis(stock_id)
                score  = result.get("total_score", 0)
                if score < cfg["min_score"]:
                    continue
                tech_detail = result.get("tech", {}).get("detail", {})
                if tech_detail.get("vol_ratio", 1) < cfg["min_vol_ratio"]:
                    continue
                if not result.get("market_ok", True):
                    continue

                trade = auto_paper_buy(
                    stock_id   = stock_id,
                    price      = result.get("close_price", 0),
                    score      = score,
                    stock_name = result.get("name", ""),
                    reason     = (
                        f"T:{result['tech']['score']} "
                        f"C:{result['chip']['score']} "
                        f"F:{result['fund']['score']}"
                    )
                )
                if trade:
                    new_positions.append(trade)
            except Exception as e:
                logger.debug(f"模擬分析 {stock_id} 失敗: {e}")

    except Exception as e:
        logger.error(f"模擬買入掃描失敗: {e}")

    # Step 3：組成日報
    lines = [
        f"🎮 *模擬交易日報*",
        f"━━━━━━━━━━━━━━━━━━",
        f"📅 {today}",
    ]

    if closed_today:
        lines.append(f"\n📤 今日出場 {len(closed_today)} 筆：")
        for t in closed_today:
            emoji   = "🟢" if t["pnl_pct"] > 0 else "🔴"
            reason  = {"STOP_LOSS": "停損", "TAKE_PROFIT": "停利", "TIMEOUT": "到期"}.get(t["exit_reason"], t["exit_reason"])
            lines.append(
                f"{emoji} `{t['stock_id']}` "
                f"${t['entry_price']}→${t['exit_price']} "
                f"*{t['pnl_pct']:+.1f}%* （{reason}，持{t['hold_days']}天）"
            )

    if new_positions:
        lines.append(f"\n📥 今日新倉 {len(new_positions)} 筆：")
        for p in new_positions:
            lines.append(
                f"🔵 `{p['stock_id']}` {p['name']} "
                f"${p['price']} ×{p['shares']}張  評分*{p['score']}分*\n"
                f"   停損 ${p['stop_loss']} ／ 停利 ${p['take_profit']}"
            )

    if not closed_today and not new_positions:
        lines.append("\n今日無出場也無新倉\n繼續追蹤現有持倉中...")

    # 整體績效摘要
    stats = get_paper_stats()
    if stats["closed"] > 0:
        lines.append(
            f"\n━━━━━━━━━━━━━━━━━━\n"
            f"📊 累計：{stats['closed']}筆  勝率 *{stats['win_rate']:.1f}%*  "
            f"損益 *{stats['total_pnl']:+,.0f}元*"
        )
        # 是否可以考慮實盤
        if (stats["win_rate"] >= cfg["live_threshold_wr"] and
                stats["closed"] >= cfg["live_threshold_trades"]):
            lines.append(
                f"\n🚀 *模擬策略已達標！*\n"
                f"勝率 {stats['win_rate']:.1f}% ≥ {cfg['live_threshold_wr']}%\n"
                f"交易筆數 {stats['closed']} ≥ {cfg['live_threshold_trades']} 筆\n"
                f"_可考慮開始小額實盤驗證_"
            )

    return "\n".join(lines)


# ══════════════════════════════════════════
# 統計與查詢
# ══════════════════════════════════════════

def get_paper_stats() -> dict:
    """取得模擬交易整體統計"""
    ensure_tables()
    df = query_df("""
        SELECT status, pnl_pct, pnl_amount, hold_days
        FROM paper_trades WHERE status='CLOSED'
    """)

    if df.empty:
        return {
            "closed": 0, "wins": 0, "losses": 0, "win_rate": 0,
            "total_pnl": 0, "avg_win": 0, "avg_loss": 0, "profit_factor": 0,
        }

    wins   = df[df["pnl_pct"] > 0]
    losses = df[df["pnl_pct"] <= 0]
    total  = len(df)
    wr     = len(wins) / total * 100 if total else 0
    avg_w  = wins["pnl_pct"].mean() if not wins.empty else 0
    avg_l  = losses["pnl_pct"].mean() if not losses.empty else 0
    pf     = abs(avg_w / avg_l) if avg_l != 0 else 0

    return {
        "closed":        total,
        "wins":          len(wins),
        "losses":        len(losses),
        "win_rate":      round(wr, 1),
        "total_pnl":     round(df["pnl_amount"].sum(), 0),
        "avg_win":       round(avg_w, 2),
        "avg_loss":      round(avg_l, 2),
        "profit_factor": round(pf, 2),
    }


def get_paper_portfolio() -> str:
    """查看目前模擬持倉"""
    ensure_tables()
    df = query_df("""
        SELECT p.stock_id, p.stock_name, p.price, p.shares, p.amount,
               p.stop_loss, p.take_profit, p.score, p.entry_date,
               (SELECT close FROM daily_price WHERE stock_id=p.stock_id
                ORDER BY date DESC LIMIT 1) as current_price
        FROM paper_trades p
        WHERE p.status='OPEN'
        ORDER BY p.score DESC
    """)

    if df.empty:
        cfg = load_config()
        return (
            f"📋 *模擬持倉*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"目前無模擬持倉\n\n"
            f"⚙️ 模擬設定：門檻 {cfg['min_score']}分  "
            f"停損 {cfg['stop_loss_pct']*100:.0f}%  "
            f"停利 {cfg['take_profit_pct']*100:.0f}%"
        )

    lines = [f"📋 *模擬持倉*  共 {len(df)} 支\n"]
    total_invested = 0

    for _, r in df.iterrows():
        cp = float(r["current_price"]) if pd.notna(r.get("current_price")) else float(r["price"])
        ep = float(r["price"])
        pnl = (cp - ep) / ep * 100
        pnl_emoji = "🟢" if pnl > 0 else "🔴"
        total_invested += float(r["amount"])
        lines.append(
            f"{pnl_emoji} `{r['stock_id']}` {r.get('stock_name','')}\n"
            f"   進場 ${ep}  現價 ${cp}  *{pnl:+.1f}%*\n"
            f"   停損 ${r['stop_loss']}  停利 ${r['take_profit']}\n"
            f"   評分 {r['score']}分  進場 {r['entry_date']}"
        )

    lines.append(f"\n💰 模擬投入總額：${total_invested:,.0f}")
    return "\n".join(lines)


def get_paper_report() -> str:
    """取得完整模擬績效報告"""
    ensure_tables()
    stats = get_paper_stats()
    cfg   = load_config()

    if stats["closed"] == 0:
        return (
            "📊 *模擬交易績效報告*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "⚠️ 尚無已完成的模擬交易\n\n"
            f"系統每天 15:45 自動掃描信號並建倉\n"
            f"門檻設定：評分 ≥ {cfg['min_score']} 分\n"
            f"確認自選股清單有股票後等待自動運行 😊"
        )

    wr_emoji = "🟢" if stats["win_rate"] >= 55 else ("🟡" if stats["win_rate"] >= 45 else "🔴")
    pnl_emoji = "📈" if stats["total_pnl"] >= 0 else "📉"
    ready = (stats["win_rate"] >= cfg["live_threshold_wr"] and
             stats["closed"] >= cfg["live_threshold_trades"])

    # 最近5筆
    recent = query_df("""
        SELECT stock_id, entry_date, exit_date, price, exit_price,
               pnl_pct, exit_reason, hold_days
        FROM paper_trades WHERE status='CLOSED'
        ORDER BY exit_date DESC LIMIT 5
    """)

    msg = (
        f"📊 *模擬交易績效報告*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔢 總交易筆數：{stats['closed']} 筆\n"
        f"{wr_emoji} 模擬勝率：*{stats['win_rate']:.1f}%*"
        f"（{stats['wins']}勝/{stats['losses']}敗）\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💚 均獲利：*{stats['avg_win']:+.2f}%*\n"
        f"❤️  均虧損：*{stats['avg_loss']:+.2f}%*\n"
        f"⚖️  盈虧比：*{stats['profit_factor']:.1f}x*\n"
        f"{pnl_emoji} 模擬損益：*${stats['total_pnl']:+,.0f}元*\n"
    )

    if not recent.empty:
        msg += "\n📋 最近5筆：\n"
        for _, r in recent.iterrows():
            e = "🟢" if r["pnl_pct"] > 0 else "🔴"
            reason_map = {"STOP_LOSS":"停損","TAKE_PROFIT":"停利","TIMEOUT":"到期"}
            reason = reason_map.get(r["exit_reason"], r["exit_reason"])
            msg += f"{e} `{r['stock_id']}` {r['pnl_pct']:+.1f}% ({reason})\n"

    msg += f"\n{'━'*18}\n"
    if ready:
        msg += (
            f"🚀 *策略已達實盤標準！*\n"
            f"勝率 {stats['win_rate']:.1f}% ≥ {cfg['live_threshold_wr']}%\n"
            f"筆數 {stats['closed']} ≥ {cfg['live_threshold_trades']} 筆\n"
            f"建議：先以小資金（10%）開始實盤驗證"
        )
    else:
        remaining = max(0, cfg["live_threshold_trades"] - stats["closed"])
        msg += (
            f"⏳ *距離實盤標準*\n"
            f"還需 {remaining} 筆 交易記錄\n"
            f"目標勝率：{cfg['live_threshold_wr']}%（目前 {stats['win_rate']:.1f}%）"
        )

    return msg


def update_paper_config(key: str, value) -> str:
    """更新模擬交易設定"""
    cfg = load_config()
    param_map = {
        "門檻": "min_score",
        "停損": "stop_loss_pct",
        "停利": "take_profit_pct",
        "本金": "capital",
        "最大持股": "max_positions",
        "每次比例": "position_pct",
        "啟用": "enabled",
    }
    real_key = param_map.get(key, key)
    if real_key not in cfg:
        return f"❌ 找不到參數：{key}"
    cfg[real_key] = value
    save_config(cfg)
    return f"✅ 模擬設定更新：{key} = {value}"


def get_paper_config_summary() -> str:
    """顯示模擬設定"""
    cfg = load_config()
    stats = get_paper_stats()
    status = "🟢 運行中" if cfg["enabled"] else "🔴 已暫停"

    return (
        f"⚙️ *模擬交易設定*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"狀態：{status}\n"
        f"模擬本金：${cfg['capital']:,.0f}\n"
        f"每次投入：{cfg['position_pct']*100:.0f}%\n"
        f"最大持倉：{cfg['max_positions']} 支\n"
        f"進場門檻：評分 ≥ {cfg['min_score']} 分\n"
        f"停損設定：{cfg['stop_loss_pct']*100:.0f}%\n"
        f"停利設定：{cfg['take_profit_pct']*100:.0f}%\n"
        f"最長持有：{cfg['max_hold_days']} 天\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"累計模擬：{stats['closed']} 筆  勝率 {stats['win_rate']:.1f}%\n"
        f"達標標準：勝率 ≥ {cfg['live_threshold_wr']}%  且  ≥ {cfg['live_threshold_trades']} 筆\n\n"
        f"修改範例：`模擬設定 門檻 70`\n"
        f"          `模擬設定 停損 0.06`"
    )
