# 量化師 Bot — VPS 部署 & 重啟完整指南

## 第一步：把修復的檔案上傳到 VPS

在你的電腦上，用 scp 把下載的檔案傳上去：

```bash
# 先在 VPS 建一個暫存資料夾
ssh your_user@your_vps_ip "mkdir -p ~/uploads"

# 從你的電腦把修復檔案傳上去（在你電腦上執行）
scp analyzer.py       your_user@your_vps_ip:~/uploads/
scp engine.py         your_user@your_vps_ip:~/uploads/
scp ai_self_learning.py  your_user@your_vps_ip:~/uploads/
scp tw_stock_news.py  your_user@your_vps_ip:~/uploads/
scp deploy.sh         your_user@your_vps_ip:~/uploads/
```

---

## 第二步：在 VPS 上執行部署腳本

```bash
# SSH 進入 VPS
ssh your_user@your_vps_ip

# 執行一鍵部署
bash ~/uploads/deploy.sh
```

---

## 第三步：手動 patch main.py（新增指令和排程函式）

由於 main.py 結構複雜，排程函式需要手動加。
在 VPS 上執行：

```bash
cd ~/quant-bot    # 換成你的實際路徑
nano main.py      # 或用 vim main.py
```

### A. 在 run_scheduler() 函式內，找到這一段：

```python
schedule.every().sunday.at("21:00").do(lambda: asyncio.run(do_weekly_learning()))
```

### 在它後面加入（縮排要對齊，4個空格）：

```python
    async def do_daily_news():
        from telegram import Bot
        bot = Bot(token=bot_token)
        try:
            report = run_daily_news_summary(claude_client)
            for uid in user_ids:
                for i in range(0, len(report), 4000):
                    await bot.send_message(chat_id=uid, text=report[i:i+4000])
        except Exception as e:
            logger.error(f"每日新聞推送失敗: {e}")

    async def do_self_learning():
        from telegram import Bot
        bot = Bot(token=bot_token)
        try:
            report = run_daily_self_learning(claude_client)
            for uid in user_ids:
                await bot.send_message(chat_id=uid, text=report[:4000])
        except Exception as e:
            logger.error(f"每日自學習推送失敗: {e}")

    schedule.every().day.at("16:00").do(lambda: asyncio.run(do_daily_news()))
    schedule.every().day.at("21:00").do(lambda: asyncio.run(do_self_learning()))
```

### B. 在 handle_text() 的 routes 字典裡新增一行：

```python
        ("新聞", "今日新聞", "news"): cmd_news,
```

### C. 在 main() 的 cmds 列表新增：

```python
        ("news", cmd_news),
```

### D. 在 cmd_score 函式後面新增 cmd_news 函式：

```python
async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id): return
    await update.message.reply_text("📰 爬取今日台股新聞中，請稍候...")
    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            report = executor.submit(
                run_daily_news_summary, claude_client
            ).result(timeout=90)
        for i in range(0, len(report), 4000):
            await update.message.reply_text(report[i:i+4000])
    except Exception as e:
        await update.message.reply_text(f"新聞取得失敗：{e}")
```

---

## 第四步：重啟 Bot

### 方法一：如果用 systemd 管理（推薦）

```bash
# 查看服務名稱
systemctl list-units --type=service | grep -i bot

# 重啟（把 quant-bot 換成你的實際服務名）
sudo systemctl restart quant-bot

# 查看狀態
sudo systemctl status quant-bot

# 看即時 log
sudo journalctl -u quant-bot -f
```

### 方法二：如果用 screen 或 tmux

```bash
# 查看目前有哪些 screen
screen -ls

# 進入 Bot 的 screen（把 XXXX 換成實際 ID）
screen -r XXXX

# 在 screen 裡按 Ctrl+C 停止，然後重新啟動：
cd ~/quant-bot
python main.py

# 離開 screen 但保持運行：Ctrl+A 然後按 D
```

### 方法三：如果用 pm2 管理

```bash
pm2 list                    # 查看所有程序
pm2 restart quant-bot       # 重啟
pm2 logs quant-bot          # 查看 log
```

### 方法四：直接 kill 重啟

```bash
# 找到 Python 程序
ps aux | grep main.py

# Kill 它（把 XXXX 換成 PID）
kill XXXX

# 用 nohup 在背景重啟
cd ~/quant-bot
nohup python main.py > logs/bot.log 2>&1 &
echo "Bot PID: $!"
```

---

## 快速確認是否成功

重啟後在 Telegram 發送：

```
新聞          ← 測試新聞功能
分析 1605     ← 確認成交量顯示正常（應顯示「張」）
回測 1605     ← 確認回測可以執行
```

---

## 檔案對應表

| 修復的檔案 | 放置路徑 |
|-----------|---------|
| analyzer.py | `quant-bot/factors/analyzer.py` |
| engine.py | `quant-bot/backtest/engine.py` |
| ai_self_learning.py | `quant-bot/memory/ai_self_learning.py` |
| tw_stock_news.py | `quant-bot/news/tw_stock_news.py` |

---

## 排程時間表（修改後）

| 時間 | 任務 |
|------|------|
| 15:10 | 更新資料庫 |
| 15:35 | 推送每日評分報告 |
| 16:00 | 🆕 台股新聞彙整推送到 TG |
| 每15分鐘 | 持股警報檢查 |
| 21:00 | 🆕 AI 每日自學習報告推送到 TG |
| 每週日 21:00 | 週策略學習 |
