import logging
import asyncio
import aiohttp
import hashlib
import time
import base64
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = "8225924716:AAFzKnXZ8lJG_X1W9poH6Muyi-MMCXTWMy0"
ADMIN_ID = 8387532956

# Данные OAuth2 приложения ЮMoney
CLIENT_ID = "FA75F890120A05C3E64075605E6FD61DB8EE82146E1171B5CC854F2ACC7C20E8"
CLIENT_SECRET = "4E2C4D267ACA070493EC7412C90809CFF7BC2446EB44FD98B827DFA6F9A720BEC9046D59BAD4C1F9CCF1283AB402D1DDCEE9E367390AD01FDF2C840ABE6AC70B"

# Номер кошелька ЮMoney
YOOMONEY_WALLET = "410011111111111"  # ⚠️ ЗАМЕНИТЕ

# Настройки бота
BOT_USERNAME = "aimnoob_bot"
SUPPORT_USERNAME = "aimnoob_support"
SHOP_URL = "https://aimnoob.ru"

# Настройки подключения
API_URL = "https://api.telegram.org"
TIMEOUT = 30

# Создаем бота с простыми настройками
bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Хранилище платежей
pending_payments = {}

# ========== ПРОДУКТЫ ==========
PRODUCTS = {
    "apk_week": {"name": "AimNoob Standoff 2 | НЕДЕЛЯ", "price": 150, "platform": "Android", "period": "7 дней", "platform_code": "apk", "emoji": "📱"},
    "apk_month": {"name": "AimNoob Standoff 2 | МЕСЯЦ", "price": 350, "platform": "Android", "period": "30 дней", "platform_code": "apk", "emoji": "📱"},
    "apk_forever": {"name": "AimNoob Standoff 2 | НАВСЕГДА", "price": 150, "platform": "Android", "period": "Навсегда", "platform_code": "apk", "emoji": "📱"},
    "ios_week": {"name": "AimNoob Standoff 2 | НЕДЕЛЯ", "price": 300, "platform": "iOS", "period": "7 дней", "platform_code": "ios", "emoji": "🍏"},
    "ios_month": {"name": "AimNoob Standoff 2 | МЕСЯЦ", "price": 450, "platform": "iOS", "period": "30 дней", "platform_code": "ios", "emoji": "🍏"},
    "ios_forever": {"name": "AimNoob Standoff 2 | НАВСЕГДА", "price": 850, "platform": "iOS", "period": "Навсегда", "platform_code": "ios", "emoji": "🍏"}
}

# ========== СОСТОЯНИЯ ==========
class OrderState(StatesGroup):
    choosing_platform = State()
    choosing_subscription = State()

