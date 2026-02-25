"""
database/crawler.py - 歷史資料爬蟲
支援：斷點續爬、防 Ban IP、進度顯示
資料來源：
  - 股價：Yahoo Finance（免費，穩定）
  - 籌碼：台灣證交所公開資料
  - 股票清單：證交所 + 櫃買中心
"""
import time
import logging
import pandas as pd
from datetime import datetime, timedelta
from database.db_manager import (
    get_conn, safe_request, CRAWL_CONFIG,
    get_crawl_progress, update_crawl_progress, log_crawl,
)

logger = logging.getLogger(__name__)


# ── 取得股票清單 ──────────────────────────────────

def fetch_stock_list() -> list:
    """
    從證交所取得所有上市股票清單
    """
    stocks = []

    # 上市（TWSE）
    url = "https://isin.twse.com.tw/isin/C_public.jsp"
    params = {"strMode": "2"}
    resp = safe_request(url, params)

    if resp:
        try:
            resp.encoding = "big5"
            tables = pd.read_html(resp.text)
            df = tables[0]
            df.columns = df.iloc[0]
            df = df.iloc[1:]
            for _, row in df.iterrows():
                code_name = str(row.iloc[0])
                if len(code_name) > 4 and code_name[:4].isdigit():
                    parts = code_name.split("\u3000")
                    if len(parts) >= 2:
                        stocks.append({
                            "stock_id": parts[0].strip(),
                            "name": parts[1].strip(),
                            "market": "TWSE",
                        })
        except Exception as e:
            logger.error(f"解析上市清單失敗: {e}")

    # 上櫃（OTC）
    url2 = "https://isin.twse.com.tw/isin/C_public.jsp"
    params2 = {"strMode": "4"}
    resp2 = safe_request(url2, params2)

    if resp2:
        try:
            resp2.encoding = "big5"
            tables2 = pd.read_html(resp2.text)
            df2 = tables2[0]
            df2.columns = df2.iloc[0]
            df2 = df2.iloc[1:]
            for _, row in df2.iterrows():
                code_name = str(row.iloc[0])
                if len(code_name) > 4 and code_name[:4].isdigit():
                    parts = code_name.split("\u3000")
                    if len(parts) >= 2:
                        stocks.append({
                            "stock_id": parts[0].strip(),
                            "name": parts[1].strip(),
                            "market": "OTC",
                        })
        except Exception as e:
            logger.error(f"解析上櫃清單失敗: {e}")

    logger.info(f"✅ 取得股票清單：共 {len(stocks)} 支")
    return stocks


def save_stock_list(stocks: list):
    """儲存股票清單到資料庫"""
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


# ── 歷史股價爬蟲（Yahoo Finance）────────────────────

def fetch_price_yfinance(stock_id: str, start: str, end: str) -> pd.DataFrame:
    """
    使用 yfinance 取得歷史股價（自動處理除權息還原）
    """
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
                df["adj_close"] = df["close"]  # yfinance 已自動還原
                df = df[["stock_id", "date", "open", "high", "low", "close", "volume", "adj_close"]]
                df = df.dropna(subset=["close"])
                return df

        except Exception as e:
            logger.debug(f"{stock_id}{suffix} 失敗: {e}")

    return pd.DataFrame()


def save_prices(df: pd.DataFrame):
    """批次儲存股價到資料庫"""
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
                logger.debug(f"儲存股價失敗 {row.get('stock_id')}/{row.get('date')}: {e}")
    return count


def crawl_all_prices(start_year: int = 2015, stock_ids: list = None):
    """
    批次爬取所有股票歷史股價
    支援斷點續爬
    """
    if stock_ids is None:
        with get_conn() as conn:
            rows = conn.execute("SELECT stock_id FROM stocks ORDER BY stock_id").fetchall()
            stock_ids = [r["stock_id"] for r in rows]

    if not stock_ids:
        logger.warning("股票清單為空，請先執行 fetch_stock_list()")
        return

    # 斷點續爬
    last_done = get_crawl_progress("price_crawl") or ""
    total = len(stock_ids)
    done = 0

    start_date = f"{start_year}-01-01"
    end_date = datetime.now().strftime("%Y-%m-%d")

    logger.info(f"開始爬取股價 | 共 {total} 支 | {start_date} ~ {end_date}")
    if last_done:
        logger.info(f"斷點續爬：從 {last_done} 之後繼續")

    for i, stock_id in enumerate(stock_ids):
        # 跳過已爬完的
        if last_done and stock_id <= last_done:
            continue

        try:
            df = fetch_price_yfinance(stock_id, start_date, end_date)
            count = save_prices(df)
            done += 1

            if done % 10 == 0:
                logger.info(f"進度：{i+1}/{total} | 已存 {count} 筆 | {stock_id}")

            # 批次暫停（每 20 支暫停一下，降低被封機率）
            if done % CRAWL_CONFIG["batch_size"] == 0:
                logger.info(f"⏸️  批次暫停 {CRAWL_CONFIG['batch_pause']} 秒...")
                time.sleep(CRAWL_CONFIG["batch_pause"])
            else:
                time.sleep(random.uniform(0.5, 1.5))

            # 儲存斷點
            update_crawl_progress("price_crawl", stock_id, "running")

        except Exception as e:
            logger.error(f"❌ {stock_id} 爬取失敗：{e}")
            log_crawl("price_crawl", "error", f"{stock_id}: {e}")

    update_crawl_progress("price_crawl", "", "done")
    logger.info(f"✅ 股價爬取完成！共處理 {done} 支")


# ── 三大法人籌碼爬蟲 ──────────────────────────────

