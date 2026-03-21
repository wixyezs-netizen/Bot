# aimnoob_bot_final.py
import logging
import asyncio
import hashlib
import time
import random
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = "8225924716:AAFzKnXZ8lJG_X1W9poH6Muyi-MMCXTWMy0"
ADMIN_ID = 8387532956

# Реквизиты для перевода
CARD_NUMBER = "+79002535363"
CARD_NAME = "Николай М."
CARD_BANK = "Сбербанк"

# Чат поддержки
SUPPORT_CHAT_USERNAME = "aimnoob_support"

# Настройки
BOT_USERNAME = "aimnoob_bot"
SHOP_URL = "https://aimnoob.ru"

# Создаем бота
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

logging.basicConfig(level=logging.INFO)
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
    waiting_for_screenshot = State()

# ========== КЛАВИАТУРЫ ==========
def platform_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Android (APK)", callback_data="platform_apk")],
        [InlineKeyboardButton(text="🍏 iOS", callback_data="platform_ios")],
        [InlineKeyboardButton(text="ℹ️ О чите", callback_data="about")]
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
        [InlineKeyboardButton(text="💳 Перевод (Сбер/Т-Банк)", callback_data=f"pay_transfer_{product['platform_code']}_{product['period']}")],
        [InlineKeyboardButton(text="⭐ Telegram Stars", callback_data=f"pay_stars_{product['platform_code']}_{product['period']}")],
        [InlineKeyboardButton(text="💰 GOLD (ЮMoney Gold)", callback_data=f"pay_gold_{product['platform_code']}_{product['period']}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_subscription")]
    ])

def transfer_keyboard(order_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я оплатил(а)", callback_data=f"paid_{order_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="restart")]
    ])

def admin_payment_keyboard(order_id, user_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить оплату", callback_data=f"confirm_payment_{order_id}_{user_id}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_payment_{order_id}_{user_id}")],
        [InlineKeyboardButton(text="📸 Посмотреть чек", callback_data=f"view_check_{order_id}")]
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
    return hashlib.md5(f"{time.time()}_{random.randint(1000, 9999)}".encode()).hexdigest()[:12]

async def send_to_admin(user, product, payment_method, price, order_id):
    platform_name = "Android" if product['platform_code'] == 'apk' else "iOS"
    message = (
        f"🆕 <b>НОВЫЙ ЗАКАЗ</b>\n\n"
        f"👤 {user.full_name}\n"
        f"🆔 User ID: <code>{user.id}</code>\n"
        f"📦 {product['name']}\n"
        f"📱 {platform_name}\n"
        f"💰 {price}\n"
        f"💳 {payment_method}\n"
        f"🆔 Заказ: <code>{order_id}</code>\n\n"
        f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )
    await bot.send_message(ADMIN_ID, message, parse_mode="HTML")

async def send_stars_payment(product, user_id):
    """Создание ссылки для оплаты Stars"""
    # Создаем инвойс для Telegram Stars
    from aiogram.types import LabeledPrice, PreCheckoutQuery
    
    # Сохраняем заказ
    order_id = generate_order_id()
    pending_orders[order_id] = {
        "user_id": user_id,
        "product": product,
        "amount": product['price_stars'],
        "payment_method": "STARS",
        "status": "pending",
        "created_at": time.time()
    }
    
    return order_id

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
        f"ℹ️ <b>О чите AimNoob</b>\n\n"
        f"🎮 Версия: 0.37.1\n"
        f"📅 Обновление: Март 2026\n\n"
        f"<b>Функции:</b>\n"
        f"• AimLock (автоприцел)\n"
        f"• WallHack (стены)\n"
        f"• ESP (информация)\n"
        f"• Radar (радар)\n\n"
        f"💬 Поддержка: @{SUPPORT_CHAT_USERNAME}"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=about_keyboard())
    await callback.answer()

