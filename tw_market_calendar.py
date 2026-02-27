"""
==========================================================
  台灣股市開盤判斷模組 - 2026年完整版
  Taiwan Stock Market Calendar 2026
==========================================================
  開盤時間：週一至週五 09:00 ~ 13:30 (台灣時間 UTC+8)
  資料來源：行政院人事行政總處 + 台灣證券交易所(TWSE)

  ⚠️  補班/補課日 = 正常開盤（已特別標記）
  ⚠️  每年底請至 TWSE 官網確認最新異動：
      https://www.twse.com.tw/zh/holidaySchedule/holidaySchedule
==========================================================
"""

from datetime import date, datetime
import pytz

# ──────────────────────────────────────────────────────────
#  2026 年台灣股市【休市日】完整清單
#  格式: date(年, 月, 日): "說明"
# ──────────────────────────────────────────────────────────
TW_HOLIDAYS_2026: dict[date, str] = {

    # ── 元旦 ──────────────────────────────────────────────
    date(2026, 1,  1): "元旦",

    # ── 春節 (農曆除夕～初三 + 調整假) ─────────────────────
    # 2026 農曆新年 = 2月17日 (馬年)
    date(2026, 2, 16): "春節（農曆除夕）",
    date(2026, 2, 17): "春節（初一）",
    date(2026, 2, 18): "春節（初二）",
    date(2026, 2, 19): "春節（初三）",
    date(2026, 2, 20): "春節彈性放假",    # 視行政院公告調整

    # ── 和平紀念日 ────────────────────────────────────────
    # 2026-02-28 = 週六，補假 → 03-02 (週一)
    date(2026, 3,  2): "和平紀念日補假",

    # ── 兒童節 + 清明節 ────────────────────────────────────
    # 兒童節 04-04 (週六) → 補假 04-03 (週五)
    # 清明節 04-05 (週日) → 補假 04-06 (週一)
    date(2026, 4,  3): "兒童節補假",
    date(2026, 4,  6): "清明節補假",

    # ── 勞動節 ────────────────────────────────────────────
    date(2026, 5,  1): "勞動節",

    # ── 端午節 ────────────────────────────────────────────
    # 農曆5月5日 ≈ 2026-06-20 (週六) → 補假 06-22 (週一)
    date(2026, 6, 22): "端午節補假",

    # ── 中秋節 ────────────────────────────────────────────
    # 農曆8月15日 ≈ 2026-09-30 (週三)
    date(2026, 9, 30): "中秋節",

    # ── 國慶日 ────────────────────────────────────────────
    # 2026-10-10 = 週六 → 補假 10-12 (週一)
    date(2026, 10, 12): "國慶日補假",

    # ── 元旦（跨年）────────────────────────────────────────
    # 2027-01-01 = 週五，有些年底會提早收市，視TWSE公告
    date(2026, 12, 31): "元旦前彈性放假（待確認）",
}

# ──────────────────────────────────────────────────────────
#  2026 年【補班日】= 原本是假日但需上班，股市正常開盤
#  ※ 這些日子即使是週六也要交易
# ──────────────────────────────────────────────────────────
TW_MAKEUP_WORKDAYS_2026: dict[date, str] = {
    date(2026, 2, 14): "春節補班（週六）",   # 視行政院公告
    date(2026, 4,  4): "兒童節/清明補班（週六）",  # 視公告
    date(2026, 6, 20): "端午補班（週六）",    # 視公告
    date(2026, 10, 10): "國慶補班（週六）",   # 視公告
}

# ──────────────────────────────────────────────────────────
#  台灣時區
# ──────────────────────────────────────────────────────────
TW_TZ = pytz.timezone("Asia/Taipei")

# 開盤時間
MARKET_OPEN  = (9,  0)   # 09:00
MARKET_CLOSE = (13, 30)  # 13:30


# ══════════════════════════════════════════════════════════
#  核心判斷函式
# ══════════════════════════════════════════════════════════

