"""
risk/manager.py - 風險控管模組
功能：
- 單次最大投入資金比例
- 同時持股上限
- 大盤空頭自動暫停選股
- 每日最大虧損上限
- 黑天鵝偵測
"""
import json
import os
import logging
from datetime import datetime
from config import MEMORY_DIR

logger = logging.getLogger(__name__)
RISK_CONFIG_FILE = os.path.join(MEMORY_DIR, "risk_config.json")
RISK_LOG_FILE = os.path.join(MEMORY_DIR, "risk_log.json")

# 預設風控參數
DEFAULT_RISK_CONFIG = {
    "total_capital": 1_000_000,       # 總資金（元）
    "max_position_pct": 0.10,          # 單次最大投入 10%
    "max_positions": 5,                # 最大同時持股數
    "max_daily_loss_pct": 0.03,        # 每日最大虧損 3%
    "stop_loss_pct": 0.05,             # 停損比例 5%
    "take_profit_pct": 0.10,           # 停利比例 10%
    "pause_when_vix_above": 30,        # VIX 超過 30 暫停選股
    "pause_when_adl_below": -300,      # ADL 低於 -300 暫停選股
    "min_ml_score": 0.60,              # ML 最低進場門檻
    "market_pause": False,             # 是否已暫停選股
    "updated_at": "",
}


def load_risk_config() -> dict:
    if os.path.exists(RISK_CONFIG_FILE):
        with open(RISK_CONFIG_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
            config = DEFAULT_RISK_CONFIG.copy()
            config.update(saved)
            return config
    return DEFAULT_RISK_CONFIG.copy()


def save_risk_config(config: dict):
    config["updated_at"] = datetime.now().isoformat()
    with open(RISK_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def update_risk_param(key: str, value) -> str:
    """更新單一風控參數"""
    config = load_risk_config()
    if key not in config:
        return f"❌ 找不到參數：{key}"
    config[key] = value
    save_risk_config(config)
    return f"✅ 已更新 {key} = {value}"


def get_risk_summary() -> str:
    """顯示目前風控設定"""
    c = load_risk_config()
    status = "🔴 已暫停" if c["market_pause"] else "🟢 運行中"
    return (
        f"🛡️ 風險控管設定\n\n"
        f"系統狀態：{status}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"總資金：${c['total_capital']:,.0f}\n"
        f"單次最大投入：{c['max_position_pct']*100:.0f}%"
        f"（${c['total_capital']*c['max_position_pct']:,.0f}）\n"
        f"最大同時持股：{c['max_positions']} 支\n"
        f"每日最大虧損：{c['max_daily_loss_pct']*100:.0f}%\n"
        f"停損設定：{c['stop_loss_pct']*100:.0f}%\n"
        f"停利設定：{c['take_profit_pct']*100:.0f}%\n"
        f"━━━━━━━━━━━━━━━\n"
        f"VIX 暫停門檻：{c['pause_when_vix_above']}\n"
        f"ADL 暫停門檻：{c['pause_when_adl_below']}\n"
        f"ML 進場門檻：{c['min_ml_score']*100:.0f}%\n\n"
        f"修改範例：\n"
        f"「風控設定 停損 0.07」→ 停損改為 7%\n"
        f"「風控設定 總資金 2000000」→ 改總資金"
    )


def check_market_risk() -> dict:
    """
    檢查市場整體風險
    判斷是否應暫停選股
    """
    from factors.macro import get_macro_snapshot
    config = load_risk_config()
    warnings = []
    should_pause = False

    try:
        macro = get_macro_snapshot()
        vix = macro.get("vix", 0) or 0
        adl = macro.get("adl_daily", 0) or 0

        if vix >= config["pause_when_vix_above"]:
            should_pause = True
            warnings.append(f"🚨 VIX={vix:.1f} 超過警戒線 {config['pause_when_vix_above']}，市場恐慌！")

        if adl <= config["pause_when_adl_below"]:
            should_pause = True
            warnings.append(f"🚨 ADL={adl} 低於警戒線 {config['pause_when_adl_below']}，大盤弱勢！")

        if vix >= 20:
            warnings.append(f"⚠️ VIX={vix:.1f} 偏高，建議降低倉位")

    except Exception as e:
        logger.error(f"市場風險檢查失敗: {e}")

    # 更新暫停狀態
    if should_pause != config["market_pause"]:
        config["market_pause"] = should_pause
        save_risk_config(config)

    return {
        "should_pause": should_pause,
        "market_pause": config["market_pause"],
        "warnings": warnings,
    }


def calc_position_size(stock_price: float) -> dict:
    """
    計算建議建倉大小（張數）
    """
    config = load_risk_config()
    max_invest = config["total_capital"] * config["max_position_pct"]
    cost_per_lot = stock_price * 1000
    suggested_lots = int(max_invest / cost_per_lot)
    actual_invest = suggested_lots * cost_per_lot

    return {
        "stock_price": stock_price,
        "max_invest": max_invest,
        "suggested_lots": max(1, suggested_lots),
        "actual_invest": actual_invest,
        "stop_loss_price": round(stock_price * (1 - config["stop_loss_pct"]), 2),
        "take_profit_price": round(stock_price * (1 + config["take_profit_pct"]), 2),
        "max_loss_amount": round(actual_invest * config["stop_loss_pct"], 0),
    }


def check_blackswan(change_pct: float, vix: float = None) -> bool:
    """偵測黑天鵝事件（大盤單日暴跌 > 5%）"""
    if abs(change_pct) >= 5:
        logger.warning(f"🚨 黑天鵝偵測！大盤變動 {change_pct:.2f}%")
        config = load_risk_config()
        config["market_pause"] = True
        save_risk_config(config)
        return True
    return False
