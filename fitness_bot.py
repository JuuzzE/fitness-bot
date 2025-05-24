import logging
import os
import httpx
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler,
    CallbackQueryHandler,
)

# --- Загрузка конфигурации (например, из .env файла для локальной разработки) ---
# Для локального теста можно создать файл .env в корне проекта с таким содержанием:
# GROQ_API_KEY=" ваш_gsk_ключ "
# TELEGRAM_TOKEN=" ваш_telegram_токен "
# from dotenv import load_dotenv
# load_dotenv() # Раскомментировать, если используется .env локально

# --- Константы и Настройки ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

if not GROQ_API_KEY or not TELEGRAM_TOKEN:
    logger.critical(
        "Не найдены API ключи! Установите GROQ_API_KEY и TELEGRAM_TOKEN как переменные окружения."
    )
    # exit(1) # Можно раскомментировать, чтобы бот не стартовал без ключей

# --- Системный промпт для AI ---
SYSTEM_PROMPT_DIETITIAN = """
Ты — ФитГуру, продвинутый AI-ассистент, выполняющий роль профессионального диетолога и фитнес-тренера.
Твоя главная задача — помочь пользователю достичь своих целей в области здоровья и фитнеса через персонализированные рекомендации по питанию и тренировкам.
При первом общении с новым пользователем (или если профиль неполный) ты должен инициировать сбор данных о пользователе (возраст, пол, рост, вес, уровень активности, цели), чтобы создать его профиль. Объясни пользователю, зачем это нужно.
На основе профиля рассчитывай ИМТ, базальный метаболизм, суточную потребность в калориях и рекомендуемое потребление калорий для достижения цели. Представь эти расчеты пользователю в понятной форме.
Используй сохраненный профиль для персонализации всех последующих советов, планов тренировок и ответов на вопросы.
Всегда будь дружелюбным, эмпатичным, поддерживающим и предоставляй научно обоснованную информацию.
Общайся на "ты".
Всегда отвечай на русском языке.
Если пользователь просит что-то, что выходит за рамки твоей компетенции как диетолога/тренера (например, медицинский диагноз), вежливо откажи и порекомендуй обратиться к специалисту.
"""

# --- Состояния для ConversationHandler ---
(PROFILE_GENDER, PROFILE_AGE, PROFILE_HEIGHT, PROFILE_WEIGHT,
 PROFILE_ACTIVITY, PROFILE_GOAL, PROFILE_PROCESS) = range(7)

# --- Ключи для context.user_data ---
GENDER, AGE, HEIGHT, CURRENT_WEIGHT, ACTIVITY_LEVEL, GOAL = \
    "gender", "age", "height", "current_weight", "activity_level", "goal"
PROFILE_COMPLETE = "profile_complete"
BMI, BMR, TDEE, TARGET_CALORIES = "bmi", "bmr", "tdee", "target_calories"

AWAITING_WEIGHT_UPDATE = "awaiting_weight_update"

# --- Факторы для расчетов ---
ACTIVITY_FACTORS = {
    "минимальная": 1.2, "легкая": 1.375, "средняя": 1.55,
    "высокая": 1.725, "экстремальная": 1.9
}
GOAL_FACTORS = {"похудеть": -500, "поддерживать вес": 0, "набрать массу": 300}

# --- Вспомогательные функции для расчетов ---
def calculate_bmi(weight_kg, height_cm):
    if not weight_kg or not height_cm or height_cm == 0: return None
    return round(weight_kg / ((height_cm / 100) ** 2), 1)

def calculate_bmr(weight_kg, height_cm, age_years, gender_str):
    if not all([weight_kg, height_cm, age_years, gender_str]): return None
    gender_str = gender_str.lower()
    if gender_str == "мужской":
        return round((10 * weight_kg) + (6.25 * height_cm) - (5 * age_years) + 5)
    elif gender_str == "женский":
        return round((10 * weight_kg) + (6.25 * height_cm) - (5 * age_years) - 161)
    return None

def calculate_tdee(bmr, activity_level_str):
    if not bmr or not activity_level_str: return None
    factor = ACTIVITY_FACTORS.get(activity_level_str.lower())
    return round(bmr * factor) if factor else None

