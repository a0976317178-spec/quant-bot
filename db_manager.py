"""
database/db_manager.py - 資料庫管理核心
使用 SQLite 儲存所有歷史數據，支援斷點續爬、防 Ban IP 機制
"""
import sqlite3
import os
import logging
import time
import random
import requests
from contextlib import contextmanager
from datetime import datetime, timedelta
from config import DATA_DIR

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(DATA_DIR, "quant.db")

# ── 防 Ban IP 設定 ────────────────────────────────
REQUEST_HEADERS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/118.0.0.0 Safari/537.36",
]

CRAWL_CONFIG = {
    "min_delay": 1.5,       # 最短請求間隔（秒）
    "max_delay": 4.0,       # 最長請求間隔（秒）
    "retry_times": 3,       # 失敗重試次數
    "retry_delay": 10,      # 重試等待時間（秒）
    "batch_size": 20,       # 每批次爬取數量
    "batch_pause": 30,      # 每批次後暫停（秒）
    "timeout": 15,          # 請求逾時（秒）
}


# ── 防 Ban 請求函式 ───────────────────────────────

def safe_request(url: str, params: dict = None, method: str = "GET") -> object:
    """
    防 Ban 的安全請求函式
    - 隨機 User-Agent
    - 隨機延遲
    - 自動重試
    """
    headers = {
        "User-Agent": random.choice(REQUEST_HEADERS),
        "Accept": "application/json, text/html, */*",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }

    for attempt in range(CRAWL_CONFIG["retry_times"]):
        try:
            # 隨機延遲，模擬人類行為
            delay = random.uniform(CRAWL_CONFIG["min_delay"], CRAWL_CONFIG["max_delay"])
            time.sleep(delay)

            if method == "GET":
                resp = requests.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=CRAWL_CONFIG["timeout"],
                )
            else:
                resp = requests.post(
                    url,
                    data=params,
                    headers=headers,
                    timeout=CRAWL_CONFIG["timeout"],
                )

            # 檢查是否被封鎖
            if resp.status_code == 429:
                wait = CRAWL_CONFIG["retry_delay"] * (attempt + 1)
                logger.warning(f"⚠️  遭到頻率限制，等待 {wait} 秒後重試...")
                time.sleep(wait)
                continue

            if resp.status_code == 403:
                logger.warning(f"⚠️  403 Forbidden，等待 {CRAWL_CONFIG['retry_delay']} 秒...")
                time.sleep(CRAWL_CONFIG["retry_delay"])
                continue

            resp.raise_for_status()
            return resp

        except requests.exceptions.Timeout:
            logger.warning(f"⚠️  請求逾時（第 {attempt+1} 次）：{url}")
        except requests.exceptions.ConnectionError:
            logger.warning(f"⚠️  連線失敗（第 {attempt+1} 次）：{url}")
        except Exception as e:
            logger.error(f"❌ 請求失敗（第 {attempt+1} 次）：{e}")

        if attempt < CRAWL_CONFIG["retry_times"] - 1:
            time.sleep(CRAWL_CONFIG["retry_delay"])

    logger.error(f"❌ 請求最終失敗：{url}")
    return None


# ── 資料庫初始化 ──────────────────────────────────

