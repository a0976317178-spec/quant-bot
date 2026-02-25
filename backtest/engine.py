"""
backtest/engine.py - 歷史回測引擎
Walk-Forward 回測，計算：勝率、最大虧損、年化報酬、夏普比率
"""
import pandas as pd
import numpy as np
import logging
from datetime import datetime, timedelta
from database.db_manager import query_df

logger = logging.getLogger(__name__)


def run_backtest(
    stock_ids: list = None,
    start_date: str = "2022-01-01",
    end_date: str = None,
    entry_threshold: float = 0.60,   # ML 信號門檻
    stop_loss_pct: float = 0.05,     # 停損 5%
    take_profit_pct: float = 0.10,   # 停利 10%
    hold_days: int = 5,              # 最長持有天數
    capital: float = 1_000_000,      # 初始資金
    max_positions: int = 5,          # 最大同時持股數
) -> dict:
    """
    執行歷史回測
    """
    if not end_date:
        end_date = datetime.now().strftime("%Y-%m-%d")

    # 取得有標籤的歷史數據
    sql = """
        SELECT f.stock_id, f.date, f.label,
               p.open, p.high, p.low, p.close, p.volume
        FROM factor_cache f
        JOIN daily_price p ON f.stock_id = p.stock_id AND f.date = p.date
        WHERE f.date >= ? AND f.date <= ?
          AND f.label IS NOT NULL
        ORDER BY f.date, f.stock_id
    """
    df = query_df(sql, (start_date, end_date))

    if df.empty:
        return {
            "error": "資料庫中無回測資料，請先執行因子計算與標籤建立",
            "total_trades": 0,
        }

    trades = []
    equity_curve = [capital]
    current_capital = capital
    current_positions = {}
    dates = sorted(df["date"].unique())

    for i, date in enumerate(dates):
        day_data = df[df["date"] == date]

        # 平倉：檢查持有中的股票
        to_close = []
        for sid, pos in current_positions.items():
            price_row = day_data[day_data["stock_id"] == sid]
            if price_row.empty:
                continue

            high = price_row.iloc[0]["high"]
            low = price_row.iloc[0]["low"]
            close = price_row.iloc[0]["close"]
            entry = pos["entry_price"]
            hold = pos["hold_days"]

            exit_price = None
            exit_reason = ""

            if low <= entry * (1 - stop_loss_pct):
                exit_price = entry * (1 - stop_loss_pct)
                exit_reason = "停損"
            elif high >= entry * (1 + take_profit_pct):
                exit_price = entry * (1 + take_profit_pct)
                exit_reason = "停利"
            elif hold >= hold_days:
                exit_price = close
                exit_reason = "持有到期"

            if exit_price:
                pnl = (exit_price - entry) / entry
                pnl_amount = pnl * pos["capital_used"]
                current_capital += pos["capital_used"] + pnl_amount

                trades.append({
                    "stock_id": sid,
                    "entry_date": pos["entry_date"],
                    "exit_date": date,
                    "entry_price": entry,
                    "exit_price": round(exit_price, 2),
                    "pnl_pct": round(pnl * 100, 2),
                    "pnl_amount": round(pnl_amount, 0),
                    "exit_reason": exit_reason,
                    "hold_days": hold,
                })
                to_close.append(sid)
            else:
                current_positions[sid]["hold_days"] += 1

        for sid in to_close:
            del current_positions[sid]

        # 進場：篩選今日信號
        if len(current_positions) < max_positions:
            signals = day_data[day_data["label"] == 1]
            available_slots = max_positions - len(current_positions)

            for _, row in signals.head(available_slots).iterrows():
                sid = row["stock_id"]
                if sid in current_positions:
                    continue

                capital_per_trade = current_capital / max_positions
                if capital_per_trade < 10000:
                    continue

                current_positions[sid] = {
                    "entry_price": row["close"],
                    "entry_date": date,
                    "capital_used": capital_per_trade,
                    "hold_days": 0,
                }
                current_capital -= capital_per_trade

        total_value = current_capital + sum(
            p["capital_used"] for p in current_positions.values()
        )
        equity_curve.append(total_value)

    # ── 績效計算 ──────────────────────────────────
    if not trades:
        return {"error": "回測期間無交易記錄", "total_trades": 0}

    trades_df = pd.DataFrame(trades)
    wins = trades_df[trades_df["pnl_pct"] > 0]
    losses = trades_df[trades_df["pnl_pct"] <= 0]

    win_rate = len(wins) / len(trades_df) * 100
    avg_win = wins["pnl_pct"].mean() if len(wins) > 0 else 0
    avg_loss = losses["pnl_pct"].mean() if len(losses) > 0 else 0
    profit_factor = abs(wins["pnl_amount"].sum() / losses["pnl_amount"].sum()) if len(losses) > 0 and losses["pnl_amount"].sum() != 0 else float("inf")

    equity = pd.Series(equity_curve)
    total_return = (equity.iloc[-1] - equity.iloc[0]) / equity.iloc[0] * 100

    # 最大回撤
    rolling_max = equity.cummax()
    drawdown = (equity - rolling_max) / rolling_max
    max_drawdown = drawdown.min() * 100

    # 年化報酬
    days = (datetime.strptime(end_date, "%Y-%m-%d") - datetime.strptime(start_date, "%Y-%m-%d")).days
    annual_return = ((1 + total_return / 100) ** (365 / max(days, 1)) - 1) * 100 if days > 0 else 0

    # 夏普比率（簡化版）
    daily_returns = equity.pct_change().dropna()
    sharpe = (daily_returns.mean() / daily_returns.std() * np.sqrt(252)) if daily_returns.std() > 0 else 0

    return {
        "start_date": start_date,
        "end_date": end_date,
        "initial_capital": capital,
        "final_capital": round(equity.iloc[-1], 0),
        "total_return": round(total_return, 2),
        "annual_return": round(annual_return, 2),
        "max_drawdown": round(max_drawdown, 2),
        "sharpe_ratio": round(sharpe, 2),
        "total_trades": len(trades_df),
        "win_rate": round(win_rate, 2),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2),
        "win_count": len(wins),
        "loss_count": len(losses),
        "trades": trades_df.tail(20).to_dict("records"),
    }


def format_backtest_report(result: dict) -> str:
    """將回測結果格式化為 TG 訊息"""
    if "error" in result:
        return f"❌ 回測失敗：{result['error']}"

    total_pnl = result["final_capital"] - result["initial_capital"]
    pnl_emoji = "🟢" if total_pnl > 0 else "🔴"

    return (
        f"📊 回測報告\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📅 期間：{result['start_date']} ~ {result['end_date']}\n\n"
        f"💰 資金績效\n"
        f"初始資金：${result['initial_capital']:,.0f}\n"
        f"最終資金：${result['final_capital']:,.0f}\n"
        f"{pnl_emoji} 總損益：${total_pnl:+,.0f}（{result['total_return']:+.2f}%）\n"
        f"📈 年化報酬：{result['annual_return']:+.2f}%\n"
        f"📉 最大回撤：{result['max_drawdown']:.2f}%\n"
        f"⚡ 夏普比率：{result['sharpe_ratio']:.2f}\n\n"
        f"🎯 交易統計\n"
        f"總交易次數：{result['total_trades']} 次\n"
        f"勝率：{result['win_rate']:.1f}%"
        f"（{result['win_count']}勝 / {result['loss_count']}敗）\n"
        f"平均獲利：+{result['avg_win_pct']:.2f}%\n"
        f"平均虧損：{result['avg_loss_pct']:.2f}%\n"
        f"獲利因子：{result['profit_factor']:.2f}\n"
    )
