import logging
import os
import httpx
import json
from datetime import datetime, date

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
# (Как в предыдущей версии v2.7)
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)
if not GROQ_API_KEY: logger.warning("GROQ_API_KEY не установлен. AI-функции не будут работать.")
if not TELEGRAM_TOKEN: logger.critical("TELEGRAM_TOKEN не найден! Бот не может запуститься."); exit(1) # Можно сделать exit()

# --- Системный промпт для AI (как в v2.7) ---
SYSTEM_PROMPT_DIETITIAN = """...""" # Вставь сюда полный SYSTEM_PROMPT_DIETITIAN из v2.7

# --- Состояния и Ключи (как в v2.7) ---
(PROFILE_GENDER, PROFILE_AGE, PROFILE_HEIGHT, PROFILE_WEIGHT,
 PROFILE_ACTIVITY, PROFILE_GOAL) = range(6)
(ADDMEAL_CHOOSE_TYPE, ADDMEAL_GET_DESCRIPTION) = range(PROFILE_GOAL + 1, PROFILE_GOAL + 3)
GENDER, AGE, HEIGHT, CURRENT_WEIGHT, ACTIVITY_LEVEL, GOAL = "gender", "age", "height", "current_weight", "activity_level", "goal"
PROFILE_COMPLETE, BMI, BMR, TDEE, TARGET_CALORIES = "profile_complete", "bmi", "bmr", "tdee", "target_calories"
AWAITING_WEIGHT_UPDATE, TODAY_MEALS, LAST_MEAL_DATE = "awaiting_weight_update", "today_meals", "last_meal_date"
ACTIVITY_FACTORS = {"минимальная": 1.2, "легкая": 1.375, "средняя": 1.55, "высокая": 1.725, "экстремальная": 1.9}
GOAL_FACTORS = {"похудеть": -500, "поддерживать вес": 0, "набрать массу": 300}

# --- Вспомогательные функции для расчетов (как в v2.7) ---
# (Вставь сюда calculate_bmi, calculate_bmr, calculate_tdee, calculate_target_calories, get_bmi_interpretation из v2.7)
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


# --- Функция для запросов к Groq API (как в v2.7) ---
async def ask_groq(user_message: str, model: str = "gemma2-9b-it", system_prompt_override: str = None, temperature: float = 0.5):
    # (Вставь сюда полный код ask_groq из v2.7)
    if not GROQ_API_KEY:
        logger.warning("GROQ_API_KEY не установлен. AI запрос не будет выполнен.")
        return "К сожалению, я сейчас не могу связаться со своим AI-мозгом. Попробуйте позже или проверьте настройки API ключа."
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    current_system_prompt = system_prompt_override if system_prompt_override else SYSTEM_PROMPT_DIETITIAN
    data = {"messages": [{"role": "system", "content": current_system_prompt}, {"role": "user", "content": user_message}], "model": model, "temperature": temperature}
    logger.info(f"Отправка запроса к Groq. Модель: {model}, Температура: {temperature}. Сообщение: {user_message[:100]}...")
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=data, timeout=30.0)
            response.raise_for_status()
            response_data = response.json()
            if response_data.get("choices") and response_data["choices"][0].get("message"):
                logger.info(f"Успешный ответ от Groq ({model}).")
                return response_data["choices"][0]["message"]["content"]
            logger.error(f"Неожиданная структура ответа от Groq ({model}): {response_data}")
            return "🤖 Извини, у меня небольшие технические шоколадки с AI. Структура ответа некорректна."
    except httpx.HTTPStatusError as e:
        logger.error(f"Ошибка HTTP от Groq ({model}): {e.response.status_code} - {e.response.text}")
        if "model_decommissioned" in e.response.text: return f"🔌 Ой, похоже, выбранная модель AI ({model}) больше не доступна. Разработчик уже в курсе!"
        return f"🔌 Ошибка при обращении к AI (код: {e.response.status_code}). Пожалуйста, проверь свой API ключ Groq."
    except httpx.ReadTimeout:
        logger.error(f"Таймаут чтения ответа от Groq API ({model}). Модель слишком долго генерировала ответ.")
        return "⏳ AI задумался слишком надолго и не успел ответить за 30 секунд. Попробуй, пожалуйста, еще раз или выбери другую опцию."
    except httpx.TimeoutException as e:
        logger.error(f"Общий таймаут при запросе к Groq API ({model}): {e}")
        return "⏳ Упс, не удалось связаться с AI вовремя (таймаут). Попробуй, пожалуйста, еще раз чуть позже."
    except httpx.RequestError as e:
        logger.error(f"Ошибка запроса к Groq API ({model}): {e}")
        return "📡 Проблема с подключением к AI. Возможно, временные неполадки в сети."
    except (KeyError, IndexError) as e:
        logger.error(f"Ошибка парсинга ответа от Groq API ({model}): {e}")
        return "🤯 Получен неожиданный или неполный ответ от AI. Попробуй еще раз."
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в ask_groq ({model}): {e}", exc_info=True)
        return "💥 Ой, что-то пошло совсем не так с AI! Разработчик уже в курсе."

