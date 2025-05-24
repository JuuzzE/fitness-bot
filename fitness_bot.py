import logging
import os
import httpx
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler,
    CallbackQueryHandler,
)

# --- Константы и Настройки ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

if not GROQ_API_KEY or not TELEGRAM_TOKEN:
    logger.critical("КРИТИЧЕСКАЯ ОШИБКА: Не найдены API ключи! Установите GROQ_API_KEY и TELEGRAM_TOKEN.")

# --- Системный промпт для AI ---
SYSTEM_PROMPT_DIETITIAN = """
Ты — ФитГуру, продвинутый AI-ассистент, выполняющий роль профессионального диетолога и фитнес-тренера.
Твоя главная задача — помочь пользователю достичь своих целей в области здоровья и фитнеса через персонализированные рекомендации по питанию и тренировкам.
Общайся на "ты". Используй эмодзи для более живого общения.
Всегда отвечай на русском языке. Пиши грамотно, естественно, избегай канцеляризмов и неестественных оборотов.
При первом общении с новым пользователем (или если профиль неполный) ты должен инициировать сбор данных о пользователе (возраст, пол, рост, вес, уровень активности, цели), чтобы создать его профиль. Объясни пользователю, зачем это нужно.
На основе профиля рассчитывай ИМТ, базальный метаболизм, суточную потребность в калориях и рекомендуемое потребление калорий для достижения цели. Представь эти расчеты пользователю в понятной форме, используя Markdown для выделения.
Используй сохраненный профиль для персонализации всех последующих советов, планов тренировок и ответов на вопросы.
Если пользователь просит что-то, что выходит за рамки твоей компетенции как диетолога/тренера (например, медицинский диагноз), вежливо откажи и порекомендуй обратиться к специалисту.
При генерации тренировок, четко указывай название упражнения, количество подходов, количество повторений, время отдыха. Оценивай примерное количество сожженных калорий за тренировку.
"""

# --- Состояния и Ключи (как раньше) ---
(PROFILE_GENDER, PROFILE_AGE, PROFILE_HEIGHT, PROFILE_WEIGHT,
 PROFILE_ACTIVITY, PROFILE_GOAL) = range(6)
GENDER, AGE, HEIGHT, CURRENT_WEIGHT, ACTIVITY_LEVEL, GOAL = \
    "gender", "age", "height", "current_weight", "activity_level", "goal"
PROFILE_COMPLETE = "profile_complete"
BMI, BMR, TDEE, TARGET_CALORIES = "bmi", "bmr", "tdee", "target_calories"
AWAITING_WEIGHT_UPDATE = "awaiting_weight_update"
ACTIVITY_FACTORS = {"минимальная": 1.2, "легкая": 1.375, "средняя": 1.55, "высокая": 1.725, "экстремальная": 1.9}
GOAL_FACTORS = {"похудеть": -500, "поддерживать вес": 0, "набрать массу": 300}

# --- Вспомогательные функции для расчетов (как раньше) ---
def calculate_bmi(w, h): return round(w / ((h / 100) ** 2), 1) if all(isinstance(i, (int, float)) for i in [w, h]) and h != 0 else None
def calculate_bmr(w, h, a, g_str):
    if not all([w, h, a, g_str]) or not all(isinstance(i, (int, float)) for i in [w, h, a]): return None
    g_str = g_str.lower()
    if g_str == "мужской": return round((10 * w) + (6.25 * h) - (5 * a) + 5)
    elif g_str == "женский": return round((10 * w) + (6.25 * h) - (5 * a) - 161)
    return None
def calculate_tdee(bmr, act_lvl): return round(bmr * ACTIVITY_FACTORS.get(act_lvl.lower())) if bmr and act_lvl and isinstance(bmr, (int, float)) else None
def calculate_target_calories(tdee, gl): return tdee + GOAL_FACTORS.get(gl.lower()) if tdee and gl and isinstance(tdee, (int, float)) else None
def get_bmi_interpretation(bmi):
    if bmi is None: return ""
    if bmi < 18.5: return " (📉 Дефицит массы тела)"
    if bmi < 25: return " (✅ Нормальный вес)"
    if bmi < 30: return " (⚠️ Избыточный вес)"
    return " (🆘 Ожирение)"

