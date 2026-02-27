with open('main.py', 'r', encoding='utf-8') as f:
    text = f.read()

# 把被關起來的函數，重新定義在全域空間
fix_code = """
async def cmd_paper_portfolio(update, context):
    await update.message.reply_text(get_paper_portfolio(), parse_mode="Markdown")

async def handle_text"""

# 自動尋找並替換修復
if "async def cmd_paper_portfolio(update, context):" not in text.split("async def handle_text")[0]:
    text = text.replace("async def handle_text", fix_code)
    with open('main.py', 'w', encoding='utf-8') as f:
        f.write(text)
    print("✅ 蟲子捏死了！main.py 修復成功！")
else:
    print("✅ 已經修復過了。")