# ========== КЛАВИАТУРЫ ==========
def platform_keyboard():
    buttons = [
        [InlineKeyboardButton(text="📱 Android (APK)", callback_data="platform_apk")],
        [InlineKeyboardButton(text="🍏 iOS", callback_data="platform_ios")],
        [InlineKeyboardButton(text="ℹ️ О чите", callback_data="about")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def apk_subscription_keyboard():
    buttons = [
        [InlineKeyboardButton(text="🔓 НЕДЕЛЯ | 150 ₽", callback_data="sub_apk_week")],
        [InlineKeyboardButton(text="🔥 МЕСЯЦ | 350 ₽", callback_data="sub_apk_month")],
        [InlineKeyboardButton(text="⭐ НАВСЕГДА | 150 ₽", callback_data="sub_apk_forever")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_platform")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def ios_subscription_keyboard():
    buttons = [
        [InlineKeyboardButton(text="🔓 НЕДЕЛЯ | 300 ₽", callback_data="sub_ios_week")],
        [InlineKeyboardButton(text="🔥 МЕСЯЦ | 450 ₽", callback_data="sub_ios_month")],
        [InlineKeyboardButton(text="⭐ НАВСЕГДА | 850 ₽", callback_data="sub_ios_forever")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_platform")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def payment_keyboard(payment_url):
    buttons = [
        [InlineKeyboardButton(text="💳 Оплатить ЮMoney", url=payment_url)],
        [InlineKeyboardButton(text="✅ Проверить оплату", callback_data="check_payment")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="restart")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def support_keyboard():
    buttons = [
        [InlineKeyboardButton(text="💬 Поддержка", url=f"https://t.me/{SUPPORT_USERNAME}")],
        [InlineKeyboardButton(text="🌐 Сайт", url=SHOP_URL)],
        [InlineKeyboardButton(text="🔄 Новый заказ", callback_data="restart")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def about_keyboard():
    buttons = [[InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_platform")]]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ========== ФУНКЦИИ ==========
def create_payment_link(amount, payment_id, product_name):
    comment = f"AimNoob {product_name} (Заказ #{payment_id})"
    return f"https://yoomoney.ru/quickpay/confirm.xml?receiver={YOOMONEY_WALLET}&quickpay-form=shop&targets={comment.replace(' ', '+')}&sum={amount}&label={payment_id}&successURL=https://t.me/{BOT_USERNAME}?start=success&paymentType=AC"

async def check_payment(payment_id, amount):
    """Проверка платежа через API ЮMoney"""
    try:
        auth_string = f"{CLIENT_ID}:{CLIENT_SECRET}"
        auth_b64 = base64.b64encode(auth_string.encode()).decode()
        
        headers = {
            "Authorization": f"Basic {auth_b64}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        data = {"label": payment_id, "records": 5}
        
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post("https://yoomoney.ru/api/operation-history", headers=headers, data=data) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    for op in result.get("operations", []):
                        if op.get("label") == payment_id and op.get("status") == "success":
                            if abs(float(op.get("amount", 0)) - amount) < 0.01:
                                return True
    except Exception as e:
        logger.error(f"Payment check error: {e}")
    return False

# ========== ОБРАБОТЧИКИ ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    
    welcome_text = (
        "🎯 *AimNoob — лучший чит для Standoff 2* 🎯\n\n"
        "🔥 *Преимущества:*\n"
        "• ✅ Анти-бан система\n"
        "• 🎯 Идеальный AimLock\n"
        "• 👁️ WallHack\n"
        "• 📊 ESP информация\n\n"
        "💎 *Выберите платформу:*"
    )
    
    await message.answer(
        welcome_text,
        parse_mode="Markdown",
        reply_markup=platform_keyboard()
    )
    await state.set_state(OrderState.choosing_platform)

@dp.callback_query(F.data == "about")
async def about_cheat(callback: CallbackQuery):
    about_text = (
        "ℹ️ *О чите AimNoob*\n\n"
        "🎮 *Версия:* 0.37.1\n"
        "*Функции:* AimLock, WallHack, ESP, Radar\n\n"
        f"💬 По вопросам: @{SUPPORT_USERNAME}"
    )
    await callback.message.edit_text(about_text, parse_mode="Markdown", reply_markup=about_keyboard())
    await callback.answer()

@dp.callback_query(F.data.startswith("platform_"))
async def process_platform(callback: CallbackQuery, state: FSMContext):
    platform = callback.data.split("_")[1]
    await state.update_data(platform=platform)
    
    if platform == "apk":
        await callback.message.edit_text(
            "📱 *Android (APK)*\n\nВыберите срок подписки:",
            parse_mode="Markdown",
            reply_markup=apk_subscription_keyboard()
        )
    else:
        await callback.message.edit_text(
            "🍏 *iOS*\n\nВыберите срок подписки:",
            parse_mode="Markdown",
            reply_markup=ios_subscription_keyboard()
        )
    
    await state.set_state(OrderState.choosing_subscription)
    await callback.answer()

@dp.callback_query(F.data.startswith("sub_"))
async def process_subscription(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    product_key = f"{parts[1]}_{parts[2]}"
    product = PRODUCTS.get(product_key)
    
    if not product:
        await callback.answer("Ошибка", show_alert=True)
        return
    
    user_id = callback.from_user.id
    amount = product["price"]
    payment_id = hashlib.md5(f"{user_id}_{amount}_{time.time()}".encode()).hexdigest()[:16]
    
    payment_url = create_payment_link(amount, payment_id, product["name"])
    
    pending_payments[user_id] = {
        "payment_id": payment_id,
        "amount": amount,
        "product": product,
        "created_at": time.time(),
        "status": "pending"
    }
    
    payment_text = (
        f"{product['emoji']} *Оплата AimNoob*\n\n"
        f"📦 *Товар:* {product['name']}\n"
        f"💰 *Сумма:* {amount} ₽\n"
        f"🆔 *Заказ:* `{payment_id}`\n\n"
        f"1️⃣ Нажмите кнопку оплаты\n"
        f"2️⃣ Оплатите {amount} ₽\n"
        f"3️⃣ Нажмите \"Проверить оплату\""
    )
    
    await callback.message.edit_text(
        payment_text,
        parse_mode="Markdown",
        reply_markup=payment_keyboard(payment_url)
    )
    
    await bot.send_message(
        ADMIN_ID,
        f"🔄 Новый заказ\n👤 {callback.from_user.full_name}\n📦 {product['name']}\n💰 {amount} ₽",
        parse_mode="Markdown"
    )
    
    await callback.answer()

@dp.callback_query(F.data == "check_payment")
async def check_payment_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    payment_info = pending_payments.get(user_id)
    
    if not payment_info:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    
    await callback.answer("🔍 Проверяю...")
    
    checking_msg = await callback.message.edit_text(
        "🔄 Проверяем оплату...\nПожалуйста, подождите.",
        parse_mode="Markdown"
    )
    
    payment_received = await check_payment(payment_info["payment_id"], payment_info["amount"])
    
    if payment_received:
        product = payment_info["product"]
        license_key = f"AIMNOOB-{payment_info['payment_id'][:8]}-{user_id % 10000}"
        
        success_text = (
            f"✅ *Оплата подтверждена!*\n\n"
            f"🎉 Добро пожаловать в AimNoob!\n\n"
            f"🔑 *Ключ:* `{license_key}`\n"
            f"📥 *Скачать:* {SHOP_URL}/download/{product['platform_code']}_{user_id}\n\n"
            f"💬 Поддержка: @{SUPPORT_USERNAME}"
        )
        
        await checking_msg.edit_text(success_text, parse_mode="Markdown", reply_markup=support_keyboard())
        
        await bot.send_message(
            ADMIN_ID,
            f"✅ ПРОДАЖА!\n👤 {callback.from_user.full_name}\n📦 {product['name']}\n💰 {payment_info['amount']} ₽\n🔑 {license_key}",
            parse_mode="Markdown"
        )
    else:
        fail_text = (
            f"❌ *Платеж не найден*\n\n"
            f"💰 Сумма: {payment_info['amount']} ₽\n\n"
            f"*Проверьте:*\n"
            f"• Оплачена ли точная сумма\n"
            f"• Если оплатили — подождите 1-2 минуты"
        )
        
        await checking_msg.edit_text(fail_text, parse_mode="Markdown")

@dp.callback_query(F.data == "restart")
async def restart_order(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id in pending_payments:
        del pending_payments[user_id]
    await state.clear()
    await cmd_start(callback.message, state)
    await callback.answer()

@dp.callback_query(F.data == "back_to_platform")
async def back_to_platform(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await cmd_start(callback.message, state)
    await callback.answer()

# ========== ЗАПУСК ==========
async def main():
    print("=" * 50)
    print("🎯 AIMNOOB SHOP BOT 🎯")
    print("=" * 50)
    
    # Пытаемся подключиться с повторными попытками
    max_retries = 5
    for attempt in range(max_retries):
        try:
            me = await bot.get_me()
            print(f"✅ Бот @{me.username} успешно запущен!")
            print(f"📱 Username: @{BOT_USERNAME}")
            print(f"💬 Поддержка: @{SUPPORT_USERNAME}")
            print("=" * 50)
            print("✅ Ожидание сообщений...")
            break
        except Exception as e:
            print(f"❌ Попытка {attempt + 1}/{max_retries} не удалась: {e}")
            if attempt < max_retries - 1:
                wait_time = 5 * (attempt + 1)
                print(f"⏳ Повтор через {wait_time} секунд...")
                await asyncio.sleep(wait_time)
            else:
                print("❌ Не удалось подключиться к Telegram API")
                print("Проверьте:")
                print("1. Интернет соединение")
                print("2. Токен бота")
                print("3. Доступ к api.telegram.org")
                return
    
    # Запускаем polling
    while True:
        try:
            await dp.start_polling(
                bot,
                polling_timeout=30,
                skip_updates=True
            )
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            print("🔄 Перезапуск через 10 секунд...")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())
