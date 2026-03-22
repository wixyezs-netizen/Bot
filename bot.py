# aimnoob_bot_universal.py
import logging
import asyncio
import aiohttp
import hashlib
import time
import random
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = "8225924716:AAFZ_8Eu8aJ4BF7pErZY5Ef3emG9Cl9PikE"
ADMIN_ID = 8387532956
SUPPORT_CHAT_ID = 8354762345

# Данные ЮMoney
YOOMONEY_ACCESS_TOKEN = "4100118889570559.3288B2E716CEEB922A26BD6BEAC58648FBFB680CCF64E4E1447D714D6FB5EA5F01F1478FAC686BEF394C8A186C98982DE563C1ABCDF9F2F61D971B61DA3C7E486CA818F98B9E0069F1C0891E090DD56A11319D626A40F0AE8302A8339DED9EB7969617F191D93275F64C4127A3ECB7AED33FCDE91CA68690EB7534C67E6C219E"
YOOMONEY_WALLET = "4100118889570559"

# Настройки
SUPPORT_CHAT_USERNAME = "aimnoob_support"
SHOP_URL = "https://aimnoob.ru"
YOOMONEY_FEE_RATE = 0.03

# ========== ИНИЦИАЛИЗАЦИЯ ==========
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Хранилища
pending_orders = {}
confirmed_payments = {}
balance_history = []

# ========== ФУНКЦИИ РАСЧЕТА ==========
def calculate_user_payment(amount):
    fee = amount * YOOMONEY_FEE_RATE
    if fee < 4.5:
        fee = 4.5
    return int(round(amount + fee))

# ========== ПРОДУКТЫ ==========
PRODUCTS = {
    "test_1rub": {
        "name": "🧪 ТЕСТОВЫЙ ТОВАР | 1₽",
        "price": 1,
        "price_with_fee": calculate_user_payment(1),
        "price_stars": 5,
        "platform": "Test",
        "period": "ТЕСТ",
        "platform_code": "test",
        "emoji": "🧪",
        "is_test": True
    },
    "apk_week": {
        "name": "AimNoob Standoff 2 | НЕДЕЛЯ",
        "price": 150,
        "price_with_fee": calculate_user_payment(150),
        "price_stars": 350,
        "platform": "Android",
        "period": "НЕДЕЛЮ",
        "platform_code": "apk",
        "emoji": "📱",
        "is_test": False
    },
    "apk_month": {
        "name": "AimNoob Standoff 2 | МЕСЯЦ",
        "price": 350,
        "price_with_fee": calculate_user_payment(350),
        "price_stars": 800,
        "platform": "Android",
        "period": "МЕСЯЦ",
        "platform_code": "apk",
        "emoji": "📱",
        "is_test": False
    },
    "apk_forever": {
        "name": "AimNoob Standoff 2 | НАВСЕГДА",
        "price": 150,
        "price_with_fee": calculate_user_payment(150),
        "price_stars": 350,
        "platform": "Android",
        "period": "НАВСЕГДА",
        "platform_code": "apk",
        "emoji": "📱",
        "is_test": False
    },
    "ios_week": {
        "name": "AimNoob Standoff 2 | НЕДЕЛЯ",
        "price": 300,
        "price_with_fee": calculate_user_payment(300),
        "price_stars": 700,
        "platform": "iOS",
        "period": "НЕДЕЛЮ",
        "platform_code": "ios",
        "emoji": "🍏",
        "is_test": False
    },
    "ios_month": {
        "name": "AimNoob Standoff 2 | МЕСЯЦ",
        "price": 450,
        "price_with_fee": calculate_user_payment(450),
        "price_stars": 1000,
        "platform": "iOS",
        "period": "МЕСЯЦ",
        "platform_code": "ios",
        "emoji": "🍏",
        "is_test": False
    },
    "ios_forever": {
        "name": "AimNoob Standoff 2 | НАВСЕГДА",
        "price": 850,
        "price_with_fee": calculate_user_payment(850),
        "price_stars": 2000,
        "platform": "iOS",
        "period": "НАВСЕГДА",
        "platform_code": "ios",
        "emoji": "🍏",
        "is_test": False
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
        [InlineKeyboardButton(text="🧪 ТЕСТ (1₽)", callback_data="platform_test")],
        [InlineKeyboardButton(text="📱 Android (APK)", callback_data="platform_apk")],
        [InlineKeyboardButton(text="🍏 iOS", callback_data="platform_ios")],
        [InlineKeyboardButton(text="ℹ️ О чите", callback_data="about")],
        [InlineKeyboardButton(text="📞 Поддержка", url=f"https://t.me/{SUPPORT_CHAT_USERNAME}")]
    ])

