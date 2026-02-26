#!/bin/bash
# ============================================================
# deploy.sh - 量化師 Bot 一鍵部署腳本
# 在 VPS 上執行：bash deploy.sh
# ============================================================

set -e   # 任何錯誤就停止

# ── 設定你的專案路徑（請確認這是你的實際路徑）──
PROJECT_DIR="$HOME/quant-bot"

echo "====================================="
echo " 量化師 Bot 部署腳本"
echo " 專案目錄：$PROJECT_DIR"
echo "====================================="

# ── 確認目錄存在 ──
if [ ! -d "$PROJECT_DIR" ]; then
    echo "❌ 找不到 $PROJECT_DIR，請確認路徑正確"
    echo "   嘗試：find ~ -name 'main.py' 2>/dev/null | head -5"
    exit 1
fi

cd "$PROJECT_DIR"
echo "✅ 進入 $PROJECT_DIR"

# ── 建立必要資料夾 ──
mkdir -p news memory data
echo "✅ 建立資料夾：news/ memory/ data/"

# ── 建立 news/__init__.py（讓 Python 識別為套件）──
if [ ! -f "news/__init__.py" ]; then
    touch news/__init__.py
    echo "✅ 建立 news/__init__.py"
fi

# ── 複製修復的檔案 ──
# （假設你已經把修復的檔案上傳到 ~/uploads/ 資料夾）

UPLOAD_DIR="$HOME/uploads"

copy_if_exists() {
    local src="$1"
    local dst="$2"
    if [ -f "$src" ]; then
        cp "$src" "$dst"
        echo "✅ 複製：$src → $dst"
    else
        echo "⚠️  找不到：$src（請手動複製）"
    fi
}

# 修復的核心檔案
copy_if_exists "$UPLOAD_DIR/analyzer.py"        "factors/analyzer.py"
copy_if_exists "$UPLOAD_DIR/engine.py"          "backtest/engine.py"

# 新功能模組
copy_if_exists "$UPLOAD_DIR/ai_self_learning.py" "memory/ai_self_learning.py"
copy_if_exists "$UPLOAD_DIR/tw_stock_news.py"    "news/tw_stock_news.py"

# ── 修改 main.py（自動 patch）──
echo ""
echo "📝 開始 patch main.py..."

MAIN="$PROJECT_DIR/main.py"

# 備份原始 main.py
cp "$MAIN" "${MAIN}.bak.$(date +%Y%m%d_%H%M%S)"
echo "✅ 已備份 main.py"

# Patch 1：新增 import（如果還沒有的話）
if ! grep -q "from news.tw_stock_news" "$MAIN"; then
    # 找到 from ml.self_learning import 那行，在後面加
    sed -i '/from ml.self_learning import/a from news.tw_stock_news import run_daily_news_summary\nfrom memory.ai_self_learning import run_daily_self_learning, get_self_learning_summary' "$MAIN"
    echo "✅ Patch 1：新增 import"
else
    echo "⏭️  Patch 1：import 已存在，略過"
fi

# Patch 2：在排程器中新增任務
if ! grep -q "do_daily_news" "$MAIN"; then
    # 找到 schedule.every().sunday 那行，在後面加兩個新排程
    sed -i '/schedule.every().sunday.at/a\    schedule.every().day.at("16:00").do(lambda: asyncio.run(do_daily_news()))\n    schedule.every().day.at("21:00").do(lambda: asyncio.run(do_self_learning()))' "$MAIN"
    echo "✅ Patch 2：排程器新增 16:00 新聞 + 21:00 自學習"
else
    echo "⏭️  Patch 2：排程已存在，略過"
fi

# ── 安裝依賴 ──
echo ""
echo "📦 檢查 Python 依賴..."
pip install requests schedule --break-system-packages -q 2>/dev/null || \
pip install requests schedule -q 2>/dev/null || \
echo "⚠️  pip 安裝失敗，請手動：pip install requests schedule"
echo "✅ 依賴確認完成"

echo ""
echo "====================================="
echo " 部署完成！"
echo "====================================="
echo ""
echo "接下來請執行重啟指令（見下方）"
