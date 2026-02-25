"""
portfolio/tracker.py - 持股追蹤 + 停損停利即時警報
功能：
- 記錄進場價格、停損、目標價
- 每15分鐘盤中監控
- 觸及停損或目標時立刻推播 TG
"""
import json
import os
import logging
from datetime import datetime
from config import MEMORY_DIR

logger = logging.getLogger(__name__)
PORTFOLIO_FILE = os.path.join(MEMORY_DIR, "portfolio.json")


# ── 持股管理 ──────────────────────────────────────

def load_portfolio() -> dict:
    if os.path.exists(PORTFOLIO_FILE):
        with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_portfolio(data: dict):
    with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_position(stock_id: str, entry_price: float, shares: int,
                 stop_loss_pct: float = 0.05, target_pct: float = 0.10) -> str:
    """
    新增持股
    stop_loss_pct: 停損比例（預設 5%）
    target_pct: 目標獲利比例（預設 10%）
    """
    portfolio = load_portfolio()
    stop_loss_price = round(entry_price * (1 - stop_loss_pct), 2)
    target_price = round(entry_price * (1 + target_pct), 2)

    portfolio[stock_id] = {
        "stock_id": stock_id,
        "entry_price": entry_price,
        "shares": shares,
        "stop_loss": stop_loss_price,
        "target": target_price,
        "stop_loss_pct": stop_loss_pct,
        "target_pct": target_pct,
        "entry_date": datetime.now().strftime("%Y-%m-%d"),
        "status": "持有中",
        "alerted": False,
    }
    save_portfolio(portfolio)

    cost = entry_price * shares * 1000
    return (
        f"✅ 已加入持股追蹤\n\n"
        f"股票：{stock_id}\n"
        f"進場價：${entry_price}\n"
        f"張數：{shares} 張\n"
        f"成本：${cost:,.0f}\n"
        f"停損價：${stop_loss_price} (-{stop_loss_pct*100:.0f}%)\n"
        f"目標價：${target_price} (+{target_pct*100:.0f}%)\n"
        f"📌 盤中每15分鐘自動監控"
    )


def remove_position(stock_id: str, exit_price: float = None) -> str:
    portfolio = load_portfolio()
    if stock_id not in portfolio:
        return f"❌ 持股中找不到 {stock_id}"

    pos = portfolio[stock_id]
    result = f"✅ 已移除 {stock_id} 持股追蹤"

    if exit_price:
        entry = pos["entry_price"]
        pnl_pct = (exit_price - entry) / entry * 100
        pnl_amount = (exit_price - entry) * pos["shares"] * 1000
        emoji = "🟢" if pnl_pct > 0 else "🔴"
        result += (
            f"\n\n{emoji} 交易結果\n"
            f"進場：${entry} → 出場：${exit_price}\n"
            f"損益：{pnl_pct:+.2f}%（${pnl_amount:+,.0f}）"
        )

    del portfolio[stock_id]
    save_portfolio(portfolio)
    return result


def list_portfolio() -> str:
    portfolio = load_portfolio()
    if not portfolio:
        return "📋 目前沒有持股\n\n輸入「買進 2330 100 進場價格」來新增追蹤"

    lines = ["📊 持股追蹤清單\n"]
    for sid, pos in portfolio.items():
        lines.append(
            f"━━ {sid} ({pos.get('entry_date','')}) ━━\n"
            f"進場：${pos['entry_price']}  張數：{pos['shares']} 張\n"
            f"停損：${pos['stop_loss']}  目標：${pos['target']}\n"
            f"狀態：{pos.get('status','持有中')}"
        )
    return "\n\n".join(lines)


# ── 盤中監控核心 ──────────────────────────────────

async def check_portfolio_alerts() -> list:
    """
    檢查所有持股是否觸及停損或目標
    回傳需要推播的警報列表
    """
    from factors.realtime import get_stock_quote
    portfolio = load_portfolio()
    alerts = []

    for stock_id, pos in portfolio.items():
        if pos.get("status") != "持有中":
            continue
        try:
            quote = get_stock_quote(stock_id)
            if not quote or quote.get("close", 0) == 0:
                continue

            current = quote["close"]
            entry = pos["entry_price"]
            pnl_pct = (current - entry) / entry * 100

            # 觸及停損
            if current <= pos["stop_loss"]:
                alerts.append({
                    "type": "🚨 停損警報",
                    "stock_id": stock_id,
                    "current": current,
                    "entry": entry,
                    "pnl_pct": pnl_pct,
                    "message": (
                        f"🚨 停損警報！{stock_id}\n"
                        f"現價 ${current} 已跌破停損價 ${pos['stop_loss']}\n"
                        f"損益：{pnl_pct:+.2f}%\n"
                        f"建議立即執行停損！"
                    ),
                })
                pos["status"] = "停損觸發"
                pos["alerted"] = True

            # 觸及目標
            elif current >= pos["target"]:
                alerts.append({
                    "type": "🎯 目標達成",
                    "stock_id": stock_id,
                    "current": current,
                    "entry": entry,
                    "pnl_pct": pnl_pct,
                    "message": (
                        f"🎯 目標達成！{stock_id}\n"
                        f"現價 ${current} 已達目標價 ${pos['target']}\n"
                        f"獲利：{pnl_pct:+.2f}%\n"
                        f"建議考慮分批出場或移動停利"
                    ),
                })
                pos["status"] = "目標達成"

            # 一般更新狀態
            pos["current_price"] = current
            pos["current_pnl_pct"] = round(pnl_pct, 2)
            pos["last_check"] = datetime.now().strftime("%H:%M")

        except Exception as e:
            logger.error(f"監控 {stock_id} 失敗: {e}")

    save_portfolio(portfolio)
    return alerts