@dp.callback_query(F.data.startswith("platform_"))
async def process_platform(callback: types.CallbackQuery, state: FSMContext):
    platform = callback.data.split("_")[1]
    await state.update_data(platform=platform)
    
    if platform == "apk":
        await callback.message.edit_text(
            "📱 <b>Android (APK)</b>\n\n✅ Android 10+\n✅ Root не требуется\n\nВыберите срок:",
            parse_mode="HTML", reply_markup=apk_subscription_keyboard()
        )
    else:
        await callback.message.edit_text(
            "🍏 <b>iOS</b>\n\n✅ iOS 14 - 18\n✅ Установка через AltStore\n\nВыберите срок:",
            parse_mode="HTML", reply_markup=ios_subscription_keyboard()
        )
    
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
        f"• Перевод: {product['price']} ₽\n"
        f"• GOLD: {product['price_stars']} ⭐\n"
        f"• STARS: {product['price_stars']} ⭐\n\n"
        f"💎 <b>Выберите способ оплаты:</b>"
    )
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=payment_methods_keyboard(product))
    await state.set_state(OrderState.choosing_payment)
    await callback.answer()

# ========== ОПЛАТА ПЕРЕВОДОМ (СБЕР) ==========
@dp.callback_query(F.data.startswith("pay_transfer_"))
async def process_transfer_payment(callback: types.CallbackQuery, state: FSMContext):
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
    
    # Сохраняем заказ
    pending_orders[order_id] = {
        "user_id": user_id,
        "product": product,
        "amount": product['price'],
        "payment_method": "TRANSFER",
        "status": "pending",
        "created_at": time.time()
    }
    
    text = (
        f"{product['emoji']} <b>Оплата переводом</b>\n\n"
        f"📦 {product['name']}\n"
        f"💰 Сумма: {product['price']} ₽\n"
        f"🆔 Заказ: <code>{order_id}</code>\n\n"
        f"📝 <b>Реквизиты для перевода:</b>\n"
        f"🏦 Банк: {CARD_BANK}\n"
        f"📞 Номер: <code>{CARD_NUMBER}</code>\n"
        f"👤 Получатель: {CARD_NAME}\n\n"
        f"💡 <b>Инструкция:</b>\n"
        f"1️⃣ Переведите {product['price']} ₽ по указанным реквизитам\n"
        f"2️⃣ Сделайте скриншот чека\n"
        f"3️⃣ Нажмите кнопку 'Я оплатил' и отправьте скриншот\n\n"
        f"⚠️ В комментарии к переводу укажите номер заказа: <code>{order_id}</code>"
    )
    
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=transfer_keyboard(order_id)
    )
    
    await send_to_admin(callback.from_user, product, "Перевод (Сбер)", f"{product['price']} ₽", order_id)
    await state.update_data(current_order_id=order_id)
    await state.set_state(OrderState.waiting_for_screenshot)
    await callback.answer()

@dp.callback_query(F.data.startswith("paid_"))
async def paid_button(callback: types.CallbackQuery, state: FSMContext):
    order_id = callback.data.replace("paid_", "")
    
    await callback.message.edit_text(
        f"📸 <b>Отправьте скриншот чека</b>\n\n"
        f"Пожалуйста, отправьте скриншот подтверждения оплаты.\n"
        f"🆔 Заказ: <code>{order_id}</code>\n\n"
        f"После проверки мы выдадим доступ к читу.",
        parse_mode="HTML"
    )
    
    await state.update_data(current_order_id=order_id)
    await state.set_state(OrderState.waiting_for_screenshot)
    await callback.answer()

@dp.message(OrderState.waiting_for_screenshot)
async def handle_screenshot(message: types.Message, state: FSMContext):
    data = await state.get_data()
    order_id = data.get('current_order_id')
    order = pending_orders.get(order_id)
    
    if not order:
        await message.answer("❌ Заказ не найден. Начните новый заказ.", reply_markup=restart_keyboard())
        await state.clear()
        return
    
    if message.photo:
        # Получаем фото
        photo = message.photo[-1]
        file_id = photo.file_id
        
        # Сохраняем информацию о скриншоте
        order['screenshot_file_id'] = file_id
        order['status'] = "waiting_confirmation"
        
        # Отправляем админу на подтверждение
        admin_text = (
            f"📸 <b>НОВЫЙ ЧЕК НА ПРОВЕРКУ</b>\n\n"
            f"👤 Покупатель: {message.from_user.full_name}\n"
            f"🆔 User ID: <code>{message.from_user.id}</code>\n"
            f"📦 Товар: {order['product']['name']}\n"
            f"💰 Сумма: {order['amount']} ₽\n"
            f"🆔 Заказ: <code>{order_id}</code>\n\n"
            f"⬇️ Чек ниже ⬇️"
        )
        
        # Отправляем админу фото и кнопки
        await bot.send_photo(
            ADMIN_ID,
            photo=file_id,
            caption=admin_text,
            parse_mode="HTML",
            reply_markup=admin_payment_keyboard(order_id, message.from_user.id)
        )
        
        await message.answer(
            f"✅ <b>Чек отправлен!</b>\n\n"
            f"🆔 Заказ: <code>{order_id}</code>\n\n"
            f"Мы проверим оплату в ближайшее время.\n"
            f"Обычно это занимает до 15 минут.\n\n"
            f"💬 Вопросы: @{SUPPORT_CHAT_USERNAME}",
            parse_mode="HTML",
            reply_markup=support_keyboard()
        )
        
        await state.clear()
        
    else:
        await message.answer(
            "❌ Пожалуйста, отправьте <b>фото чека</b> (скриншот).\n\n"
            "Нажмите на кнопку 📎 и выберите фото.",
            parse_mode="HTML"
        )

