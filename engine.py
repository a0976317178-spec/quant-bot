"""
backtest/engine.py - 個股歷史回測引擎
用法：回測 2330        → 回測台積電
     回測 2330 2022   → 從 2022 年開始回測
     回測 2330 2022 2024 → 指定期間
"""
import pandas as pd
import numpy as np
import logging
import yfinance as yf
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def fetch_stock_history(stock_id: str, start: str, end: str) -> pd.DataFrame:
    """取得個股歷史 OHLCV（優先從資料庫，其次 Yahoo）"""
    # 先嘗試從資料庫取
    try:
        from database.db_manager import query_df
        sql = """
            SELECT date, open, high, low, close, volume
            FROM daily_price
            WHERE stock_id = ? AND date >= ? AND date <= ?
            ORDER BY date
        """
        df = query_df(sql, (stock_id, start, end))
        if len(df) >= 60:
            return df
    except Exception:
        pass

    # 備用：Yahoo Finance
    try:
        for suffix in [".TW", ".TWO"]:
            df = yf.download(f"{stock_id}{suffix}", start=start, end=end,
                             progress=False, auto_adjust=True)
            if not df.empty:
                df = df.reset_index()
                df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower()
                               for c in df.columns]
                df["date"] = df["date"].astype(str)
                return df[["date","open","high","low","close","volume"]]
    except Exception as e:
        logger.error(f"取得歷史資料失敗 {stock_id}: {e}")

    return pd.DataFrame()


def run_backtest(
    stock_id: str,
    start_date: str = None,
    end_date: str = None,
    stop_loss_pct: float = 0.05,
    take_profit_pct: float = 0.10,
    hold_days: int = 10,
    capital: float = 1_000_000,
) -> dict:
    """
    個股歷史回測
    策略：每次以均線突破 + 成交量放大作為進場信號
    """
    if not end_date:
        end_date = datetime.now().strftime("%Y-%m-%d")
    if not start_date:
        start_date = (datetime.now() - timedelta(days=3*365)).strftime("%Y-%m-%d")

    df = fetch_stock_history(stock_id, start_date, end_date)
    if df.empty or len(df) < 60:
        return {
            "error": f"❌ {stock_id} 歷史資料不足（至少需要60天）\n"
                     f"請先執行「爬取 2022」下載歷史資料",
            "stock_id": stock_id,
        }

    df = df.copy()
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["high"]  = pd.to_numeric(df["high"],  errors="coerce")
    df["low"]   = pd.to_numeric(df["low"],   errors="coerce")
    df["volume"]= pd.to_numeric(df["volume"],errors="coerce")
    df = df.dropna(subset=["close"]).reset_index(drop=True)

    # ── 計算技術指標 ──────────────────────────────
    df["ma20"]     = df["close"].rolling(20).mean()
    df["ma60"]     = df["close"].rolling(60).mean()
    df["vol_ma20"] = df["volume"].rolling(20).mean()
    df["rsi"]      = calc_rsi(df["close"], 14)

    # ── 進場條件（可擴充為 ML 信號）────────────────
    # 條件1：收盤站上 20MA
    # 條件2：20MA 高於 60MA（多頭排列）
    # 條件3：成交量 > 20日均量 1.2 倍
    # 條件4：RSI 在 50~70（趨勢但未超買）
    df["signal"] = (
        (df["close"] > df["ma20"]) &
        (df["ma20"] > df["ma60"]) &
        (df["volume"] > df["vol_ma20"] * 1.2) &
        (df["rsi"] > 50) & (df["rsi"] < 70)
    )

    # ── 模擬交易 ──────────────────────────────────
    trades = []
    equity_curve = []
    current_capital = capital
    in_position = False
    entry_price = 0
    entry_date = ""
    hold_count = 0
    lots = 0

    for i, row in df.iterrows():
        if pd.isna(row["ma60"]):
            equity_curve.append(current_capital)
            continue

        if in_position:
            hold_count += 1
            exit_price = None
            exit_reason = ""

            if row["low"] <= entry_price * (1 - stop_loss_pct):
                exit_price = round(entry_price * (1 - stop_loss_pct), 2)
                exit_reason = "停損"
            elif row["high"] >= entry_price * (1 + take_profit_pct):
                exit_price = round(entry_price * (1 + take_profit_pct), 2)
                exit_reason = "停利"
            elif hold_count >= hold_days:
                exit_price = row["close"]
                exit_reason = "持有到期"

            if exit_price:
                pnl_pct = (exit_price - entry_price) / entry_price
                pnl_amount = pnl_pct * lots * entry_price * 1000
                current_capital += lots * exit_price * 1000

                trades.append({
                    "進場日": entry_date,
                    "出場日": row["date"],
                    "進場價": entry_price,
                    "出場價": exit_price,
                    "張數": lots,
                    "損益%": round(pnl_pct * 100, 2),
                    "損益金額": round(pnl_amount, 0),
                    "出場原因": exit_reason,
                    "持有天數": hold_count,
                })
                in_position = False
                hold_count = 0

        elif row["signal"] and not in_position:
            entry_price = row["close"]
            entry_date = row["date"]
            capital_per_trade = current_capital * 0.9  # 單次投入 90%
            lots = max(1, int(capital_per_trade / (entry_price * 1000)))
            cost = lots * entry_price * 1000
            if cost <= current_capital:
                current_capital -= cost
                in_position = True
                hold_count = 0

        equity_curve.append(current_capital + (lots * row["close"] * 1000 if in_position else 0))

    # 若還在持倉，以最後收盤價結算
    if in_position:
        last_close = df.iloc[-1]["close"]
        pnl_pct = (last_close - entry_price) / entry_price
        current_capital += lots * last_close * 1000
        trades.append({
            "進場日": entry_date,
            "出場日": df.iloc[-1]["date"] + "(未平倉)",
            "進場價": entry_price,
            "出場價": last_close,
            "張數": lots,
            "損益%": round(pnl_pct * 100, 2),
            "損益金額": round(pnl_pct * lots * entry_price * 1000, 0),
            "出場原因": "未平倉",
            "持有天數": hold_count,
        })

    if not trades:
        return {
            "error": f"⚠️ {stock_id} 在此期間無符合進場條件的交易\n"
                     f"可能原因：期間較短或股票全程下跌趨勢",
            "stock_id": stock_id,
        }

    # ── 績效計算 ──────────────────────────────────
    trades_df = pd.DataFrame(trades)
    wins = trades_df[trades_df["損益%"] > 0]
    losses = trades_df[trades_df["損益%"] <= 0]

    equity = pd.Series(equity_curve)
    total_return = (equity.iloc[-1] - capital) / capital * 100
    rolling_max = equity.cummax()
    drawdown = (equity - rolling_max) / rolling_max
    max_drawdown = drawdown.min() * 100

    days = len(df)
    annual_return = ((1 + total_return / 100) ** (252 / max(days, 1)) - 1) * 100

    daily_rets = equity.pct_change().dropna()
    sharpe = (daily_rets.mean() / daily_rets.std() * (252 ** 0.5)) if daily_rets.std() > 0 else 0

    pf_denom = abs(losses["損益金額"].sum()) if len(losses) > 0 and losses["損益金額"].sum() != 0 else 1
    profit_factor = abs(wins["損益金額"].sum()) / pf_denom if len(losses) > 0 else float("inf")

    # 取得股票名稱
    name = ""
    try:
        from database.db_manager import get_conn
        with get_conn() as conn:
            row = conn.execute("SELECT name FROM stocks WHERE stock_id=?", (stock_id,)).fetchone()
            if row:
                name = row["name"]
    except:
        pass

    return {
        "stock_id": stock_id,
        "name": name,
        "start_date": start_date,
        "end_date": end_date,
        "initial_capital": capital,
        "final_capital": round(equity.iloc[-1], 0),
        "total_return": round(total_return, 2),
        "annual_return": round(annual_return, 2),
        "max_drawdown": round(max_drawdown, 2),
        "sharpe_ratio": round(sharpe, 2),
        "total_trades": len(trades_df),
        "win_rate": round(len(wins) / len(trades_df) * 100, 2),
        "avg_win": round(wins["損益%"].mean(), 2) if len(wins) > 0 else 0,
        "avg_loss": round(losses["損益%"].mean(), 2) if len(losses) > 0 else 0,
        "profit_factor": round(profit_factor, 2),
        "win_count": len(wins),
        "loss_count": len(losses),
        "best_trade": round(trades_df["損益%"].max(), 2),
        "worst_trade": round(trades_df["損益%"].min(), 2),
        "avg_hold_days": round(trades_df["持有天數"].mean(), 1),
        "recent_trades": trades_df.tail(5).to_dict("records"),
    }


