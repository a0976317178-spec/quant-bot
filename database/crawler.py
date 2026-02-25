"""
database/crawler.py - 歷史資料爬蟲
"""
import time
import random
import logging
import pandas as pd
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from database.db_manager import (
    get_conn, safe_request, CRAWL_CONFIG,
    get_crawl_progress, update_crawl_progress, log_crawl,
)

logger = logging.getLogger(__name__)


def fetch_stock_list() -> list:
    stocks = []
    targets = [
        ("https://isin.twse.com.tw/isin/C_public.jsp", {"strMode": "2"}, "TWSE"),
        ("https://isin.twse.com.tw/isin/C_public.jsp", {"strMode": "4"}, "OTC"),
    ]
    for url, params, market in targets:
        resp = safe_request(url, params)
        if not resp:
            logger.error(f"無法連線取得 {market} 清單")
            continue
        try:
            resp.encoding = "big5"
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.find_all("tr")
            count = 0
            for row in rows:
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue
                cell_text = cells[0].get_text(strip=True)
                if len(cell_text) < 5:
                    continue
                code = cell_text[:4]
                if not code.isdigit():
                    continue
                name_part = cell_text[4:].strip().lstrip("\u3000").strip()
                if not name_part:
                    continue
                stocks.append({"stock_id": code, "name": name_part, "market": market})
                count += 1
            logger.info(f"{market} 解析完成：{count} 支")
        except Exception as e:
            logger.error(f"解析 {market} 清單失敗: {e}")

    seen = set()
    unique = []
    for s in stocks:
        if s["stock_id"] not in seen:
            seen.add(s["stock_id"])
            unique.append(s)

    logger.info(f"✅ 取得股票清單：共 {len(unique)} 支")
    return unique


def save_stock_list(stocks: list):
    with get_conn() as conn:
        for s in stocks:
            conn.execute("""
                INSERT INTO stocks (stock_id, name, market, updated_at)
                VALUES (?, ?, ?, datetime('now', 'localtime'))
                ON CONFLICT(stock_id) DO UPDATE SET
                    name = excluded.name,
                    market = excluded.market,
                    updated_at = excluded.updated_at
            """, (s["stock_id"], s["name"], s["market"]))
    logger.info(f"✅ 股票清單已儲存：{len(stocks)} 支")


def fetch_price_yfinance(stock_id: str, start: str, end: str) -> pd.DataFrame:
    import yfinance as yf
    for suffix in [".TW", ".TWO"]:
        try:
            ticker = yf.Ticker(f"{stock_id}{suffix}")
            df = ticker.history(start=start, end=end, auto_adjust=True)
            if not df.empty:
                df = df.reset_index()
                df.columns = [c.lower() for c in df.columns]
                df["stock_id"] = stock_id
                df["date"] = df["date"].dt.strftime("%Y-%m-%d")
                df["adj_close"] = df["close"]
                df = df[["stock_id", "date", "open", "high", "low", "close", "volume", "adj_close"]]
                df = df.dropna(subset=["close"])
                return df
        except Exception as e:
            logger.debug(f"{stock_id}{suffix} 失敗: {e}")
    return pd.DataFrame()


def save_prices(df: pd.DataFrame):
    if df.empty:
        return 0
    with get_conn() as conn:
        count = 0
        for _, row in df.iterrows():
            try:
                conn.execute("""
                    INSERT INTO daily_price
                        (stock_id, date, open, high, low, close, volume, adj_close)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(stock_id, date) DO UPDATE SET
                        open=excluded.open, high=excluded.high,
                        low=excluded.low, close=excluded.close,
                        volume=excluded.volume, adj_close=excluded.adj_close
                """, (
                    row["stock_id"], row["date"],
                    row["open"], row["high"], row["low"],
                    row["close"], row["volume"], row["adj_close"],
                ))
                count += 1
            except Exception as e:
                logger.debug(f"儲存失敗 {row.get('stock_id')}: {e}")
    return count