# ========== АДМИН-ПАНЕЛЬ ДЛЯ ПОДТВЕРЖДЕНИЯ ==========
@dp.callback_query(F.data.startswith("confirm_payment_"))
async def confirm_payment(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    order_id = parts[2]
    user_id = int(parts[3])
    
    order = pending_orders.get(order_id)
    if not order:
        await callback.answer("❌ Заказ не найден", show_alert=True)
        return
    
    product = order['product']
    license_key = f"AIMNOOB-{order_id[:8]}-{user_id % 10000}"
    
    # Отправляем пользователю ключ
    user_text = (
        f"✅ <b>Оплата подтверждена!</b>\n\n"
        f"🎉 Добро пожаловать в AimNoob!\n\n"
        f"📦 <b>Ваш заказ:</b>\n"
        f"• {product['name']}\n"
        f"• {product['emoji']} {product['platform']}\n\n"
        f"🔑 <b>Лицензионный ключ:</b>\n"
        f"<code>{license_key}</code>\n\n"
        f"📥 <b>Скачать:</b>\n"
        f"{SHOP_URL}/download/{product['platform_code']}_{user_id}\n\n"
        f"📖 Сохраните ключ! Он понадобится для активации.\n\n"
        f"💬 Поддержка: @{SUPPORT_CHAT_USERNAME}"
    )
    
    await bot.send_message(user_id, user_text, parse_mode="HTML", reply_markup=support_keyboard())
    
    # Обновляем статус
    order['status'] = "confirmed"
    order['license_key'] = license_key
    
    # Уведомляем админа
    await callback.message.edit_caption(
        f"✅ <b>ПЛАТЕЖ ПОДТВЕРЖДЕН!</b>\n\n"
        f"👤 {callback.from_user.full_name}\n"
        f"📦 {product['name']}\n"
        f"💰 {order['amount']} ₽\n"
        f"🆔 Заказ: <code>{order_id}</code>\n"
        f"🔑 Ключ: <code>{license_key}</code>\n\n"
        f"✅ Доступ выдан пользователю",
        parse_mode="HTML"
    )
    
    await callback.answer("✅ Оплата подтверждена!")

@dp.callback_query(F.data.startswith("reject_payment_"))
async def reject_payment(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    order_id = parts[2]
    user_id = int(parts[3])
    
    order = pending_orders.get(order_id)
    if order:
        order['status'] = "rejected"
    
    # Уведомляем пользователя
    await bot.send_message(
        user_id,
        f"❌ <b>Оплата НЕ подтверждена</b>\n\n"
        f"Платеж не найден или сумма не совпадает.\n\n"
        f"Проверьте:\n"
        f"• Правильная ли сумма перевода\n"
        f"• Правильные ли реквизиты\n\n"
        f"Если вы оплатили, отправьте чек повторно.\n\n"
        f"💬 Вопросы: @{SUPPORT_CHAT_USERNAME}",
        parse_mode="HTML",
        reply_markup=restart_keyboard()
    )
    
    # Обновляем сообщение админа
    await callback.message.edit_caption(
        f"❌ <b>ПЛАТЕЖ ОТКЛОНЕН</b>\n\n"
        f"Причина: платеж не найден\n"
        f"🆔 Заказ: <code>{order_id}</code>",
        parse_mode="HTML"
    )
    
    await callback.answer("❌ Платеж отклонен")

@dp.callback_query(F.data.startswith("view_check_"))
async def view_check(callback: types.CallbackQuery):
    order_id = callback.data.replace("view_check_", "")
    order = pending_orders.get(order_id)
    
    if order and order.get('screenshot_file_id'):
        await callback.message.answer_photo(
            order['screenshot_file_id'],
            caption=f"📸 Чек для заказа: <code>{order_id}</code>",
            parse_mode="HTML"
        )
    else:
        await callback.answer("Чек не найден", show_alert=True)
    
    await callback.answer()

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
        f"📝 <b>Инструкция:</b>\n"
        f"1️⃣ Нажмите кнопку ниже\n"
        f"2️⃣ Отправьте готовое сообщение в чат\n"
        f"3️⃣ Ожидайте подтверждения\n\n"
        f"💬 <b>Ваше сообщение:</b>\n"
        f"<code>{msg}</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 Перейти в чат", url=f"https://t.me/{SUPPORT_CHAT_USERNAME}")],
            [InlineKeyboardButton(text="✅ Я отправил(а)", callback_data="gold_sent")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="restart")]
        ])
    )
    
    await send_to_admin(callback.from_user, product, "GOLD", f"{product['price_stars']} ⭐", "GOLD_" + str(callback.from_user.id))
    await callback.answer()

