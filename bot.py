# aimnoob_bot.py - ПОЛНЫЙ ГОТОВЫЙ БОТ
import logging
import asyncio
import aiohttp
import hashlib
import time
import random
from datetime import datetime
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

# Данные ЮMoney
CLIENT_ID = "5FE649CABBAD2E9FE9095C8DB64AF17CCC754D9179A8B8D41B9689281A295AF7"
REDIRECT_URI = "https://yoomoney.ru"

# ✅ ВАШ ACCESS TOKEN (уже получен)
YOOMONEY_ACCESS_TOKEN = "4100118889570559.3288B2E716CEEB922A26BD6BEAC58648FBFB680CCF64E4E1447D714D6FB5EA5F01F1478FAC686BEF394C8A186C98982DE563C1ABCDF9F2F61D971B61DA3C7E486CA818F98B9E0069F1C0891E090DD56A11319D626A40F0AE8302A8339DED9EB7969617F191D93275F64C4127A3ECB7AED33FCDE91CA68690EB7534C67E6C219E"

# Номер кошелька ЮMoney (ВАШ НОМЕР КОШЕЛЬКА)
YOOMONEY_WALLET = "4100118889570559"  # ЗАМЕНИТЕ НА ВАШ!

# Чат поддержки (ВАШ ЧАТ)
SUPPORT_CHAT_USERNAME = "aimnoob_support"  # ЗАМЕНИТЕ НА ВАШ!

# Ссылка на скачивание (ВАШ САЙТ)
SHOP_URL = "https://aimnoob.ru"

# ========== ИНИЦИАЛИЗАЦИЯ БОТА ==========
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Хранилище заказов
pending_orders = {}

# ========== ПРОДУКТЫ ==========
PRODUCTS = {
    "apk_week": {
        "name": "AimNoob Standoff 2 | НЕДЕЛЯ",
        "price": 150,
        "price_stars": 350,
        "platform": "Android",
        "period": "НЕДЕЛЮ",
        "platform_code": "apk",
        "emoji": "📱"
    },
    "apk_month": {
        "name": "AimNoob Standoff 2 | МЕСЯЦ",
        "price": 350,
        "price_stars": 800,
        "platform": "Android",
        "period": "МЕСЯЦ",
        "platform_code": "apk",
        "emoji": "📱"
    },
    "apk_forever": {
        "name": "AimNoob Standoff 2 | НАВСЕГДА",
        "price": 150,
        "price_stars": 350,
        "platform": "Android",
        "period": "НАВСЕГДА",
        "platform_code": "apk",
        "emoji": "📱"
    },
    "ios_week": {
        "name": "AimNoob Standoff 2 | НЕДЕЛЯ",
        "price": 300,
        "price_stars": 700,
        "platform": "iOS",
        "period": "НЕДЕЛЮ",
        "platform_code": "ios",
        "emoji": "🍏"
    },
    "ios_month": {
        "name": "AimNoob Standoff 2 | МЕСЯЦ",
        "price": 450,
        "price_stars": 1000,
        "platform": "iOS",
        "period": "МЕСЯЦ",
        "platform_code": "ios",
        "emoji": "🍏"
    },
    "ios_forever": {
        "name": "AimNoob Standoff 2 | НАВСЕГДА",
        "price": 850,
        "price_stars": 2000,
        "platform": "iOS",
        "period": "НАВСЕГДА",
        "platform_code": "ios",
        "emoji": "🍏"
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
        [InlineKeyboardButton(text="📱 Android (APK)", callback_data="platform_apk")],
        [InlineKeyboardButton(text="🍏 iOS", callback_data="platform_ios")],
        [InlineKeyboardButton(text="ℹ️ О чите", callback_data="about")],
        [InlineKeyboardButton(text="📞 Поддержка", url=f"https://t.me/{SUPPORT_CHAT_USERNAME}")]
    ])

def apk_subscription_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔓 НЕДЕЛЯ | 150₽ / 350⭐", callback_data="sub_apk_week")],
        [InlineKeyboardButton(text="🔥 МЕСЯЦ | 350₽ / 800⭐", callback_data="sub_apk_month")],
        [InlineKeyboardButton(text="⭐ НАВСЕГДА | 150₽ / 350⭐", callback_data="sub_apk_forever")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_platform")]
    ])

def ios_subscription_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔓 НЕДЕЛЯ | 300₽ / 700⭐", callback_data="sub_ios_week")],
        [InlineKeyboardButton(text="🔥 МЕСЯЦ | 450₽ / 1000⭐", callback_data="sub_ios_month")],
        [InlineKeyboardButton(text="⭐ НАВСЕГДА | 850₽ / 2000⭐", callback_data="sub_ios_forever")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_platform")]
    ])

