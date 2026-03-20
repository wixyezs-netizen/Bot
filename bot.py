import logging
import asyncio
import aiohttp
import hashlib
import time
import base64
import json
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = "8225924716:AAFzKnXZ8lJG_X1W9poH6Muyi-MMCXTWMy0"  # ⚠️ ЗАМЕНИТЕ НА ТОКЕН ОТ @BotFather
ADMIN_ID = 8387532956  # ⚠️ ЗАМЕНИТЕ НА ВАШ TELEGRAM ID

# Данные OAuth2 приложения ЮMoney
CLIENT_ID = "FA75F890120A05C3E64075605E6FD61DB8EE82146E1171B5CC854F2ACC7C20E8"
CLIENT_SECRET = "4E2C4D267ACA070493EC7412C90809CFF7BC2446EB44FD98B827DFA6F9A720BEC9046D59BAD4C1F9CCF1283AB402D1DDCEE9E367390AD01FDF2C840ABE6AC70B"

# Номер кошелька ЮMoney (укажите ваш)
YOOMONEY_WALLET = "410011111111111"  # ⚠️ ЗАМЕНИТЕ НА ВАШ НОМЕР КОШЕЛЬКА

# Настройки бота
BOT_USERNAME = "aimnoob_bot"  # ⚠️ ЗАМЕНИТЕ НА USERNAME ВАШЕГО БОТА
SUPPORT_USERNAME = "aimnoob_support"  # ⚠️ ЗАМЕНИТЕ НА ЛОГИН ЧАТА ПОДДЕРЖКИ
SHOP_URL = "https://aimnoob.ru"  # Ваш сайт

# Инициализация бота
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Хранилище платежей (в реальном проекте используйте базу данных)
pending_payments = {}

# ========== ПРОДУКТЫ AIMNOOB ==========
PRODUCTS = {
    # Android (APK)
    "apk_week": {
        "name": "AimNoob Standoff 2 | НЕДЕЛЯ",
        "price": 150,
        "platform": "Android",
        "period": "7 дней",
        "platform_code": "apk",
        "emoji": "📱"
    },
    "apk_month": {
        "name": "AimNoob Standoff 2 | МЕСЯЦ",
        "price": 350,
        "platform": "Android",
        "period": "30 дней",
        "platform_code": "apk",
        "emoji": "📱"
    },
    "apk_forever": {
        "name": "AimNoob Standoff 2 | НАВСЕГДА",
        "price": 150,
        "platform": "Android",
        "period": "Навсегда",
        "platform_code": "apk",
        "emoji": "📱"
    },
    # iOS
    "ios_week": {
        "name": "AimNoob Standoff 2 | НЕДЕЛЯ",
        "price": 300,
        "platform": "iOS",
        "period": "7 дней",
        "platform_code": "ios",
        "emoji": "🍏"
    },
    "ios_month": {
        "name": "AimNoob Standoff 2 | МЕСЯЦ",
        "price": 450,
        "platform": "iOS",
        "period": "30 дней",
        "platform_code": "ios",
        "emoji": "🍏"
    },
    "ios_forever": {
        "name": "AimNoob Standoff 2 | НАВСЕГДА",
        "price": 850,
        "platform": "iOS",
        "period": "Навсегда",
        "platform_code": "ios",
        "emoji": "🍏"
    }
}

# ========== СОСТОЯНИЯ ==========
class OrderState(StatesGroup):
    choosing_platform = State()
    choosing_subscription = State()

