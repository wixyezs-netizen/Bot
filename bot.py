import asyncio
import logging
import sqlite3
import secrets
import json
import aiohttp
from datetime import datetime
from typing import Optional

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

# ========== CryptoBot настройки ==========
CRYPTO_BOT_TOKEN = "493276:AAtS7R1zYy0gaPw8eax1EgiWo0tdnd6dQ9c"
CRYPTO_API_URL = "https://pay.crypt.bot/api"

# ========== Карта для оплаты ==========
CARD_NUMBER = "2200 7021 3256 9927"
CARD_HOLDER = "AIMNOOB SHOP"

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
            payment_id TEXT,
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

def add_order(user_id, username, product, price, currency, payment_id=None, status="pending"):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO orders (user_id, username, product, price, currency, payment_id, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, username, product, price, currency, payment_id, status, datetime.now())
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
    builder.button(text="💳 Карта", callback_data=f"pay_card_{product_name}_{price}")
    builder.button(text="⭐ Telegram Stars", callback_data=f"pay_stars_{product_name}_{price}")
    builder.button(text="💰 CryptoBot", callback_data=f"pay_crypto_{product_name}_{price}")
    builder.button(text="🪙 GOLD", callback_data=f"pay_gold_{product_name}_{price}")
    builder.button(text="🎨 NFT", callback_data=f"pay_nft_{product_name}_{price}")
    builder.button(text="◀️ Назад", callback_data="back_durations")
    builder.adjust(1)
    return builder.as_markup()

# ==================== ОПЛАТА КАРТОЙ (С ЧЕКОМ) ====================
@dp.callback_query(F.data.startswith("pay_card_"))
async def pay_card(callback: CallbackQuery, state: FSMContext):
    """Оплата картой с реквизитами"""
    try:
        _, _, product_name, price = callback.data.split("_", 3)
        price = int(price)
        
        user_id = callback.from_user.id
        username = callback.from_user.username or "нет"
        
        # Создаем заказ
        order_id = add_order(user_id, username, product_name, price, "card", status="pending")
        
        await state.update_data(product=product_name, price=price, order_id=order_id)
        
        await callback.message.edit_text(
            f"💳 <b>Оплата картой</b>\n\n"
            f"📦 Товар: {product_name}\n"
            f"💰 Сумма: {price} ₽\n"
            f"🆔 Номер заказа: #{order_id}\n\n"
            f"<b>💳 Реквизиты для оплаты:</b>\n"
            f"<code>{CARD_NUMBER}</code>\n\n"
            f"<b>Получатель:</b> {CARD_HOLDER}\n\n"
            f"✅ <b>После оплаты:</b>\n"
            f"1. Сохраните чек\n"
            f"2. Нажмите кнопку «Я оплатил»\n"
            f"3. Отправьте скриншот чека\n\n"
            f"<i>Если у вас есть вопросы: @aimnoob_support</i>",
            parse_mode="HTML"
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
        logger.info(f"Создан заказ #{order_id} для оплаты картой")
        
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await callback.message.edit_text("❌ Ошибка при создании заказа", parse_mode="HTML")

# ==================== CRYPTOBOT (АВТОМАТИЧЕСКАЯ ОПЛАТА) ====================
async def create_crypto_invoice(amount_usdt: float, order_id: int, product_name: str) -> Optional[str]:
    """Создание инвойса в CryptoBot"""
    try:
        headers = {
            "Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN,
            "Content-Type": "application/json"
        }
        
        data = {
            "asset": "USDT",
            "amount": str(amount_usdt),
            "description": f"Оплата {product_name} (заказ #{order_id})",
            "payload": f"crypto_{order_id}",
            "expires_in": 3600  # 1 час
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{CRYPTO_API_URL}/createInvoice", headers=headers, json=data) as response:
                if response.status == 200:
                    result = await response.json()
                    if result.get("ok"):
                        invoice = result.get("result")
                        return invoice.get("pay_url")
                else:
                    logger.error(f"CryptoBot API error: {await response.text()}")
        return None
    except Exception as e:
        logger.error(f"CryptoBot error: {e}")
        return None

async def check_crypto_payment(invoice_id: str) -> bool:
    """Проверка статуса оплаты в CryptoBot"""
    try:
        headers = {
            "Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{CRYPTO_API_URL}/getInvoices", headers=headers, params={"invoice_ids": invoice_id}) as response:
                if response.status == 200:
                    result = await response.json()
                    if result.get("ok"):
                        invoices = result.get("result", {}).get("items", [])
                        for invoice in invoices:
                            if invoice.get("status") == "paid":
                                return True
        return False
    except Exception as e:
        logger.error(f"Check payment error: {e}")
        return False

@dp.callback_query(F.data.startswith("pay_crypto_"))
async def pay_crypto(callback: CallbackQuery, state: FSMContext):
    """Оплата через CryptoBot (автоматическая)"""
    try:
        _, _, product_name, price = callback.data.split("_", 3)
        price = int(price)
        usdt_price = round(price / 80, 2)  # 1 USDT ≈ 80 ₽
        
        user_id = callback.from_user.id
        username = callback.from_user.username or "нет"
        
        # Создаем заказ
        order_id = add_order(user_id, username, product_name, price, "crypto", status="pending")
        
        await state.update_data(product=product_name, price=price, order_id=order_id)
        
        # Создаем инвойс в CryptoBot
        pay_url = await create_crypto_invoice(usdt_price, order_id, product_name)
        
        if pay_url:
            await callback.message.edit_text(
                f"💰 <b>Оплата криптовалютой (USDT)</b>\n\n"
                f"📦 Товар: {product_name}\n"
                f"💰 Сумма: {price} ₽ (~{usdt_price} USDT)\n"
                f"🆔 Заказ: #{order_id}\n\n"
                f"🔗 <a href='{pay_url}'>Нажмите для оплаты через CryptoBot</a>\n\n"
                f"✅ <b>После оплаты чит придет автоматически!</b>\n"
                f"⏱ Обычно это занимает 1-2 минуты.\n\n"
                f"<i>Если оплата не прошла автоматически, свяжитесь с поддержкой: @aimnoob_support</i>",
                parse_mode="HTML",
                disable_web_page_preview=True
            )
            
            # Кнопка проверки статуса
            await callback.message.answer(
                "🔄 <b>Проверить статус оплаты</b>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"check_crypto_{order_id}")]
                ]),
                parse_mode="HTML"
            )
        else:
            await callback.message.edit_text(
                f"❌ <b>Ошибка при создании платежа</b>\n\n"
                f"Пожалуйста, попробуйте позже или выберите другой способ оплаты.\n\n"
                f"Связь с поддержкой: @aimnoob_support",
                parse_mode="HTML"
            )
        
        await callback.answer()
        logger.info(f"Создан крипто-заказ #{order_id}")
        
    except Exception as e:
        logger.error(f"Ошибка CryptoBot: {e}")
        await callback.message.edit_text("❌ Ошибка при создании платежа", parse_mode="HTML")

