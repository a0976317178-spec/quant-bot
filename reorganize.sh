#!/bin/bash
# ============================================================
# reorganize.sh - 把根目錄的檔案移到正確的子資料夾
# 在 VPS 上執行：
#   cd ~/quant-bot
#   bash reorganize.sh
# ============================================================

set -e
echo "======================================"
echo " 開始整理檔案結構..."
echo "======================================"

# ── 建立缺少的資料夾和 __init__.py ──
mkdir -p news report
for dir in backtest database factors memory ml portfolio risk report news; do
    touch "$dir/__init__.py" 2>/dev/null || true
done
echo "✅ 資料夾確認完成"

# ── 移動函式：用 git mv，若檔案不存在就略過 ──
gmv() {
    local src="$1"
    local dst="$2"
    if [ -f "$src" ]; then
        git mv "$src" "$dst"
        echo "✅ $src → $dst"
    else
        echo "⏭️  找不到 $src，略過"
    fi
}

echo ""
echo "📁 factors/"
gmv "analyzer.py"          "factors/analyzer.py"
gmv "chips.py"             "factors/chips.py"
gmv "macro.py"             "factors/macro.py"
gmv "realtime.py"          "factors/realtime.py"
gmv "technical.py"         "factors/technical.py"
gmv "fundamental_score.py" "factors/fundamental_score.py"

echo ""
echo "📁 backtest/"
gmv "engine.py"            "backtest/engine.py"

echo ""
echo "📁 database/"
gmv "db_manager.py"        "database/db_manager.py"
gmv "crawler.py"           "database/crawler.py"
gmv "daily_update.py"      "database/daily_update.py"
gmv "query.py"             "database/query.py"

echo ""
echo "📁 memory/"
gmv "ai_self_learning.py"  "memory/ai_self_learning.py"
gmv "daily_learning.py"    "memory/daily_learning.py"
gmv "rules_manager.py"     "memory/rules_manager.py"
gmv "trade_log.py"         "memory/trade_log.py"

echo ""
echo "📁 ml/"
gmv "predict.py"           "ml/predict.py"
gmv "train.py"             "ml/train.py"
gmv "self_learning.py"     "ml/self_learning.py"

echo ""
echo "📁 portfolio/"
gmv "tracker.py"           "portfolio/tracker.py"

echo ""
echo "📁 risk/"
gmv "manager.py"           "risk/manager.py"

echo ""
echo "📁 report/"
gmv "daily_report.py"      "report/daily_report.py"

echo ""
echo "📁 news/"
gmv "tw_stock_news.py"     "news/tw_stock_news.py"

echo ""
echo "📋 根目錄保留的檔案（不移動）"
echo "   main.py / config.py / main_patch.py / scheduler.py"
echo "   requirements.txt / .env.example / README.md"

# ── commit & push ──
echo ""
echo "======================================"
echo " 提交並推送到 GitHub..."
echo "======================================"
git add -A
git commit -m "refactor: 整理檔案結構，移入對應子資料夾"
git push origin main

echo ""
echo "======================================"
echo "✅ 完成！檔案已整理並推送到 GitHub"
echo "======================================"
echo ""
echo "下一步重啟 Bot："
echo "  sudo systemctl restart quant-bot   # systemd"
echo "  pm2 restart quant-bot              # pm2"
