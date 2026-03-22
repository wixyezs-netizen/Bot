# bot.py
import logging
import asyncio
import aiohttp
import hashlib
import time
import random
import json
import os
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from urllib.parse import quote_plus

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN", "8225924716:AAFZ_8Eu8aJ4BF7pErZY5Ef3emG9Cl9PikE")

# ===== ИСПРАВЛЕНИЕ 1: Безопасный разбор ADMIN_ID =====
admin_ids_str = os.getenv("ADMIN_ID", "8387532956")
admin_ids_list = [int(x.strip()) for x in admin_ids_str.split(",") if x.strip().isdigit()]

if len(admin_ids_list) >= 2:
    ADMIN_ID = admin_ids_list[0]
    SUPPORT_CHAT_ID = admin_ids_list[1]
elif len(admin_ids_list) == 1:
    ADMIN_ID = admin_ids_list[0]
    SUPPORT_CHAT_ID = int(os.getenv("SUPPORT_CHAT_ID", "8354762345"))
else:
    ADMIN_ID = 8387532956
    SUPPORT_CHAT_ID = 8354762345

# Список всех админов (для проверки прав)
ADMIN_IDS = set(admin_ids_list) if admin_ids_list else {ADMIN_ID}

CRYPTOBOT_TOKEN = os.getenv("CRYPTOBOT_TOKEN", "493276:AAtS7R1zYy0gaPw8eax1EgiWo0tdnd6dQ9c")
YOOMONEY_ACCESS_TOKEN = os.getenv("YOOMONEY_ACCESS_TOKEN", "4100118889570559.3288B2E716CEEB922A26BD6BEAC58648FBFB680CCF64E4E1447D714D6FB5EA5F01F1478FAC686BEF394C8A186C98982DE563C1ABCDF9F2F61D971B61DA3C7E486CA818F98B9E0069F1C0891E090DD56A11319D626A40F0AE8302A8339DED9EB7969617F191D93275F64C4127A3ECB7AED33FCDE91CA68690EB7534C67E6C219E")
YOOMONEY_WALLET = os.getenv("YOOMONEY_WALLET", "4100118889570559")

SUPPORT_CHAT_USERNAME = os.getenv("SUPPORT_CHAT_USERNAME", "aimnoob_support")
SHOP_URL = os.getenv("SHOP_URL", "https://aimnoob.ru")

# ========== ИНИЦИАЛИЗАЦИЯ ==========
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

pending_orders = {}
confirmed_payments = {}
balance_history = []

# ========== ПРОДУКТЫ ==========
PRODUCTS = {
    "apk_week": {
        "name": "📱 AimNoob Android",
        "period_text": "НЕДЕЛЮ",
        "price": 150,
        "price_stars": 350,
        "price_gold": 350,
        "price_nft": 250,
        "price_crypto_usdt": 2,
        "platform": "Android",
        "period": "НЕДЕЛЮ",
        "platform_code": "apk",
        "emoji": "📱",
        "duration": "7 дней"
    },
    "apk_month": {
        "name": "📱 AimNoob Android",
        "period_text": "МЕСЯЦ",
        "price": 350,
        "price_stars": 800,
        "price_gold": 800,
        "price_nft": 600,
        "price_crypto_usdt": 5,
        "platform": "Android",
        "period": "МЕСЯЦ",
        "platform_code": "apk",
        "emoji": "📱",
        "duration": "30 дней"
    },
    "apk_forever": {
        "name": "📱 AimNoob Android",
        "period_text": "НАВСЕГДА",
        "price": 800,
        "price_stars": 1800,
        "price_gold": 1800,
        "price_nft": 1400,
        "price_crypto_usdt": 12,
        "platform": "Android",
        "period": "НАВСЕГДА",
        "platform_code": "apk",
        "emoji": "📱",
        "duration": "Навсегда"
    },
    "ios_week": {
        "name": "🍎 AimNoob iOS",
        "period_text": "НЕДЕЛЮ",
        "price": 300,
        "price_stars": 700,
        "price_gold": 700,
        "price_nft": 550,
        "price_crypto_usdt": 4,
        "platform": "iOS",
        "period": "НЕДЕЛЮ",
        "platform_code": "ios",
        "emoji": "🍎",
        "duration": "7 дней"
    },
    "ios_month": {
        "name": "🍎 AimNoob iOS",
        "period_text": "МЕСЯЦ",
        "price": 450,
        "price_stars": 1000,
        "price_gold": 1000,
        "price_nft": 800,
        "price_crypto_usdt": 6,
        "platform": "iOS",
        "period": "МЕСЯЦ",
        "platform_code": "ios",
        "emoji": "🍎",
        "duration": "30 дней"
    },
    "ios_forever": {
        "name": "🍎 AimNoob iOS",
        "period_text": "НАВСЕГДА",
        "price": 850,
        "price_stars": 2000,
        "price_gold": 2000,
        "price_nft": 1600,
        "price_crypto_usdt": 12,
        "platform": "iOS",
        "period": "НАВСЕГДА",
        "platform_code": "ios",
        "emoji": "🍎",
        "duration": "Навсегда"
    }
}

