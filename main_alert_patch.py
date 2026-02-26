# main_alert_patch.py
# ============================================================
# 這不是完整檔案，只列出需要在 main.py 新增的地方
# ============================================================

# ① 頂部 import 新增（加在其他 from 後面）
from alert.daily_alert import run_daily_scan

# ② 新增 /scan 指令（加在 cmd_news 後面）
async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """立即執行全市場選股掃描"""
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text("🔍 全市場掃描中，約需 30~60 秒...")
    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            report = executor.submit(run_daily_scan, "close").result(timeout=120)
        for i in range(0, len(report), 4000):
            await update.message.reply_text(report[i:i+4000])
    except Exception as e:
        await update.message.reply_text(f"掃描失敗：{e}")

# ③ handle_text() 的 routes 字典新增（加在「選股」那行旁邊）
#        ("掃描全市場", "全市場", "scan"): cmd_scan,

# ④ main() 的 cmds 列表新增
#        ("scan", cmd_scan),

# ⑤ run_scheduler() 裡新增兩個排程（加在現有排程後面）
#
#    schedule.every().day.at("08:30").do(
#        lambda: threading.Thread(
#            target=run_open_alert,
#            args=(bot_token, user_ids),
#            daemon=True
#        ).start()
#    )
#    schedule.every().day.at("15:30").do(
#        lambda: threading.Thread(
#            target=run_close_alert,
#            args=(bot_token, user_ids),
#            daemon=True
#        ).start()
#    )
#
#    # 記得在 run_scheduler() 頂部加 import
#    from alert.daily_alert import run_open_alert, run_close_alert