def init_db():
    """建立所有資料表"""
    with get_conn() as conn:
        conn.executescript("""
        -- 股票基本資料
        CREATE TABLE IF NOT EXISTS stocks (
            stock_id    TEXT PRIMARY KEY,
            name        TEXT,
            market      TEXT,   -- TWSE / OTC
            industry    TEXT,
            listed_date TEXT,
            updated_at  TEXT
        );

        -- 每日 OHLCV（調整後，處理除權息）
        CREATE TABLE IF NOT EXISTS daily_price (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id    TEXT NOT NULL,
            date        TEXT NOT NULL,
            open        REAL,
            high        REAL,
            low         REAL,
            close       REAL,
            volume      INTEGER,
            adj_close   REAL,   -- 還原權值後收盤價
            UNIQUE(stock_id, date)
        );

        -- 三大法人籌碼
        CREATE TABLE IF NOT EXISTS institutional (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id        TEXT NOT NULL,
            date            TEXT NOT NULL,
            foreign_net     INTEGER,   -- 外資買賣超（張）
            trust_net       INTEGER,   -- 投信買賣超（張）
            dealer_net      INTEGER,   -- 自營商買賣超（張）
            total_net       INTEGER,   -- 三大法人合計
            UNIQUE(stock_id, date)
        );

        -- 月營收
        CREATE TABLE IF NOT EXISTS monthly_revenue (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id    TEXT NOT NULL,
            year        INTEGER,
            month       INTEGER,
            revenue     REAL,
            yoy         REAL,   -- 年增率 %
            mom         REAL,   -- 月增率 %
            UNIQUE(stock_id, year, month)
        );

        -- 宏觀指標（每日）
        CREATE TABLE IF NOT EXISTS macro_daily (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT UNIQUE NOT NULL,
            vix         REAL,
            adl_daily   INTEGER,   -- 當日騰落值
            adl_cum     INTEGER,   -- 累計騰落線
            advancing   INTEGER,
            declining   INTEGER
        );

        -- 爬蟲進度追蹤（斷點續爬）
        CREATE TABLE IF NOT EXISTS crawl_progress (
            task_name   TEXT PRIMARY KEY,
            last_date   TEXT,
            status      TEXT,   -- running / done / error
            updated_at  TEXT
        );

        -- 技術因子快取（避免重複計算）
        CREATE TABLE IF NOT EXISTS factor_cache (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id    TEXT NOT NULL,
            date        TEXT NOT NULL,
            rsi         REAL,
            atr_pct     REAL,
            bias_20ma   REAL,
            macd_slope  REAL,
            return_5d   REAL,
            return_10d  REAL,
            return_20d  REAL,
            vol_ratio   REAL,
            label       INTEGER,
            UNIQUE(stock_id, date)
        );

        -- 爬蟲日誌
        CREATE TABLE IF NOT EXISTS crawl_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            task        TEXT,
            status      TEXT,
            message     TEXT,
            created_at  TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE INDEX IF NOT EXISTS idx_price_stock_date ON daily_price(stock_id, date);
        CREATE INDEX IF NOT EXISTS idx_inst_stock_date  ON institutional(stock_id, date);
        CREATE INDEX IF NOT EXISTS idx_factor_stock_date ON factor_cache(stock_id, date);
        """)
    logger.info(f"✅ 資料庫初始化完成：{DB_PATH}")


@contextmanager
def get_conn():
    """資料庫連線管理器（自動 commit/rollback）"""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # 提升並發效能
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


# ── 通用查詢工具 ──────────────────────────────────

def query_df(sql: str, params: tuple = ()):
    """執行 SQL 並回傳 DataFrame"""
    import pandas as pd
    with get_conn() as conn:
        return pd.read_sql_query(sql, conn, params=params)


def get_crawl_progress(task_name: str) -> object:
    """取得爬蟲斷點日期"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT last_date FROM crawl_progress WHERE task_name = ?",
            (task_name,)
        ).fetchone()
        return row["last_date"] if row else None


def update_crawl_progress(task_name: str, last_date: str, status: str = "running"):
    """更新爬蟲斷點"""
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO crawl_progress (task_name, last_date, status, updated_at)
            VALUES (?, ?, ?, datetime('now', 'localtime'))
            ON CONFLICT(task_name) DO UPDATE SET
                last_date = excluded.last_date,
                status = excluded.status,
                updated_at = excluded.updated_at
        """, (task_name, last_date, status))


def log_crawl(task: str, status: str, message: str = ""):
    """記錄爬蟲日誌"""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO crawl_log (task, status, message) VALUES (?, ?, ?)",
            (task, status, message)
        )


def get_db_stats() -> dict:
    """取得資料庫統計資訊"""
    with get_conn() as conn:
        stats = {}
        tables = ["stocks", "daily_price", "institutional", "monthly_revenue", "macro_daily", "factor_cache"]
        for table in tables:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            stats[table] = count

        # 價格資料日期範圍
        row = conn.execute("SELECT MIN(date), MAX(date) FROM daily_price").fetchone()
        stats["price_date_range"] = f"{row[0]} ~ {row[1]}" if row[0] else "無資料"

        # 資料庫檔案大小
        if os.path.exists(DB_PATH):
            size_mb = os.path.getsize(DB_PATH) / 1024 / 1024
            stats["db_size_mb"] = round(size_mb, 2)

        return stats


if __name__ == "__main__":
    init_db()
    stats = get_db_stats()
    for k, v in stats.items():
        print(f"{k}: {v}")
