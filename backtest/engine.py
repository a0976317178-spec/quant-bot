"""
backtest/engine.py - 歷史回測引擎（修復版）
修復：
  1. 參數名改為 stock_id（main.py 呼叫時用的是 stock_id= 不是 stock_ids=）
  2. 不再依賴 factor_cache 表的 label（大多數環境沒有這張表的資料）
  3. 改用 rule-based 策略（MA+RSI信號）直接從 daily_price 跑回測
  4. 支援單一股票精確回測，也支援多股回測
"""
import pandas as pd
import numpy as np
import logging
from datetime import datetime, timedelta
from database.db_manager import query_df

logger = logging.getLogger(__name__)


def _get_price_data(stock_id: str, start_date: str, end_date: str) -> pd.DataFrame:
    """
    從資料庫取得某股歷史價格（含均線計算用資料）
    多抓60天歷史供 MA60 計算
    """
    # 多往前抓60天，確保第一個交易日能算出完整均線
    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        fetch_from = (start_dt - timedelta(days=90)).strftime("%Y-%m-%d")
    except Exception:
        fetch_from = "2020-01-01"

    sql = """
        SELECT date, open, high, low, close, volume
        FROM daily_price
        WHERE stock_id = ? AND date >= ? AND date <= ?
        ORDER BY date ASC
    """
    df = query_df(sql, (stock_id, fetch_from, end_date))
    if df.empty:
        return pd.DataFrame()

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    # ✅ 成交量轉為「張」（除以1000）
    df["volume"] = (df["volume"] / 1000).astype(int)

    # 計算技術指標
    df["ma5"]  = df["close"].rolling(5).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma60"] = df["close"].rolling(60).mean()
    df["vol_ma20"] = df["volume"].rolling(20).mean()

    # RSI
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss.replace(0, 0.001)
    df["rsi"] = 100 - (100 / (1 + rs))

    # 乖離率
    df["bias20"] = (df["close"] - df["ma20"]) / df["ma20"] * 100

    # 過濾到回測區間
    df = df[df["date"] >= pd.to_datetime(start_date)].reset_index(drop=True)
    return df