# ========== СОСТОЯНИЯ ==========
class OrderState(StatesGroup):
    choosing_platform = State()
    choosing_subscription = State()
    choosing_payment = State()

# ========== КЛАВИАТУРЫ ==========
def platform_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Android", callback_data="platform_apk")],
        [InlineKeyboardButton(text="🍎 iOS", callback_data="platform_ios")],
        [InlineKeyboardButton(text="ℹ️ О программе", callback_data="about")],
        [InlineKeyboardButton(text="💬 Поддержка", url=f"https://t.me/{SUPPORT_CHAT_USERNAME}")]
    ])

def apk_subscription_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ НЕДЕЛЯ — 150₽", callback_data="sub_apk_week")],
        [InlineKeyboardButton(text="🔥 МЕСЯЦ — 350₽", callback_data="sub_apk_month")],
        [InlineKeyboardButton(text="💎 НАВСЕГДА — 800₽", callback_data="sub_apk_forever")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_platform")]
    ])

def ios_subscription_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ НЕДЕЛЯ — 300₽", callback_data="sub_ios_week")],
        [InlineKeyboardButton(text="🔥 МЕСЯЦ — 450₽", callback_data="sub_ios_month")],
        [InlineKeyboardButton(text="💎 НАВСЕГДА — 850₽", callback_data="sub_ios_forever")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_platform")]
    ])

def payment_methods_keyboard(product):
    buttons = [
        [InlineKeyboardButton(text="💳 ЮMoney", callback_data=f"pay_yoomoney_{product['platform_code']}_{product['period']}")],
        [InlineKeyboardButton(text="⭐ Telegram Stars", callback_data=f"pay_stars_{product['platform_code']}_{product['period']}")],
        [InlineKeyboardButton(text="₿ Криптобот", callback_data=f"pay_crypto_{product['platform_code']}_{product['period']}")],
        [InlineKeyboardButton(text="💰 GOLD", callback_data=f"pay_gold_{product['platform_code']}_{product['period']}")],
        [InlineKeyboardButton(text="🎨 NFT", callback_data=f"pay_nft_{product['platform_code']}_{product['period']}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_subscription")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ===== ИСПРАВЛЕНИЕ 2: Разные префиксы callback_data =====
# ЮMoney: "checkym_{order_id}"   (было "check_{order_id}" — конфликт!)
# Крипто: "checkcr_{order_id}"   (было "check_crypto_{order_id}")

def payment_keyboard(payment_url, order_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить ЮMoney", url=payment_url)],
        [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"checkym_{order_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="restart")]
    ])

def crypto_payment_keyboard(invoice_url, order_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="₿ Оплатить криптой", url=invoice_url)],
        [InlineKeyboardButton(text="✅ Проверить платеж", callback_data=f"checkcr_{order_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="restart")]
    ])

def support_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Поддержка", url=f"https://t.me/{SUPPORT_CHAT_USERNAME}")],
        [InlineKeyboardButton(text="🌐 Сайт", url=SHOP_URL)],
        [InlineKeyboardButton(text="🔄 Новая покупка", callback_data="restart")]
    ])

def about_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_platform")]
    ])

def admin_confirm_keyboard(order_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"admin_confirm_{order_id}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"admin_reject_{order_id}")]
    ])

# ========== ФУНКЦИИ ==========
def generate_order_id():
    return hashlib.md5(f"{time.time()}_{random.randint(1000, 9999)}".encode()).hexdigest()[:12]

def create_payment_link(amount, order_id, product_name):
    comment = f"Заказ {order_id}: {product_name}"
    return (
        f"https://yoomoney.ru/quickpay/confirm.xml"
        f"?receiver={YOOMONEY_WALLET}"
        f"&quickpay-form=shop"
        f"&targets={quote_plus(comment)}"
        f"&sum={amount}"
        f"&label={order_id}"
        f"&successURL=https://t.me/aimnoob_bot?start=success"
        f"&paymentType=AC"
    )

def generate_license_key(order_id, user_id):
    return f"AIMNOOB-{order_id[:8]}-{user_id % 10000}"

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# ========== КРИПТОБОТ API ==========
async def create_crypto_invoice(amount_usdt, order_id, description):
    if not CRYPTOBOT_TOKEN:
        return None

    url = "https://pay.crypt.bot/api/createInvoice"
    headers = {
        "Crypto-Pay-API-Token": CRYPTOBOT_TOKEN,
        "Content-Type": "application/json"
    }
    data = {
        "asset": "USDT",
        "amount": str(amount_usdt),
        "description": description,
        "payload": order_id,
        "paid_btn_name": "callback",
        "paid_btn_url": f"https://t.me/aimnoob_bot?start=paid_{order_id}"
    }

    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=data) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if result.get("ok"):
                        inv = result["result"]
                        return {
                            "invoice_id": inv.get("invoice_id"),
                            "pay_url": inv.get("pay_url"),
                            "amount": inv.get("amount")
                        }
                else:
                    body = await resp.text()
                    logger.error(f"CryptoBot createInvoice {resp.status}: {body}")
    except Exception as e:
        logger.error(f"CryptoBot API error: {e}")
    return None