def payment_methods_keyboard(product):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 ЮMoney (Карта/СБП)", callback_data=f"pay_yoomoney_{product['platform_code']}_{product['period']}")],
        [InlineKeyboardButton(text="⭐ Telegram Stars", callback_data=f"pay_stars_{product['platform_code']}_{product['period']}")],
        [InlineKeyboardButton(text="💰 GOLD (ЮMoney Gold)", callback_data=f"pay_gold_{product['platform_code']}_{product['period']}")],
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

def restart_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Новый заказ", callback_data="restart")]
    ])

def about_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_platform")]
    ])

# ========== ФУНКЦИИ ==========
def generate_order_id():
    """Генерация уникального ID заказа"""
    return hashlib.md5(f"{time.time()}_{random.randint(1000, 9999)}".encode()).hexdigest()[:12]

def create_payment_link(amount, order_id, product_name):
    """Создает ссылку для оплаты через ЮMoney"""
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

async def check_yoomoney_payment(order_id, amount):
    """Проверка платежа через API ЮMoney"""
    if not YOOMONEY_ACCESS_TOKEN:
        logger.error("❌ ACCESS TOKEN не настроен!")
        return False
    
    headers = {
        "Authorization": f"Bearer {YOOMONEY_ACCESS_TOKEN}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    
    data = {
        "label": order_id,
        "records": 10,
        "type": "incoming"
    }
    
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
                    for op in result.get("operations", []):
                        if op.get("label") == order_id and op.get("status") == "success":
                            op_amount = float(op.get("amount", 0))
                            if abs(op_amount - amount) < 0.01:
                                logger.info(f"✅ Платеж {order_id} найден!")
                                return True
                elif resp.status == 401:
                    logger.error("❌ Токен недействителен!")
    except Exception as e:
        logger.error(f"Ошибка проверки: {e}")
    
    return False

async def send_to_admin(user, product, payment_method, price, order_id):
    """Отправка уведомления админу"""
    platform_name = "Android" if product['platform_code'] == 'apk' else "iOS"
    message = (
        f"🆕 <b>НОВЫЙ ЗАКАЗ</b>\n\n"
        f"👤 {user.full_name}\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"📦 {product['name']}\n"
        f"📱 {platform_name}\n"
        f"💰 {price}\n"
        f"💳 {payment_method}\n"
        f"🆔 Заказ: <code>{order_id}</code>\n\n"
        f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )
    await bot.send_message(ADMIN_ID, message, parse_mode="HTML")

def generate_license_key(order_id, user_id):
    """Генерация лицензионного ключа"""
    return f"AIMNOOB-{order_id[:8]}-{user_id % 10000}"

# ========== ОБРАБОТЧИКИ ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    text = (
        "🎯 <b>AimNoob — лучший чит для Standoff 2</b> 🎯\n\n"
        "🔥 <b>Преимущества:</b>\n"
        "• ✅ Анти-бан система\n"
        "• 🎯 Идеальный AimLock\n"
        "• 👁️ WallHack через стены\n"
        "• 📊 ESP информация\n\n"
        "💎 <b>Выберите платформу:</b>"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=platform_keyboard())
    await state.set_state(OrderState.choosing_platform)

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
    
    if platform == "apk":
        text = "📱 <b>Android (APK)</b>\n\n✅ Android 10+\n✅ Root не требуется\n\nВыберите срок:"
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=apk_subscription_keyboard())
    else:
        text = "🍏 <b>iOS</b>\n\n✅ iOS 14 - 18\n✅ Установка через AltStore\n\nВыберите срок:"
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
        f"• ЮMoney: {product['price']} ₽\n"
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
    amount = product["price"]
    payment_url = create_payment_link(amount, order_id, product["name"])
    
    # Сохраняем заказ
    pending_orders[order_id] = {
        "user_id": user_id,
        "user_name": callback.from_user.full_name,
        "product": product,
        "amount": amount,
        "payment_method": "ЮMoney",
        "status": "pending",
        "created_at": time.time()
    }
    
    text = (
        f"{product['emoji']} <b>Оплата ЮMoney</b>\n\n"
        f"📦 {product['name']}\n"
        f"💰 Сумма: {amount} ₽\n"
        f"🆔 Заказ: <code>{order_id}</code>\n\n"
        f"📝 <b>Инструкция:</b>\n"
        f"1️⃣ Нажмите кнопку оплаты\n"
        f"2️⃣ Оплатите {amount} ₽\n"
        f"3️⃣ Вернитесь и нажмите 'Проверить оплату'\n\n"
        f"⚠️ <b>Важно:</b> В комментарии укажите код: <code>{order_id}</code>"
    )
    
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=payment_keyboard(payment_url, order_id)
    )
    
    await send_to_admin(callback.from_user, product, "ЮMoney", f"{amount} ₽", order_id)
    await callback.answer()

