import logging
import os # Для переменных окружения
import httpx # Асинхронные запросы
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, ConversationHandler

# --- Константы и Настройки ---
# Загружаем ключи из переменных окружения (безопаснее для деплоя)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "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") # Оставил дефолт для локального теста
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "7561034666:AAEEWXkLlHkualECQ8Bmmkn2BNtlHa5Vrg4") # Оставил дефолт

if not GROQ_API_KEY or not TELEGRAM_TOKEN:
    logging.error("Не найдены API ключи! Установите GROQ_API_KEY и TELEGRAM_TOKEN как переменные окружения.")
    # exit() # Раскомментируй для продакшена, чтобы бот не стартовал без ключей

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler (если понадобится для более сложных диалогов)
# Пример: AWAITING_FOOD_ITEM, AWAITING_CALORIES и т.д.
AWAITING_WEIGHT = 1 # Для примера использования с ConversationHandler, но для веса и текущий подход норм

SYSTEM_PROMPT_FITNESS_NUTRITION = """
Ты — продвинутый AI-ассистент по фитнесу и питанию по имени ФитГуру.
Твоя задача — помогать пользователям с тренировками, подсчетом калорий,
составлением планов питания и отвечать на вопросы о здоровом образе жизни.
Будь дружелюбным, поддерживающим и предоставляй точную, научно обоснованную информацию.
Если пользователь просит тренировку, предлагай варианты для дома без оборудования, если не указано иное.
Если пользователь спрашивает о калориях, старайся дать оценку или попроси уточнить продукт.
Всегда отвечай на русском языке.
"""

async def ask_groq(user_message: str, model: str = "mixtral-8x7b-32768"):
    """
    Асинхронно отправляет запрос к Groq API и возвращает ответ модели.
    """
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT_FITNESS_NUTRITION},
            {"role": "user", "content": user_message}
        ],
        "model": model
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=data,
                timeout=30.0 # Таймаут для запроса
            )
            response.raise_for_status() # Вызовет исключение для HTTP ошибок 4xx/5xx
            response_data = response.json()

            if response_data.get("choices") and response_data["choices"][0].get("message"):
                return response_data["choices"][0]["message"]["content"]
            else:
                logger.error(f"Неожиданная структура ответа от Groq API: {response_data}")
                return "Извини, у меня не получилось обработать твой запрос к AI. Структура ответа некорректна."

    except httpx.HTTPStatusError as e:
        logger.error(f"Ошибка HTTP при запросе к Groq API: {e.response.status_code} - {e.response.text}")
        return f"Произошла ошибка при обращении к AI (код: {e.response.status_code}). Попробуй позже."
    except httpx.RequestError as e:
        logger.error(f"Ошибка запроса к Groq API: {e}")
        return "Проблема с подключением к AI. Попробуй позже."
    except (KeyError, IndexError) as e:
        logger.error(f"Ошибка парсинга ответа от Groq API: {e} - Ответ: {response_data if 'response_data' in locals() else 'Не получен'}")
        return "Получен неожиданный ответ от AI. Попробуй еще раз."
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в ask_groq: {e}")
        return "Произошла непредвиденная ошибка. Мы уже работаем над этим!"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name
    await update.message.reply_text(
        f"Привет, {user_name}! Я твой домашний AI-тренер ФитГуру 💪\n\n"
        "Я могу помочь тебе с:\n"
        "🏋️‍♂️ Планами тренировок (/train)\n"
        "⚖️ Записью веса (/weight)\n"
        "🍏 Вопросами по питанию и калориям (просто спроси!)\n\n"
        "Используй /help для списка всех команд."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Список доступных команд:\n"
        "/start - Начальное приветствие\n"
        "/train - Получить программу тренировки\n"
        "/weight - Записать текущий вес\n"
        "/help - Показать это сообщение\n\n"
        "Ты также можешь просто написать мне свой вопрос о фитнесе или питании!"
    )

async def weight_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введи свой текущий вес (например, 75.5):")
    context.user_data['awaiting_weight'] = True # Флаг для message_handler

async def handle_weight_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        weight_text = update.message.text.replace(',', '.') # Заменяем запятую на точку для float
        current_weight = float(weight_text)
        # Здесь можно добавить логику сохранения веса в базу данных или user_data для истории
        context.user_data['current_weight'] = current_weight
        context.user_data['awaiting_weight'] = False # Сбрасываем флаг
        logger.info(f"Пользователь {update.effective_user.id} записал вес: {current_weight} кг")
        await update.message.reply_text(f"Отлично! Вес {current_weight} кг записан. 📊")
    except ValueError:
        await update.message.reply_text("Пожалуйста, введи вес числом (например, 75.5 или 75). Попробуй еще раз /weight.")
        # Можно не сбрасывать флаг, чтобы пользователь сразу попробовал ввести еще раз
        # context.user_data['awaiting_weight'] = True # Оставляем флаг для повторного ввода

async def train_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = "Создай короткую (примерно 20-30 минут) домашнюю тренировку для всего тела без специального оборудования. Укажи количество подходов, повторений для каждого упражнения и дай пару советов по технике или мотивации. Стиль — дружелюбный и поддерживающий тренер."
    await update.message.reply_text("Подбираю для тебя тренировку... Пожалуйста, подожди немного. 🏋️‍♂️")
    reply = await ask_groq(prompt)
    await update.message.reply_text(reply)

async def general_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Сначала проверяем, не ждем ли мы ввод веса
    if context.user_data.get('awaiting_weight'):
        await handle_weight_input(update, context)
        return

    user_message = update.message.text
    logger.info(f"Получено сообщение от {update.effective_user.id}: {user_message}")

    # Если это не команда и не ожидаемый ввод, отправляем в Groq
    await update.message.reply_text("Думаю над твоим вопросом... 🤔")
    reply = await ask_groq(user_message)
    await update.message.reply_text(reply)


def main():
    if not TELEGRAM_TOKEN:
        logger.critical("Токен Telegram не найден!")
        return

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Добавляем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("weight", weight_command))
    app.add_handler(CommandHandler("train", train_command))

    # Обработчик текстовых сообщений должен идти после командных,
    # и он будет обрабатывать ввод веса или общие вопросы.
    # Фильтр ~filters.COMMAND нужен, чтобы команды не попадали в этот обработчик случайно.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, general_message_handler))

    logger.info("Бот запускается...")
    app.run_polling()

if __name__ == "__main__":
    main()