"""
ml/self_learning.py - AI 自我學習模組
功能：
  1. 每次交易出場後，自動更新該股勝率資料庫
  2. 每週分析哪些策略參數表現最佳
  3. 自動調整每股的最佳停損/停利/持有天數
  4. 寫給 AI 自己看的「操盤日記」
"""
import logging
from datetime import datetime
from database.db_manager import get_conn, query_df

logger = logging.getLogger(__name__)


def update_win_rate(stock_id: str):
    """
    出場後自動更新該股的勝率資料庫
    每次買進/賣出後呼叫
    """
    sql = """
        SELECT pnl_pct, pnl_amount, hold_days
        FROM trade_log
        WHERE stock_id=? AND status='已出場' AND pnl_pct IS NOT NULL
    """
    df = query_df(sql, (stock_id,))
    if df.empty:
        return

    wins   = df[df["pnl_pct"] > 0]
    losses = df[df["pnl_pct"] <= 0]
    total  = len(df)
    win_count  = len(wins)
    loss_count = len(losses)
    win_rate   = win_count / total * 100 if total > 0 else 0
    avg_win    = wins["pnl_pct"].mean() if not wins.empty else 0
    avg_loss   = losses["pnl_pct"].mean() if not losses.empty else 0
    profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else 0
    avg_hold   = df["hold_days"].mean() if "hold_days" in df.columns else 0
    best_pnl   = df["pnl_pct"].max()
    worst_pnl  = df["pnl_pct"].min()
    total_pnl  = df["pnl_amount"].sum() if "pnl_amount" in df.columns else 0

    # 取股票名稱
    name = ""
    with get_conn() as conn:
        row = conn.execute("SELECT name FROM stocks WHERE stock_id=?", (stock_id,)).fetchone()
        if row:
            name = row["name"]

    with get_conn() as conn:
        conn.execute("""
            INSERT INTO win_rate_db
                (stock_id, name, total_trades, wins, losses, win_rate,
                 avg_win_pct, avg_loss_pct, profit_factor, avg_hold_days,
                 best_pnl, worst_pnl, total_pnl, last_updated)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now','localtime'))
            ON CONFLICT(stock_id) DO UPDATE SET
                name=excluded.name,
                total_trades=excluded.total_trades,
                wins=excluded.wins, losses=excluded.losses,
                win_rate=excluded.win_rate,
                avg_win_pct=excluded.avg_win_pct,
                avg_loss_pct=excluded.avg_loss_pct,
                profit_factor=excluded.profit_factor,
                avg_hold_days=excluded.avg_hold_days,
                best_pnl=excluded.best_pnl, worst_pnl=excluded.worst_pnl,
                total_pnl=excluded.total_pnl,
                last_updated=excluded.last_updated
        """, (stock_id, name, total, win_count, loss_count, round(win_rate, 1),
              round(avg_win, 2), round(avg_loss, 2), round(profit_factor, 2),
              round(avg_hold, 1), round(best_pnl, 2), round(worst_pnl, 2),
              round(total_pnl, 0)))

    logger.info(f"勝率更新：{stock_id} 勝率{win_rate:.1f}% ({win_count}勝/{loss_count}敗)")


def optimize_strategy_params(stock_id: str):
    """
    根據歷史交易記錄，找出該股最佳策略參數
    需要至少5次交易記錄才能優化
    """
    sql = """
        SELECT pnl_pct, hold_days, score, price
        FROM trade_log
        WHERE stock_id=? AND status='已出場' AND pnl_pct IS NOT NULL
        ORDER BY created_at DESC LIMIT 50
    """
    df = query_df(sql, (stock_id,))
    if len(df) < 5:
        return None

    # 分析哪些評分水準勝率最高
    high_score = df[df["score"] >= 70]
    mid_score  = df[(df["score"] >= 50) & (df["score"] < 70)]

    best_score_threshold = 60
    if not high_score.empty:
        high_win = len(high_score[high_score["pnl_pct"] > 0]) / len(high_score)
        mid_win  = len(mid_score[mid_score["pnl_pct"] > 0]) / len(mid_score) if not mid_score.empty else 0
        if high_win > mid_win:
            best_score_threshold = 70

    # 分析最佳持有天數（獲利交易的平均持有天數）
    win_trades = df[df["pnl_pct"] > 0]
    best_hold = int(win_trades["hold_days"].mean()) if not win_trades.empty else 10
    best_hold = max(3, min(20, best_hold))

    # 計算整體勝率
    win_rate = len(df[df["pnl_pct"] > 0]) / len(df) * 100

    with get_conn() as conn:
        conn.execute("""
            INSERT INTO strategy_params
                (stock_id, best_hold_days, best_score_threshold,
                 backtest_win_rate, optimized_at, sample_count)
            VALUES (?,?,?,?,datetime('now','localtime'),?)
            ON CONFLICT(stock_id) DO UPDATE SET
                best_hold_days=excluded.best_hold_days,
                best_score_threshold=excluded.best_score_threshold,
                backtest_win_rate=excluded.backtest_win_rate,
                optimized_at=excluded.optimized_at,
                sample_count=excluded.sample_count
        """, (stock_id, best_hold, best_score_threshold,
              round(win_rate, 1), len(df)))

    return {
        "stock_id": stock_id,
        "best_hold_days": best_hold,
        "best_score_threshold": best_score_threshold,
        "win_rate": round(win_rate, 1),
        "sample_count": len(df),
    }


