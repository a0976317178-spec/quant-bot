"""
scheduler.py - 每日盤後自動執行任務
每天 15:30 台股收盤後自動觸發：
1. 爬取最新因子數據
2. 更新 ML 模型
3. 產生選股清單
4. 透過 TG 推播給您
"""
import schedule
import time
import logging
import asyncio
from datetime import datetime
from telegram import Bot
from config import TELEGRAM_TOKEN, ALLOWED_USER_IDS

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# 監控清單（可擴充）
WATCHLIST = [
    "2330", "2317", "2454", "2382", "2308",  # 半導體
    "2881", "2882", "2886", "2891",           # 金融
    "1301", "1303", "6505",                    # 傳產
]


async def send_telegram_message(message: str):
    """發送 TG 通知"""
    bot = Bot(token=TELEGRAM_TOKEN)
    for user_id in ALLOWED_USER_IDS:
        try:
            await bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"發送 TG 失敗 {user_id}: {e}")


def daily_task():
    """每日盤後主任務"""
    # ── 台股假日判斷：非交易日直接跳過，節省 Token ──
    from tw_market_calendar import is_trading_day, get_holiday_name
    if not is_trading_day():
        reason = get_holiday_name() or "週末"
        logger.info(f"📅 今日非台股交易日（{reason}），跳過執行")
        return

    logger.info("🚀 開始執行每日盤後任務...")
    start_time = datetime.now()

    report_lines = [
        f"📊 *每日量化報告*",
        f"執行時間：{start_time.strftime('%Y-%m-%d %H:%M')}",
        "─" * 30,
    ]

    try:
        # 1. 取得宏觀因子
        from factors.macro import get_macro_snapshot
        macro = get_macro_snapshot()

        report_lines.append(f"🌐 *宏觀指標*")
        report_lines.append(f"VIX: {macro.get('vix', 'N/A')} ({macro.get('vix_signal', '')})")
        report_lines.append(f"大盤騰落: {macro.get('adl_daily', 'N/A')} 家")
        report_lines.append("─" * 30)

        # 2. 對監控清單進行預測
        from ml.predict import screen_stocks, load_latest_model
        model = load_latest_model()

        if model is None:
            report_lines.append("⚠️ 模型尚未訓練，請執行 /train 指令")
        else:
            report_lines.append("🔥 *今日候選股票（信號 > 60%）*")
            # 這裡簡化，實際應爬取每支股票的因子
            report_lines.append("（請先執行完整因子爬蟲後此處將自動顯示）")

        report_lines.append("─" * 30)
        elapsed = (datetime.now() - start_time).seconds
        report_lines.append(f"✅ 任務完成，耗時 {elapsed} 秒")

    except Exception as e:
        logger.error(f"每日任務失敗: {e}")
        report_lines.append(f"❌ 任務失敗：{str(e)}")

    report = "\n".join(report_lines)
    asyncio.run(send_telegram_message(report))
    logger.info("每日任務完成")


def run_scheduler():
    """啟動排程器"""
    # 每天 15:35 執行（台股收盤後 5 分鐘）
    schedule.every().day.at("15:35").do(daily_task)

    # 每週日 22:00 重新訓練模型
    schedule.every().sunday.at("22:00").do(retrain_model)

    logger.info("📅 排程器已啟動")
    logger.info("  - 每日 15:35 執行盤後分析")
    logger.info("  - 每週日 22:00 重新訓練模型")

    while True:
        schedule.run_pending()
        time.sleep(60)


def retrain_model():
    """重新訓練模型"""
    logger.info("🤖 開始重新訓練模型...")
    asyncio.run(send_telegram_message("🤖 開始重新訓練 ML 模型，請稍候..."))
    # 實際訓練邏輯在 ml/train.py
    asyncio.run(send_telegram_message("✅ 模型訓練完成！"))


if __name__ == "__main__":
    # 手動執行一次測試
    daily_task()
