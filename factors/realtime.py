"""
factors/realtime.py - 即時股價與基本資料爬取
資料來源：台灣證交所 + Yahoo Finance
"""
import requests
import pandas as pd
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def fetch_twse_realtime(stock_id: str) -> dict:
    """
    從證交所即時行情 API 取得股票資料
    """
    url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
    params = {
        "ex_ch": f"tse_{stock_id}.tw",
        "json": "1",
        "delay": "0",
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://mis.twse.com.tw/",
    }

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        data = resp.json()

        if not data.get("msgArray"):
            # 嘗試 OTC（上櫃）
            params["ex_ch"] = f"otc_{stock_id}.tw"
            resp = requests.get(url, params=params, headers=headers, timeout=10)
            data = resp.json()

        if data.get("msgArray"):
            info = data["msgArray"][0]
            
            # 解析欄位
            name = info.get("n", "")
            close = float(info.get("z", info.get("y", 0)) or 0)  # 成交價，收盤用昨收
            open_price = float(info.get("o", 0) or 0)
            high = float(info.get("h", 0) or 0)
            low = float(info.get("l", 0) or 0)
            prev_close = float(info.get("y", 0) or 0)
            volume = int(info.get("v", 0) or 0)
            change = close - prev_close if close and prev_close else 0
            change_pct = (change / prev_close * 100) if prev_close else 0

            return {
                "stock_id": stock_id,
                "name": name,
                "close": close,
                "open": open_price,
                "high": high,
                "low": low,
                "prev_close": prev_close,
                "change": round(change, 2),
                "change_pct": round(change_pct, 2),
                "volume": volume,
                "source": "TWSE即時",
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

    except Exception as e:
        logger.error(f"TWSE即時行情失敗 {stock_id}: {e}")

    return {}


def fetch_yahoo_quote(stock_id: str) -> dict:
    """
    從 Yahoo Finance 取得股票資料（備用來源）
    """
    symbol = f"{stock_id}.TW"
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        meta = data["chart"]["result"][0]["meta"]

        close = meta.get("regularMarketPrice", 0)
        prev_close = meta.get("previousClose", meta.get("chartPreviousClose", 0))
        change = close - prev_close if close and prev_close else 0
        change_pct = (change / prev_close * 100) if prev_close else 0

        return {
            "stock_id": stock_id,
            "name": meta.get("shortName", stock_id),
            "close": round(close, 2),
            "prev_close": round(prev_close, 2),
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "volume": meta.get("regularMarketVolume", 0),
            "high": meta.get("regularMarketDayHigh", 0),
            "low": meta.get("regularMarketDayLow", 0),
            "open": meta.get("regularMarketOpen", 0),
            "source": "Yahoo Finance",
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    except Exception as e:
        logger.error(f"Yahoo Finance 失敗 {stock_id}: {e}")

    return {}


def get_stock_quote(stock_id: str) -> dict:
    """
    取得股票即時報價（先用 TWSE，失敗則用 Yahoo）
    """
    # 先嘗試證交所
    quote = fetch_twse_realtime(stock_id)

    # 若證交所失敗或收盤後，改用 Yahoo Finance
    if not quote or quote.get("close", 0) == 0:
        quote = fetch_yahoo_quote(stock_id)

    return quote


def fetch_historical(stock_id: str, days: int = 120) -> pd.DataFrame:
    """
    取得歷史 OHLCV 資料（用於技術指標計算）
    """
    import yfinance as yf
    from datetime import timedelta

    end = datetime.now()
    start = end - timedelta(days=days + 30)  # 多抓一些避免資料缺失

    try:
        df = yf.download(
            f"{stock_id}.TW",
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
        )
        if df.empty:
            # 嘗試上櫃
            df = yf.download(
                f"{stock_id}.TWO",
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                progress=False,
            )

        if not df.empty:
            df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
            df = df.reset_index()
            df.columns = [c.lower() for c in df.columns]
            return df.tail(days)

    except Exception as e:
        logger.error(f"取得歷史資料失敗 {stock_id}: {e}")

    return pd.DataFrame()


def format_quote_message(quote: dict) -> str:
    """
    將報價格式化成 TG 訊息
    """
    if not quote:
        return "❌ 無法取得股價資料"

    change = quote.get("change", 0)
    change_pct = quote.get("change_pct", 0)
    arrow = "🔴▲" if change > 0 else ("🟢▼" if change < 0 else "⬜")

    return (
        f"📈 *{quote.get('stock_id')} {quote.get('name', '')}*\n"
        f"現價：*${quote.get('close', 'N/A')}*  "
        f"{arrow} {change:+.2f} ({change_pct:+.2f}%)\n"
        f"開：{quote.get('open', 'N/A')}  "
        f"高：{quote.get('high', 'N/A')}  "
        f"低：{quote.get('low', 'N/A')}\n"
        f"昨收：{quote.get('prev_close', 'N/A')}  "
        f"量：{quote.get('volume', 0):,} 張\n"
        f"更新：{quote.get('updated_at', '')}"
    )


if __name__ == "__main__":
    quote = get_stock_quote("2330")
    print(format_quote_message(quote))