# В функции ask_groq меняем модель по умолчанию:
async def ask_groq(user_message: str, model: str = "gemma2-9b-it", system_prompt_override: str = None, temperature: float = 0.5): # ИЗМЕНЕНА МОДЕЛЬ
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    current_system_prompt = system_prompt_override if system_prompt_override else SYSTEM_PROMPT_DIETITIAN
    data = {
        "messages": [{"role": "system", "content": current_system_prompt}, {"role": "user", "content": user_message}],
        "model": model,
        "temperature": temperature # Добавлен параметр температуры
    }
    # ... (остальной код ask_groq с обработкой ошибок как раньше) ...
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=data, timeout=45.0)
            response.raise_for_status()
            response_data = response.json()
            if response_data.get("choices") and response_data["choices"][0].get("message"):
                return response_data["choices"][0]["message"]["content"]
            logger.error(f"Неожиданная структура ответа от Groq: {response_data}")
            return "🤖 Извини, у меня небольшие технические шоколадки с AI. Попробуй позже!"
    except httpx.HTTPStatusError as e:
        logger.error(f"Ошибка HTTP от Groq: {e.response.status_code} - {e.response.text}")
        if "model_decommissioned" in e.response.text:
             return f"🔌 Ой, похоже, выбранная модель AI ({model}) больше не доступна. Разработчик уже в курсе!"
        return f"🔌 Ошибка при обращении к AI (код: {e.response.status_code}). Пожалуйста, проверь свой API ключ Groq на Railway."
    except httpx.RequestError as e:
        logger.error(f"Ошибка запроса к Groq: {e}")
        return "📡 Проблема с подключением к AI. Возможно, временные неполадки."
    except (KeyError, IndexError) as e:
        logger.error(f"Ошибка парсинга ответа Groq: {e}")
        return "🤯 Получен неожиданный или неполный ответ от AI. Попробуй еще раз."
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в ask_groq: {e}", exc_info=True)
        return "💥 Ой, что-то пошло совсем не так! Разработчик уже уведомлен (наверное)."


# --- Функции для ConversationHandler (создание профиля - как раньше, с логами и фиксом Markdown) ---
# start_command, handle_gender_and_ask_age, handle_age_and_ask_height,
# handle_height_and_ask_weight, handle_weight_and_ask_activity, handle_activity_and_ask_goal,
# process_final_profile, cancel_onboarding - остаются такими же, как в предыдущей версии (v2.2)

# (Вставь сюда код этих функций из предыдущего ответа - он длинный, чтобы не дублировать здесь полностью)
# Ключевые моменты:
# - start_command проверяет PROFILE_COMPLETE
# - handle_* функции корректно переходят по состояниям
# - process_final_profile с логами, проверками и исправленным Markdown для summary

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: # Копипаста из v2.2
    user = update.effective_user
    if context.user_data.get(PROFILE_COMPLETE):
        await update.message.reply_text(
            f"👋 С возвращением, {user.first_name}!\nТвой профиль уже со мной. Чем могу быть полезен сегодня?\n"
            "Используй /menu для навигации или просто спроси!",
            parse_mode=ParseMode.MARKDOWN
        )
        return ConversationHandler.END
    if not context.user_data.get(GENDER):
        context.user_data.clear()
        logger.info(f"User {user.id} ({user.username}) начинает создание профиля.")
    await update.message.reply_text(
        f"🌟 Привет, {user.first_name}! Я *ФитГуру* – твой личный AI-диетолог и тренер.\n\n"
        "Чтобы наши тренировки и планы питания были максимально эффективными, мне нужно немного узнать о тебе. "
        "Это быстро и абсолютно конфиденциально! 🤫\n\n"
        "🚹🚺 Для начала, укажи, пожалуйста, свой *пол*:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("👨 Мужской", callback_data="мужской"), InlineKeyboardButton("👩 Женский", callback_data="женский")]
        ]),
        parse_mode=ParseMode.MARKDOWN
    )
    return PROFILE_GENDER