@dp.callback_query(F.data.startswith("check_crypto_"))
async def check_crypto_payment_status(callback: CallbackQuery):
    """Проверка статуса крипто-оплаты"""
    try:
        order_id = int(callback.data.split("_")[2])
        
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("SELECT status, product, price FROM orders WHERE id = ?", (order_id,))
        order = cur.fetchone()
        conn.close()
        
        if order:
            status, product_name, price = order
            if status == "paid":
                await callback.message.answer(
                    f"✅ <b>Оплата подтверждена!</b>\n\n"
                    f"📦 Товар: {product_name}\n"
                    f"💰 Сумма: {price} ₽\n"
                    f"🆔 Заказ: #{order_id}\n\n"
                    f"🔑 <b>Ваш чит:</b>\n"
                    f"<code>https://example.com/cheat/{order_id}</code>\n\n"
                    f"📖 <b>Инструкция:</b>\n"
                    f"1. Скачайте файл по ссылке\n"
                    f"2. Установите APK\n"
                    f"3. Запустите и наслаждайтесь игрой!\n\n"
                    f"Приятной игры! 🎮",
                    parse_mode="HTML"
                )
            elif status == "pending":
                await callback.message.answer(
                    f"⏳ <b>Заказ #{order_id} ожидает оплаты</b>\n\n"
                    f"Пожалуйста, оплатите счет. После оплаты чит придет автоматически.",
                    parse_mode="HTML"
                )
            else:
                await callback.message.answer(
                    f"❌ Заказ #{order_id} не найден или отменен.",
                    parse_mode="HTML"
                )
        
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка проверки: {e}")

