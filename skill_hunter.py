"""
skill_hunter.py - AI 技能獵人

讓使用者在 Telegram 說「幫我學習下載影片的技能」，Bot 就會：
  1. 理解需求，生成搜尋關鍵字
  2. 搜尋 SkillsMP.com（27萬+技能庫）
  3. 從 GitHub 下載 SKILL.md
  4. 安全掃描（防止惡意指令）
  5. 存入 quant-skills/ 並通知用戶

支援：
  - SkillsMP API（需 API Key，有 AI 語意搜尋）
  - GitHub Search API fallback（無需 Key）
  - 用戶直接貼 GitHub URL 安裝
"""
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

SKILLS_DIR       = "quant-skills"
SKILL_LOG_FILE   = "data/skill_log.json"
SKILL_INDEX_FILE = "data/skill_index.json"   # 已安裝技能的索引

# ── 設定讀取 ─────────────────────────────────────────

def _get_skillsmp_key() -> str:
    """讀取 SkillsMP API Key（從 .env 或環境變數）"""
    from dotenv import load_dotenv
    load_dotenv()
    return os.environ.get("SKILLSMP_API_KEY", "")


def _load_index() -> dict:
    os.makedirs("data", exist_ok=True)
    if os.path.exists(SKILL_INDEX_FILE):
        try:
            with open(SKILL_INDEX_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_index(index: dict):
    os.makedirs("data", exist_ok=True)
    with open(SKILL_INDEX_FILE, "w") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════
# Step 1：搜尋技能
# ══════════════════════════════════════════

def search_skillsmp(query: str, limit: int = 5) -> list[dict]:
    """
    搜尋 SkillsMP.com
    優先用 AI 語意搜尋，fallback 到關鍵字搜尋
    """
    api_key = _get_skillsmp_key()
    results = []

    if api_key:
        # AI 語意搜尋（最準確）
        try:
            url = "https://skillsmp.com/api/v1/skills/ai-search"
            resp = requests.get(
                url,
                params={"q": query},
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("data", {}).get("data", [])[:limit]:
                    skill = item.get("skill", {})
                    if skill.get("githubUrl"):
                        results.append({
                            "name":        skill.get("name", "unknown"),
                            "description": skill.get("description", ""),
                            "github_url":  skill.get("githubUrl", ""),
                            "stars":       skill.get("stars", 0),
                            "score":       round(item.get("score", 0) * 100, 1),
                            "source":      "SkillsMP AI",
                        })
                logger.info(f"SkillsMP AI 搜尋 '{query}' 找到 {len(results)} 個技能")
        except Exception as e:
            logger.warning(f"SkillsMP AI 搜尋失敗: {e}")

        # 備用：關鍵字搜尋
        if not results:
            try:
                url2 = "https://skillsmp.com/api/v1/skills/search"
                resp2 = requests.get(
                    url2,
                    params={"q": query, "limit": limit, "sortBy": "stars"},
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=15,
                )
                if resp2.status_code == 200:
                    data2 = resp2.json()
                    for skill in data2.get("data", {}).get("skills", [])[:limit]:
                        if skill.get("githubUrl"):
                            results.append({
                                "name":        skill.get("name", "unknown"),
                                "description": skill.get("description", ""),
                                "github_url":  skill.get("githubUrl", ""),
                                "stars":       skill.get("stars", 0),
                                "score":       0,
                                "source":      "SkillsMP",
                            })
            except Exception as e:
                logger.warning(f"SkillsMP 關鍵字搜尋失敗: {e}")

    # 無 API Key 或結果為空 → GitHub Search fallback
    if not results:
        results = _search_github_fallback(query, limit)

    return results


def _search_github_fallback(query: str, limit: int = 5) -> list[dict]:
    """
    GitHub Search API fallback（無需 API Key，60次/小時）
    搜尋含有 SKILL.md 的 repository
    """
    try:
        url = "https://api.github.com/search/repositories"
        params = {
            "q": f"{query} SKILL.md in:path language:markdown",
            "sort": "stars",
            "per_page": limit,
        }
        gh_token = os.environ.get("GITHUB_TOKEN", "")
        headers  = {"Accept": "application/vnd.github.v3+json"}
        if gh_token:
            headers["Authorization"] = f"token {gh_token}"

        resp = requests.get(url, params=params, headers=headers, timeout=15)
        results = []
        if resp.status_code == 200:
            for repo in resp.json().get("items", [])[:limit]:
                results.append({
                    "name":        repo["name"],
                    "description": repo.get("description") or "",
                    "github_url":  repo["html_url"],
                    "stars":       repo.get("stargazers_count", 0),
                    "score":       0,
                    "source":      "GitHub",
                })
        logger.info(f"GitHub fallback 找到 {len(results)} 個")
        return results
    except Exception as e:
        logger.error(f"GitHub fallback 失敗: {e}")
        return []


# ══════════════════════════════════════════
# Step 2：下載 SKILL.md
# ══════════════════════════════════════════

def download_skill_md(github_url: str) -> str | None:
    """
    從 GitHub URL 下載 SKILL.md 內容
    支援格式：
    - https://github.com/user/repo
    - https://github.com/user/repo/tree/main/skills/skill-name
    """
    # 嘗試多個路徑組合
    candidates = _build_raw_urls(github_url)

    for raw_url in candidates:
        try:
            resp = requests.get(raw_url, timeout=10)
            if resp.status_code == 200 and len(resp.text) > 50:
                logger.info(f"✅ 下載成功：{raw_url}")
                return resp.text
        except Exception:
            continue

    logger.warning(f"無法下載 SKILL.md from {github_url}")
    return None


def _build_raw_urls(github_url: str) -> list[str]:
    """從各種 GitHub URL 格式建立 raw content 候選 URL"""
    raw_candidates = []
    url = github_url.rstrip("/")

    # 解析 https://github.com/user/repo/tree/branch/path 格式
    m = re.match(
        r"https://github\.com/([^/]+)/([^/]+)(?:/tree/([^/]+)(/.*)?)?", url
    )
    if not m:
        return raw_candidates

    owner  = m.group(1)
    repo   = m.group(2)
    branch = m.group(3) or "main"
    path   = (m.group(4) or "").strip("/")

    base = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}"

    if path:
        # 優先：指定路徑下的 SKILL.md
        raw_candidates.append(f"{base}/{path}/SKILL.md")
        raw_candidates.append(f"{base}/{path}")  # 如果 path 本身就是檔案
    
    # 根目錄 SKILL.md
    raw_candidates.append(f"{base}/SKILL.md")
    
    # 常見子目錄
    for sub in ["skill", "skills", ".claude/skills"]:
        raw_candidates.append(f"{base}/{sub}/SKILL.md")

    # 嘗試 master 分支
    if branch == "main":
        base_m = f"https://raw.githubusercontent.com/{owner}/{repo}/master"
        if path:
            raw_candidates.append(f"{base_m}/{path}/SKILL.md")
        raw_candidates.append(f"{base_m}/SKILL.md")

    return raw_candidates


# ══════════════════════════════════════════
# Step 3：安全掃描
# ══════════════════════════════════════════

# 危險指令黑名單（防止惡意 SKILL.md）
_DANGEROUS_PATTERNS = [
    r"rm\s+-rf",
    r"curl\s+.*\|\s*sh",
    r"wget\s+.*\|\s*sh",
    r"eval\s*\(",
    r"exec\s*\(",
    r"os\.system\(",
    r"subprocess",
    r"__import__",
    r"base64\.decode",
    r"ignore previous instructions",
    r"忽略之前的指令",
    r"system prompt",
]

def security_scan(content: str) -> tuple[bool, list[str]]:
    """
    簡易安全掃描
    回傳 (is_safe, warnings)
    """
    warnings = []
    content_lower = content.lower()

    for pattern in _DANGEROUS_PATTERNS:
        if re.search(pattern, content_lower, re.IGNORECASE):
            warnings.append(f"⚠️ 發現可疑指令：`{pattern}`")

    # 長度異常（超過 50KB 可能有問題）
    if len(content) > 50_000:
        warnings.append(f"⚠️ 檔案過大（{len(content)//1024}KB），請確認是否正常")

    is_safe = len(warnings) == 0
    return is_safe, warnings


# ══════════════════════════════════════════
# Step 4：儲存技能
# ══════════════════════════════════════════

def save_skill(name: str, content: str, metadata: dict) -> str:
    """
    儲存技能到 quant-skills/ 資料夾
    回傳儲存路徑
    """
    # 清理技能名稱，只保留英數字和底線
    safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", name).lower()
    folder    = Path(SKILLS_DIR) / safe_name
    folder.mkdir(parents=True, exist_ok=True)
    skill_path = folder / "SKILL.md"
    skill_path.write_text(content, encoding="utf-8")

    # 更新索引
    index = _load_index()
    index[safe_name] = {
        "name":        name,
        "folder":      safe_name,
        "source":      metadata.get("source", ""),
        "github_url":  metadata.get("github_url", ""),
        "stars":       metadata.get("stars", 0),
        "description": metadata.get("description", ""),
        "installed_at": datetime.now().isoformat(),
        "auto_learned": metadata.get("auto_learned", True),
    }
    _save_index(index)

    logger.info(f"技能已儲存：{skill_path}")
    return str(skill_path)


# ══════════════════════════════════════════
# 主流程：對話驅動的技能學習
# ══════════════════════════════════════════

def learn_skill_from_request(user_request: str, claude_client=None) -> str:
    """
    主入口：接收用戶自然語言需求，自動完成搜尋→下載→安裝→回報
    
    範例：
    user_request = "幫我學習下載 YouTube 影片的技能"
    → 搜尋、下載、安裝，回傳結果訊息
    """
    # Step 0：用 Claude 理解需求、生成搜尋關鍵字
    search_query = _extract_search_query(user_request, claude_client)
    
    msg_lines = [
        f"🔍 *技能學習啟動*",
        f"━━━━━━━━━━━━━━━━━━",
        f"需求：{user_request}",
        f"搜尋關鍵字：`{search_query}`",
        f"",
    ]

    # Step 1：搜尋
    results = search_skillsmp(search_query, limit=5)
    if not results:
        return (
            f"🔍 技能搜尋：`{search_query}`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"❌ 找不到相關技能\n\n"
            f"💡 建議：\n"
            f"• 試試其他關鍵字（英文效果更好）\n"
            f"• 貼上 GitHub URL 直接安裝：`安裝技能 https://github.com/...`\n"
            f"• 自己新增：`新增技能 名稱 說明內容`"
        )

    msg_lines.append(f"📦 找到 {len(results)} 個候選技能，開始安裝最佳匹配...\n")

    # Step 2：嘗試安裝第一個可用的
    installed = []
    failed    = []

    for result in results[:3]:  # 最多嘗試前3個
        name       = result["name"]
        github_url = result["github_url"]
        desc       = result["description"][:60] if result["description"] else ""
        stars      = result["stars"]

        # 下載
        content = download_skill_md(github_url)
        if not content:
            failed.append(name)
            continue

        # 安全掃描
        is_safe, warnings = security_scan(content)
        if not is_safe:
            msg_lines.append(
                f"🚫 `{name}` 安全掃描未通過，已跳過\n"
                + "\n".join(warnings)
            )
            failed.append(name)
            continue

        # 儲存
        save_skill(name, content, {
            **result,
            "auto_learned": True,
            "learn_request": user_request,
        })

        installed.append({
            "name":  name,
            "desc":  desc,
            "stars": stars,
            "url":   github_url,
        })
        break  # 找到第一個成功的就停止

    if not installed:
        return (
            "\n".join(msg_lines) +
            f"\n❌ 所有候選技能均安裝失敗（可能是格式不相容）\n\n"
            f"💡 請嘗試直接貼 GitHub URL：\n`安裝技能 https://github.com/user/repo`"
        )

    # 組成成功報告
    s = installed[0]
    api_key = _get_skillsmp_key()
    key_hint = "" if api_key else "\n💡 設定 SKILLSMP_API_KEY 可使用 AI 語意搜尋，更準確！"

    return (
        f"🧠 *AI 技能學習成功！*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"✅ 已安裝技能：*{s['name']}*\n"
        f"📝 說明：{s['desc']}\n"
        f"⭐ GitHub Stars：{s['stars']}\n"
        f"🔗 來源：{s['url']}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💡 下次你說「{user_request[:20]}...」\n"
        f"   AI 就會使用這個技能協助你！\n"
        f"📚 輸入 `技能庫` 查看所有已學技能{key_hint}"
    )


def install_skill_from_url(github_url: str) -> str:
    """直接從 GitHub URL 安裝技能"""
    if "github.com" not in github_url:
        return "❌ 請提供有效的 GitHub URL，例如：\n`https://github.com/user/repo`"

    content = download_skill_md(github_url)
    if not content:
        return (
            f"❌ 無法從以下 URL 下載 SKILL.md：\n`{github_url}`\n\n"
            f"請確認：\n"
            f"• 這個 repo 包含 SKILL.md 檔案\n"
            f"• URL 格式正確\n"
            f"• repo 為公開（public）"
        )

    is_safe, warnings = security_scan(content)
    if not is_safe:
        warn_text = "\n".join(warnings)
        return (
            f"🚫 *安全掃描未通過，安裝中止*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{warn_text}\n\n"
            f"如果你確認此技能安全，請告訴我你信任此來源。"
        )

    # 從 URL 提取技能名稱
    parts = github_url.rstrip("/").split("/")
    name  = parts[-1] if parts else "custom_skill"

    path = save_skill(name, content, {
        "github_url":   github_url,
        "source":       "GitHub Direct",
        "auto_learned": False,
    })

    # 取 SKILL.md 第一行描述
    first_line = ""
    for line in content.splitlines():
        if line.startswith("description:"):
            first_line = line.replace("description:", "").strip()
            break
    if not first_line:
        for line in content.splitlines():
            if line.startswith("# "):
                first_line = line.lstrip("# ")
                break

    return (
        f"✅ *技能安裝成功！*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"技能名稱：*{name}*\n"
        f"說明：{first_line[:80]}\n"
        f"來源：{github_url}\n\n"
        f"AI 下次分析時將自動套用此技能 🎯"
    )


def _extract_search_query(user_request: str, claude_client=None) -> str:
    """
    用 Claude 從自然語言需求提取英文搜尋關鍵字
    如果沒有 claude_client，用簡單規則處理
    """
    if claude_client:
        try:
            from config import CLAUDE_FAST_MODEL
            resp = claude_client.messages.create(
                model=CLAUDE_FAST_MODEL,
                max_tokens=50,
                messages=[{
                    "role": "user",
                    "content": (
                        f"從以下需求提取1~3個英文搜尋關鍵字（用於搜尋 GitHub 技能庫），"
                        f"只回傳關鍵字，不要解釋：\n{user_request}"
                    )
                }]
            )
            return resp.content[0].text.strip()[:80]
        except Exception:
            pass

    # 簡單規則 fallback：直譯常見詞彙
    keyword_map = {
        "下載影片": "video download yt-dlp",
        "下載視頻": "video download yt-dlp",
        "youtube": "youtube download",
        "截圖": "screenshot",
        "爬蟲": "web scraping",
        "爬取": "web scraping",
        "郵件": "email",
        "翻譯": "translation",
        "pdf": "pdf",
        "圖片": "image",
        "股票": "stock trading",
        "資料庫": "database",
        "API": "api",
    }
    req_lower = user_request.lower()
    for zh, en in keyword_map.items():
        if zh.lower() in req_lower:
            return en

    # 直接用原始需求（移除中文）
    return re.sub(r"[\u4e00-\u9fff]", " ", user_request).strip() or user_request


# ══════════════════════════════════════════
# 技能管理查詢
# ══════════════════════════════════════════

def list_all_skills() -> str:
    """列出所有已安裝的技能（含來源）"""
    # 來自 quant-skills/ 資料夾
    skills_path = Path(SKILLS_DIR)
    folders     = sorted(skills_path.iterdir()) if skills_path.exists() else []
    index       = _load_index()

    if not folders:
        return (
            "📚 *技能庫*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "目前尚無安裝任何技能\n\n"
            "💡 說：`幫我學習 [需求]` 讓 AI 自動搜尋安裝\n"
            "💡 或：`安裝技能 https://github.com/...`"
        )

    built_in = ["momentum", "chip_flow", "macro_timing",
                "fundamental", "risk_control", "pattern_recognition"]

    lines = [f"📚 *已安裝技能庫*  共 {len(folders)} 個\n"]

    # 內建台股技能
    lines.append("🏠 *內建台股技能：*")
    for f in folders:
        if f.name in built_in and (f / "SKILL.md").exists():
            info  = index.get(f.name, {})
            lines.append(f"  ✅ `{f.name}` — {info.get('description','台股分析技能')[:40]}")

    # AI 自動學習的技能
    auto = [f for f in folders if f.name not in built_in and
            index.get(f.name, {}).get("auto_learned", False) and
            (f / "SKILL.md").exists()]
    if auto:
        lines.append(f"\n🤖 *AI 自動學習技能（{len(auto)} 個）：*")
        for f in auto:
            info  = index.get(f.name, {})
            stars = info.get("stars", 0)
            src   = info.get("source", "")
            installed = info.get("installed_at", "")[:10]
            lines.append(
                f"  🆕 `{f.name}`\n"
                f"     {info.get('description','')[:45]}\n"
                f"     ⭐{stars}  來源：{src}  安裝日：{installed}"
            )

    # 手動安裝
    manual = [f for f in folders if f.name not in built_in and
              not index.get(f.name, {}).get("auto_learned", False) and
              (f / "SKILL.md").exists()]
    if manual:
        lines.append(f"\n🔧 *手動安裝技能（{len(manual)} 個）：*")
        for f in manual:
            info = index.get(f.name, {})
            lines.append(f"  📌 `{f.name}` — {info.get('description','')[:45]}")

    api_key = _get_skillsmp_key()
    key_status = "✅ 已設定" if api_key else "❌ 未設定（AI語意搜尋需要）"
    lines.append(f"\n⚙️ SkillsMP API Key：{key_status}")
    lines.append("💡 說 `幫我學習 [需求]` 可自動搜尋安裝新技能")
    return "\n".join(lines)


def uninstall_skill(skill_name: str) -> str:
    """移除一個已安裝的技能"""
    import shutil
    safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", skill_name).lower()
    built_in  = ["momentum", "chip_flow", "macro_timing",
                 "fundamental", "risk_control", "pattern_recognition"]

    if safe_name in built_in:
        return f"❌ `{skill_name}` 是內建台股技能，不能移除"

    folder = Path(SKILLS_DIR) / safe_name
    if not folder.exists():
        return f"❌ 找不到技能：`{skill_name}`"

    shutil.rmtree(folder)
    index = _load_index()
    index.pop(safe_name, None)
    _save_index(index)
    return f"✅ 已移除技能：`{skill_name}`"


def set_skillsmp_key(api_key: str) -> str:
    """設定 SkillsMP API Key"""
    env_file = ".env"
    lines    = []
    found    = False

    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                if line.startswith("SKILLSMP_API_KEY="):
                    lines.append(f"SKILLSMP_API_KEY={api_key}\n")
                    found = True
                else:
                    lines.append(line)

    if not found:
        lines.append(f"SKILLSMP_API_KEY={api_key}\n")

    with open(env_file, "w") as f:
        f.writelines(lines)

    return (
        f"✅ SkillsMP API Key 已設定！\n"
        f"重啟 Bot 後生效：`pm2 restart 2`\n\n"
        f"設定後可享用：\n"
        f"• AI 語意搜尋（27萬+技能庫）\n"
        f"• 更精準的技能匹配\n"
        f"• 每天 500 次搜尋額度"
    )