def test_subscription_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧪 ТЕСТ | 1₽ (+комиссия ≈6₽)", callback_data="sub_test_1rub")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_platform")]
    ])

def apk_subscription_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔓 НЕДЕЛЯ | 150₽ (+комиссия ≈156₽)", callback_data="sub_apk_week")],
        [InlineKeyboardButton(text="🔥 МЕСЯЦ | 350₽ (+комиссия ≈365₽)", callback_data="sub_apk_month")],
        [InlineKeyboardButton(text="⭐ НАВСЕГДА | 150₽ (+комиссия ≈156₽)", callback_data="sub_apk_forever")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_platform")]
    ])

def ios_subscription_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔓 НЕДЕЛЯ | 300₽ (+комиссия ≈313₽)", callback_data="sub_ios_week")],
        [InlineKeyboardButton(text="🔥 МЕСЯЦ | 450₽ (+комиссия ≈469₽)", callback_data="sub_ios_month")],
        [InlineKeyboardButton(text="⭐ НАВСЕГДА | 850₽ (+комиссия ≈886₽)", callback_data="sub_ios_forever")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_platform")]
    ])

def payment_methods_keyboard(product):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 ЮMoney", callback_data=f"pay_yoomoney_{product['platform_code']}_{product['period']}")],
        [InlineKeyboardButton(text="⭐ Telegram Stars", callback_data=f"pay_stars_{product['platform_code']}_{product['period']}")],
        [InlineKeyboardButton(text="💰 GOLD", callback_data=f"pay_gold_{product['platform_code']}_{product['period']}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_subscription")]
    ])

def payment_keyboard(payment_url, order_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить ЮMoney", url=payment_url)],
        [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check_{order_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="restart")]
    ])

def support_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Поддержка", url=f"https://t.me/{SUPPORT_CHAT_USERNAME}")],
        [InlineKeyboardButton(text="🌐 Сайт", url=SHOP_URL)],
        [InlineKeyboardButton(text="🔄 Новый заказ", callback_data="restart")]
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
        f"&targets={comment.replace(' ', '+')}"
        f"&sum={amount}"
        f"&label={order_id}"
        f"&successURL=https://t.me/aimnoob_bot?start=success"
        f"&paymentType=AC"
    )

async def get_yoomoney_balance():
    """Получение баланса ЮMoney"""
    if not YOOMONEY_ACCESS_TOKEN:
        return None
        
    headers = {"Authorization": f"Bearer {YOOMONEY_ACCESS_TOKEN}"}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://yoomoney.ru/api/account-info", headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return float(data.get('balance', 0))
    except Exception as e:
        logger.error(f"Ошибка получения баланса: {e}")
    
    return None

async def check_payment_advanced(order_id, expected_amount, product_price):
    """Продвинутая проверка платежа через несколько методов"""
    
    # Метод 1: Проверка через операции
    logger.info(f"🔍 Метод 1: Проверка через API операций")
    if await check_via_operations(order_id, expected_amount, product_price):
        return True
    
    # Метод 2: Проверка через изменение баланса
    logger.info(f"🔍 Метод 2: Проверка через баланс")
    if await check_via_balance(order_id, expected_amount):
        return True
    
    # Метод 3: Автоподтверждение через админа (для тестов)
    order = pending_orders.get(order_id)
    if order and order.get('product', {}).get('is_test', False):
        logger.info(f"🧪 Тестовый товар - автоподтверждение")
        return True
    
    return False