async def check_crypto_invoice(invoice_id):
    if not CRYPTOBOT_TOKEN:
        return False

    url = "https://pay.crypt.bot/api/getInvoices"
    headers = {
        "Crypto-Pay-API-Token": CRYPTOBOT_TOKEN,
        "Content-Type": "application/json"
    }
    data = {"invoice_ids": [invoice_id]}

    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=data) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if result.get("ok"):
                        items = result.get("result", {}).get("items", [])
                        if items:
                            return items[0].get("status") == "paid"
    except Exception as e:
        logger.error(f"CryptoBot check error: {e}")
    return False

# ========== ЮMONEY ФУНКЦИИ ==========
async def get_yoomoney_balance():
    if not YOOMONEY_ACCESS_TOKEN:
        return None

    headers = {"Authorization": f"Bearer {YOOMONEY_ACCESS_TOKEN}"}
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get("https://yoomoney.ru/api/account-info", headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return float(data.get('balance', 0))
                else:
                    body = await resp.text()
                    logger.error(f"YooMoney account-info {resp.status}: {body}")
    except Exception as e:
        logger.error(f"YooMoney balance error: {e}")
    return None

# ===== ИСПРАВЛЕНИЕ 3: Улучшенная проверка платежа =====
async def check_yoomoney_payment(order_id, expected_amount):
    if not YOOMONEY_ACCESS_TOKEN:
        logger.warning("YOOMONEY_ACCESS_TOKEN не задан — проверка невозможна")
        return False

    headers = {"Authorization": f"Bearer {YOOMONEY_ACCESS_TOKEN}"}
    data = {"type": "deposition", "records": 100}

    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                "https://yoomoney.ru/api/operation-history",
                headers=headers,
                data=data
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"YooMoney history {resp.status}: {body}")
                    return False

                result = await resp.json()
                operations = result.get("operations", [])
                logger.info(f"YooMoney: получено {len(operations)} операций, ищем label={order_id}, сумма={expected_amount}")

                # 1) Точное совпадение по label
                for op in operations:
                    op_label = op.get("label", "")
                    op_status = op.get("status", "")
                    op_amount = float(op.get("amount", 0))

                    if op_label == order_id and op_status == "success":
                        if abs(op_amount - expected_amount) <= 5:
                            logger.info(f"✅ Найден платёж по label: {op}")
                            return True

                # 2) Фоллбэк: совпадение по сумме + время (30 мин)
                order_data = pending_orders.get(order_id)
                order_time = order_data.get("created_at", time.time()) if order_data else time.time()

                for op in operations:
                    if op.get("status") != "success":
                        continue
                    op_amount = float(op.get("amount", 0))
                    if abs(op_amount - expected_amount) > 2:
                        continue
                    try:
                        dt_str = op.get("datetime", "")
                        op_time = datetime.fromisoformat(dt_str.replace("Z", "+00:00")).timestamp()
                        if abs(op_time - order_time) <= 1800:
                            logger.info(f"✅ Найден платёж по сумме+времени: {op}")
                            return True
                    except Exception:
                        pass

    except Exception as e:
        logger.error(f"YooMoney check error: {e}")

    return False

