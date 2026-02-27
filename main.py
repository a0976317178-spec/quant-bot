"""
main.py - 台股量化交易 Bot（完整整合版）
整合：資料庫分析 / 勝率學習 / 每日自動更新 / 持股追蹤
"""
import os
import logging
import asyncio
import threading
import concurrent.futures
from datetime import datetime
from dotenv import load_dotenv
import anthropic
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    MessageHandler, ContextTypes, filters
)
from config import (
    TELEGRAM_TOKEN, ALLOWED_USER_IDS,
    ANTHROPIC_API_KEY, CLAUDE_FAST_MODEL, CLAUDE_SMART_MODEL
)
from memory.rules_manager import (
    add_rule, delete_rule, list_rules,
    load_history, save_history, clear_history, get_rules_as_prompt
)
from memory.daily_learning import (
    add_to_watchlist, remove_from_watchlist,
    list_watchlist, get_recent_learnings, daily_learning_task
)
from memory.trade_log import (
    log_entry, log_exit, get_trade_stats, format_trade_history
)
from portfolio.tracker import (
    add_position, remove_position, list_portfolio, check_portfolio_alerts
)
from risk.manager import (
    get_risk_summary, check_market_risk,
    calc_position_size, update_risk_param
)
from database.db_manager import (
    get_db_stats, format_db_status, init_db
)
from database.daily_update import run_daily_update
from report.daily_report import generate_daily_report, score_stock
from factors.analyzer import full_analysis, format_analysis_report
from ml.self_learning import (
    update_win_rate, get_win_rate_report,
    get_strategy_advice, weekly_self_learning
)
from memory.ai_self_learning import get_self_learning_summary, run_daily_self_learning
from alert.daily_alert import run_daily_scan, run_open_alert, run_close_alert
# ✅ 修補1：匯入假日判斷模組
from tw_market_calendar import is_trading_day, is_market_open, get_holiday_name
# ✅ 新功能：技能載入引擎
from skill_hunter import (
    learn_skill_from_request, install_skill_from_url,
    list_all_skills, uninstall_skill, set_skillsmp_key
)
from skill_loader import (
    build_skills_prompt, list_skills, add_custom_skill,
    get_skill_detail, run_skill_self_learning
)
# ✅ 新功能：模擬交易引擎
from paper_trading import (
    run_daily_paper_trading, get_paper_portfolio,
    get_paper_report, get_paper_config_summary, update_paper_config
)
# ✅ 新功能：AI 信號追蹤器
from ai_signal_tracker import (
    run_daily_signal_scan, get_active_signals,
    get_signal_performance, get_signal_history,
    run_signal_self_learning, generate_buy_signals
)

load_dotenv()
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)
claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def is_authorized(user_id: int) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS


async def ask_claude(user_id: int, message: str, use_smart: bool = False) -> str:
    model = CLAUDE_SMART_MODEL if use_smart else CLAUDE_FAST_MODEL
    rules = get_rules_as_prompt()
    recent = get_recent_learnings(days=3)

    # ✅ 修補3：將勝率資料注入 system prompt，讓 AI 更聰明
    win_rate_context = ""
    try:
        from database.db_manager import query_df
        wr_df = query_df("""
            SELECT stock_id, win_rate, total_trades, profit_factor, avg_win_pct, avg_loss_pct
            FROM win_rate_db WHERE total_trades >= 2 ORDER BY win_rate DESC LIMIT 10
        """)
        if not wr_df.empty:
            top = wr_df.to_dict("records")
            lines = [f"  {r['stock_id']}：勝率{r['win_rate']:.0f}%（{r['total_trades']}次）盈虧比{r['profit_factor']:.1f}x" for r in top]
            win_rate_context = "\n【我的歷史勝率TOP10（請優先推薦高勝率標的）】\n" + "\n".join(lines)
    except Exception:
        pass

    # ✅ 新功能：載入技能庫知識
    skills_prompt = ""
    try:
        skills_prompt = build_skills_prompt(max_skills=4)
    except Exception:
        pass

    system = (
        "你是專業台股量化交易AI助理「量化師」。\n"
        "所有回覆必須使用繁體中文，格式要清晰易讀，適當使用emoji。\n"
        "專長：技術分析、籌碼分析、基本面、量化選股、風險控管。\n"
        f"近期市場觀察：{recent}\n"
        f"{win_rate_context}\n"
        f"{skills_prompt}\n"
        f"{rules}\n"
        "分析時依量價、籌碼、基本面、宏觀四維度，並說明風險。\n"
        "回覆要有重點、有結論、有操作建議，避免空泛說明。\n"
        "使用技能庫的評分規則時，請在回覆中說明使用了哪個技能。"
    )
    history = load_history(user_id)
    history.append({"role": "user", "content": message})
    try:
        resp = claude_client.messages.create(
            model=model,
            max_tokens=2000,  # ✅ 修補4：從 1200 提升至 2000
            system=system,
            messages=history
        )
        reply = resp.content[0].text
        history.append({"role": "assistant", "content": reply})
        save_history(user_id, history)
        return reply
    except Exception as e:
        return f"⚠️ API 錯誤：{e}"


