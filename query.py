"""
database/query.py - 常用資料庫查詢
從資料庫取出分析所需的乾淨數據
"""
import pandas as pd
import logging
from database.db_manager import get_conn, query_df

logger = logging.getLogger(__name__)


def get_price_history(stock_id: str, days: int = 120) -> pd.DataFrame:
    """取得股票歷史股價（從資料庫）"""
    sql = """
        SELECT date, open, high, low, close, volume, adj_close
        FROM daily_price
        WHERE stock_id = ?
        ORDER BY date DESC
        LIMIT ?
    """
    df = query_df(sql, (stock_id, days))
    if not df.empty:
        df = df.sort_values("date").reset_index(drop=True)
    return df


def get_institutional_history(stock_id: str, days: int = 60) -> pd.DataFrame:
    """取得三大法人歷史籌碼（從資料庫）"""
    sql = """
        SELECT date, foreign_net, trust_net, dealer_net, total_net
        FROM institutional
        WHERE stock_id = ?
        ORDER BY date DESC
        LIMIT ?
    """
    df = query_df(sql, (stock_id, days))
    if not df.empty:
        df = df.sort_values("date").reset_index(drop=True)

    # 計算衍生因子
    if not df.empty:
        # 外資連買天數
        df["foreign_consecutive"] = 0
        consecutive = 0
        for i, row in df.iterrows():
            if row["foreign_net"] > 0:
                consecutive = consecutive + 1 if consecutive > 0 else 1
            elif row["foreign_net"] < 0:
                consecutive = consecutive - 1 if consecutive < 0 else -1
            else:
                consecutive = 0
            df.at[i, "foreign_consecutive"] = consecutive

        df["foreign_net_ma5"] = df["foreign_net"].rolling(5).mean()
        df["trust_net_ma5"] = df["trust_net"].rolling(5).mean()

    return df


def get_latest_factors(stock_id: str) -> dict:
    """取得最新的綜合因子（量價 + 籌碼）"""
    price_df = get_price_history(stock_id, days=120)
    inst_df = get_institutional_history(stock_id, days=30)

    result = {"stock_id": stock_id}

    if not price_df.empty:
        from factors.technical import calc_technical_factors
        tech = calc_technical_factors(price_df)
        latest = tech.iloc[-1]
        result.update({
            "close": latest.get("close"),
            "rsi": latest.get("rsi"),
            "atr_pct": latest.get("atr_pct"),
            "bias_20ma": latest.get("bias_20ma"),
            "macd_slope": latest.get("macd_hist_slope"),
            "return_5d": latest.get("return_5d"),
            "return_20d": latest.get("return_20d"),
        })

    if not inst_df.empty:
        latest_inst = inst_df.iloc[-1]
        result.update({
            "foreign_consecutive": latest_inst.get("foreign_consecutive"),
            "foreign_net_ma5": latest_inst.get("foreign_net_ma5"),
            "trust_net_ma5": latest_inst.get("trust_net_ma5"),
        })

    return result


def get_all_stocks() -> list:
    """取得資料庫中所有股票代號"""
    with get_conn() as conn:
        rows = conn.execute("SELECT stock_id, name, market FROM stocks ORDER BY stock_id").fetchall()
        return [dict(r) for r in rows]


def get_training_dataset(start_date: str = "2020-01-01") -> pd.DataFrame:
    """
    取得用於 ML 訓練的完整數據集
    合併：技術因子 + 籌碼因子 + 標籤
    """
    sql = """
        SELECT
            f.stock_id, f.date,
            f.rsi, f.atr_pct, f.bias_20ma, f.macd_slope,
            f.return_5d, f.return_10d, f.return_20d, f.vol_ratio,
            i.foreign_net, i.trust_net, i.total_net,
            f.label
        FROM factor_cache f
        LEFT JOIN institutional i ON f.stock_id = i.stock_id AND f.date = i.date
        WHERE f.date >= ? AND f.label IS NOT NULL
        ORDER BY f.date
    """
    df = query_df(sql, (start_date,))
    logger.info(f"訓練資料集：{len(df)} 筆，{df['stock_id'].nunique() if not df.empty else 0} 支股票")
    return df