def is_trading_day(check_date: date | None = None) -> bool:
    """
    判斷指定日期是否為台股交易日。
    - check_date 不傳則使用今天（台灣時間）
    """
    if check_date is None:
        check_date = datetime.now(TW_TZ).date()

    # 補班日 → 即使週六也開盤
    if check_date in TW_MAKEUP_WORKDAYS_2026:
        return True

    # 週六/週日 → 休市
    if check_date.weekday() >= 5:
        return False

    # 國定假日 → 休市
    if check_date in TW_HOLIDAYS_2026:
        return False

    return True


def is_market_open(check_dt: datetime | None = None) -> bool:
    """
    判斷指定時間點台股是否正在交易中。
    - check_dt 不傳則使用現在（自動轉台灣時間）
    """
    if check_dt is None:
        check_dt = datetime.now(TW_TZ)
    elif check_dt.tzinfo is None:
        check_dt = TW_TZ.localize(check_dt)
    else:
        check_dt = check_dt.astimezone(TW_TZ)

    # 先判斷是否為交易日
    if not is_trading_day(check_dt.date()):
        return False

    # 判斷是否在開盤時間內
    open_time  = check_dt.replace(hour=MARKET_OPEN[0],  minute=MARKET_OPEN[1],  second=0, microsecond=0)
    close_time = check_dt.replace(hour=MARKET_CLOSE[0], minute=MARKET_CLOSE[1], second=0, microsecond=0)

    return open_time <= check_dt < close_time


def get_holiday_name(check_date: date | None = None) -> str | None:
    """
    回傳指定日期的假日名稱，若非假日則回傳 None。
    """
    if check_date is None:
        check_date = datetime.now(TW_TZ).date()

    if check_date in TW_MAKEUP_WORKDAYS_2026:
        return None  # 補班日 = 非假日

    if check_date.weekday() >= 5:
        return "週末"

    return TW_HOLIDAYS_2026.get(check_date, None)


def market_status() -> dict:
    """
    回傳目前市場狀態摘要（方便整合進指標主程式）。

    回傳格式:
    {
        "is_open":        bool,   # 現在是否開盤中
        "is_trading_day": bool,   # 今天是否為交易日
        "holiday_name":   str,    # 假日名稱（None=正常交易日）
        "current_tw_time": str,   # 台灣當前時間
        "should_alert":   bool    # 是否應該發出提醒（=is_open）
    }
    """
    now = datetime.now(TW_TZ)
    today = now.date()
    holiday = get_holiday_name(today)
    trading_day = is_trading_day(today)
    open_now = is_market_open(now)

    return {
        "is_open":         open_now,
        "is_trading_day":  trading_day,
        "holiday_name":    holiday,
        "current_tw_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "should_alert":    open_now,   # ← 指標主邏輯用這個 flag
    }


# ══════════════════════════════════════════════════════════
#  使用範例（直接執行此檔案時顯示）
# ══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 50)
    print("  台股市場狀態檢查")
    print("=" * 50)

    status = market_status()
    print(f"  台灣時間  : {status['current_tw_time']}")
    print(f"  今天交易日: {'✅ 是' if status['is_trading_day'] else '❌ 否'}")
    print(f"  假日原因  : {status['holiday_name'] or '（正常交易日）'}")
    print(f"  現在開盤中: {'✅ 是' if status['is_open'] else '❌ 否'}")
    print(f"  應發出提醒: {'✅ 發送' if status['should_alert'] else '⛔ 跳過，節省Token'}")
    print("=" * 50)

    # ── 2026 全年假日總覽 ──
    print("\n📅 2026 台股休市日清單：")
    import calendar
    for d, name in sorted(TW_HOLIDAYS_2026.items()):
        weekday_name = ["一","二","三","四","五","六","日"][d.weekday()]
        print(f"  {d}（週{weekday_name}）  {name}")

    print("\n🔧 2026 補班日（週六開盤）清單：")
    for d, name in sorted(TW_MAKEUP_WORKDAYS_2026.items()):
        weekday_name = ["一","二","三","四","五","六","日"][d.weekday()]
        print(f"  {d}（週{weekday_name}）  {name}")
