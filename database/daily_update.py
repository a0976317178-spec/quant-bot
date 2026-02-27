"""
database/daily_update.py - 每日自動更新所有資料
每天 15:00 收盤後自動執行：
1. 今日股價（所有監控清單）
2. 三大法人籌碼
3. 月營收（每月10號更新）
4. 宏觀指標（VIX）
5. 因子快取重新計算

修復：
  - yfinance MultiIndex DataFrame 相容性問題（Series.strftime 錯誤）
  - 使用 ticker.history() 取代 yf.download() 避免 MultiIndex
"""
import logging
import time
import pandas as pd
from datetime import datetime, timedelta
from database.db_manager import get_conn, query_df, safe_request

logger = logging.getLogger(__name__)


def _safe_float(val) -> float:
    """安全地把 pandas Series 或純量轉成 float"""
    if hasattr(val, "iloc"):
        val = val.iloc[0]
    try:
        return float(val)
    except Exception:
        return 0.0


def _safe_date_str(val) -> str:
    """安全地把 pandas Timestamp / Series 轉成日期字串"""
    if hasattr(val, "iloc"):
        val = val.iloc[0]
    return pd.Timestamp(val).strftime("%Y-%m-%d")


# ══════════════════════════════════════════
# 1. 今日股價更新
# ══════════════════════════════════════════

def update_today_prices(stock_ids: list = None) -> str:
    """
    更新今日收盤價
    ✅ 修復：改用 ticker.history() 避免 MultiIndex 問題
    """
    import yfinance as yf

    if stock_ids is None:
        try:
            from memory.daily_learning import load_watchlist
            stock_ids = load_watchlist()
        except Exception:
            stock_ids = []

        if not stock_ids:
            with get_conn() as conn:
                rows = conn.execute(
                    "SELECT stock_id FROM stocks ORDER BY stock_id"
                ).fetchall()
                stock_ids = [r["stock_id"] for r in rows]

    if not stock_ids:
        return "❌ 股票清單為空，請先執行更新清單"

    yesterday = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
    today     = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    success   = 0
    fail      = 0

    for stock_id in stock_ids:
        saved = False
        for suffix in [".TW", ".TWO"]:
            try:
                # ✅ 修復核心：改用 ticker.history()，回傳的是單層 DataFrame
                ticker = yf.Ticker(f"{stock_id}{suffix}")
                df = ticker.history(start=yesterday, end=today, auto_adjust=True)

                if df is None or df.empty:
                    continue

                df = df.reset_index()

                with get_conn() as conn:
                    for _, row in df.iterrows():
                        # ✅ 修復：用 _safe_date_str 避免 Series.strftime 錯誤
                        date_str = _safe_date_str(row["Date"])
                        close  = _safe_float(row.get("Close",  0))
                        open_  = _safe_float(row.get("Open",   0))
                        high   = _safe_float(row.get("High",   0))
                        low    = _safe_float(row.get("Low",    0))
                        vol    = _safe_float(row.get("Volume", 0))
                        if close <= 0:
                            continue
                        conn.execute("""
                            INSERT INTO daily_price
                                (stock_id, date, open, high, low, close, volume, adj_close)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(stock_id, date) DO UPDATE SET
                                open=excluded.open, high=excluded.high,
                                low=excluded.low, close=excluded.close,
                                volume=excluded.volume, adj_close=excluded.adj_close
                        """, (stock_id, date_str, open_, high, low, close, vol, close))

                success += 1
                saved = True
                break
            except Exception as e:
                logger.debug(f"{stock_id}{suffix} 更新失敗: {e}")
                continue

        if not saved:
            fail += 1
        time.sleep(0.3)

    return f"✅ 股價更新完成：{success} 支成功，{fail} 支失敗"


# ══════════════════════════════════════════
# 2. 今日三大法人更新
# ══════════════════════════════════════════