# ========== ОБРАБОТКА УСПЕШНОГО ПЛАТЕЖА ==========
async def process_successful_payment(order_id, source="API"):
    order = pending_orders.get(order_id)
    if not order or order_id in confirmed_payments:
        return False

    product = order["product"]
    user_id = order["user_id"]
    license_key = generate_license_key(order_id, user_id)

    confirmed_payments[order_id] = {
        **order,
        'confirmed_at': time.time(),
        'confirmed_by': source,
        'license_key': license_key
    }

    success_text = (
        f"🎉 <b>Оплата подтверждена!</b>\n\n"
        f"✨ Добро пожаловать в AimNoob!\n\n"
        f"📦 <b>Ваша покупка:</b>\n"
        f"{product['emoji']} {product['name']}\n"
        f"⏱️ Срок: {product['duration']}\n"
        f"🔍 Метод: {source}\n\n"
        f"🔑 <b>Ваш лицензионный ключ:</b>\n"
        f"<code>{license_key}</code>\n\n"
        f"📥 <b>Скачивание:</b>\n"
        f"🔗 {SHOP_URL}/download/{product['platform_code']}_{user_id}\n\n"
        f"💫 <b>Активация:</b>\n"
        f"1️⃣ Скачайте файл по ссылке\n"
        f"2️⃣ Введите ключ при запуске\n"
        f"3️⃣ Наслаждайтесь игрой! 🎮\n\n"
        f"💬 Поддержка: @{SUPPORT_CHAT_USERNAME}"
    )

    try:
        await bot.send_message(user_id, success_text, parse_mode="HTML", reply_markup=support_keyboard())
    except Exception as e:
        logger.error(f"Ошибка отправки пользователю: {e}")

    # Уведомляем всех админов
    admin_text = (
        f"💎 <b>НОВАЯ ПРОДАЖА ({source})</b>\n\n"
        f"👤 {order['user_name']}\n"
        f"🆔 {user_id}\n"
        f"📦 {product['name']} ({product['duration']})\n"
        f"💰 {order.get('amount', product['price'])} {order.get('currency', '₽')}\n"
        f"🔑 <code>{license_key}</code>\n"
        f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(aid, admin_text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Ошибка уведомления админа {aid}: {e}")

    pending_orders.pop(order_id, None)
    return True

async def send_admin_notification(user, product, payment_method, price, order_id):
    message = (
        f"🔔 <b>НОВЫЙ ЗАКАЗ</b>\n\n"
        f"👤 {user.full_name}\n"
        f"🆔 <code>{user.id}</code>\n"
        f"📦 {product['name']} ({product['duration']})\n"
        f"💰 {price}\n"
        f"💳 {payment_method}\n"
        f"🆔 <code>{order_id}</code>\n\n"
        f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(aid, message, parse_mode="HTML", reply_markup=admin_confirm_keyboard(order_id))
        except Exception as e:
            logger.error(f"Ошибка отправки админу {aid}: {e}")

# ========== Хелпер: найти продукт по callback ==========
def find_product(parts_2, parts_3):
    for p in PRODUCTS.values():
        if p['platform_code'] == parts_2 and p['period'] == parts_3:
            return p
    return None

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()

    current_balance = await get_yoomoney_balance()
    if current_balance is not None:
        balance_history.append({'time': time.time(), 'balance': current_balance})

    text = (
        "🎯 <b>AimNoob — Премиум чит для Standoff 2</b>\n\n"
        "✨ <b>Возможности:</b>\n"
        "🛡️ Продвинутая защита от банов\n"
        "🎯 Умный AimBot с настройками\n"
        "👁️ WallHack и ESP\n"
        "📊 Полная информация о противниках\n"
        "⚡ Быстрые обновления\n\n"
        "🚀 <b>Выберите платформу:</b>"
    )

    await message.answer(text, parse_mode="HTML", reply_markup=platform_keyboard())
    await state.set_state(OrderState.choosing_platform)

@dp.callback_query(F.data == "about")
async def about_cheat(callback: types.CallbackQuery):
    text = (
        "📋 <b>Подробная информация</b>\n\n"
        "🎮 <b>Версия:</b> 0.37.1 (Март 2026)\n"
        "🔥 <b>Статус:</b> Активно обновляется\n\n"
        "🛠️ <b>Функционал:</b>\n"
        "• 🎯 Умный AimBot с плавностью\n"
        "• 👁️ WallHack через препятствия\n"
        "• 📍 ESP с информацией об игроках\n"
        "• 🗺️ Мини-радар\n"
        "• ⚙️ Гибкие настройки\n\n"
        "🛡️ <b>Безопасность:</b>\n"
        "• Обход античитов\n"
        "• Регулярные обновления\n"
        "• Тестирование на безопасность\n\n"
        f"💬 Поддержка: @{SUPPORT_CHAT_USERNAME}"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=about_keyboard())
    await callback.answer()

@dp.callback_query(F.data.startswith("platform_"))
async def process_platform(callback: types.CallbackQuery, state: FSMContext):
    platform = callback.data.split("_")[1]
    await state.update_data(platform=platform)

    if platform == "apk":
        text = (
            "📱 <b>Android Version</b>\n\n"
            "🔧 <b>Требования:</b>\n"
            "• Android 10.0+\n"
            "• 2 ГБ свободной памяти\n"
            "• Root не требуется\n\n"
            "📦 <b>Что входит:</b>\n"
            "• APK файл с читом\n"
            "• Инструкция по установке\n"
            "• Техническая поддержка\n\n"
            "💰 <b>Выберите тариф:</b>"
        )
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=apk_subscription_keyboard())
    else:
        text = (
            "🍎 <b>iOS Version</b>\n\n"
            "🔧 <b>Требования:</b>\n"
            "• iOS 14.0 - 18.0\n"
            "• Установка через AltStore\n"
            "• Jailbreak не требуется\n\n"
            "📦 <b>Что входит:</b>\n"
            "• IPA файл с читом\n"
            "• Подробная инструкция\n"
            "• Помощь в установке\n\n"
            "💰 <b>Выберите тариф:</b>"
        )
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=ios_subscription_keyboard())

    await state.set_state(OrderState.choosing_subscription)
    await callback.answer()

@dp.callback_query(F.data.startswith("sub_"))
async def process_subscription(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    product_key = f"{parts[1]}_{parts[2]}"
    product = PRODUCTS.get(product_key)

    if not product:
        await callback.answer("❌ Ошибка", show_alert=True)
        return

    await state.update_data(selected_product=product)

    text = (
        f"🛒 <b>Оформление покупки</b>\n\n"
        f"{product['emoji']} <b>{product['name']}</b>\n"
        f"⏱️ Длительность: {product['duration']}\n\n"
        f"💎 <b>Стоимость:</b>\n"
        f"💳 ЮMoney: {product['price']} ₽\n"
        f"⭐ Stars: {product['price_stars']} ⭐\n"
        f"₿ Крипта: {product['price_crypto_usdt']} USDT\n"
        f"💰 GOLD: {product['price_gold']} 🪙\n"
        f"🎨 NFT: {product['price_nft']} 🖼️\n\n"
        f"🎯 <b>Способ оплаты:</b>"
    )

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=payment_methods_keyboard(product))
    await state.set_state(OrderState.choosing_payment)
    await callback.answer()

# ========== ОПЛАТА ЮMONEY ==========
@dp.callback_query(F.data.startswith("pay_yoomoney_"))
async def process_yoomoney_payment(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    product = find_product(parts[2], parts[3])
    if not product:
        await callback.answer("❌ Ошибка", show_alert=True)
        return

    user_id = callback.from_user.id
    order_id = generate_order_id()
    amount = product["price"]
    payment_url = create_payment_link(amount, order_id, f"{product['name']} ({product['duration']})")

    pending_orders[order_id] = {
        "user_id": user_id,
        "user_name": callback.from_user.full_name,
        "product": product,
        "amount": amount,
        "currency": "₽",
        "payment_method": "ЮMoney",
        "status": "pending",
        "created_at": time.time()
    }

    text = (
        f"💳 <b>Оплата ЮMoney</b>\n\n"
        f"{product['emoji']} {product['name']}\n"
        f"⏱️ {product['duration']}\n"
        f"💰 К оплате: <b>{amount} ₽</b>\n"
        f"🆔 Номер заказа: <code>{order_id}</code>\n\n"
        f"🔄 <b>Инструкция:</b>\n"
        f"1️⃣ Нажмите «Оплатить ЮMoney»\n"
        f"2️⃣ Оплатите через ЮMoney\n"
        f"3️⃣ Вернитесь и нажмите «Проверить оплату»\n\n"
        f"💫 <b>Автоматическая проверка платежа</b>"
    )

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=payment_keyboard(payment_url, order_id))
    await send_admin_notification(callback.from_user, product, "💳 ЮMoney", f"{amount} ₽", order_id)
    await callback.answer()

# ===== ИСПРАВЛЕНИЕ 4: Обработчик "checkym_" (ЮMoney) =====
@dp.callback_query(F.data.startswith("checkym_"))
async def check_yoomoney_callback(callback: types.CallbackQuery):
    order_id = callback.data.removeprefix("checkym_")
    order = pending_orders.get(order_id)

    if not order:
        await callback.answer("❌ Заказ не найден или уже обработан", show_alert=True)
        return

    if order_id in confirmed_payments:
        await callback.answer("✅ Заказ уже подтвержден!", show_alert=True)
        return

    await callback.answer("🔍 Проверяем платеж...")

    checking_msg = await callback.message.edit_text(
        "🔄 <b>Проверка платежа...</b>\n\n"
        "🔍 Поиск транзакции в системе ЮMoney\n"
        "⏳ Подождите 15-25 секунд...",
        parse_mode="HTML"
    )

    payment_found = False
    for attempt in range(5):
        logger.info(f"Проверка ЮMoney платежа {order_id}, попытка {attempt + 1}/5")
        payment_found = await check_yoomoney_payment(order_id, order["amount"])
        if payment_found:
            break
        await asyncio.sleep(5)

    if payment_found:
        await process_successful_payment(order_id, "Автопроверка ЮMoney")
        await checking_msg.edit_text(
            "✅ <b>Платеж найден!</b>\n\n"
            "🎉 Ваш заказ обработан\n"
            "📨 Проверьте новое сообщение ⬆️",
            parse_mode="HTML",
            reply_markup=support_keyboard()
        )
    else:
        fail_text = (
            f"⏳ <b>Платеж пока не обнаружен</b>\n\n"
            f"💰 Сумма: {order['amount']} ₽\n"
            f"🆔 Заказ: <code>{order_id}</code>\n\n"
            f"🔍 <b>Возможные причины:</b>\n"
            f"• Платеж еще обрабатывается (1-3 мин)\n"
            f"• Оплачена неточная сумма\n"
            f"• Проблема на стороне банка\n\n"
            f"⏰ Попробуйте через 1-2 минуты\n"
            f"💬 Или обратитесь в поддержку"
        )
        payment_url = create_payment_link(order["amount"], order_id, f"{order['product']['name']} ({order['product']['duration']})")
        await checking_msg.edit_text(fail_text, parse_mode="HTML", reply_markup=payment_keyboard(payment_url, order_id))

# ========== ОПЛАТА STARS ==========
@dp.callback_query(F.data.startswith("pay_stars_"))
async def process_stars_payment(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    product = find_product(parts[2], parts[3])
    if not product:
        await callback.answer("❌ Ошибка", show_alert=True)
        return

    order_id = generate_order_id()

    pending_orders[order_id] = {
        "user_id": callback.from_user.id,
        "user_name": callback.from_user.full_name,
        "product": product,
        "amount": product['price_stars'],
        "currency": "⭐",
        "payment_method": "Telegram Stars",
        "status": "pending",
        "created_at": time.time()
    }

    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"AimNoob — {product['name']}",
        description=f"Подписка на {product['duration']} для {product['platform']}",
        payload=f"stars_{order_id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label="XTR", amount=product['price_stars'])],
        start_parameter="aimnoob_payment"
    )

    await callback.message.delete()
    await callback.answer()

@dp.pre_checkout_query()
async def pre_checkout_query_handler(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def successful_payment(message: types.Message):
    payload = message.successful_payment.invoice_payload
    if payload.startswith("stars_"):
        order_id = payload.removeprefix("stars_")
        await process_successful_payment(order_id, "Telegram Stars")

# ========== ОПЛАТА КРИПТО ==========
@dp.callback_query(F.data.startswith("pay_crypto_"))
async def process_crypto_payment(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    product = find_product(parts[2], parts[3])
    if not product:
        await callback.answer("❌ Ошибка", show_alert=True)
        return

    order_id = generate_order_id()
    amount_usdt = product["price_crypto_usdt"]
    description = f"AimNoob {product['name']} ({product['duration']})"

    invoice_data = await create_crypto_invoice(amount_usdt, order_id, description)
    if not invoice_data:
        await callback.answer("❌ Ошибка создания инвойса. Попробуйте позже.", show_alert=True)
        return

    pending_orders[order_id] = {
        "user_id": callback.from_user.id,
        "user_name": callback.from_user.full_name,
        "product": product,
        "amount": amount_usdt,
        "currency": "USDT",
        "payment_method": "CryptoBot",
        "status": "pending",
        "invoice_id": invoice_data["invoice_id"],
        "created_at": time.time()
    }

    text = (
        f"₿ <b>Криптооплата</b>\n\n"
        f"{product['emoji']} {product['name']}\n"
        f"⏱️ {product['duration']}\n"
        f"💰 К оплате: <b>{amount_usdt} USDT</b>\n"
        f"🆔 Заказ: <code>{order_id}</code>\n\n"
        f"🪙 <b>Принимаемые валюты:</b>\n"
        f"USDT, BTC, ETH, TON, LTC, BNB, TRX и др.\n\n"
        f"🔄 <b>Инструкция:</b>\n"
        f"1️⃣ Нажмите «Оплатить криптой»\n"
        f"2️⃣ Выберите валюту и переведите\n"
        f"3️⃣ Нажмите «Проверить платеж»"
    )

    await callback.message.edit_text(
        text, parse_mode="HTML",
        reply_markup=crypto_payment_keyboard(invoice_data["pay_url"], order_id)
    )
    await send_admin_notification(callback.from_user, product, "₿ CryptoBot", f"{amount_usdt} USDT", order_id)
    await callback.answer()

# ===== ИСПРАВЛЕНИЕ 5: Обработчик "checkcr_" (Крипто) =====
@dp.callback_query(F.data.startswith("checkcr_"))
async def check_crypto_callback(callback: types.CallbackQuery):
    order_id = callback.data.removeprefix("checkcr_")
    order = pending_orders.get(order_id)

    if not order:
        await callback.answer("❌ Заказ не найден", show_alert=True)
        return

    if order_id in confirmed_payments:
        await callback.answer("✅ Уже оплачено!", show_alert=True)
        return

    await callback.answer("🔍 Проверяем...")

    invoice_id = order.get("invoice_id")
    if not invoice_id:
        await callback.answer("❌ Ошибка: нет invoice_id", show_alert=True)
        return

    is_paid = await check_crypto_invoice(invoice_id)
    if is_paid:
        await process_successful_payment(order_id, "CryptoBot")
        await callback.message.edit_text(
            "✅ <b>Криптоплатеж подтвержден!</b>\n\n"
            "🎉 Заказ обработан\n"
            "📨 Ключ отправлен в новом сообщении ⬆️",
            parse_mode="HTML",
            reply_markup=support_keyboard()
        )
    else:
        await callback.answer("⏳ Платеж пока не подтвержден. Попробуйте через минуту.", show_alert=True)

# ========== ОПЛАТА GOLD ==========
@dp.callback_query(F.data.startswith("pay_gold_"))
async def process_gold_payment(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    product = find_product(parts[2], parts[3])
    if not product:
        await callback.answer("❌ Ошибка", show_alert=True)
        return

    price_gold = product['price_gold']
    chat_message = (
        f"Привет! Хочу купить чит на Standoff 2 🔑 Версия 0.37.1, "
        f"подписка на {product['period_text']} ({product['platform']}) — "
        f"готов купить за {price_gold} голды прямо сейчас 💰"
    )

    text = (
        f"💰 <b>Оплата GOLD</b>\n\n"
        f"{product['emoji']} {product['name']}\n"
        f"⏱️ {product['duration']}\n"
        f"💰 Стоимость: <b>{price_gold} GOLD</b>\n\n"
        f"📝 <b>Ваше сообщение для чата:</b>\n"
        f"<code>{chat_message}</code>\n\n"
        f"🔄 <b>Инструкция:</b>\n"
        f"1️⃣ Нажмите «Перейти к оплате»\n"
        f"2️⃣ Отправьте сообщение в чат\n"
        f"3️⃣ Ожидайте обработки"
    )

    support_url = f"https://t.me/{SUPPORT_CHAT_USERNAME}?text={quote_plus(chat_message)}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Перейти к оплате", url=support_url)],
        [InlineKeyboardButton(text="✅ Я написал", callback_data="gold_sent")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="restart")]
    ])

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    order_id = f"GOLD_{callback.from_user.id}_{int(time.time())}"
    await send_admin_notification(callback.from_user, product, "💰 GOLD", f"{price_gold} 🪙", order_id)
    await callback.answer()