async def handle_gender_and_ask_age(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: # Копипаста из v2.2
    query = update.callback_query
    await query.answer()
    context.user_data[GENDER] = query.data
    await query.edit_message_text(
        text=f"Пол: *{query.data.capitalize()}*. Отлично! 👍\n\n🎂 Теперь введи свой *возраст* (полных лет):",
        parse_mode=ParseMode.MARKDOWN
    )
    return PROFILE_AGE

async def handle_age_and_ask_height(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: # Копипаста из v2.2
    try:
        age = int(update.message.text)
        if not 10 <= age <= 100: raise ValueError("Возраст вне допустимого диапазона")
        context.user_data[AGE] = age
        await update.message.reply_text(
            text=f"Возраст: *{age} лет*. Замечательно! ✅\n\n📏 Теперь введи свой *рост* (в сантиметрах):",
            parse_mode=ParseMode.MARKDOWN
        )
        return PROFILE_HEIGHT
    except ValueError as e:
        logger.warning(f"User {update.effective_user.id} ввел некорректный возраст: {update.message.text}. Ошибка: {e}")
        await update.message.reply_text("🤔 Хм, возраст должен быть целым числом от 10 до 100 (например, 25). Попробуй еще раз!")
        return PROFILE_AGE

async def handle_height_and_ask_weight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: # Копипаста из v2.2
    try:
        height = int(update.message.text)
        if not 100 <= height <= 250: raise ValueError("Рост вне допустимого диапазона")
        context.user_data[HEIGHT] = height
        await update.message.reply_text(
            text=f"Рост: *{height} см*. Записал! 📝\n\n⚖️ Теперь введи свой текущий *вес* (в кг, например, 70.5):",
            parse_mode=ParseMode.MARKDOWN
        )
        return PROFILE_WEIGHT
    except ValueError as e:
        logger.warning(f"User {update.effective_user.id} ввел некорректный рост: {update.message.text}. Ошибка: {e}")
        await update.message.reply_text("🤔 Рост должен быть целым числом в сантиметрах от 100 до 250 (например, 175). Попробуй снова!")
        return PROFILE_HEIGHT

async def handle_weight_and_ask_activity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: # Копипаста из v2.2
    try:
        weight = float(update.message.text.replace(',', '.'))
        if not 30 <= weight <= 300: raise ValueError("Вес вне допустимого диапазона")
        context.user_data[CURRENT_WEIGHT] = weight
        activity_buttons = [
            [InlineKeyboardButton("🧘‍♀️ Минимальная (сидячая работа)", callback_data="минимальная")],
            [InlineKeyboardButton("🚶‍♀️ Легкая (тренировки 1-3 р/нед)", callback_data="легкая")],
            [InlineKeyboardButton("🏃‍♀️ Средняя (тренировки 3-5 р/нед)", callback_data="средняя")],
            [InlineKeyboardButton("🏋️‍♀️ Высокая (интенсивные 6-7 р/нед)", callback_data="высокая")],
            [InlineKeyboardButton("🔥 Экстремальная (физ. работа + спорт)", callback_data="экстремальная")]
        ]
        await update.message.reply_text(
            text=f"Вес: *{weight} кг*. Принято! 👌\n\n🤸‍♀️ Оцени свой обычный уровень *физической активности*:",
            reply_markup=InlineKeyboardMarkup(activity_buttons),
            parse_mode=ParseMode.MARKDOWN
        )
        return PROFILE_ACTIVITY
    except ValueError as e:
        logger.warning(f"User {update.effective_user.id} ввел некорректный вес: {update.message.text}. Ошибка: {e}")
        await update.message.reply_text("🤔 Вес должен быть числом от 30 до 300 (например, 70.5). Давай еще разок!")
        return PROFILE_WEIGHT

async def handle_activity_and_ask_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: # Копипаста из v2.2
    query = update.callback_query
    await query.answer()
    context.user_data[ACTIVITY_LEVEL] = query.data
    await query.edit_message_text(
        text=f"Уровень активности: *{query.data.capitalize()}*. Супер! 🚀\n\n🎯 Какая твоя основная *цель*?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📉 Похудеть", callback_data="похудеть")],
            [InlineKeyboardButton("⚖️ Поддерживать вес", callback_data="поддерживать вес")],
            [InlineKeyboardButton("💪 Набрать массу", callback_data="набрать массу")]
        ]),
        parse_mode=ParseMode.MARKDOWN
    )
    return PROFILE_GOAL

