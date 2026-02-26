"""
main.py 修改說明
==================================================
以下是需要在現有 main.py 中修改/新增的地方
（不是完整檔案，只列出差異）

① 頂部 import 新增（加在現有 import 後面）
② 新增 /news 指令處理器
③ 排程器新增兩個任務（每日新聞 + 每日自學習）
④ cmd 列表新增 news 和 selflearn
"""

# ──────────────────────────────────────────────────────────
# ① 在 main.py 頂部 import 區塊，新增以下兩行
# ──────────────────────────────────────────────────────────
# from news.tw_stock_news import run_daily_news_summary, cmd_news_handler
# from memory.ai_self_learning import run_daily_self_learning, get_self_learning_summary


# ──────────────────────────────────────────────────────────
# ② 新增 /news 指令處理器（加在 cmd_score 函式後面）
# ──────────────────────────────────────────────────────────
async def cmd_news(update, context):
    """立即取得台股新聞摘要"""
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text("📰 爬取今日台股新聞中，請稍候 30 秒...")
    try:
        import concurrent.futures
        from news.tw_stock_news import run_daily_news_summary
        with concurrent.futures.ThreadPoolExecutor() as executor:
            report = executor.submit(
                run_daily_news_summary, claude_client
            ).result(timeout=90)
        # 超過 4000 字分段發送
        for i in range(0, len(report), 4000):
            await update.message.reply_text(report[i:i+4000])
    except Exception as e:
        await update.message.reply_text(f"新聞取得失敗：{e}")


# ──────────────────────────────────────────────────────────
# ③ run_scheduler() 函式裡，在最後的 schedule 設定區塊新增：
# ──────────────────────────────────────────────────────────
#
# 在 schedule.every().sunday... 那行後面加：
#
#   async def do_daily_news():
#       from telegram import Bot
#       from news.tw_stock_news import run_daily_news_summary
#       bot = Bot(token=bot_token)
#       report = run_daily_news_summary(claude_client)
#       for uid in user_ids:
#           try:
#               for i in range(0, len(report), 4000):
#                   await bot.send_message(chat_id=uid, text=report[i:i+4000])
#           except: pass
#
#   async def do_self_learning():
#       from telegram import Bot
#       from memory.ai_self_learning import run_daily_self_learning
#       bot = Bot(token=bot_token)
#       report = run_daily_self_learning(claude_client)
#       for uid in user_ids:
#           try:
#               await bot.send_message(chat_id=uid, text=report[:4000])
#           except: pass
#
#   schedule.every().day.at("16:00").do(lambda: asyncio.run(do_daily_news()))
#   schedule.every().day.at("21:00").do(lambda: asyncio.run(do_self_learning()))
#
# 把 logger.info 那行改為：
#   logger.info("排程器啟動：15:10更新資料 | 15:35推報告 | 16:00新聞 | 21:00自學習 | 每15分鐘監控 | 週日策略")


# ──────────────────────────────────────────────────────────
# ④ main() 函式裡，cmds 列表新增：
# ──────────────────────────────────────────────────────────
# ("news", cmd_news),
#
# handle_text() 裡的 routes 字典新增：
# ("新聞", "今日新聞", "news"): cmd_news,
