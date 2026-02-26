"""
database/db_manager.py - 資料庫管理核心（完整版）
"""
import sqlite3
import os
import logging
import time
import random
import requests
from contextlib import contextmanager
from config import DATA_DIR

logger = logging.getLogger(__name__)
DB_PATH = os.path.join(DATA_DIR, "quant.db")

REQUEST_HEADERS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]

CRAWL_CONFIG = {
    "min_delay": 1.5, "max_delay": 4.0,
    "retry_times": 3, "retry_delay": 10,
    "batch_size": 20, "batch_pause": 30, "timeout": 15,
}


def safe_request(url, params=None, method="GET"):
    headers = {
        "User-Agent": random.choice(REQUEST_HEADERS),
        "Accept": "application/json, text/html, */*",
        "Accept-Language": "zh-TW,zh;q=0.9",
        "Connection": "keep-alive",
    }
    for attempt in range(CRAWL_CONFIG["retry_times"]):
        try:
            time.sleep(random.uniform(CRAWL_CONFIG["min_delay"], CRAWL_CONFIG["max_delay"]))
            if method == "GET":
                resp = requests.get(url, params=params, headers=headers, timeout=CRAWL_CONFIG["timeout"])
            else:
                resp = requests.post(url, data=params, headers=headers, timeout=CRAWL_CONFIG["timeout"])
            if resp.status_code == 429:
                time.sleep(CRAWL_CONFIG["retry_delay"] * (attempt + 1))
                continue
            if resp.status_code == 403:
                time.sleep(CRAWL_CONFIG["retry_delay"])
                continue
            resp.raise_for_status()
            return resp
        except Exception as e:
            logger.warning(f"請求失敗（第{attempt+1}次）：{e}")
        if attempt < CRAWL_CONFIG["retry_times"] - 1:
            time.sleep(CRAWL_CONFIG["retry_delay"])
    return None


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def query_df(sql, params=()):
    import pandas as pd
    with get_conn() as conn:
        return pd.read_sql_query(sql, conn, params=params)