# ========== ОПЛАТА STARS (TELEGRAM STARS) ==========
@dp.callback_query(F.data.startswith("pay_stars_"))
async def process_stars_payment(callback: types.CallbackQuery):
    from aiogram.types import LabeledPrice, PreCheckoutQuery
    
    parts = callback.data.split("_")
    for p in PRODUCTS.values():
        if p['platform_code'] == parts[2] and p['period'] == parts[3]:
            product = p
            break
    else:
        await callback.answer("Ошибка", show_alert=True)
        return
    
    order_id = generate_order_id()
    
    # Сохраняем заказ
    pending_orders[order_id] = {
        "user_id": callback.from_user.id,
        "product": product,
        "amount": product['price_stars'],
        "payment_method": "STARS",
        "status": "pending",
        "created_at": time.time()
    }
    
    # Создаем инвойс для оплаты Stars
    title = f"AimNoob - {product['name']}"
    description = f"Подписка на {product['period']} для {product['platform']}"
    payload = f"stars_{order_id}"
    currency = "XTR"  # Telegram Stars
    prices = [LabeledPrice(label="XTR", amount=product['price_stars'])]
    
    await callback.message.edit_text(
        f"⭐ <b>Оплата Telegram Stars</b>\n\n"
        f"📦 {product['name']}\n"
        f"💰 Стоимость: {product['price_stars']} ⭐\n\n"
        f"Нажмите кнопку ниже для оплаты:",
        parse_mode="HTML"
    )
    
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
    
    await callback.answer()

@dp.pre_checkout_query()
async def pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
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
            license_key = f"AIMNOOB-{order_id[:8]}-{user_id % 10000}"
            
            order['status'] = "confirmed"
            order['license_key'] = license_key
            
            # Отправляем пользователю ключ
            success_text = (
                f"✅ <b>Оплата Stars подтверждена!</b>\n\n"
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
            
            await message.answer(success_text, parse_mode="HTML", reply_markup=support_keyboard())
            
            # Уведомляем админа
            await bot.send_message(
                ADMIN_ID,
                f"✅ <b>ПРОДАЖА (STARS)</b>\n\n"
                f"👤 {message.from_user.full_name}\n"
                f"📦 {product['name']}\n"
                f"💰 {product['price_stars']} ⭐\n"
                f"🔑 Ключ: <code>{license_key}</code>",
                parse_mode="HTML"
            )

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
    print("="*50)
    print("🎯 AIMNOOB SHOP BOT")
    print("="*50)
    me = await bot.get_me()
    print(f"✅ Бот @{me.username} запущен!")
    print(f"💳 Реквизиты: {CARD_NUMBER} ({CARD_NAME})")
    print(f"💬 Поддержка: @{SUPPORT_CHAT_USERNAME}")
    print("="*50)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
