import logging
import asyncio
import aiohttp
import hashlib
import hmac
import time
import random
import json
import os
from datetime import datetime, timedelta
from urllib.parse import parse_qs, unquote, quote
from collections import OrderedDict
from typing import Optional, Dict, Any

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice, PreCheckoutQuery,
    MenuButtonWebApp, WebAppInfo
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiohttp import web

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ========== КОНФИГУРАЦИЯ ==========
class Config:
    BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "8776209296:AAGTYJFa3C2nsGnueLxvrjvZ4i-ywTcfeE4")
    CRYPTOBOT_TOKEN: str = os.environ.get("CRYPTOBOT_TOKEN", "493276:AAtS7R1zYy0gaPw8eax1EgiWo0tdnd6dQ9c")
    YOOMONEY_ACCESS_TOKEN: str = os.environ.get("YOOMONEY_ACCESS_TOKEN", "4100118889570559.3288B2E716CEEB922A26BD6BEAC58648FBFB680CCF64E4E1447D714D6FB5EA5F01F1478FAC686BEF394C8A186C98982DE563C1ABCDF9F2F61D971B61DA3C7E486CA818F98B9E0069F1C0891E090DD56A11319D626A40F0AE8302A8339DED9EB7969617F191D93275F64C4127A3ECB7AED33FCDE91CA68690EB7534C67E6C219E")
    YOOMONEY_WALLET: str = os.environ.get("YOOMONEY_WALLET", "4100118889570559")

    SUPPORT_CHAT_USERNAME = os.environ.get("SUPPORT_CHAT_USERNAME", "aimnoob_support")
    SHOP_URL = os.environ.get("SHOP_URL", "https://aimnoob.ru")
    MINIAPP_URL = os.environ.get("MINIAPP_URL", "https://AimNoobs.bothost.tech")
    DOWNLOAD_URL = os.environ.get("DOWNLOAD_URL", "https://go.linkify.ru/2GPF")
    WEB_PORT = int(os.environ.get("PORT", "8080"))

    ADMIN_IDS = set()
    ADMIN_ID = 0
    SUPPORT_CHAT_ID = 0

    MAX_PENDING_ORDERS = 1000
    ORDER_EXPIRY_SECONDS = 3600
    RATE_LIMIT_SECONDS = 2
    MAX_PAYMENT_CHECK_ATTEMPTS = 5
    PAYMENT_CHECK_INTERVAL = 5

    @classmethod
    def init(cls):
        if not cls.BOT_TOKEN:
            raise ValueError("BOT_TOKEN environment variable is required!")

        admin_ids_str = os.environ.get("ADMIN_ID", "")
        admin_ids_list = [
            int(x.strip())
            for x in admin_ids_str.split(",")
            if x.strip().isdigit()
        ]

        if not admin_ids_list:
            raise ValueError("ADMIN_ID environment variable is required!")

        cls.ADMIN_ID = admin_ids_list[0]
        cls.SUPPORT_CHAT_ID = (
            admin_ids_list[1]
            if len(admin_ids_list) >= 2
            else int(os.environ.get("SUPPORT_CHAT_ID", str(cls.ADMIN_ID)))
        )
        cls.ADMIN_IDS = set(admin_ids_list)

        if not cls.CRYPTOBOT_TOKEN:
            logger.warning("CRYPTOBOT_TOKEN not set - crypto payments disabled")
        if not cls.YOOMONEY_ACCESS_TOKEN:
            logger.warning("YOOMONEY_ACCESS_TOKEN not set - card payments disabled")
        if not cls.YOOMONEY_WALLET:
            logger.warning("YOOMONEY_WALLET not set - card payments disabled")


# ========== ХРАНИЛИЩЕ ДАННЫХ ==========
class OrderStorage:
    def __init__(self, max_pending=1000, expiry_seconds=3600):
        self._pending = OrderedDict()
        self._confirmed = {}
        self._lock = asyncio.Lock()
        self._max_pending = max_pending
        self._expiry_seconds = expiry_seconds

    async def add_pending(self, order_id, order_data):
        async with self._lock:
            await self._cleanup_expired()
            if len(self._pending) >= self._max_pending:
                self._pending.popitem(last=False)
            self._pending[order_id] = order_data

    async def get_pending(self, order_id):
        async with self._lock:
            return self._pending.get(order_id)

    async def confirm(self, order_id, extra_data):
        async with self._lock:
            if order_id in self._confirmed:
                return False
            order = self._pending.pop(order_id, None)
            if order is None:
                return False
            self._confirmed[order_id] = {**order, **extra_data}
            return True

    async def is_confirmed(self, order_id):
        async with self._lock:
            return order_id in self._confirmed

    async def get_confirmed(self, order_id):
        async with self._lock:
            return self._confirmed.get(order_id)

    async def remove_pending(self, order_id):
        async with self._lock:
            return self._pending.pop(order_id, None)

    async def get_stats(self):
        async with self._lock:
            return {
                "pending": len(self._pending),
                "confirmed": len(self._confirmed)
            }

    async def get_recent_pending(self, limit=5):
        async with self._lock:
            items = list(self._pending.items())[-limit:]
            return items

    async def _cleanup_expired(self):
        now = time.time()
        expired = [
            oid for oid, data in self._pending.items()
            if now - data.get("created_at", 0) > self._expiry_seconds
        ]
        for oid in expired:
            del self._pending[oid]
        if expired:
            logger.info("Cleaned up %d expired orders", len(expired))


class RateLimiter:
    def __init__(self, interval=2.0):
        self._last_action = {}
        self._interval = interval

    def check(self, user_id):
        now = time.time()
        last = self._last_action.get(user_id, 0)
        if now - last < self._interval:
            return False
        self._last_action[user_id] = now
        if len(self._last_action) > 10000:
            cutoff = now - self._interval * 10
            self._last_action = {
                uid: t for uid, t in self._last_action.items()
                if t > cutoff
            }
        return True


# ========== ПРОДУКТЫ ==========
PRODUCTS = {
    "apk_week": {
        "name": "📱 AimNoob Android",
        "period_text": "НЕДЕЛЮ",
        "price": 150,
        "price_stars": 150,
        "price_gold": 150,
        "price_nft": 150,
        "price_crypto_usdt": 1.5,
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
        "price_stars": 350,
        "price_gold": 350,
        "price_nft": 350,
        "price_crypto_usdt": 3.5,
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
        "price_stars": 800,
        "price_gold": 800,
        "price_nft": 800,
        "price_crypto_usdt": 8,
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
        "price_stars": 300,
        "price_gold": 300,
        "price_nft": 300,
        "price_crypto_usdt": 3,
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
        "price_stars": 450,
        "price_gold": 450,
        "price_nft": 450,
        "price_crypto_usdt": 4.5,
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
        "price_stars": 850,
        "price_gold": 850,
        "price_nft": 850,
        "price_crypto_usdt": 8.5,
        "platform": "iOS",
        "period": "НАВСЕГДА",
        "platform_code": "ios",
        "emoji": "🍎",
        "duration": "Навсегда"
    }
}


# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def generate_order_id():
    raw = "{}_{}_{}".format(time.time(), random.randint(100000, 999999), os.urandom(4).hex())
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def generate_license_key(order_id, user_id):
    raw = "{}_{}_{}".format(order_id, user_id, os.urandom(8).hex())
    h = hashlib.sha256(raw.encode()).hexdigest()[:16].upper()
    return "AIMNOOB-{}-{}-{}-{}".format(h[:4], h[4:8], h[8:12], h[12:16])


def is_admin(user_id):
    return user_id in Config.ADMIN_IDS


def find_product(platform_code, period):
    for p in PRODUCTS.values():
        if p['platform_code'] == platform_code and p['period'] == period:
            return p
    return None


def find_product_by_id(product_id):
    return PRODUCTS.get(product_id)


def validate_telegram_init_data(init_data, bot_token):
    if not init_data:
        return None
    try:
        parsed = parse_qs(init_data)
        received_hash = parsed.get('hash', [None])[0]
        if not received_hash:
            return None
        data_pairs = []
        for key, values in parsed.items():
            if key != 'hash':
                data_pairs.append("{}={}".format(key, values[0]))
        data_pairs.sort()
        data_check_string = '\n'.join(data_pairs)
        secret_key = hmac.new(
            b"WebAppData", bot_token.encode(), hashlib.sha256
        ).digest()
        computed_hash = hmac.new(
            secret_key, data_check_string.encode(), hashlib.sha256
        ).hexdigest()
        if computed_hash == received_hash:
            user_data = parsed.get('user', [None])[0]
            if user_data:
                return json.loads(unquote(user_data))
        return None
    except Exception as e:
        logger.warning("initData validation failed: %s", e)
        return None