# ========== КЛАВИАТУРЫ ==========
def platform_keyboard():
    """Клавиатура выбора платформы"""
    buttons = [
        [InlineKeyboardButton(text="📱 Android (APK)", callback_data="platform_apk")],
        [InlineKeyboardButton(text="🍏 iOS", callback_data="platform_ios")],
        [InlineKeyboardButton(text="ℹ️ О чите", callback_data="about")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def apk_subscription_keyboard():
    """Клавиатура выбора подписки для Android"""
    buttons = [
        [InlineKeyboardButton(text="🔓 НЕДЕЛЯ | 150 ₽", callback_data="sub_apk_week")],
        [InlineKeyboardButton(text="🔥 МЕСЯЦ | 350 ₽", callback_data="sub_apk_month")],
        [InlineKeyboardButton(text="⭐ НАВСЕГДА | 150 ₽", callback_data="sub_apk_forever")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_platform")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def ios_subscription_keyboard():
    """Клавиатура выбора подписки для iOS"""
    buttons = [
        [InlineKeyboardButton(text="🔓 НЕДЕЛЯ | 300 ₽", callback_data="sub_ios_week")],
        [InlineKeyboardButton(text="🔥 МЕСЯЦ | 450 ₽", callback_data="sub_ios_month")],
        [InlineKeyboardButton(text="⭐ НАВСЕГДА | 850 ₽", callback_data="sub_ios_forever")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_platform")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def payment_keyboard(payment_url):
    """Клавиатура оплаты"""
    buttons = [
        [InlineKeyboardButton(text="💳 Оплатить ЮMoney", url=payment_url)],
        [InlineKeyboardButton(text="✅ Проверить оплату", callback_data="check_payment")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="restart")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def restart_keyboard():
    """Клавиатура нового заказа"""
    buttons = [[InlineKeyboardButton(text="🔄 Новый заказ", callback_data="restart")]]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def support_keyboard():
    """Клавиатура поддержки"""
    buttons = [
        [InlineKeyboardButton(text="💬 Написать в поддержку", url=f"https://t.me/{SUPPORT_USERNAME}")],
        [InlineKeyboardButton(text="🌐 Наш сайт", url=SHOP_URL)],
        [InlineKeyboardButton(text="🔄 Новый заказ", callback_data="restart")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def about_keyboard():
    """Клавиатура возврата"""
    buttons = [[InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_platform")]]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ========== ФУНКЦИИ ЮMONEY ==========
def create_payment_link(amount, payment_id, product_name):
    """
    Создание ссылки для оплаты через ЮMoney с фиксированной суммой
    Использует OAuth2 приложение AimNoob
    """
    comment = f"AimNoob {product_name} (Заказ #{payment_id})"
    
    payment_url = (
        f"https://yoomoney.ru/quickpay/confirm.xml"
        f"?receiver={YOOMONEY_WALLET}"
        f"&quickpay-form=shop"
        f"&targets={comment.replace(' ', '+')}"
        f"&sum={amount}"
        f"&label={payment_id}"
        f"&successURL=https://t.me/{BOT_USERNAME}?start=success"
        f"&paymentType=AC"
        f"&client_id={CLIENT_ID}"
    )
    
    return payment_url

async def check_payment_via_api(payment_id, amount):
    """
    Проверка платежа через API ЮMoney с использованием OAuth2
    """
    if not CLIENT_ID or not CLIENT_SECRET:
        logging.warning("OAuth2 credentials not configured")
        return False
    
    # Формируем базовую авторизацию
    auth_string = f"{CLIENT_ID}:{CLIENT_SECRET}"
    auth_bytes = auth_string.encode('ascii')
    auth_b64 = base64.b64encode(auth_bytes).decode('ascii')
    
    headers = {
        "Authorization": f"Basic {auth_b64}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    
    # Запрос на получение списка операций
    data = {
        "label": payment_id,
        "records": 5
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://yoomoney.ru/api/operation-history",
                headers=headers,
                data=data
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    operations = result.get("operations", [])
                    
                    for op in operations:
                        op_label = op.get("label", "")
                        op_status = op.get("status")
                        op_amount = float(op.get("amount", 0))
                        
                        if (op_label == payment_id and 
                            op_status == "success" and
                            abs(op_amount - amount) < 0.01):
                            logging.info(f"Payment {payment_id} found! Amount: {op_amount}")
                            return True
                else:
                    logging.error(f"API error: {response.status}")
                    
    except Exception as e:
        logging.error(f"Error checking payment: {e}")
    
    return False

async def check_payment(payment_id, amount):
    """
    Основная функция проверки платежа
    """
    return await check_payment_via_api(payment_id, amount)

# ========== ОБРАБОТЧИКИ ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    """Начало работы с ботом"""
    await state.clear()
    
    # Проверка успешной оплаты (возврат после оплаты)
    if message.text and "start=success" in message.text:
        await message.answer(
            "✅ *Спасибо за покупку!*\n\n"
            "Ваш заказ обрабатывается.\n"
            "В ближайшее время вы получите доступ к читу AimNoob.\n\n"
            "Если возникнут вопросы — напишите в поддержку.",
            parse_mode="Markdown",
            reply_markup=support_keyboard()
        )
        return
    
    welcome_text = (
        "🎯 *AimNoob — лучший приватный чит для Standoff 2* 🎯\n\n"
        "🔥 *Преимущества:*\n"
        "• ✅ Анти-бан система\n"
        "• 🎯 Идеальный AimLock\n"
        "• 👁️ WallHack через стены\n"
        "• 📊 ESP информация\n"
        "• 🔒 Скрытный режим\n"
        "• ⚡ Автообновления\n\n"
        "💎 *Выберите вашу платформу:*"
    )
    
    await message.answer(
        welcome_text,
        parse_mode="Markdown",
        reply_markup=platform_keyboard()
    )
    await state.set_state(OrderState.choosing_platform)

@dp.callback_query(F.data == "about")
async def about_cheat(callback: CallbackQuery):
    """Информация о чите"""
    about_text = (
        "ℹ️ *О чите AimNoob*\n\n"
        "🎮 *Версия:* 0.37.1\n"
        "📅 *Последнее обновление:* Март 2026\n\n"
        "*Функции:*\n"
        "• AimLock (автоприцел) с настройками\n"
        "• WallHack (просмотр через стены)\n"
        "• ESP (имя, здоровье, расстояние)\n"
        "• Radar (радар на весь экран)\n"
        "• AntiFlash (анти вспышка)\n"
        "• NoSpread (точность)\n\n"
        "*Безопасность:*\n"
        "• Собственная обфускация кода\n"
        "• Инжектор с антидетектом\n"
        "• Еженедельные обновления\n\n"
        "💬 По вопросам: @{SUPPORT_USERNAME}"
    )
    
    await callback.message.edit_text(
        about_text,
        parse_mode="Markdown",
        reply_markup=about_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("platform_"))
async def process_platform(callback: CallbackQuery, state: FSMContext):
    """Выбор платформы"""
    platform = callback.data.split("_")[1]
    await state.update_data(platform=platform)
    
    if platform == "apk":
        text = (
            "📱 *Android (APK)*\n\n"
            "✅ Поддержка всех устройств на Android 10+\n"
            "✅ Простая установка через APK файл\n"
            "✅ Не требует Root прав\n\n"
            "Выберите срок подписки AimNoob:"
        )
        await callback.message.edit_text(
            text,
            parse_mode="Markdown",
            reply_markup=apk_subscription_keyboard()
        )
    else:
        text = (
            "🍏 *iOS*\n\n"
            "✅ Поддержка iOS 14 - 18\n"
            "✅ Установка через AltStore / TrollStore\n"
            "✅ Работает на всех устройствах\n\n"
            "Выберите срок подписки AimNoob:"
        )
        await callback.message.edit_text(
            text,
            parse_mode="Markdown",
            reply_markup=ios_subscription_keyboard()
        )
    
    await state.set_state(OrderState.choosing_subscription)
    await callback.answer()

@dp.callback_query(F.data.startswith("sub_"))
async def process_subscription(callback: CallbackQuery, state: FSMContext):
    """Выбор подписки и создание платежа"""
    parts = callback.data.split("_")
    product_key = f"{parts[1]}_{parts[2]}"
    product = PRODUCTS.get(product_key)
    
    if not product:
        await callback.answer("Ошибка выбора", show_alert=True)
        return
    
    user_id = callback.from_user.id
    amount = product["price"]
    payment_id = hashlib.md5(f"{user_id}_{amount}_{time.time()}_{CLIENT_ID}".encode()).hexdigest()[:16]
    
    # Создаем ссылку на оплату
    payment_url = create_payment_link(amount, payment_id, product["name"])
    
    # Сохраняем информацию о платеже
    pending_payments[user_id] = {
        "payment_id": payment_id,
        "amount": amount,
        "product": product,
        "created_at": time.time(),
        "status": "pending",
        "user": {
            "id": user_id,
            "name": callback.from_user.full_name,
            "username": callback.from_user.username
        }
    }
    
    payment_text = (
        f"{product['emoji']} *Оплата AimNoob*\n\n"
        f"📦 *Товар:* {product['name']}\n"
        f"💰 *Сумма:* {amount} ₽\n"
        f"🆔 *Номер заказа:* `{payment_id}`\n\n"
        f"📝 *Как оплатить:*\n"
        f"1️⃣ Нажмите кнопку \"Оплатить ЮMoney\"\n"
        f"2️⃣ Оплатите {amount} ₽ любой картой\n"
        f"3️⃣ Вернитесь и нажмите \"Проверить оплату\"\n\n"
        f"⏱ *После оплаты ключ активации придет автоматически*\n"
        f"💬 Проблемы? @{SUPPORT_USERNAME}"
    )
    
    await callback.message.edit_text(
        payment_text,
        parse_mode="Markdown",
        reply_markup=payment_keyboard(payment_url)
    )
    
    # Уведомляем админа о новом ожидающем платеже
    admin_notify = (
        f"🔄 *Новый ожидающий платеж*\n\n"
        f"👤 {callback.from_user.full_name}\n"
        f"🆔 ID: `{user_id}`\n"
        f"📦 {product['name']}\n"
        f"💰 {amount} ₽\n"
        f"🆔 Заказ: `{payment_id}`"
    )
    await bot.send_message(ADMIN_ID, admin_notify, parse_mode="Markdown")
    
    await callback.answer()

@dp.callback_query(F.data == "check_payment")
async def check_payment_callback(callback: CallbackQuery):
    """Проверка оплаты"""
    user_id = callback.from_user.id
    payment_info = pending_payments.get(user_id)
    
    if not payment_info:
        await callback.answer("❌ Заказ не найден. Начните заново.", show_alert=True)
        await cmd_start(callback.message, callback.from_user)
        return
    
    # Показываем индикатор загрузки
    await callback.answer("🔍 Проверяю оплату...")
    
    # Меняем текст на "проверяем..."
    checking_msg = await callback.message.edit_text(
        "🔄 *Проверяем поступление платежа...*\n\n"
        "Пожалуйста, подождите несколько секунд.",
        parse_mode="Markdown"
    )
    
    # Проверяем платеж
    payment_received = await check_payment(
        payment_info["payment_id"],
        payment_info["amount"]
    )
    
    if payment_received:
        # Успешная оплата
        product = payment_info["product"]
        payment_info["status"] = "paid"
        
        # Генерируем ключ активации
        license_key = f"AIMNOOB-{payment_info['payment_id'][:8]}-{user_id % 10000}"
        
        # Сообщение пользователю
        success_text = (
            f"✅ *Оплата подтверждена!*\n\n"
            f"🎉 *Добро пожаловать в AimNoob!*\n\n"
            f"📦 *Ваш заказ:*\n"
            f"• {product['name']}\n"
            f"• {product['emoji']} {product['platform']}\n"
            f"• Срок: {product['period']}\n\n"
            f"🔑 *Лицензионный ключ:*\n"
            f"`{license_key}`\n\n"
            f"📥 *Ссылка на скачивание:*\n"
            f"🔗 `{SHOP_URL}/download/{product['platform_code']}_{user_id}`\n\n"
            f"📖 *Инструкция по установке:*\n"
            f"1. Скачайте файл по ссылке выше\n"
            f"2. Установите согласно инструкции\n"
            f"3. Введите ключ активации\n"
            f"4. Запустите игру и наслаждайтесь!\n\n"
            f"💬 По вопросам: @{SUPPORT_USERNAME}"
        )
        
        await checking_msg.edit_text(
            success_text,
            parse_mode="Markdown",
            reply_markup=support_keyboard()
        )
        
        # Уведомление админу об успешной продаже
        admin_text = (
            f"✅ *НОВАЯ ПРОДАЖА AIMNOOB*\n\n"
            f"👤 {callback.from_user.full_name}\n"
            f"🆔 ID: `{user_id}`\n"
            f"📦 {product['name']}\n"
            f"💰 {product['price']} ₽\n"
            f"{product['emoji']} {product['platform']}\n"
            f"🔑 Ключ: `{license_key}`\n"
            f"🆔 Заказ: `{payment_info['payment_id']}`\n\n"
            f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        
        await bot.send_message(ADMIN_ID, admin_text, parse_mode="Markdown")
        
    else:
        # Платеж не найден
        payment_url = create_payment_link(
            payment_info["amount"],
            payment_info["payment_id"],
            payment_info["product"]["name"]
        )
        
        fail_text = (
            f"❌ *Платеж не найден*\n\n"
            f"💰 Сумма: {payment_info['amount']} ₽\n"
            f"🆔 Заказ: `{payment_info['payment_id']}`\n\n"
            f"*Проверьте:*\n"
            f"• Оплачена ли точная сумма {payment_info['amount']} ₽\n"
            f"• Правильный ли кошелек получателя\n"
            f"• Если оплатили — подождите 1-2 минуты\n\n"
            f"💬 Не помогло? Напишите в поддержку"
        )
        
        await checking_msg.edit_text(
            fail_text,
            parse_mode="Markdown",
            reply_markup=payment_keyboard(payment_url)
        )

@dp.callback_query(F.data == "restart")
async def restart_order(callback: CallbackQuery, state: FSMContext):
    """Начать заказ заново"""
    user_id = callback.from_user.id
    if user_id in pending_payments:
        del pending_payments[user_id]
    
    await state.clear()
    await cmd_start(callback.message, state)
    await callback.answer("🔄 Начинаем новый заказ")

@dp.callback_query(F.data == "back_to_platform")
async def back_to_platform(callback: CallbackQuery, state: FSMContext):
    """Вернуться к выбору платформы"""
    await state.clear()
    await cmd_start(callback.message, state)
    await callback.answer()

# ========== ЗАПУСК БОТА ==========
async def main():
    print("=" * 50)
    print("🎯 AIMNOOB SHOP BOT 🎯")
    print("=" * 50)
    print(f"🤖 Бот запущен и готов к работе!")
    print(f"📱 Username: @{BOT_USERNAME}")
    print(f"💬 Поддержка: @{SUPPORT_USERNAME}")
    print(f"🌐 Сайт: {SHOP_URL}")
    print(f"💰 Кошелек: {YOOMONEY_WALLET}")
    print(f"🔑 Client ID: {CLIENT_ID[:20]}...")
    print("=" * 50)
    print("Ожидание сообщений...")
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())