async def process_final_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # ... (начало функции, расчеты BMI, BMR, TDEE, TARGET_CALORIES как раньше) ...
    query = update.callback_query # Добавил
    user_id = update.effective_user.id # Добавил
    logger.info(f"User {user_id}: Entered process_final_profile with callback_data: {query.data}") # Добавил

    await query.answer() # Добавил
    context.user_data[GOAL] = query.data # Добавил
    ud = context.user_data # Добавил

    logger.info(f"User {user_id}: Goal '{ud.get(GOAL)}' saved. User data before calcs: {ud}") # Добавил

    try: # Добавил
        # Проверка наличия всех необходимых данных перед расчетами
        required_keys = [CURRENT_WEIGHT, HEIGHT, AGE, GENDER, ACTIVITY_LEVEL, GOAL]
        missing_keys = [key for key in required_keys if ud.get(key) is None]
        if missing_keys:
            logger.error(f"User {user_id}: Missing keys in user_data for calculation: {missing_keys}")
            await query.edit_message_text("Ой, не хватает некоторых данных для расчета профиля. 😥 Пожалуйста, попробуй начать заново с /start.")
            context.user_data.pop(PROFILE_COMPLETE, None)
            return ConversationHandler.END

        ud[BMI] = calculate_bmi(ud.get(CURRENT_WEIGHT), ud.get(HEIGHT))
        ud[BMR] = calculate_bmr(ud.get(CURRENT_WEIGHT), ud.get(HEIGHT), ud.get(AGE), ud.get(GENDER))
        ud[TDEE] = calculate_tdee(ud.get(BMR), ud.get(ACTIVITY_LEVEL))
        ud[TARGET_CALORIES] = calculate_target_calories(ud.get(TDEE), ud.get(GOAL))
        
        logger.info(f"User {user_id}: Calculations complete. BMI: {ud.get(BMI)}, BMR: {ud.get(BMR)}, TDEE: {ud.get(TDEE)}, TARGET_CALORIES: {ud.get(TARGET_CALORIES)}")

        if None in [ud.get(BMI), ud.get(BMR), ud.get(TDEE), ud.get(TARGET_CALORIES)]:
            logger.error(f"User {user_id}: One or more calculated values are None. Data: ...") # Сократил для краткости
            await query.edit_message_text("Ой, ошибка при расчете. Попробуй /start.")
            context.user_data.pop(PROFILE_COMPLETE, None)
            return ConversationHandler.END

        ud[PROFILE_COMPLETE] = True
        logger.info(f"User {user_id}: PROFILE_COMPLETE set to True.")
        
        ud.pop(AWAITING_WEIGHT_UPDATE, None)
        # Конец добавленных строк (выше)

        # Расчет прогноза изменения веса
        weight_change_prediction_text = ""
        if ud.get(TDEE) and ud.get(TARGET_CALORIES):
            daily_deficit_or_surplus = ud.get(TARGET_CALORIES) - ud.get(TDEE)
            # Примерно 7700 ккал на 1 кг жира (или мышц, но для мышц сложнее)
            # Недельный дефицит/профицит
            weekly_change_kcal = daily_deficit_or_surplus * 7
            # Примерное изменение веса в кг за неделю
            weekly_weight_change_kg = weekly_change_kcal / 7700 
            
            if weekly_weight_change_kg < -0.1: # Если теряем больше 100г
                weight_change_prediction_text = (
                    f"📈 При таком потреблении калорий ты можешь терять примерно "
                    f"*{abs(weekly_weight_change_kg):.1f} кг* в неделю.\n"
                )
            elif weekly_weight_change_kg > 0.1: # Если набираем больше 100г
                weight_change_prediction_text = (
                    f"📈 При таком потреблении калорий ты можешь набирать примерно "
                    f"*{weekly_weight_change_kg:.1f} кг* в неделю.\n"
                )
            else:
                weight_change_prediction_text = (
                    f"⚖️ Твое потребление калорий близко к поддержанию текущего веса.\n"
                )
        
        bmi_interp = get_bmi_interpretation(ud.get(BMI))
        summary = (
            f"🎉 *Поздравляю!* Твой профиль полностью готов. Вот твои ключевые показатели:\n\n"
            # ... (остальная часть summary как раньше) ...
            f"  - ✨ *Рекомендуемые калории для цели:* `{ud.get(TARGET_CALORIES, 'N/A')}` *ккал/день* ✨\n"
            f"{weight_change_prediction_text}\n" # ДОБАВЛЕНА СТРОКА С ПРОГНОЗОМ
            "Теперь я готов помогать тебе на пути к цели! Используй /menu для быстрого доступа к функциям.\n\n"
            "⚠️ *Помни, эти расчеты и прогнозы носят рекомендательный характер. Для точных медицинских советов проконсультируйся с врачом.*"
        )
        await query.edit_message_text(text=summary, parse_mode=ParseMode.MARKDOWN)
        logger.info(f"User {user_id}: Profile summary sent. Exiting ConversationHandler.")
        return ConversationHandler.END

    except Exception as e:
        # ... (обработка ошибок как раньше) ...
        logger.error(f"User {user_id}: ERROR in process_final_profile: {e}", exc_info=True) # Добавил
        # Попытка отправить более простое сообщение об ошибке, если предыдущее не удалось
        try: # Добавил
            await query.edit_message_text( # Добавил
                "Ой, что-то пошло не так при расчете твоего профиля. 😥 Попробуй начать заново с /start."
            )
        except Exception as e_fallback: # Добавил
            logger.error(f"User {user_id}: Failed to send fallback error message: {e_fallback}") # Добавил
            # Если даже это не удается, просто логируем
        context.user_data.pop(PROFILE_COMPLETE, None) # Добавил
        return ConversationHandler.END # Добавил


