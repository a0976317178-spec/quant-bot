"""
main.py - 台股量化交易 Bot（完整版）
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
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from config import TELEGRAM_TOKEN, ALLOWED_USER_IDS, ANTHROPIC_API_KEY, CLAUDE_FAST_MODEL, CLAUDE_SMART_MODEL
from memory.rules_manager import add_rule, delete_rule, list_rules, load_history, save_history, clear_history, get_rules_as_prompt
from memory.daily_learning import add_to_watchlist, remove_from_watchlist, list_watchlist, get_recent_learnings, daily_learning_task
from portfolio.tracker import add_position, remove_position, list_portfolio, check_portfolio_alerts
from risk.manager import get_risk_summary, check_market_risk, calc_position_size, update_risk_param

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
# 所有指令處理器
# ══════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("您沒有使用權限")
        return
    msg = (
        "🤖 *量化師* — 台股 AI 交易助理\n"
        "══════════════════════════\n\n"
        "💬 *對話*\n"
        "  `/chat` `對話`   問任何投資問題\n"
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
        "  `/sell`  `賣出 2330 1020`     平倉結算\n"
        "  `/portfolio`    `持股`        查看持倉\n"
        "  `/check`        `檢查`        手動觸發警報\n"
        "  `/calc 980`     `建倉試算 980` 計算張數\n\n"
        "🛡️ *風險控管*\n"
        "  `/risk`         `風控`        查看風控設定\n"
        "  `/riskset`      `風控設定 停損 0.07`\n"
        "  `/mktcheck`     `市場風險`    檢查大盤\n\n"
        "📈 *回測*\n"
        "  `/backtest`     `回測 2330 2022`\n\n"
        "🧠 *學習規則*\n"
        "  `/teach`  `新增規則 外資連買3天才進場`\n"
        "  `/rules`        `規則`        查看規則\n"
        "  `/delrule 1`    `刪除規則 1`\n"
        "  `/learning`     `學習記錄`\n"
        "  `/train`        `訓練模型`\n\n"
        "🗄️ *資料庫*\n"
        "  `/dbinit`  `初始化`   建立資料庫（首次）\n"
        "  `/stocks`  `更新清單` 更新股票清單\n"
        "  `/crawl 2020` `爬取 2020` 下載歷史資料\n"
        "  `/db`      `資料庫`   查看資料狀態\n\n"
        "══════════════════════════\n"
        "💡 直接輸入*4位數代號*（如 2330）即查股價\n"
        "💡 輸入任何問題直接與 AI 對話"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")
    return


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
            f"請從量價面、籌碼面、基本面、宏觀面四個維度分析，"
            f"並給出進場價位、停損價位、目標價、風險提示。"
        )
        reply = await ask_claude(update.effective_user.id, message, use_smart=True)
        await update.message.reply_text(reply)
    except Exception as e:
        await update.message.reply_text(f"分析失敗：{str(e)}")


async def cmd_xuangu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text("掃描監控清單中，請稍候...")
    try:
        from factors.realtime import get_stock_quote
        from memory.daily_learning import load_watchlist
        watchlist = load_watchlist()
        results = []
        for stock_id in watchlist:
            quote = get_stock_quote(stock_id)
            if quote and quote.get("close", 0) > 0:
                results.append(f"{stock_id} {quote.get('name','')} ${quote.get('close')} ({quote.get('change_pct',0):+.2f}%)")
        if results:
            summary = "\n".join(results)
            msg = f"以下是今日監控股票，請找出最值得關注的前3支並說明原因：\n\n{summary}"
            reply = await ask_claude(update.effective_user.id, msg, use_smart=True)
            await update.message.reply_text(f"今日監控股票\n\n{summary}\n\n{reply}")
        else:
            await update.message.reply_text("無法取得股價資料，請稍後再試")
    except Exception as e:
        await update.message.reply_text(f"掃描失敗：{e}")


async def cmd_hongguan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text("取得宏觀數據中...")
    try:
        from factors.macro import get_macro_snapshot
        data = get_macro_snapshot()
        msg = (
            f"宏觀指標快照\n\n"
            f"VIX恐慌指數：{data.get('vix', 'N/A')}\n"
            f"市場信號：{data.get('vix_signal', 'N/A')}\n"
            f"上漲家數：{data.get('advancing', 'N/A')}\n"
            f"下跌家數：{data.get('declining', 'N/A')}\n"
            f"ADL騰落值：{data.get('adl_daily', 'N/A')}\n\n"
        )
        interp = await ask_claude(update.effective_user.id,
            f"VIX={data.get('vix')}，大盤騰落={data.get('adl_daily')}家，請用2~3句話解讀市場環境並給出操作建議。")
        await update.message.reply_text(msg + interp)
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
    text = update.message.text.strip()
    parts = text.split()
    args = [p for p in parts if p not in ["買進", "buy", "進場", "/buy"]]
    if len(args) < 3:
        await update.message.reply_text(
            "格式：買進 <代號> <張數> <進場價>\n"
            "範例：買進 2330 1 980\n\n"
            "自訂停損停利：\n"
            "買進 2330 1 980 停損7 停利15"
        )
        return
    try:
        stock_id = args[0]
        shares = int(args[1])
        entry_price = float(args[2])
        stop_loss_pct = 0.05
        target_pct = 0.10
        for p in args[3:]:
            if p.startswith("停損"):
                stop_loss_pct = float(p.replace("停損", "")) / 100
            elif p.startswith("停利"):
                target_pct = float(p.replace("停利", "")) / 100
        result = add_position(stock_id, entry_price, shares, stop_loss_pct, target_pct)
        await update.message.reply_text(result)
    except Exception as e:
        await update.message.reply_text(f"格式錯誤：{e}\n範例：買進 2330 1 980")


async def cmd_machu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    text = update.message.text.strip()
    parts = text.split()
    args = [p for p in parts if p not in ["賣出", "sell", "出場", "平倉", "/sell"]]
    if not args:
        await update.message.reply_text("格式：賣出 <代號> <出場價>\n範例：賣出 2330 1020")
        return
    stock_id = args[0]
    exit_price = float(args[1]) if len(args) > 1 else None
    await update.message.reply_text(remove_position(stock_id, exit_price))


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
    text = update.message.text.strip()
    parts = text.split()
    args = [p for p in parts if p not in ["建倉試算", "試算", "calc", "/calc"]]
    if not args or not args[0].replace(".", "").isdigit():
        await update.message.reply_text("格式：建倉試算 <股價>\n範例：建倉試算 980")
        return
    price = float(args[0])
    r = calc_position_size(price)
    await update.message.reply_text(
        f"建倉試算（股價 ${price}）\n\n"
        f"建議張數：{r['suggested_lots']} 張\n"
        f"投入金額：${r['actual_invest']:,.0f}\n"
        f"最大虧損：${r['max_loss_amount']:,.0f}\n"
        f"停損價：${r['stop_loss_price']}\n"
        f"目標價：${r['take_profit_price']}\n\n"
        f"以上依據您的風控設定計算"
    )


async def cmd_fengkong(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text(get_risk_summary())


async def cmd_fengkong_shezhi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    text = update.message.text.strip()
    parts = text.split()
    args = [p for p in parts if p not in ["風控設定", "/riskset"]]
    if len(args) < 2:
        await update.message.reply_text(
            "格式：風控設定 <參數> <數值>\n\n"
            "可設定參數：\n"
            "停損 0.05  (5%)\n"
            "停利 0.10  (10%)\n"
            "總資金 1000000\n"
            "最大持股 5\n"
            "VIX門檻 30"
        )
        return
    param_map = {"停損": "stop_loss_pct", "停利": "take_profit_pct",
                 "總資金": "total_capital", "最大持股": "max_positions",
                 "VIX門檻": "pause_when_vix_above"}
    key = param_map.get(args[0], args[0])
    try:
        value = float(args[1])
        await update.message.reply_text(update_risk_param(key, value))
    except Exception as e:
        await update.message.reply_text(f"設定失敗：{e}")


async def cmd_shichang_fengxian(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text("檢查市場風險中...")
    result = check_market_risk()
    if result["warnings"]:
        msg = "\n".join(result["warnings"])
        if result["should_pause"]:
            msg += "\n\n系統已自動暫停選股信號"
        else:
            msg += "\n\n請注意風險，謹慎操作"
    else:
        msg = "市場風險正常，可正常操作"
    await update.message.reply_text(msg)


async def cmd_huice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    text = update.message.text.strip()
    parts = text.split()
    args = [p for p in parts if p not in ["回測", "backtest", "歷史回測", "/backtest"]]
    if not args:
        await update.message.reply_text(
            "請輸入股票代號，例如：\n\n"
            "回測 2330           回測台積電近3年\n"
            "回測 2330 2022      從2022年至今\n"
            "回測 2330 2022 2024 指定期間"
        )
        return
    stock_id = args[0]
    start_date = None
    end_date = None
    if len(args) >= 2:
        yr = args[1]
        start_date = f"{yr}-01-01" if len(yr) == 4 else yr
    if len(args) >= 3:
        yr2 = args[2]
        end_date = f"{yr2}-12-31" if len(yr2) == 4 else yr2
    await update.message.reply_text(f"回測 {stock_id} 中，請稍候...")

    def run():
        from backtest.engine import run_backtest, format_backtest_report
        result = run_backtest(stock_id=stock_id, start_date=start_date, end_date=end_date)
        return format_backtest_report(result)

    with concurrent.futures.ThreadPoolExecutor() as executor:
        report = executor.submit(run).result(timeout=180)
    await update.message.reply_text(report)


async def cmd_xinzeng_guize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("請輸入規則，例如：/teach 外資連買3天以上才進場")
        return
    await update.message.reply_text(add_rule(" ".join(context.args)))


async def cmd_guize_qingdan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text(list_rules())


async def cmd_shanchu_guize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("請輸入規則編號，例如：/delrule 1")
        return
    await update.message.reply_text(delete_rule(int(context.args[0])))


async def cmd_xuexi_jilu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text(get_recent_learnings(days=7))


async def cmd_xunlian(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text("開始重新訓練 ML 模型，完成後會通知您...")


async def cmd_shujuku(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text("查詢資料庫狀態中...")
    try:
        from database.db_manager import get_db_stats
        stats = get_db_stats()
        msg = (
            f"資料庫狀態\n\n"
            f"股票清單：{stats.get('stocks', 0):,} 支\n"
            f"每日股價：{stats.get('daily_price', 0):,} 筆\n"
            f"三大法人：{stats.get('institutional', 0):,} 筆\n"
            f"月營收：{stats.get('monthly_revenue', 0):,} 筆\n"
            f"宏觀指標：{stats.get('macro_daily', 0):,} 筆\n"
            f"因子快取：{stats.get('factor_cache', 0):,} 筆\n"
            f"股價資料範圍：{stats.get('price_date_range', '無資料')}\n"
            f"資料庫大小：{stats.get('db_size_mb', 0)} MB"
        )
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"查詢失敗：{e}\n請先執行「初始化」建立資料庫")


async def cmd_chushihua(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    try:
        from database.db_manager import init_db
        init_db()
        await update.message.reply_text(
            "資料庫初始化完成！\n\n"
            "建議執行順序：\n"
            "1. 更新清單（取得所有股票代號）\n"
            "2. 爬取 2020（爬取歷史股價）\n"
            "3. 資料庫（查看資料狀態）"
        )
    except Exception as e:
        await update.message.reply_text(f"初始化失敗：{e}")


async def cmd_gengxin_qingdan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text("正在從證交所更新股票清單，請稍候...")
    def run():
        from database.crawler import fetch_stock_list, save_stock_list
        stocks = fetch_stock_list()
        save_stock_list(stocks)
        return len(stocks)
    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            result = executor.submit(run).result(timeout=120)
        await update.message.reply_text(f"股票清單更新完成！共 {result} 支股票")
    except Exception as e:
        await update.message.reply_text(f"更新失敗：{e}")


async def cmd_paqu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    text = update.message.text.strip()
    parts = text.split()
    args = [p for p in parts if p not in ["爬取", "下載資料", "crawl", "/crawl"]]
    year = int(args[0]) if args and args[0].isdigit() else 2020
    await update.message.reply_text(
        f"開始爬取 {year} 年至今的歷史股價\n\n"
        f"注意：此任務需數小時，背景執行中\n"
        f"支援斷點續爬，中斷後可重新執行\n"
        f"用「資料庫」查看爬取進度"
    )
    def run_crawl():
        try:
            from database.db_manager import init_db
            from database.crawler import crawl_all_prices, crawl_macro
            init_db()
            crawl_all_prices(start_year=year)
            crawl_macro(days=365)
        except Exception as e:
            logger.error(f"爬取失敗: {e}")
    threading.Thread(target=run_crawl, daemon=True).start()


# ══════════════════════════════════════════════════════
# 智能文字偵測（關鍵字觸發）
# ══════════════════════════════════════════════════════

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    text = update.message.text.strip()
    parts = text.split()
    keyword = parts[0] if parts else ""
    arg = parts[1] if len(parts) > 1 else ""

    if keyword in ["股價", "查股價", "現價", "price"]:
        if arg: context.args = [arg]; await cmd_gujia(update, context)
        else: await update.message.reply_text("請輸入股票代號，例如：股價 2330")
    elif keyword in ["分析", "analyze", "研究", "看看"]:
        if arg: context.args = [arg]; await cmd_fenxi(update, context)
        else: await update.message.reply_text("請輸入股票代號，例如：分析 2330")
    elif keyword in ["加入", "監控", "追蹤", "watch"]:
        if arg: context.args = [arg]; await cmd_jiaru(update, context)
        else: await update.message.reply_text("請輸入股票代號，例如：加入 2330")
    elif keyword in ["移除", "刪除監控", "取消監控", "unwatch"]:
        if arg: context.args = [arg]; await cmd_yichu(update, context)
        else: await update.message.reply_text("請輸入股票代號，例如：移除 2330")
    elif keyword in ["清單", "監控清單", "自選股"]:
        await cmd_qingdan(update, context)
    elif keyword in ["選股", "掃描", "screen"]:
        await cmd_xuangu(update, context)
    elif keyword in ["宏觀", "大盤", "市場", "macro"]:
        await cmd_hongguan(update, context)
    elif keyword in ["新增規則", "加入規則", "記住", "teach"]:
        rule = text[len(keyword):].strip()
        if rule: await update.message.reply_text(add_rule(rule))
        else: await update.message.reply_text("請輸入規則，例如：新增規則 外資連買3天才進場")
    elif keyword in ["規則", "規則清單", "我的規則", "rules"]:
        await cmd_guize_qingdan(update, context)
    elif keyword in ["刪除規則", "移除規則"]:
        if arg and arg.isdigit(): await update.message.reply_text(delete_rule(int(arg)))
        else: await update.message.reply_text("請輸入規則編號，例如：刪除規則 1")
    elif keyword in ["學習記錄", "學習", "learning"]:
        await cmd_xuexi_jilu(update, context)
    elif keyword in ["資料庫", "資料庫狀態", "db"]:
        await cmd_shujuku(update, context)
    elif keyword in ["初始化", "初始化資料庫"]:
        await cmd_chushihua(update, context)
    elif keyword in ["更新清單", "更新股票", "stocks"]:
        await cmd_gengxin_qingdan(update, context)
    elif keyword in ["爬取", "下載資料", "crawl"]:
        context.args = [arg] if arg else []
        await cmd_paqu(update, context)
    elif keyword in ["清除", "清除記憶", "重置", "clear"]:
        await cmd_qingchu(update, context)
    elif keyword in ["說明", "指令", "幫助", "help", "選單"]:
        await cmd_start(update, context)
    elif keyword in ["買進", "buy", "進場"]:
        await cmd_maijin(update, context)
    elif keyword in ["賣出", "sell", "出場", "平倉"]:
        await cmd_machu(update, context)
    elif keyword in ["持股", "持股清單", "portfolio", "倉位"]:
        await cmd_chicang(update, context)
    elif keyword in ["檢查", "check", "警報", "確認持股"]:
        await cmd_jiancha(update, context)
    elif keyword in ["建倉試算", "試算", "calc"]:
        await cmd_jianyi_zhangshui(update, context)
    elif keyword in ["風控", "risk", "風險設定"]:
        await cmd_fengkong(update, context)
    elif keyword in ["風控設定"]:
        await cmd_fengkong_shezhi(update, context)
    elif keyword in ["市場風險", "風險檢查", "mktcheck"]:
        await cmd_shichang_fengxian(update, context)
    elif keyword in ["回測", "backtest", "歷史回測"]:
        await cmd_huice(update, context)
    elif text.isdigit() and len(text) == 4:
        context.args = [text]; await cmd_gujia(update, context)
    else:
        reply = await ask_claude(update.effective_user.id, text)
        await update.message.reply_text(reply)


# ══════════════════════════════════════════════════════
# 排程器
# ══════════════════════════════════════════════════════

def run_scheduler(bot_token: str, user_ids: list):
    import schedule
    import time

    async def send_report():
        from telegram import Bot
        bot = Bot(token=bot_token)
        report = await daily_learning_task()
        for uid in user_ids:
            try:
                await bot.send_message(chat_id=uid, text=report)
            except Exception as e:
                logger.error(f"推播失敗 {uid}: {e}")

    async def check_alerts():
        from telegram import Bot
        bot = Bot(token=bot_token)
        alerts = await check_portfolio_alerts()
        for alert in alerts:
            for uid in user_ids:
                try:
                    await bot.send_message(chat_id=uid, text=alert["message"])
                except: pass
        risk = check_market_risk()
        if risk["should_pause"] and risk["warnings"]:
            for uid in user_ids:
                try:
                    msg = "\n".join(risk["warnings"]) + "\n\n系統已自動暫停選股"
                    await bot.send_message(chat_id=uid, text=msg)
                except: pass

    def daily_job():
        asyncio.run(send_report())

    def alert_job():
        now = datetime.now()
        if 9 <= now.hour < 13 or (now.hour == 13 and now.minute <= 30):
            asyncio.run(check_alerts())

    schedule.every().day.at("15:35").do(daily_job)
    schedule.every(15).minutes.do(alert_job)
    logger.info("排程器啟動：每日15:35推播 | 盤中每15分鐘監控持股")
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
    app.add_handler(CommandHandler("risk",      cmd_fengkong))
    app.add_handler(CommandHandler("riskset",   cmd_fengkong_shezhi))
    app.add_handler(CommandHandler("mktcheck",  cmd_shichang_fengxian))
    app.add_handler(CommandHandler("backtest",  cmd_huice))
    app.add_handler(CommandHandler("teach",     cmd_xinzeng_guize))
    app.add_handler(CommandHandler("rules",     cmd_guize_qingdan))
    app.add_handler(CommandHandler("delrule",   cmd_shanchu_guize))
    app.add_handler(CommandHandler("learning",  cmd_xuexi_jilu))
    app.add_handler(CommandHandler("train",     cmd_xunlian))
    app.add_handler(CommandHandler("db",        cmd_shujuku))
    app.add_handler(CommandHandler("dbinit",    cmd_chushihua))
    app.add_handler(CommandHandler("stocks",    cmd_gengxin_qingdan))
    app.add_handler(CommandHandler("crawl",     cmd_paqu))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("量化交易 Bot 已啟動！")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