def get_win_rate_report() -> str:
    """生成所有股票的勝率排行報告"""
    df = query_df("""
        SELECT stock_id, name, total_trades, win_rate,
               avg_win_pct, avg_loss_pct, profit_factor, total_pnl
        FROM win_rate_db
        WHERE total_trades >= 1
        ORDER BY win_rate DESC, total_trades DESC
        LIMIT 20
    """)

    if df.empty:
        return "尚無交易記錄，完成幾次買賣後資料將自動累積"

    lines = ["勝率資料庫（依勝率排序）\n"]
    lines.append(f"{'代號':<6} {'名稱':<8} {'次數':>4} {'勝率':>6} {'均獲':>7} {'均虧':>7} {'盈虧比':>6}")
    lines.append("─" * 50)

    for _, r in df.iterrows():
        emoji = "🔥" if r["win_rate"] >= 60 else ("✅" if r["win_rate"] >= 50 else "⚠️")
        lines.append(
            f"{emoji}{r['stock_id']:<5} {str(r['name'])[:6]:<6} "
            f"{int(r['total_trades']):>4}次 "
            f"{r['win_rate']:>5.0f}% "
            f"{r['avg_win_pct']:>+6.1f}% "
            f"{r['avg_loss_pct']:>+6.1f}% "
            f"{r['profit_factor']:>5.1f}x"
        )

    total_pnl = df["total_pnl"].sum()
    lines.append("─" * 50)
    lines.append(f"累計損益：${total_pnl:+,.0f}")

    return "\n".join(lines)


def get_strategy_advice(stock_id: str) -> str:
    """查詢特定股票的 AI 建議參數"""
    with get_conn() as conn:
        sp = conn.execute(
            "SELECT * FROM strategy_params WHERE stock_id=?", (stock_id,)
        ).fetchone()
        wr = conn.execute(
            "SELECT * FROM win_rate_db WHERE stock_id=?", (stock_id,)
        ).fetchone()

    if not wr:
        return f"{stock_id} 尚無歷史交易記錄，系統將使用預設參數"

    lines = [f"{stock_id} AI 策略建議（基於{wr['total_trades']}次交易）\n"]
    lines.append(f"歷史勝率：{wr['win_rate']:.0f}% ({wr['wins']}勝/{wr['losses']}敗)")
    lines.append(f"平均獲利：{wr['avg_win_pct']:+.1f}%  平均虧損：{wr['avg_loss_pct']:+.1f}%")
    lines.append(f"盈虧比：{wr['profit_factor']:.1f}x  累計：${wr['total_pnl']:+,.0f}")

    if sp:
        lines.append(f"\n建議策略參數")
        lines.append(f"停損：{sp['best_stop_loss']*100:.0f}%  停利：{sp['best_take_profit']*100:.0f}%")
        lines.append(f"建議持有：{sp['best_hold_days']} 天")
        lines.append(f"最低進場評分：{sp['best_score_threshold']} 分")

    # 綜合建議
    if wr["win_rate"] >= 60 and wr["profit_factor"] >= 1.5:
        advice = "優質標的，策略有效，可正常交易"
    elif wr["win_rate"] >= 50:
        advice = "表現尚可，建議嚴守停損"
    else:
        advice = "歷史勝率偏低，建議提高進場評分門檻或暫停交易此股"

    lines.append(f"\nAI 建議：{advice}")
    return "\n".join(lines)


def weekly_self_learning(claude_client) -> str:
    """
    每週日執行：
    1. 更新所有股票的勝率資料庫
    2. 優化策略參數
    3. 讓 AI 分析自己的表現並生成改進建議
    """
    # 更新所有有交易記錄的股票
    df = query_df("SELECT DISTINCT stock_id FROM trade_log WHERE status='已出場'")
    updated = []
    for _, row in df.iterrows():
        update_win_rate(row["stock_id"])
        result = optimize_strategy_params(row["stock_id"])
        if result:
            updated.append(result)

    # 整體績效統計
    all_trades = query_df("""
        SELECT pnl_pct, pnl_amount, stock_id
        FROM trade_log WHERE status='已出場' AND pnl_pct IS NOT NULL
    """)

    if all_trades.empty:
        return "尚無足夠交易記錄進行自我學習"

    wins = all_trades[all_trades["pnl_pct"] > 0]
    overall_wr = len(wins) / len(all_trades) * 100
    total_pnl = all_trades["pnl_amount"].sum()

    # 讓 AI 生成改進建議
    prompt = (
        f"你是一個台股量化交易系統的 AI 策略引擎，正在進行每週自我學習檢討。\n\n"
        f"目前整體績效：\n"
        f"總交易次數：{len(all_trades)}\n"
        f"整體勝率：{overall_wr:.1f}%\n"
        f"累計損益：${total_pnl:+,.0f}\n\n"
        f"已優化的股票：{len(updated)} 支\n\n"
        f"請針對以下三點給出改進建議：\n"
        f"1. 如果勝率低於55%，策略需要做什麼調整？\n"
        f"2. 哪些市場環境下策略最有效，哪些應該暫停？\n"
        f"3. 給出3條具體可執行的改進建議\n\n"
        f"請用繁體中文，簡潔有力，每條建議不超過2句話。"
    )

    ai_advice = "（AI 建議生成中）"
    try:
        from config import CLAUDE_FAST_MODEL
        resp = claude_client.messages.create(
            model=CLAUDE_FAST_MODEL,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )
        ai_advice = resp.content[0].text
    except Exception as e:
        ai_advice = f"AI 分析失敗：{e}"

    report = (
        f"AI 自我學習報告 {datetime.now().strftime('%Y/%m/%d')}\n"
        f"══════════════════════\n"
        f"更新股票數：{len(updated)} 支\n"
        f"整體勝率：{overall_wr:.1f}%\n"
        f"累計損益：${total_pnl:+,.0f}\n\n"
        f"AI 改進建議：\n{ai_advice}"
    )
    return report
