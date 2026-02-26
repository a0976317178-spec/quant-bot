"""
memory/trade_log.py - 交易日誌 + 自動週報
功能：
- 自動記錄每次進出場
- 每週日自動生成績效週報
- 勝率統計、規則有效性分析
"""
import json
import os
import logging
from datetime import datetime, timedelta
from config import MEMORY_DIR

logger = logging.getLogger(__name__)
TRADE_LOG_FILE = os.path.join(MEMORY_DIR, "trade_log.json")
WEEKLY_REPORT_FILE = os.path.join(MEMORY_DIR, "weekly_reports.json")


# ══════════════════════════════════════════════════════
# 交易日誌
# ══════════════════════════════════════════════════════

def load_trade_log() -> list:
    if os.path.exists(TRADE_LOG_FILE):
        with open(TRADE_LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_trade_log(logs: list):
    with open(TRADE_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)


def log_entry(stock_id: str, entry_price: float, shares: int,
              reason: str = "", score: int = 0):
    """記錄進場"""
    logs = load_trade_log()
    record = {
        "id": len(logs) + 1,
        "stock_id": stock_id,
        "action": "買進",
        "price": entry_price,
        "shares": shares,
        "score": score,
        "reason": reason,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "time": datetime.now().strftime("%H:%M"),
        "status": "持有中",
        "exit_price": None,
        "exit_date": None,
        "exit_reason": "",
        "pnl_pct": None,
        "pnl_amount": None,
    }
    logs.append(record)
    save_trade_log(logs)
    logger.info(f"交易日誌：記錄買進 {stock_id} ${entry_price}")
    return record["id"]


def log_exit(stock_id: str, exit_price: float, exit_reason: str = "手動出場"):
    """記錄出場，自動計算損益"""
    logs = load_trade_log()
    updated = False

    for record in reversed(logs):
        if record["stock_id"] == stock_id and record["status"] == "持有中":
            entry_price = record["price"]
            pnl_pct = (exit_price - entry_price) / entry_price * 100
            pnl_amount = (exit_price - entry_price) * record["shares"] * 1000

            record["exit_price"] = exit_price
            record["exit_date"] = datetime.now().strftime("%Y-%m-%d")
            record["exit_reason"] = exit_reason
            record["pnl_pct"] = round(pnl_pct, 2)
            record["pnl_amount"] = round(pnl_amount, 0)
            record["status"] = "已出場"
            updated = True
            break

    if updated:
        save_trade_log(logs)
    return updated


def get_trade_stats(days: int = 30) -> dict:
    """計算近N天的交易統計"""
    logs = load_trade_log()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    closed = [
        t for t in logs
        if t["status"] == "已出場"
        and (t.get("exit_date") or "") >= cutoff
    ]

    if not closed:
        return {
            "total": 0, "wins": 0, "losses": 0,
            "win_rate": 0, "total_pnl": 0,
            "avg_win": 0, "avg_loss": 0,
            "best": None, "worst": None,
        }

    wins = [t for t in closed if (t.get("pnl_pct") or 0) > 0]
    losses = [t for t in closed if (t.get("pnl_pct") or 0) <= 0]
    total_pnl = sum(t.get("pnl_amount") or 0 for t in closed)
    avg_win = sum(t.get("pnl_pct") or 0 for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t.get("pnl_pct") or 0 for t in losses) / len(losses) if losses else 0

    best = max(closed, key=lambda x: x.get("pnl_pct") or 0) if closed else None
    worst = min(closed, key=lambda x: x.get("pnl_pct") or 0) if closed else None

    return {
        "total": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(closed) * 100, 1),
        "total_pnl": round(total_pnl, 0),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "best": best,
        "worst": worst,
    }


def format_trade_history(limit: int = 10) -> str:
    """格式化近期交易記錄"""
    logs = load_trade_log()
    if not logs:
        return "📋 尚無交易記錄\n\n輸入「買進 2330 1 980」開始記錄"

    recent = list(reversed(logs))[:limit]
    lines = [f"📋 近期交易記錄（共 {len(logs)} 筆）\n"]

    for t in recent:
        if t["status"] == "已出場":
            emoji = "🟢" if (t.get("pnl_pct") or 0) > 0 else "🔴"
            pnl = t.get("pnl_pct") or 0
            lines.append(
                f"{emoji} #{t['id']} {t['stock_id']} "
                f"{t['date']}→{t.get('exit_date','?')}\n"
                f"   ${t['price']}→${t.get('exit_price','?')} "
                f"損益：{pnl:+.2f}% | {t.get('exit_reason','')}"
            )
        else:
            lines.append(
                f"🔵 #{t['id']} {t['stock_id']} "
                f"{t['date']} 持有中\n"
                f"   進場：${t['price']} × {t['shares']}張"
            )

    return "\n".join(lines)


