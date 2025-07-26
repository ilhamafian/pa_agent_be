# bot.py
import os
import json
import re
from openai import OpenAI
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from tools.calendar import event_tool, create_event
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

print(f"BOT_TOKEN loaded: {'Yes' if BOT_TOKEN else 'No'}")
print(f"OPENAI_API_KEY loaded: {'Yes' if OPENAI_API_KEY else 'No'}")

if not BOT_TOKEN:
    print("❌ BOT_TOKEN is not set in environment variables!")
    exit(1)

if not OPENAI_API_KEY:
    print("❌ OPENAI_API_KEY is not set in environment variables!")
    exit(1)

now = datetime.now(ZoneInfo("Asia/Kuala_Lumpur"))
today_str = now.strftime("%Y-%m-%d")
tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")

with open("system_prompt.txt", "r", encoding="utf-8") as f:
    raw_prompt = f.read()
    system_prompt = raw_prompt.format(
        today=today_str,
        tomorrow=tomorrow_str,
    )

def escape_markdown(text: str) -> str:
    return re.sub(r'([_\*\[\]()~`>#+\-=|{}.!])', r'\\\1', text)

# 🧠 Simple in-memory storage for user message history
user_memory = {}

tools = [event_tool]

# Message handler
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text
    user_id = str(update.message.from_user.id)

    client = OpenAI(api_key=OPENAI_API_KEY)

    try:
        # Get user history
        history = user_memory.get(user_id, [])
        history.append({"role": "user", "content": user_input})

        # Prepare full messages
        messages = [{"role": "system", "content": system_prompt}] + history[-10:]  # Keep last 10 exchanges

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            tools=tools,
            tool_choice="auto"
        )

        message = response.choices[0].message
        print("message:", message)

        if message.tool_calls:
            for tool_call in message.tool_calls:
                function_name = tool_call.function.name
                args = json.loads(tool_call.function.arguments)

                if function_name == "get_calendar":
                    result = create_event(
                        title=args["title"],
                        date=args["date"],
                        time=args.get("time"),
                        end_time=args.get("end_time")
                    )
                    if args.get("time") and args.get("end_time"):
                        time_display = f"Time: {args['time']} - {args['end_time']}\n"
                    else:
                        time_display = "Time: All-day\n"

                    reply = (
                        f"📅 Calendar Event Created\n\n"
                        f"Title: {args['title']}\n"
                        f"Date: {args['date']}\n"
                        f"{time_display}"
                        f"Link: {result.get('htmlLink', 'Link unavailable')}"
                    )
                    history.append({"role": "assistant", "content": reply})
                    user_memory[user_id] = history

                    await update.message.reply_text(reply)
                    return

        reply = message.content.strip() if message.content else "No response."
        history.append({"role": "assistant", "content": reply})
        user_memory[user_id] = history

        await update.message.reply_text(reply)

    except Exception as e:
        print(f"Error in handle_message: {e}")

if __name__ == "__main__":
    try:
        app = ApplicationBuilder().token(BOT_TOKEN).build()
        app.add_handler(MessageHandler(filters.TEXT, handle_message))
        print("🤖 Bot is running...")
        app.run_polling()
    except Exception as e:
        print(f"❌ Failed to start bot: {e}")
        print("Please check your BOT_TOKEN and internet connection.")