@dp.callback_query(F.data.startswith("check_"))
async def check_payment_callback(callback: types.CallbackQuery):
    order_id = callback.data.replace("check_", "")
    order = pending_orders.get(order_id)
    
    if not order:
        await callback.answer("❌ Заказ не найден", show_alert=True)
        return
    
    await callback.answer("🔍 Проверяю оплату...")
    
    checking_msg = await callback.message.edit_text(
        "🔄 <b>Проверяем платеж...</b>\n\nПожалуйста, подождите.",
        parse_mode="HTML"
    )
    
    # Проверяем платеж
    payment_received = await check_yoomoney_payment(order_id, order["amount"])
    
    if payment_received:
        product = order["product"]
        user_id = order["user_id"]
        license_key = generate_license_key(order_id, user_id)
        
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
        
        await checking_msg.edit_text(success_text, parse_mode="HTML", reply_markup=support_keyboard())
        
        # Уведомляем админа
        await bot.send_message(
            ADMIN_ID,
            f"✅ <b>НОВАЯ ПРОДАЖА!</b>\n\n"
            f"👤 {order['user_name']}\n"
            f"📦 {product['name']}\n"
            f"💰 {order['amount']} ₽\n"
            f"🆔 Заказ: <code>{order_id}</code>\n"
            f"🔑 Ключ: <code>{license_key}</code>",
            parse_mode="HTML"
        )
        
        # Удаляем заказ
        del pending_orders[order_id]
        
    else:
        payment_url = create_payment_link(order["amount"], order_id, order["product"]["name"])
        
        fail_text = (
            f"❌ <b>Платеж не найден</b>\n\n"
            f"💰 Сумма: {order['amount']} ₽\n"
            f"🆔 Заказ: <code>{order_id}</code>\n\n"
            f"<b>Проверьте:</b>\n"
            f"• Оплачена ли точная сумма {order['amount']} ₽\n"
            f"• Указан ли код в комментарии: <code>{order_id}</code>\n"
            f"• Если оплатили — подождите 1-2 минуты\n\n"
            f"💬 Не помогло? Напишите @{SUPPORT_CHAT_USERNAME}"
        )
        
        await checking_msg.edit_text(fail_text, parse_mode="HTML", reply_markup=payment_keyboard(payment_url, order_id))

# ========== ОПЛАТА GOLD ==========
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
    
    await send_to_admin(callback.from_user, product, "GOLD", f"{product['price_stars']} ⭐", "GOLD_" + str(callback.from_user.id))
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

# ========== ОПЛАТА TELEGRAM STARS ==========
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
        order = pending_orders.get(order_id)
        
        if order:
            product = order['product']
            user_id = message.from_user.id
            license_key = generate_license_key(order_id, user_id)
            
            order['status'] = "confirmed"
            
            success_text = (
                f"✅ <b>Оплата Stars подтверждена!</b>\n\n"
                f"🎉 Добро пожаловать в AimNoob!\n\n"
                f"📦 {product['name']}\n"
                f"🔑 <b>Ключ:</b> <code>{license_key}</code>\n\n"
                f"📥 <b>Скачать:</b>\n"
                f"{SHOP_URL}/download/{product['platform_code']}_{user_id}\n\n"
                f"💬 Поддержка: @{SUPPORT_CHAT_USERNAME}"
            )
            
            await message.answer(success_text, parse_mode="HTML", reply_markup=support_keyboard())
            
            await bot.send_message(
                ADMIN_ID,
                f"✅ <b>ПРОДАЖА (STARS)</b>\n\n"
                f"👤 {message.from_user.full_name}\n"
                f"📦 {product['name']}\n"
                f"💰 {product['price_stars']} ⭐\n"
                f"🔑 Ключ: <code>{license_key}</code>",
                parse_mode="HTML"
            )
            
            del pending_orders[order_id]

# ========== КНОПКИ НАВИГАЦИИ ==========
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
    print("🎯 AIMNOOB SHOP BOT")
    print("="*60)
    
    # Проверка токена
    if YOOMONEY_ACCESS_TOKEN:
        print("✅ ACCESS TOKEN настроен!")
        # Проверяем работу токена
        headers = {"Authorization": f"Bearer {YOOMONEY_ACCESS_TOKEN}"}
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get("https://yoomoney.ru/api/account-info", headers=headers, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        print(f"✅ ЮMoney подключен!")
                        print(f"👤 Аккаунт: {data.get('account')}")
                        print(f"💰 Баланс: {data.get('balance')} ₽")
                    else:
                        print(f"⚠️ Не удалось проверить токен: {resp.status}")
            except Exception as e:
                print(f"⚠️ Ошибка проверки токена: {e}")
    else:
        print("❌ ACCESS TOKEN не настроен!")
    
    me = await bot.get_me()
    print(f"\n✅ Бот @{me.username} запущен!")
    print(f"💳 Кошелек ЮMoney: {YOOMONEY_WALLET}")
    print(f"💬 Поддержка: @{SUPPORT_CHAT_USERNAME}")
    print("="*60)
    print("✅ Ожидание сообщений...")
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
