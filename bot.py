import asyncio
import logging
import sqlite3
import hashlib
import secrets
import json
import aiohttp
from datetime import datetime
from aiohttp import web

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
BOT_TOKEN = "8225924716:AAFZ_8Eu8aJ4BF7pErZY5Ef3emG9Cl9PikE"  # Токен от @BotFather
ADMIN_ID = 8387532956  # Ваш Telegram ID

# ========== ЮMoney OAuth2 настройки ==========
CLIENT_ID = "EBEF10684CDF4F2B8CC0C050D99BAE2AAFCC1F9CE3AC7B3351562BD06AB0B5CD"
CLIENT_SECRET = "97455772986434ADABB07D3792BF768AD12157667CF45107B77AC23ACC7DF414F4DBE2BB557DAD0A136FB8B037D3D8F00A74188CA361B53E367E6E8C5B0980E9"
REDIRECT_URI = "https://ваш-домен.ru/oauth"  # Для ngrok: https://xxxx.ngrok.io/oauth

# Для получения токена (нужно сделать один раз)
# Ссылка для авторизации:
# https://yoomoney.ru/oauth/authorize?client_id=EBEF10684CDF4F2B8CC0C050D99BAE2AAFCC1F9CE3AC7B3351562BD06AB0B5CD&response_type=code&redirect_uri=https://ваш-домен.ru/oauth

# ========== CryptoBot настройки ==========
CRYPTO_BOT_TOKEN = "493276:AAtS7R1zYy0gaPw8eax1EgiWo0tdnd6dQ9c"

# ========== Настройки сервера ==========
WEBHOOK_HOST = "https://ваш-домен.ru"  # Для ngrok: https://xxxx.ngrok.io
WEBHOOK_PATH = "/yoomoney-webhook"
OAUTH_PATH = "/oauth"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
WEB_SERVER_PORT = 8080

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

# Глобальная переменная для хранения токена доступа
yoomoney_access_token = None

# ==================== РАБОТА С БАЗОЙ ДАННЫХ ====================
def init_db():
    """Инициализация базы данных"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    
    # Таблица заказов
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            product TEXT,
            price INTEGER,
            currency TEXT,
            payment_id TEXT,
            operation_id TEXT,
            status TEXT,
            created_at TIMESTAMP,
            paid_at TIMESTAMP
        )
    """)
    
    # Таблица пользователей
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance INTEGER DEFAULT 0,
            created_at TIMESTAMP
        )
    """)
    
    # Таблица для хранения токенов
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tokens (
            id INTEGER PRIMARY KEY,
            access_token TEXT,
            refresh_token TEXT,
            expires_at TIMESTAMP
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

def update_order_status(order_id, status, paid_at=None, operation_id=None):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    if paid_at and operation_id:
        cur.execute("UPDATE orders SET status = ?, paid_at = ?, operation_id = ? WHERE id = ?", 
                   (status, paid_at, operation_id, order_id))
    elif paid_at:
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

def save_token(access_token, refresh_token, expires_in):
    """Сохранение OAuth токена в БД"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    expires_at = datetime.now().timestamp() + expires_in
    cur.execute("DELETE FROM tokens")  # Удаляем старый токен
    cur.execute("INSERT INTO tokens (access_token, refresh_token, expires_at) VALUES (?, ?, ?)",
               (access_token, refresh_token, expires_at))
    conn.commit()
    conn.close()
    logger.info("Токен сохранен в БД")

