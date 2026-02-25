"""
memory/daily_learning.py - 每日自主學習機制
Bot 每天盤後自動：
1. 分析大盤與自選股
2. 記錄市場觀察
3. 驗證昨日預測是否正確
4. 累積學習經驗
"""
import json
import os
import logging
from datetime import datetime, timedelta
import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_SMART_MODEL, MEMORY_DIR

logger = logging.getLogger(__name__)

LEARNING_LOG_FILE = os.path.join(MEMORY_DIR, "learning_log.json")
WATCHLIST_FILE = os.path.join(MEMORY_DIR, "watchlist.json")

claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


# ── 自選股管理 ────────────────────────────────────

def load_watchlist() -> list:
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("stocks", [])
    # 預設監控清單
    return ["2330", "2317", "2454", "2382", "2308", "2881", "2882", "6505"]


def save_watchlist(stocks: list):
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        json.dump({"stocks": stocks, "updated_at": datetime.now().isoformat()}, f, ensure_ascii=False, indent=2)


def add_to_watchlist(stock_id: str) -> str:
    stocks = load_watchlist()
    if stock_id in stocks:
        return f"⚠️ {stock_id} 已在監控清單中"
    stocks.append(stock_id)
    save_watchlist(stocks)
    return f"✅ 已將 {stock_id} 加入監控清單"


def remove_from_watchlist(stock_id: str) -> str:
    stocks = load_watchlist()
    if stock_id not in stocks:
        return f"⚠️ {stock_id} 不在監控清單中"
    stocks.remove(stock_id)
    save_watchlist(stocks)
    return f"✅ 已將 {stock_id} 從監控清單移除"


def list_watchlist() -> str:
    stocks = load_watchlist()
    if not stocks:
        return "📋 監控清單是空的，用 /watch 新增股票"
    return "📋 *目前監控清單：*\n" + "、".join(stocks)


# ── 學習日誌 ──────────────────────────────────────

def load_learning_log() -> list:
    if os.path.exists(LEARNING_LOG_FILE):
        with open(LEARNING_LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_learning_log(logs: list):
    with open(LEARNING_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs[-90:], f, ensure_ascii=False, indent=2)  # 只保留最近90天


def add_learning_entry(entry: dict):
    logs = load_learning_log()
    entry["date"] = datetime.now().strftime("%Y-%m-%d")
    entry["timestamp"] = datetime.now().isoformat()
    logs.append(entry)
    save_learning_log(logs)


def get_recent_learnings(days: int = 7) -> str:
    """取得最近 N 天的學習摘要"""
    logs = load_learning_log()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    recent = [l for l in logs if l.get("date", "") >= cutoff]

    if not recent:
        return "目前還沒有學習記錄"

    lines = [f"📚 最近 {days} 天的市場觀察：\n"]
    for log in recent[-5:]:  # 最多顯示最近5筆
        lines.append(f"📅 {log.get('date')}")
        lines.append(f"{log.get('summary', '')}")
        lines.append("─" * 20)

    return "\n".join(lines)


# ── 每日自主學習主程式 ────────────────────────────

async def daily_learning_task() -> str:
    """
    每日自主學習核心邏輯
    1. 取得所有監控股票的即時數據
    2. 呼叫 Claude 進行分析
    3. 儲存學習結果
    4. 回傳報告
    """
    from factors.realtime import get_stock_quote, fetch_historical
    from factors.technical import calc_technical_factors
    from factors.macro import get_macro_snapshot
    from memory.rules_manager import get_rules_as_prompt

    today = datetime.now().strftime("%Y-%m-%d")
    watchlist = load_watchlist()

    logger.info(f"開始每日學習任務，監控 {len(watchlist)} 支股票")

    # 1. 取得宏觀數據
    macro = get_macro_snapshot()

    # 2. 分析每支股票
    stock_summaries = []
    candidates = []

    for stock_id in watchlist:
        try:
            quote = get_stock_quote(stock_id)
            if not quote or quote.get("close", 0) == 0:
                continue

            # 取得技術指標
            hist = fetch_historical(stock_id, days=60)
            tech_summary = ""
            if not hist.empty:
                tech = calc_technical_factors(hist)
                latest = tech.iloc[-1]
                rsi = latest.get("rsi", 0)
                bias = latest.get("bias_20ma", 0)
                macd_slope = latest.get("macd_hist_slope", 0)
                tech_summary = f"RSI:{rsi:.1f} 乖離:{bias:.2%} MACD斜率:{'上揚' if macd_slope > 0 else '下彎'}"

            stock_summaries.append(
                f"{stock_id} {quote.get('name','')} "
                f"現價:{quote.get('close')} "
                f"漲跌:{quote.get('change_pct',0):+.2f}% "
                f"{tech_summary}"
            )

            # 找出今日強勢股（漲幅 > 2% 且 RSI < 70）
            if quote.get("change_pct", 0) > 2:
                candidates.append(f"{stock_id} {quote.get('name','')} ({quote.get('change_pct',0):+.2f}%)")

        except Exception as e:
            logger.error(f"分析 {stock_id} 失敗: {e}")

    # 3. 呼叫 Claude 進行深度分析與學習
    rules_prompt = get_rules_as_prompt()

    analysis_prompt = f"""
今天是 {today}，請以台股量化分析師的角度，根據以下數據進行分析：

【宏觀指標】
VIX：{macro.get('vix', 'N/A')} ({macro.get('vix_signal', '')})
大盤騰落：{macro.get('adl_daily', 'N/A')} 家

【監控股票今日狀況】
{chr(10).join(stock_summaries) if stock_summaries else '無資料'}

【今日強勢股】
{chr(10).join(candidates) if candidates else '今日無明顯強勢股'}

{rules_prompt}

請提供：
1. 今日市場整體判斷（多/空/中性）
2. 值得關注的股票（附理由）
3. 明日操作策略建議
4. 今日學習到的市場規律（1~2點）

請用繁體中文回覆，簡潔有力。
"""

    try:
        response = claude_client.messages.create(
            model=CLAUDE_SMART_MODEL,
            max_tokens=1500,
            system="你是專業的台股量化分析師，每日進行市場學習與分析，回覆必須使用繁體中文。",
            messages=[{"role": "user", "content": analysis_prompt}],
        )
        analysis = response.content[0].text

        # 4. 儲存學習記錄
        add_learning_entry({
            "summary": analysis[:500],  # 儲存前500字作為摘要
            "macro_vix": macro.get("vix"),
            "macro_adl": macro.get("adl_daily"),
            "candidates": candidates,
            "stocks_analyzed": len(stock_summaries),
        })

        # 5. 組合最終報告
        report = (
            f"📊 *每日自主學習報告*\n"
            f"📅 {today}\n"
            f"{'─' * 30}\n"
            f"🌐 VIX：{macro.get('vix', 'N/A')} | ADL：{macro.get('adl_daily', 'N/A')}\n"
            f"📈 分析股票數：{len(stock_summaries)} 支\n"
            f"{'─' * 30}\n"
            f"{analysis}"
        )
        return report

    except Exception as e:
        logger.error(f"Claude 分析失敗: {e}")
        return f"❌ 每日學習任務失敗：{str(e)}"


if __name__ == "__main__":
    import asyncio
    report = asyncio.run(daily_learning_task())
    print(report)
