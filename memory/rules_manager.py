"""
memory/rules_manager.py - 管理用戶自訂選股規則（讓 AI 記住您的策略）
"""
import json
import os
from datetime import datetime
from config import MEMORY_DIR

RULES_FILE = os.path.join(MEMORY_DIR, "rules.json")
CHAT_HISTORY_FILE = os.path.join(MEMORY_DIR, "chat_history.json")


def load_rules() -> dict:
    """載入所有規則"""
    if os.path.exists(RULES_FILE):
        with open(RULES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"rules": [], "strategy_notes": "", "updated_at": ""}


def save_rules(data: dict):
    """儲存規則"""
    data["updated_at"] = datetime.now().isoformat()
    with open(RULES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_rule(rule_text: str) -> str:
    """新增一條規則"""
    data = load_rules()
    rule = {
        "id": len(data["rules"]) + 1,
        "text": rule_text,
        "created_at": datetime.now().isoformat(),
    }
    data["rules"].append(rule)
    save_rules(data)
    return f"✅ 已新增規則 #{rule['id']}：{rule_text}"


def delete_rule(rule_id: int) -> str:
    """刪除規則"""
    data = load_rules()
    before = len(data["rules"])
    data["rules"] = [r for r in data["rules"] if r["id"] != rule_id]
    if len(data["rules"]) < before:
        save_rules(data)
        return f"✅ 已刪除規則 #{rule_id}"
    return f"❌ 找不到規則 #{rule_id}"


def list_rules() -> str:
    """列出所有規則"""
    data = load_rules()
    if not data["rules"]:
        return "📋 目前沒有自訂規則"
    lines = ["📋 *您的選股規則：*\n"]
    for r in data["rules"]:
        lines.append(f"#{r['id']} {r['text']}")
    return "\n".join(lines)


def get_rules_as_prompt() -> str:
    """將規則轉換成 Claude 的 System Prompt"""
    data = load_rules()
    if not data["rules"]:
        return ""
    rules_text = "\n".join([f"- {r['text']}" for r in data["rules"]])
    return f"""
以下是用戶自訂的選股規則，你在進行分析時必須嚴格遵守：
{rules_text}

如果某支股票違反以上任一規則，即使 ML 模型給出高分，也必須排除並說明原因。
"""


# ── 對話記憶 ──────────────────────────────────────

def load_history(user_id: int, max_turns: int = 10) -> list:
    """載入對話記憶"""
    if os.path.exists(CHAT_HISTORY_FILE):
        with open(CHAT_HISTORY_FILE, "r", encoding="utf-8") as f:
            all_history = json.load(f)
        return all_history.get(str(user_id), [])[-max_turns*2:]
    return []


def save_history(user_id: int, messages: list):
    """儲存對話記憶"""
    all_history = {}
    if os.path.exists(CHAT_HISTORY_FILE):
        with open(CHAT_HISTORY_FILE, "r", encoding="utf-8") as f:
            all_history = json.load(f)

    # 只保留最近 50 則
    all_history[str(user_id)] = messages[-50:]
    with open(CHAT_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(all_history, f, ensure_ascii=False, indent=2)


def clear_history(user_id: int) -> str:
    """清除對話記憶"""
    all_history = {}
    if os.path.exists(CHAT_HISTORY_FILE):
        with open(CHAT_HISTORY_FILE, "r", encoding="utf-8") as f:
            all_history = json.load(f)
    all_history[str(user_id)] = []
    with open(CHAT_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(all_history, f, ensure_ascii=False, indent=2)
    return "✅ 對話記憶已清除"