# ══════════════════════════════════════════════════════
# 指令處理器
# ══════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    msg = (
        "🤖 *量化師* ｜ 台股 AI 交易助理\n"
        "══════════════════════════════\n\n"

        "💬 *對話與問答*\n"
        "┣ `/chat` 或直接輸入文字 — 問任何投資問題\n"
        "┗ `/clear` `清除` — 重置 AI 對話記憶\n\n"

        "📊 *行情分析*\n"
        "┣ `/price 2330`  `股價 2330` — 即時報價\n"
        "┣ `/analyze 2330`  `分析 2330` — 四維度深度分析\n"
        "┣ `/screen`  `選股` — 掃描自選股清單評分\n"
        "┣ `/scan`  `掃描全市場` — 全市場強勢股掃描\n"
        "┣ `/macro`  `宏觀` — VIX 大盤指標快照\n"
        "┗ `/news`  `新聞` — 今日台股 AI 新聞摘要\n\n"

        "📋 *自選股管理*\n"
        "┣ `/watch 2330`  `加入 2330` — 加入監控\n"
        "┣ `/unwatch 2330`  `移除 2330` — 移出監控\n"
        "┗ `/list`  `清單` — 查看自選股清單\n\n"

        "💼 *持股追蹤*\n"
        "┣ `買進 2330 1 980` — 記錄進場（股票/張數/價格）\n"
        "┣ `賣出 2330 1020` — 平倉並計算損益\n"
        "┣ `持股` — 查看目前持倉狀態\n"
        "┗ `建倉試算 980` — 依風控計算建議張數\n\n"

        "🏆 *勝率學習*\n"
        "┣ `/winrate`  `勝率` — 個股勝率排行榜\n"
        "┣ `/advice 2330`  `策略 2330` — AI 策略建議\n"
        "┣ `/tradelog`  `交易記錄` — 歷史交易明細\n"
        "┗ `/stats`  `績效` — 近30天損益統計\n\n"

        "🛡️ *風險控管*\n"
        "┣ `風控` — 查看目前風控設定\n"
        "┣ `風控設定 停損 7` — 修改風控參數\n"
        "┗ `市場風險` — 檢查大盤 VIX / ADL 風險\n\n"

        "📈 *回測*\n"
        "┗ `回測 2330 2022` — 個股歷史策略回測\n\n"

        "🧠 *AI 學習規則*\n"
        "┣ `新增規則 外資連買3天才進場` — 新增規則\n"
        "┣ `規則` — 查看所有規則\n"
        "┣ `刪除規則 1` — 刪除指定規則\n"
        "┗ `學習記錄` — 查看 AI 自學習日誌\n\n"

        "🤖 *AI 自動信號*\n"
        "┣ `信號清單` — 查看目前 AI 主動買入信號\n"
        "┣ `信號績效` — AI 信號勝率統計\n"
        "┣ `信號記錄` — 歷史信號記錄\n"
        "┗ `立即掃信號` — 手動觸發信號掃描\n\n"

        "🎮 *模擬交易（先練再實盤）*\n"
        "┣ `模擬持倉` — AI 目前模擬倉位\n"
        "┣ `模擬績效` — 模擬勝率與損益\n"
        "┗ `模擬設定` — 查看/修改模擬參數\n\n"

        "📚 *技能庫（AI 自動學習）*\n"
        "┣ `技能庫` — 查看所有策略技能\n"
        "┣ `技能 momentum` — 查看技能規則\n"
        "┗ `新增技能 名稱 內容` — 新增自定義技能\n\n"

        "🌐 *AI 自動搜尋新技能*\n"
        "┣ `幫我學習 [需求]` — AI 自動搜尋安裝（例：幫我學習下載影片）\n"
        "┣ `安裝技能 [GitHub URL]` — 直接安裝 GitHub 技能\n"
        "┣ `移除技能 名稱` — 移除已安裝技能\n"
        "┗ `設定SkillsMP金鑰 sk_live_xxx` — 啟用 AI 語意搜尋\n\n"

        "🗄️ *資料庫*\n"
        "┣ `/db`  `資料庫` — 查看資料庫狀態\n"
        "┣ `/update`  `更新資料` — 立即更新所有數據\n"
        "┣ `/report`  `每日報告` — 手動觸發今日評分報告\n"
        "┣ `/dbinit`  `初始化` — 建立資料庫結構\n"
        "┗ `/crawl 2022`  `爬取 2022` — 下載指定年度股價\n\n"

        "══════════════════════════════\n"
        "💡 直接輸入 4 位數字查詢股價\n"
        "💡 任何問題直接用文字詢問 AI"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text(
            "💬 請輸入問題\n範例：/chat 現在台積電值得買嗎？"
        )
        return
    await update.message.reply_text("🤔 思考中，請稍候...")
    reply = await ask_claude(update.effective_user.id, " ".join(context.args))
    await update.message.reply_text(reply)


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    result = clear_history(update.effective_user.id)
    await update.message.reply_text(f"🗑️ {result}\n\n已清除 AI 對話記憶，重新開始對話吧！")