# ==================== GOLD ОПЛАТА (ПЕРЕКИДЫВАЕТ В ЧАТ С АДМИНОМ) ====================
@dp.callback_query(F.data.startswith("pay_gold_"))
async def pay_gold(callback: CallbackQuery, state: FSMContext):
    """Оплата GOLD - перекидывает в чат с админом"""
    try:
        _, _, product_name, price = callback.data.split("_", 3)
        price = int(price)
        gold_price = 350
        
        # Создаем заказ
        order_id = add_order(callback.from_user.id, callback.from_user.username or "нет", 
                            product_name, price, "gold", status="pending")
        
        # Текст для отправки админу
        message_text = (
            f"🪙 <b>НОВЫЙ ЗАКАЗ (GOLD)</b>\n\n"
            f"👤 Пользователь: @{callback.from_user.username or callback.from_user.id}\n"
            f"🆔 ID: {callback.from_user.id}\n"
            f"📦 Товар: {product_name}\n"
            f"💰 Сумма: {price} ₽ ({gold_price} голды)\n"
            f"🆔 Заказ: #{order_id}\n\n"
            f"📝 <b>Сообщение от пользователя:</b>\n"
            f"Привет! Хочу купить чит на Standoff 2 🔑 Версия 0.37.1, "
            f"подписка — готов купить за {gold_price} голды прямо сейчас 💰\n\n"
            f"💬 Для связи с пользователем: @{callback.from_user.username or callback.from_user.id}\n"
            f"📌 Для подтверждения заказа отправьте команду:\n"
            f"<code>/approve {order_id}</code>"
        )
        
        # Создаем клавиатуру для быстрого ответа
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить заказ", callback_data=f"approve_{order_id}")],
            [InlineKeyboardButton(text="❌ Отклонить заказ", callback_data=f"reject_{order_id}")]
        ])
        
        # Отправляем админу
        await bot.send_message(ADMIN_ID, message_text, parse_mode="HTML", reply_markup=kb)
        
        # Создаем ссылку на чат с админом
        admin_username = (await bot.get_chat(ADMIN_ID)).username or "aimnoob_support"
        
        await callback.message.edit_text(
            f"🪙 <b>Оплата GOLD (Standoff 2)</b>\n\n"
            f"📦 Товар: {product_name}\n"
            f"💰 Сумма: {gold_price} голды\n"
            f"🆔 Заказ: #{order_id}\n\n"
            f"👇 <b>Для оплаты свяжитесь с администратором:</b>\n"
            f"<a href='tg://user?id={ADMIN_ID}'>Написать администратору</a>\n\n"
            f"📝 <b>Ваше сообщение будет автоматически содержать:</b>\n"
            f"<i>Привет! Хочу купить чит на Standoff 2 🔑 Версия 0.37.1, "
            f"подписка — готов купить за {gold_price} голды прямо сейчас 💰\n"
            f"Заказ #{order_id}</i>\n\n"
            f"⏳ После оплаты администратор подтвердит заказ и выдаст чит.",
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        
        # Кнопка для открытия чата с админом
        await callback.message.answer(
            "💬 <b>Связаться с администратором</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💬 Написать админу", url=f"tg://user?id={ADMIN_ID}")]
            ]),
            parse_mode="HTML"
        )
        
        await state.clear()
        await callback.answer()
        logger.info(f"Создан заказ GOLD #{order_id}, отправлено уведомление админу")
        
    except Exception as e:
        logger.error(f"Ошибка GOLD: {e}")

# ==================== NFT ОПЛАТА ====================
@dp.callback_query(F.data.startswith("pay_nft_"))
async def pay_nft(callback: CallbackQuery, state: FSMContext):
    """Оплата NFT - перекидывает в чат с админом"""
    try:
        _, _, product_name, price = callback.data.split("_", 3)
        price = int(price)
        nft_price = 250
        
        order_id = add_order(callback.from_user.id, callback.from_user.username or "нет", 
                            product_name, price, "nft", status="pending")
        
        # Отправляем админу
        await bot.send_message(
            ADMIN_ID,
            f"🎨 <b>НОВЫЙ ЗАКАЗ (NFT)</b>\n\n"
            f"👤 Пользователь: @{callback.from_user.username or callback.from_user.id}\n"
            f"📦 Товар: {product_name}\n"
            f"💰 Сумма: {price} ₽ ({nft_price} NFT)\n"
            f"🆔 Заказ: #{order_id}\n\n"
            f"💬 Связь: @{callback.from_user.username or callback.from_user.id}\n"
            f"📌 Для подтверждения: /approve {order_id}",
            parse_mode="HTML"
        )
        
        await callback.message.edit_text(
            f"🎨 <b>Оплата NFT</b>\n\n"
            f"📦 Товар: {product_name}\n"
            f"💰 Сумма: {nft_price} NFT\n"
            f"🆔 Заказ: #{order_id}\n\n"
            f"👇 <b>Для оплаты свяжитесь с администратором:</b>\n"
            f"<a href='tg://user?id={ADMIN_ID}'>Написать администратору</a>\n\n"
            f"⏳ После оплаты администратор подтвердит заказ и выдаст чит.",
            parse_mode="HTML"
        )
        
        await callback.message.answer(
            "💬 <b>Связаться с администратором</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💬 Написать админу", url=f"tg://user?id={ADMIN_ID}")]
            ]),
            parse_mode="HTML"
        )
        
        await state.clear()
        await callback.answer()
        logger.info(f"Создан заказ NFT #{order_id}")
        
    except Exception as e:
        logger.error(f"Ошибка NFT: {e}")

