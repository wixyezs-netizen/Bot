import asyncio
import logging
import sqlite3
import secrets
import random
from datetime import datetime
from typing import Dict, Optional

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
    Message, LabeledPrice, PreCheckoutQuery
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ==================== КОНФИГУРАЦИЯ ====================
BOT_TOKEN = "8225924716:AAFZ_8Eu8aJ4BF7pErZY5Ef3emG9Cl9PikE"  # ВСТАВЬТЕ ВАШ ТОКЕН
ADMIN_ID = 8387532956  # ВАШ Telegram ID (узнайте у @userinfobot)

# ========== ЮMoney настройки (для кнопки) ==========
YOOMONEY_RECEIVER = "4100118889570559"  # Ваш кошелек ЮMoney

# ========== CryptoBot настройки ==========
CRYPTO_BOT_TOKEN = "493276:AAtS7R1zYy0gaPw8eax1EgiWo0tdnd6dQ9c"

# База данных
DB_NAME = "shop_bot.db"

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# ==================== РАБОТА С БАЗОЙ ДАННЫХ ====================
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            product TEXT,
            price INTEGER,
            currency TEXT,
            status TEXT,
            created_at TIMESTAMP,
            paid_at TIMESTAMP
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance INTEGER DEFAULT 0,
            created_at TIMESTAMP
        )
    """)
    
    conn.commit()
    conn.close()
    logger.info("База данных инициализирована")

def add_order(user_id, username, product, price, currency, status="pending"):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO orders (user_id, username, product, price, currency, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, username, product, price, currency, status, datetime.now())
    )
    order_id = cur.lastrowid
    conn.commit()
    conn.close()
    return order_id

def update_order_status(order_id, status, paid_at=None):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    if paid_at:
        cur.execute("UPDATE orders SET status = ?, paid_at = ? WHERE id = ?", (status, paid_at, order_id))
    else:
        cur.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
    conn.commit()
    conn.close()

def get_order(order_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT user_id, product, price, status FROM orders WHERE id = ?", (order_id,))
    result = cur.fetchone()
    conn.close()
    return result

def get_user_orders_count(user_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM orders WHERE user_id = ? AND status = 'paid'", (user_id,))
    count = cur.fetchone()[0]
    conn.close()
    return count

def get_all_orders_count():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM orders")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM orders WHERE status = 'paid'")
    paid = cur.fetchone()[0]
    cur.execute("SELECT SUM(price) FROM orders WHERE status = 'paid'")
    revenue = cur.fetchone()[0] or 0
    conn.close()
    return total, paid, revenue

# ==================== FSM СОСТОЯНИЯ ====================
class OrderState(StatesGroup):
    choosing_platform = State()
    choosing_duration = State()
    choosing_payment = State()
    waiting_for_receipt = State()

# ==================== КЛАВИАТУРЫ ====================
def main_menu_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="🛒 Купить чит", callback_data="buy_cheat")
    builder.button(text="👤 Профиль", callback_data="profile")
    builder.button(text="🎁 Акции", callback_data="promo")
    builder.button(text="👥 Пригласить друга", callback_data="invite")
    builder.adjust(1)
    return builder.as_markup()

def games_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="🔫 STANDOFF 2", callback_data="game_standoff2")
    builder.button(text="◀️ Назад", callback_data="back_main")
    builder.adjust(1)
    return builder.as_markup()

def platforms_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="📱 АПК (Android)", callback_data="platform_apk")
    builder.button(text="🍏 iOS", callback_data="platform_ios")
    builder.button(text="◀️ Назад к играм", callback_data="back_games")
    builder.adjust(1)
    return builder.as_markup()

def durations_kb(platform: str):
    builder = InlineKeyboardBuilder()
    if platform == "apk":
        builder.button(text="🗓 НЕДЕЛЯ | 150 ₽", callback_data="duration_week_150")
        builder.button(text="📅 МЕСЯЦ | 350 ₽", callback_data="duration_month_350")
        builder.button(text="♾ НАВСЕГДА | 700 ₽", callback_data="duration_forever_700")
    else:
        builder.button(text="🗓 НЕДЕЛЯ | 300 ₽", callback_data="duration_week_300")
        builder.button(text="📅 МЕСЯЦ | 450 ₽", callback_data="duration_month_450")
        builder.button(text="♾ НАВСЕГДА | 850 ₽", callback_data="duration_forever_850")
    builder.button(text="◀️ Назад", callback_data="back_platforms")
    builder.adjust(1)
    return builder.as_markup()

def payment_methods_kb(product_name: str, price: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Карта (ЮMoney)", callback_data=f"pay_yoomoney_{product_name}_{price}")
    builder.button(text="⭐ Telegram Stars", callback_data=f"pay_stars_{product_name}_{price}")
    builder.button(text="💰 CryptoBot", callback_data=f"pay_crypto_{product_name}_{price}")
    builder.button(text="🪙 GOLD (Standoff 2)", callback_data=f"pay_gold_{product_name}_{price}")
    builder.button(text="🎨 NFT", callback_data=f"pay_nft_{product_name}_{price}")
    builder.button(text="◀️ Назад", callback_data="back_durations")
    builder.adjust(1)
    return builder.as_markup()

def get_yoomoney_button_html(bill_number: str, amount: int, purpose: str):
    """Генерирует HTML-код кнопки ЮMoney"""
    import urllib.parse
    encoded_purpose = urllib.parse.quote(purpose)
    
    return f'''<a href="https://yoomoney.ru/quickpay/confirm.xml?receiver={YOOMONEY_RECEIVER}&quickpay-form=shop&targets={encoded_purpose}&sum={amount}&paymentType=PC&label={bill_number}" target="_blank">
        <img src="https://yoomoney.ru/i/shop/buttons/quickpay_button.png" width="330" height="50" alt="Оплатить через ЮMoney">
    </a>'''

# ==================== ЮMONEY ОПЛАТА (С ЧЕКОМ) ====================
def generate_bill_number(order_id: int, user_id: int) -> str:
    """Генерация уникального номера счета"""
    return f"AIMNOOB_{order_id}_{user_id}_{secrets.token_hex(4)}"

@dp.callback_query(F.data.startswith("pay_yoomoney_"))
async def pay_yoomoney(callback: CallbackQuery, state: FSMContext):
    """Обработка оплаты через ЮMoney"""
    try:
        _, _, product_name, price = callback.data.split("_", 3)
        price = int(price)
        
        user_id = callback.from_user.id
        username = callback.from_user.username or "нет"
        
        # Создаем заказ
        order_id = add_order(user_id, username, product_name, price, "yoomoney", status="pending")
        
        # Генерируем billNumber
        bill_number = generate_bill_number(order_id, user_id)
        
        await state.update_data(product=product_name, price=price, order_id=order_id)
        
        # Описание платежа
        purpose = f"Оплата {product_name} (заказ #{order_id})"
        
        # Генерируем кнопку
        button_html = get_yoomoney_button_html(bill_number, price, purpose)
        
        await callback.message.edit_text(
            f"💳 <b>Оплата картой (ЮMoney)</b>\n\n"
            f"📦 Товар: {product_name}\n"
            f"💰 Сумма: {price} ₽\n"
            f"🆔 Номер заказа: #{order_id}\n\n"
            f"👇 <b>Нажмите на кнопку ниже для оплаты:</b>\n\n"
            f"{button_html}\n\n"
            f"✅ <b>После оплаты нажмите кнопку «Я оплатил» и пришлите чек</b>\n\n"
            f"<i>Если у вас есть вопросы: @aimnoob_support</i>",
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        
        # Кнопка "Я оплатил"
        await callback.message.answer(
            "📸 <b>После оплаты нажмите кнопку и отправьте чек</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"send_receipt_{order_id}")]
            ]),
            parse_mode="HTML"
        )
        
        await callback.answer()
        logger.info(f"Создан заказ #{order_id} для ЮMoney")
        
    except Exception as e:
        logger.error(f"Ошибка при создании оплаты ЮMoney: {e}")
        await callback.message.edit_text(
            "❌ Произошла ошибка. Пожалуйста, попробуйте позже.",
            parse_mode="HTML"
        )

# ==================== TELEGRAM STARS (АВТО) ====================
@dp.callback_query(F.data.startswith("pay_stars_"))
async def pay_stars(callback: CallbackQuery, state: FSMContext):
    """Оплата Telegram Stars (автоматическая)"""
    try:
        _, _, product_name, price = callback.data.split("_", 3)
        price = int(price)
        stars_price = max(1, price // 10)
        
        order_id = add_order(callback.from_user.id, callback.from_user.username or "нет", 
                            product_name, price, "stars")
        
        await state.update_data(product=product_name, price=price, order_id=order_id)
        
        prices = [LabeledPrice(label=product_name, amount=stars_price)]
        
        await bot.send_invoice(
            chat_id=callback.from_user.id,
            title=f"Оплата {product_name}",
            description=f"Товар: {product_name}\nСумма: {price} ₽",
            payload=f"stars_{order_id}_{callback.from_user.id}",
            provider_token="",
            currency="XTR",
            prices=prices,
            start_parameter="aimnoob_payment"
        )
        
        await callback.answer()
        logger.info(f"Создан инвойс Stars для заказа #{order_id}")
        
    except Exception as e:
        logger.error(f"Ошибка Stars: {e}")
        await callback.message.edit_text("❌ Ошибка при создании платежа", parse_mode="HTML")

@dp.pre_checkout_query()
async def pre_checkout_query_handler(query: PreCheckoutQuery):
    await query.answer(ok=True)

@dp.message(F.successful_payment)
async def successful_payment(message: Message):
    """Успешная оплата Stars"""
    try:
        payload = message.successful_payment.invoice_payload
        parts = payload.split("_")
        
        if len(parts) >= 2 and parts[0] == "stars":
            order_id = int(parts[1])
            update_order_status(order_id, "paid", datetime.now())
            
            conn = sqlite3.connect(DB_NAME)
            cur = conn.cursor()
            cur.execute("SELECT product, price FROM orders WHERE id = ?", (order_id,))
            order = cur.fetchone()
            conn.close()
            
            if order:
                product_name, price = order
                await message.answer(
                    f"✅ <b>Оплата прошла успешно!</b>\n\n"
                    f"📦 Товар: {product_name}\n"
                    f"💰 Сумма: {price} ₽\n"
                    f"🆔 Заказ: #{order_id}\n"
                    f"⭐ Способ: Telegram Stars\n\n"
                    f"🔑 <b>Ваш чит:</b>\n"
                    f"<code>https://example.com/cheat/{order_id}</code>\n\n"
                    f"📖 <b>Инструкция по установке:</b>\n"
                    f"1. Скачайте файл по ссылке\n"
                    f"2. Установите APK\n"
                    f"3. Запустите и наслаждайтесь игрой!\n\n"
                    f"Приятной игры! 🎮",
                    parse_mode="HTML"
                )
                
                # Уведомляем админа
                await bot.send_message(
                    ADMIN_ID,
                    f"⭐ <b>ОПЛАТА STARS</b>\n\n"
                    f"👤 {message.from_user.username or message.from_user.id}\n"
                    f"📦 {product_name}\n"
                    f"💰 {price} ₽\n"
                    f"🆔 #{order_id}",
                    parse_mode="HTML"
                )
                logger.info(f"Заказ #{order_id} оплачен через Stars")
                
    except Exception as e:
        logger.error(f"Ошибка при обработке оплаты Stars: {e}")

# ==================== CRYPTOBOT (С ЧЕКОМ) ====================
@dp.callback_query(F.data.startswith("pay_crypto_"))
async def pay_crypto(callback: CallbackQuery, state: FSMContext):
    """Оплата через CryptoBot"""
    try:
        _, _, product_name, price = callback.data.split("_", 3)
        price = int(price)
        usdt_price = round(price / 80, 2)
        
        order_id = add_order(callback.from_user.id, callback.from_user.username or "нет", 
                            product_name, price, "crypto")
        
        await state.update_data(product=product_name, price=price, order_id=order_id)
        
        await callback.message.edit_text(
            f"💰 <b>Оплата криптовалютой (USDT)</b>\n\n"
            f"📦 Товар: {product_name}\n"
            f"💰 Сумма: {price} ₽ (~{usdt_price} USDT)\n"
            f"🆔 Заказ: #{order_id}\n\n"
            f"💳 <b>Реквизиты для оплаты:</b>\n"
            f"<code>USDT (TRC20): TXXXXXXXXXXXXXXXXXXXXXXXXXXXXX</code>\n\n"
            f"✅ <b>После оплаты нажмите кнопку и пришлите чек</b>\n\n"
            f"<i>Связь с поддержкой: @aimnoob_support</i>",
            parse_mode="HTML"
        )
        
        await callback.message.answer(
            "📸 <b>После оплаты нажмите кнопку и отправьте чек</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"send_receipt_{order_id}")]
            ]),
            parse_mode="HTML"
        )
        
        await callback.answer()
        logger.info(f"Создан заказ #{order_id} для CryptoBot")
        
    except Exception as e:
        logger.error(f"Ошибка CryptoBot: {e}")
        await callback.message.edit_text("❌ Ошибка при создании платежа", parse_mode="HTML")

# ==================== GOLD ОПЛАТА ====================
@dp.callback_query(F.data.startswith("pay_gold_"))
async def pay_gold(callback: CallbackQuery, state: FSMContext):
    """Оплата GOLD (ручная)"""
    try:
        _, _, product_name, price = callback.data.split("_", 3)
        price = int(price)
        gold_price = 350
        
        order_id = add_order(callback.from_user.id, callback.from_user.username or "нет", 
                            product_name, price, "gold")
        
        # Отправляем уведомление админу в чат
        await bot.send_message(
            ADMIN_ID,
            f"🪙 <b>НОВЫЙ ЗАКАЗ (GOLD)</b>\n\n"
            f"👤 Пользователь: @{callback.from_user.username or callback.from_user.id}\n"
            f"🆔 ID: {callback.from_user.id}\n"
            f"📦 Товар: {product_name}\n"
            f"💰 Сумма: {price} ₽ ({gold_price} голды)\n"
            f"🆔 Заказ: #{order_id}\n\n"
            f"📝 <b>Сообщение от пользователя:</b>\n"
            f"Привет! Хочу купить чит на Standoff 2 🔑 Версия 0.37.1, "
            f"подписка — готов купить за {gold_price} голды прямо сейчас 💰\n\n"
            f"💬 Связь с пользователем: @{callback.from_user.username or callback.from_user.id}\n"
            f"📌 Для подтверждения заказа отправьте команду:\n"
            f"<code>/approve {order_id}</code>",
            parse_mode="HTML"
        )
        
        await callback.message.edit_text(
            f"🪙 <b>Оплата GOLD (Standoff 2)</b>\n\n"
            f"📦 Товар: {product_name}\n"
            f"💰 Сумма: {gold_price} голды\n"
            f"🆔 Заказ: #{order_id}\n"
            f"👤 Контакт для оплаты: @aimnoob_support\n\n"
            f"⏳ <b>Ваш заказ отправлен администратору!</b>\n"
            f"Ожидайте подтверждения.",
            parse_mode="HTML"
        )
        
        await state.clear()
        await callback.answer()
        logger.info(f"Создан заказ GOLD #{order_id}")
        
    except Exception as e:
        logger.error(f"Ошибка GOLD: {e}")

# ==================== NFT ОПЛАТА ====================
@dp.callback_query(F.data.startswith("pay_nft_"))
async def pay_nft(callback: CallbackQuery, state: FSMContext):
    """Оплата NFT (ручная)"""
    try:
        _, _, product_name, price = callback.data.split("_", 3)
        price = int(price)
        nft_price = 250
        
        order_id = add_order(callback.from_user.id, callback.from_user.username or "нет", 
                            product_name, price, "nft")
        
        # Отправляем уведомление админу
        await bot.send_message(
            ADMIN_ID,
            f"🎨 <b>НОВЫЙ ЗАКАЗ (NFT)</b>\n\n"
            f"👤 Пользователь: @{callback.from_user.username or callback.from_user.id}\n"
            f"📦 Товар: {product_name}\n"
            f"💰 Сумма: {price} ₽ ({nft_price} NFT)\n"
            f"🆔 Заказ: #{order_id}\n\n"
            f"💬 Связь: @{callback.from_user.username or callback.from_user.id}\n"
            f"📌 Для подтверждения заказа отправьте команду:\n"
            f"<code>/approve {order_id}</code>",
            parse_mode="HTML"
        )
        
        await callback.message.edit_text(
            f"🎨 <b>Оплата NFT</b>\n\n"
            f"📦 Товар: {product_name}\n"
            f"💰 Сумма: {nft_price} NFT\n"
            f"🆔 Заказ: #{order_id}\n"
            f"👤 Контакт: @aimnoob_support\n\n"
            f"⏳ Заказ отправлен администратору!",
            parse_mode="HTML"
        )
        
        await state.clear()
        await callback.answer()
        logger.info(f"Создан заказ NFT #{order_id}")
        
    except Exception as e:
        logger.error(f"Ошибка NFT: {e}")

# ==================== ПРИЕМ ЧЕКОВ ====================
@dp.callback_query(F.data.startswith("send_receipt_"))
async def send_receipt(callback: CallbackQuery, state: FSMContext):
    """Запрос на отправку чека"""
    order_id = int(callback.data.split("_")[2])
    await state.update_data(receipt_order_id=order_id)
    await state.set_state(OrderState.waiting_for_receipt)
    
    await callback.message.answer(
        f"📸 <b>Отправьте скриншот или фото чека об оплате</b>\n\n"
        f"🆔 Заказ: #{order_id}\n\n"
        f"Отправьте изображение в ответ на это сообщение:",
        parse_mode="HTML"
    )
    await callback.answer()

@dp.message(OrderState.waiting_for_receipt, F.photo)
async def handle_receipt(message: Message, state: FSMContext):
    """Обработка чека"""
    data = await state.get_data()
    order_id = data.get("receipt_order_id")
    
    if not order_id:
        await message.answer("❌ Заказ не найден. Попробуйте снова.", parse_mode="HTML")
        await state.clear()
        return
    
    # Получаем заказ
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT user_id, product, price, currency FROM orders WHERE id = ?", (order_id,))
    order = cur.fetchone()
    conn.close()
    
    if not order:
        await message.answer("❌ Заказ не найден.", parse_mode="HTML")
        await state.clear()
        return
    
    user_id, product_name, price, currency = order
    
    # Отправляем чек админу
    photo = message.photo[-1]
    caption = (
        f"🧾 <b>НОВЫЙ ЧЕК ДЛЯ ПРОВЕРКИ</b>\n\n"
        f"👤 Пользователь: @{message.from_user.username or message.from_user.id}\n"
        f"🆔 ID: {message.from_user.id}\n"
        f"📦 Товар: {product_name}\n"
        f"💰 Сумма: {price} ₽\n"
        f"💳 Способ: {currency.upper()}\n"
        f"🆔 Заказ: #{order_id}\n\n"
        f"✅ <b>Для подтверждения отправьте:</b>\n"
        f"<code>/approve {order_id}</code>\n\n"
        f"❌ <b>Для отклонения отправьте:</b>\n"
        f"<code>/reject {order_id}</code>"
    )
    
    await bot.send_photo(ADMIN_ID, photo.file_id, caption=caption, parse_mode="HTML")
    
    await message.answer(
        f"✅ <b>Чек отправлен на проверку!</b>\n\n"
        f"🆔 Заказ: #{order_id}\n"
        f"Ожидайте подтверждения администратора.",
        parse_mode="HTML"
    )
    
    await state.clear()

@dp.message(OrderState.waiting_for_receipt)
async def handle_no_photo(message: Message):
    await message.answer("❌ Пожалуйста, отправьте фото чека.")

# ==================== АДМИН-КОМАНДЫ ====================
@dp.message(F.from_user.id == ADMIN_ID, F.text.startswith("/approve"))
async def approve_order(message: Message):
    """Подтверждение заказа админом"""
    try:
        order_id = int(message.text.split()[1])
        update_order_status(order_id, "paid", datetime.now())
        
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("SELECT user_id, product, price, currency FROM orders WHERE id = ?", (order_id,))
        result = cur.fetchone()
        conn.close()
        
        if result:
            user_id, product_name, price, currency = result
            
            await bot.send_message(
                user_id,
                f"✅ <b>Ваш заказ #{order_id} подтвержден!</b>\n\n"
                f"📦 Товар: {product_name}\n"
                f"💰 Сумма: {price} ₽\n"
                f"💳 Способ: {currency.upper()}\n\n"
                f"🔑 <b>Ваш чит:</b>\n"
                f"<code>Ссылка на скачивание: https://example.com/cheat/{order_id}</code>\n\n"
                f"📖 <b>Инструкция по установке:</b>\n"
                f"1. Скачайте файл по ссылке\n"
                f"2. Установите APK\n"
                f"3. Запустите и наслаждайтесь игрой!\n\n"
                f"Приятной игры! 🎮",
                parse_mode="HTML"
            )
            
            await message.reply(f"✅ Заказ #{order_id} подтвержден, чит отправлен пользователю.")
            logger.info(f"Админ подтвердил заказ #{order_id}")
        else:
            await message.reply(f"❌ Заказ #{order_id} не найден.")
            
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")

@dp.message(F.from_user.id == ADMIN_ID, F.text.startswith("/reject"))
async def reject_order(message: Message):
    """Отклонение заказа админом"""
    try:
        order_id = int(message.text.split()[1])
        update_order_status(order_id, "rejected", datetime.now())
        
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM orders WHERE id = ?", (order_id,))
        result = cur.fetchone()
        conn.close()
        
        if result:
            user_id = result[0]
            await bot.send_message(
                user_id,
                f"❌ <b>Ваш заказ #{order_id} отклонен!</b>\n\n"
                f"Пожалуйста, свяжитесь с поддержкой: @aimnoob_support",
                parse_mode="HTML"
            )
            
            await message.reply(f"❌ Заказ #{order_id} отклонен.")
            logger.info(f"Админ отклонил заказ #{order_id}")
        else:
            await message.reply(f"❌ Заказ #{order_id} не найден.")
            
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")

@dp.message(F.from_user.id == ADMIN_ID, F.text == "/stats")
async def stats(message: Message):
    """Статистика"""
    total, paid, revenue = get_all_orders_count()
    await message.reply(
        f"📊 <b>СТАТИСТИКА МАГАЗИНА</b>\n\n"
        f"📦 Всего заказов: {total}\n"
        f"✅ Оплаченных: {paid}\n"
        f"💰 Выручка: {revenue} ₽\n"
        f"📈 Конверсия: {paid/total*100:.1f}%" if total > 0 else "📈 Конверсия: 0%",
        parse_mode="HTML"
    )

# ==================== ОСНОВНЫЕ ХЕНДЛЕРЫ ====================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO users (user_id, username, created_at) VALUES (?, ?, ?)",
               (message.from_user.id, message.from_user.username or "нет", datetime.now()))
    conn.commit()
    conn.close()
    
    await message.answer(
        "✨ <b>Добро пожаловать в AIMNOOB SHOP!</b> ✨\n\n"
        "Здесь вы можете:\n"
        "✅ Купить читы для Standoff 2\n"
        "✅ Отслеживать свой профиль\n"
        "✅ Приглашать друзей и копить баллы\n\n"
        "💎 <b>Наши плюсы:</b>\n"
        "🔹 Доступные цены\n"
        "🔹 Быстрая поддержка\n"
        "🔹 Простота использования\n\n"
        "👇 <b>Выберите действие:</b>",
        reply_markup=main_menu_kb(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "buy_cheat")
async def buy_cheat(callback: CallbackQuery):
    await callback.message.edit_text(
        "🎮 <b>Выберите игру:</b>\n\nУ нас есть читы для самых популярных игр:",
        reply_markup=games_kb(),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "game_standoff2")
async def game_standoff2(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "📱 <b>ВЫБЕРИТЕ ПЛАТФОРМУ:</b>",
        reply_markup=platforms_kb(),
        parse_mode="HTML"
    )
    await state.set_state(OrderState.choosing_platform)
    await callback.answer()

@dp.callback_query(F.data.startswith("platform_"))
async def platform_chosen(callback: CallbackQuery, state: FSMContext):
    platform = callback.data.split("_")[1]
    await state.update_data(platform=platform)
    await callback.message.edit_text(
        "⏳ <b>ВЫБЕРИТЕ СРОК ДЕЙСТВИЯ ПОДПИСКИ:</b>",
        reply_markup=durations_kb(platform),
        parse_mode="HTML"
    )
    await state.set_state(OrderState.choosing_duration)
    await callback.answer()

@dp.callback_query(F.data.startswith("duration_"))
async def duration_chosen(callback: CallbackQuery, state: FSMContext):
    data = callback.data.split("_")
    duration = data[1]
    price = int(data[2])
    
    duration_text = {"week": "НЕДЕЛЯ", "month": "МЕСЯЦ", "forever": "НАВСЕГДА"}.get(duration, "НЕДЕЛЯ")
    product_name = f"Standoff 2 0.37.1 | {duration_text}"
    await state.update_data(product=product_name, price=price)
    
    await callback.message.edit_text(
        f"🛍 <b>Ваш заказ:</b>\n\n"
        f"📦 Товар: {product_name}\n"
        f"💰 Цена: {price} ₽\n\n"
        f"💳 <b>ВЫБЕРИТЕ СПОСОБ ОПЛАТЫ:</b>",
        reply_markup=payment_methods_kb(product_name, price),
        parse_mode="HTML"
    )
    await state.set_state(OrderState.choosing_payment)
    await callback.answer()

@dp.callback_query(F.data == "profile")
async def show_profile(callback: CallbackQuery):
    orders_count = get_user_orders_count(callback.from_user.id)
    await callback.message.answer(
        f"👤 <b>Ваш профиль</b>\n\n"
        f"🆔 ID: {callback.from_user.id}\n"
        f"👥 Username: @{callback.from_user.username or 'нет'}\n"
        f"📦 Покупок: {orders_count}\n"
        f"⭐ Баланс: 0 баллов\n\n"
        f"Приглашайте друзей и получайте бонусы!",
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "promo")
async def promo(callback: CallbackQuery):
    await callback.message.answer(
        "🎁 <b>Акции и промокоды</b>\n\n"
        "🔥 <b>Действующие акции:</b>\n"
        "• При покупке подписки на месяц - скидка 10%\n"
        "• Пригласи друга - получи 50 баллов\n"
        "• За отзыв в группе - 1 неделя чиста бесплатно\n\n"
        "🔑 <b>Промокоды:</b>\n"
        "• AIMNOOB10 - скидка 10% на первый заказ\n"
        "• STANDOFF25 - скидка 25% на подписку навсегда\n\n"
        "<i>Промокоды вводятся при оплате через ЮMoney в комментарии</i>",
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "invite")
async def invite(callback: CallbackQuery):
    bot_username = (await bot.get_me()).username
    await callback.message.answer(
        f"👥 <b>Пригласи друга и получи бонус!</b>\n\n"
        f"Поделитесь ссылкой с другом:\n"
        f"<code>https://t.me/{bot_username}?start=ref_{callback.from_user.id}</code>\n\n"
        f"🎁 <b>Бонусы:</b>\n"
        f"• За каждого приглашенного друга - 50 баллов\n"
        f"• 100 баллов = 50 ₽ скидка\n"
        f"• Друг получит 25 баллов за регистрацию\n\n"
        f"<i>Скопируйте ссылку и отправьте другу!</i>",
        parse_mode="HTML"
    )
    await callback.answer()

# ==================== НАВИГАЦИЯ ====================
@dp.callback_query(F.data == "back_main")
async def back_main(callback: CallbackQuery):
    await cmd_start(callback.message)
    await callback.answer()

@dp.callback_query(F.data == "back_games")
async def back_games(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "🎮 <b>Выберите игру:</b>",
        reply_markup=games_kb(),
        parse_mode="HTML"
    )
    await state.set_state(None)
    await callback.answer()

@dp.callback_query(F.data == "back_platforms")
async def back_platforms(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "📱 <b>ВЫБЕРИТЕ ПЛАТФОРМУ:</b>",
        reply_markup=platforms_kb(),
        parse_mode="HTML"
    )
    await state.set_state(OrderState.choosing_platform)
    await callback.answer()

@dp.callback_query(F.data == "back_durations")
async def back_durations(callback: CallbackQuery, state: FSMContext):
    user_data = await state.get_data()
    platform = user_data.get("platform", "apk")
    await callback.message.edit_text(
        "⏳ <b>ВЫБЕРИТЕ СРОК ДЕЙСТВИЯ ПОДПИСКИ:</b>",
        reply_markup=durations_kb(platform),
        parse_mode="HTML"
    )
    await state.set_state(OrderState.choosing_duration)
    await callback.answer()

@dp.callback_query(F.data == "back_payment")
async def back_payment(callback: CallbackQuery, state: FSMContext):
    user_data = await state.get_data()
    product = user_data.get("product", "Standoff 2 0.37.1 | НЕДЕЛЯ")
    price = user_data.get("price", 150)
    await callback.message.edit_text(
        f"🛍 <b>Ваш заказ:</b>\n\n"
        f"📦 Товар: {product}\n"
        f"💰 Цена: {price} ₽\n\n"
        f"💳 <b>ВЫБЕРИТЕ СПОСОБ ОПЛАТЫ:</b>",
        reply_markup=payment_methods_kb(product, price),
        parse_mode="HTML"
    )
    await state.set_state(OrderState.choosing_payment)
    await callback.answer()

# ==================== ЗАПУСК ====================
async def main():
    init_db()
    logger.info("Бот запускается...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