# ========== ОПЛАТА NFT ==========
@dp.callback_query(F.data.startswith("pay_nft_"))
async def process_nft_payment(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    product = find_product(parts[2], parts[3])
    if not product:
        await callback.answer("❌ Ошибка", show_alert=True)
        return

    price_nft = product['price_nft']
    chat_message = (
        f"Привет! Хочу купить чит на Standoff 2 🔑 Версия 0.37.1, "
        f"подписка на {product['period_text']} ({product['platform']}) — "
        f"готов купить за {price_nft} NFT прямо сейчас 💰"
    )

    text = (
        f"🎨 <b>Оплата NFT</b>\n\n"
        f"{product['emoji']} {product['name']}\n"
        f"⏱️ {product['duration']}\n"
        f"💰 Стоимость: <b>{price_nft} NFT</b>\n\n"
        f"📝 <b>Ваше сообщение для чата:</b>\n"
        f"<code>{chat_message}</code>\n\n"
        f"🔄 <b>Инструкция:</b>\n"
        f"1️⃣ Нажмите «Перейти к оплате»\n"
        f"2️⃣ Отправьте сообщение в чат\n"
        f"3️⃣ Ожидайте обработки"
    )

    support_url = f"https://t.me/{SUPPORT_CHAT_USERNAME}?text={quote_plus(chat_message)}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎨 Перейти к оплате", url=support_url)],
        [InlineKeyboardButton(text="✅ Я написал", callback_data="nft_sent")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="restart")]
    ])

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    order_id = f"NFT_{callback.from_user.id}_{int(time.time())}"
    await send_admin_notification(callback.from_user, product, "🎨 NFT", f"{price_nft} 🖼️", order_id)
    await callback.answer()