# ==================== TELEGRAM STARS (АВТО) ====================
@dp.callback_query(F.data.startswith("pay_stars_"))
async def pay_stars(callback: CallbackQuery, state: FSMContext):
    """Оплата Telegram Stars (автоматическая)"""
    try:
        _, _, product_name, price = callback.data.split("_", 3)
        price = int(price)
        stars_price = max(1, price // 10)
        
        order_id = add_order(callback.from_user.id, callback.from_user.username or "нет", 
                            product_name, price, "stars", status="pending")
        
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
                
                await bot.send_message(
                    ADMIN_ID,
                    f"⭐ <b>ОПЛАТА STARS</b>\n\n👤 {message.from_user.username}\n📦 {product_name}\n💰 {price} ₽\n🆔 #{order_id}",
                    parse_mode="HTML"
                )
                logger.info(f"Заказ #{order_id} оплачен через Stars")
                
    except Exception as e:
        logger.error(f"Ошибка Stars: {e}")

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
    
    photo = message.photo[-1]
    caption = (
        f"🧾 <b>НОВЫЙ ЧЕК ДЛЯ ПРОВЕРКИ</b>\n\n"
        f"👤 Пользователь: @{message.from_user.username or message.from_user.id}\n"
        f"🆔 ID: {message.from_user.id}\n"
        f"📦 Товар: {product_name}\n"
        f"💰 Сумма: {price} ₽\n"
        f"💳 Способ: {currency.upper()}\n"
        f"🆔 Заказ: #{order_id}\n\n"
        f"✅ <b>Действия:</b>"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"approve_{order_id}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{order_id}")]
    ])
    
    await bot.send_photo(ADMIN_ID, photo.file_id, caption=caption, parse_mode="HTML", reply_markup=kb)
    
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
@dp.callback_query(F.data.startswith("approve_"))
async def approve_order_callback(callback: CallbackQuery):
    """Подтверждение заказа через кнопку"""
    order_id = int(callback.data.split("_")[1])
    await approve_order_logic(order_id, callback.message, callback.from_user.id)

@dp.callback_query(F.data.startswith("reject_"))
async def reject_order_callback(callback: CallbackQuery):
    """Отклонение заказа через кнопку"""
    order_id = int(callback.data.split("_")[1])
    await reject_order_logic(order_id, callback.message, callback.from_user.id)

@dp.message(F.from_user.id == ADMIN_ID, F.text.startswith("/approve"))
async def approve_order_command(message: Message):
    """Подтверждение заказа командой"""
    try:
        order_id = int(message.text.split()[1])
        await approve_order_logic(order_id, message, message.from_user.id)
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")

@dp.message(F.from_user.id == ADMIN_ID, F.text.startswith("/reject"))
async def reject_order_command(message: Message):
    """Отклонение заказа командой"""
    try:
        order_id = int(message.text.split()[1])
        await reject_order_logic(order_id, message, message.from_user.id)
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")

async def approve_order_logic(order_id: int, reply_obj, admin_id: int):
    """Логика подтверждения заказа"""
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
            f"<code>https://example.com/cheat/{order_id}</code>\n\n"
            f"📖 <b>Инструкция по установке:</b>\n"
            f"1. Скачайте файл по ссылке\n"
            f"2. Установите APK\n"
            f"3. Запустите и наслаждайтесь игрой!\n\n"
            f"Приятной игры! 🎮",
            parse_mode="HTML"
        )
        
        if isinstance(reply_obj, types.Message):
            await reply_obj.reply(f"✅ Заказ #{order_id} подтвержден, чит отправлен пользователю.")
        else:
            await reply_obj.edit_caption(
                caption=f"{reply_obj.caption}\n\n✅ <b>ЗАКАЗ ПОДТВЕРЖДЕН</b>",
                parse_mode="HTML"
            )
        
        logger.info(f"Админ подтвердил заказ #{order_id}")
    else:
        await reply_obj.reply(f"❌ Заказ #{order_id} не найден.")

async def reject_order_logic(order_id: int, reply_obj, admin_id: int):
    """Логика отклонения заказа"""
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
        
        if isinstance(reply_obj, types.Message):
            await reply_obj.reply(f"❌ Заказ #{order_id} отклонен.")
        else:
            await reply_obj.edit_caption(
                caption=f"{reply_obj.caption}\n\n❌ <b>ЗАКАЗ ОТКЛОНЕН</b>",
                parse_mode="HTML"
            )
        
        logger.info(f"Админ отклонил заказ #{order_id}")
    else:
        await reply_obj.reply(f"❌ Заказ #{order_id} не найден.")

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
        "<i>Промокоды вводятся при оплате в комментарии</i>",
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