async def check_via_operations(order_id, expected_amount, product_price):
    """Проверка через API операций"""
    if not YOOMONEY_ACCESS_TOKEN:
        return False
    
    headers = {"Authorization": f"Bearer {YOOMONEY_ACCESS_TOKEN}"}
    data = {"records": 100, "type": "incoming"}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://yoomoney.ru/api/operation-history",
                headers=headers,
                data=data,
                timeout=15
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    operations = result.get("operations", [])
                    
                    logger.info(f"📋 Найдено операций: {len(operations)}")
                    
                    for op in operations:
                        op_label = op.get("label", "")
                        op_status = op.get("status")
                        op_amount = float(op.get("amount", 0))
                        op_datetime = op.get("datetime", "")
                        
                        # Проверяем по label
                        if op_label == order_id and op_status == "success":
                            if abs(op_amount - expected_amount) <= 2 or abs(op_amount - product_price) <= 2:
                                logger.info(f"✅ Найден платеж по label: {op_amount}₽")
                                return True
                        
                        # Проверяем по сумме и времени (для операций за последние 30 минут)
                        if op_status == "success" and op_datetime:
                            try:
                                op_time = datetime.fromisoformat(op_datetime.replace('Z', '+00:00'))
                                now = datetime.now(op_time.tzinfo)
                                if (now - op_time).total_seconds() <= 1800:  # 30 минут
                                    if abs(op_amount - expected_amount) <= 1 or abs(op_amount - product_price) <= 1:
                                        logger.info(f"✅ Найден платеж по сумме и времени: {op_amount}₽")
                                        return True
                            except:
                                pass
                    
                    return False
                else:
                    logger.error(f"Ошибка API операций: {resp.status}")
    except Exception as e:
        logger.error(f"Ошибка проверки операций: {e}")
    
    return False

async def check_via_balance(order_id, expected_amount):
    """Проверка через изменение баланса"""
    order = pending_orders.get(order_id)
    if not order:
        return False
    
    order_time = order.get('created_at', time.time())
    
    # Получаем текущий баланс
    current_balance = await get_yoomoney_balance()
    if current_balance is None:
        return False
    
    # Ищем в истории баланса подходящее изменение
    for balance_record in balance_history:
        if balance_record['time'] >= order_time:
            balance_diff = current_balance - balance_record['balance']
            
            # Проверяем, соответствует ли изменение баланса ожидаемому платежу
            if abs(balance_diff - expected_amount) <= 2:
                logger.info(f"✅ Обнаружено изменение баланса: +{balance_diff}₽")
                return True
    
    # Записываем текущий баланс для будущих проверок
    balance_history.append({
        'time': time.time(),
        'balance': current_balance
    })
    
    # Оставляем только записи за последние 2 часа
    cutoff_time = time.time() - 7200
    balance_history[:] = [r for r in balance_history if r['time'] > cutoff_time]
    
    return False

async def send_admin_notification(user, product, payment_method, price, order_id):
    """Отправка уведомления админу с возможностью подтверждения"""
    platform_name = "Android" if product['platform_code'] == 'apk' else "iOS"
    if product['platform_code'] == 'test':
        platform_name = "Тест"
    
    message = (
        f"🆕 <b>НОВЫЙ ЗАКАЗ</b>\n\n"
        f"👤 {user.full_name}\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"📦 {product['name']}\n"
        f"📱 {platform_name}\n"
        f"💰 {price}\n"
        f"💳 {payment_method}\n"
        f"🆔 Заказ: <code>{order_id}</code>\n\n"
        f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
        f"<b>⚡ Можете подтвердить вручную:</b>"
    )
    
    try:
        await bot.send_message(ADMIN_ID, message, parse_mode="HTML", reply_markup=admin_confirm_keyboard(order_id))
    except Exception as e:
        logger.error(f"Ошибка отправки админу: {e}")