# --- Обычные команды ---
# menu_command, help_command, my_profile_command, weight_command_entry (как в v2.2)
async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE): # Копипаста из v2.2
    if not context.user_data.get(PROFILE_COMPLETE):
        await update.message.reply_text("Сначала давай создадим твой профиль! Нажми /start 😊", parse_mode=ParseMode.MARKDOWN)
        return
    menu_buttons = [
        [KeyboardButton("🏋️‍♂️ Тренировка (/train)"), KeyboardButton("⚖️ Обновить вес (/weight)")],
        [KeyboardButton("📊 Мой профиль (/myprofile)"), KeyboardButton("❓ Помощь (/help)")],
    ]
    await update.message.reply_text(
        "👇 Вот что мы можем сделать:",
        reply_markup=ReplyKeyboardMarkup(menu_buttons, resize_keyboard=True, one_time_keyboard=False)
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE): # Копипаста из v2.2
    help_text = (
        "👋 Привет! Я *ФитГуру* – твой гид в мире фитнеса и здорового питания.\n\n"
        "📌 *Основные команды:*\n"
        "/start - Начать работу, создать или просмотреть профиль.\n"
        "/menu - Показать главное меню с кнопками.\n"
        "/myprofile - Твой текущий фитнес-профиль и показатели.\n"
        "/train - Получить персонализированную тренировку.\n"
        "/weight - Обновить свой текущий вес.\n"
        "/cancel - (Во время создания профиля) Отменить процесс.\n"
        "/help - Это сообщение.\n\n"
        "🍏 Если твой профиль создан, просто напиши мне свой вопрос о фитнесе или питании, используя команды /ask или /train." # Изменено
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

async def my_profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE): # Копипаста из v2.2
    if not context.user_data.get(PROFILE_COMPLETE):
        await update.message.reply_text("Твой профиль еще не создан. Пожалуйста, начни с /start 🌟")
        return
    ud = context.user_data
    bmi_interp = get_bmi_interpretation(ud.get(BMI))
    summary = (
        f"👤 *Твой фитнес-профиль, {update.effective_user.first_name}:*\n\n"
        f"  - Пол: _{ud.get(GENDER, 'N/A').capitalize()}_\n"
        f"  - Возраст: _{ud.get(AGE, 'N/A')} лет_\n"
        f"  - Рост: _{ud.get(HEIGHT, 'N/A')} см_\n"
        f"  - Вес: *{ud.get(CURRENT_WEIGHT, 'N/A')} кг*\n"
        f"  - Активность: _{ud.get(ACTIVITY_LEVEL, 'N/A').capitalize()}_\n"
        f"  - Цель: _{ud.get(GOAL, 'N/A').capitalize()}_\n\n"
        f"📊 *Расчетные показатели:*\n"
        f"  - ИМТ: *{ud.get(BMI, 'N/A')}*{bmi_interp}\n"
        f"  - BMR: *{ud.get(BMR, 'N/A')} ккал/день*\n"
        f"  - TDEE: *{ud.get(TDEE, 'N/A')} ккал/день*\n"
        f"  - Рекомендуемые калории: `{ud.get(TARGET_CALORIES, 'N/A')}` *ккал/день*\n\n"
        "Для обновления веса используй /weight. Чтобы начать профиль заново, нажми /start (старый будет удален)."
    )
    await update.message.reply_text(summary, parse_mode=ParseMode.MARKDOWN)

