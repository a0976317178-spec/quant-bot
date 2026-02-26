"""
news/tw_stock_news.py - 台股重要新聞彙整 + Telegram 推送

功能：
  1. 每日收盤後自動爬取台股重要新聞
  2. 用 Claude 分類、摘要、評分（哪些影響盤面）
  3. 推送整理好的新聞摘要到 Telegram

新聞來源（無需登入、可直接抓）：
  - 鉅亨網 ANUE API（最穩定）
  - 證交所公告
  - Yahoo 財經 RSS
"""
import json
import logging
import os
import hashlib
import requests
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

NEWS_CACHE_PATH = "data/news_cache.json"


def _ensure_dir():
    os.makedirs("data", exist_ok=True)


def _load_cache() -> dict:
    """載入已推送過的新聞 ID（避免重複）"""
    _ensure_dir()
    if not os.path.exists(NEWS_CACHE_PATH):
        return {}
    try:
        with open(NEWS_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache: dict):
    _ensure_dir()
    # 只保留近 3 天的記錄
    cutoff = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    cache = {k: v for k, v in cache.items() if v.get("date", "") >= cutoff}
    with open(NEWS_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _make_id(title: str) -> str:
    return hashlib.md5(title.encode()).hexdigest()[:12]


# ── 新聞來源 1：鉅亨網 ANUE ─────────────────────────────


def fetch_anue_news(limit: int = 30) -> list:
    """
    鉅亨網財經新聞 API（最穩定，無需登入）
    分類：tw（台灣股市）、stock（個股）、macro（總經）
    """
    news_list = []
    categories = [
        ("tw",    "台灣股市"),
        ("macro", "總體經濟"),
    ]

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://news.cnyes.com/",
    }

    for cat, cat_name in categories:
        try:
            url = (
                f"https://api.cnyes.com/media/api/v1/newslist/"
                f"category/tw_stock?limit={limit}&startAt="
                f"{int((datetime.now() - timedelta(days=1)).timestamp())}"
                f"&endAt={int(datetime.now().timestamp())}"
            )
            # 用更簡單的端點
            url2 = f"https://api.cnyes.com/media/api/v1/newslist/category/{cat}?limit={limit}"
            resp = requests.get(url2, headers=headers, timeout=10)
            data = resp.json()

            items = data.get("data", {}).get("items", [])
            for item in items:
                title = item.get("title", "")
                summary = item.get("summary", "")
                pub_ts  = item.get("publishAt", 0)
                pub_date = datetime.fromtimestamp(pub_ts).strftime("%Y-%m-%d %H:%M") if pub_ts else ""
                news_id  = _make_id(title)

                news_list.append({
                    "id":       news_id,
                    "title":    title,
                    "summary":  summary[:150] if summary else "",
                    "date":     pub_date,
                    "source":   f"鉅亨網/{cat_name}",
                    "category": cat,
                })
        except Exception as e:
            logger.warning(f"鉅亨網 {cat} 爬取失敗: {e}")

    return news_list


# ── 新聞來源 2：Yahoo 財經 RSS ──────────────────────────


def fetch_yahoo_rss_news() -> list:
    """Yahoo 財經台股 RSS"""
    news_list = []
    try:
        import xml.etree.ElementTree as ET
        url = "https://tw.stock.yahoo.com/rss?category=tw-market-news"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.encoding = "utf-8"
        root = ET.fromstring(resp.content)

        for item in root.findall(".//item")[:20]:
            title   = item.findtext("title", "").strip()
            desc    = item.findtext("description", "").strip()
            pub     = item.findtext("pubDate", "")

            if not title:
                continue

            # 簡單清理 HTML tags
            import re
            desc = re.sub(r"<[^>]+>", "", desc)[:150]

            news_list.append({
                "id":       _make_id(title),
                "title":    title,
                "summary":  desc,
                "date":     pub,
                "source":   "Yahoo財經",
                "category": "tw",
            })
    except Exception as e:
        logger.warning(f"Yahoo RSS 爬取失敗: {e}")
    return news_list


# ── 新聞來源 3：證交所重訊 ──────────────────────────────


def fetch_twse_announcements() -> list:
    """證交所重大訊息公告（當日）"""
    news_list = []
    try:
        today = datetime.now().strftime("%Y%m%d")
        url = "https://www.twse.com.tw/announcement/notice"
        params = {"response": "json", "date": today}
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        data = resp.json()

        rows = data.get("data", [])
        for row in rows[:15]:
            if len(row) < 4:
                continue
            title = f"【{row[0]}】{row[2]}"
            news_list.append({
                "id":       _make_id(title),
                "title":    title,
                "summary":  row[3] if len(row) > 3 else "",
                "date":     datetime.now().strftime("%Y-%m-%d"),
                "source":   "證交所公告",
                "category": "announcement",
            })
    except Exception as e:
        logger.warning(f"證交所公告爬取失敗: {e}")
    return news_list