def generate_license_key(order_id, user_id, is_test=False):
    """Генерация лицензионного ключа"""
    if is_test:
        return f"TEST-KEY-{order_id[:8]}"
    return f"AIMNOOB-{order_id[:8]}-{user_id % 10000}"

# ========== ОБРАБОТЧИКИ ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    
    # Записываем начальный баланс
    current_balance = await get_yoomoney_balance()
    if current_balance is not None:
        balance_history.append({
            'time': time.time(),
            'balance': current_balance
        })
    
    text = (
        "🎯 <b>AimNoob — лучший чит для Standoff 2</b> 🎯\n\n"
        "🔥 <b>Преимущества:</b>\n"
        "• ✅ Анти-бан система\n"
        "• 🎯 Идеальный AimLock\n"
        "• 👁️ WallHack через стены\n"
        "• 📊 ESP информация\n\n"
        "⚠️ <b>Важно:</b> ЮMoney берет комиссию ~3%\n"
        "Цена в кнопках уже с учетом комиссии!\n\n"
        "💎 <b>Выберите платформу:</b>"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=platform_keyboard())
    await state.set_state(OrderState.choosing_platform)

@dp.message(Command("balance"))
async def cmd_balance(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    balance = await get_yoomoney_balance()
    if balance is not None:
        await message.answer(f"💰 <b>Баланс ЮMoney:</b> {balance} ₽", parse_mode="HTML")
    else:
        await message.answer("❌ Ошибка получения баланса")

@dp.message(Command("orders"))
async def cmd_orders(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    if not pending_orders:
        await message.answer("📝 Активных заказов нет")
        return
    
    text = "📝 <b>Активные заказы:</b>\n\n"
    for order_id, order in pending_orders.items():
        created_time = datetime.fromtimestamp(order['created_at']).strftime('%H:%M:%S')
        text += (
            f"🆔 <code>{order_id}</code>\n"
            f"👤 {order['user_name']}\n"
            f"📦 {order['product']['name']}\n"
            f"💰 {order.get('user_payment_amount', order['product']['price'])} ₽\n"
            f"⏰ {created_time}\n\n"
        )
    
    await message.answer(text, parse_mode="HTML")

# Админские обработчики подтверждения
@dp.callback_query(F.data.startswith("admin_confirm_"))
async def admin_confirm_payment(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    order_id = callback.data.replace("admin_confirm_", "")
    await process_successful_payment(order_id, "Админ")
    
    await callback.message.edit_text(
        f"✅ <b>Заказ {order_id} подтвержден админом</b>",
        parse_mode="HTML"
    )
    await callback.answer("✅ Заказ подтвержден!")

@dp.callback_query(F.data.startswith("admin_reject_"))
async def admin_reject_payment(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    order_id = callback.data.replace("admin_reject_", "")
    order = pending_orders.get(order_id)
    
    if order:
        del pending_orders[order_id]
        await callback.message.edit_text(
            f"❌ <b>Заказ {order_id} отклонен админом</b>",
            parse_mode="HTML"
        )
        
        # Уведомляем пользователя
        try:
            await bot.send_message(
                order['user_id'],
                "❌ <b>Платеж отклонен</b>\n\n"
                f"Заказ {order_id} был отклонен.\n"
                f"Обратитесь в поддержку: @{SUPPORT_CHAT_USERNAME}",
                parse_mode="HTML"
            )
        except:
            pass
    
    await callback.answer("❌ Заказ отклонен!")

async def process_successful_payment(order_id, source="API"):
    """Обработка успешного платежа"""
    order = pending_orders.get(order_id)
    if not order:
        return False
    
    product = order["product"]
    user_id = order["user_id"]
    is_test = product.get("is_test", False)
    license_key = generate_license_key(order_id, user_id, is_test)
    
    # Отмечаем как подтвержденный
    confirmed_payments[order_id] = {
        **order,
        'confirmed_at': time.time(),
        'confirmed_by': source,
        'license_key': license_key
    }
    
    # Готовим текст для пользователя
    if is_test:
        success_text = (
            f"✅ <b>ТЕСТОВАЯ ОПЛАТА ПОДТВЕРЖДЕНА!</b>\n\n"
            f"🎉 Система работает корректно!\n\n"
            f"🔑 <b>Тестовый ключ:</b>\n"
            f"<code>{license_key}</code>\n\n"
            f"💬 Поддержка: @{SUPPORT_CHAT_USERNAME}"
        )
    else:
        success_text = (
            f"✅ <b>Оплата подтверждена!</b>\n\n"
            f"🎉 Добро пожаловать в AimNoob!\n\n"
            f"📦 <b>Ваш заказ:</b>\n"
            f"• {product['name']}\n"
            f"• {product['emoji']} {product['platform']}\n\n"
            f"🔑 <b>Лицензионный ключ:</b>\n"
            f"<code>{license_key}</code>\n\n"
            f"📥 <b>Скачать:</b>\n"
            f"{SHOP_URL}/download/{product['platform_code']}_{user_id}\n\n"
            f"💬 Поддержка: @{SUPPORT_CHAT_USERNAME}"
        )
    
    # Отправляем пользователю
    try:
        await bot.send_message(user_id, success_text, parse_mode="HTML", reply_markup=support_keyboard())
    except Exception as e:
        logger.error(f"Ошибка отправки пользователю: {e}")
    
    # Уведомляем админа о продаже
    try:
        await bot.send_message(
            ADMIN_ID,
            f"✅ <b>НОВАЯ ПРОДАЖА! ({source})</b>\n\n"
            f"👤 {order['user_name']}\n"
            f"📦 {product['name']}\n"
            f"💰 Оплачено: {order.get('user_payment_amount', product['price'])} ₽\n"
            f"🆔 Заказ: <code>{order_id}</code>\n"
            f"🔑 Ключ: <code>{license_key}</code>",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка уведомления админа: {e}")
    
    # Удаляем из ожидающих
    if order_id in pending_orders:
        del pending_orders[order_id]
    
    return True

@dp.callback_query(F.data == "about")
async def about_cheat(callback: types.CallbackQuery):
    text = (
        "ℹ️ <b>О чите AimNoob</b>\n\n"
        "🎮 <b>Версия:</b> 0.37.1\n"
        "📅 <b>Обновление:</b> Март 2026\n\n"
        "<b>Функции:</b>\n"
        "• AimLock (автоприцел)\n"
        "• WallHack (стены)\n"
        "• ESP (информация)\n"
        "• Radar (радар)\n\n"
        f"💬 Поддержка: @{SUPPORT_CHAT_USERNAME}"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=about_keyboard())
    await callback.answer()

@dp.callback_query(F.data.startswith("platform_"))
async def process_platform(callback: types.CallbackQuery, state: FSMContext):
    platform = callback.data.split("_")[1]
    await state.update_data(platform=platform)
    
    if platform == "test":
        text = "🧪 <b>Тестовый режим</b>\n\n✅ Проверка оплаты за 1₽\n✅ Проверка работы бота\n\nВыберите тестовый товар:"
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=test_subscription_keyboard())
    elif platform == "apk":
        text = "📱 <b>Android (APK)</b>\n\n✅ Android 10+\n✅ Root не требуется\n\n💰 Цены уже с учетом комиссии:\n• НЕДЕЛЯ: 156₽\n• МЕСЯЦ: 365₽\n• НАВСЕГДА: 156₽\n\nВыберите срок:"
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=apk_subscription_keyboard())
    else:
        text = "🍏 <b>iOS</b>\n\n✅ iOS 14 - 18\n✅ Установка через AltStore\n\n💰 Цены уже с учетом комиссии:\n• НЕДЕЛЯ: 313₽\n• МЕСЯЦ: 469₽\n• НАВСЕГДА: 886₽\n\nВыберите срок:"
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=ios_subscription_keyboard())
    
    await state.set_state(OrderState.choosing_subscription)
    await callback.answer()

@dp.callback_query(F.data.startswith("sub_"))
async def process_subscription(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    product_key = f"{parts[1]}_{parts[2]}"
    product = PRODUCTS.get(product_key)
    
    if not product:
        await callback.answer("Ошибка", show_alert=True)
        return
    
    await state.update_data(selected_product=product)
    
    text = (
        f"{product['emoji']} <b>Оформление заказа</b>\n\n"
        f"📦 {product['name']}\n"
        f"📱 {product['platform']}\n\n"
        f"💰 <b>Цены:</b>\n"
        f"• ЮMoney: {product['price']} ₽ + комиссия = {product['price_with_fee']} ₽\n"
        f"• STARS: {product['price_stars']} ⭐\n"
        f"• GOLD: {product['price_stars']} ⭐\n\n"
        f"💎 <b>Выберите способ оплаты:</b>"
    )
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=payment_methods_keyboard(product))
    await state.set_state(OrderState.choosing_payment)
    await callback.answer()

# ========== ОПЛАТА ЮMONEY ==========
@dp.callback_query(F.data.startswith("pay_yoomoney_"))
async def process_yoomoney_payment(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    for p in PRODUCTS.values():
        if p['platform_code'] == parts[2] and p['period'] == parts[3]:
            product = p
            break
    else:
        await callback.answer("Ошибка", show_alert=True)
        return
    
    user_id = callback.from_user.id
    order_id = generate_order_id()
    amount = product["price_with_fee"]
    payment_url = create_payment_link(amount, order_id, product["name"])
    
    # Записываем баланс перед заказом
    current_balance = await get_yoomoney_balance()
    if current_balance is not None:
        balance_history.append({
            'time': time.time(),
            'balance': current_balance
        })
    
    # Сохраняем заказ
    pending_orders[order_id] = {
        "user_id": user_id,
        "user_name": callback.from_user.full_name,
        "product": product,
        "user_payment_amount": amount,
        "product_price": product["price"],
        "payment_method": "ЮMoney",
        "status": "pending",
        "created_at": time.time()
    }
    
    text = (
        f"{product['emoji']} <b>Оплата ЮMoney</b>\n\n"
        f"📦 {product['name']}\n"
        f"💰 <b>Сумма к оплате: {amount} ₽</b>\n"
        f"(включая комиссию ЮMoney 3%)\n"
        f"🆔 Заказ: <code>{order_id}</code>\n\n"
        f"📝 <b>Инструкция:</b>\n"
        f"1️⃣ Нажмите кнопку оплаты\n"
        f"2️⃣ Оплатите <b>{amount} ₽</b>\n"
        f"3️⃣ Вернитесь и нажмите 'Проверить оплату'\n\n"
        f"⚠️ <b>Важно:</b> В комментарии укажите код: <code>{order_id}</code>\n\n"
        f"🔄 <b>Автоподтверждение:</b> Платежи подтверждаются автоматически!"
    )
    
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=payment_keyboard(payment_url, order_id)
    )
    
    await send_admin_notification(callback.from_user, product, "ЮMoney", f"{amount} ₽", order_id)
    await callback.answer()

@dp.callback_query(F.data.startswith("check_"))
async def check_payment_callback(callback: types.CallbackQuery):
    order_id = callback.data.replace("check_", "")
    order = pending_orders.get(order_id)
    
    if not order:
        await callback.answer("❌ Заказ не найден", show_alert=True)
        return
    
    # Проверяем, не подтвержден ли уже
    if order_id in confirmed_payments:
        await callback.answer("✅ Заказ уже подтвержден!", show_alert=True)
        return
    
    await callback.answer("🔍 Проверяю оплату...")
    
    checking_msg = await callback.message.edit_text(
        "🔄 <b>Проверяем платеж...</b>\n\n"
        "Используем несколько методов проверки:\n"
        "• API операций ЮMoney\n"
        "• Мониторинг баланса\n"
        "• Ручное подтверждение админом\n\n"
        "Пожалуйста, подождите 30 секунд...",
        parse_mode="HTML"
    )
    
    # Проверяем платеж продвинутыми методами
    payment_found = False
    for attempt in range(6):  # 6 попыток по 5 секунд
        logger.info(f"🔍 Попытка {attempt+1} проверки платежа {order_id}")
        
        payment_found = await check_payment_advanced(
            order_id, 
            order["user_payment_amount"],
            order["product_price"]
        )
        
        if payment_found:
            break
        
        await asyncio.sleep(5)
    
    if payment_found:
        await process_successful_payment(order_id, "Авто")
        
        await checking_msg.edit_text(
            "✅ <b>Платеж найден и подтвержден!</b>\n\n"
            "🎉 Ваш заказ обработан!\n"
            "Проверьте сообщения выше ⬆️",
            parse_mode="HTML",
            reply_markup=support_keyboard()
        )
    else:
        # Платеж не найден - показываем инструкции
        fail_text = (
            f"🔍 <b>Платеж пока не найден</b>\n\n"
            f"💰 Сумма к оплате: {order['user_payment_amount']} ₽\n"
            f"🆔 Заказ: <code>{order_id}</code>\n\n"
            f"<b>Что делать:</b>\n"
            f"• Убедитесь что оплачена точная сумма: <b>{order['user_payment_amount']} ₽</b>\n"
            f"• Проверьте комментарий: <code>{order_id}</code>\n"
            f"• Подождите 2-3 минуты и попробуйте снова\n"
            f"• Админ может подтвердить платеж вручную\n\n"
            f"💬 Поддержка: @{SUPPORT_CHAT_USERNAME}"
        )
        
        payment_url = create_payment_link(order["user_payment_amount"], order_id, order["product"]["name"])
        await checking_msg.edit_text(fail_text, parse_mode="HTML", reply_markup=payment_keyboard(payment_url, order_id))

# ========== ОСТАЛЬНЫЕ ПЛАТЕЖИ (GOLD, STARS) ==========
@dp.callback_query(F.data.startswith("pay_gold_"))
async def process_gold_payment(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    for p in PRODUCTS.values():
        if p['platform_code'] == parts[2] and p['period'] == parts[3]:
            product = p
            break
    else:
        await callback.answer("Ошибка", show_alert=True)
        return
    
    platform_name = "Android" if product['platform_code'] == 'apk' else "iOS"
    if product['platform_code'] == 'test':
        platform_name = "Тест"
    
    msg = (f"Привет! Хочу купить чит на Standoff 2 🔑 Версия 0.37.1, "
           f"подписка на {product['period']} ({platform_name}) — "
           f"готов купить за {product['price_stars']} голды прямо сейчас 💰")
    
    await callback.message.edit_text(
        f"{product['emoji']} <b>Оплата GOLD</b>\n\n"
        f"📦 {product['name']}\n"
        f"💰 Сумма: {product['price_stars']} GOLD\n\n"
        f"📝 <b>Ваше сообщение:</b>\n"
        f"<code>{msg}</code>\n\n"
        f"1️⃣ Нажмите кнопку ниже\n"
        f"2️⃣ Отправьте сообщение в чат",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 Перейти в чат", url=f"https://t.me/{SUPPORT_CHAT_USERNAME}")],
            [InlineKeyboardButton(text="✅ Я отправил(а)", callback_data="gold_sent")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="restart")]
        ])
    )
    
    await send_admin_notification(callback.from_user, product, "GOLD", f"{product['price_stars']} ⭐", "GOLD_" + str(callback.from_user.id))
    await callback.answer()