async def weight_command_entry(update: Update, context: ContextTypes.DEFAULT_TYPE): # Копипаста из v2.2
    if not context.user_data.get(PROFILE_COMPLETE):
        await update.message.reply_text("Сначала давай создадим твой профиль! Нажми /start 😊")
        return
    await update.message.reply_text("⚖️ Введи свой *текущий вес* (например, 70.5):", parse_mode=ParseMode.MARKDOWN)
    context.user_data[AWAITING_WEIGHT_UPDATE] = True

async def handle_weight_update(update: Update, context: ContextTypes.DEFAULT_TYPE): # Копипаста из v2.2, но с доп. проверкой
    try:
        new_weight = float(update.message.text.replace(',', '.'))
        if not 30 <= new_weight <= 300: raise ValueError("Некорректный вес")
        ud = context.user_data
        if not all(ud.get(key) for key in [HEIGHT, AGE, GENDER, ACTIVITY_LEVEL, GOAL]):
            logger.error(f"User {update.effective_user.id}: Missing profile data for weight update recalcs.")
            await update.message.reply_text("Не удалось обновить показатели, не хватает данных профиля. Попробуй /myprofile или /start.")
            ud.pop(AWAITING_WEIGHT_UPDATE, None)
            return
        ud[CURRENT_WEIGHT] = new_weight
        ud[BMI] = calculate_bmi(new_weight, ud.get(HEIGHT))
        ud[BMR] = calculate_bmr(new_weight, ud.get(HEIGHT), ud.get(AGE), ud.get(GENDER))
        ud[TDEE] = calculate_tdee(ud.get(BMR), ud.get(ACTIVITY_LEVEL))
        ud[TARGET_CALORIES] = calculate_target_calories(ud.get(TDEE), ud.get(GOAL))
        ud.pop(AWAITING_WEIGHT_UPDATE, None)
        bmi_interp = get_bmi_interpretation(ud.get(BMI))
        await update.message.reply_text(
            f"✅ Вес *{new_weight} кг* успешно обновлен! Твои показатели пересчитаны:\n"
            f"  - ИМТ: *{ud.get(BMI, 'N/A')}*{bmi_interp}\n"
            f"  - Рекомендуемые калории: `{ud.get(TARGET_CALORIES, 'N/A')}` *ккал/день*",
            parse_mode=ParseMode.MARKDOWN
        )
        logger.info(f"Пользователь {update.effective_user.id} обновил вес: {new_weight} кг.")
    except ValueError:
        await update.message.reply_text("🤔 Вес должен быть числом (например, 70.5). Попробуй еще раз /weight или введи вес снова.")
    except Exception as e:
        logger.error(f"Ошибка в handle_weight_update: {e}", exc_info=True)
        await update.message.reply_text("💥 Ой, произошла ошибка при обновлении веса.")
        context.user_data.pop(AWAITING_WEIGHT_UPDATE, None)

