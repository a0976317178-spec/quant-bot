"""
skill_loader.py - 技能載入與自學習引擎

功能：
  1. 自動掃描 quant-skills/ 資料夾，載入所有 SKILL.md
  2. 將技能知識注入 Claude 的 system prompt
  3. 記錄每個技能的使用次數與效果
  4. 每週評估哪些技能最有效，讓 AI 更重視它們
  5. 支援從 GitHub 下載新技能（用戶貼連結自動整合）

架構：
  quant-skills/
  ├── momentum/SKILL.md          ← 動能策略
  ├── chip_flow/SKILL.md         ← 籌碼流向
  ├── macro_timing/SKILL.md      ← 宏觀擇時
  ├── fundamental/SKILL.md       ← 基本面
  ├── risk_control/SKILL.md      ← 風險控管
  ├── pattern_recognition/SKILL.md ← K線型態
  └── [用戶自己新增的技能]/
"""
import os
import json
import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

SKILLS_DIR      = "quant-skills"
SKILL_LOG_FILE  = "data/skill_log.json"


# ══════════════════════════════════════════
# 技能讀取
# ══════════════════════════════════════════

def scan_skills() -> list[dict]:
    """
    掃描 quant-skills/ 資料夾，讀取所有 SKILL.md
    回傳技能列表
    """
    skills = []
    skills_path = Path(SKILLS_DIR)

    if not skills_path.exists():
        logger.warning(f"技能資料夾不存在：{SKILLS_DIR}")
        return []

    for folder in sorted(skills_path.iterdir()):
        skill_file = folder / "SKILL.md"
        if not skill_file.exists():
            continue
        try:
            content = skill_file.read_text(encoding="utf-8")
            meta    = _parse_frontmatter(content)
            body    = _strip_frontmatter(content)

            skills.append({
                "name":        meta.get("name", folder.name),
                "description": meta.get("description", ""),
                "version":     meta.get("version", "1.0"),
                "tags":        meta.get("tags", []),
                "body":        body,
                "path":        str(skill_file),
                "folder":      folder.name,
            })
            logger.info(f"✅ 載入技能：{meta.get('name', folder.name)}")
        except Exception as e:
            logger.error(f"讀取技能失敗 {folder.name}: {e}")

    return skills


def _parse_frontmatter(content: str) -> dict:
    """解析 YAML frontmatter"""
    meta = {}
    if not content.startswith("---"):
        return meta
    end = content.find("---", 3)
    if end == -1:
        return meta
    fm = content[3:end].strip()
    for line in fm.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            k = k.strip()
            v = v.strip()
            # 解析 list（tags: [a, b]）
            if v.startswith("[") and v.endswith("]"):
                v = [x.strip().strip("'\"") for x in v[1:-1].split(",")]
            meta[k] = v
    return meta


def _strip_frontmatter(content: str) -> str:
    """移除 frontmatter，只保留正文"""
    if not content.startswith("---"):
        return content
    end = content.find("---", 3)
    if end == -1:
        return content
    return content[end + 3:].strip()


# ══════════════════════════════════════════
# 技能注入 Claude Prompt
# ══════════════════════════════════════════

def build_skills_prompt(max_skills: int = 6) -> str:
    """
    建立技能系統 prompt，注入 Claude 的 system message
    根據技能使用效果排序，讓最有效的技能排最前面
    """
    skills   = scan_skills()
    if not skills:
        return ""

    # 根據歷史效果排序（越有效的排越前面）
    log      = _load_skill_log()
    scored   = []
    for s in skills:
        stats  = log.get(s["name"], {})
        score  = stats.get("effectiveness", 50)  # 預設 50 分
        scored.append((score, s))
    scored.sort(key=lambda x: x[0], reverse=True)

    lines = ["【量化師技能庫 — 你已掌握以下交易技能，分析時必須參考這些規則】\n"]
    for _, s in scored[:max_skills]:
        stats    = log.get(s["name"], {})
        eff      = stats.get("effectiveness", 50)
        uses     = stats.get("uses", 0)
        eff_str  = f"（效果評分：{eff}/100，已使用{uses}次）" if uses > 0 else ""
        lines.append(f"═══ 技能：{s['name']} {eff_str}")
        # 只注入技能主體（避免 prompt 太長，截斷到 1000 字）
        body_preview = s["body"][:1000] + "..." if len(s["body"]) > 1000 else s["body"]
        lines.append(body_preview)
        lines.append("")

    lines.append("【重要】分析股票時，請綜合運用以上所有技能的評分規則，給出有依據的建議。")
    return "\n".join(lines)


# ══════════════════════════════════════════
# 技能使用記錄與自學習
# ══════════════════════════════════════════