# ══════════════════════════════════════════════════════
# 自動週報生成
# ══════════════════════════════════════════════════════

async def generate_weekly_report(claude_client, user_id: int) -> str:
    """
    每週日自動生成週報
    包含：本週績效、交易統計、AI 檢討建議
    """
    from memory.rules_manager import load_history, get_rules_as_prompt

    stats = get_trade_stats(days=7)
    monthly_stats = get_trade_stats(days=30)
    logs = load_trade_log()

    # 本週交易明細
    cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    this_week = [
        t for t in logs
        if t["date"] >= cutoff
    ]

    # 持倉狀態
    holding = [t for t in logs if t["status"] == "持有中"]

    # 建構週報基本資料
    report_data = (
        f"本週交易統計：\n"
        f"交易次數：{stats['total']} 次\n"
        f"勝率：{stats['win_rate']}%（{stats['wins']}勝/{stats['losses']}敗）\n"
        f"本週損益：${stats['total_pnl']:+,.0f}\n"
        f"平均獲利：{stats['avg_win']:+.2f}%\n"
        f"平均虧損：{stats['avg_loss']:.2f}%\n"
    )

    if stats["best"]:
        b = stats["best"]
        report_data += f"最佳交易：{b['stock_id']} {b.get('pnl_pct',0):+.2f}%\n"
    if stats["worst"]:
        w = stats["worst"]
        report_data += f"最差交易：{w['stock_id']} {w.get('pnl_pct',0):+.2f}%\n"

    report_data += f"\n本月累計統計：\n"
    report_data += f"交易次數：{monthly_stats['total']} 次\n"
    report_data += f"勝率：{monthly_stats['win_rate']}%\n"
    report_data += f"累計損益：${monthly_stats['total_pnl']:+,.0f}\n"

    if holding:
        report_data += f"\n目前持倉：{len(holding)} 支\n"
        for h in holding:
            report_data += f"  {h['stock_id']} 進場${h['price']} ({h['date']})\n"

    # 本週交易進出場原因
    if this_week:
        report_data += "\n本週交易明細：\n"
        for t in this_week:
            report_data += (
                f"  {t['stock_id']} {t['action']} "
                f"${t['price']} | 原因：{t.get('reason','未填寫')}\n"
            )

    # 請 Claude 做深度檢討
    prompt = (
        f"以下是我這週的台股交易記錄，請幫我做專業的績效檢討：\n\n"
        f"{report_data}\n\n"
        f"請分析：\n"
        f"1. 本週操作的優缺點\n"
        f"2. 勝率和損益是否達到合理水準（目標：勝率>55%，盈虧比>1.5）\n"
        f"3. 有沒有違反紀律的跡象\n"
        f"4. 下週的操作建議\n"
        f"5. 需要修正的交易習慣\n\n"
        f"請用繁體中文，語氣像嚴格但善意的投資教練。"
    )

    try:
        from config import CLAUDE_SMART_MODEL
        import anthropic
        client = claude_client
        resp = client.messages.create(
            model=CLAUDE_SMART_MODEL,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        ai_review = resp.content[0].text
    except Exception as e:
        ai_review = f"（AI 檢討生成失敗：{e}）"

    # 組合完整週報
    week_str = datetime.now().strftime("%Y/%m/%d")
    report = (
        f"📊 週報 {week_str}\n"
        f"══════════════════════\n\n"
        f"💰 本週績效\n"
        f"交易：{stats['total']}次 | 勝率：{stats['win_rate']}%\n"
        f"損益：${stats['total_pnl']:+,.0f}\n"
        f"均獲利：{stats['avg_win']:+.2f}% | 均虧損：{stats['avg_loss']:.2f}%\n\n"
        f"📅 本月累計\n"
        f"交易：{monthly_stats['total']}次 | 勝率：{monthly_stats['win_rate']}%\n"
        f"損益：${monthly_stats['total_pnl']:+,.0f}\n\n"
        f"══════════════════════\n"
        f"🤖 AI 教練檢討\n\n"
        f"{ai_review}\n\n"
        f"══════════════════════\n"
        f"輸入「交易記錄」查看詳細歷史"
    )

    # 儲存週報
    reports = []
    if os.path.exists(WEEKLY_REPORT_FILE):
        with open(WEEKLY_REPORT_FILE, "r", encoding="utf-8") as f:
            reports = json.load(f)
    reports.append({
        "date": week_str,
        "stats": stats,
        "monthly_stats": monthly_stats,
        "report": report,
    })
    # 只保留最近 52 週
    reports = reports[-52:]
    with open(WEEKLY_REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(reports, f, ensure_ascii=False, indent=2)

    return report