def crawl_institutional(start_date: str = None, end_date: str = None):
    """
    爬取三大法人歷史資料
    每次只能抓一天，需逐日爬取
    """
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
        # 跳過週末
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue

        date_str = current.strftime("%Y%m%d")
        url = "https://www.twse.com.tw/fund/T86"
        params = {
            "response": "json",
            "date": date_str,
            "selectType": "ALLBUT0999",
        }

        resp = safe_request(url, params)
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
                            try:
                                return int(str(val).replace(",", "").strip())
                            except:
                                return 0

                        foreign_net = parse_int(row[4])
                        trust_net = parse_int(row[10])
                        dealer_net = parse_int(row[14])

                        rows.append((
                            stock_id,
                            current.strftime("%Y-%m-%d"),
                            foreign_net, trust_net, dealer_net,
                            foreign_net + trust_net + dealer_net,
                        ))

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
                        pct = done / max(total_days, 1) * 100
                        logger.info(f"籌碼爬取進度：{done} 天 ({pct:.1f}%) | {current.strftime('%Y-%m-%d')}")

                    update_crawl_progress("institutional_crawl", current.strftime("%Y-%m-%d"))

            except Exception as e:
                logger.error(f"籌碼解析失敗 {date_str}: {e}")

        current += timedelta(days=1)

    update_crawl_progress("institutional_crawl", end_date, "done")
    logger.info(f"✅ 籌碼爬取完成！共 {done} 天")


# ── 宏觀指標爬蟲 ──────────────────────────────────

def crawl_macro(days: int = 365):
    """爬取宏觀指標歷史資料"""
    try:
        import yfinance as yf
        end = datetime.now()
        start = end - timedelta(days=days)

        # VIX
        vix_df = yf.download("^VIX", start=start, end=end, progress=False)

        if not vix_df.empty:
            vix_df = vix_df.reset_index()
            with get_conn() as conn:
                for _, row in vix_df.iterrows():
                    date_str = row["Date"].strftime("%Y-%m-%d")
                    close = float(row["Close"].iloc[0] if hasattr(row["Close"], "iloc") else row["Close"])
                    conn.execute("""
                        INSERT INTO macro_daily (date, vix)
                        VALUES (?, ?)
                        ON CONFLICT(date) DO UPDATE SET vix = excluded.vix
                    """, (date_str, close))

            logger.info(f"✅ VIX 歷史資料儲存完成：{len(vix_df)} 筆")

    except Exception as e:
        logger.error(f"宏觀指標爬取失敗: {e}")


import random  # 補充 import


def crawl_monthly_revenue(months: int = 12):
    """
    爬取月營收資料（證交所公開資訊觀測站）
    每月 10 日前後更新上月營收
    """
    import requests
    from datetime import datetime

    now = datetime.now()
    success = 0

    for i in range(months):
        # 計算目標年月
        month = now.month - i
        year = now.year
        while month <= 0:
            month += 12
            year -= 1

        # 台股民國年
        roc_year = year - 1911

        url = "https://mops.twse.com.tw/mops/web/ajax_t05st10_ifrs"
        payload = {
            "encodeURIComponent": "1",
            "step": "1",
            "firstin": "1",
            "off": "1",
            "TYPEK": "sii",
            "year": str(roc_year),
            "month": str(month).zfill(2),
        }

        try:
            resp = requests.post(url, data=payload,
                                 headers={"User-Agent": "Mozilla/5.0"},
                                 timeout=30)
            resp.encoding = "utf-8"

            import pandas as pd
            tables = pd.read_html(resp.text)
            if not tables:
                continue

            df = tables[0]
            # 拍平多層欄位
            if hasattr(df.columns, "levels"):
                df.columns = [" ".join(str(c) for c in col if "Unnamed" not in str(c)).strip()
                               for col in df.columns]

            rows_saved = 0
            with get_conn() as conn:
                for _, row in df.iterrows():
                    try:
                        row_vals = [str(v) for v in row.values]
                        if len(row_vals) < 4:
                            continue
                        stock_id = str(row_vals[0]).strip().replace(" ", "")
                        if not stock_id[:4].isdigit():
                            continue

                        def to_float(v):
                            try:
                                return float(str(v).replace(",", "").replace(" ", ""))
                            except:
                                return 0.0

                        revenue = to_float(row_vals[2])
                        yoy = to_float(row_vals[6]) if len(row_vals) > 6 else 0.0
                        mom = to_float(row_vals[5]) if len(row_vals) > 5 else 0.0

                        if revenue <= 0:
                            continue

                        conn.execute("""
                            INSERT INTO monthly_revenue
                                (stock_id, year, month, revenue, yoy, mom, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                            ON CONFLICT(stock_id, year, month) DO UPDATE SET
                                revenue=excluded.revenue,
                                yoy=excluded.yoy,
                                mom=excluded.mom
                        """, (stock_id, year, month, revenue, yoy, mom))
                        rows_saved += 1
                    except Exception:
                        continue

            success += 1
            logger.info(f"月營收 {year}/{month:02d} 儲存 {rows_saved} 筆")

        except Exception as e:
            logger.error(f"月營收爬取失敗 {year}/{month}: {e}")

    logger.info(f"✅ 月營收爬取完成，共處理 {success} 個月")


if __name__ == "__main__":
    from database.db_manager import init_db
    init_db()

    print("1. 更新股票清單...")
    stocks = fetch_stock_list()
    save_stock_list(stocks)

    print("2. 爬取近 1 年股價（測試用）...")
    test_stocks = ["2330", "2317", "2454", "2382", "2308"]
    crawl_all_prices(start_year=2024, stock_ids=test_stocks)

    print("3. 爬取近 6 個月籌碼...")
    start = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
    crawl_institutional(start_date=start)

    print("4. 爬取 VIX 歷史...")
    crawl_macro(days=365)

    print("完成！")