def create_payment_link(amount, order_id, product_name):
    comment = "Заказ {}: {}".format(order_id, product_name)
    safe_targets = quote(comment, safe='')
    success_url = quote('https://t.me/aimnoob_bot?start=success', safe='')
    return (
        "https://yoomoney.ru/quickpay/confirm.xml"
        "?receiver={}"
        "&quickpay-form=shop"
        "&targets={}"
        "&sum={}"
        "&label={}"
        "&successURL={}"
        "&paymentType=AC"
    ).format(Config.YOOMONEY_WALLET, safe_targets, amount, order_id, success_url)


# ========== ПЛАТЁЖНЫЕ СЕРВИСЫ ==========
class YooMoneyService:
    @staticmethod
    async def get_balance():
        if not Config.YOOMONEY_ACCESS_TOKEN:
            return None
        headers = {"Authorization": "Bearer {}".format(Config.YOOMONEY_ACCESS_TOKEN)}
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    "https://yoomoney.ru/api/account-info",
                    headers=headers
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return float(data.get('balance', 0))
                    else:
                        body = await resp.text()
                        logger.error("YooMoney account-info %s: %s", resp.status, body)
        except Exception as e:
            logger.error("YooMoney balance error: %s", e)
        return None

    @staticmethod
    async def check_payment(order_id, expected_amount, order_time):
        if not Config.YOOMONEY_ACCESS_TOKEN:
            return False
        headers = {"Authorization": "Bearer {}".format(Config.YOOMONEY_ACCESS_TOKEN)}
        data = {"type": "deposition", "records": 100}
        try:
            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    "https://yoomoney.ru/api/operation-history",
                    headers=headers, data=data
                ) as resp:
                    if resp.status != 200:
                        return False
                    result = await resp.json()
                    operations = result.get("operations", [])
                    for op in operations:
                        if (op.get("label") == order_id
                                and op.get("status") == "success"
                                and abs(float(op.get("amount", 0)) - expected_amount) <= 5):
                            return True
                    for op in operations:
                        if op.get("status") != "success":
                            continue
                        op_amount = float(op.get("amount", 0))
                        if abs(op_amount - expected_amount) > 2:
                            continue
                        try:
                            dt_str = op.get("datetime", "")
                            op_time = datetime.fromisoformat(
                                dt_str.replace("Z", "+00:00")
                            ).timestamp()
                            if abs(op_time - order_time) <= 1800:
                                return True
                        except (ValueError, TypeError):
                            pass
        except Exception as e:
            logger.error("YooMoney check error: %s", e)
        return False


class CryptoBotService:
    BASE_URL = "https://pay.crypt.bot/api"

    @staticmethod
    async def create_invoice(amount_usdt, order_id, description):
        if not Config.CRYPTOBOT_TOKEN:
            return None
        headers = {
            "Crypto-Pay-API-Token": Config.CRYPTOBOT_TOKEN,
            "Content-Type": "application/json"
        }
        data = {
            "asset": "USDT",
            "amount": str(amount_usdt),
            "description": description[:256],
            "payload": order_id,
            "paid_btn_name": "callback",
            "paid_btn_url": "https://t.me/aimnoob_bot?start=paid_{}".format(order_id)
        }
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    CryptoBotService.BASE_URL + "/createInvoice",
                    headers=headers, json=data
                ) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        if result.get("ok"):
                            inv = result["result"]
                            return {
                                "invoice_id": inv.get("invoice_id"),
                                "pay_url": inv.get("pay_url"),
                                "amount": inv.get("amount")
                            }
                    body = await resp.text()
                    logger.error("CryptoBot createInvoice %s: %s", resp.status, body)
        except Exception as e:
            logger.error("CryptoBot API error: %s", e)
        return None

    @staticmethod
    async def check_invoice(invoice_id):
        if not Config.CRYPTOBOT_TOKEN:
            return False
        headers = {
            "Crypto-Pay-API-Token": Config.CRYPTOBOT_TOKEN,
            "Content-Type": "application/json"
        }
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    CryptoBotService.BASE_URL + "/getInvoices",
                    headers=headers,
                    json={"invoice_ids": [invoice_id]}
                ) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        if result.get("ok"):
                            items = result.get("result", {}).get("items", [])
                            if items:
                                return items[0].get("status") == "paid"
        except Exception as e:
            logger.error("CryptoBot check error: %s", e)
        return False


# ========== ИНИЦИАЛИЗАЦИЯ ==========
Config.init()

bot = Bot(
    token=Config.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

orders = OrderStorage(
    max_pending=Config.MAX_PENDING_ORDERS,
    expiry_seconds=Config.ORDER_EXPIRY_SECONDS
)
rate_limiter = RateLimiter(interval=Config.RATE_LIMIT_SECONDS)


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
        [InlineKeyboardButton(
            text="🎮 Открыть магазин",
            web_app=WebAppInfo(url=Config.MINIAPP_URL)
        )],
        [InlineKeyboardButton(text="ℹ️ О программе", callback_data="about")],
        [InlineKeyboardButton(
            text="💬 Поддержка",
            url="https://t.me/{}".format(Config.SUPPORT_CHAT_USERNAME)
        )]
    ])


def subscription_keyboard(platform):
    prices = {
        "apk": [
            ("⚡ НЕДЕЛЯ — 150₽", "sub_apk_week"),
            ("🔥 МЕСЯЦ — 350₽", "sub_apk_month"),
            ("💎 НАВСЕГДА — 800₽", "sub_apk_forever"),
        ],
        "ios": [
            ("⚡ НЕДЕЛЯ — 300₽", "sub_ios_week"),
            ("🔥 МЕСЯЦ — 450₽", "sub_ios_month"),
            ("💎 НАВСЕГДА — 850₽", "sub_ios_forever"),
        ]
    }
    buttons = [
        [InlineKeyboardButton(text=text, callback_data=cb)]
        for text, cb in prices.get(platform, [])
    ]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_platform")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def payment_methods_keyboard(product):
    pc = product['platform_code']
    p = product['period']
    buttons = [
        [InlineKeyboardButton(text="💳 Картой", callback_data="pay_yoomoney_{}_{}".format(pc, p))],
        [InlineKeyboardButton(text="⭐ Telegram Stars", callback_data="pay_stars_{}_{}".format(pc, p))],
        [InlineKeyboardButton(text="₿ Криптобот", callback_data="pay_crypto_{}_{}".format(pc, p))],
        [InlineKeyboardButton(text="💰 GOLD", callback_data="pay_gold_{}_{}".format(pc, p))],
        [InlineKeyboardButton(text="🎨 NFT", callback_data="pay_nft_{}_{}".format(pc, p))],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_subscription")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def payment_keyboard(payment_url, order_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить картой", url=payment_url)],
        [InlineKeyboardButton(text="✅ Проверить оплату", callback_data="checkym_{}".format(order_id))],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="restart")]
    ])


def crypto_payment_keyboard(invoice_url, order_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="₿ Оплатить криптой", url=invoice_url)],
        [InlineKeyboardButton(text="✅ Проверить платеж", callback_data="checkcr_{}".format(order_id))],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="restart")]
    ])


def support_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="💬 Поддержка",
            url="https://t.me/{}".format(Config.SUPPORT_CHAT_USERNAME)
        )],
        [InlineKeyboardButton(text="🌐 Сайт", url=Config.SHOP_URL)],
        [InlineKeyboardButton(text="🔄 Новая покупка", callback_data="restart")]
    ])


def download_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Скачать AimNoob", url=Config.DOWNLOAD_URL)],
        [InlineKeyboardButton(
            text="💬 Поддержка",
            url="https://t.me/{}".format(Config.SUPPORT_CHAT_USERNAME)
        )],
        [InlineKeyboardButton(text="🌐 Сайт", url=Config.SHOP_URL)],
        [InlineKeyboardButton(text="🔄 Новая покупка", callback_data="restart")]
    ])


def about_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_platform")]
    ])


def admin_confirm_keyboard(order_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data="admin_confirm_{}".format(order_id))],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data="admin_reject_{}".format(order_id))]
    ])


def manual_payment_keyboard(support_url, sent_callback):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Перейти к оплате", url=support_url)],
        [InlineKeyboardButton(text="✅ Я написал", callback_data=sent_callback)],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="restart")]
    ])