async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    text = update.message.text.strip().split()
    args = [p for p in text if p not in ["股價", "查股價", "現價", "price", "/price"]]
    if not args:
        await update.message.reply_text("📌 請輸入股票代號\n範例：`股價 2330`", parse_mode="Markdown")
        return
    stock_id = args[0]
    await update.message.reply_text(f"🔍 查詢 {stock_id} 中...")
    try:
        from factors.realtime import get_stock_quote, format_quote_message
        quote = get_stock_quote(stock_id)
        if not quote or quote.get("close", 0) == 0:
            await update.message.reply_text(f"❌ 找不到 `{stock_id}`，請確認股票代號", parse_mode="Markdown")
            return
        await update.message.reply_text(format_quote_message(quote), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ 查詢失敗：{e}")


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """完整分析：調用資料庫數據 + AI 解讀 + 存入分析日誌"""
    if not is_authorized(update.effective_user.id):
        return
    text = update.message.text.strip().split()
    args = [p for p in text if p not in ["分析", "analyze", "/analyze", "研究"]]
    if not args:
        await update.message.reply_text("📌 請輸入股票代號\n範例：`分析 2330`", parse_mode="Markdown")
        return
    stock_id = args[0]
    await update.message.reply_text(f"🔬 深度分析 {stock_id} 中，調用資料庫數據...")

    def run_analysis():
        return full_analysis(stock_id)

    with concurrent.futures.ThreadPoolExecutor() as executor:
        result = executor.submit(run_analysis).result(timeout=60)

    report = format_analysis_report(result)
    await update.message.reply_text(report)

    # AI 深度解讀
    ai_prompt = (
        f"股票 {stock_id} {result['name']} 的量化分析結果如下：\n"
        f"綜合評分：{result['total_score']}分  {result['grade']}\n"
        f"技術面：{result['tech']['note']}\n"
        f"籌碼面：{result['chip']['note']}\n"
        f"基本面：{result['fund']['note']}\n"
        f"環境面：{result['env']['note']}\n"
        f"現價：${result['close_price']}\n\n"
        f"請用3句話給出最關鍵的操作建議，說明：①最佳進場時機 ②主要風險點 ③建議停損位置。"
    )
    ai_comment = await ask_claude(update.effective_user.id, ai_prompt, use_smart=True)
    await update.message.reply_text(
        f"🤖 *AI 操作解讀*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{ai_comment}",
        parse_mode="Markdown"
    )


async def cmd_screen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    await update.message.reply_text("🔍 掃描自選股清單中...")
    try:
        from memory.daily_learning import load_watchlist
        watchlist = load_watchlist()
        if not watchlist:
            await update.message.reply_text(
                "📋 自選股清單為空\n\n"
                "使用 `加入 2330` 新增股票到監控清單",
                parse_mode="Markdown"
            )
            return

        results = []
        for sid in watchlist:
            try:
                r = score_stock(sid)
                results.append(r)
            except:
                pass
        results.sort(key=lambda x: x["score"], reverse=True)

        lines = [f"📊 *自選股掃描結果*  共 {len(results)} 支\n"]
        for i, r in enumerate(results):
            if r["score"] >= 70:
                emoji = "🔥"
            elif r["score"] >= 55:
                emoji = "✅"
            else:
                emoji = "⬇️"
            lines.append(f"{emoji} `{r['stock_id']}` {r['name']}　*{r['score']}分*　{r['grade']}")

        if results:
            top = results[0]
            lines.append(f"\n🏆 最高分：`{top['stock_id']}` {top['score']}分")
            lines.append(f"💡 輸入 `分析 {top['stock_id']}` 取得完整分析")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ 掃描失敗：{e}")


async def cmd_macro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    await update.message.reply_text("🌐 取得宏觀數據中...")
    try:
        from factors.macro import get_macro_snapshot
        data = get_macro_snapshot()
        vix = data.get("vix", "N/A")
        vix_signal = data.get("vix_signal", "")
        adv = data.get("advancing", "N/A")
        dec = data.get("declining", "N/A")

        if isinstance(vix, (int, float)):
            if vix < 15:
                vix_emoji = "🟢"
            elif vix < 20:
                vix_emoji = "🟡"
            elif vix < 25:
                vix_emoji = "🟠"
            else:
                vix_emoji = "🔴"
        else:
            vix_emoji = "⚪"

        msg = (
            f"🌐 *宏觀指標快照*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{vix_emoji} VIX 恐慌指數：*{vix}*　{vix_signal}\n"
            f"📈 上漲家數：{adv}\n"
            f"📉 下跌家數：{dec}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
        )
        interp = await ask_claude(
            update.effective_user.id,
            f"VIX={vix}，上漲{adv}家、下跌{dec}家，"
            f"請用3句話解讀目前市場環境並給出本週操作建議。"
        )
        await update.message.reply_text(msg + interp, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ 取得失敗：{e}")


async def cmd_watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    text = update.message.text.strip().split()
    args = [p for p in text if p not in ["加入", "監控", "watch", "/watch"]]
    if not args:
        await update.message.reply_text("📌 請輸入代號\n範例：`加入 2330`", parse_mode="Markdown")
        return
    await update.message.reply_text(add_to_watchlist(args[0]))


async def cmd_unwatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    text = update.message.text.strip().split()
    args = [p for p in text if p not in ["移除", "取消監控", "unwatch", "/unwatch"]]
    if not args:
        await update.message.reply_text("📌 請輸入代號\n範例：`移除 2330`", parse_mode="Markdown")
        return
    await update.message.reply_text(remove_from_watchlist(args[0]))


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    await update.message.reply_text(list_watchlist())


async def cmd_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """買進：記錄進場 + 持股追蹤 + 交易日誌"""
    if not is_authorized(update.effective_user.id):
        return
    text = update.message.text.strip().split()
    args = [p for p in text if p not in ["買進", "buy", "進場", "/buy"]]
    if len(args) < 3:
        await update.message.reply_text(
            "📌 *買進格式*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "`買進 代號 張數 進場價`\n"
            "範例：`買進 2330 1 980`\n\n"
            "🔧 *自訂停損停利*\n"
            "`買進 2330 1 980 停損7 停利15`",
            parse_mode="Markdown"
        )
        return
    try:
        stock_id = args[0]
        shares = int(args[1])
        entry_price = float(args[2])
        stop_loss_pct = 0.05
        target_pct = 0.10
        reason = ""
        for p in args[3:]:
            if p.startswith("停損"):
                stop_loss_pct = float(p.replace("停損", "")) / 100
            elif p.startswith("停利"):
                target_pct = float(p.replace("停利", "")) / 100
            else:
                reason += p + " "

        score = 0
        try:
            r = score_stock(stock_id)
            score = r["score"]
        except:
            pass

        result = add_position(stock_id, entry_price, shares, stop_loss_pct, target_pct)
        log_entry(stock_id, entry_price, shares, reason.strip(), score)
        await update.message.reply_text(result)
    except Exception as e:
        await update.message.reply_text(
            f"⚠️ 格式錯誤：{e}\n\n"
            "正確格式：`買進 2330 1 980`",
            parse_mode="Markdown"
        )


async def cmd_sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """賣出：平倉 + 更新交易日誌 + 觸發 AI 學習"""
    if not is_authorized(update.effective_user.id):
        return
    text = update.message.text.strip().split()
    args = [p for p in text if p not in ["賣出", "sell", "出場", "平倉", "/sell"]]
    if not args:
        await update.message.reply_text(
            "📌 *賣出格式*\n"
            "`賣出 代號 出場價`\n"
            "範例：`賣出 2330 1020`",
            parse_mode="Markdown"
        )
        return
    stock_id = args[0]
    exit_price = float(args[1]) if len(args) > 1 else None
    exit_reason = " ".join(args[2:]) if len(args) > 2 else "手動出場"

    result = remove_position(stock_id, exit_price)
    await update.message.reply_text(result)

    if exit_price:
        log_exit(stock_id, exit_price, exit_reason)

        def learn():
            update_win_rate(stock_id)
        threading.Thread(target=learn, daemon=True).start()

        advice = get_strategy_advice(stock_id)
        await update.message.reply_text(
            f"🧠 *AI 學習完成*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{advice}",
            parse_mode="Markdown"
        )


async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    await update.message.reply_text(list_portfolio())


async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    await update.message.reply_text("🔍 檢查持股狀態中...")
    alerts = await check_portfolio_alerts()
    if alerts:
        for a in alerts:
            await update.message.reply_text(a["message"])
    else:
        await update.message.reply_text(
            "✅ *持股狀態正常*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "所有持股未觸及停損或目標價\n"
            "系統每 15 分鐘盤中自動監控中 👀",
            parse_mode="Markdown"
        )


async def cmd_calc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    text = update.message.text.strip().split()
    args = [p for p in text if p not in ["建倉試算", "試算", "calc", "/calc"]]
    if not args or not args[0].replace(".", "").isdigit():
        await update.message.reply_text(
            "📌 請輸入股價\n範例：`建倉試算 980`",
            parse_mode="Markdown"
        )
        return
    price = float(args[0])
    r = calc_position_size(price)
    await update.message.reply_text(
        f"🧮 *建倉試算*  股價 ${price}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📦 建議張數：*{r['suggested_lots']} 張*\n"
        f"💰 投入金額：${r['actual_invest']:,.0f}\n"
        f"📉 最大虧損：${r['max_loss_amount']:,.0f}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🛑 停損價：*${r['stop_loss_price']}*\n"
        f"🎯 目標價：*${r['take_profit_price']}*\n\n"
        f"_以上依據您的風控設定計算_",
        parse_mode="Markdown"
    )


async def cmd_winrate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看勝率排行榜"""
    if not is_authorized(update.effective_user.id):
        return
    await update.message.reply_text(get_win_rate_report())


async def cmd_advice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查詢特定股票 AI 策略建議"""
    if not is_authorized(update.effective_user.id):
        return
    text = update.message.text.strip().split()
    args = [p for p in text if p not in ["策略", "advice", "/advice"]]
    if not args:
        await update.message.reply_text("📌 請輸入代號\n範例：`策略 2330`", parse_mode="Markdown")
        return
    await update.message.reply_text(get_strategy_advice(args[0]))


async def cmd_tradelog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看交易記錄"""
    if not is_authorized(update.effective_user.id):
        return
    await update.message.reply_text(format_trade_history(limit=10))


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """交易績效統計"""
    if not is_authorized(update.effective_user.id):
        return
    stats = get_trade_stats(days=30)
    if stats["total"] == 0:
        await update.message.reply_text(
            "📋 *近30天尚無交易記錄*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "使用 `買進 2330 1 980` 記錄進場\n"
            "使用 `賣出 2330 1020` 記錄出場",
            parse_mode="Markdown"
        )
        return

    wr_emoji = "🟢" if stats["win_rate"] >= 55 else ("🟡" if stats["win_rate"] >= 45 else "🔴")
    pnl_emoji = "📈" if stats["total_pnl"] >= 0 else "📉"
    best = stats.get("best")
    worst = stats.get("worst")

    msg = (
        f"📊 *近30天交易績效*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔢 總交易次數：{stats['total']} 次\n"
        f"{wr_emoji} 勝率：*{stats['win_rate']}%*（{stats['wins']}勝 / {stats['losses']}敗）\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💚 平均獲利：*{stats['avg_win']:+.2f}%*\n"
        f"❤️ 平均虧損：*{stats['avg_loss']:+.2f}%*\n"
        f"{pnl_emoji} 累計損益：*${stats['total_pnl']:+,.0f}*\n"
    )
    if best:
        msg += f"\n🏆 最佳交易：`{best['stock_id']}` {best.get('pnl_pct', 0):+.2f}%"
    if worst:
        msg += f"\n💀 最差交易：`{worst['stock_id']}` {worst.get('pnl_pct', 0):+.2f}%"

    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    await update.message.reply_text(get_risk_summary())


async def cmd_riskset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    text = update.message.text.strip().split()
    args = [p for p in text if p not in ["風控設定", "/riskset"]]
    if len(args) < 2:
        await update.message.reply_text(
            "🛡️ *風控設定格式*\n"
            "`風控設定 <參數> <數值>`\n\n"
            "📋 *可設定參數*\n"
            "┣ `停損 0.05`（停損 5%）\n"
            "┣ `停利 0.10`（停利 10%）\n"
            "┣ `總資金 1000000`\n"
            "┣ `最大持股 5`\n"
            "┗ `VIX門檻 30`",
            parse_mode="Markdown"
        )
        return
    param_map = {
        "停損": "stop_loss_pct", "停利": "take_profit_pct",
        "總資金": "total_capital", "最大持股": "max_positions",
        "VIX門檻": "pause_when_vix_above"
    }
    key = param_map.get(args[0], args[0])
    try:
        await update.message.reply_text(update_risk_param(key, float(args[1])))
    except Exception as e:
        await update.message.reply_text(f"⚠️ 設定失敗：{e}")


async def cmd_mktcheck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    await update.message.reply_text("🔍 檢查市場風險中...")
    result = check_market_risk()
    if result["warnings"]:
        msg = (
            f"⚠️ *市場風險警告*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
        )
        msg += "\n".join(result["warnings"])
        if result["should_pause"]:
            msg += "\n\n🛑 *系統已自動暫停選股推薦*"
        else:
            msg += "\n\n💡 請注意風險，適當降低倉位"
    else:
        msg = (
            "✅ *市場風險正常*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "VIX 與 ADL 均在正常範圍\n"
            "可正常進行操作 💪"
        )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    text = update.message.text.strip().split()
    args = [p for p in text if p not in ["回測", "backtest", "/backtest"]]
    if not args:
        await update.message.reply_text(
            "📈 *回測格式*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "`回測 代號`\n"
            "`回測 代號 起始年`\n"
            "`回測 代號 起始年 結束年`\n\n"
            "範例：`回測 2330 2022`",
            parse_mode="Markdown"
        )
        return
    stock_id = args[0]
    start_date = f"{args[1]}-01-01" if len(args) >= 2 else None
    end_date   = f"{args[2]}-12-31" if len(args) >= 3 else None
    await update.message.reply_text(f"📈 回測 `{stock_id}` 中，請稍候...", parse_mode="Markdown")

    def run():
        from backtest.engine import run_backtest, format_backtest_report
        result = run_backtest(stock_id=stock_id, start_date=start_date, end_date=end_date)
        return format_backtest_report(result)

    with concurrent.futures.ThreadPoolExecutor() as executor:
        report = executor.submit(run).result(timeout=180)
    await update.message.reply_text(report)


async def cmd_teach(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text(
            "📌 請輸入規則\n範例：`/teach 外資連買3天才進場`",
            parse_mode="Markdown"
        )
        return
    await update.message.reply_text(add_rule(" ".join(context.args)))


async def cmd_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    await update.message.reply_text(list_rules())


async def cmd_delrule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "📌 請輸入規則編號\n範例：`/delrule 1`",
            parse_mode="Markdown"
        )
        return
    await update.message.reply_text(delete_rule(int(context.args[0])))


async def cmd_learning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    report = get_self_learning_summary(days=7)
    await update.message.reply_text(report if report else get_recent_learnings(days=7))


async def cmd_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    try:
        stats = get_db_stats()
        await update.message.reply_text(format_db_status(stats))
    except Exception as e:
        await update.message.reply_text(
            f"⚠️ 查詢失敗：{e}\n\n"
            "請先執行 `初始化` 建立資料庫",
            parse_mode="Markdown"
        )


async def cmd_dbinit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    try:
        init_db()
        await update.message.reply_text(
            "✅ *資料庫初始化完成*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "建議執行順序：\n"
            "1️⃣ 輸入 `更新清單` — 下載股票清單\n"
            "2️⃣ 輸入 `爬取 2022` — 下載歷史股價\n"
            "3️⃣ 輸入 `更新資料` — 更新最新數據",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ 初始化失敗：{e}")


async def cmd_stocks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    await update.message.reply_text("🔄 從證交所更新股票清單中...")
    def run():
        from database.crawler import fetch_stock_list, save_stock_list
        stocks = fetch_stock_list()
        save_stock_list(stocks)
        return len(stocks)
    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            n = executor.submit(run).result(timeout=120)
        await update.message.reply_text(f"✅ 股票清單更新完成！共 {n} 支")
    except Exception as e:
        await update.message.reply_text(f"⚠️ 更新失敗：{e}")


async def cmd_crawl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    text = update.message.text.strip().split()
    args = [p for p in text if p not in ["爬取", "crawl", "/crawl"]]
    year = int(args[0]) if args and args[0].isdigit() else 2022
    await update.message.reply_text(
        f"🕷️ *開始爬取 {year} 年至今的歷史股價*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⏳ 背景執行中，可繼續使用 Bot\n"
        f"📊 使用 `資料庫` 查看爬取進度",
        parse_mode="Markdown"
    )
    def run():
        from database.crawler import crawl_all_prices, crawl_institutional, crawl_macro
        from datetime import timedelta
        crawl_all_prices(start_year=year)
        start = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
        crawl_institutional(start_date=start)
        crawl_macro(days=365)
    threading.Thread(target=run, daemon=True).start()


async def cmd_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    await update.message.reply_text("🔄 開始更新所有資料，約需 2～5 分鐘...")
    result = await run_daily_update()
    await update.message.reply_text(result)


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    await update.message.reply_text("📋 生成每日評分報告中...")
    with concurrent.futures.ThreadPoolExecutor() as executor:
        report = executor.submit(generate_daily_report).result(timeout=120)
    await update.message.reply_text(report)


async def cmd_score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    text = update.message.text.strip().split()
    args = [p for p in text if p not in ["評分", "score", "/score"]]
    if not args:
        await update.message.reply_text("📌 請輸入代號\n範例：`評分 2330`", parse_mode="Markdown")
        return
    stock_id = args[0]
    await update.message.reply_text(f"🔢 評分 `{stock_id}` 中...", parse_mode="Markdown")
    with concurrent.futures.ThreadPoolExecutor() as executor:
        result = executor.submit(score_stock, stock_id).result(timeout=60)
    msg = (
        f"🔢 *{result['stock_id']} {result['name']} 評分*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"總分：*{result['score']} 分*　{result['grade']}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
    )
    for d in result["details"]:
        msg += f"{d}\n"
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    await update.message.reply_text("📰 爬取今日台股新聞中，請稍候約 30 秒...")
    try:
        from news.tw_stock_news import run_daily_news_summary
        with concurrent.futures.ThreadPoolExecutor() as executor:
            report = executor.submit(run_daily_news_summary, claude_client).result(timeout=90)
        for i in range(0, len(report), 4000):
            await update.message.reply_text(report[i:i+4000])
    except Exception as e:
        await update.message.reply_text(f"⚠️ 新聞取得失敗：{e}")


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    await update.message.reply_text("🔍 全市場掃描中，約需 30～60 秒...")
    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            report = executor.submit(run_daily_scan, "close").result(timeout=120)
        for i in range(0, len(report), 4000):
            await update.message.reply_text(report[i:i+4000])
    except Exception as e:
        await update.message.reply_text(f"⚠️ 掃描失敗：{e}")


async def cmd_signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看目前 AI 主動信號清單"""
    if not is_authorized(update.effective_user.id):
        return
    await update.message.reply_text(get_active_signals(), parse_mode="Markdown")


async def cmd_signal_perf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看 AI 信號績效統計"""
    if not is_authorized(update.effective_user.id):
        return
    await update.message.reply_text(get_signal_performance(), parse_mode="Markdown")


async def cmd_signal_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看歷史信號記錄"""
    if not is_authorized(update.effective_user.id):
        return
    await update.message.reply_text(get_signal_history(), parse_mode="Markdown")


async def cmd_signal_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """手動觸發 AI 信號掃描"""
    if not is_authorized(update.effective_user.id):
        return
    await update.message.reply_text("🤖 AI 信號掃描中，請稍候...")
    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            report = executor.submit(run_daily_signal_scan, claude_client).result(timeout=120)
        await update.message.reply_text(report, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ 掃描失敗：{e}")


# ══════════════════════════════════════════════════════
# 文字訊息路由
# ══════════════════════════════════════════════════════

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    text = update.message.text.strip()
    parts = text.split()
    kw = parts[0] if parts else ""

    routes = {
        # 模擬交易
        ("模擬持倉", "模擬倉位", "paper"): cmd_paper_portfolio,
        ("模擬績效", "模擬報告"): cmd_paper_report,
        ("模擬設定", "paper設定"): cmd_paper_config,
        ("執行模擬", "模擬交易"): cmd_paper_run,
        # 技能獵人（AI 自動搜尋學習）
        ("幫我學習", "學習技能", "搜尋技能", "learn"): cmd_learn_skill,
        ("安裝技能", "install技能"): cmd_install_skill_url,
        ("移除技能", "刪除技能"): cmd_uninstall_skill,
        ("設定SkillsMP金鑰", "設定skillsmp", "skillsmpkey"): cmd_set_skillsmp_key,
        # 技能庫
        ("技能庫", "技能列表", "所有技能", "skills"): cmd_all_skills,
        ("技能", "skill查看"): cmd_skill_detail,
        ("新增技能",): cmd_add_skill,
        # 信號
        ("信號清單", "ai信號", "signals"): cmd_signals,
        ("信號績效", "signal績效"): cmd_signal_perf,
        ("信號記錄", "信號歷史"): cmd_signal_history,
        ("立即掃信號", "掃信號", "signal"): cmd_signal_scan,
        # 股價
        ("股價", "查股價", "現價", "price"): cmd_price,
        # 分析
        ("分析", "analyze", "研究"): cmd_analyze,
        # 監控
        ("加入", "監控", "watch"): cmd_watch,
        ("移除", "取消監控", "unwatch"): cmd_unwatch,
        ("清單", "監控清單", "自選股"): cmd_list,
        # 選股
        ("選股", "掃描", "screen"): cmd_screen,
        ("掃描全市場", "全市場", "scan"): cmd_scan,
        ("新聞", "今日新聞", "news"): cmd_news,
        # 宏觀
        ("宏觀", "大盤", "市場", "macro"): cmd_macro,
        # 持股
        ("買進", "buy", "進場"): cmd_buy,
        ("賣出", "sell", "出場", "平倉"): cmd_sell,
        ("持股", "持股清單", "portfolio", "倉位"): cmd_portfolio,
        ("檢查", "check", "警報"): cmd_check,
        ("建倉試算", "試算", "calc"): cmd_calc,
        # 勝率
        ("勝率", "winrate"): cmd_winrate,
        ("策略", "advice"): cmd_advice,
        ("交易記錄", "tradelog"): cmd_tradelog,
        ("績效", "stats"): cmd_stats,
        # 風控
        ("風控", "risk", "風險設定"): cmd_risk,
        ("風控設定", "riskset"): cmd_riskset,
        ("市場風險", "風險檢查", "mktcheck"): cmd_mktcheck,
        # 回測
        ("回測", "backtest"): cmd_backtest,
        # 規則
        ("新增規則", "記住", "teach"): cmd_teach,
        ("規則", "規則清單", "rules"): cmd_rules,
        ("刪除規則", "delrule"): cmd_delrule,
        ("學習記錄", "learning"): cmd_learning,
        # 資料庫
        ("資料庫", "db"): cmd_db,
        ("初始化",): cmd_dbinit,
        ("更新清單", "stocks"): cmd_stocks,
        ("爬取", "crawl"): cmd_crawl,
        ("更新資料", "立即更新", "update"): cmd_update,
        ("每日報告", "今日報告", "report"): cmd_report,
        ("評分", "score"): cmd_score,
        # 其他
        ("清除", "clear"): cmd_clear,
        ("說明", "指令", "幫助", "help", "選單", "start"): cmd_start,
    }

    for keywords, handler in routes.items():
        if kw in keywords:
            await handler(update, context)
            return

    # 4位數代號直接查股價
    if text.isdigit() and len(text) == 4:
        context.args = [text]
        await cmd_price(update, context)
        return

    # 預設對話
    reply = await ask_claude(update.effective_user.id, text)
    await update.message.reply_text(reply)


# ══════════════════════════════════════════════════════
# 排程器
# ══════════════════════════════════════════════════════

def run_scheduler(bot_token: str, user_ids: list):
    import schedule
    import time

    async def do_daily_update():
        # ✅ 修補2：非交易日跳過更新
        if not is_trading_day():
            reason = get_holiday_name() or "週末"
            logger.info(f"📅 非交易日（{reason}），跳過每日資料更新")
            return
        from telegram import Bot
        bot = Bot(token=bot_token)
        result = await run_daily_update()
        logger.info(f"每日更新完成：{result[:50]}")
        for uid in user_ids:
            try:
                await bot.send_message(chat_id=uid, text=result)
            except:
                pass

    async def do_daily_report():
        # ✅ 修補2：非交易日跳過報告
        if not is_trading_day():
            reason = get_holiday_name() or "週末"
            logger.info(f"📅 非交易日（{reason}），跳過每日報告")
            return
        from telegram import Bot
        bot = Bot(token=bot_token)
        report = generate_daily_report()
        for uid in user_ids:
            try:
                await bot.send_message(chat_id=uid, text=report)
            except:
                pass

    async def do_alert_check():
        # ✅ 修補1：使用 is_market_open() 取代手動時間判斷，自動處理假日
        if not is_market_open():
            return
        from telegram import Bot
        bot = Bot(token=bot_token)
        alerts = await check_portfolio_alerts()
        for a in alerts:
            for uid in user_ids:
                try:
                    await bot.send_message(chat_id=uid, text=a["message"])
                except:
                    pass
        risk = check_market_risk()
        if risk["should_pause"] and risk["warnings"]:
            msg = (
                "⚠️ *市場風險警告*\n"
                "━━━━━━━━━━━━━━━━━━\n"
            ) + "\n".join(risk["warnings"]) + "\n\n🛑 系統已自動暫停選股"
            for uid in user_ids:
                try:
                    await bot.send_message(chat_id=uid, text=msg, parse_mode="Markdown")
                except:
                    pass

    async def do_daily_paper_trading_task():
        """✅ 模擬交易：每日自動評估持倉 + 建立新倉"""
        if not is_trading_day():
            return
        from telegram import Bot
        bot = Bot(token=bot_token)
        try:
            report = run_daily_paper_trading(claude_client)
            for uid in user_ids:
                try:
                    await bot.send_message(chat_id=uid, text=report, parse_mode="Markdown")
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"模擬交易執行失敗: {e}")

    async def do_daily_signal_scan():
        """✅ 新功能：每日收盤後自動 AI 信號掃描 + 評估"""
        if not is_trading_day():
            return
        from telegram import Bot
        bot = Bot(token=bot_token)
        try:
            report = run_daily_signal_scan(claude_client)
            for uid in user_ids:
                try:
                    await bot.send_message(chat_id=uid, text=report, parse_mode="Markdown")
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"每日信號掃描失敗: {e}")

    async def do_weekly_learning():
        from telegram import Bot
        bot = Bot(token=bot_token)
        # 交易週報
        report = weekly_self_learning(claude_client)
        for uid in user_ids:
            try:
                await bot.send_message(chat_id=uid, text=report)
            except Exception:
                pass
        # ✅ 新增：信號系統自學習（自動調整門檻）
        try:
            signal_report = run_signal_self_learning(claude_client)
            for uid in user_ids:
                try:
                    await bot.send_message(chat_id=uid, text=signal_report, parse_mode="Markdown")
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"信號自學習失敗: {e}")
        # ✅ 技能自學習：評估技能效果，讓AI越來越聰明
        try:
            skill_report = run_skill_self_learning(claude_client)
            for uid in user_ids:
                try:
                    await bot.send_message(chat_id=uid, text=skill_report, parse_mode="Markdown")
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"技能自學習失敗: {e}")

    schedule.every().day.at("15:10").do(lambda: asyncio.run(do_daily_update()))
    schedule.every().day.at("15:35").do(lambda: asyncio.run(do_daily_report()))
    schedule.every().day.at("15:40").do(lambda: asyncio.run(do_daily_signal_scan()))  # ✅ 新增
    schedule.every().day.at("15:45").do(lambda: asyncio.run(do_daily_paper_trading_task()))  # ✅ 模擬交易
    schedule.every(15).minutes.do(lambda: asyncio.run(do_alert_check()))
    schedule.every().day.at("21:00").do(lambda: asyncio.run(do_weekly_learning()))  # ✅ 改為每天推送學習報告
    schedule.every().day.at("08:30").do(
        lambda: threading.Thread(target=run_open_alert, args=(bot_token, user_ids), daemon=True).start()
    )
    schedule.every().day.at("15:30").do(
        lambda: threading.Thread(target=run_close_alert, args=(bot_token, user_ids), daemon=True).start()
    )

    logger.info("排程器啟動：15:10更新 | 15:35報告 | 15:40信號掃描 | 每15分鐘監控 | 週日自學習")
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

    init_db()
    threading.Thread(
        target=run_scheduler,
        args=(TELEGRAM_TOKEN, ALLOWED_USER_IDS),
        daemon=True
    ).start()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    cmds = [
        ("start",    cmd_start),   ("help",     cmd_start),
        ("chat",     cmd_chat),    ("clear",    cmd_clear),
        ("price",    cmd_price),   ("analyze",  cmd_analyze),
        ("screen",   cmd_screen),  ("macro",    cmd_macro),
        ("watch",    cmd_watch),   ("unwatch",  cmd_unwatch),
        ("list",     cmd_list),    ("buy",      cmd_buy),
        ("sell",     cmd_sell),    ("portfolio",cmd_portfolio),
        ("check",    cmd_check),   ("calc",     cmd_calc),
        ("winrate",  cmd_winrate), ("advice",   cmd_advice),
        ("tradelog", cmd_tradelog),("stats",    cmd_stats),
        ("risk",     cmd_risk),    ("riskset",  cmd_riskset),
        ("mktcheck", cmd_mktcheck),("backtest", cmd_backtest),
        ("teach",    cmd_teach),   ("rules",    cmd_rules),
        ("delrule",  cmd_delrule), ("learning", cmd_learning),
        ("db",       cmd_db),      ("dbinit",   cmd_dbinit),
        ("stocks",   cmd_stocks),  ("crawl",    cmd_crawl),
        ("update",   cmd_update),  ("report",   cmd_report),
        ("score",    cmd_score),   ("news",     cmd_news),
        ("scan",     cmd_scan),    ("signals",  cmd_signals),
    ]
    for name, handler in cmds:
        app.add_handler(CommandHandler(name, handler))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_text
    ))

    logger.info("🚀 量化師 Bot 啟動成功")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()