@dp.callback_query(F.data == "gold_sent")
async def gold_sent(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "✅ <b>Спасибо!</b>\n\n"
        "Ваш запрос отправлен. Мы проверим и выдадим доступ.\n\n"
        f"💬 Поддержка: @{SUPPORT_CHAT_USERNAME}",
        parse_mode="HTML",
        reply_markup=support_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("pay_stars_"))
async def process_stars_payment(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    for p in PRODUCTS.values():
        if p['platform_code'] == parts[2] and p['period'] == parts[3]:
            product = p
            break
    else:
        await callback.answer("Ошибка", show_alert=True)
        return
    
    order_id = generate_order_id()
    
    pending_orders[order_id] = {
        "user_id": callback.from_user.id,
        "user_name": callback.from_user.full_name,
        "product": product,
        "amount": product['price_stars'],
        "payment_method": "STARS",
        "status": "pending",
        "created_at": time.time()
    }
    
    title = f"AimNoob - {product['name']}"
    description = f"Подписка на {product['period']} для {product['platform']}"
    payload = f"stars_{order_id}"
    currency = "XTR"
    prices = [LabeledPrice(label="XTR", amount=product['price_stars'])]
    
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=title,
        description=description,
        payload=payload,
        provider_token="",
        currency=currency,
        prices=prices,
        start_parameter="aimnoob_payment",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Оплатить Stars", pay=True)]
        ])
    )
    
    await callback.message.delete()
    await callback.answer()