# --- Улучшенная команда /train ---
async def train_command_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get(PROFILE_COMPLETE):
        await update.message.reply_text("Чтобы подобрать тренировку, мне нужен твой профиль. Начни с /start 🌟", parse_mode=ParseMode.MARKDOWN)
        return

    keyboard = [
        [InlineKeyboardButton("🏠 Дома (без оборудования)", callback_data="train_home")],
        [InlineKeyboardButton("🏋️‍♂️ В зале", callback_data="train_gym")],
        [InlineKeyboardButton("🌳 На улице", callback_data="train_street")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Определяем, откуда пришел запрос, чтобы правильно ответить
    if update.callback_query: # Если это callback от другой кнопки (маловероятно для /train)
        await update.callback_query.message.reply_text(
            "Отлично! Где ты планируешь заниматься?",
            reply_markup=reply_markup
        )
    elif update.message: # Если это команда /train или текст от кнопки меню
         await update.message.reply_text(
            "Отлично! Где ты планируешь заниматься?",
            reply_markup=reply_markup
        )

# В функции handle_train_location_and_generate обновляем промпт:
async def handle_train_location_and_generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (получение query, location_choice, profile_info как раньше) ...
    query = update.callback_query # Добавил, т.к. ниже используется
    await query.answer() # Добавил
    
    location_choice = query.data # Добавил для ясности, что это из callback_data
    location_text = ""
    equipment_text = ""

    if location_choice == "train_home":
        location_text = "для дома"
        equipment_text = "без специального оборудования"
    elif location_choice == "train_gym":
        location_text = "для тренажерного зала"
        equipment_text = "с использованием стандартного оборудования зала"
    elif location_choice == "train_street":
        location_text = "для улицы (воркаут площадка или открытое пространство)"
        equipment_text = "с минимальным оборудованием или без него (например, собственным весом, турники, брусья)"
    
    ud = context.user_data # Добавил для ясности
    profile_info = (f"Профиль: Пол:{ud.get(GENDER,'N/A')}, Возраст:{ud.get(AGE,'N/A')}, "
                    f"Рост:{ud.get(HEIGHT,'N/A')}см, Вес:{ud.get(CURRENT_WEIGHT,'N/A')}кг, "
                    f"Активность:{ud.get(ACTIVITY_LEVEL,'N/A')}, Цель:{ud.get(GOAL,'N/A')}. "
                    f"ИМТ:{ud.get(BMI,'N/A')}, Рекомендуемые калории для цели:{ud.get(TARGET_CALORIES,'N/A')}.")    
     prompt = (
        f"{profile_info} Пользователь хочет тренировку {location_text}, {equipment_text}. "
        "Создай программу тренировок на 30-45 минут. Ответ должен быть полностью на русском языке, очень грамотным и естественным, без странных фраз. "
        "Для каждого упражнения четко укажи:\n"
        "1. Название упражнения (без использования Markdown заголовков типа `####` перед названием).\n"
        "2. Краткое и понятное описание техники выполнения этого упражнения (1-2 предложения).\n"
        "3. Количество подходов (например, 3-4).\n"
        "4. Количество повторений в каждом подходе (например, 10-15 или до отказа для некоторых).\n"
        "5. Примерное время отдыха между подходами (например, 60-90 секунд).\n"
        "Не обсуждай рабочий вес отягощений, если это тренировка не в зале или пользователь не просил об этом отдельно. "
        "Раздели тренировку на секции: Разминка, Основная часть, Заминка. Используй для названий секций Markdown заголовки уровня 2 (##). "
        "В самом конце, отдельным абзацем, четко укажи: '🔥 Примерно сожжено калорий за эту тренировку: X-Y ккал.'. Замени X-Y на реалистичную оценку. "
        "Стиль — дружелюбный и мотивирующий тренер. Ответ должен быть хорошо структурирован с использованием Markdown (списки -, жирный шрифт * для акцентов)."
    )
    
    await query.edit_message_text("🏋️‍♂️ Подбираю для тебя *персонализированную тренировку*... Пожалуйста, подожди!", parse_mode=ParseMode.MARKDOWN)
    # Используем чуть более высокую температуру для креативности в подборе упражнений, но не слишком высокую.
    reply = await ask_groq(prompt, temperature=0.65) 
    await query.message.reply_text(reply, parse_mode=ParseMode.MARKDOWN)


# --- Обработчик общих сообщений (теперь не вызывает AI) ---
async def general_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    
    # Сначала обрабатываем тексты от кнопок ReplyKeyboard
    if user_message == "🏋️‍♂️ Тренировка (/train)":
        await train_command_entry(update, context) # Используем новую функцию входа
        return
    if user_message == "⚖️ Обновить вес (/weight)":
        await weight_command_entry(update, context)
        return
    if user_message == "📊 Мой профиль (/myprofile)":
        await my_profile_command(update, context)
        return
    if user_message == "❓ Помощь (/help)":
        await help_command(update, context)
        return
        
    if context.user_data.get(AWAITING_WEIGHT_UPDATE) is True:
        await handle_weight_update(update, context)
        return

    # Если это не кнопка меню и не ожидание веса, то это обычное сообщение
    logger.info(f"Получено обычное текстовое сообщение от {update.effective_user.id} ({update.effective_user.username}): '{user_message}'. AI не будет вызван.")
    await update.message.reply_text(
        "🤖 Хм, я не совсем понял твой запрос. Если нужна помощь, используй /help или кнопки в /menu. "
        "Я пока не умею свободно общаться на любые темы, но могу помочь с тренировками и профилем! 😊",
        parse_mode=ParseMode.MARKDOWN
    )

# --- Основная функция ---
def main():
    if not TELEGRAM_TOKEN:
        logger.critical("TELEGRAM_TOKEN не найден! Бот не может запуститься.")
        return
    if not GROQ_API_KEY:
        logger.warning("GROQ_API_KEY не установлен. AI-функции (тренировки) не будут работать.")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    onboarding_conv_handler = ConversationHandler( # Как в v2.2
        entry_points=[CommandHandler("start", start_command)],
        states={
            PROFILE_GENDER: [CallbackQueryHandler(handle_gender_and_ask_age, pattern="^(мужской|женский)$")],
            PROFILE_AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_age_and_ask_height)],
            PROFILE_HEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_height_and_ask_weight)],
            PROFILE_WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_weight_and_ask_activity)],
            PROFILE_ACTIVITY: [CallbackQueryHandler(handle_activity_and_ask_goal, pattern="^(минимальная|легкая|средняя|высокая|экстремальная)$")],
            PROFILE_GOAL: [CallbackQueryHandler(process_final_profile, pattern="^(похудеть|поддерживать вес|набрать массу)$")],
        },
        fallbacks=[CommandHandler("cancel", cancel_onboarding), CommandHandler("start", start_command)],
        allow_reentry=True, per_user=True, per_chat=True,
    )
    app.add_handler(onboarding_conv_handler)

    # Новые обработчики для команды /train
    app.add_handler(CommandHandler("train", train_command_entry))
    app.add_handler(CallbackQueryHandler(handle_train_location_and_generate, pattern="^train_(home|gym|street)$"))


    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("myprofile", my_profile_command))
    app.add_handler(CommandHandler("weight", weight_command_entry))
    # Команда /train теперь обрабатывается train_command_entry

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, general_message_handler))

    logger.info("🤖 Бот ФитГуру v2.3 (улучшенные тренировки, контроль AI) запускается...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()