# ══════════════════════════════════════════════════════
# 模擬交易指令
# ══════════════════════════════════════════════════════

async def cmd_paper_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看模擬持倉"""
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text(get_paper_portfolio(), parse_mode="Markdown")

async def cmd_paper_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看模擬績效報告"""
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text(get_paper_report(), parse_mode="Markdown")

async def cmd_paper_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看/修改模擬設定"""
    if not is_authorized(update.effective_user.id): return
    text = update.message.text.strip().split()
    args = [p for p in text if p not in ["模擬設定", "paper設定"]]
    if len(args) < 2:
        await update.message.reply_text(get_paper_config_summary(), parse_mode="Markdown")
        return
    try:
        val = float(args[1]) if '.' in args[1] else int(args[1])
        await update.message.reply_text(update_paper_config(args[0], val))
    except Exception as e:
        await update.message.reply_text(f"⚠️ 設定失敗：{e}")

async def cmd_paper_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """手動執行一次模擬交易"""
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text("🎮 執行模擬交易中...")
    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            report = executor.submit(run_daily_paper_trading, claude_client).result(timeout=120)
        await update.message.reply_text(report, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ 失敗：{e}")

# ══════════════════════════════════════════════════════
# 技能庫指令
# ══════════════════════════════════════════════════════

async def cmd_skills_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看技能庫"""
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text(list_skills(), parse_mode="Markdown")