def calculate_target_calories(tdee, goal_str):
    if not tdee or not goal_str: return None
    adjustment = GOAL_FACTORS.get(goal_str.lower())
    return tdee + adjustment if adjustment is not None else None

def get_bmi_interpretation(bmi):
    if bmi is None: return ""
    if bmi < 18.5: return " (Дефицит массы тела)"
    if bmi < 25: return " (Нормальный вес)"
    if bmi < 30: return " (Избыточный вес)"
    return " (Ожирение)"

# --- Функция для запросов к Groq API ---
async def ask_groq(user_message: str, model: str = "mixtral-8x7b-32768", system_prompt_override: str = None):
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    current_system_prompt = system_prompt_override if system_prompt_override else SYSTEM_PROMPT_DIETITIAN
    data = {
        "messages": [
            {"role": "system", "content": current_system_prompt},
            {"role": "user", "content": user_message}
        ],
        "model": model
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions", headers=headers, json=data, timeout=45.0
            )
            response.raise_for_status()
            response_data = response.json()
            if response_data.get("choices") and response_data["choices"][0].get("message"):
                return response_data["choices"][0]["message"]["content"]
            logger.error(f"Неожиданная структура ответа от Groq: {response_data}")
            return "Извини, не удалось обработать ответ от AI."
    except httpx.HTTPStatusError as e:
        logger.error(f"Ошибка HTTP от Groq: {e.response.status_code} - {e.response.text}")
        return f"Ошибка при обращении к AI (код: {e.response.status_code})."
    except httpx.RequestError as e:
        logger.error(f"Ошибка запроса к Groq: {e}")
        return "Проблема с подключением к AI."
    except (KeyError, IndexError) as e:
        logger.error(f"Ошибка парсинга ответа Groq: {e}")
        return "Получен неожиданный ответ от AI."
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в ask_groq: {e}")
        return "Произошла непредвиденная ошибка."

# --- Функции для ConversationHandler (создание профиля) ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if context.user_data.get(PROFILE_COMPLETE):
        await update.message.reply_text(
            f"С возвращением, {user.first_name}! Твой профиль уже создан. Чем могу помочь?\n"
            "Используй /train, /weight, /myprofile или просто задай вопрос."
        )
        return ConversationHandler.END

    context.user_data.clear() # Очищаем на случай начала нового профиля
    logger.info(f"Пользователь {user.id} ({user.username}) начинает создание профиля.")
    await update.message.reply_text(
        f"Привет, {user.first_name}! Я ФитГуру, твой личный AI-диетолог и тренер. 💪\n\n"
        "Чтобы я мог составить для тебя персонализированные рекомендации, мне нужно немного узнать о тебе. "
        "Это займет всего пару минут. Вся информация останется между нами.\n\n"
        "Для начала, укажи, пожалуйста, свой пол:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Мужской", callback_data="мужской")],
            [InlineKeyboardButton("Женский", callback_data="женский")]
        ])
    )
    return PROFILE_GENDER