def _generate_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    rule-based 買進信號（不需要 factor_cache）
    買進條件（同時滿足）：
      1. 股價在 MA20 之上
      2. MA20 > MA60（多頭排列）
      3. RSI 在 40~70（動能健康，非超買）
      4. 成交量 >= 0.8倍均量（非死水）
      5. 乖離率 > -8%（非過度偏低）
    """
    df = df.copy()
    df["signal"] = 0

    # 避免 NaN
    mask = (
        df["close"].notna() &
        df["ma20"].notna() &
        df["ma60"].notna() &
        df["rsi"].notna()
    )

    buy_cond = (
        mask &
        (df["close"] > df["ma20"]) &
        (df["ma20"] > df["ma60"]) &
        (df["rsi"] >= 40) & (df["rsi"] <= 70) &
        (df["volume"] >= df["vol_ma20"] * 0.8) &
        (df["bias20"] > -8)
    )
    df.loc[buy_cond, "signal"] = 1
    return df


def run_backtest(
    stock_id: str = None,          # ✅ 修復：改為 stock_id（singular），和 main.py 一致
    stock_ids: list = None,        # 向後相容：傳 list 也可以
    start_date: str = None,
    end_date: str = None,
    stop_loss_pct: float = 0.05,
    take_profit_pct: float = 0.10,
    hold_days: int = 10,
    capital: float = 1_000_000,
    max_positions: int = 5,
) -> dict:
    """
    執行歷史回測
    - 單一股票：run_backtest(stock_id="1605", start_date="2022-01-01")
    - 多股票  ：run_backtest(stock_ids=["2330","2317"], start_date="2022-01-01")
    """
    if not end_date:
        end_date = datetime.now().strftime("%Y-%m-%d")
    if not start_date:
        start_date = (datetime.now() - timedelta(days=365 * 2)).strftime("%Y-%m-%d")

    # ✅ 統一處理：stock_id 或 stock_ids 都能用
    if stock_id:
        targets = [stock_id]
    elif stock_ids:
        targets = stock_ids
    else:
        return {"error": "請指定股票代號", "total_trades": 0}

    # 取得所有股票的歷史資料
    all_dfs = {}
    for sid in targets:
        df = _get_price_data(sid, start_date, end_date)
        if df.empty:
            logger.warning(f"{sid} 無歷史資料，略過")
            continue
        df = _generate_signals(df)
        all_dfs[sid] = df

    if not all_dfs:
        return {
            "error": (
                f"資料庫中找不到 {','.join(targets)} 的歷史資料\n"
                f"請先執行「爬取 2022」下載歷史股價"
            ),
            "total_trades": 0,
        }

    # ── 回測執行（逐日掃描）─────────────────────────────
    trades = []
    equity_curve = [capital]
    current_capital = capital
    current_positions = {}   # {sid: {entry_price, entry_date, capital_used, hold_days}}

    # 取所有交易日（聯集）
    all_dates = sorted(set(
        d for df in all_dfs.values() for d in df["date"].tolist()
    ))

    for date in all_dates:

        # 平倉：檢查持有中的股票
        to_close = []
        for sid, pos in current_positions.items():
            if sid not in all_dfs:
                continue
            day_row = all_dfs[sid][all_dfs[sid]["date"] == date]
            if day_row.empty:
                pos["hold_days"] += 1
                continue

            high  = day_row.iloc[0]["high"]
            low   = day_row.iloc[0]["low"]
            close = day_row.iloc[0]["close"]
            entry = pos["entry_price"]
            hold  = pos["hold_days"]

            exit_price  = None
            exit_reason = ""

            if low <= entry * (1 - stop_loss_pct):
                exit_price  = round(entry * (1 - stop_loss_pct), 2)
                exit_reason = "停損"
            elif high >= entry * (1 + take_profit_pct):
                exit_price  = round(entry * (1 + take_profit_pct), 2)
                exit_reason = "停利"
            elif hold >= hold_days:
                exit_price  = close
                exit_reason = "持有到期"

            if exit_price:
                pnl     = (exit_price - entry) / entry
                pnl_amt = pnl * pos["capital_used"]
                current_capital += pos["capital_used"] + pnl_amt

                trades.append({
                    "stock_id":    sid,
                    "entry_date":  pos["entry_date"].strftime("%Y-%m-%d")
                                   if hasattr(pos["entry_date"], "strftime")
                                   else str(pos["entry_date"]),
                    "exit_date":   date.strftime("%Y-%m-%d")
                                   if hasattr(date, "strftime") else str(date),
                    "entry_price": entry,
                    "exit_price":  exit_price,
                    "pnl_pct":     round(pnl * 100, 2),
                    "pnl_amount":  round(pnl_amt, 0),
                    "exit_reason": exit_reason,
                    "hold_days":   hold,
                })
                to_close.append(sid)
            else:
                current_positions[sid]["hold_days"] += 1

        for sid in to_close:
            del current_positions[sid]

        # 進場：今日有信號的股票
        if len(current_positions) < max_positions:
            for sid, df in all_dfs.items():
                if sid in current_positions:
                    continue
                if len(current_positions) >= max_positions:
                    break
                day_row = df[df["date"] == date]
                if day_row.empty or day_row.iloc[0]["signal"] != 1:
                    continue
                capital_per = current_capital / max_positions
                if capital_per < 10000:
                    continue
                current_positions[sid] = {
                    "entry_price":  day_row.iloc[0]["close"],
                    "entry_date":   date,
                    "capital_used": capital_per,
                    "hold_days":    0,
                }
                current_capital -= capital_per

        total_val = current_capital + sum(
            p["capital_used"] for p in current_positions.values()
        )
        equity_curve.append(total_val)

    # ── 績效計算 ─────────────────────────────────────────
    if not trades:
        return {
            "error": (
                f"回測期間（{start_date} ~ {end_date}）無觸發交易信號\n"
                f"資料筆數：{sum(len(df) for df in all_dfs.values())} 日\n"
                f"嘗試調整參數或確認資料是否完整（季線多頭排列+RSI 40-70才進場）"
            ),
            "total_trades": 0,
        }

    tdf = pd.DataFrame(trades)
    wins   = tdf[tdf["pnl_pct"] > 0]
    losses = tdf[tdf["pnl_pct"] <= 0]

    win_rate = len(wins) / len(tdf) * 100
    avg_win  = wins["pnl_pct"].mean()   if len(wins)   > 0 else 0
    avg_loss = losses["pnl_pct"].mean() if len(losses) > 0 else 0

    loss_sum = losses["pnl_amount"].sum()
    profit_factor = (
        abs(wins["pnl_amount"].sum() / loss_sum)
        if loss_sum != 0 else float("inf")
    )

    equity = pd.Series(equity_curve)
    total_return  = (equity.iloc[-1] - equity.iloc[0]) / equity.iloc[0] * 100
    rolling_max   = equity.cummax()
    drawdown      = (equity - rolling_max) / rolling_max
    max_drawdown  = drawdown.min() * 100

    days_count = max(
        (datetime.strptime(end_date, "%Y-%m-%d") -
         datetime.strptime(start_date, "%Y-%m-%d")).days, 1
    )
    annual_return = ((1 + total_return / 100) ** (365 / days_count) - 1) * 100

    daily_ret = equity.pct_change().dropna()
    sharpe = (
        daily_ret.mean() / daily_ret.std() * np.sqrt(252)
        if daily_ret.std() > 0 else 0
    )

    # 最近20筆交易
    recent_trades = tdf.tail(20).to_dict("records")

    # 各退場原因統計
    exit_stats = tdf["exit_reason"].value_counts().to_dict()

    return {
        "stock_id":       ",".join(targets),
        "start_date":     start_date,
        "end_date":       end_date,
        "initial_capital": capital,
        "final_capital":   round(equity.iloc[-1], 0),
        "total_return":    round(total_return, 2),
        "annual_return":   round(annual_return, 2),
        "max_drawdown":    round(max_drawdown, 2),
        "sharpe_ratio":    round(sharpe, 2),
        "total_trades":    len(tdf),
        "win_rate":        round(win_rate, 2),
        "avg_win_pct":     round(avg_win, 2),
        "avg_loss_pct":    round(avg_loss, 2),
        "profit_factor":   round(profit_factor, 2),
        "win_count":       len(wins),
        "loss_count":      len(losses),
        "exit_stats":      exit_stats,
        "trades":          recent_trades,
        "strategy_desc":   f"MA多頭排列+RSI(40-70)+量能過濾 | 停損{stop_loss_pct*100:.0f}% 停利{take_profit_pct*100:.0f}% 最長{hold_days}日",
    }


def format_backtest_report(result: dict) -> str:
    """將回測結果格式化為 TG 訊息"""
    if "error" in result:
        return f"❌ 回測失敗：{result['error']}"

    total_pnl = result["final_capital"] - result["initial_capital"]
    pnl_emoji = "🟢" if total_pnl > 0 else "🔴"

    exit_stats = result.get("exit_stats", {})
    exit_lines = "  ".join(
        f"{k}:{v}次" for k, v in exit_stats.items()
    )

    recent = result.get("trades", [])
    trade_lines = ""
    if recent:
        trade_lines = "\n📋 最近5筆交易\n"
        for t in recent[-5:]:
            emoji = "✅" if t["pnl_pct"] > 0 else "❌"
            trade_lines += (
                f"  {emoji} {t['stock_id']} "
                f"{t['entry_date']}→{t['exit_date']} "
                f"{t['pnl_pct']:+.1f}% ({t['exit_reason']})\n"
            )

    return (
        f"📊 回測報告  {result.get('stock_id', '')}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📅 {result['start_date']} ~ {result['end_date']}\n"
        f"📐 策略：{result.get('strategy_desc', '')}\n\n"
        f"💰 資金績效\n"
        f"  初始：${result['initial_capital']:,.0f}\n"
        f"  最終：${result['final_capital']:,.0f}\n"
        f"  {pnl_emoji} 損益：${total_pnl:+,.0f}（{result['total_return']:+.2f}%）\n"
        f"  📈 年化報酬：{result['annual_return']:+.2f}%\n"
        f"  📉 最大回撤：{result['max_drawdown']:.2f}%\n"
        f"  ⚡ 夏普比率：{result['sharpe_ratio']:.2f}\n\n"
        f"🎯 交易統計\n"
        f"  總交易：{result['total_trades']} 次\n"
        f"  勝率：{result['win_rate']:.1f}%"
        f"（{result['win_count']}勝/{result['loss_count']}敗）\n"
        f"  平均獲利：+{result['avg_win_pct']:.2f}%\n"
        f"  平均虧損：{result['avg_loss_pct']:.2f}%\n"
        f"  獲利因子：{result['profit_factor']:.2f}\n"
        f"  退場方式：{exit_lines}\n"
        f"{trade_lines}"
    )