def init_db():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS stocks (
            stock_id TEXT PRIMARY KEY, name TEXT, market TEXT,
            industry TEXT, listed_date TEXT, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS daily_price (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id TEXT NOT NULL, date TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL,
            volume INTEGER, adj_close REAL,
            UNIQUE(stock_id, date)
        );
        CREATE TABLE IF NOT EXISTS institutional (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id TEXT NOT NULL, date TEXT NOT NULL,
            foreign_net INTEGER, trust_net INTEGER,
            dealer_net INTEGER, total_net INTEGER,
            UNIQUE(stock_id, date)
        );
        CREATE TABLE IF NOT EXISTS monthly_revenue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id TEXT NOT NULL, year INTEGER, month INTEGER,
            revenue REAL, last_revenue REAL, yoy REAL, mom REAL,
            UNIQUE(stock_id, year, month)
        );
        CREATE TABLE IF NOT EXISTS macro_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE NOT NULL, vix REAL,
            adl_daily INTEGER, adl_cum INTEGER,
            advancing INTEGER, declining INTEGER
        );
        CREATE TABLE IF NOT EXISTS factor_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id TEXT NOT NULL, date TEXT NOT NULL,
            rsi REAL, atr_pct REAL, bias_20ma REAL,
            macd_slope REAL, return_5d REAL, return_10d REAL,
            return_20d REAL, vol_ratio REAL,
            ma20 REAL, ma60 REAL, label INTEGER, updated_at TEXT,
            UNIQUE(stock_id, date)
        );
        CREATE TABLE IF NOT EXISTS trade_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id TEXT NOT NULL, action TEXT,
            price REAL, shares INTEGER, score INTEGER DEFAULT 0,
            reason TEXT DEFAULT '', exit_price REAL,
            exit_reason TEXT DEFAULT '', pnl_pct REAL,
            pnl_amount REAL, hold_days INTEGER DEFAULT 0,
            entry_date TEXT, exit_date TEXT,
            status TEXT DEFAULT '持有中',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS win_rate_db (
            stock_id TEXT PRIMARY KEY, name TEXT DEFAULT '',
            total_trades INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0,
            win_rate REAL DEFAULT 0,
            avg_win_pct REAL DEFAULT 0, avg_loss_pct REAL DEFAULT 0,
            profit_factor REAL DEFAULT 0, avg_hold_days REAL DEFAULT 0,
            best_pnl REAL DEFAULT 0, worst_pnl REAL DEFAULT 0,
            total_pnl REAL DEFAULT 0, last_updated TEXT
        );
        CREATE TABLE IF NOT EXISTS strategy_params (
            stock_id TEXT PRIMARY KEY,
            best_stop_loss REAL DEFAULT 0.05,
            best_take_profit REAL DEFAULT 0.10,
            best_hold_days INTEGER DEFAULT 10,
            best_entry_rsi_min REAL DEFAULT 45,
            best_entry_rsi_max REAL DEFAULT 70,
            best_vol_ratio REAL DEFAULT 1.2,
            best_score_threshold INTEGER DEFAULT 60,
            backtest_win_rate REAL DEFAULT 0,
            backtest_sharpe REAL DEFAULT 0,
            backtest_max_dd REAL DEFAULT 0,
            optimized_at TEXT, sample_count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS analysis_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id TEXT NOT NULL, date TEXT NOT NULL,
            score INTEGER DEFAULT 0, tech_score INTEGER DEFAULT 0,
            chip_score INTEGER DEFAULT 0, fund_score INTEGER DEFAULT 0,
            env_score INTEGER DEFAULT 0, close_price REAL DEFAULT 0,
            summary TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS crawl_progress (
            task_name TEXT PRIMARY KEY, last_date TEXT,
            status TEXT, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS crawl_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task TEXT, status TEXT, message TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_price_stock_date  ON daily_price(stock_id, date);
        CREATE INDEX IF NOT EXISTS idx_inst_stock_date   ON institutional(stock_id, date);
        CREATE INDEX IF NOT EXISTS idx_factor_stock_date ON factor_cache(stock_id, date);
        CREATE INDEX IF NOT EXISTS idx_trade_stock       ON trade_log(stock_id);
        CREATE INDEX IF NOT EXISTS idx_analysis_stock    ON analysis_log(stock_id, date);
        """)
    logger.info(f"資料庫初始化完成：{DB_PATH}")


def get_db_stats():
    with get_conn() as conn:
        def count(table):
            try:
                return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except:
                return 0
        stats = {
            "stocks":          count("stocks"),
            "daily_price":     count("daily_price"),
            "institutional":   count("institutional"),
            "monthly_revenue": count("monthly_revenue"),
            "macro_daily":     count("macro_daily"),
            "factor_cache":    count("factor_cache"),
            "trade_log":       count("trade_log"),
            "win_rate_db":     count("win_rate_db"),
            "analysis_log":    count("analysis_log"),
        }
        r = conn.execute("SELECT MIN(date), MAX(date) FROM daily_price").fetchone()
        stats["price_date_range"] = f"{r[0]} ~ {r[1]}" if r[0] else "無資料"
        r2 = conn.execute("SELECT MAX(date) FROM institutional").fetchone()
        stats["inst_latest"] = r2[0] if r2[0] else "無資料"
        r3 = conn.execute("SELECT MAX(date) FROM macro_daily").fetchone()
        stats["macro_latest"] = r3[0] if r3[0] else "無資料"
        r4 = conn.execute("SELECT MAX(year), MAX(month) FROM monthly_revenue").fetchone()
        stats["revenue_latest"] = f"{r4[0]}/{r4[1]:02d}" if r4[0] else "無資料"
        r5 = conn.execute(
            "SELECT stock_id, win_rate, total_trades FROM win_rate_db "
            "WHERE total_trades>=3 ORDER BY win_rate DESC LIMIT 1"
        ).fetchone()
        stats["best_stock"] = f"{r5['stock_id']} 勝率{r5['win_rate']:.0f}%({r5['total_trades']}次)" if r5 else "資料不足"
        if os.path.exists(DB_PATH):
            stats["db_size_mb"] = round(os.path.getsize(DB_PATH) / 1024 / 1024, 2)
    return stats


def format_db_status(stats):
    return (
        "資料庫狀態\n"
        "══════════════════════\n"
        f"股票清單：{stats['stocks']:,} 支\n"
        f"每日股價：{stats['daily_price']:,} 筆\n"
        f"  範圍：{stats['price_date_range']}\n"
        f"三大法人：{stats['institutional']:,} 筆\n"
        f"  最新：{stats['inst_latest']}\n"
        f"月營收：{stats['monthly_revenue']:,} 筆\n"
        f"  最新：{stats['revenue_latest']}\n"
        f"宏觀指標：{stats['macro_daily']:,} 筆\n"
        f"  最新：{stats['macro_latest']}\n"
        f"因子快取：{stats['factor_cache']:,} 筆\n"
        "══════════════════════\n"
        f"交易日誌：{stats['trade_log']:,} 筆\n"
        f"勝率資料庫：{stats['win_rate_db']:,} 支\n"
        f"分析記錄：{stats['analysis_log']:,} 筆\n"
        f"最優股票：{stats['best_stock']}\n"
        "══════════════════════\n"
        f"資料庫大小：{stats.get('db_size_mb', 0)} MB\n\n"
        "輸入「更新資料」立即更新所有資料"
    )


def get_crawl_progress(task_name):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT last_date FROM crawl_progress WHERE task_name=?", (task_name,)
        ).fetchone()
        return row["last_date"] if row else None


def update_crawl_progress(task_name, last_date, status="running"):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO crawl_progress (task_name, last_date, status, updated_at)
            VALUES (?, ?, ?, datetime('now','localtime'))
            ON CONFLICT(task_name) DO UPDATE SET
                last_date=excluded.last_date, status=excluded.status,
                updated_at=excluded.updated_at
        """, (task_name, last_date, status))


def log_crawl(task, status, message=""):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO crawl_log (task, status, message) VALUES (?, ?, ?)",
            (task, status, message)
        )
