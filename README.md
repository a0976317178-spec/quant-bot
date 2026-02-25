# 🤖 台股量化交易 AI Bot

整合 Claude AI + LightGBM + Telegram Bot 的台股量化選股系統

---

## 📁 專案結構

```
quant_bot/
├── main.py                  # TG Bot 主程式 + Claude API
├── config.py                # 全域設定
├── scheduler.py             # 每日盤後自動排程
├── requirements.txt
├── .env.example
│
├── factors/                 # 第二階段：因子計算
│   ├── technical.py         # 量價因子（RSI/ATR/MACD/乖離率）
│   ├── flow.py              # 籌碼因子（三大法人）
│   ├── fundamental.py       # 基本面因子（營收/EPS）
│   ├── macro.py             # 宏觀因子（VIX/ADL）
│   └── labeling.py          # 第三階段：標籤定義
│
├── ml/                      # 第四階段：機器學習
│   ├── train.py             # LightGBM Walk-Forward 訓練
│   └── predict.py           # 選股預測
│
├── memory/                  # 記憶模組
│   └── rules_manager.py     # 自訂規則 + 對話記憶
│
└── data/                    # 資料儲存
    ├── factors/             # 因子數據
    ├── models/              # 訓練好的模型
    └── raw/                 # 原始爬蟲數據
```

---

## 🚀 安裝與啟動

```bash
# 1. 切換到專案目錄
cd /d E:\cloud

# 2. 建立虛擬環境
python -m venv venv
venv\Scripts\activate

# 3. 安裝套件
pip install -r requirements.txt

# 4. 設定環境變數
copy .env.example .env
notepad .env

# 5. 啟動 Bot
python main.py
```

---

## ⚙️ .env 設定

```
TELEGRAM_BOT_TOKEN=你的TG Token
ANTHROPIC_API_KEY=你的Claude API Key
ALLOWED_USER_IDS=你的TG User ID
```

---

## 📋 TG Bot 指令

| 指令 | 說明 |
|------|------|
| `/start` | 啟動 Bot |
| `/chat <問題>` | 與 Claude 對話 |
| `/analyze <代號>` | 分析股票（例：/analyze 2330） |
| `/screen` | 執行 ML 選股掃描 |
| `/macro` | 查看宏觀指標（VIX/ADL） |
| `/teach <規則>` | 教導 AI 您的選股規則 |
| `/rules` | 查看所有自訂規則 |
| `/delrule <編號>` | 刪除規則 |
| `/clear` | 清除對話記憶 |
| `/train` | 重新訓練 ML 模型 |

---

## 🧠 如何「訓練」AI 記住您的策略

```
/teach 外資連買3天以上才考慮進場
/teach RSI必須在50到70之間
/teach 月營收年增率需大於10%
/teach 大盤VIX超過25時停止操作
```

這些規則會永久儲存，每次分析時 AI 都會自動套用。

---

## 🤖 自動排程

每天 15:35（台股收盤後）自動執行：
- 爬取最新因子數據
- 執行 ML 預測
- 推播選股結果到 TG

每週日 22:00 自動重新訓練模型。

---

## ⚠️ 免責聲明

本系統僅供學習研究使用，不構成任何投資建議。
股票投資有風險，請自行評估並承擔投資損失。