def format_backtest_report(result: dict) -> str:
    if "error" in result:
        return result["error"]

    total_pnl = result["final_capital"] - result["initial_capital"]
    emoji = "🟢" if total_pnl >= 0 else "🔴"
    name_str = f" {result['name']}" if result.get("name") else ""

    report = (
        f"📊 {result['stock_id']}{name_str} 回測報告\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 {result['start_date']} ～ {result['end_date']}\n\n"
        f"💰 資金績效\n"
        f"  初始資金：${result['initial_capital']:,.0f}\n"
        f"  最終資金：${result['final_capital']:,.0f}\n"
        f"  {emoji} 總損益：${total_pnl:+,.0f}（{result['total_return']:+.2f}%）\n"
        f"  年化報酬：{result['annual_return']:+.2f}%\n"
        f"  最大回撤：{result['max_drawdown']:.2f}%\n"
        f"  夏普比率：{result['sharpe_ratio']:.2f}\n\n"
        f"🎯 交易統計\n"
        f"  交易次數：{result['total_trades']} 次\n"
        f"  勝率：{result['win_rate']:.1f}%"
        f"  ({result['win_count']}勝/{result['loss_count']}敗)\n"
        f"  平均獲利：+{result['avg_win']:.2f}%\n"
        f"  平均虧損：{result['avg_loss']:.2f}%\n"
        f"  獲利因子：{result['profit_factor']:.2f}\n"
        f"  最佳交易：+{result['best_trade']:.2f}%\n"
        f"  最差交易：{result['worst_trade']:.2f}%\n"
        f"  平均持有：{result['avg_hold_days']:.1f} 天\n\n"
        f"📋 最近5筆交易\n"
    )
    for t in result.get("recent_trades", []):
        e = "🟢" if t["損益%"] > 0 else "🔴"
        report += (
            f"  {e} {t['進場日']}→{t['出場日'][:10]}"
            f" {t['損益%']:+.2f}% ({t['出場原因']})\n"
        )
    return report


def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))