@dp.callback_query(F.data == "gold_sent")
async def gold_sent(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "✅ <b>Отлично!</b>\n\n"
        "💫 Ваш запрос принят в обработку\n"
        "⏱️ Время обработки: до 30 минут\n"
        "📨 Уведомим о готовности заказа\n\n"
        f"💬 Поддержка: @{SUPPORT_CHAT_USERNAME}",
        parse_mode="HTML",
        reply_markup=support_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "nft_sent")
async def nft_sent(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "✅ <b>Превосходно!</b>\n\n"
        "🎨 Ваш NFT заказ принят\n"
        "⏱️ Время обработки: до 30 минут\n"
        "📨 Отправим ключ после проверки\n\n"
        f"💬 Поддержка: @{SUPPORT_CHAT_USERNAME}",
        parse_mode="HTML",
        reply_markup=support_keyboard()
    )
    await callback.answer()

# ========== АДМИНСКИЕ КОМАНДЫ ==========
@dp.callback_query(F.data.startswith("admin_confirm_"))
async def admin_confirm_payment(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return

    order_id = callback.data.removeprefix("admin_confirm_")

    if order_id in confirmed_payments:
        await callback.answer("✅ Уже подтвержден", show_alert=True)
        return

    success = await process_successful_payment(order_id, "👨‍💼 Админ")

    if success:
        await callback.message.edit_text(
            f"✅ <b>Заказ подтвержден</b>\n\n"
            f"🆔 {order_id}\n"
            f"👨‍💼 Подтвердил: {callback.from_user.full_name}\n"
            f"📨 Ключ отправлен пользователю",
            parse_mode="HTML"
        )
        await callback.answer("✅ Готово!")
    else:
        await callback.answer("❌ Ошибка (заказ не найден или уже обработан)", show_alert=True)

@dp.callback_query(F.data.startswith("admin_reject_"))
async def admin_reject_payment(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return

    order_id = callback.data.removeprefix("admin_reject_")
    order = pending_orders.pop(order_id, None)

    if order:
        await callback.message.edit_text(
            f"❌ <b>Заказ отклонен</b>\n\n"
            f"🆔 {order_id}\n"
            f"👨‍💼 Отклонил: {callback.from_user.full_name}",
            parse_mode="HTML"
        )
        try:
            await bot.send_message(
                order['user_id'],
                f"❌ <b>Заказ отклонен</b>\n\n"
                f"🆔 {order_id}\n"
                f"📞 Обратитесь в поддержку\n"
                f"💬 @{SUPPORT_CHAT_USERNAME}",
                parse_mode="HTML"
            )
        except:
            pass

    await callback.answer("❌ Отклонен")

@dp.message(Command("orders"))
async def cmd_orders(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    text = "📊 <b>СТАТИСТИКА ЗАКАЗОВ</b>\n\n"

    if pending_orders:
        text += f"⏳ <b>Ожидают оплаты:</b> {len(pending_orders)}\n"
        for oid, order in list(pending_orders.items())[:5]:
            t = datetime.fromtimestamp(order['created_at']).strftime('%H:%M')
            text += f"• {t} | {order['user_name']} | {order['product']['name']}\n"
    else:
        text += "⏳ <b>Ожидают оплаты:</b> 0\n"

    text += f"\n✅ <b>Подтверждено:</b> {len(confirmed_payments)}\n"

    balance = await get_yoomoney_balance()
    text += f"💰 <b>Баланс ЮМoney:</b> {balance} ₽\n" if balance else "💰 <b>Баланс ЮМoney:</b> ошибка\n"

    await message.answer(text, parse_mode="HTML")

# ========== НАВИГАЦИЯ ==========
@dp.callback_query(F.data == "restart")
async def restart_order(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await cmd_start(callback.message, state)
    await callback.answer()

@dp.callback_query(F.data == "back_to_platform")
async def back_to_platform(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await cmd_start(callback.message, state)
    await callback.answer()

@dp.callback_query(F.data == "back_to_subscription")
async def back_to_subscription(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    platform = data.get("platform", "apk")

    if platform == "apk":
        text = (
            "📱 <b>Android Version</b>\n\n"
            "🔧 <b>Требования:</b> Android 10.0+\n"
            "📦 <b>Что входит:</b> APK + Инструкция\n\n"
            "💰 <b>Выберите тариф:</b>"
        )
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=apk_subscription_keyboard())
    else:
        text = (
            "🍎 <b>iOS Version</b>\n\n"
            "🔧 <b>Требования:</b> iOS 14.0 - 18.0\n"
            "📦 <b>Что входит:</b> IPA + Инструкция\n\n"
            "💰 <b>Выберите тариф:</b>"
        )
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=ios_subscription_keyboard())

    await state.set_state(OrderState.choosing_subscription)
    await callback.answer()

# ========== ЗАПУСК ==========
async def main():
    print("🎯" + "=" * 50 + "🎯")
    print("🚀      AIMNOOB PREMIUM SHOP BOT       🚀")
    print("💎" + "=" * 50 + "💎")
    print(f"🔧 ADMIN_ID:        {ADMIN_ID}")
    print(f"🔧 SUPPORT_CHAT_ID: {SUPPORT_CHAT_ID}")
    print(f"🔧 ALL ADMIN_IDS:   {ADMIN_IDS}")

    if not BOT_TOKEN:
        print("❌ BOT_TOKEN не найден!")
        return

    try:
        balance = await get_yoomoney_balance()
        if balance is not None:
            print(f"✅ ЮMoney: подключен (баланс: {balance} ₽)")
            balance_history.append({'time': time.time(), 'balance': balance})
        else:
            print("⚠️  ЮMoney: проблемы с подключением")

        me = await bot.get_me()
        print(f"\n🤖 Бот: @{me.username}")
        print(f"💬 Поддержка: @{SUPPORT_CHAT_USERNAME}")
        print(f"🌐 Сайт: {SHOP_URL}")

        print(f"\n💳 СПОСОБЫ ОПЛАТЫ:")
        print(f"• 💳 ЮMoney (карты)")
        print(f"• ⭐ Telegram Stars")
        print(f"• ₿  CryptoBot")
        print(f"• 💰 GOLD (ручная)")
        print(f"• 🎨 NFT (ручная)")

        print(f"\n📦 ПРОДУКТЫ:")
        for key, product in PRODUCTS.items():
            print(f"• {product['emoji']} {product['name']} ({product['duration']}) — {product['price']}₽")

        print("🎯" + "=" * 50 + "🎯")
        print("✨ Бот запущен и готов к работе!")
        print("💎" + "=" * 50 + "💎")

        await dp.start_polling(bot)

    except Exception as e:
        print(f"❌ Ошибка запуска: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