def get_token():
    """Получение токена из БД"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT access_token, refresh_token, expires_at FROM tokens ORDER BY id DESC LIMIT 1")
    result = cur.fetchone()
    conn.close()
    
    if result:
        access_token, refresh_token, expires_at = result
        # Проверяем, не истек ли токен
        if datetime.now().timestamp() < expires_at:
            return access_token
        else:
            # Токен истек, нужно обновить
            return refresh_token_async(refresh_token)
    return None

async def refresh_token_async(refresh_token):
    """Обновление истекшего токена"""
    try:
        async with aiohttp.ClientSession() as session:
            data = {
                'client_id': CLIENT_ID,
                'client_secret': CLIENT_SECRET,
                'grant_type': 'refresh_token',
                'refresh_token': refresh_token
            }
            async with session.post('https://yoomoney.ru/oauth/token', data=data) as response:
                if response.status == 200:
                    result = await response.json()
                    access_token = result.get('access_token')
                    new_refresh_token = result.get('refresh_token')
                    expires_in = result.get('expires_in')
                    save_token(access_token, new_refresh_token, expires_in)
                    return access_token
    except Exception as e:
        logger.error(f"Ошибка обновления токена: {e}")
    return None

# ==================== FSM СОСТОЯНИЯ ====================
class OrderState(StatesGroup):
    choosing_platform = State()
    choosing_duration = State()
    choosing_payment = State()

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
    
    return f'''<a href="https://yoomoney.ru/quickpay/fundraise/button?billNumber={bill_number}&sum={amount}&purpose={encoded_purpose}&" target="_blank">
        <img src="https://yoomoney.ru/i/shop/buttons/quickpay_button.png" width="330" height="50" alt="Оплатить через ЮMoney">
    </a>'''

# ==================== ЮMONEY OAuth2 (АВТООПЛАТА) ====================
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
        
        # Сохраняем billNumber в БД
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("UPDATE orders SET payment_id = ? WHERE id = ?", (bill_number, order_id))
        conn.commit()
        conn.close()
        
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
            f"✅ <b>После успешной оплаты чит придет автоматически!</b>\n"
            f"⏱ Обычно это занимает 1-2 минуты.\n\n"
            f"📝 <b>Важно:</b> Не закрывайте это окно до получения чита.\n\n"
            f"<i>Если оплата не пришла через 10 минут, свяжитесь с поддержкой: @aimnoob_support</i>",
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        
        # Кнопка проверки статуса
        await callback.message.answer(
            "🔄 <b>Проверить статус оплаты</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"check_payment_{order_id}")]
            ]),
            parse_mode="HTML"
        )
        
        await callback.answer()
        logger.info(f"Создана ссылка на оплату для заказа #{order_id}")
        
    except Exception as e:
        logger.error(f"Ошибка при создании оплаты ЮMoney: {e}")
        await callback.message.edit_text(
            "❌ Произошла ошибка при создании платежа. Пожалуйста, попробуйте позже.",
            parse_mode="HTML"
        )

@dp.callback_query(F.data.startswith("check_payment_"))
async def check_payment(callback: CallbackQuery):
    """Проверка статуса оплаты через API ЮMoney"""
    order_id = int(callback.data.split("_")[2])
    
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT status, product, price FROM orders WHERE id = ?", (order_id,))
    result = cur.fetchone()
    conn.close()
    
    if result:
        status, product, price = result
        if status == "paid":
            await callback.message.answer(
                f"✅ <b>Оплата подтверждена!</b>\n\n"
                f"📦 Товар: {product}\n"
                f"💰 Сумма: {price} ₽\n\n"
                f"🔑 <b>Ваш чит:</b>\n"
                f"<code>https://example.com/cheat/{order_id}</code>\n\n"
                f"📖 <b>Инструкция по установке:</b>\n"
                f"1. Скачайте файл по ссылке\n"
                f"2. Установите APK\n"
                f"3. Запустите и наслаждайтесь игрой!\n\n"
                f"Приятной игры! 🎮",
                parse_mode="HTML"
            )
        elif status == "pending":
            await callback.message.answer(
                f"⏳ <b>Заказ #{order_id} еще не оплачен</b>\n\n"
                f"Пожалуйста, оплатите счет и нажмите кнопку снова через 1-2 минуты.",
                parse_mode="HTML"
            )
        else:
            await callback.message.answer(
                f"❌ Заказ #{order_id} отменен или не найден.",
                parse_mode="HTML"
            )
    
    await callback.answer()

# ==================== WEBHOOK ДЛЯ ЮMONEY ====================
async def yoomoney_webhook(request):
    """
    Обработчик уведомлений от ЮMoney о успешной оплате
    """
    try:
        data = await request.post()
        logger.info(f"Получено уведомление от ЮMoney: {dict(data)}")
        
        # Параметры уведомления
        notification_type = data.get('notification_type')
        operation_id = data.get('operation_id')
        amount = data.get('amount')
        currency = data.get('currency')
        datetime_val = data.get('datetime')
        sender = data.get('sender')
        codepro = data.get('codepro')
        label = data.get('label')  # billNumber
        sha1_hash = data.get('sha1_hash')
        
        if not label:
            logger.warning("Отсутствует label в уведомлении")
            return web.Response(status=400)
        
        # Парсим label
        if label and label.startswith("AIMNOOB_"):
            parts = label.split("_")
            if len(parts) >= 3:
                order_id = int(parts[1])
                user_id = int(parts[2])
                
                logger.info(f"Обработка оплаты заказа #{order_id}")
                
                # Проверяем заказ
                conn = sqlite3.connect(DB_NAME)
                cur = conn.cursor()
                cur.execute("SELECT id, product, price, status FROM orders WHERE id = ?", (order_id,))
                order = cur.fetchone()
                
                if order and order[3] == "pending":
                    # Обновляем статус
                    cur.execute("UPDATE orders SET status = ?, paid_at = ?, operation_id = ? WHERE id = ?", 
                               ("paid", datetime.now(), operation_id, order_id))
                    conn.commit()
                    
                    product_name = order[1]
                    price = order[2]
                    
                    logger.info(f"Заказ #{order_id} успешно оплачен через ЮMoney")
                    
                    # Отправляем чит пользователю
                    try:
                        await bot.send_message(
                            user_id,
                            f"✅ <b>Оплата прошла успешно!</b>\n\n"
                            f"📦 Товар: {product_name}\n"
                            f"💰 Сумма: {price} ₽\n"
                            f"🆔 Заказ: #{order_id}\n"
                            f"💳 Способ: ЮMoney\n\n"
                            f"🔑 <b>Ваш чит:</b>\n"
                            f"<code>https://example.com/cheat/{order_id}</code>\n\n"
                            f"📖 <b>Инструкция по установке:</b>\n"
                            f"1. Скачайте файл по ссылке\n"
                            f"2. Установите APK\n"
                            f"3. Запустите и наслаждайтесь игрой!\n\n"
                            f"Приятной игры! 🎮",
                            parse_mode="HTML"
                        )
                        logger.info(f"Чит отправлен пользователю {user_id}")
                    except Exception as e:
                        logger.error(f"Не удалось отправить чит: {e}")
                    
                    # Уведомляем админа
                    try:
                        await bot.send_message(
                            ADMIN_ID,
                            f"💰 <b>УСПЕШНАЯ ОПЛАТА!</b>\n\n"
                            f"👤 Пользователь: {user_id}\n"
                            f"📦 Товар: {product_name}\n"
                            f"💰 Сумма: {price} ₽\n"
                            f"🆔 Заказ: #{order_id}\n"
                            f"💳 Способ: ЮMoney\n"
                            f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}",
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.error(f"Не удалось уведомить админа: {e}")
                    
                conn.close()
        
        return web.Response(status=200)
        
    except Exception as e:
        logger.error(f"Ошибка при обработке webhook: {e}")
        return web.Response(status=500)

# ==================== OAuth2 ХЕНДЛЕР ====================
async def oauth_handler(request):
    """Обработчик OAuth2 callback"""
    try:
        query = request.query
        code = query.get('code')
        
        if not code:
            return web.Response(text="No code provided", status=400)
        
        # Обмениваем код на токен
        async with aiohttp.ClientSession() as session:
            data = {
                'client_id': CLIENT_ID,
                'client_secret': CLIENT_SECRET,
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': REDIRECT_URI
            }
            async with session.post('https://yoomoney.ru/oauth/token', data=data) as response:
                if response.status == 200:
                    result = await response.json()
                    access_token = result.get('access_token')
                    refresh_token = result.get('refresh_token')
                    expires_in = result.get('expires_in')
                    
                    save_token(access_token, refresh_token, expires_in)
                    
                    logger.info("OAuth2 токен успешно получен")
                    return web.Response(text="Токен успешно получен! Можете закрыть это окно.", status=200)
                else:
                    error = await response.text()
                    logger.error(f"Ошибка получения токена: {error}")
                    return web.Response(text=f"Ошибка: {error}", status=400)
                    
    except Exception as e:
        logger.error(f"Ошибка OAuth: {e}")
        return web.Response(text=str(e), status=500)

# ==================== TELEGRAM STARS ====================
@dp.callback_query(F.data.startswith("pay_stars_"))
async def pay_stars(callback: CallbackQuery, state: FSMContext):
    """Оплата Telegram Stars"""
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
                    f"🆔 Заказ: #{order_id}\n\n"
                    f"🔑 <b>Ваш чит:</b>\n"
                    f"<code>https://example.com/cheat/{order_id}</code>\n\n"
                    f"Приятной игры! 🎮",
                    parse_mode="HTML"
                )
                logger.info(f"Заказ #{order_id} оплачен через Stars")
                
    except Exception as e:
        logger.error(f"Ошибка при обработке оплаты Stars: {e}")

# ==================== CRYPTOBOT ====================
@dp.callback_query(F.data.startswith("pay_crypto_"))
async def pay_crypto(callback: CallbackQuery, state: FSMContext):
    """Оплата через CryptoBot"""
    try:
        _, _, product_name, price = callback.data.split("_", 3)
        price = int(price)
        usdt_price = round(price / 80, 2)
        
        order_id = add_order(callback.from_user.id, callback.from_user.username or "нет", 
                            product_name, price, "crypto")
        
        crypto_link = f"https://t.me/CryptoBot?start=pay_{order_id}"
        
        await state.update_data(product=product_name, price=price, order_id=order_id)
        
        await callback.message.edit_text(
            f"💰 <b>Оплата криптовалютой (USDT)</b>\n\n"
            f"📦 Товар: {product_name}\n"
            f"💰 Сумма: {price} ₽ (~{usdt_price} USDT)\n"
            f"🆔 Заказ: #{order_id}\n\n"
            f"🔗 <a href='{crypto_link}'>Нажмите для оплаты через CryptoBot</a>\n\n"
            f"✅ <b>После оплаты пришлите скриншот чека</b>\n\n"
            f"<i>Связь с поддержкой: @aimnoob_support</i>",
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        
        await callback.message.answer(
            "📸 <b>Отправить чек об оплате</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📸 Отправить чек", callback_data=f"send_receipt_{order_id}")]
            ]),
            parse_mode="HTML"
        )
        
        await callback.answer()
        logger.info(f"Создана ссылка CryptoBot для заказа #{order_id}")
        
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
            f"подписка на НЕДЕЛЮ — готов купить за {gold_price} голды прямо сейчас 💰\n\n"
            f"💬 Связь с пользователем: @{callback.from_user.username or callback.from_user.id}",
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
        
        await bot.send_message(
            ADMIN_ID,
            f"🎨 <b>НОВЫЙ ЗАКАЗ (NFT)</b>\n\n"
            f"👤 Пользователь: @{callback.from_user.username or callback.from_user.id}\n"
            f"📦 Товар: {product_name}\n"
            f"💰 Сумма: {price} ₽ ({nft_price} NFT)\n"
            f"🆔 Заказ: #{order_id}\n\n"
            f"💬 Связь: @{callback.from_user.username or callback.from_user.id}",
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
    
    await callback.message.answer(
        f"📸 <b>Отправьте скриншот или фото чека об оплате</b>\n\n"
        f"🆔 Заказ: #{order_id}\n\n"
        f"Отправьте изображение в ответ на это сообщение:",
        parse_mode="HTML"
    )
    await callback.answer()

@dp.message(F.photo)
async def handle_receipt(message: Message, state: FSMContext):
    """Обработка чека"""
    data = await state.get_data()
    order_id = data.get("receipt_order_id")
    
    if not order_id:
        await message.answer("❌ Сначала выберите способ оплаты и создайте заказ.", parse_mode="HTML")
        return
    
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT user_id, product, price, currency FROM orders WHERE id = ?", (order_id,))
    order = cur.fetchone()
    conn.close()
    
    if not order:
        await message.answer("❌ Заказ не найден.")
        await state.clear()
        return
    
    user_id, product_name, price, currency = order
    
    photo = message.photo[-1]
    caption = (
        f"🧾 <b>НОВЫЙ ЧЕК ДЛЯ ПРОВЕРКИ</b>\n\n"
        f"👤 Пользователь: @{message.from_user.username or message.from_user.id}\n"
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

# ==================== АДМИН-КОМАНДЫ ====================
@dp.callback_query(F.data.startswith("approve_"))
async def approve_order(callback: CallbackQuery):
    """Подтверждение заказа админом"""
    order_id = int(callback.data.split("_")[1])
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
            f"Спасибо за покупку! 🎮",
            parse_mode="HTML"
        )
        
        await callback.message.edit_caption(
            caption=f"{callback.message.caption}\n\n✅ <b>ЗАКАЗ ПОДТВЕРЖДЕН</b>",
            parse_mode="HTML"
        )
        
        logger.info(f"Админ подтвердил заказ #{order_id}")
    
    await callback.answer()

@dp.callback_query(F.data.startswith("reject_"))
async def reject_order(callback: CallbackQuery):
    """Отклонение заказа админом"""
    order_id = int(callback.data.split("_")[1])
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
        
        await callback.message.edit_caption(
            caption=f"{callback.message.caption}\n\n❌ <b>ЗАКАЗ ОТКЛОНЕН</b>",
            parse_mode="HTML"
        )
        
        logger.info(f"Админ отклонил заказ #{order_id}")
    
    await callback.answer()

@dp.message(F.text, F.from_user.id == ADMIN_ID)
async def admin_commands(message: Message):
    """Команды администратора"""
    text = message.text.lower()
    
    if text.startswith("/approve"):
        try:
            order_id = int(text.split()[1])
            update_order_status(order_id, "paid", datetime.now())
            
            conn = sqlite3.connect(DB_NAME)
            cur = conn.cursor()
            cur.execute("SELECT user_id, product, price FROM orders WHERE id = ?", (order_id,))
            result = cur.fetchone()
            conn.close()
            
            if result:
                user_id, product_name, price = result
                await bot.send_message(
                    user_id,
                    f"✅ <b>Заказ #{order_id} подтвержден!</b>\n\n"
                    f"📦 Товар: {product_name}\n"
                    f"🔑 Чит: https://example.com/cheat/{order_id}",
                    parse_mode="HTML"
                )
                await message.reply(f"✅ Заказ #{order_id} подтвержден")
                
        except Exception as e:
            await message.reply(f"❌ Ошибка: {e}")
    
    elif text.startswith("/reject"):
        try:
            order_id = int(text.split()[1])
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
                    f"❌ Заказ #{order_id} отклонен. Свяжитесь с поддержкой: @aimnoob_support",
                    parse_mode="HTML"
                )
                await message.reply(f"❌ Заказ #{order_id} отклонен")
                
        except Exception as e:
            await message.reply(f"❌ Ошибка: {e}")
    
    elif text == "/stats":
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM orders")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM orders WHERE status = 'paid'")
        paid = cur.fetchone()[0]
        cur.execute("SELECT SUM(price) FROM orders WHERE status = 'paid'")
        revenue = cur.fetchone()[0] or 0
        conn.close()
        
        await message.reply(
            f"📊 <b>СТАТИСТИКА</b>\n\n"
            f"📦 Всего: {total}\n"
            f"✅ Оплачено: {paid}\n"
            f"💰 Выручка: {revenue} ₽",
            parse_mode="HTML"
        )
    
    elif text == "/oauth_url":
        # Получить ссылку для авторизации OAuth2
        oauth_url = f"https://yoomoney.ru/oauth/authorize?client_id={CLIENT_ID}&response_type=code&redirect_uri={REDIRECT_URI}"
        await message.reply(
            f"🔑 <b>Ссылка для получения OAuth токена:</b>\n\n"
            f"<code>{oauth_url}</code>\n\n"
            f"Перейдите по ссылке, авторизуйтесь, и после редиректа токен будет сохранен автоматически.",
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
        "🔹 Быстрая поддержка\n\n"
        "👇 <b>Выберите действие:</b>",
        reply_markup=main_menu_kb(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "buy_cheat")
async def buy_cheat(callback: CallbackQuery):
    await callback.message.edit_text(
        "🎮 <b>Выберите игру:</b>",
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
        "⏳ <b>ВЫБЕРИТЕ СРОК:</b>",
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
        f"⭐ Баланс: 0 баллов",
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "promo")
async def promo(callback: CallbackQuery):
    await callback.message.answer(
        "🎁 <b>Акции</b>\n\n"
        "🔥 При покупке подписки на месяц - скидка 10%\n"
        "🎉 За отзыв - 1 неделя чиста бесплатно\n\n"
        "🔑 Промокод: AIMNOOB10 - скидка 10%",
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "invite")
async def invite(callback: CallbackQuery):
    bot_username = (await bot.get_me()).username
    await callback.message.answer(
        f"👥 <b>Пригласи друга!</b>\n\n"
        f"Ссылка: <code>https://t.me/{bot_username}?start=ref_{callback.from_user.id}</code>\n\n"
        f"🎁 За каждого друга - 50 баллов!",
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
    await callback.message.edit_text("🎮 <b>Выберите игру:</b>", reply_markup=games_kb(), parse_mode="HTML")
    await state.set_state(None)
    await callback.answer()

@dp.callback_query(F.data == "back_platforms")
async def back_platforms(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("📱 <b>ВЫБЕРИТЕ ПЛАТФОРМУ:</b>", reply_markup=platforms_kb(), parse_mode="HTML")
    await state.set_state(OrderState.choosing_platform)
    await callback.answer()

@dp.callback_query(F.data == "back_durations")
async def back_durations(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    platform = data.get("platform", "apk")
    await callback.message.edit_text("⏳ <b>ВЫБЕРИТЕ СРОК:</b>", reply_markup=durations_kb(platform), parse_mode="HTML")
    await state.set_state(OrderState.choosing_duration)
    await callback.answer()

@dp.callback_query(F.data == "back_payment")
async def back_payment(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    product = data.get("product", "Standoff 2 0.37.1 | НЕДЕЛЯ")
    price = data.get("price", 150)
    await callback.message.edit_text(
        f"🛍 <b>Ваш заказ:</b>\n\n📦 {product}\n💰 {price} ₽\n\n💳 <b>Способ оплаты:</b>",
        reply_markup=payment_methods_kb(product, price),
        parse_mode="HTML"
    )
    await state.set_state(OrderState.choosing_payment)
    await callback.answer()

# ==================== ЗАПУСК ====================
async def main():
    init_db()
    
    # Запускаем веб-сервер
    app = web.Application()
    app.router.add_post(WEBHOOK_PATH, yoomoney_webhook)
    app.router.add_get(OAUTH_PATH, oauth_handler)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', WEB_SERVER_PORT)
    await site.start()
    
    logger.info(f"Webhook сервер запущен на порту {WEB_SERVER_PORT}")
    logger.info(f"Webhook URL: {WEBHOOK_URL}")
    logger.info(f"OAuth URL: {WEBHOOK_HOST}{OAUTH_PATH}")
    
    logger.info("Бот запускается...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