def _load_skill_log() -> dict:
    os.makedirs("data", exist_ok=True)
    if os.path.exists(SKILL_LOG_FILE):
        try:
            with open(SKILL_LOG_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_skill_log(log: dict):
    os.makedirs("data", exist_ok=True)
    with open(SKILL_LOG_FILE, "w") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def record_skill_use(skill_name: str, success: bool = True):
    """記錄技能使用結果，用於自學習排序"""
    log  = _load_skill_log()
    now  = datetime.now().isoformat()

    if skill_name not in log:
        log[skill_name] = {
            "uses": 0, "successes": 0, "failures": 0,
            "effectiveness": 50, "last_used": now
        }

    log[skill_name]["uses"]     += 1
    log[skill_name]["last_used"] = now

    if success:
        log[skill_name]["successes"] += 1
    else:
        log[skill_name]["failures"] += 1

    # 重新計算效果評分（成功率 × 100）
    total   = log[skill_name]["uses"]
    success_count = log[skill_name]["successes"]
    log[skill_name]["effectiveness"] = round(success_count / total * 100) if total > 0 else 50

    _save_skill_log(log)


def run_skill_self_learning(claude_client) -> str:
    """
    每週技能自學習：
    1. 分析哪些技能信號帶來了盈利
    2. 調整技能的效果評分
    3. 請 Claude 提出技能改進建議
    4. 自動更新技能效果排名
    """
    # 從模擬交易結果反推哪個技能最有效
    try:
        from database.db_manager import query_df
        closed = query_df("""
            SELECT stock_id, score, pnl_pct, signal_reason
            FROM paper_trades WHERE status='CLOSED'
            ORDER BY created_at DESC LIMIT 50
        """)
    except Exception:
        closed = None

    log    = _load_skill_log()
    skills = scan_skills()

    if not skills:
        return "⚠️ 技能庫為空，請確認 quant-skills/ 資料夾存在"

    skill_names = [s["name"] for s in skills]
    stats_lines = []
    for name in skill_names:
        s = log.get(name, {})
        stats_lines.append(
            f"  {name}：效果{s.get('effectiveness',50)}/100  "
            f"使用{s.get('uses',0)}次"
        )

    prompt = f"""
你是量化師Bot的技能自學習模組，正在進行週度技能評估。

【目前技能庫狀態】
{chr(10).join(stats_lines)}

{'【近期模擬交易結果摘要】' + chr(10) + closed.to_string() if closed is not None and not closed.empty else '（尚無模擬交易數據）'}

請完成以下任務（繁體中文）：

1. 📊【技能效果評估】
   - 哪些技能組合在最近的分析中最有幫助？
   - 有沒有發現技能規則需要調整的地方？

2. 🔧【技能改進建議】
   - 針對台股特性，現有技能有什麼不足？
   - 建議新增什麼技能或規則？

3. 💡【學習洞察】
   - 從模擬交易中學到了什麼規律？
   - 下週重點關注什麼指標？

請給出具體可執行的建議。
"""

    try:
        from config import CLAUDE_SMART_MODEL
        resp = claude_client.messages.create(
            model=CLAUDE_SMART_MODEL,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        ai_insight = resp.content[0].text
    except Exception as e:
        ai_insight = f"（AI 分析暫時失敗：{e}）"

    report = (
        f"🧠 *技能自學習週報*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📚 已載入技能：{len(skills)} 個\n\n"
        f"*技能效果排行：*\n"
    )

    sorted_skills = sorted(
        [(log.get(n, {}).get("effectiveness", 50), n) for n in skill_names],
        reverse=True
    )
    medals = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣"]
    for i, (eff, name) in enumerate(sorted_skills):
        m = medals[i] if i < len(medals) else f"{i+1}."
        report += f"{m} `{name}` — 效果 {eff}/100\n"

    report += f"\n━━━━━━━━━━━━━━━━━━\n{ai_insight}"
    return report


# ══════════════════════════════════════════
# 技能管理指令（Telegram 使用）
# ══════════════════════════════════════════

def list_skills() -> str:
    """列出所有已載入的技能"""
    skills = scan_skills()
    log    = _load_skill_log()

    if not skills:
        return (
            "📚 *技能庫*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "⚠️ 尚無技能\n"
            "請確認 `quant-skills/` 資料夾存在"
        )

    lines = [f"📚 *技能庫*  共 {len(skills)} 個\n"]
    for s in skills:
        stats = log.get(s["name"], {})
        eff   = stats.get("effectiveness", 50)
        uses  = stats.get("uses", 0)
        bar   = "█" * (eff // 20) + "░" * (5 - eff // 20)
        lines.append(
            f"🔧 *{s['name']}*\n"
            f"   {s['description'][:50]}...\n"
            f"   效果：{bar} {eff}/100  使用：{uses}次"
        )

    lines.append(
        "\n💡 每週日AI自動評估技能效果並排序\n"
        "💡 `新增技能 <名稱> <策略描述>` 可新增自定義技能"
    )
    return "\n".join(lines)


def add_custom_skill(name: str, description: str, content: str) -> str:
    """新增自定義技能"""
    folder = Path(SKILLS_DIR) / name.replace(" ", "_").lower()
    folder.mkdir(parents=True, exist_ok=True)

    skill_content = f"""---
name: {name}
description: {description}
version: 1.0
author: user
tags: [自定義]
---

# {name}

{content}
"""
    (folder / "SKILL.md").write_text(skill_content, encoding="utf-8")
    return (
        f"✅ *技能新增成功*\n"
        f"技能名稱：{name}\n"
        f"說明：{description}\n\n"
        f"下次 AI 分析時將自動套用此技能 🎯"
    )


def get_skill_detail(skill_name: str) -> str:
    """查看技能詳細內容"""
    skills = scan_skills()
    for s in skills:
        if s["name"].lower() == skill_name.lower():
            log   = _load_skill_log()
            stats = log.get(s["name"], {})
            return (
                f"🔧 *技能詳情：{s['name']}*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"版本：{s['version']}\n"
                f"效果評分：{stats.get('effectiveness', 50)}/100\n"
                f"使用次數：{stats.get('uses', 0)}\n"
                f"標籤：{', '.join(s['tags']) if s['tags'] else '無'}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"{s['body'][:800]}{'...' if len(s['body']) > 800 else ''}"
            )
    return f"❌ 找不到技能：{skill_name}\n\n輸入 `技能庫` 查看所有技能"