# --- Функции для ConversationHandler (создание профиля - как в v2.7) ---
# (Вставь сюда start_command ... process_final_profile, cancel_onboarding из v2.7)
# --- Копипаста функций онбординга из v2.7 (с коррекцией для LAST_MEAL_DATE в cancel_onboarding) ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    today_str = date.today().isoformat()
    if context.user_data.get(LAST_MEAL_DATE) != today_str:
        context.user_data[TODAY_MEALS] = []
        context.user_data[LAST_MEAL_DATE] = today_str
        logger.info(f"User {user.id}: Новый день, данные о приемах пищи сброшены.")
    if context.user_data.get(PROFILE_COMPLETE):
        await update.message.reply_text(f"👋 С возвращением, {user.first_name}!\nТвой профиль уже со мной. Чем могу быть полезен сегодня?\nИспользуй /menu для навигации или просто спроси!",parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END
    if not context.user_data.get(GENDER):
        context.user_data.clear()
        context.user_data[LAST_MEAL_DATE] = today_str 
        context.user_data[TODAY_MEALS] = []
        logger.info(f"User {user.id} ({user.username}) начинает создание профиля.")
    else: logger.info(f"User {user.id} ({user.username}) продолжает создание профиля.")
    await update.message.reply_text(f"🌟 Привет, {user.first_name}! Я *ФитГуру* – твой личный AI-диетолог и тренер.\n\nЧтобы наши тренировки и планы питания были максимально эффективными, мне нужно немного узнать о тебе. Это быстро и абсолютно конфиденциально! 🤫\n\n🚹🚺 Для начала, укажи, пожалуйста, свой *пол*:",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👨 Мужской", callback_data="мужской"), InlineKeyboardButton("👩 Женский", callback_data="женский")]]),parse_mode=ParseMode.MARKDOWN)
    return PROFILE_GENDER
async def handle_gender_and_ask_age(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer(); context.user_data[GENDER] = query.data
    await query.edit_message_text(text=f"Пол: *{query.data.capitalize()}*. Отлично! 👍\n\n🎂 Теперь введи свой *возраст* (полных лет):",parse_mode=ParseMode.MARKDOWN)
    return PROFILE_AGE
async def handle_age_and_ask_height(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        age = int(update.message.text)
        if not 10 <= age <= 100: raise ValueError("Возраст вне допустимого диапазона")
        context.user_data[AGE] = age
        await update.message.reply_text(text=f"Возраст: *{age} лет*. Замечательно! ✅\n\n📏 Теперь введи свой *рост* (в сантиметрах):",parse_mode=ParseMode.MARKDOWN)
        return PROFILE_HEIGHT
    except ValueError as e:
        logger.warning(f"User {update.effective_user.id} ввел некорректный возраст: {update.message.text}. Ошибка: {e}")
        await update.message.reply_text("🤔 Хм, возраст должен быть целым числом от 10 до 100 (например, 25). Попробуй еще раз!")
        return PROFILE_AGE
async def handle_height_and_ask_weight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        height = int(update.message.text)
        if not 100 <= height <= 250: raise ValueError("Рост вне допустимого диапазона")
        context.user_data[HEIGHT] = height
        await update.message.reply_text(text=f"Рост: *{height} см*. Записал! 📝\n\n⚖️ Теперь введи свой текущий *вес* (в кг, например, 70.5):",parse_mode=ParseMode.MARKDOWN)
        return PROFILE_WEIGHT
    except ValueError as e:
        logger.warning(f"User {update.effective_user.id} ввел некорректный рост: {update.message.text}. Ошибка: {e}")
        await update.message.reply_text("🤔 Рост должен быть целым числом в сантиметрах от 100 до 250 (например, 175). Попробуй снова!")
        return PROFILE_HEIGHT
async def handle_weight_and_ask_activity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        weight = float(update.message.text.replace(',', '.'))
        if not 30 <= weight <= 300: raise ValueError("Вес вне допустимого диапазона")
        context.user_data[CURRENT_WEIGHT] = weight
        activity_buttons = [[InlineKeyboardButton("🧘‍♀️ Минимальная (сидячая работа)", callback_data="минимальная")], [InlineKeyboardButton("🚶‍♀️ Легкая (тренировки 1-3 р/нед)", callback_data="легкая")], [InlineKeyboardButton("🏃‍♀️ Средняя (тренировки 3-5 р/нед)", callback_data="средняя")], [InlineKeyboardButton("🏋️‍♀️ Высокая (интенсивные 6-7 р/нед)", callback_data="высокая")], [InlineKeyboardButton("🔥 Экстремальная (физ. работа + спорт)", callback_data="экстремальная")]]
        await update.message.reply_text(text=f"Вес: *{weight} кг*. Принято! 👌\n\n🤸‍♀️ Оцени свой обычный уровень *физической активности*:", reply_markup=InlineKeyboardMarkup(activity_buttons), parse_mode=ParseMode.MARKDOWN)
        return PROFILE_ACTIVITY
    except ValueError as e:
        logger.warning(f"User {update.effective_user.id} ввел некорректный вес: {update.message.text}. Ошибка: {e}")
        await update.message.reply_text("🤔 Вес должен быть числом от 30 до 300 (например, 70.5). Давай еще разок!")
        return PROFILE_WEIGHT
async def handle_activity_and_ask_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer(); context.user_data[ACTIVITY_LEVEL] = query.data
    await query.edit_message_text(text=f"Уровень активности: *{query.data.capitalize()}*. Супер! 🚀\n\n🎯 Какая твоя основная *цель*?", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📉 Похудеть", callback_data="похудеть")], [InlineKeyboardButton("⚖️ Поддерживать вес", callback_data="поддерживать вес")], [InlineKeyboardButton("💪 Набрать массу", callback_data="набрать массу")]]), parse_mode=ParseMode.MARKDOWN)
    return PROFILE_GOAL
async def process_final_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; user_id = update.effective_user.id
    logger.info(f"User {user_id}: Entered process_final_profile with callback_data: {query.data}")
    await query.answer(); context.user_data[GOAL] = query.data; ud = context.user_data
    logger.info(f"User {user_id}: Goal '{ud.get(GOAL)}' saved. User data before calcs: {ud}")
    try:
        required_keys = [CURRENT_WEIGHT, HEIGHT, AGE, GENDER, ACTIVITY_LEVEL, GOAL]
        missing_keys = [key for key in required_keys if ud.get(key) is None]
        if missing_keys:
            logger.error(f"User {user_id}: Missing keys for calculation: {missing_keys}")
            await query.edit_message_text("Ой, не хватает данных. 😥 /start заново.")
            ud.pop(PROFILE_COMPLETE, None); return ConversationHandler.END
        ud[BMI] = calculate_bmi(ud.get(CURRENT_WEIGHT), ud.get(HEIGHT))
        ud[BMR] = calculate_bmr(ud.get(CURRENT_WEIGHT), ud.get(HEIGHT), ud.get(AGE), ud.get(GENDER))
        ud[TDEE] = calculate_tdee(ud.get(BMR), ud.get(ACTIVITY_LEVEL))
        ud[TARGET_CALORIES] = calculate_target_calories(ud.get(TDEE), ud.get(GOAL))
        logger.info(f"User {user_id}: Calcs complete. BMI:{ud.get(BMI)}, BMR:{ud.get(BMR)}, TDEE:{ud.get(TDEE)}, TARGET_CALORIES:{ud.get(TARGET_CALORIES)}")
        if None in [ud.get(BMI), ud.get(BMR), ud.get(TDEE), ud.get(TARGET_CALORIES)]:
            logger.error(f"User {user_id}: Calculated values are None. Data: BMI={ud.get(BMI)}, BMR={ud.get(BMR)}, TDEE={ud.get(TDEE)}, TARGET_CALORIES={ud.get(TARGET_CALORIES)}")
            await query.edit_message_text("Ой, ошибка при расчете. 😥 /start заново.")
            ud.pop(PROFILE_COMPLETE, None); return ConversationHandler.END
        ud[PROFILE_COMPLETE] = True; logger.info(f"User {user_id}: PROFILE_COMPLETE set to True.")
        ud.pop(AWAITING_WEIGHT_UPDATE, None)
        weight_change_prediction_text = ""
        if ud.get(TDEE) and ud.get(TARGET_CALORIES):
            daily_diff = ud.get(TARGET_CALORIES) - ud.get(TDEE); weekly_kcal = daily_diff * 7
            weekly_kg = weekly_kcal / 7700 
            if weekly_kg < -0.05: weight_change_prediction_text = f"📈 При таком режиме ты можешь терять примерно *{abs(weekly_kg):.1f} кг* в неделю.\n"
            elif weekly_kg > 0.05: weight_change_prediction_text = f"📈 При таком режиме ты можешь набирать примерно *{weekly_kg:.1f} кг* в неделю.\n"
            else: weight_change_prediction_text = f"⚖️ Твое потребление калорий близко к поддержанию текущего веса.\n"
        bmi_interp = get_bmi_interpretation(ud.get(BMI))
        summary = (f"🎉 *Поздравляю!* Твой профиль полностью готов. Вот твои ключевые показатели:\n\n👤 *Твой профиль:*\n  - Пол: _{ud.get(GENDER, 'N/A').capitalize()}_\n  - Возраст: _{ud.get(AGE, 'N/A')} лет_\n  - Рост: _{ud.get(HEIGHT, 'N/A')} см_\n  - Вес: _{ud.get(CURRENT_WEIGHT, 'N/A')} кг_\n  - Активность: _{ud.get(ACTIVITY_LEVEL, 'N/A').capitalize()}_\n  - Цель: _{ud.get(GOAL, 'N/A').capitalize()}_\n\n📊 *Расчетные показатели:*\n  - ИМТ: *{ud.get(BMI, 'N/A')}*{bmi_interp}\n  - BMR (базальный метаболизм): *{ud.get(BMR, 'N/A')} ккал/день*\n  - TDEE (суточная потребность): *{ud.get(TDEE, 'N/A')} ккал/день*\n  - ✨ *Рекомендуемые калории для цели:* `{ud.get(TARGET_CALORIES, 'N/A')}` *ккал/день* ✨\n{weight_change_prediction_text}\nТеперь я готов помогать тебе на пути к цели! Используй /menu для быстрого доступа к функциям.\n\n⚠️ *Помни, эти расчеты и прогнозы носят рекомендательный характер. Для точных медицинских советов проконсультируйся с врачом.*")
        await query.edit_message_text(text=summary, parse_mode=ParseMode.MARKDOWN)
        logger.info(f"User {user_id}: Profile summary sent. Exiting."); return ConversationHandler.END
    except Exception as e:
        logger.error(f"User {user_id}: ERROR in process_final_profile: {e}", exc_info=True)
        try: await query.edit_message_text("Ой, что-то пошло не так при расчете твоего профиля. 😥 Попробуй начать заново с /start.")
        except Exception as e_fallback: logger.error(f"User {user_id}: Failed to send fallback error message: {e_fallback}")
        ud.pop(PROFILE_COMPLETE, None); return ConversationHandler.END
async def cancel_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if not context.user_data.get(PROFILE_COMPLETE):
        logger.info(f"User {user_id} отменил создание профиля.")
        context.user_data.clear()
        context.user_data[LAST_MEAL_DATE] = date.today().isoformat()
        context.user_data[TODAY_MEALS] = []
        await update.message.reply_text("❌ Создание профиля отменено. Можешь начать заново командой /start.")
    else: await update.message.reply_text("👍 Твой профиль уже создан. Если хочешь начать заново, используй /start (старый профиль будет сброшен).")
    for key in ['current_meal_type', 'current_meal_description', AWAITING_WEIGHT_UPDATE]: context.user_data.pop(key, None)
    return ConversationHandler.END
# --- Конец копипасты функций онбординга ---

# --- Функции для ЗАПИСИ ПРИЕМОВ ПИЩИ (как в v2.7, с логами) ---
async def add_meal_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # ... (код add_meal_start из v2.7, с логами и проверкой дня) ...
    user_id = update.effective_user.id
    logger.info(f"User {user_id}: Initiating /addmeal.")
    today_str = date.today().isoformat()
    if context.user_data.get(LAST_MEAL_DATE) != today_str:
        context.user_data[TODAY_MEALS] = []
        context.user_data[LAST_MEAL_DATE] = today_str
        logger.info(f"User {user_id}: Новый день, данные о приемах пищи сброшены для /addmeal.")
        if update.message: await update.message.reply_text("☀️ Новый день - новые записи о питании!")
    if not context.user_data.get(PROFILE_COMPLETE):
        msg = update.callback_query.message if update.callback_query else update.message
        await msg.reply_text("Чтобы записывать приемы пищи, сначала создай профиль через /start 🌟")
        return ConversationHandler.END
    keyboard = [[InlineKeyboardButton("🍳 Завтрак", callback_data="meal_Завтрак")],[InlineKeyboardButton("🥗 Обед", callback_data="meal_Обед")],[InlineKeyboardButton("🍝 Ужин", callback_data="meal_Ужин")],[InlineKeyboardButton("🍎 Перекус", callback_data="meal_Перекус")],]
    msg_text = "Какой прием пищи ты хочешь записать?"
    target_message = update.message if update.message else update.callback_query.message
    if update.callback_query and update.callback_query.message.text == msg_text: # Предотвращаем редактирование того же сообщения с теми же кнопками
        logger.info(f"User {user_id}: add_meal_start called from callback, message text is the same, not editing.")
        # Можно просто ничего не делать или отправить новое сообщение, если edit_text нежелателен
        # await update.callback_query.message.reply_text("Пожалуйста, выбери тип приема пищи:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif update.callback_query:
        await update.callback_query.message.edit_text(msg_text, reply_markup=InlineKeyboardMarkup(keyboard))
    else: # update.message
        await update.message.reply_text(msg_text, reply_markup=InlineKeyboardMarkup(keyboard))
    return ADDMEAL_CHOOSE_TYPE
async def add_meal_choose_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user_id = update.effective_user.id
    logger.info(f"User {user_id}: In add_meal_choose_type, callback_data: {query.data}")
    await query.answer()
    meal_type_name = query.data.split('_')[1]
    context.user_data['current_meal_type'] = meal_type_name
    await query.edit_message_text(f"Записываем '{meal_type_name}'.\nОпиши подробно, что ты съел(а) и примерное количество (например, 'Овсянка на молоке 200г, 1 банан, кофе'):")
    return ADDMEAL_GET_DESCRIPTION
async def add_meal_get_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # ... (код add_meal_get_description из v2.7, с логами, JSON парсингом и вызовом show_today_calories) ...
    meal_description = update.message.text; ud = context.user_data; current_meal_type = ud.get('current_meal_type', 'Прием пищи'); user_id = update.effective_user.id
    logger.info(f"User {user_id}: In add_meal_get_description, meal_type: {current_meal_type}, description: {meal_description}")
    await update.message.reply_text(f"Понял! Анализирую калорийность для '{current_meal_type}'... 🤔 Это может занять до 30 секунд.")
    profile_info = (f"Профиль пользователя: Цель калорий в день: {ud.get(TARGET_CALORIES, 'не указана')} ккал. Текущий вес: {ud.get(CURRENT_WEIGHT, 'N/A')} кг. Цель: {ud.get(GOAL, 'N/A')}.")
    prompt = (f"{profile_info} Пользователь описывает съеденную пищу для приема '{current_meal_type}':\n'{meal_description}'\n\nТвоя задача: Оцени КБЖУ для каждого продукта/блюда. Верни ответ в СТРОГОМ JSON формате (только JSON, без текста до/после, все значения КБЖУ - числа):\n{{\n  \"meal_name\": \"{current_meal_type}\",\n  \"items\": [\n    {{\"name\": \"[Продукт 1]\", \"quantity\": \"[Кол-во 1]\", \"calories\": X, \"protein\": Y, \"fat\": Z, \"carbs\": W}},\n    {{\"name\": \"[Продукт 2]\", \"quantity\": \"[Кол-во 2]\", \"calories\": X, \"protein\": Y, \"fat\": Z, \"carbs\": W}}\n  ],\n  \"total\": {{\"calories\": X_total, \"protein\": Y_total, \"fat\": Z_total, \"carbs\": W_total}}\n}}\nЕсли продукт не можешь оценить, КБЖУ 0 или пропусти, но посчитай итог по остальным. Будь точным.")
    ai_response_json_str = await ask_groq(prompt, temperature=0.1)
    try:
        if ai_response_json_str.startswith("```json"): ai_response_json_str = ai_response_json_str[7:]
        if ai_response_json_str.endswith("```"): ai_response_json_str = ai_response_json_str[:-3]
        ai_response_json_str = ai_response_json_str.strip()
        logger.info(f"User {user_id}: AI response for meal: {ai_response_json_str}")
        meal_data = json.loads(ai_response_json_str)
        if not isinstance(meal_data, dict) or 'total' not in meal_data or not all(k in meal_data['total'] for k in ['calories', 'protein', 'fat', 'carbs']) or not all(isinstance(meal_data['total'][k], (int, float)) for k in ['calories', 'protein', 'fat', 'carbs']):
            raise ValueError("Ответ AI не содержит корректные числовые поля total КБЖУ или имеет неверную структуру.")
        meal_data['user_description'] = meal_description; meal_data['timestamp'] = datetime.now().isoformat()
        if TODAY_MEALS not in ud or not isinstance(ud[TODAY_MEALS], list): ud[TODAY_MEALS] = []
        ud[TODAY_MEALS].append(meal_data)
        total_cals = meal_data.get('total', {}).get('calories', 0)
        response_text = f"✅ Прием пищи '{current_meal_type}' записан!\nТы съел(а): {meal_description}\n"
        if isinstance(meal_data.get("items"), list) and meal_data["items"]:
            response_text += "Примерная оценка по продуктам:\n"
            for item in meal_data["items"]: response_text += f"  - {item.get('name','?')} ({item.get('quantity','?')}) ≈ {item.get('calories',0)} ккал\n"
        response_text += f"Всего за этот прием: *{total_cals} ккал*.\n\n"
        ud.pop('current_meal_type', None); ud.pop('current_meal_description', None)
        await show_today_calories(update, context, pre_text=response_text)
        return ConversationHandler.END
    except json.JSONDecodeError as e:
        logger.error(f"User {user_id}: Ошибка декодирования JSON от AI для еды: {e}. Ответ AI: '{ai_response_json_str}'")
        await update.message.reply_text("Ой, не смог разобрать ответ от AI по калориям. 🤖 Попробуй описать блюдо иначе.")
    except ValueError as e:
        logger.error(f"User {user_id}: Ошибка в структуре JSON от AI для еды: {e}. Ответ AI: '{ai_response_json_str}'")
        await update.message.reply_text("AI вернул данные в неожиданном формате. Попробуй еще раз.")
    except Exception as e:
        logger.error(f"User {user_id}: Непредвиденная ошибка в add_meal_get_description: {e}", exc_info=True)
        await update.message.reply_text("Произошла внутренняя ошибка при обработке приема пищи.")
    ud.pop('current_meal_type', None); ud.pop('current_meal_description', None)
    return ConversationHandler.END
async def add_meal_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # ... (код add_meal_cancel из v2.7) ...
    user_id = update.effective_user.id
    logger.info(f"User {user_id}: Отмена записи приема пищи.")
    await update.message.reply_text("❌ Запись приема пищи отменена.")
    context.user_data.pop('current_meal_type', None)
    context.user_data.pop('current_meal_description', None)
    return ConversationHandler.END

# --- Команда /todaycalories и вспомогательная функция (с ИСПРАВЛЕНИЕМ иероглифов) ---
async def show_today_calories(update: Update, context: ContextTypes.DEFAULT_TYPE, pre_text=""):
    # ... (код show_today_calories из v2.7, но с исправленной строкой для "нет записей") ...
    user = update.effective_user
    today_str = date.today().isoformat()
    if context.user_data.get(LAST_MEAL_DATE) != today_str:
        context.user_data[TODAY_MEALS] = []
        context.user_data[LAST_MEAL_DATE] = today_str
        logger.info(f"User {user.id}: Новый день, данные о приемах пищи сброшены для /todaycalories.")
    if not context.user_data.get(PROFILE_COMPLETE):
        msg_target = update.message if update.message else update.callback_query.message
        await msg_target.reply_text("Сначала создай профиль через /start, чтобы я мог отслеживать твои калории. 🌟")
        return
    today_meals_data = context.user_data.get(TODAY_MEALS, [])
    total_calories_today, total_protein_today, total_fat_today, total_carbs_today = 0,0,0,0
    summary_meals_text = ""
    if not today_meals_data: # ИСПРАВЛЕНО
        summary_meals_text = "🫙 За сегодня еще не было записано приемов пищи. Используй команду /addmeal, чтобы добавить первый!\n"
    else:
        summary_meals_text = "*За сегодня ты съел(а):*\n"
        for meal_entry in today_meals_data:
            meal_name = meal_entry.get('meal_name', 'Прием пищи')
            items_text_list = []
            if isinstance(meal_entry.get("items"), list):
                for item in meal_entry["items"]: items_text_list.append(f"  - {item.get('name','?')} ({item.get('quantity','?')}) ≈ {item.get('calories',0)} ккал")
            total_meal_cals = meal_entry.get('total', {}).get('calories', 0)
            total_meal_p = meal_entry.get('total', {}).get('protein', 0)
            total_meal_f = meal_entry.get('total', {}).get('fat', 0)
            total_meal_c = meal_entry.get('total', {}).get('carbs', 0)
            summary_meals_text += f"\n🍽️ *{meal_name}* (Общ: {total_meal_cals} ккал, Б:{total_meal_p:.1f} Ж:{total_meal_f:.1f} У:{total_meal_c:.1f}):\n" # Добавил округление БЖУ
            if items_text_list: summary_meals_text += "\n".join(items_text_list) + "\n"
            else: summary_meals_text += f"  _{meal_entry.get('user_description', 'Детали не распознаны')}_\n"
            total_calories_today += total_meal_cals; total_protein_today += total_meal_p; total_fat_today += total_meal_f; total_carbs_today += total_meal_c
        summary_meals_text += f"\n*📊 Итого за сегодня:*\n  Ккал: *{total_calories_today}*,\n  Белки: {total_protein_today:.1f} г,\n  Жиры: {total_fat_today:.1f} г,\n  Углеводы: {total_carbs_today:.1f} г\n"
    target_cals = context.user_data.get(TARGET_CALORIES)
    remaining_cals_text = ""
    if target_cals is not None and isinstance(target_cals, (int, float)):
        remaining = target_cals - total_calories_today
        if remaining >= 0: remaining_cals_text = f"\n🎯 Твоя цель на день: *{target_cals} ккал*.\n✨ Осталось потребить: *{remaining:.0f} ккал*."
        else: remaining_cals_text = f"\n🎯 Твоя цель на день: *{target_cals} ккал*.\n🔴 Перебор: *{abs(remaining):.0f} ккал*!"
    full_response = pre_text + summary_meals_text + remaining_cals_text
    message_target = update.message if update.message else update.callback_query.message
    await message_target.reply_text(full_response, parse_mode=ParseMode.MARKDOWN)
async def today_calories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_today_calories(update, context)

# --- Обычные команды (как в v2.7, с обновленным меню и help) ---
# (Вставь сюда menu_command, help_command, my_profile_command, weight_command_entry, train_command_entry, handle_train_location_and_generate из v2.7)
# --- Копипаста обычных команд ---
async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE): # Как в v2.7
    if not context.user_data.get(PROFILE_COMPLETE):
        await update.message.reply_text("Сначала давай создадим твой профиль! Нажми /start 😊", parse_mode=ParseMode.MARKDOWN)
        return
    menu_buttons = [[KeyboardButton("✍️ Записать еду (/addmeal)"), KeyboardButton("🗓️ Мои калории (/todaycalories)")],[KeyboardButton("🏋️‍♂️ Тренировка (/train)"), KeyboardButton("⚖️ Обновить вес (/weight)")],[KeyboardButton("📊 Мой профиль (/myprofile)"), KeyboardButton("❓ Помощь (/help)")],]
    await update.message.reply_text("👇 Вот что мы можем сделать:", reply_markup=ReplyKeyboardMarkup(menu_buttons, resize_keyboard=True, one_time_keyboard=False))
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE): # Как в v2.7
    help_text = ("👋 Привет! Я *ФитГуру* – твой гид в мире фитнеса и здорового питания.\n\n📌 *Основные команды:*\n/start - Начать работу, создать или просмотреть профиль.\n/menu - Показать главное меню с кнопками.\n/myprofile - Твой текущий фитнес-профиль и показатели.\n/train - Предложить варианты тренировок.\n/weight - Обновить свой текущий вес.\n/addmeal - Записать прием пищи.\n/todaycalories - Посмотреть итоги по КБЖУ за сегодня.\n/cancel - (Во время диалога) Отменить текущее действие.\n/help - Это сообщение.\n\n🤖 Я могу помочь с тренировками, расчетом показателей и записью питания!")
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)
async def my_profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE): # Как в v2.7
    # ... (код my_profile_command из v2.7) ...
    if not context.user_data.get(PROFILE_COMPLETE):
        await update.message.reply_text("Твой профиль еще не создан. Пожалуйста, начни с /start 🌟")
        return
    ud = context.user_data; bmi_interp = get_bmi_interpretation(ud.get(BMI)); weight_change_prediction_text = ""
    if ud.get(TDEE) and ud.get(TARGET_CALORIES):
        daily_deficit_or_surplus = ud.get(TARGET_CALORIES) - ud.get(TDEE); weekly_change_kcal = daily_deficit_or_surplus * 7
        weekly_weight_change_kg = weekly_change_kcal / 7700 
        if weekly_weight_change_kg < -0.05: weight_change_prediction_text = f"📈 Прогноз: *{abs(weekly_weight_change_kg):.1f} кг/нед.* в минус\n"
        elif weekly_weight_change_kg > 0.05: weight_change_prediction_text = f"📈 Прогноз: *{weekly_weight_change_kg:.1f} кг/нед.* в плюс\n"
    summary = (f"👤 *Твой фитнес-профиль, {update.effective_user.first_name}:*\n\n  - Пол: _{ud.get(GENDER, 'N/A').capitalize()}_\n  - Возраст: _{ud.get(AGE, 'N/A')} лет_\n  - Рост: _{ud.get(HEIGHT, 'N/A')} см_\n  - Вес: *{ud.get(CURRENT_WEIGHT, 'N/A')} кг*\n  - Активность: _{ud.get(ACTIVITY_LEVEL, 'N/A').capitalize()}_\n  - Цель: _{ud.get(GOAL, 'N/A').capitalize()}_\n\n📊 *Расчетные показатели:*\n  - ИМТ: *{ud.get(BMI, 'N/A')}*{bmi_interp}\n  - BMR: *{ud.get(BMR, 'N/A')} ккал/день*\n  - TDEE: *{ud.get(TDEE, 'N/A')} ккал/день*\n  - Рекомендуемые калории: `{ud.get(TARGET_CALORIES, 'N/A')}` *ккал/день*\n{weight_change_prediction_text}\nДля обновления веса используй /weight. Чтобы начать профиль заново, нажми /start (старый будет удален).")
    await update.message.reply_text(summary, parse_mode=ParseMode.MARKDOWN)
async def weight_command_entry(update: Update, context: ContextTypes.DEFAULT_TYPE): # Как в v2.7
    # ... (код weight_command_entry из v2.7) ...
    if not context.user_data.get(PROFILE_COMPLETE):
        await update.message.reply_text("Сначала давай создадим твой профиль! Нажми /start 😊")
        return
    await update.message.reply_text("⚖️ Введи свой *текущий вес* (например, 70.5):", parse_mode=ParseMode.MARKDOWN)
    context.user_data[AWAITING_WEIGHT_UPDATE] = True
async def handle_weight_update(update: Update, context: ContextTypes.DEFAULT_TYPE): # Как в v2.7
    # ... (код handle_weight_update из v2.7) ...
    try:
        new_weight = float(update.message.text.replace(',', '.'))
        if not 30 <= new_weight <= 300: raise ValueError("Некорректный вес")
        ud = context.user_data
        if not all(ud.get(key) for key in [HEIGHT, AGE, GENDER, ACTIVITY_LEVEL, GOAL]):
            logger.error(f"User {update.effective_user.id}: Missing profile data for weight update recalcs.")
            await update.message.reply_text("Не удалось обновить показатели, не хватает данных профиля. Попробуй /myprofile или /start.")
            ud.pop(AWAITING_WEIGHT_UPDATE, None); return
        ud[CURRENT_WEIGHT] = new_weight; ud[BMI] = calculate_bmi(new_weight, ud.get(HEIGHT)); ud[BMR] = calculate_bmr(new_weight, ud.get(HEIGHT), ud.get(AGE), ud.get(GENDER)); ud[TDEE] = calculate_tdee(ud.get(BMR), ud.get(ACTIVITY_LEVEL)); ud[TARGET_CALORIES] = calculate_target_calories(ud.get(TDEE), ud.get(GOAL))
        ud.pop(AWAITING_WEIGHT_UPDATE, None); bmi_interp = get_bmi_interpretation(ud.get(BMI)); weight_change_prediction_text = ""
        if ud.get(TDEE) and ud.get(TARGET_CALORIES):
            daily_deficit_or_surplus = ud.get(TARGET_CALORIES) - ud.get(TDEE); weekly_change_kcal = daily_deficit_or_surplus * 7
            weekly_weight_change_kg = weekly_change_kcal / 7700 
            if weekly_weight_change_kg < -0.05: weight_change_prediction_text = f"📈 Прогноз: *{abs(weekly_weight_change_kg):.1f} кг/нед.* в минус\n"
            elif weekly_weight_change_kg > 0.05: weight_change_prediction_text = f"📈 Прогноз: *{weekly_weight_change_kg:.1f} кг/нед.* в плюс\n"
        await update.message.reply_text(f"✅ Вес *{new_weight} кг* успешно обновлен! Твои показатели пересчитаны:\n  - ИМТ: *{ud.get(BMI, 'N/A')}*{bmi_interp}\n  - Рекомендуемые калории: `{ud.get(TARGET_CALORIES, 'N/A')}` *ккал/день*\n{weight_change_prediction_text}",parse_mode=ParseMode.MARKDOWN)
        logger.info(f"Пользователь {update.effective_user.id} обновил вес: {new_weight} кг.")
    except ValueError: await update.message.reply_text("🤔 Вес должен быть числом (например, 70.5). Попробуй еще раз /weight или введи вес снова.")
    except Exception as e:
        logger.error(f"Ошибка в handle_weight_update: {e}", exc_info=True)
        await update.message.reply_text("💥 Ой, произошла ошибка при обновлении веса.")
        context.user_data.pop(AWAITING_WEIGHT_UPDATE, None)
async def train_command_entry(update: Update, context: ContextTypes.DEFAULT_TYPE): # Как в v2.6
    # ... (код train_command_entry из v2.6) ...
    if not context.user_data.get(PROFILE_COMPLETE):
        await update.message.reply_text("Чтобы подобрать тренировку, мне нужен твой профиль. Начни с /start 🌟", parse_mode=ParseMode.MARKDOWN)
        return
    keyboard = [[InlineKeyboardButton("🏠 Дома (без оборудования)", callback_data="train_home")], [InlineKeyboardButton("🏋️‍♂️ В зале", callback_data="train_gym")], [InlineKeyboardButton("🌳 На улице", callback_data="train_street")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    message_to_reply = update.message if update.message else update.callback_query.message
    await message_to_reply.reply_text("Отлично! Где ты планируешь заниматься?", reply_markup=reply_markup)
async def handle_train_location_and_generate(update: Update, context: ContextTypes.DEFAULT_TYPE): # Как в v2.6
    # ... (код handle_train_location_and_generate из v2.6) ...
    query = update.callback_query; await query.answer(); user_id = update.effective_user.id 
    logger.info(f"User {user_id}: Запрос тренировки, выбор: {query.data}"); location_choice = query.data
    location_text, equipment_text = "", ""
    if location_choice == "train_home": location_text, equipment_text = "для дома", "без специального оборудования"
    elif location_choice == "train_gym": location_text, equipment_text = "для тренажерного зала", "с использованием стандартного оборудования зала"
    elif location_choice == "train_street": location_text, equipment_text = "для улицы", "с минимальным оборудованием или без него"
    ud = context.user_data
    profile_info = (f"ВАЖНО: Это данные профиля пользователя, используй их: Пол:{ud.get(GENDER,'N/A')}, Возраст:{ud.get(AGE,'N/A')}, Рост:{ud.get(HEIGHT,'N/A')}см, Вес:{ud.get(CURRENT_WEIGHT,'N/A')}кг, Активность:{ud.get(ACTIVITY_LEVEL,'N/A')}, Цель:{ud.get(GOAL,'N/A')}. ИМТ:{ud.get(BMI,'N/A')}, Рекомендуемые калории для цели:{ud.get(TARGET_CALORIES,'N/A')}. Не запрашивай эти данные у пользователя снова, они уже предоставлены.")
    prompt = (f"{profile_info} Пользователь хочет тренировку {location_text}, {equipment_text}. Твоя задача - сгенерировать программу тренировок. Ответ должен быть полностью на русском языке, очень грамотным и естественным, без выдуманных слов или странных фраз. Для каждого упражнения четко укажи:\n1. *Название упражнения* (жирный шрифт, без Markdown заголовков перед названием).\n2. Техника выполнения: Краткое и понятное описание техники (2-3 предложения).\n3. Подходы: Количество (например, 3-4).\n4. Повторения: Количество в каждом подходе (например, 10-15 или до около отказа).\n5. Отдых: Время между подходами (например, 60-90 секунд).\nНе обсуждай рабочий вес, если это не тренировка в зале или не просили отдельно. Раздели тренировку на секции: '## Разминка (5-7 минут)', '## Основная часть (20-30 минут)', '## Заминка (5 минут)'. Используй именно такие Markdown заголовки. В самом конце, отдельным абзацем, четко укажи: '🔥 Примерно сожжено калорий за эту тренировку: X-Y ккал.'. Замени X-Y на реалистичную оценку. Стиль — дружелюбный тренер. Ответ хорошо структурирован с Markdown (списки -, жирный шрифт *).")
    await query.edit_message_text("🏋️‍♂️ Подбираю для тебя *персонализированную тренировку*... Это может занять до 30 секунд.", parse_mode=ParseMode.MARKDOWN)
    logger.info(f"User {user_id}: Отправка промпта для тренировки в AI...")
    reply = await ask_groq(prompt, temperature=0.45)
    if reply and not any(err_word in reply for err_word in ["Ошибка", "Упс", "Извини", "К сожалению", "не могу", "не удалось"]):
        logger.info(f"User {user_id}: Получен валидный ответ от AI для тренировки.")
        await query.message.reply_text(reply, parse_mode=ParseMode.MARKDOWN)
    else:
        logger.warning(f"User {user_id}: AI вернул сообщение, похожее на ошибку, или пустой ответ: {reply}")
        await query.message.reply_text(reply if reply else "Не удалось сгенерировать тренировку. Попробуйте позже.", parse_mode=ParseMode.MARKDOWN)
# --- Конец копипасты обычных команд ---

# --- Обработчик общих сообщений (как в v2.7) ---
async def general_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (код general_message_handler из v2.7, с обработкой кнопок меню) ...
    user_message = update.message.text
    if user_message == "✍️ Записать еду (/addmeal)": await add_meal_start(update, context); return
    if user_message == "🗓️ Мои калории (/todaycalories)": await today_calories_command(update, context); return
    if user_message == "🏋️‍♂️ Тренировка (/train)": await train_command_entry(update, context); return
    if user_message == "⚖️ Обновить вес (/weight)": await weight_command_entry(update, context); return
    if user_message == "📊 Мой профиль (/myprofile)": await my_profile_command(update, context); return
    if user_message == "❓ Помощь (/help)": await help_command(update, context); return
    if context.user_data.get(AWAITING_WEIGHT_UPDATE) is True: await handle_weight_update(update, context); return
    logger.info(f"Получено обычное текстовое сообщение от {update.effective_user.id} ({update.effective_user.username}): '{user_message}'. AI не будет вызван.")
    await update.message.reply_text("🤖 Хм, я не совсем понял твой запрос. Если нужна помощь, используй /help или кнопки в /menu. Я могу помочь с тренировками, расчетом показателей и записью твоего питания! 😊", parse_mode=ParseMode.MARKDOWN)

# --- Основная функция (как в v2.7, с добавлением add_meal_conv_handler) ---
def main():
    if not TELEGRAM_TOKEN: logger.critical("TELEGRAM_TOKEN не найден! Бот не может запуститься."); return
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    onboarding_conv_handler = ConversationHandler(entry_points=[CommandHandler("start", start_command)], states={PROFILE_GENDER: [CallbackQueryHandler(handle_gender_and_ask_age, pattern="^(мужской|женский)$")], PROFILE_AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_age_and_ask_height)], PROFILE_HEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_height_and_ask_weight)], PROFILE_WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_weight_and_ask_activity)], PROFILE_ACTIVITY: [CallbackQueryHandler(handle_activity_and_ask_goal, pattern="^(минимальная|легкая|средняя|высокая|экстремальная)$")], PROFILE_GOAL: [CallbackQueryHandler(process_final_profile, pattern="^(похудеть|поддерживать вес|набрать массу)$")],}, fallbacks=[CommandHandler("cancel", cancel_onboarding), CommandHandler("start", start_command)], allow_reentry=True, per_user=True, per_chat=True,)
    app.add_handler(onboarding_conv_handler)
    add_meal_conv_handler = ConversationHandler(entry_points=[CommandHandler("addmeal", add_meal_start)], states={ADDMEAL_CHOOSE_TYPE: [CallbackQueryHandler(add_meal_choose_type, pattern="^meal_(Завтрак|Обед|Ужин|Перекус)$")], ADDMEAL_GET_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_meal_get_description)],}, fallbacks=[CommandHandler("cancel", add_meal_cancel)], per_user=True, per_chat=True,)
    app.add_handler(add_meal_conv_handler)
    app.add_handler(CommandHandler("train", train_command_entry))
    app.add_handler(CallbackQueryHandler(handle_train_location_and_generate, pattern="^train_(home|gym|street)$"))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("myprofile", my_profile_command))
    app.add_handler(CommandHandler("weight", weight_command_entry))
    app.add_handler(CommandHandler("todaycalories", today_calories_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, general_message_handler))
    logger.info("🤖 Бот ФитГуру v2.7 (с записью приемов пищи) запускается...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()