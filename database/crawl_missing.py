"""
database/crawl_missing.py - 補齊缺少的數據
執行：python3 crawl_missing.py
會依序爬取：三大法人、月營收、宏觀指標（VIX）
"""
import sys
import os
import time
import logging
import requests
from datetime import datetime, timedelta

# 讓 Python 找得到其他模組
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def crawl_institutional_twse():
    """
    爬取三大法人買賣超（證交所）
    資料：外資、投信、自營商 每日買賣超（張數）
    """
    from database.db_manager import get_conn, query_df

    logger.info("開始爬取三大法人數據...")
    headers = {"User-Agent": "Mozilla/5.0"}
    success = 0

    # 爬近180天
    today = datetime.now()
    for delta in range(180):
        date = today - timedelta(days=delta)
        if date.weekday() >= 5:  # 跳過週末
            continue

        date_str = date.strftime("%Y%m%d")
        date_db  = date.strftime("%Y-%m-%d")

        # 確認是否已有資料
        with get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM institutional WHERE date=?", (date_db,)
            ).fetchone()[0]
            if count > 0:
                continue

        try:
            url = "https://www.twse.com.tw/fund/T86"
            params = {"response": "json", "date": date_str, "selectType": "ALL"}
            resp = requests.get(url, params=params, headers=headers, timeout=15)
            data = resp.json()

            if data.get("stat") != "OK":
                time.sleep(0.5)
                continue

            rows = data.get("data", [])
            records = []
            for row in rows:
                try:
                    stock_id = str(row[0]).strip()
                    if not stock_id.isdigit() or len(stock_id) != 4:
                        continue

                    def parse_int(s):
                        return int(str(s).replace(",", "").replace("+", "") or 0)

                    foreign_net = parse_int(row[4])   # 外資買賣超
                    trust_net   = parse_int(row[10])  # 投信買賣超
                    dealer_net  = parse_int(row[13])  # 自營商買賣超
                    total_net   = foreign_net + trust_net + dealer_net

                    records.append((
                        stock_id, date_db,
                        foreign_net, trust_net, dealer_net, total_net
                    ))
                except Exception:
                    continue

            if records:
                with get_conn() as conn:
                    conn.executemany("""
                        INSERT OR REPLACE INTO institutional
                            (stock_id, date, foreign_net, trust_net, dealer_net, total_net)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, records)
                success += 1
                logger.info(f"三大法人 {date_db}：{len(records)} 筆")

            time.sleep(0.4)  # 避免被封

        except Exception as e:
            logger.warning(f"三大法人 {date_db} 失敗: {e}")
            time.sleep(1)

    logger.info(f"三大法人爬取完成：{success} 天有資料")


def crawl_monthly_revenue():
    """
    爬取月營收（公開資訊觀測站）
    """
    from database.db_manager import get_conn

    logger.info("開始爬取月營收...")
    headers = {"User-Agent": "Mozilla/5.0"}
    success = 0

    now = datetime.now()
    # 爬近12個月
    for months_ago in range(12):
        if months_ago == 0:
            month = now.month - 1 or 12
            year  = now.year if now.month > 1 else now.year - 1
        else:
            dt    = now - timedelta(days=months_ago * 31)
            year  = dt.year
            month = dt.month

        # 民國年
        roc_year = year - 1911

        try:
            url = "https://mops.twse.com.tw/nas/t21/sii/t21sc03_{}_{}_{}.html".format(
                roc_year, month, 0
            )
            resp = requests.get(url, headers=headers, timeout=15)
            resp.encoding = "big5"

            # 用正則解析HTML表格
            import re
            pattern = r'<td[^>]*>(\d{4,6})</td>.*?<td[^>]*>([^<]+)</td>.*?<td[^>]*>([\d,]+)</td>.*?<td[^>]*>([\d,]+)</td>.*?<td[^>]*>([+-]?[\d.]+)</td>'
            matches = re.findall(pattern, resp.text, re.DOTALL)

            records = []
            for m in matches:
                try:
                    stock_id = m[0][:4]
                    if not stock_id.isdigit():
                        continue
                    revenue  = int(m[2].replace(",", ""))
                    yoy      = float(m[4].replace(",", ""))
                    records.append((stock_id, year, month, revenue, yoy, 0))
                except Exception:
                    continue

            if records:
                with get_conn() as conn:
                    conn.executemany("""
                        INSERT OR REPLACE INTO monthly_revenue
                            (stock_id, year, month, revenue, yoy, mom)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, records)
                success += 1
                logger.info(f"月營收 {year}/{month}：{len(records)} 筆")

            time.sleep(1)

        except Exception as e:
            logger.warning(f"月營收 {year}/{month} 失敗: {e}")
            time.sleep(2)

    logger.info(f"月營收爬取完成：{success} 個月有資料")


def crawl_macro_vix():
    """
    爬取宏觀指標（VIX）
    """
    from database.db_manager import get_conn

    logger.info("開始爬取 VIX 宏觀指標...")

    try:
        import time as time_module
        end_ts   = int(time_module.time())
        start_ts = end_ts - 365 * 86400  # 一年

        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX"
            f"?period1={start_ts}&period2={end_ts}&interval=1d"
        )
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=15)
        data = resp.json()

        result    = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes    = result["indicators"]["quote"][0]["close"]

        records = []
        for ts, close in zip(timestamps, closes):
            if close is None:
                continue
            date_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            records.append((date_str, round(close, 2)))

        if records:
            with get_conn() as conn:
                conn.executemany("""
                    INSERT OR REPLACE INTO macro_daily (date, vix)
                    VALUES (?, ?)
                """, records)
            logger.info(f"VIX 爬取完成：{len(records)} 筆")

    except Exception as e:
        logger.error(f"VIX 爬取失敗: {e}")


if __name__ == "__main__":
    print("=" * 50)
    print("開始補齊缺少的數據")
    print("=" * 50)

    print("\n① 爬取 VIX 宏觀指標（最快）...")
    crawl_macro_vix()

    print("\n② 爬取月營收...")
    crawl_monthly_revenue()

    print("\n③ 爬取三大法人（最慢，約需10~20分鐘）...")
    crawl_institutional_twse()

    print("\n✅ 全部完成！在 Telegram 輸入「資料庫」確認筆數")