async def handle_gender_and_ask_age(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data[GENDER] = query.data
    await query.edit_message_text(text=f"Пол: {query.data.capitalize()}. Понял.\nТеперь введи свой возраст (полных лет):")
    return PROFILE_AGE

async def handle_age_and_ask_height(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        age = int(update.message.text)
        if not 10 <= age <= 100: raise ValueError("Некорректный возраст")
        context.user_data[AGE] = age
        await update.message.reply_text(f"Возраст: {age} лет. Отлично!\nТеперь введи свой рост (в сантиметрах):")
        return PROFILE_HEIGHT
    except ValueError:
        await update.message.reply_text("Пожалуйста, введи возраст целым числом (например, 25).")
        return PROFILE_AGE

async def handle_height_and_ask_weight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        height = int(update.message.text)
        if not 100 <= height <= 250: raise ValueError("Некорректный рост")
        context.user_data[HEIGHT] = height
        await update.message.reply_text(f"Рост: {height} см. Записал!\nТеперь введи свой текущий вес (в кг, например, 70.5):")
        return PROFILE_WEIGHT
    except ValueError:
        await update.message.reply_text("Пожалуйста, введи рост целым числом в сантиметрах (например, 175).")
        return PROFILE_HEIGHT

async def handle_weight_and_ask_activity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        weight = float(update.message.text.replace(',', '.'))
        if not 30 <= weight <= 300: raise ValueError("Некорректный вес")
        context.user_data[CURRENT_WEIGHT] = weight
        activity_buttons = [
            [InlineKeyboardButton(label.capitalize(), callback_data=key)]
            for key, label in {
                "минимальная": "Минимальная (сидячая работа, нет тренировок)",
                "легкая": "Легкая (легкие тренировки 1-3 р/нед)",
                "средняя": "Средняя (тренировки 3-5 р/нед)",
                "высокая": "Высокая (интенсивные тренировки 6-7 р/нед)",
                "экстремальная": "Экстремальная (тяжелая физ. работа + тренировки)"
            }.items()
        ] # Генерируем кнопки, чтобы избежать слишком длинных строк в коде
        await update.message.reply_text(
            f"Вес: {weight} кг. Принято!\nОцени свой обычный уровень физической активности:",
            reply_markup=InlineKeyboardMarkup(activity_buttons)
        )
        return PROFILE_ACTIVITY
    except ValueError:
        await update.message.reply_text("Пожалуйста, введи вес числом (например, 70.5).")
        return PROFILE_WEIGHT

async def handle_activity_and_ask_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data[ACTIVITY_LEVEL] = query.data
    await query.edit_message_text(
        text=f"Уровень активности: {query.data.capitalize()}. Почти готово!\nКакая твоя основная цель?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Похудеть 📉", callback_data="похудеть")],
            [InlineKeyboardButton("Поддерживать вес ⚖️", callback_data="поддерживать вес")],
            [InlineKeyboardButton("Набрать массу 💪", callback_data="набрать массу")]
        ])
    )
    return PROFILE_GOAL

async def process_final_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data[GOAL] = query.data
    ud = context.user_data

    # Расчеты
    ud[BMI] = calculate_bmi(ud.get(CURRENT_WEIGHT), ud.get(HEIGHT))
    ud[BMR] = calculate_bmr(ud.get(CURRENT_WEIGHT), ud.get(HEIGHT), ud.get(AGE), ud.get(GENDER))
    ud[TDEE] = calculate_tdee(ud.get(BMR), ud.get(ACTIVITY_LEVEL))
    ud[TARGET_CALORIES] = calculate_target_calories(ud.get(TDEE), ud.get(GOAL))
    ud[PROFILE_COMPLETE] = True

    bmi_interp = get_bmi_interpretation(ud.get(BMI))
    summary = (
        f"🎉 Отлично! Твой профиль создан и сохранен. Вот твои основные показатели:\n\n"
        f"👤 **Профиль:**\n"
        f"  - Пол: {ud.get(GENDER, 'N/A').capitalize()}\n"
        f"  - Возраст: {ud.get(AGE, 'N/A')} лет\n"
        f"  - Рост: {ud.get(HEIGHT, 'N/A')} см\n"
        f"  - Вес: {ud.get(CURRENT_WEIGHT, 'N/A')} кг\n"
        f"  - Активность: {ud.get(ACTIVITY_LEVEL, 'N/A').capitalize()}\n"
        f"  - Цель: {ud.get(GOAL, 'N/A').capitalize()}\n\n"
        f"📊 **Расчетные показатели:**\n"
        f"  - ИМТ: {ud.get(BMI, 'N/A')}{bmi_interp}\n"
        f"  - Базальный метаболизм (BMR): {ud.get(BMR, 'N/A')} ккал/день\n"
        f"  - Суточная потребность (TDEE): {ud.get(TDEE, 'N/A')} ккал/день\n"
        f"  - ✨ **Рекомендуемые калории для цели: {ud.get(TARGET_CALORIES, 'N/A')} ккал/день** ✨\n\n"
        "Теперь я могу давать тебе более точные советы! Используй:\n"
        "/myprofile - посмотреть свой профиль\n"
        "/train - получить тренировку\n"
        "/weight - обновить вес\n"
        "Или просто задавай вопросы по питанию и фитнесу.\n\n"
        "⚠️ *Помни, эти расчеты ориентировочны. Для точных медицинских рекомендаций обратись к врачу.*"
    )
    await query.edit_message_text(text=summary, parse_mode='Markdown')
    logger.info(f"Пользователь {update.effective_user.id} завершил профиль: {ud}")
    return ConversationHandler.END

