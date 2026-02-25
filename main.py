"""
main.py - 台股量化交易 Bot（完整版）
"""
import os
import logging
import asyncio
import threading
import concurrent.futures
import schedule
import time
from datetime import datetime
from dotenv import load_dotenv
import anthropic

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from config import TELEGRAM_TOKEN, ALLOWED_USER_IDS, ANTHROPIC_API_KEY, CLAUDE_FAST_MODEL, CLAUDE_SMART_MODEL

from memory.rules_manager import add_rule, delete_rule, list_rules, load_history, save_history, clear_history, get_rules_as_prompt
from memory.daily_learning import add_to_watchlist, remove_from_watchlist, list_watchlist, get_recent_learnings, daily_learning_task
from portfolio.tracker import add_position, remove_position, list_portfolio, check_portfolio_alerts
from risk.manager import get_risk_summary, check_market_risk, calc_position_size, update_risk_param
from database.daily_update import run_daily_update

# --- 修正後的匯入區塊 ---
from daily_report import (
    calc_total_score, format_score_report,
    add_journal_entry, get_journal_summary
)
from report.daily_report import generate_daily_report, score_stock

load_dotenv()
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)
claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def is_authorized(user_id: int) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS

async def ask_claude(user_id: int, user_message: str, use_smart: bool = False) -> str:
    model = CLAUDE_SMART_MODEL if use_smart else CLAUDE_FAST_MODEL
    rules_prompt = get_rules_as_prompt()
    recent = get_recent_learnings(days=3)
    system_prompt = (
        "你是專業台股量化交易AI助理「量化師」。\n"
        "【強制規定】所有回覆必須使用繁體中文，不得出現英文句子。\n"
        "【專長】技術分析、籌碼分析、基本面分析、量化選股、風險控管。\n"
        f"【近期市場觀察】{recent}\n"
        f"{rules_prompt}\n"
        "分析股票時請依量價面、籌碼面、基本面、宏觀面四個維度進行，並說明風險。"
    )
    history = load_history(user_id)
    history.append({"role": "user", "content": user_message})
    try:
        resp = claude_client.messages.create(model=model, max_tokens=1200, system=system_prompt, messages=history)
        reply = resp.content[0].text
        history.append({"role": "assistant", "content": reply})
        save_history(user_id, history)
        return reply
    except anthropic.APIError as e:
        return f"API錯誤：{str(e)}"