async def cmd_skill_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看技能詳細內容"""
    if not is_authorized(update.effective_user.id): return
    text = update.message.text.strip().split()
    args = [p for p in text if p not in ["技能", "skill查看"]]
    if not args:
        await update.message.reply_text("📌 請輸入技能名稱\n範例：`技能 momentum`", parse_mode="Markdown")
        return
    await update.message.reply_text(get_skill_detail(args[0]), parse_mode="Markdown")

async def cmd_add_skill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """新增自定義技能"""
    if not is_authorized(update.effective_user.id): return
    text = update.message.text.strip()
    parts = text.split(maxsplit=2)
    args = [p for p in parts if p not in ["新增技能"]]
    if len(args) < 2:
        await update.message.reply_text(
            "📌 *新增技能格式*\n"
            "`新增技能 技能名稱 策略說明內容`\n\n"
            "例如：\n`新增技能 breakout 突破策略：股價突破前高+量能放大3倍時進場，停損設在突破點下方2%`",
            parse_mode="Markdown"
        )
        return
    name = args[0]
    content = args[1] if len(args) > 1 else ""
    await update.message.reply_text(
        add_custom_skill(name, f"用戶自定義技能：{name}", content)
    )


# ══════════════════════════════════════════════════════
# 技能獵人（Skill Hunter）指令
# ══════════════════════════════════════════════════════

async def cmd_learn_skill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """AI 自動搜尋並學習新技能"""
    if not is_authorized(update.effective_user.id): return
    text  = update.message.text.strip()
    # 去除觸發詞
    for kw in ["幫我學習", "學習技能", "搜尋技能", "learn"]:
        text = text.replace(kw, "").strip()
    if not text:
        await update.message.reply_text(
            "📌 *使用方式：*\n`幫我學習 [你的需求]`\n\n"
            "💡 *例如：*\n"
            "• `幫我學習下載 YouTube 影片`\n"
            "• `幫我學習爬取網頁資料`\n"
            "• `幫我學習傳送電子郵件`\n"
            "• `幫我學習 PDF 處理`",
            parse_mode="Markdown"
        )
        return
    await update.message.reply_text(f"🔍 正在搜尋「{text[:30]}」相關技能，請稍候...")
    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            result = executor.submit(
                learn_skill_from_request, text, claude_client
            ).result(timeout=60)
        await update.message.reply_text(result, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ 搜尋失敗：{e}")


async def cmd_install_skill_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """直接從 GitHub URL 安裝技能"""
    if not is_authorized(update.effective_user.id): return
    text = update.message.text.strip()
    for kw in ["安裝技能", "install技能"]:
        text = text.replace(kw, "").strip()
    if not text or "github.com" not in text:
        await update.message.reply_text(
            "📌 *使用方式：*\n`安裝技能 [GitHub URL]`\n\n"
            "💡 *例如：*\n`安裝技能 https://github.com/user/repo`",
            parse_mode="Markdown"
        )
        return
    url = text.split()[0]
    await update.message.reply_text(f"📥 正在安裝技能，請稍候...")
    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            result = executor.submit(install_skill_from_url, url).result(timeout=30)
        await update.message.reply_text(result, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ 安裝失敗：{e}")


async def cmd_uninstall_skill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """移除已安裝的技能"""
    if not is_authorized(update.effective_user.id): return
    text = update.message.text.strip()
    for kw in ["移除技能", "刪除技能"]:
        text = text.replace(kw, "").strip()
    if not text:
        await update.message.reply_text("📌 請輸入要移除的技能名稱\n範例：`移除技能 video_download`", parse_mode="Markdown")
        return
    await update.message.reply_text(uninstall_skill(text.split()[0]), parse_mode="Markdown")


async def cmd_set_skillsmp_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """設定 SkillsMP API Key"""
    if not is_authorized(update.effective_user.id): return
    text = update.message.text.strip()
    for kw in ["設定SkillsMP金鑰", "設定skillsmp", "skillsmpkey"]:
        text = text.replace(kw, "").strip()
    if not text or not text.startswith("sk_"):
        await update.message.reply_text(
            "📌 *設定 SkillsMP API Key*\n\n"
            "前往 https://skillsmp.com/docs/api 取得金鑰\n"
            "格式：`設定SkillsMP金鑰 sk_live_xxxxx`",
            parse_mode="Markdown"
        )
        return
    await update.message.reply_text(set_skillsmp_key(text.split()[0]), parse_mode="Markdown")


async def cmd_all_skills(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """列出所有技能（含 AI 自動學習的）"""
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text(list_all_skills(), parse_mode="Markdown")