async def cancel_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Создание профиля отменено. Можешь начать заново командой /start.")
    context.user_data.clear()
    return ConversationHandler.END

# --- Обычные команды ---
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "Привет! Я твой AI-фитнес-тренер и диетолог ФитГуру. Вот что я умею:\n\n"
        "/start - Начать работу со мной или создать/просмотреть профиль.\n"
        "/myprofile - Показать твой текущий профиль и расчетные показатели.\n"
        "/train - Получить персонализированную программу тренировки.\n"
        "/weight - Записать (обновить) свой текущий вес.\n"
        "/cancel - (Во время создания профиля) Отменить создание профиля.\n"
        "/help - Показать это сообщение.\n\n"
        "Если твой профиль уже создан, ты можешь просто написать мне свой вопрос о фитнесе, питании или калориях!"
    )
    await update.message.reply_text(help_text)

async def my_profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get(PROFILE_COMPLETE):
        await update.message.reply_text("Твой профиль еще не создан. Пожалуйста, начни с команды /start.")
        return

    ud = context.user_data
    bmi_interp = get_bmi_interpretation(ud.get(BMI))
    summary = (
        f"Вот данные твоего профиля, {update.effective_user.first_name}:\n\n"
        f"👤 **Профиль:**\n"
        f"  - Пол: {ud.get(GENDER, 'N/A').capitalize()}\n"
        f"  - Возраст: {ud.get(AGE, 'N/A')} лет\n"
        f"  - Рост: {ud.get(HEIGHT, 'N/A')} см\n"
        f"  - Вес: {ud.get(CURRENT_WEIGHT, 'N/A')} кг\n"
        f"  - Активность: {ud.get(ACTIVITY_LEVEL, 'N/A').capitalize()}\n"
        f"  - Цель: {ud.get(GOAL, 'N/A').capitalize()}\n\n"
        f"📊 **Расчетные показатели:**\n"
        f"  - ИМТ: {ud.get(BMI, 'N/A')}{bmi_interp}\n"
        f"  - BMR: {ud.get(BMR, 'N/A')} ккал/день\n"
        f"  - TDEE: {ud.get(TDEE, 'N/A')} ккал/день\n"
        f"  - Рекомендуемые калории: {ud.get(TARGET_CALORIES, 'N/A')} ккал/день\n\n"
        "Используй /weight для обновления веса или /start чтобы создать профиль заново (старый будет удален)."
    )
    await update.message.reply_text(summary, parse_mode='Markdown')

async def weight_command_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get(PROFILE_COMPLETE):
        await update.message.reply_text("Сначала создай профиль командой /start.")
        return
    await update.message.reply_text("Введи свой текущий вес (например, 70.5):")
    context.user_data[AWAITING_WEIGHT_UPDATE] = True

async def handle_weight_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        new_weight = float(update.message.text.replace(',', '.'))
        if not 30 <= new_weight <= 300: raise ValueError("Некорректный вес")

        ud = context.user_data
        ud[CURRENT_WEIGHT] = new_weight
        # Пересчет показателей
        ud[BMI] = calculate_bmi(new_weight, ud.get(HEIGHT))
        ud[BMR] = calculate_bmr(new_weight, ud.get(HEIGHT), ud.get(AGE), ud.get(GENDER))
        ud[TDEE] = calculate_tdee(ud.get(BMR), ud.get(ACTIVITY_LEVEL))
        ud[TARGET_CALORIES] = calculate_target_calories(ud.get(TDEE), ud.get(GOAL))
        
        ud.pop(AWAITING_WEIGHT_UPDATE, None) # Удаляем флаг
        bmi_interp = get_bmi_interpretation(ud.get(BMI))
        await update.message.reply_text(
            f"⚖️ Вес {new_weight} кг обновлен! Твои показатели пересчитаны:\n"
            f"  - ИМТ: {ud.get(BMI, 'N/A')}{bmi_interp}\n"
            f"  - Рекомендуемые калории: {ud.get(TARGET_CALORIES, 'N/A')} ккал/день",
            parse_mode='Markdown'
        )
        logger.info(f"Пользователь {update.effective_user.id} обновил вес: {new_weight} кг.")
    except ValueError:
        await update.message.reply_text("Пожалуйста, введи вес числом (например, 70.5). Попробуй еще раз /weight или введи вес снова.")
        # Оставляем флаг, чтобы следующее сообщение тоже пыталось обработаться как вес