# ========== БИЗНЕС-ЛОГИКА ==========
async def process_successful_payment(order_id, source="API"):
    order = await orders.get_pending(order_id)
    if not order:
        if await orders.is_confirmed(order_id):
            logger.info("Order %s already confirmed", order_id)
        return False

    product = order["product"]
    user_id = order["user_id"]
    license_key = generate_license_key(order_id, user_id)

    confirmed = await orders.confirm(order_id, {
        'confirmed_at': time.time(),
        'confirmed_by': source,
        'license_key': license_key
    })

    if not confirmed:
        return False

    success_text = (
        "🎉 <b>Оплата подтверждена!</b>\n\n"
        "✨ Добро пожаловать в AimNoob!\n\n"
        "📦 <b>Ваша покупка:</b>\n"
        "{emoji} {name}\n"
        "⏱️ Срок: {duration}\n"
        "🔍 Метод: {source}\n\n"
        "🔑 <b>Ваш лицензионный ключ:</b>\n"
        "<code>{key}</code>\n\n"
        "📥 <b>Скачивание:</b>\n"
        "👇 Нажмите кнопку ниже для загрузки\n\n"
        "💫 <b>Активация:</b>\n"
        "1️⃣ Скачайте файл по кнопке ниже\n"
        "2️⃣ Установите приложение\n"
        "3️⃣ Введите ключ при запуске\n"
        "4️⃣ Наслаждайтесь игрой! 🎮\n\n"
        "💬 Поддержка: @{support}"
    ).format(
        emoji=product['emoji'], name=product['name'],
        duration=product['duration'], source=source,
        key=license_key, support=Config.SUPPORT_CHAT_USERNAME
    )

    try:
        await bot.send_message(user_id, success_text, reply_markup=download_keyboard())
    except Exception as e:
        logger.error("Error sending to user %s: %s", user_id, e)

    order_amount = order.get('amount', product['price'])
    order_currency = order.get('currency', '₽')
    now_str = datetime.now().strftime('%d.%m.%Y %H:%M')

    admin_text = (
        "💎 <b>НОВАЯ ПРОДАЖА ({source})</b>\n\n"
        "👤 {user_name}\n"
        "🆔 {user_id}\n"
        "📦 {product_name} ({duration})\n"
        "💰 {amount} {currency}\n"
        "🔑 <code>{key}</code>\n"
        "📅 {now}"
    ).format(
        source=source, user_name=order['user_name'],
        user_id=user_id, product_name=product['name'],
        duration=product['duration'], amount=order_amount,
        currency=order_currency, key=license_key, now=now_str
    )
    for aid in Config.ADMIN_IDS:
        try:
            await bot.send_message(aid, admin_text)
        except Exception as e:
            logger.error("Error notifying admin %s: %s", aid, e)

    return True


async def send_admin_notification(user, product, payment_method, price, order_id):
    now_str = datetime.now().strftime('%d.%m.%Y %H:%M')
    message = (
        "🔔 <b>НОВЫЙ ЗАКАЗ</b>\n\n"
        "👤 {full_name}\n"
        "🆔 <code>{user_id}</code>\n"
        "📦 {product_name} ({duration})\n"
        "💰 {price}\n"
        "💳 {payment_method}\n"
        "🆔 <code>{order_id}</code>\n\n"
        "📅 {now}"
    ).format(
        full_name=user.full_name, user_id=user.id,
        product_name=product['name'], duration=product['duration'],
        price=price, payment_method=payment_method,
        order_id=order_id, now=now_str
    )
    for aid in Config.ADMIN_IDS:
        try:
            await bot.send_message(aid, message, reply_markup=admin_confirm_keyboard(order_id))
        except Exception as e:
            logger.error("Error sending to admin %s: %s", aid, e)


async def send_start_message(target, state):
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

    if isinstance(target, types.Message):
        await target.answer(text, reply_markup=platform_keyboard())
    elif isinstance(target, types.CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=platform_keyboard())
        except Exception:
            await target.message.answer(text, reply_markup=platform_keyboard())

    await state.set_state(OrderState.choosing_platform)


# ========== ОБРАБОТЧИКИ БОТА ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    args = message.text.split()
    if len(args) > 1:
        deep_link = args[1]
        if deep_link.startswith("buy_stars_"):
            product_id = deep_link.replace("buy_stars_", "", 1)
            product = find_product_by_id(product_id)
            if product:
                order_id = generate_order_id()
                await orders.add_pending(order_id, {
                    "user_id": message.from_user.id,
                    "user_name": message.from_user.full_name,
                    "product": product,
                    "amount": product['price_stars'],
                    "currency": "⭐",
                    "payment_method": "Telegram Stars",
                    "status": "pending",
                    "created_at": time.time()
                })
                title = "AimNoob - {}".format(product['name'])
                desc = "Подписка на {} для {}".format(product['duration'], product['platform'])
                payload = "stars_{}".format(order_id)
                await bot.send_invoice(
                    chat_id=message.from_user.id,
                    title=title, description=desc,
                    payload=payload, provider_token="",
                    currency="XTR",
                    prices=[LabeledPrice(label="XTR", amount=product['price_stars'])],
                    start_parameter="aimnoob_payment"
                )
                return
    await send_start_message(message, state)


@dp.callback_query(F.data == "about")
async def about_cheat(callback: types.CallbackQuery):
    text = (
        "📋 <b>Подробная информация</b>\n\n"
        "🎮 <b>Версия:</b> 0.37.1\n"
        "🔥 <b>Статус:</b> Активно\n\n"
        "🛠️ <b>Функционал:</b>\n"
        "• 🎯 AimBot\n"
        "• 👁️ WallHack\n"
        "• 📍 ESP\n"
        "• 🗺️ Радар\n"
        "• ⚙️ Настройки\n\n"
        "💬 Поддержка: @{}"
    ).format(Config.SUPPORT_CHAT_USERNAME)
    await callback.message.edit_text(text, reply_markup=about_keyboard())
    await callback.answer()


@dp.callback_query(F.data.startswith("platform_"))
async def process_platform(callback: types.CallbackQuery, state: FSMContext):
    platform = callback.data.split("_")[1]
    if platform not in ("apk", "ios"):
        await callback.answer("❌ Неизвестная платформа", show_alert=True)
        return
    await state.update_data(platform=platform)
    platform_info = {
        "apk": {
            "title": "📱 <b>Android Version</b>",
            "requirements": "• Android 10.0+\n• 2 ГБ RAM\n• Root не нужен",
            "includes": "• APK файл\n• Инструкция\n• Поддержка"
        },
        "ios": {
            "title": "🍎 <b>iOS Version</b>",
            "requirements": "• iOS 14.0 - 18.0\n• AltStore\n• Jailbreak не нужен",
            "includes": "• IPA файл\n• Инструкция\n• Помощь"
        }
    }
    info = platform_info[platform]
    text = (
        "{title}\n\n"
        "🔧 <b>Требования:</b>\n{requirements}\n\n"
        "📦 <b>Входит:</b>\n{includes}\n\n"
        "💰 <b>Тариф:</b>"
    ).format(title=info['title'], requirements=info['requirements'], includes=info['includes'])
    await callback.message.edit_text(text, reply_markup=subscription_keyboard(platform))
    await state.set_state(OrderState.choosing_subscription)
    await callback.answer()