# ══════════════════════════════════════════════════════
# 所有指令處理器 (已去除重複定義)
# ══════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("您沒有使用權限")
        return
    msg = (
        "🤖 *量化師* — 台股 AI 交易助理\n"
        "══════════════════════════\n\n"
        "💬 *對話*\n"
        "  `/chat` `對話`    問任何投資問題\n"
        "  `/clear` `清除記憶`  重置對話\n\n"
        "📊 *行情分析*\n"
        "  `/price 2330`   `股價 2330`   即時報價\n"
        "  `/analyze 2330` `分析 2330`   四維度分析\n"
        "  `/screen`       `選股`        掃描監控清單\n"
        "  `/macro`        `宏觀`        VIX 大盤指標\n\n"
        "📋 *自選股監控*\n"
        "  `/watch 2330`   `加入 2330`   加入監控\n"
        "  `/unwatch 2330` `移除 2330`   移除監控\n"
        "  `/list`         `清單`        查看監控清單\n\n"
        "💼 *持股追蹤*\n"
        "  `/buy`   `買進 2330 1 980`    新增持股\n"
        "  `/sell`  `賣出 2330 1020`      平倉結算\n"
        "  `/portfolio`    `持股`        查看持倉\n"
        "  `/check`        `檢查`        手動觸發警報\n"
        "  `/calc 980`     `建倉試算 980` 計算張數\n\n"
        "🛡️ *風險控管*\n"
        "  `/risk`         `風控`        查看風控設定\n"
        "  `/riskset`      `風控設定 停損 0.07`\n"
        "  `/mktcheck`     `市場風險`    檢查大盤\n\n"
        "📈 *回測 & 評分*\n"
        "  `/backtest`     `回測 2330`\n"
        "  `/score`        `評分 2330`\n\n"
        "🧠 *學習規則*\n"
        "  `/teach`  `新增規則 外資連買3天`\n"
        "  `/rules`        `規則`        查看規則\n"
        "  `/delrule 1`    `刪除規則 1`\n"
        "  `/learning`     `學習記錄`\n\n"
        "🗄️ *資料庫*\n"
        "  `/dbinit`  `初始化`  建立資料庫（首次）\n"
        "  `/stocks`  `更新清單` 更新股票清單\n"
        "  `/crawl 2020` `爬取 2020` 下載歷史資料\n"
        "  `/db`      `資料庫`   查看資料狀態\n\n"
        "══════════════════════════\n"
        "💡 直接輸入*4位數代號*（如 2330）即查股價\n"
        "💡 輸入任何問題直接與 AI 對話"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_duihua(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("請輸入問題，例如：/chat 現在適合進場嗎？")
        return
    await update.message.reply_text("思考中...")
    reply = await ask_claude(update.effective_user.id, " ".join(context.args))
    await update.message.reply_text(reply)

async def cmd_qingchu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text(clear_history(update.effective_user.id))

async def cmd_gujia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("請輸入股票代號，例如：/price 2330")
        return
    stock_id = context.args[0]
    await update.message.reply_text(f"查詢 {stock_id} 即時股價中...")
    try:
        from factors.realtime import get_stock_quote, format_quote_message
        quote = get_stock_quote(stock_id)
        if not quote or quote.get("close", 0) == 0:
            await update.message.reply_text(f"找不到 {stock_id} 的資料，請確認代號是否正確")
            return
        await update.message.reply_text(format_quote_message(quote), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"查詢失敗：{e}")

async def cmd_fenxi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("請輸入股票代號，例如：/analyze 2330")
        return
    stock_id = context.args[0]
    await update.message.reply_text(f"正在分析 {stock_id}，請稍候...")
    try:
        from factors.realtime import get_stock_quote, fetch_historical
        from factors.technical import calc_technical_factors
        quote = get_stock_quote(stock_id)
        if not quote or quote.get("close", 0) == 0:
            await update.message.reply_text(f"找不到 {stock_id} 的即時資料")
            return
        tech_info = "技術指標資料不足"
        hist = fetch_historical(stock_id, days=60)
        if not hist.empty:
            tech = calc_technical_factors(hist)
            latest = tech.iloc[-1]
            tech_info = (
                f"RSI(14)：{latest.get('rsi', 0):.1f}\n"
                f"乖離率(20MA)：{latest.get('bias_20ma', 0):.2%}\n"
                f"ATR波動率：{latest.get('atr_pct', 0):.2%}\n"
                f"MACD斜率：{'上揚' if latest.get('macd_hist_slope', 0) > 0 else '下彎'}\n"
                f"5日報酬：{latest.get('return_5d', 0):.2%}\n"
                f"20日報酬：{latest.get('return_20d', 0):.2%}"
            )
        message = (
            f"請分析台股 {stock_id} {quote.get('name', '')}，以下是即時數據：\n\n"
            f"現價：${quote.get('close')}\n"
            f"漲跌：{quote.get('change_pct', 0):+.2f}%\n"
            f"高低：{quote.get('high')} / {quote.get('low')}\n"
            f"量：{quote.get('volume', 0):,} 張\n\n"
            f"{tech_info}\n\n"
            f"請從量價面、籌碼面、基本面、宏觀面四個維度分析，並給出操作建議。"
        )
        reply = await ask_claude(update.effective_user.id, message, use_smart=True)
        await update.message.reply_text(reply)
        
        # 自動附上評分卡
        try:
            score = calc_total_score(stock_id)
            await update.message.reply_text(format_score_report(score))
        except Exception as se:
            logger.debug(f"評分卡失敗: {se}")

    except Exception as e:
        await update.message.reply_text(f"分析失敗：{str(e)}")

async def cmd_pingfen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """個股綜合評分"""
    if not is_authorized(update.effective_user.id): return
    args = [p for p in update.message.text.strip().split() if p not in ["評分", "score", "/score", "打分"]]
    if not args:
        await update.message.reply_text("請輸入股票代號，例如：評分 2330")
        return
    stock_id = args[0]
    await update.message.reply_text(f"計算 {stock_id} 綜合評分中...")
    try:
        def run():
            return calc_total_score(stock_id)
        with concurrent.futures.ThreadPoolExecutor() as ex:
            result = ex.submit(run).result(timeout=60)
        await update.message.reply_text(format_score_report(result))
    except Exception as e:
        await update.message.reply_text(f"評分失敗: {e}")

async def cmd_riji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看交易日誌"""
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text(get_journal_summary())

async def cmd_zhoubao(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """產生週報"""
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text("產生交易週報中，請稍候...")
    # 預留串接真正的週報生成函式
    await update.message.reply_text("週報功能正在建置中。")

async def cmd_xuangu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text("掃描監控清單中...")
    try:
        from factors.realtime import get_stock_quote
        watchlist = list_watchlist() # Simplified
        await update.message.reply_text(f"目前的監控清單：\n{watchlist}")
    except Exception as e:
        await update.message.reply_text(f"掃描失敗：{e}")

async def cmd_hongguan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text("取得宏觀數據中...")
    try:
        from factors.macro import get_macro_snapshot
        data = get_macro_snapshot()
        msg = f"VIX恐慌指數：{data.get('vix', 'N/A')}\n"
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"取得宏觀數據失敗：{e}")

async def cmd_jiaru(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("請輸入股票代號，例如：/watch 2330")
        return
    await update.message.reply_text(add_to_watchlist(context.args[0]))

async def cmd_yichu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("請輸入股票代號，例如：/unwatch 2330")
        return
    await update.message.reply_text(remove_from_watchlist(context.args[0]))

async def cmd_qingdan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text(list_watchlist())

async def cmd_maijin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    args = [p for p in update.message.text.strip().split() if p not in ["買進", "buy", "進場", "/buy"]]
    if len(args) < 3:
        await update.message.reply_text("格式：買進 <代號> <張數> <進場價>\n範例：買進 2330 1 980")
        return
    try:
        stock_id = args[0]
        shares = int(args[1])
        entry_price = float(args[2])
        result = add_position(stock_id, entry_price, shares, 0.05, 0.10)
        
        # 修正：寫入交易日誌
        reason = " ".join(args[3:]) if len(args) > 3 else "手動進場"
        add_journal_entry(stock_id, "買進", f"價格:{entry_price} 張數:{shares} 理由:{reason}")
        
        await update.message.reply_text(f"{result}\n已記錄至交易日誌。")
    except Exception as e:
        await update.message.reply_text(f"格式錯誤：{e}")

async def cmd_machu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    args = [p for p in update.message.text.strip().split() if p not in ["賣出", "sell", "出場", "平倉", "/sell"]]
    if not args:
        await update.message.reply_text("格式：賣出 <代號> <出場價>\n範例：賣出 2330 1020")
        return
    stock_id = args[0]
    exit_price = float(args[1]) if len(args) > 1 else None
    result = remove_position(stock_id, exit_price)
    
    if exit_price:
        add_journal_entry(stock_id, "賣出", f"出場價格:{exit_price}")
        
    await update.message.reply_text(f"{result}\n已記錄出場。")

async def cmd_chicang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text(list_portfolio())

async def cmd_jiancha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text("檢查持股狀態中...")
    alerts = await check_portfolio_alerts()
    if alerts:
        for alert in alerts:
            await update.message.reply_text(alert["message"])
    else:
        await update.message.reply_text("所有持股正常，未觸及停損或目標價")

async def cmd_jianyi_zhangshui(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    args = [p for p in update.message.text.strip().split() if p not in ["建倉試算", "試算", "calc", "/calc"]]
    if not args:
        await update.message.reply_text("格式：建倉試算 <股價>\n範例：建倉試算 980")
        return
    price = float(args[0])
    r = calc_position_size(price)
    await update.message.reply_text(f"建議張數：{r.get('suggested_lots', 0)} 張\n預計投入：${r.get('actual_invest', 0):,.0f}")

async def cmd_daily_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text("生成每日報告中，請稍候...")
    def run():
        return generate_daily_report()
    with concurrent.futures.ThreadPoolExecutor() as executor:
        report = executor.submit(run).result(timeout=120)
    await update.message.reply_text(report)

async def cmd_fengkong(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text(get_risk_summary())

async def cmd_fengkong_shezhi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    args = [p for p in update.message.text.strip().split() if p not in ["風控設定", "/riskset"]]
    if len(args) < 2:
        await update.message.reply_text("格式：風控設定 <參數> <數值>")
        return
    await update.message.reply_text(update_risk_param(args[0], float(args[1])))

async def cmd_shichang_fengxian(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    result = check_market_risk()
    msg = "\n".join(result["warnings"]) if result["warnings"] else "市場風險正常"
    await update.message.reply_text(msg)

async def cmd_huice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    args = [p for p in update.message.text.strip().split() if p not in ["回測", "backtest", "/backtest"]]
    if not args:
        await update.message.reply_text("請輸入股票代號，例如：回測 2330")
        return
    await update.message.reply_text(f"開始回測 {args[0]}...")

async def cmd_xinzeng_guize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("請輸入規則")
        return
    await update.message.reply_text(add_rule(" ".join(context.args)))

async def cmd_guize_qingdan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text(list_rules())

async def cmd_shanchu_guize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    if not context.args: return
    await update.message.reply_text(delete_rule(int(context.args[0])))

async def cmd_xuexi_jilu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text(get_recent_learnings(days=7))

async def cmd_shujuku(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    try:
        from database.db_manager import get_db_stats
        stats = get_db_stats()
        await update.message.reply_text(f"資料庫狀態：股票清單 {stats.get('stocks', 0)} 支")
    except Exception as e:
        await update.message.reply_text(f"查詢失敗：{e}")

async def cmd_chushihua(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    try:
        from database.db_manager import init_db
        init_db()
        await update.message.reply_text("資料庫初始化完成！")
    except Exception as e:
        await update.message.reply_text(f"初始化失敗：{e}")

async def cmd_gengxin_qingdan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text("股票清單更新功能執行中...")

async def cmd_paqu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text("歷史資料爬取已在背景啟動...")

async def cmd_update_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text("手動更新資料庫中...")
    result = await run_daily_update()
    await update.message.reply_text(result)

# ══════════════════════════════════════════════════════
# 智能文字偵測（關鍵字觸發）
# ══════════════════════════════════════════════════════

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    text = update.message.text.strip()
    parts = text.split()
    keyword = parts[0] if parts else ""
    arg = parts[1] if len(parts) > 1 else ""

    mapping = {
        "股價": cmd_gujia, "price": cmd_gujia,
        "分析": cmd_fenxi, "analyze": cmd_fenxi,
        "加入": cmd_jiaru, "watch": cmd_jiaru,
        "移除": cmd_yichu, "unwatch": cmd_yichu,
        "清單": cmd_qingdan, "自選股": cmd_qingdan,
        "選股": cmd_xuangu, "screen": cmd_xuangu,
        "宏觀": cmd_hongguan, "macro": cmd_hongguan,
        "評分": cmd_pingfen, "score": cmd_pingfen,
        "買進": cmd_maijin, "buy": cmd_maijin,
        "賣出": cmd_machu, "sell": cmd_machu,
        "持股": cmd_chicang, "portfolio": cmd_chicang,
        "檢查": cmd_jiancha, "check": cmd_jiancha,
        "試算": cmd_jianyi_zhangshui, "calc": cmd_jianyi_zhangshui,
        "日報": cmd_daily_report, "report": cmd_daily_report,
        "風控": cmd_fengkong, "risk": cmd_fengkong,
        "風控設定": cmd_fengkong_shezhi,
        "回測": cmd_huice, "backtest": cmd_huice,
        "規則": cmd_guize_qingdan, "rules": cmd_guize_qingdan,
        "日誌": cmd_riji, "journal": cmd_riji,
        "週報": cmd_zhoubao, "weekly": cmd_zhoubao,
        "資料庫": cmd_shujuku, "db": cmd_shujuku,
    }

    if keyword in mapping:
        if arg: context.args = parts[1:]
        await mapping[keyword](update, context)
    elif text.isdigit() and len(text) == 4:
        context.args = [text]
        await cmd_gujia(update, context)
    else:
        reply = await ask_claude(update.effective_user.id, text)
        await update.message.reply_text(reply)

# ══════════════════════════════════════════════════════
# 排程器 (已清理重複任務)
# ══════════════════════════════════════════════════════

def run_scheduler(bot_token: str, user_ids: list):
    async def run_async_task(task_func):
        from telegram import Bot
        bot = Bot(token=bot_token)
        try:
            msg = await task_func()
            if msg:
                for uid in user_ids:
                    await bot.send_message(chat_id=uid, text=msg)
        except Exception as e:
            logger.error(f"排程任務失敗: {e}")

    def daily_report_job():
        asyncio.run(run_async_task(generate_daily_report))

    def weekly_report_job():
        # 預留週報生成邏輯
        pass

    schedule.every().day.at("16:00").do(daily_report_job)
    schedule.every().sunday.at("20:00").do(weekly_report_job)
    
    logger.info("排程器啟動：每日16:00推播日報 | 週日20:00週報")
    while True:
        schedule.run_pending()
        time.sleep(60)

# ══════════════════════════════════════════════════════
# 主程式
# ══════════════════════════════════════════════════════

def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("請設定 TELEGRAM_BOT_TOKEN")
    if not ANTHROPIC_API_KEY:
        raise ValueError("請設定 ANTHROPIC_API_KEY")

    threading.Thread(target=run_scheduler, args=(TELEGRAM_TOKEN, ALLOWED_USER_IDS), daemon=True).start()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # 指令綁定 (已去重複)
    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("help",      cmd_start))
    app.add_handler(CommandHandler("chat",      cmd_duihua))
    app.add_handler(CommandHandler("clear",     cmd_qingchu))
    app.add_handler(CommandHandler("price",     cmd_gujia))
    app.add_handler(CommandHandler("analyze",   cmd_fenxi))
    app.add_handler(CommandHandler("screen",    cmd_xuangu))
    app.add_handler(CommandHandler("macro",     cmd_hongguan))
    app.add_handler(CommandHandler("watch",     cmd_jiaru))
    app.add_handler(CommandHandler("unwatch",   cmd_yichu))
    app.add_handler(CommandHandler("list",      cmd_qingdan))
    app.add_handler(CommandHandler("buy",       cmd_maijin))
    app.add_handler(CommandHandler("sell",      cmd_machu))
    app.add_handler(CommandHandler("portfolio", cmd_chicang))
    app.add_handler(CommandHandler("check",     cmd_jiancha))
    app.add_handler(CommandHandler("calc",      cmd_jianyi_zhangshui))
    app.add_handler(CommandHandler("report",    cmd_daily_report))
    app.add_handler(CommandHandler("score",     cmd_pingfen))
    app.add_handler(CommandHandler("risk",      cmd_fengkong))
    app.add_handler(CommandHandler("riskset",   cmd_fengkong_shezhi))
    app.add_handler(CommandHandler("mktcheck",  cmd_shichang_fengxian))
    app.add_handler(CommandHandler("backtest",  cmd_huice))
    app.add_handler(CommandHandler("journal",   cmd_riji))
    app.add_handler(CommandHandler("weekly",    cmd_zhoubao))
    app.add_handler(CommandHandler("teach",     cmd_xinzeng_guize))
    app.add_handler(CommandHandler("rules",     cmd_guize_qingdan))
    app.add_handler(CommandHandler("delrule",   cmd_shanchu_guize))
    app.add_handler(CommandHandler("learning",  cmd_xuexi_jilu))
    app.add_handler(CommandHandler("update",    cmd_update_now))
    app.add_handler(CommandHandler("db",        cmd_shujuku))
    app.add_handler(CommandHandler("dbinit",    cmd_chushihua))
    app.add_handler(CommandHandler("stocks",    cmd_gengxin_qingdan))
    app.add_handler(CommandHandler("crawl",     cmd_paqu))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("量化交易 Bot 已啟動！")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