def update_today_institutional() -> str:
    """更新今日三大法人資料"""
    today = datetime.now()

    if today.weekday() >= 5:
        return "📅 今日為假日，跳過三大法人更新"

    date_str = today.strftime("%Y%m%d")
    url = "https://www.twse.com.tw/fund/T86"
    params = {
        "response": "json",
        "date": date_str,
        "selectType": "ALLBUT0999",
    }

    resp = safe_request(url, params)
    if not resp:
        return "❌ 三大法人：無法連線至證交所"

    try:
        data = resp.json()
        if data.get("stat") != "OK":
            return f"⚠️ 三大法人：今日資料尚未公布（{date_str}）"

        rows = []
        for row in data.get("data", []):
            if len(row) < 15:
                continue
            stock_id = row[0].strip()
            if not stock_id or not stock_id[:4].isdigit():
                continue

            def p(v):
                try:
                    return int(str(v).replace(",", "").strip())
                except Exception:
                    return 0

            foreign_net = p(row[4])
            trust_net   = p(row[10])
            dealer_net  = p(row[14])
            rows.append((
                stock_id, today.strftime("%Y-%m-%d"),
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

        return f"✅ 三大法人更新完成：{len(rows)} 支"

    except Exception as e:
        return f"❌ 三大法人解析失敗：{e}"


# ══════════════════════════════════════════
# 3. 月營收更新（每月10號後可取得上月資料）
# ══════════════════════════════════════════

def update_monthly_revenue() -> str:
    """爬取月營收資料（公開資訊觀測站）"""
    today = datetime.now()

    if today.day < 10:
        return "📅 月營收：每月10號後才更新，本月尚未到期"

    if today.month == 1:
        year, month = today.year - 1, 12
    else:
        year, month = today.year, today.month - 1

    with get_conn() as conn:
        existing = conn.execute(
            "SELECT COUNT(*) as cnt FROM monthly_revenue WHERE year=? AND month=?",
            (year, month)
        ).fetchone()
        if existing and existing["cnt"] > 100:
            return f"✅ 月營收：{year}/{month:02d} 資料已存在（{existing['cnt']} 筆），跳過"

    url = "https://mops.twse.com.tw/nas/t21/sii/t21sc03_{year}_{month}_0.csv".format(
        year=year - 1911,
        month=month
    )

    try:
        import requests
        resp = requests.get(url, timeout=30,
                            headers={"User-Agent": "Mozilla/5.0"})
        resp.encoding = "big5"

        import io
        df = pd.read_csv(io.StringIO(resp.text), header=0)

        if df.empty:
            return f"⚠️ 月營收：{year}/{month:02d} 無資料"

        count = 0
        with get_conn() as conn:
            for _, row in df.iterrows():
                try:
                    stock_id = str(row.iloc[0]).strip()
                    if not stock_id[:4].isdigit():
                        continue
                    revenue      = float(str(row.iloc[4]).replace(",", "") or 0)
                    last_revenue = float(str(row.iloc[7]).replace(",", "") or 0)
                    yoy          = float(str(row.iloc[10]).replace(",", "") or 0)
                    mom          = float(str(row.iloc[9]).replace(",", "") or 0)
                    conn.execute("""
                        INSERT INTO monthly_revenue
                            (stock_id, year, month, revenue, last_revenue, yoy, mom)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(stock_id, year, month) DO UPDATE SET
                            revenue=excluded.revenue,
                            yoy=excluded.yoy, mom=excluded.mom
                    """, (stock_id, year, month, revenue, last_revenue, yoy, mom))
                    count += 1
                except Exception:
                    continue

        return f"✅ 月營收更新完成：{year}/{month:02d} 共 {count} 筆"

    except Exception as e:
        return f"❌ 月營收爬取失敗：{e}"


# ══════════════════════════════════════════
# 4. 宏觀指標更新（VIX）
# ══════════════════════════════════════════

def update_macro() -> str:
    """
    更新 VIX 等宏觀指標
    ✅ 修復：改用 ticker.history() 避免 MultiIndex / Series.strftime 錯誤
    """
    try:
        import yfinance as yf
        end   = datetime.now() + timedelta(days=1)
        start = datetime.now() - timedelta(days=7)

        # ✅ 修復核心：改用 Ticker.history() 而不是 yf.download()
        vix_ticker = yf.Ticker("^VIX")
        vix_df = vix_ticker.history(
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            auto_adjust=True
        )

        if vix_df is None or vix_df.empty:
            return "⚠️ 宏觀指標：VIX 資料取得失敗"

        vix_df = vix_df.reset_index()
        count = 0
        with get_conn() as conn:
            for _, row in vix_df.iterrows():
                # ✅ 修復：用 _safe_date_str 避免 Series.strftime 錯誤
                date_str = _safe_date_str(row["Date"])
                close    = _safe_float(row.get("Close", 0))
                if close <= 0:
                    continue
                conn.execute("""
                    INSERT INTO macro_daily (date, vix)
                    VALUES (?, ?)
                    ON CONFLICT(date) DO UPDATE SET vix = excluded.vix
                """, (date_str, close))
                count += 1

        return f"✅ 宏觀指標更新完成：VIX {count} 筆"

    except Exception as e:
        return f"❌ 宏觀指標更新失敗：{e}"


# ══════════════════════════════════════════
# 5. 因子快取更新
# ══════════════════════════════════════════

def update_factor_cache(stock_ids: list = None) -> str:
    """重新計算並快取技術指標因子"""
    if stock_ids is None:
        try:
            from memory.daily_learning import load_watchlist
            stock_ids = load_watchlist()
        except Exception:
            stock_ids = []

    if not stock_ids:
        return "⚠️ 因子快取：監控清單為空，跳過"

    count = 0
    today = datetime.now().strftime("%Y-%m-%d")

    for stock_id in stock_ids:
        try:
            sql = """
                SELECT date, open, high, low, close, volume
                FROM daily_price
                WHERE stock_id = ?
                ORDER BY date DESC LIMIT 60
            """
            df = query_df(sql, (stock_id,))
            if df.empty or len(df) < 20:
                continue

            import numpy as np
            df = df.sort_values("date").reset_index(drop=True)
            closes  = df["close"].astype(float)
            volumes = df["volume"].astype(float)

            ma20    = closes.rolling(20).mean().iloc[-1]
            ma60    = closes.rolling(60).mean().iloc[-1] if len(df) >= 60 else ma20
            vol_ma20 = volumes.rolling(20).mean().iloc[-1]
            current = closes.iloc[-1]

            delta = closes.diff()
            gain  = delta.clip(lower=0).rolling(14).mean().iloc[-1]
            loss  = (-delta.clip(upper=0)).rolling(14).mean().iloc[-1]
            rsi   = 100 - (100 / (1 + gain / loss)) if loss > 0 else 50

            ret_5d  = (current / closes.iloc[-6]  - 1) if len(df) >= 6  else 0
            ret_20d = (current / closes.iloc[-21] - 1) if len(df) >= 21 else 0

            with get_conn() as conn:
                conn.execute("""
                    INSERT INTO factor_cache
                        (stock_id, date, ma20, ma60, rsi, vol_ratio,
                         return_5d, return_20d, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(stock_id, date) DO UPDATE SET
                        ma20=excluded.ma20, ma60=excluded.ma60,
                        rsi=excluded.rsi, vol_ratio=excluded.vol_ratio,
                        return_5d=excluded.return_5d,
                        return_20d=excluded.return_20d,
                        updated_at=excluded.updated_at
                """, (
                    stock_id, today,
                    float(ma20), float(ma60), float(rsi),
                    float(volumes.iloc[-1] / vol_ma20) if vol_ma20 > 0 else 1.0,
                    float(ret_5d), float(ret_20d),
                ))
            count += 1

        except Exception as e:
            logger.debug(f"因子快取失敗 {stock_id}: {e}")

    return f"✅ 因子快取更新完成：{count} 支"


# ══════════════════════════════════════════
# 每日完整更新（主函式）
# ══════════════════════════════════════════

async def run_daily_update() -> str:
    """
    每日完整更新流程
    由排程器在 15:10 自動呼叫
    """
    start_time = datetime.now()
    results = []

    logger.info("開始每日資料更新...")
    results.append(f"🔄 每日資料更新 {start_time.strftime('%Y/%m/%d %H:%M')}")
    results.append("━━━━━━━━━━━━━━━━━")

    logger.info("更新今日股價...")
    results.append(update_today_prices())

    logger.info("更新三大法人...")
    results.append(update_today_institutional())

    logger.info("更新月營收...")
    results.append(update_monthly_revenue())

    logger.info("更新宏觀指標...")
    results.append(update_macro())

    logger.info("更新因子快取...")
    results.append(update_factor_cache())

    elapsed = (datetime.now() - start_time).seconds
    results.append("━━━━━━━━━━━━━━━━━")
    results.append(f"⏱️ 總耗時：{elapsed} 秒")

    return "\n".join(results)