@dp.callback_query(F.data.startswith("sub_"))
async def process_subscription(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    if len(parts) < 3:
        await callback.answer("❌ Ошибка", show_alert=True)
        return
    product_key = "{}_{}".format(parts[1], parts[2])
    product = find_product_by_id(product_key)
    if not product:
        await callback.answer("❌ Не найдено", show_alert=True)
        return
    await state.update_data(selected_product=product)
    text = (
        "🛒 <b>Оформление</b>\n\n"
        "{emoji} <b>{name}</b>\n"
        "⏱️ {duration}\n\n"
        "💎 <b>Стоимость:</b>\n"
        "💳 Картой: {price} ₽\n"
        "⭐ Stars: {price_stars} ⭐\n"
        "₿ Крипта: {price_crypto} USDT\n"
        "💰 GOLD: {price_gold} 🪙\n"
        "🎨 NFT: {price_nft} 🖼️\n\n"
        "🎯 <b>Способ оплаты:</b>"
    ).format(
        emoji=product['emoji'], name=product['name'],
        duration=product['duration'], price=product['price'],
        price_stars=product['price_stars'],
        price_crypto=product['price_crypto_usdt'],
        price_gold=product['price_gold'],
        price_nft=product['price_nft']
    )
    await callback.message.edit_text(text, reply_markup=payment_methods_keyboard(product))
    await state.set_state(OrderState.choosing_payment)
    await callback.answer()


# ========== ОПЛАТА КАРТОЙ ==========
@dp.callback_query(F.data.startswith("pay_yoomoney_"))
async def process_yoomoney_payment(callback: types.CallbackQuery):
    if not Config.YOOMONEY_WALLET:
        await callback.answer("❌ Недоступно", show_alert=True)
        return
    parts = callback.data.split("_")
    if len(parts) < 4:
        await callback.answer("❌ Ошибка", show_alert=True)
        return
    product = find_product(parts[2], parts[3])
    if not product:
        await callback.answer("❌ Не найдено", show_alert=True)
        return
    user_id = callback.from_user.id
    if not rate_limiter.check(user_id):
        await callback.answer("⏳ Подождите...", show_alert=True)
        return
    order_id = generate_order_id()
    amount = product["price"]
    product_desc = "{} ({})".format(product['name'], product['duration'])
    payment_url = create_payment_link(amount, order_id, product_desc)
    await orders.add_pending(order_id, {
        "user_id": user_id, "user_name": callback.from_user.full_name,
        "product": product, "amount": amount, "currency": "₽",
        "payment_method": "Картой",
        "status": "pending", "created_at": time.time()
    })
    text = (
        "💳 <b>Оплата картой</b>\n\n"
        "{emoji} {name}\n⏱️ {duration}\n"
        "💰 <b>{amount} ₽</b>\n"
        "🆔 <code>{order_id}</code>\n\n"
        "1️⃣ Нажмите «Оплатить»\n"
        "2️⃣ Оплатите\n"
        "3️⃣ Нажмите «Проверить»"
    ).format(
        emoji=product['emoji'], name=product['name'],
        duration=product['duration'], amount=amount, order_id=order_id
    )
    await callback.message.edit_text(text, reply_markup=payment_keyboard(payment_url, order_id))
    await send_admin_notification(callback.from_user, product, "💳 Картой", "{} ₽".format(amount), order_id)
    await callback.answer()


@dp.callback_query(F.data.startswith("checkym_"))
async def check_yoomoney_callback(callback: types.CallbackQuery):
    order_id = callback.data.replace("checkym_", "", 1)
    order = await orders.get_pending(order_id)
    if not order:
        if await orders.is_confirmed(order_id):
            await callback.answer("✅ Уже подтвержден!", show_alert=True)
        else:
            await callback.answer("❌ Не найден", show_alert=True)
        return
    if not rate_limiter.check(callback.from_user.id):
        await callback.answer("⏳ Подождите...", show_alert=True)
        return
    await callback.answer("🔍 Проверяем...")
    checking_msg = await callback.message.edit_text(
        "🔄 <b>Проверка...</b>\n⏳ 15-25 секунд"
    )
    payment_found = False
    for attempt in range(Config.MAX_PAYMENT_CHECK_ATTEMPTS):
        payment_found = await YooMoneyService.check_payment(
            order_id, order["amount"], order.get("created_at", time.time())
        )
        if payment_found:
            break
        await asyncio.sleep(Config.PAYMENT_CHECK_INTERVAL)
    if payment_found:
        success = await process_successful_payment(order_id, "Автопроверка")
        if success:
            await checking_msg.edit_text(
                "✅ <b>Платеж найден!</b>\n📨 Проверьте новое сообщение ↑",
                reply_markup=support_keyboard()
            )
        else:
            await checking_msg.edit_text("✅ <b>Уже обработан</b>", reply_markup=support_keyboard())
    else:
        product = order['product']
        product_desc = "{} ({})".format(product['name'], product['duration'])
        payment_url = create_payment_link(order["amount"], order_id, product_desc)
        await checking_msg.edit_text(
            "⏳ <b>Не найден</b>\nПопробуйте через 1-2 мин",
            reply_markup=payment_keyboard(payment_url, order_id)
        )


# ========== STARS ==========
@dp.callback_query(F.data.startswith("pay_stars_"))
async def process_stars_payment(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    if len(parts) < 4:
        await callback.answer("❌", show_alert=True)
        return
    product = find_product(parts[2], parts[3])
    if not product:
        await callback.answer("❌ Не найдено", show_alert=True)
        return
    if not rate_limiter.check(callback.from_user.id):
        await callback.answer("⏳", show_alert=True)
        return
    order_id = generate_order_id()
    await orders.add_pending(order_id, {
        "user_id": callback.from_user.id,
        "user_name": callback.from_user.full_name,
        "product": product, "amount": product['price_stars'],
        "currency": "⭐", "payment_method": "Telegram Stars",
        "status": "pending", "created_at": time.time()
    })
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title="AimNoob - {}".format(product['name']),
        description="Подписка на {} для {}".format(product['duration'], product['platform']),
        payload="stars_{}".format(order_id),
        provider_token="", currency="XTR",
        prices=[LabeledPrice(label="XTR", amount=product['price_stars'])],
        start_parameter="aimnoob_payment"
    )
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()


@dp.pre_checkout_query()
async def pre_checkout_query_handler(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@dp.message(F.successful_payment)
async def successful_payment(message: types.Message):
    payload = message.successful_payment.invoice_payload
    if payload.startswith("stars_"):
        order_id = payload.replace("stars_", "", 1)
        await process_successful_payment(order_id, "Telegram Stars")


# ========== КРИПТО ==========
@dp.callback_query(F.data.startswith("pay_crypto_"))
async def process_crypto_payment(callback: types.CallbackQuery):
    if not Config.CRYPTOBOT_TOKEN:
        await callback.answer("❌ Недоступно", show_alert=True)
        return
    parts = callback.data.split("_")
    if len(parts) < 4:
        await callback.answer("❌", show_alert=True)
        return
    product = find_product(parts[2], parts[3])
    if not product:
        await callback.answer("❌", show_alert=True)
        return
    if not rate_limiter.check(callback.from_user.id):
        await callback.answer("⏳", show_alert=True)
        return
    order_id = generate_order_id()
    amount_usdt = product["price_crypto_usdt"]
    description = "AimNoob {} ({})".format(product['name'], product['duration'])
    invoice_data = await CryptoBotService.create_invoice(amount_usdt, order_id, description)
    if not invoice_data:
        await callback.answer("❌ Ошибка инвойса", show_alert=True)
        return
    await orders.add_pending(order_id, {
        "user_id": callback.from_user.id,
        "user_name": callback.from_user.full_name,
        "product": product, "amount": amount_usdt, "currency": "USDT",
        "payment_method": "CryptoBot", "status": "pending",
        "invoice_id": invoice_data["invoice_id"], "created_at": time.time()
    })
    text = (
        "₿ <b>Крипто</b>\n\n"
        "{emoji} {name}\n⏱️ {duration}\n"
        "💰 <b>{amount} USDT</b>\n"
        "🆔 <code>{order_id}</code>"
    ).format(
        emoji=product['emoji'], name=product['name'],
        duration=product['duration'], amount=amount_usdt, order_id=order_id
    )
    await callback.message.edit_text(text, reply_markup=crypto_payment_keyboard(invoice_data["pay_url"], order_id))
    await send_admin_notification(callback.from_user, product, "₿ CryptoBot", "{} USDT".format(amount_usdt), order_id)
    await callback.answer()


@dp.callback_query(F.data.startswith("checkcr_"))
async def check_crypto_callback(callback: types.CallbackQuery):
    order_id = callback.data.replace("checkcr_", "", 1)
    order = await orders.get_pending(order_id)
    if not order:
        if await orders.is_confirmed(order_id):
            await callback.answer("✅ Уже оплачено!", show_alert=True)
        else:
            await callback.answer("❌ Не найден", show_alert=True)
        return
    if not rate_limiter.check(callback.from_user.id):
        await callback.answer("⏳", show_alert=True)
        return
    await callback.answer("🔍 Проверяем...")
    invoice_id = order.get("invoice_id")
    if not invoice_id:
        await callback.answer("❌ Нет invoice_id", show_alert=True)
        return
    is_paid = await CryptoBotService.check_invoice(invoice_id)
    if is_paid:
        success = await process_successful_payment(order_id, "CryptoBot")
        if success:
            await callback.message.edit_text(
                "✅ <b>Подтверждено!</b>\n📨 Ключ в сообщении ↑",
                reply_markup=support_keyboard()
            )
    else:
        await callback.answer("⏳ Не подтвержден. Попробуйте позже.", show_alert=True)


# ========== GOLD / NFT ==========
@dp.callback_query(F.data.startswith("pay_gold_"))
async def process_gold_payment(callback: types.CallbackQuery):
    await _process_manual_payment(callback, "gold")


@dp.callback_query(F.data.startswith("pay_nft_"))
async def process_nft_payment(callback: types.CallbackQuery):
    await _process_manual_payment(callback, "nft")


async def _process_manual_payment(callback, method):
    parts = callback.data.split("_")
    if len(parts) < 4:
        await callback.answer("❌", show_alert=True)
        return
    product = find_product(parts[2], parts[3])
    if not product:
        await callback.answer("❌", show_alert=True)
        return
    if not rate_limiter.check(callback.from_user.id):
        await callback.answer("⏳", show_alert=True)
        return
    cfg = {
        "gold": {"name": "GOLD", "icon": "💰", "price_key": "price_gold", "emoji": "🪙"},
        "nft": {"name": "NFT", "icon": "🎨", "price_key": "price_nft", "emoji": "🖼️"}
    }[method]
    price = product[cfg["price_key"]]
    chat_message = "Привет! Хочу купить чит на Standoff 2. {} ({}) за {} {}".format(
        product['platform'], product['period_text'], price, cfg['name']
    )
    encoded_message = quote(chat_message, safe='')
    support_url = "https://t.me/{}?text={}".format(Config.SUPPORT_CHAT_USERNAME, encoded_message)
    text = (
        "{icon} <b>Оплата {method_name}</b>\n\n"
        "{emoji} {product_name}\n⏱️ {duration}\n"
        "💰 <b>{price} {method_name}</b>\n\n"
        "1️⃣ Нажмите «Перейти»\n"
        "2️⃣ Отправьте сообщение\n"
        "3️⃣ Ожидайте"
    ).format(
        icon=cfg['icon'], method_name=cfg['name'],
        emoji=product['emoji'], product_name=product['name'],
        duration=product['duration'], price=price
    )
    order_id = generate_order_id()
    await orders.add_pending(order_id, {
        "user_id": callback.from_user.id,
        "user_name": callback.from_user.full_name,
        "product": product, "amount": price,
        "currency": cfg["name"], "payment_method": cfg["name"],
        "status": "pending", "created_at": time.time()
    })
    await callback.message.edit_text(
        text, reply_markup=manual_payment_keyboard(support_url, "{}_sent".format(method))
    )
    await send_admin_notification(
        callback.from_user, product,
        "{} {}".format(cfg['icon'], cfg['name']),
        "{} {}".format(price, cfg['emoji']), order_id
    )
    await callback.answer()


@dp.callback_query(F.data.in_({"gold_sent", "nft_sent"}))
async def manual_payment_sent(callback: types.CallbackQuery):
    method_name = "GOLD" if callback.data == "gold_sent" else "NFT"
    icon = "💰" if callback.data == "gold_sent" else "🎨"
    text = (
        "✅ <b>Принято!</b>\n\n"
        "{icon} {method_name} заказ в обработке\n"
        "⏱️ До 30 минут\n"
        "💬 @{support}"
    ).format(icon=icon, method_name=method_name, support=Config.SUPPORT_CHAT_USERNAME)
    await callback.message.edit_text(text, reply_markup=support_keyboard())
    await callback.answer()


# ========== АДМИН ==========
@dp.callback_query(F.data.startswith("admin_confirm_"))
async def admin_confirm_payment(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌", show_alert=True)
        return
    order_id = callback.data.replace("admin_confirm_", "", 1)
    success = await process_successful_payment(order_id, "👨‍💼 Админ")
    if success:
        await callback.message.edit_text(
            "✅ <b>Подтвержден</b>\n🆔 {}\n👨‍💼 {}".format(
                order_id, callback.from_user.full_name
            )
        )
        await callback.answer("✅")
    else:
        await callback.answer("❌ Не найден / уже обработан", show_alert=True)


@dp.callback_query(F.data.startswith("admin_reject_"))
async def admin_reject_payment(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌", show_alert=True)
        return
    order_id = callback.data.replace("admin_reject_", "", 1)
    order = await orders.remove_pending(order_id)
    if order:
        await callback.message.edit_text(
            "❌ <b>Отклонен</b>\n🆔 {}".format(order_id)
        )
        try:
            await bot.send_message(order['user_id'],
                "❌ <b>Заказ отклонен</b>\n💬 @{}".format(Config.SUPPORT_CHAT_USERNAME)
            )
        except Exception:
            pass
    await callback.answer("❌ Отклонен")


@dp.message(Command("orders"))
async def cmd_orders(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    stats = await orders.get_stats()
    text = "📊 <b>Статистика</b>\n\n"
    text += "⏳ Ожидают: {}\n".format(stats['pending'])
    text += "✅ Подтверждено: {}\n".format(stats['confirmed'])
    balance = await YooMoneyService.get_balance()
    if balance is not None:
        text += "💰 Баланс: {} ₽\n".format(balance)
    await message.answer(text)


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "🔧 <b>Админ:</b>\n\n"
        "/orders — Статистика\n"
        "/help — Справка"
    )


# ========== НАВИГАЦИЯ ==========
@dp.callback_query(F.data == "restart")
async def restart_order(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await send_start_message(callback, state)
    await callback.answer()


@dp.callback_query(F.data == "back_to_platform")
async def back_to_platform(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await send_start_message(callback, state)
    await callback.answer()


@dp.callback_query(F.data == "back_to_subscription")
async def back_to_subscription(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    platform = data.get("platform", "apk")
    title = "📱 <b>Android</b>" if platform == "apk" else "🍎 <b>iOS</b>"
    text = "{}\n\n💰 <b>Тариф:</b>".format(title)
    await callback.message.edit_text(text, reply_markup=subscription_keyboard(platform))
    await state.set_state(OrderState.choosing_subscription)
    await callback.answer()


# ========== МИНИ-ПРИЛОЖЕНИЕ ==========
MINIAPP_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,maximum-scale=1,user-scalable=no">
<title>AimNoob Shop</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;background:#050510;color:#fff;min-height:100vh}
.app{max-width:480px;margin:0 auto;padding:16px 16px 80px}
.header{text-align:center;padding:20px 0 24px}
.logo{width:64px;height:64px;background:linear-gradient(135deg,#8b5cf6,#ec4899);border-radius:20px;display:flex;align-items:center;justify-content:center;font-size:32px;margin:0 auto 12px}
h1{font-size:24px;font-weight:700;background:linear-gradient(135deg,#fff,#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.tagline{color:#8e8e9e;font-size:12px;margin-top:4px}
.status-bar{display:flex;align-items:center;justify-content:center;gap:8px;background:rgba(16,185,129,.1);border-radius:20px;padding:6px 12px;width:fit-content;margin:0 auto 20px;font-size:11px}
.status-dot{width:6px;height:6px;background:#10b981;border-radius:50%}
.tabs{display:flex;gap:4px;background:rgba(255,255,255,.04);border-radius:14px;padding:4px;margin-bottom:20px}
.tab{flex:1;padding:10px;border:none;background:transparent;color:#8e8e9e;font-size:12px;font-weight:600;border-radius:11px;cursor:pointer}
.tab.active{background:rgba(255,255,255,.12);color:#fff}
.section{margin-bottom:24px}
.section-title{font-size:16px;font-weight:600;margin-bottom:12px;display:flex;align-items:center;gap:8px}
.cards{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.card{background:rgba(255,255,255,.04);border-radius:16px;padding:12px;cursor:pointer;transition:.2s;border:1px solid rgba(255,255,255,.06)}
.card:active{transform:scale(.98)}
.card-icon{font-size:28px;margin-bottom:8px}
.card-name{font-size:14px;font-weight:600;margin-bottom:2px}
.card-period{font-size:11px;color:#8e8e9e;margin-bottom:8px}
.card-price{font-size:18px;font-weight:700;color:#f59e0b}
.card-old{font-size:10px;color:#6b6b7a;text-decoration:line-through;margin-left:4px}
.card-features{display:flex;gap:4px;flex-wrap:wrap;margin:8px 0}
.feat{font-size:8px;padding:2px 6px;background:rgba(139,92,246,.2);border-radius:6px;color:#a78bfa}
.card-btn{width:100%;padding:8px;background:linear-gradient(135deg,#8b5cf6,#ec4899);border:none;border-radius:12px;color:#fff;font-size:12px;font-weight:600;margin-top:8px;cursor:pointer}
.card-full{grid-column:1/-1}
.card-full .card-body{display:flex;align-items:center;gap:12px}
.card-full .card-icon{margin:0}
.card-full .card-info{flex:1}
.card-full .card-right{text-align:right}
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.9);z-index:1000;align-items:flex-end;justify-content:center}
.modal-overlay.open{display:flex}
.modal{background:#0a0a18;border-radius:24px 24px 0 0;width:100%;max-width:480px;max-height:85vh;overflow-y:auto}
.modal-handle{width:40px;height:4px;background:#2a2a36;border-radius:2px;margin:12px auto}
.modal-head{display:flex;justify-content:space-between;align-items:center;padding:16px 20px;border-bottom:1px solid rgba(255,255,255,.06)}
.modal-title{font-size:18px;font-weight:600}
.modal-close{width:32px;height:32px;border-radius:16px;background:rgba(255,255,255,.08);border:none;color:#fff;font-size:20px;cursor:pointer}
.modal-body{padding:20px}
.pay-list{display:flex;flex-direction:column;gap:8px}
.pay-item{display:flex;align-items:center;justify-content:space-between;padding:12px;background:rgba(255,255,255,.04);border-radius:12px;cursor:pointer}
.pay-item:active{background:rgba(255,255,255,.08)}
.pay-left{display:flex;align-items:center;gap:12px}
.pay-icon{width:44px;height:44px;background:rgba(255,255,255,.08);border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:20px}
.pay-name{font-weight:600;font-size:14px}
.pay-desc{font-size:10px;color:#8e8e9e}
.pay-amount{font-weight:700;color:#f59e0b}
.action-btn{width:100%;padding:14px;background:linear-gradient(135deg,#8b5cf6,#ec4899);border:none;border-radius:14px;color:#fff;font-weight:600;font-size:16px;cursor:pointer;margin-top:12px}
.action-btn.secondary{background:rgba(255,255,255,.08)}
.key-box{background:rgba(0,0,0,.4);padding:12px;border-radius:12px;font-family:monospace;font-size:12px;text-align:center;margin:12px 0;cursor:pointer}
.license-card{background:rgba(255,255,255,.04);border-radius:12px;padding:12px;margin-bottom:8px}
.license-header{display:flex;align-items:center;gap:12px;margin-bottom:8px}
.license-icon{width:40px;height:40px;background:linear-gradient(135deg,#8b5cf6,#ec4899);border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:20px}
.license-name{font-weight:600}
.license-date{font-size:10px;color:#8e8e9e}
.license-key{font-family:monospace;font-size:10px;background:rgba(0,0,0,.3);padding:8px;border-radius:8px;margin:8px 0;cursor:pointer}
.profile-card{background:rgba(255,255,255,.04);border-radius:20px;padding:24px;text-align:center;margin-bottom:16px}
.profile-avatar{width:64px;height:64px;background:linear-gradient(135deg,#8b5cf6,#ec4899);border-radius:20px;display:flex;align-items:center;justify-content:center;font-size:32px;margin:0 auto 12px}
.profile-name{font-size:18px;font-weight:700}
.profile-stats{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:16px}
.stat-box{background:rgba(255,255,255,.04);border-radius:12px;padding:12px}
.stat-num{font-size:20px;font-weight:700;color:#a78bfa}
.toast{position:fixed;bottom:100px;left:16px;right:16px;background:rgba(0,0,0,.8);border-radius:12px;padding:12px;text-align:center;z-index:1100;animation:fadeIn .3s}
@keyframes fadeIn{from{opacity:0;transform:translateY(20px)}to{opacity:1;transform:translateY(0)}}
.nav{position:fixed;bottom:0;left:0;right:0;background:rgba(10,10,22,.95);backdrop-filter:blur(20px);border-top:1px solid rgba(255,255,255,.06);display:flex;justify-content:space-around;padding:8px 16px 12px;max-width:480px;margin:0 auto}
.nav-btn{background:none;border:none;color:#8e8e9e;font-size:10px;padding:6px 16px;border-radius:12px;cursor:pointer}
.nav-btn.active{color:#a78bfa;background:rgba(139,92,246,.1)}
.page{display:none}
.page.active{display:block}
.empty{text-align:center;padding:40px 20px}
.product-summary{text-align:center;margin-bottom:16px}
.ps-icon{font-size:48px;margin-bottom:8px}
.ps-price{font-size:28px;font-weight:700;color:#f59e0b}
.status-view{text-align:center;padding:20px}
.spin{animation:spin 1s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>
<div class="app">
<div class="header">
<div class="logo">🎯</div>
<h1>AimNoob</h1>
<div class="tagline">Premium Cheat • Standoff 2</div>
</div>
<div class="status-bar"><span class="status-dot"></span><span>v0.37.1 • Online • Undetected</span></div>
<div class="tabs">
<button class="tab active" data-tab="shop">🛒 Магазин</button>
<button class="tab" data-tab="keys">🔑 Ключи</button>
<button class="tab" data-tab="profile">👤 Профиль</button>
</div>
<div id="page-shop" class="page active"></div>
<div id="page-keys" class="page"></div>
<div id="page-profile" class="page"></div>
</div>
<div class="nav">
<button class="nav-btn active" data-tab="shop">🛒</button>
<button class="nav-btn" data-tab="keys">🔑</button>
<button class="nav-btn" data-tab="profile">👤</button>
</div>
<div class="modal-overlay" id="modal">
<div class="modal">
<div class="modal-handle"></div>
<div class="modal-head"><div class="modal-title" id="mTitle">Оплата</div><button class="modal-close" id="mClose">×</button></div>
<div class="modal-body" id="mBody"></div>
</div>
</div>
<script>
const tg = window.Telegram.WebApp;
tg.expand();
tg.enableClosingConfirmation();
const user = tg.initDataUnsafe?.user || {id: Date.now(), first_name: 'Гость', username: 'user'};
let licenses = JSON.parse(localStorage.getItem('aimnoob_licenses') || '[]');
let selected = null;
const DOWNLOAD_URL = '{download_url}';
const SUPPORT = '{support}';
const API = window.location.origin + '/api';

const PRODUCTS = {
    android: [
        {id: 'apk_week', name: 'Android', period: 'НЕДЕЛЯ', duration: '7 дней', price: 150, stars: 150, gold: 150, nft: 150, usdt: 1.5, icon: '📱', features: ['AimBot', 'WallHack', 'ESP']},
        {id: 'apk_month', name: 'Android', period: 'МЕСЯЦ', duration: '30 дней', price: 350, stars: 350, gold: 350, nft: 350, usdt: 3.5, icon: '📱', features: ['AimBot', 'WallHack', 'ESP', 'Anti-Ban']},
        {id: 'apk_forever', name: 'Android', period: 'НАВСЕГДА', duration: '∞', price: 800, stars: 800, gold: 800, nft: 800, usdt: 8, icon: '📱', features: ['AimBot', 'WallHack', 'ESP', 'Anti-Ban', 'Updates']}
    ],
    ios: [
        {id: 'ios_week', name: 'iOS', period: 'НЕДЕЛЯ', duration: '7 дней', price: 300, stars: 300, gold: 300, nft: 300, usdt: 3, icon: '🍎', features: ['AimBot', 'WallHack', 'ESP']},
        {id: 'ios_month', name: 'iOS', period: 'МЕСЯЦ', duration: '30 дней', price: 450, stars: 450, gold: 450, nft: 450, usdt: 4.5, icon: '🍎', features: ['AimBot', 'WallHack', 'ESP', 'Anti-Ban']},
        {id: 'ios_forever', name: 'iOS', period: 'НАВСЕГДА', duration: '∞', price: 850, stars: 850, gold: 850, nft: 850, usdt: 8.5, icon: '🍎', features: ['AimBot', 'WallHack', 'ESP', 'Anti-Ban', 'Updates']}
    ]
};

function switchTab(tab) {
    document.querySelectorAll('.tab, .nav-btn').forEach(btn => btn.classList.toggle('active', btn.dataset.tab === tab));
    document.querySelectorAll('.page').forEach(page => page.classList.toggle('active', page.id === `page-${tab}`));
    if (tab === 'shop') renderShop();
    else if (tab === 'keys') renderKeys();
    else if (tab === 'profile') renderProfile();
}

function renderShop() {
    let html = '';
    for (const [platform, products] of Object.entries(PRODUCTS)) {
        const icon = platform === 'android' ? '📱' : '🍎';
        const name = platform === 'android' ? 'Android' : 'iOS';
        html += `<div class="section"><div class="section-title">${icon} ${name}</div><div class="cards">`;
        products.forEach((p, i) => {
            const ppd = p.duration !== '∞' ? `${Math.round(p.price / parseInt(p.duration))}₽/день` : 'лучшая цена';
            if (i === 2) {
                html += `<div class="card card-full" data-id="${p.id}"><div class="card-body"><div class="card-icon">${p.icon}</div><div class="card-info"><div class="card-name">${p.name} ${p.period}</div><div class="card-period">${p.duration} • ${ppd}</div></div><div class="card-right"><div class="card-price">${p.price}₽</div></div><button class="card-btn buy-btn" data-id="${p.id}">💎 Купить</button></div></div>`;
            } else {
                html += `<div class="card" data-id="${p.id}"><div class="card-icon">${p.icon}</div><div class="card-name">${p.name}</div><div class="card-period">${p.period} • ${p.duration}</div><div class="card-price">${p.price}₽</div><div class="price-per">${ppd}</div><div class="card-features">${p.features.map(f => `<span class="feat">${f}</span>`).join('')}</div><button class="card-btn buy-btn" data-id="${p.id}">🛒 Купить</button></div>`;
            }
        });
        html += `</div></div>`;
    }
    document.getElementById('page-shop').innerHTML = html;
    document.querySelectorAll('.buy-btn').forEach(btn => btn.addEventListener('click', (e) => { e.stopPropagation(); openPayModal(findProduct(btn.dataset.id)); }));
}

function findProduct(id) {
    return [...PRODUCTS.android, ...PRODUCTS.ios].find(p => p.id === id);
}

function openModal(title) {
    document.getElementById('mTitle').textContent = title;
    document.getElementById('modal').classList.add('open');
}

function closeModal() {
    document.getElementById('modal').classList.remove('open');
}

function openPayModal(product) {
    selected = product;
    openModal('Способ оплаты');
    const mb = document.getElementById('mBody');
    mb.innerHTML = `<div class="product-summary"><div class="ps-icon">${product.icon}</div><div class="ps-name">${product.name} • ${product.period}</div><div class="ps-period">${product.duration}</div><div class="ps-price">${product.price} ₽</div></div>
    <div class="pay-list">
        <div class="pay-item" data-method="yoomoney"><div class="pay-left"><div class="pay-icon">💳</div><div><div class="pay-name">Картой</div><div class="pay-desc">Visa, MC, Мир, SBP</div></div></div><div class="pay-amount">${product.price} ₽</div></div>
        <div class="pay-item" data-method="stars"><div class="pay-left"><div class="pay-icon">⭐</div><div><div class="pay-name">Telegram Stars</div><div class="pay-desc">Встроенные платежи</div></div></div><div class="pay-amount">${product.stars} ⭐</div></div>
        <div class="pay-item" data-method="crypto"><div class="pay-left"><div class="pay-icon">₿</div><div><div class="pay-name">Крипта</div><div class="pay-desc">USDT, BTC, ETH, TON</div></div></div><div class="pay-amount">${product.usdt} USDT</div></div>
        <div class="pay-item" data-method="gold"><div class="pay-left"><div class="pay-icon">💰</div><div><div class="pay-name">GOLD</div><div class="pay-desc">Игровая валюта</div></div></div><div class="pay-amount">${product.gold} 🪙</div></div>
        <div class="pay-item" data-method="nft"><div class="pay-left"><div class="pay-icon">🎨</div><div><div class="pay-name">NFT</div><div class="pay-desc">Коллекционные</div></div></div><div class="pay-amount">${product.nft} 🖼️</div></div>
    </div>`;
    mb.querySelectorAll('.pay-item').forEach(el => el.addEventListener('click', () => processPayment(el.dataset.method)));
}

function processPayment(method) {
    const mb = document.getElementById('mBody');
    mb.innerHTML = '<div class="status-view"><div class="ps-icon spin">⏳</div><div>Создание платежа...</div></div>';
    fetch(API + '/create_payment', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({product_id: selected.id, method: method, user_id: user.id, user_name: user.first_name, init_data: tg.initData})
    }).then(r => r.json()).then(res => {
        if (!res.success) throw new Error(res.error || 'Ошибка');
        if (method === 'yoomoney') showPayment(res.payment_url, res.order_id, '💳', `${selected.price} ₽`, 'ym');
        else if (method === 'stars') {
            mb.innerHTML = `<div class="status-view"><div class="ps-icon">⭐</div><div>${selected.stars} Stars</div><div class="status-desc">Оплатите в боте</div><button class="action-btn" id="starsBtn">⭐ Оплатить в боте</button></div>`;
            document.getElementById('starsBtn').addEventListener('click', () => tg.openTelegramLink(`https://t.me/aimnoob_bot?start=buy_stars_${selected.id}`));
        } else if (method === 'crypto') showPayment(res.payment_url, res.order_id, '₿', `${selected.usdt} USDT`, 'cr', res.invoice_id);
        else showManual(method, res.order_id);
    }).catch(e => { toast(e.message, 'error'); setTimeout(() => openPayModal(selected), 1200); });
}

function showPayment(url, oid, icon, amount, type, invoiceId) {
    const mb = document.getElementById('mBody');
    mb.innerHTML = `<div class="status-view"><div class="ps-icon">${icon}</div><div>${amount}</div><div class="status-desc">Заказ #${oid.slice(-8)}</div><button class="action-btn" id="payBtn">💳 Оплатить</button><button class="action-btn secondary" id="checkBtn" style="margin-top:8px">✅ Проверить оплату</button></div>`;
    document.getElementById('payBtn').addEventListener('click', () => window.open(url, '_blank'));
    document.getElementById('checkBtn').addEventListener('click', () => type === 'ym' ? checkPayment(oid) : checkCrypto(oid, invoiceId));
}

function showManual(method, oid) {
    const names = {gold: 'GOLD', nft: 'NFT'};
    const amounts = {gold: selected.gold, nft: selected.nft};
    const icons = {gold: '💰', nft: '🎨'};
    const msg = `Привет! Хочу купить AimNoob ${selected.name} на ${selected.period} за ${amounts[method]} ${names[method]}`;
    const mb = document.getElementById('mBody');
    mb.innerHTML = `<div class="status-view"><div class="ps-icon">${icons[method]}</div><div>${amounts[method]} ${names[method]}</div><button class="action-btn" id="msgBtn">💬 Написать</button><button class="action-btn secondary" id="doneBtn">✅ Я написал</button></div>`;
    document.getElementById('msgBtn').addEventListener('click', () => window.open(`https://t.me/${SUPPORT}?text=${encodeURIComponent(msg)}`, '_blank'));
    document.getElementById('doneBtn').addEventListener('click', () => { closeModal(); toast('Заказ создан!'); });
}

function checkPayment(oid) {
    const mb = document.getElementById('mBody');
    mb.innerHTML = '<div class="status-view"><div class="ps-icon spin">⏳</div><div>Проверка...</div></div>';
    fetch(API + '/check_payment', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({order_id: oid})})
        .then(r => r.json()).then(res => { if (res.paid) showSuccess(res.license_key); else { mb.innerHTML = '<div class="status-view"><div>⏳ Не найден</div><button class="action-btn secondary" id="retryBtn">🔄 Повторить</button></div>'; document.getElementById('retryBtn').addEventListener('click', () => checkPayment(oid)); } })
        .catch(() => toast('Ошибка', 'error'));
}

function checkCrypto(oid, iid) {
    const mb = document.getElementById('mBody');
    mb.innerHTML = '<div class="status-view"><div class="ps-icon spin">⏳</div><div>Проверка...</div></div>';
    fetch(API + '/check_crypto', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({invoice_id: iid, order_id: oid})})
        .then(r => r.json()).then(res => { if (res.paid) showSuccess(res.license_key); else { mb.innerHTML = '<div class="status-view"><div>⏳ В обработке</div><button class="action-btn secondary" id="retryBtn">🔄 Повторить</button></div>'; document.getElementById('retryBtn').addEventListener('click', () => checkCrypto(oid, iid)); } })
        .catch(() => toast('Ошибка', 'error'));
}

function showSuccess(key) {
    licenses.push({key: key, product: `${selected.name} • ${selected.period}`, date: new Date().toISOString()});
    localStorage.setItem('aimnoob_licenses', JSON.stringify(licenses));
    const mb = document.getElementById('mBody');
    document.getElementById('mTitle').textContent = 'Успех!';
    mb.innerHTML = `<div class="status-view"><div class="ps-icon">✅</div><div>Оплата подтверждена!</div><div class="key-box" id="keyBox">🔑 ${key}</div><button class="action-btn" id="dlBtn">📥 Скачать AimNoob</button><button class="action-btn secondary" id="keysBtn">🔑 Мои ключи</button></div>`;
    document.getElementById('keyBox').addEventListener('click', () => copyKey(key));
    document.getElementById('dlBtn').addEventListener('click', () => window.open(DOWNLOAD_URL, '_blank'));
    document.getElementById('keysBtn').addEventListener('click', () => { closeModal(); switchTab('keys'); });
}

function renderKeys() {
    const el = document.getElementById('page-keys');
    if (!licenses.length) {
        el.innerHTML = `<div class="empty"><div>🔑</div><div>Нет активных ключей</div><button class="action-btn" id="goShop">🛒 В магазин</button></div>`;
        document.getElementById('goShop')?.addEventListener('click', () => switchTab('shop'));
        return;
    }
    let html = '<div class="section"><div class="section-title">🔑 Мои лицензии</div>';
    licenses.forEach(l => {
        html += `<div class="license-card"><div class="license-header"><div class="license-icon">🎯</div><div><div class="license-name">${l.product}</div><div class="license-date">${new Date(l.date).toLocaleDateString('ru-RU')}</div></div></div><div class="license-key" data-key="${l.key}">${l.key}</div><button class="action-btn secondary" data-key="${l.key}">📋 Скопировать</button></div>`;
    });
    html += '</div>';
    el.innerHTML = html;
    el.querySelectorAll('.license-key, .action-btn').forEach(btn => btn.addEventListener('click', () => copyKey(btn.dataset.key)));
}

function renderProfile() {
    const el = document.getElementById('page-profile');
    el.innerHTML = `<div class="profile-card"><div class="profile-avatar">🎯</div><div class="profile-name">${user.first_name || 'Пользователь'}</div><div class="profile-stats"><div class="stat-box"><div class="stat-num">${licenses.length}</div><div>Ключей</div></div><div class="stat-box"><div class="stat-num">0.37</div><div>Версия</div></div></div></div><button class="action-btn" id="supportBtn">💬 Поддержка</button><button class="action-btn secondary" id="downloadBtn">📥 Скачать</button>`;
    document.getElementById('supportBtn')?.addEventListener('click', () => window.open(`https://t.me/${SUPPORT}`, '_blank'));
    document.getElementById('downloadBtn')?.addEventListener('click', () => window.open(DOWNLOAD_URL, '_blank'));
}

function copyKey(key) {
    navigator.clipboard.writeText(key).then(() => toast('Ключ скопирован!')).catch(() => toast('Ошибка', 'error'));
}

function toast(msg, type) {
    const t = document.createElement('div');
    t.className = 'toast';
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 2000);
}

document.querySelectorAll('.tab, .nav-btn').forEach(btn => btn.addEventListener('click', () => switchTab(btn.dataset.tab)));
document.getElementById('mClose').addEventListener('click', closeModal);
document.getElementById('modal').addEventListener('click', (e) => { if (e.target === document.getElementById('modal')) closeModal(); });

renderShop();
tg.ready();
</script>
</body>
</html>"""


def get_miniapp_html():
    return MINIAPP_HTML.format(
        download_url=Config.DOWNLOAD_URL,
        support=Config.SUPPORT_CHAT_USERNAME
    )


# ========== WEB SERVER API ==========
class WebHandlers:
    @staticmethod
    async def handle_miniapp(request):
        return web.Response(
            text=get_miniapp_html(),
            content_type='text/html',
            charset='utf-8'
        )

    @staticmethod
    async def handle_health(request):
        stats = await orders.get_stats()
        return web.json_response({
            "status": "ok",
            "pending": stats["pending"],
            "confirmed": stats["confirmed"],
            "uptime": time.time()
        })

    @staticmethod
    async def handle_create_payment(request):
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"success": False, "error": "Invalid JSON"}, status=400)

        product_id = data.get('product_id')
        method = data.get('method')
        user_id = data.get('user_id')
        user_name = data.get('user_name', 'MiniApp User')
        init_data = data.get('init_data', '')

        if init_data:
            validated_user = validate_telegram_init_data(init_data, Config.BOT_TOKEN)
            if validated_user:
                user_id = validated_user.get('id', user_id)
                user_name = validated_user.get('first_name', user_name)

        if not product_id or not method or not user_id:
            return web.json_response({"success": False, "error": "Missing fields"}, status=400)

        product = find_product_by_id(product_id)
        if not product:
            return web.json_response({"success": False, "error": "Product not found"}, status=404)

        if method not in ('yoomoney', 'crypto', 'stars', 'gold', 'nft'):
            return web.json_response({"success": False, "error": "Unknown method"}, status=400)

        order_id = generate_order_id()

        if method == 'yoomoney':
            if not Config.YOOMONEY_WALLET:
                return web.json_response({"success": False, "error": "Card payments unavailable"})
            amount = product['price']
            product_desc = "{} ({})".format(product['name'], product['duration'])
            payment_url = create_payment_link(amount, order_id, product_desc)
            await orders.add_pending(order_id, {
                "user_id": user_id, "user_name": user_name, "product": product,
                "amount": amount, "currency": "₽", "payment_method": "Картой",
                "status": "pending", "created_at": time.time()
            })
            return web.json_response({"success": True, "payment_url": payment_url, "order_id": order_id})

        elif method == 'crypto':
            if not Config.CRYPTOBOT_TOKEN:
                return web.json_response({"success": False, "error": "Crypto unavailable"})
            amount_usdt = product['price_crypto_usdt']
            desc = "AimNoob {} ({})".format(product['name'], product['duration'])
            invoice_data = await CryptoBotService.create_invoice(amount_usdt, order_id, desc)
            if not invoice_data:
                return web.json_response({"success": False, "error": "Invoice creation failed"})
            await orders.add_pending(order_id, {
                "user_id": user_id, "user_name": user_name, "product": product,
                "amount": amount_usdt, "currency": "USDT", "payment_method": "CryptoBot",
                "status": "pending", "invoice_id": invoice_data["invoice_id"],
                "created_at": time.time()
            })
            return web.json_response({
                "success": True, "payment_url": invoice_data["pay_url"],
                "invoice_id": invoice_data["invoice_id"], "order_id": order_id
            })

        elif method == 'stars':
            await orders.add_pending(order_id, {
                "user_id": user_id, "user_name": user_name, "product": product,
                "amount": product['price_stars'], "currency": "⭐",
                "payment_method": "Telegram Stars",
                "status": "pending", "created_at": time.time()
            })
            return web.json_response({"success": True, "order_id": order_id, "method": "stars"})

        else:
            price_key = "price_{}".format(method)
            await orders.add_pending(order_id, {
                "user_id": user_id, "user_name": user_name, "product": product,
                "amount": product.get(price_key, 0), "currency": method.upper(),
                "payment_method": method.upper(), "status": "pending",
                "created_at": time.time()
            })
            return web.json_response({"success": True, "order_id": order_id, "method": method})

    @staticmethod
    async def handle_check_payment(request):
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"paid": False, "error": "Invalid JSON"})

        order_id = data.get('order_id')
        if not order_id:
            return web.json_response({"paid": False, "error": "No order_id"})

        confirmed = await orders.get_confirmed(order_id)
        if confirmed:
            return web.json_response({"paid": True, "license_key": confirmed.get('license_key', '')})

        order = await orders.get_pending(order_id)
        if not order:
            return web.json_response({"paid": False, "error": "Order not found"})

        payment_found = False
        for _ in range(3):
            payment_found = await YooMoneyService.check_payment(
                order_id, order["amount"], order.get("created_at", time.time())
            )
            if payment_found:
                break
            await asyncio.sleep(3)

        if payment_found:
            await process_successful_payment(order_id, "MiniApp")
            cp = await orders.get_confirmed(order_id)
            lk = cp.get('license_key', '') if cp else ''
            return web.json_response({"paid": True, "license_key": lk})

        return web.json_response({"paid": False})

    @staticmethod
    async def handle_check_crypto(request):
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"paid": False, "error": "Invalid JSON"})

        invoice_id = data.get('invoice_id')
        order_id = data.get('order_id')
        if not invoice_id or not order_id:
            return web.json_response({"paid": False, "error": "Missing fields"})

        is_paid = await CryptoBotService.check_invoice(invoice_id)
        if is_paid:
            await process_successful_payment(order_id, "MiniApp CryptoBot")
            cp = await orders.get_confirmed(order_id)
            lk = cp.get('license_key', '') if cp else ''
            return web.json_response({"paid": True, "license_key": lk})

        return web.json_response({"paid": False})


# ========== CORS MIDDLEWARE ==========
@web.middleware
async def cors_middleware(request, handler):
    if request.method == 'OPTIONS':
        response = web.Response(status=200)
    else:
        try:
            response = await handler(request)
        except web.HTTPException as e:
            response = e
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response


# ========== ЗАПУСК ==========
async def main():
    logger.info("=" * 50)
    logger.info("AIMNOOB PREMIUM SHOP BOT")
    logger.info("=" * 50)
    logger.info("ADMIN_IDS: %s", Config.ADMIN_IDS)
    logger.info("MINIAPP_URL: %s", Config.MINIAPP_URL)
    logger.info("WEB_PORT: %s", Config.WEB_PORT)

    runner = None

    try:
        me = await bot.get_me()
        logger.info("Bot: @%s", me.username)

        balance = await YooMoneyService.get_balance()
        if balance is not None:
            logger.info("YooMoney connected (balance: %s RUB)", balance)

        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="🎮 Магазин",
                    web_app=WebAppInfo(url=Config.MINIAPP_URL)
                )
            )
            logger.info("Menu button set")
        except Exception as e:
            logger.warning("Could not set menu button: %s", e)

        app = web.Application(middlewares=[cors_middleware])
        app.router.add_get('/', WebHandlers.handle_miniapp)
        app.router.add_get('/health', WebHandlers.handle_health)
        app.router.add_post('/api/create_payment', WebHandlers.handle_create_payment)
        app.router.add_post('/api/check_payment', WebHandlers.handle_check_payment)
        app.router.add_post('/api/check_crypto', WebHandlers.handle_check_crypto)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', Config.WEB_PORT)
        await site.start()
        logger.info("Web server started on port %s", Config.WEB_PORT)

        logger.info("Bot starting polling...")
        await dp.start_polling(bot)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.error("Fatal error: %s", e)
        import traceback
        traceback.print_exc()
    finally:
        if runner:
            await runner.cleanup()
        await bot.session.close()
        logger.info("Bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