@dp.pre_checkout_query()
async def pre_checkout_query_handler(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def successful_payment(message: types.Message):
    payment = message.successful_payment
    payload = payment.invoice_payload
    
    if payload.startswith("stars_"):
        order_id = payload.replace("stars_", "")
        await process_successful_payment(order_id, "Stars")

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
    
    if platform == "test":
        await callback.message.edit_text(
            "🧪 <b>Тестовый режим</b>\n\nВыберите тестовый товар:",
            parse_mode="HTML",
            reply_markup=test_subscription_keyboard()
        )
    elif platform == "apk":
        await callback.message.edit_text(
            "📱 <b>Android (APK)</b>\n\nВыберите срок:",
            parse_mode="HTML",
            reply_markup=apk_subscription_keyboard()
        )
    else:
        await callback.message.edit_text(
            "🍏 <b>iOS</b>\n\nВыберите срок:",
            parse_mode="HTML",
            reply_markup=ios_subscription_keyboard()
        )
    
    await state.set_state(OrderState.choosing_subscription)
    await callback.answer()

# ========== ЗАПУСК ==========
async def main():
    print("="*60)
    print("🎯 AIMNOOB SHOP BOT (УНИВЕРСАЛЬНАЯ ВЕРСИЯ)")
    print("="*60)
    
    # Проверка ЮMoney
    balance = await get_yoomoney_balance()
    if balance is not None:
        print(f"✅ ЮMoney подключен! Баланс: {balance} ₽")
        balance_history.append({'time': time.time(), 'balance': balance})
    else:
        print("⚠️ Проблемы с подключением к ЮMoney")
    
    me = await bot.get_me()
    print(f"\n✅ Бот @{me.username} запущен!")
    print(f"💳 Кошелек ЮMoney: {YOOMONEY_WALLET}")
    print(f"💬 Поддержка: @{SUPPORT_CHAT_USERNAME}")
    print("="*60)
    print("🚀 ОСОБЕННОСТИ ЭТОЙ ВЕРСИИ:")
    print("• 🔍 Многоуровневая проверка платежей")
    print("• 💰 Мониторинг изменения баланса")
    print("• ⚡ Ручное подтверждение админом")
    print("• 🧪 Автоподтверждение тестовых платежей")
    print("="*60)
    print("📝 Админские команды:")
    print("• /balance - баланс ЮMoney")
    print("• /orders - активные заказы")
    print("="*60)
    print("✅ Ожидание сообщений...")
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