# ── AI 新聞分析 ─────────────────────────────────────────


def analyze_news_with_claude(news_list: list, claude_client) -> str:
    """
    用 Claude 分析新聞，產出：
    - 最重要的 5~8 條新聞摘要
    - 對盤面的影響評估
    - 哪些族群/個股值得關注
    """
    if not news_list:
        return "今日無重要新聞資料"

    # 去重複並限制數量
    seen = set()
    unique_news = []
    for n in news_list:
        if n["id"] not in seen:
            seen.add(n["id"])
            unique_news.append(n)
        if len(unique_news) >= 50:
            break

    # 組成 prompt
    news_text = "\n".join([
        f"[{i+1}] ({n['source']}) {n['title']} — {n['summary']}"
        for i, n in enumerate(unique_news[:40])
    ])

    today = datetime.now().strftime("%Y年%m月%d日")
    prompt = f"""
今天是 {today}，以下是今日台股相關新聞：

{news_text}

請用繁體中文完成以下分析：

1. 📰【今日最重要 5~8 條新聞】
   格式：每條一行，用 • 開頭，包含「為何重要」的一句說明

2. 📈【對盤面的影響】
   - 今日整體市場氛圍：偏多 / 偏空 / 中性？
   - 哪些族群可能受到影響（例：AI/半導體/金融）？

3. 🔍【值得追蹤的個股或族群】
   - 列出 2~4 個受新聞影響的方向
   - 說明是利多還是利空

格式要簡潔，適合在手機 Telegram 閱讀。
"""

    try:
        from config import CLAUDE_SMART_MODEL
        resp = claude_client.messages.create(
            model=CLAUDE_SMART_MODEL,
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}]
        )
        return resp.content[0].text
    except Exception as e:
        logger.error(f"Claude 新聞分析失敗: {e}")
        # fallback：直接列出標題
        titles = "\n".join([f"• {n['title']}" for n in unique_news[:10]])
        return f"今日新聞（未分析）：\n{titles}"


# ── 主入口：每日新聞彙整 ───────────────────────────────


def run_daily_news_summary(claude_client, bot=None, user_ids: list = None) -> str:
    """
    每日新聞彙整主流程（由排程器呼叫）
    1. 爬取多來源新聞
    2. AI 分析重要性
    3. 推送到 Telegram（如果有提供 bot 和 user_ids）
    4. 回傳報告文字
    """
    today = datetime.now().strftime("%Y-%m-%d")
    cache = _load_cache()

    logger.info("開始爬取今日台股新聞...")

    # 爬取所有來源
    all_news = []
    all_news.extend(fetch_anue_news(limit=30))
    all_news.extend(fetch_yahoo_rss_news())
    all_news.extend(fetch_twse_announcements())

    # 過濾已推送過的新聞
    new_news = [n for n in all_news if n["id"] not in cache]

    if not new_news:
        msg = f"📰 {today} 新聞摘要\n今日無新增新聞（{len(all_news)} 條已在快取中）"
        return msg

    logger.info(f"取得 {len(new_news)} 條新新聞（共 {len(all_news)} 條）")

    # AI 分析
    analysis = analyze_news_with_claude(new_news, claude_client)

    # 組成推送訊息
    report = (
        f"📰 台股每日新聞彙整\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📅 {today}｜來源：{len(new_news)}條新聞\n\n"
        f"{analysis}\n\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🤖 量化師 AI 自動彙整"
    )

    # 更新快取
    for n in new_news:
        cache[n["id"]] = {"date": today, "title": n["title"]}
    _save_cache(cache)

    logger.info(f"新聞彙整完成，報告長度：{len(report)} 字")
    return report


# ── 快捷指令：立即取得新聞（/news 指令用）───────────────


async def cmd_news_handler(update, context, claude_client):
    """
    /news 指令 或 「新聞」文字訊息的處理器
    可直接在 main.py 的 cmd 列表中加入
    """
    await update.message.reply_text("📰 爬取今日台股新聞中，請稍候...")
    try:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            report = executor.submit(
                run_daily_news_summary, claude_client
            ).result(timeout=60)
        # Telegram 訊息長度限制 4096
        if len(report) > 4000:
            parts = [report[i:i+4000] for i in range(0, len(report), 4000)]
            for part in parts:
                await update.message.reply_text(part)
        else:
            await update.message.reply_text(report)
    except Exception as e:
        await update.message.reply_text(f"新聞取得失敗：{e}")
