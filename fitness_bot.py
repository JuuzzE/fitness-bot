import logging
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# Вставь сюда свои ключи
GROQ_API_KEY = "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"
TELEGRAM_TOKEN = "7561034666:AAEEWXkLlHkualECQ8Bmmkn2BNtlHa5Vrg4"
logging.basicConfig(level=logging.INFO)

def ask_groq(message):
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "messages": [{"role": "user", "content": message}],
        "model": "mixtral-8x7b-32768"
    }
    response = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=data)
    return response.json()["choices"][0]["message"]["content"]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я твой домашний AI-тренер 💪\n\nНапиши /train чтобы начать тренировку,\nили /weight чтобы записать вес 📉."
    )

async def weight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введи свой текущий вес (в кг):")
    context.user_data['awaiting_weight'] = True

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('awaiting_weight'):
        weight = update.message.text
        context.user_data['awaiting_weight'] = False
        await update.message.reply_text(f"Вес {weight} кг записан! 📊")
        return

    prompt = f"Ты фитнес-тренер. Пользователь хочет домашнюю тренировку без оборудования. Вот его сообщение:\n\n{update.message.text}"
    reply = ask_groq(prompt)
    await update.message.reply_text(reply)

async def train(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = "Создай короткую (20 мин) домашнюю тренировку без оборудования. Укажи количество подходов, повторений и советы. Стиль — дружелюбный тренер."
    reply = ask_groq(prompt)
    await update.message.reply_text(reply)

app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("weight", weight))
app.add_handler(CommandHandler("train", train))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

app.run_polling()
