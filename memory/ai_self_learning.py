"""
memory/ai_self_learning.py - AI 每日自學習模組（新功能）

功能：
  1. 每日分析 analysis_log（分析過的股票評分 vs 後來實際漲跌）
  2. 找出哪些因子最準確、哪些常出錯
  3. 用 Claude API 生成學習心得 + 改進建議
  4. 自動產生代碼片段優化建議（寫到 self_learning_log）
  5. 每日推送學習報告到 Telegram

每日 21:00 自動執行（在 main.py 排程器中加入）
"""
import json
import logging
import os
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

LEARNING_LOG_PATH = "data/self_learning_log.jsonl"


def _ensure_dir():
    os.makedirs("data", exist_ok=True)


def _load_recent_logs(days: int = 7) -> list:
    """讀取最近 N 天的學習日誌"""
    _ensure_dir()
    if not os.path.exists(LEARNING_LOG_PATH):
        return []
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    records = []
    with open(LEARNING_LOG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            try:
                obj = json.loads(line.strip())
                if obj.get("date", "") >= cutoff:
                    records.append(obj)
            except Exception:
                pass
    return records


def _save_learning(record: dict):
    """儲存一筆學習記錄"""
    _ensure_dir()
    with open(LEARNING_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _get_analysis_performance(days: int = 5) -> dict:
    """
    從 DB 取得最近 N 天的分析記錄，
    並計算「評分高的股票後來表現如何」
    回傳：統計資料 dict
    """
    try:
        from database.db_manager import query_df

        cutoff = (datetime.now() - timedelta(days=days + 3)).strftime("%Y-%m-%d")
        sql = """
            SELECT a.stock_id, a.date, a.score,
                   a.tech_score, a.chip_score, a.fund_score, a.env_score,
                   a.close_price,
                   p_after.close AS close_after
            FROM analysis_log a
            LEFT JOIN daily_price p_after
                ON a.stock_id = p_after.stock_id
                AND p_after.date = (
                    SELECT date FROM daily_price
                    WHERE stock_id = a.stock_id AND date > a.date
                    ORDER BY date LIMIT 1
                )
            WHERE a.date >= ?
            ORDER BY a.date DESC
        """
        df = query_df(sql, (cutoff,))

        if df.empty:
            return {"status": "no_data", "records": 0}

        df = df.dropna(subset=["close_after", "close_price"])
        df = df[df["close_price"] > 0]
        df["next_day_return"] = (df["close_after"] - df["close_price"]) / df["close_price"] * 100

        high_score = df[df["score"] >= 60]
        low_score  = df[df["score"] <  45]

        stats = {
            "status": "ok",
            "records": len(df),
            "total_analyzed": len(df),
            "high_score_count": len(high_score),
            "high_score_avg_return": round(high_score["next_day_return"].mean(), 2) if len(high_score) > 0 else 0,
            "low_score_count": len(low_score),
            "low_score_avg_return": round(low_score["next_day_return"].mean(), 2) if len(low_score) > 0 else 0,
            # 各因子相關性
            "tech_correlation":  round(df["tech_score"].corr(df["next_day_return"]), 3) if len(df) > 5 else 0,
            "chip_correlation":  round(df["chip_score"].corr(df["next_day_return"]), 3) if len(df) > 5 else 0,
            "fund_correlation":  round(df["fund_score"].corr(df["next_day_return"]), 3) if len(df) > 5 else 0,
            "env_correlation":   round(df["env_score"].corr(df["next_day_return"]),  3) if len(df) > 5 else 0,
            "top_performers":    df.nlargest(3, "next_day_return")[["stock_id", "score", "next_day_return"]].to_dict("records"),
            "worst_performers":  df.nsmallest(3, "next_day_return")[["stock_id", "score", "next_day_return"]].to_dict("records"),
        }
        return stats

    except Exception as e:
        logger.error(f"取得分析績效失敗: {e}")
        return {"status": "error", "message": str(e), "records": 0}


def _get_win_rate_trends() -> dict:
    """從 win_rate_db 取得勝率趨勢摘要"""
    try:
        from database.db_manager import query_df
        sql = """
            SELECT stock_id, win_rate, total_trades, profit_factor,
                   last_updated
            FROM win_rate_db
            ORDER BY win_rate DESC LIMIT 10
        """
        df = query_df(sql)
        if df.empty:
            return {}
        return {
            "top_stocks": df.to_dict("records"),
            "avg_win_rate": round(df["win_rate"].mean(), 1),
        }
    except Exception as e:
        logger.warning(f"取得勝率趨勢失敗: {e}")
        return {}


def run_daily_self_learning(claude_client) -> str:
    """
    每日自學習主流程（由排程器呼叫）
    1. 分析近期績效數據
    2. 呼叫 Claude 生成洞察
    3. 自動產生改進代碼建議
    4. 儲存並回傳報告
    """
    today = datetime.now().strftime("%Y-%m-%d")
    logger.info(f"開始每日自學習：{today}")

    # 收集數據
    perf  = _get_analysis_performance(days=5)
    rates = _get_win_rate_trends()

    # 組成學習 prompt
    prompt = f"""
你是台股量化交易AI「量化師」的自學習模組。
今天是 {today}，請根據以下數據進行學習分析：

【近5日分析績效】
{json.dumps(perf, ensure_ascii=False, indent=2)}

【勝率資料庫摘要】
{json.dumps(rates, ensure_ascii=False, indent=2)}

請完成以下三項任務，全程使用繁體中文：

1. 【今日學習心得】（3~5點）
   - 哪個因子最近最準確？哪個因子表現最差？
   - 高分股票是否真的有上漲？
   - 有什麼市場規律發現？

2. 【策略優化建議】（2~3點具體建議）
   - 建議調整哪些評分權重？
   - 是否需要增加新的過濾條件？

3. 【代碼改進提案】（1~2個具體代碼片段）
   - 用 Python 代碼寫出建議的改進，格式如下：
   ```python
   # 改進說明：XXX
   # 檔案：factors/analyzer.py
   # 在 analyze_technical() 中新增：
   def 新函式或修改邏輯():
       ...
   ```

請保持專業、數據導向，避免空泛建議。
"""

    try:
        from config import CLAUDE_SMART_MODEL
        resp = claude_client.messages.create(
            model=CLAUDE_SMART_MODEL,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )
        ai_insight = resp.content[0].text
    except Exception as e:
        ai_insight = f"Claude API 呼叫失敗：{e}"
        logger.error(ai_insight)

    # 儲存學習記錄
    record = {
        "date": today,
        "performance_stats": perf,
        "win_rate_summary": rates,
        "ai_insight": ai_insight,
        "generated_at": datetime.now().isoformat(),
    }
    _save_learning(record)

    # 格式化報告
    report = (
        f"🧠 AI 每日自學習報告\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📅 {today}\n\n"
    )

    if perf.get("status") == "ok":
        report += (
            f"📊 近5日績效回顧\n"
            f"  分析記錄：{perf['records']} 筆\n"
            f"  高分股（≥60）隔日均報酬：{perf['high_score_avg_return']:+.2f}%\n"
            f"  低分股（<45）隔日均報酬：{perf['low_score_avg_return']:+.2f}%\n"
            f"  技術面相關性：{perf['tech_correlation']}\n"
            f"  籌碼面相關性：{perf['chip_correlation']}\n\n"
        )

    report += f"💡 AI 洞察\n{ai_insight}\n"
    report += f"\n━━━━━━━━━━━━━━━\n輸入「學習記錄」查看歷史"

    logger.info(f"每日自學習完成，洞察長度：{len(ai_insight)} 字")
    return report


def get_self_learning_summary(days: int = 7) -> str:
    """取得最近幾天的自學習摘要（供 /learning 指令使用）"""
    records = _load_recent_logs(days=days)
    if not records:
        return f"最近 {days} 天尚無自學習記錄\n排程每日 21:00 自動執行"

    lines = [f"🧠 最近 {days} 天自學習摘要\n"]
    for rec in reversed(records[-5:]):  # 最新5筆
        date = rec.get("date", "未知")
        insight = rec.get("ai_insight", "")
        # 只取前2行
        short = "\n".join(insight.split("\n")[:3]) if insight else "無記錄"
        lines.append(f"📅 {date}\n{short}\n")

    return "\n".join(lines)
