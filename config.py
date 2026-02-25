"""
config.py - 全域設定
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ─────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_IDS = [
    int(uid.strip())
    for uid in os.getenv("ALLOWED_USER_IDS", "").split(",")
    if uid.strip()
]

# ── Claude API ───────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# 日常對話用輕量模型（省 Token）
CLAUDE_FAST_MODEL = "claude-haiku-4-5-20251001"

# 複雜分析、程式生成用強力模型
CLAUDE_SMART_MODEL = "claude-sonnet-4-6"

# ── 路徑設定 ─────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
FACTORS_DIR = os.path.join(DATA_DIR, "factors")
MODELS_DIR = os.path.join(DATA_DIR, "models")
RAW_DIR = os.path.join(DATA_DIR, "raw")
MEMORY_DIR = os.path.join(BASE_DIR, "memory")

# 自動建立資料夾
for d in [DATA_DIR, FACTORS_DIR, MODELS_DIR, RAW_DIR, MEMORY_DIR]:
    os.makedirs(d, exist_ok=True)

# ── 選股標籤設定（第三階段）────────────────────────
LABEL_CONFIG = {
    "future_days": 5,        # 未來觀察天數
    "target_up_pct": 0.05,   # 最高價需上漲 5%
    "stop_loss_pct": 0.02,   # 最低價不得跌破 2%
}

# ── 技術因子設定（第二階段）────────────────────────
FACTOR_CONFIG = {
    "return_windows": [5, 10, 20],   # 報酬率回看天數
    "rsi_period": 14,
    "atr_period": 14,
    "ma_period": 20,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
}