async def train_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get(PROFILE_COMPLETE):
        await update.message.reply_text("Для подбора тренировки нужен твой профиль. Начни с /start.")
        return

    ud = context.user_data
    profile_info = (
        f"Профиль пользователя: Пол: {ud.get(GENDER)}, Возраст: {ud.get(AGE)}, "
        f"Рост: {ud.get(HEIGHT)}см, Вес: {ud.get(CURRENT_WEIGHT)}кг, "
        f"Активность: {ud.get(ACTIVITY_LEVEL)}, Цель: {ud.get(GOAL)}. "
        f"Его ИМТ: {ud.get(BMI, 'N/A')}, Рекомендуемые калории: {ud.get(TARGET_CALORIES, 'N/A')}."
    )
    prompt = (
        f"{profile_info} Создай короткую (20-30 мин) домашнюю тренировку для всего тела "
        "без оборудования, учитывая профиль. Укажи подходы, повторения, советы."
    )
    await update.message.reply_text("🏋️‍♂️ Подбираю персонализированную тренировку...")
    reply = await ask_groq(prompt)
    await update.message.reply_text(reply)

# --- Обработчик общих сообщений ---
async def general_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get(AWAITING_WEIGHT_UPDATE):
        await handle_weight_update(update, context)
        return

    if not context.user_data.get(PROFILE_COMPLETE):
        await update.message.reply_text(
            "Похоже, твой профиль еще не создан. "
            "Пожалуйста, начни с команды /start, чтобы я мог тебе помочь. 😊"
        )
        return

    user_message = update.message.text
    logger.info(f"Сообщение от {update.effective_user.id}: {user_message}")
    
    ud = context.user_data
    profile_info = (
        f"Контекст пользователя (для напоминания): "
        f"Пол: {ud.get(GENDER)}, Возраст: {ud.get(AGE)}, "
        f"Рост: {ud.get(HEIGHT)}см, Вес: {ud.get(CURRENT_WEIGHT)}кг, "
        f"Активность: {ud.get(ACTIVITY_LEVEL)}, Цель: {ud.get(GOAL)}. "
        f"ИМТ: {ud.get(BMI, 'N/A')}, BMR: {ud.get(BMR, 'N/A')} ккал, TDEE: {ud.get(TDEE, 'N/A')} ккал, "
        f"Рекомендуемые калории: {ud.get(TARGET_CALORIES, 'N/A')} ккал. "
        "Ответь на следующий вопрос пользователя, учитывая эту информацию: "
    )
    full_prompt = f"{profile_info}{user_message}"

    await update.message.reply_text("🤔 Думаю над твоим вопросом...")
    reply = await ask_groq(full_prompt)
    await update.message.reply_text(reply)

# --- Основная функция ---
def main():
    if not TELEGRAM_TOKEN: return

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    onboarding_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start_command)],
        states={
            PROFILE_GENDER: [CallbackQueryHandler(handle_gender_and_ask_age, pattern="^(мужской|женский)$")],
            PROFILE_AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_age_and_ask_height)],
            PROFILE_HEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_height_and_ask_weight)],
            PROFILE_WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_weight_and_ask_activity)],
            PROFILE_ACTIVITY: [CallbackQueryHandler(handle_activity_and_ask_goal, pattern="^(минимальная|легкая|средняя|высокая|экстремальная)$")],
            PROFILE_GOAL: [CallbackQueryHandler(process_final_profile, pattern="^(похудеть|поддерживать вес|набрать массу)$")],
        },
        fallbacks=[CommandHandler("cancel", cancel_onboarding)],
        per_user=True, # Важно для хранения данных профиля в user_data
        per_chat=True,
    )
    app.add_handler(onboarding_conv_handler)

    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("myprofile", my_profile_command))
    app.add_handler(CommandHandler("weight", weight_command_entry))
    app.add_handler(CommandHandler("train", train_command))
    # /start уже обрабатывается ConversationHandler как точка входа

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, general_message_handler))

    logger.info("Бот ФитГуру запускается...")
    app.run_polling()

if __name__ == "__main__":
    main()