def crawl_all_prices(start_year: int = 2015, stock_ids: list = None):
    if stock_ids is None:
        with get_conn() as conn:
            rows = conn.execute("SELECT stock_id FROM stocks ORDER BY stock_id").fetchall()
            stock_ids = [r["stock_id"] for r in rows]
    if not stock_ids:
        logger.warning("股票清單為空，請先執行更新清單")
        return

    last_done = get_crawl_progress("price_crawl") or ""
    total = len(stock_ids)
    done = 0
    start_date = f"{start_year}-01-01"
    end_date = datetime.now().strftime("%Y-%m-%d")
    logger.info(f"開始爬取股價 | 共 {total} 支 | {start_date} ~ {end_date}")

    for i, stock_id in enumerate(stock_ids):
        if last_done and stock_id <= last_done:
            continue
        try:
            df = fetch_price_yfinance(stock_id, start_date, end_date)
            count = save_prices(df)
            done += 1
            if done % 10 == 0:
                logger.info(f"進度：{i+1}/{total} | {stock_id}")
            if done % CRAWL_CONFIG["batch_size"] == 0:
                time.sleep(CRAWL_CONFIG["batch_pause"])
            else:
                time.sleep(random.uniform(0.5, 1.5))
            update_crawl_progress("price_crawl", stock_id, "running")
        except Exception as e:
            logger.error(f"❌ {stock_id} 失敗：{e}")
            log_crawl("price_crawl", "error", f"{stock_id}: {e}")

    update_crawl_progress("price_crawl", "", "done")
    logger.info(f"✅ 股價爬取完成！共 {done} 支")


def crawl_institutional(start_date: str = None, end_date: str = None):
    if not end_date:
        end_date = datetime.now().strftime("%Y-%m-%d")
    if not start_date:
        last = get_crawl_progress("institutional_crawl")
        start_date = last or "2020-01-01"

    current = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    total_days = (end - current).days
    done = 0
    logger.info(f"開始爬取三大法人籌碼 | {start_date} ~ {end_date}")

    while current <= end:
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue
        date_str = current.strftime("%Y%m%d")
        resp = safe_request("https://www.twse.com.tw/fund/T86", {
            "response": "json", "date": date_str, "selectType": "ALLBUT0999"
        })
        if resp:
            try:
                data = resp.json()
                if data.get("stat") == "OK":
                    rows = []
                    for row in data.get("data", []):
                        if len(row) < 15:
                            continue
                        stock_id = row[0].strip()
                        if not stock_id or not stock_id[:4].isdigit():
                            continue
                        def parse_int(val):
                            try: return int(str(val).replace(",", "").strip())
                            except: return 0
                        foreign_net = parse_int(row[4])
                        trust_net = parse_int(row[10])
                        dealer_net = parse_int(row[14])
                        rows.append((stock_id, current.strftime("%Y-%m-%d"),
                                     foreign_net, trust_net, dealer_net,
                                     foreign_net + trust_net + dealer_net))
                    with get_conn() as conn:
                        conn.executemany("""
                            INSERT INTO institutional
                                (stock_id, date, foreign_net, trust_net, dealer_net, total_net)
                            VALUES (?, ?, ?, ?, ?, ?)
                            ON CONFLICT(stock_id, date) DO UPDATE SET
                                foreign_net=excluded.foreign_net,
                                trust_net=excluded.trust_net,
                                dealer_net=excluded.dealer_net,
                                total_net=excluded.total_net
                        """, rows)
                    done += 1
                    if done % 20 == 0:
                        logger.info(f"籌碼進度：{done}/{total_days} 天")
                    update_crawl_progress("institutional_crawl", current.strftime("%Y-%m-%d"))
            except Exception as e:
                logger.error(f"籌碼解析失敗 {date_str}: {e}")
        current += timedelta(days=1)

    update_crawl_progress("institutional_crawl", end_date, "done")
    logger.info(f"✅ 籌碼爬取完成！共 {done} 天")


def crawl_macro(days: int = 365):
    try:
        import yfinance as yf
        end = datetime.now()
        start = end - timedelta(days=days)
        vix_df = yf.download("^VIX", start=start, end=end, progress=False)
        if not vix_df.empty:
            vix_df = vix_df.reset_index()
            with get_conn() as conn:
                for _, row in vix_df.iterrows():
                    date_str = row["Date"].strftime("%Y-%m-%d")
                    close = float(row["Close"].iloc[0] if hasattr(row["Close"], "iloc") else row["Close"])
                    conn.execute("""
                        INSERT INTO macro_daily (date, vix) VALUES (?, ?)
                        ON CONFLICT(date) DO UPDATE SET vix = excluded.vix
                    """, (date_str, close))
            logger.info(f"✅ VIX 儲存完成：{len(vix_df)} 筆")
    except Exception as e:
        logger.error(f"宏觀指標爬取失敗: {e}")
