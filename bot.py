# bot.py
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
        "name": "\U0001f4f1 AimNoob Android",
        "period_text": "\u041d\u0415\u0414\u0415\u041b\u042e",
        "price": 150,
        "price_stars": 350,
        "price_gold": 350,
        "price_nft": 250,
        "price_crypto_usdt": 2,
        "platform": "Android",
        "period": "\u041d\u0415\u0414\u0415\u041b\u042e",
        "platform_code": "apk",
        "emoji": "\U0001f4f1",
        "duration": "7 \u0434\u043d\u0435\u0439"
    },
    "apk_month": {
        "name": "\U0001f4f1 AimNoob Android",
        "period_text": "\u041c\u0415\u0421\u042f\u0426",
        "price": 350,
        "price_stars": 800,
        "price_gold": 800,
        "price_nft": 600,
        "price_crypto_usdt": 5,
        "platform": "Android",
        "period": "\u041c\u0415\u0421\u042f\u0426",
        "platform_code": "apk",
        "emoji": "\U0001f4f1",
        "duration": "30 \u0434\u043d\u0435\u0439"
    },
    "apk_forever": {
        "name": "\U0001f4f1 AimNoob Android",
        "period_text": "\u041d\u0410\u0412\u0421\u0415\u0413\u0414\u0410",
        "price": 800,
        "price_stars": 1800,
        "price_gold": 1800,
        "price_nft": 1400,
        "price_crypto_usdt": 12,
        "platform": "Android",
        "period": "\u041d\u0410\u0412\u0421\u0415\u0413\u0414\u0410",
        "platform_code": "apk",
        "emoji": "\U0001f4f1",
        "duration": "\u041d\u0430\u0432\u0441\u0435\u0433\u0434\u0430"
    },
    "ios_week": {
        "name": "\U0001f34e AimNoob iOS",
        "period_text": "\u041d\u0415\u0414\u0415\u041b\u042e",
        "price": 300,
        "price_stars": 700,
        "price_gold": 700,
        "price_nft": 550,
        "price_crypto_usdt": 4,
        "platform": "iOS",
        "period": "\u041d\u0415\u0414\u0415\u041b\u042e",
        "platform_code": "ios",
        "emoji": "\U0001f34e",
        "duration": "7 \u0434\u043d\u0435\u0439"
    },
    "ios_month": {
        "name": "\U0001f34e AimNoob iOS",
        "period_text": "\u041c\u0415\u0421\u042f\u0426",
        "price": 450,
        "price_stars": 1000,
        "price_gold": 1000,
        "price_nft": 800,
        "price_crypto_usdt": 6,
        "platform": "iOS",
        "period": "\u041c\u0415\u0421\u042f\u0426",
        "platform_code": "ios",
        "emoji": "\U0001f34e",
        "duration": "30 \u0434\u043d\u0435\u0439"
    },
    "ios_forever": {
        "name": "\U0001f34e AimNoob iOS",
        "period_text": "\u041d\u0410\u0412\u0421\u0415\u0413\u0414\u0410",
        "price": 850,
        "price_stars": 2000,
        "price_gold": 2000,
        "price_nft": 1600,
        "price_crypto_usdt": 12,
        "platform": "iOS",
        "period": "\u041d\u0410\u0412\u0421\u0415\u0413\u0414\u0410",
        "platform_code": "ios",
        "emoji": "\U0001f34e",
        "duration": "\u041d\u0430\u0432\u0441\u0435\u0433\u0434\u0430"
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
        [InlineKeyboardButton(text="\U0001f4f1 Android", callback_data="platform_apk")],
        [InlineKeyboardButton(text="\U0001f34e iOS", callback_data="platform_ios")],
        [InlineKeyboardButton(
            text="\U0001f3ae \u041e\u0442\u043a\u0440\u044b\u0442\u044c \u043c\u0430\u0433\u0430\u0437\u0438\u043d",
            web_app=WebAppInfo(url=Config.MINIAPP_URL)
        )],
        [InlineKeyboardButton(text="\u2139\ufe0f \u041e \u043f\u0440\u043e\u0433\u0440\u0430\u043c\u043c\u0435", callback_data="about")],
        [InlineKeyboardButton(
            text="\U0001f4ac \u041f\u043e\u0434\u0434\u0435\u0440\u0436\u043a\u0430",
            url="https://t.me/{}".format(Config.SUPPORT_CHAT_USERNAME)
        )]
    ])


def subscription_keyboard(platform):
    prices = {
        "apk": [
            ("\u26a1 \u041d\u0415\u0414\u0415\u041b\u042f \u2014 150\u20bd", "sub_apk_week"),
            ("\U0001f525 \u041c\u0415\u0421\u042f\u0426 \u2014 350\u20bd", "sub_apk_month"),
            ("\U0001f48e \u041d\u0410\u0412\u0421\u0415\u0413\u0414\u0410 \u2014 800\u20bd", "sub_apk_forever"),
        ],
        "ios": [
            ("\u26a1 \u041d\u0415\u0414\u0415\u041b\u042f \u2014 300\u20bd", "sub_ios_week"),
            ("\U0001f525 \u041c\u0415\u0421\u042f\u0426 \u2014 450\u20bd", "sub_ios_month"),
            ("\U0001f48e \u041d\u0410\u0412\u0421\u0415\u0413\u0414\u0410 \u2014 850\u20bd", "sub_ios_forever"),
        ]
    }
    buttons = [
        [InlineKeyboardButton(text=text, callback_data=cb)]
        for text, cb in prices.get(platform, [])
    ]
    buttons.append([InlineKeyboardButton(text="\u25c0\ufe0f \u041d\u0430\u0437\u0430\u0434", callback_data="back_to_platform")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def payment_methods_keyboard(product):
    pc = product['platform_code']
    p = product['period']
    buttons = [
        [InlineKeyboardButton(text="\U0001f4b3 \u041a\u0430\u0440\u0442\u043e\u0439", callback_data="pay_yoomoney_{}_{}".format(pc, p))],
        [InlineKeyboardButton(text="\u2b50 Telegram Stars", callback_data="pay_stars_{}_{}".format(pc, p))],
        [InlineKeyboardButton(text="\u20bf \u041a\u0440\u0438\u043f\u0442\u043e\u0431\u043e\u0442", callback_data="pay_crypto_{}_{}".format(pc, p))],
        [InlineKeyboardButton(text="\U0001f4b0 GOLD", callback_data="pay_gold_{}_{}".format(pc, p))],
        [InlineKeyboardButton(text="\U0001f3a8 NFT", callback_data="pay_nft_{}_{}".format(pc, p))],
        [InlineKeyboardButton(text="\u25c0\ufe0f \u041d\u0430\u0437\u0430\u0434", callback_data="back_to_subscription")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def payment_keyboard(payment_url, order_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\U0001f4b3 \u041e\u043f\u043b\u0430\u0442\u0438\u0442\u044c \u043a\u0430\u0440\u0442\u043e\u0439", url=payment_url)],
        [InlineKeyboardButton(text="\u2705 \u041f\u0440\u043e\u0432\u0435\u0440\u0438\u0442\u044c \u043e\u043f\u043b\u0430\u0442\u0443", callback_data="checkym_{}".format(order_id))],
        [InlineKeyboardButton(text="\u274c \u041e\u0442\u043c\u0435\u043d\u0430", callback_data="restart")]
    ])


def crypto_payment_keyboard(invoice_url, order_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\u20bf \u041e\u043f\u043b\u0430\u0442\u0438\u0442\u044c \u043a\u0440\u0438\u043f\u0442\u043e\u0439", url=invoice_url)],
        [InlineKeyboardButton(text="\u2705 \u041f\u0440\u043e\u0432\u0435\u0440\u0438\u0442\u044c \u043f\u043b\u0430\u0442\u0435\u0436", callback_data="checkcr_{}".format(order_id))],
        [InlineKeyboardButton(text="\u274c \u041e\u0442\u043c\u0435\u043d\u0430", callback_data="restart")]
    ])


def support_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="\U0001f4ac \u041f\u043e\u0434\u0434\u0435\u0440\u0436\u043a\u0430",
            url="https://t.me/{}".format(Config.SUPPORT_CHAT_USERNAME)
        )],
        [InlineKeyboardButton(text="\U0001f310 \u0421\u0430\u0439\u0442", url=Config.SHOP_URL)],
        [InlineKeyboardButton(text="\U0001f504 \u041d\u043e\u0432\u0430\u044f \u043f\u043e\u043a\u0443\u043f\u043a\u0430", callback_data="restart")]
    ])


def download_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\U0001f4e5 \u0421\u043a\u0430\u0447\u0430\u0442\u044c AimNoob", url=Config.DOWNLOAD_URL)],
        [InlineKeyboardButton(
            text="\U0001f4ac \u041f\u043e\u0434\u0434\u0435\u0440\u0436\u043a\u0430",
            url="https://t.me/{}".format(Config.SUPPORT_CHAT_USERNAME)
        )],
        [InlineKeyboardButton(text="\U0001f310 \u0421\u0430\u0439\u0442", url=Config.SHOP_URL)],
        [InlineKeyboardButton(text="\U0001f504 \u041d\u043e\u0432\u0430\u044f \u043f\u043e\u043a\u0443\u043f\u043a\u0430", callback_data="restart")]
    ])


def about_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\u25c0\ufe0f \u041d\u0430\u0437\u0430\u0434", callback_data="back_to_platform")]
    ])


def admin_confirm_keyboard(order_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\u2705 \u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044c", callback_data="admin_confirm_{}".format(order_id))],
        [InlineKeyboardButton(text="\u274c \u041e\u0442\u043a\u043b\u043e\u043d\u0438\u0442\u044c", callback_data="admin_reject_{}".format(order_id))]
    ])


def manual_payment_keyboard(support_url, sent_callback):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\U0001f4ac \u041f\u0435\u0440\u0435\u0439\u0442\u0438 \u043a \u043e\u043f\u043b\u0430\u0442\u0435", url=support_url)],
        [InlineKeyboardButton(text="\u2705 \u042f \u043d\u0430\u043f\u0438\u0441\u0430\u043b", callback_data=sent_callback)],
        [InlineKeyboardButton(text="\u274c \u041e\u0442\u043c\u0435\u043d\u0430", callback_data="restart")]
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
        "\U0001f389 <b>\u041e\u043f\u043b\u0430\u0442\u0430 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0430!</b>\n\n"
        "\u2728 \u0414\u043e\u0431\u0440\u043e \u043f\u043e\u0436\u0430\u043b\u043e\u0432\u0430\u0442\u044c \u0432 AimNoob!\n\n"
        "\U0001f4e6 <b>\u0412\u0430\u0448\u0430 \u043f\u043e\u043a\u0443\u043f\u043a\u0430:</b>\n"
        "{emoji} {name}\n"
        "\u23f1\ufe0f \u0421\u0440\u043e\u043a: {duration}\n"
        "\U0001f50d \u041c\u0435\u0442\u043e\u0434: {source}\n\n"
        "\U0001f511 <b>\u0412\u0430\u0448 \u043b\u0438\u0446\u0435\u043d\u0437\u0438\u043e\u043d\u043d\u044b\u0439 \u043a\u043b\u044e\u0447:</b>\n"
        "<code>{key}</code>\n\n"
        "\U0001f4e5 <b>\u0421\u043a\u0430\u0447\u0438\u0432\u0430\u043d\u0438\u0435:</b>\n"
        "\U0001f447 \u041d\u0430\u0436\u043c\u0438\u0442\u0435 \u043a\u043d\u043e\u043f\u043a\u0443 \u043d\u0438\u0436\u0435 \u0434\u043b\u044f \u0437\u0430\u0433\u0440\u0443\u0437\u043a\u0438\n\n"
        "\U0001f4ab <b>\u0410\u043a\u0442\u0438\u0432\u0430\u0446\u0438\u044f:</b>\n"
        "1\ufe0f\u20e3 \u0421\u043a\u0430\u0447\u0430\u0439\u0442\u0435 \u0444\u0430\u0439\u043b \u043f\u043e \u043a\u043d\u043e\u043f\u043a\u0435 \u043d\u0438\u0436\u0435\n"
        "2\ufe0f\u20e3 \u0423\u0441\u0442\u0430\u043d\u043e\u0432\u0438\u0442\u0435 \u043f\u0440\u0438\u043b\u043e\u0436\u0435\u043d\u0438\u0435\n"
        "3\ufe0f\u20e3 \u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u043a\u043b\u044e\u0447 \u043f\u0440\u0438 \u0437\u0430\u043f\u0443\u0441\u043a\u0435\n"
        "4\ufe0f\u20e3 \u041d\u0430\u0441\u043b\u0430\u0436\u0434\u0430\u0439\u0442\u0435\u0441\u044c \u0438\u0433\u0440\u043e\u0439! \U0001f3ae\n\n"
        "\U0001f4ac \u041f\u043e\u0434\u0434\u0435\u0440\u0436\u043a\u0430: @{support}"
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
    order_currency = order.get('currency', '\u20bd')
    now_str = datetime.now().strftime('%d.%m.%Y %H:%M')

    admin_text = (
        "\U0001f48e <b>\u041d\u041e\u0412\u0410\u042f \u041f\u0420\u041e\u0414\u0410\u0416\u0410 ({source})</b>\n\n"
        "\U0001f464 {user_name}\n"
        "\U0001f194 {user_id}\n"
        "\U0001f4e6 {{product_name} ({duration})\n"
        "\U0001f4b0 {amount} {currency}\n"
        "\U0001f511 <code>{key}</code>\n"
        "\U0001f4c5 {now}"
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
        "\U0001f514 <b>\u041d\u041e\u0412\u042b\u0419 \u0417\u0410\u041a\u0410\u0417</b>\n\n"
        "\U0001f464 {full_name}\n"
        "\U0001f194 <code>{user_id}</code>\n"
        "\U0001f4e6 {{product_name} ({duration})\n"
        "\U0001f4b0 {price}\n"
        "\U0001f4b3 {payment_method}\n"
        "\U0001f194 <code>{order_id}</code>\n\n"
        "\U0001f4c5 {now}"
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
        "\U0001f3af <b>AimNoob \u2014 \u041f\u0440\u0435\u043c\u0438\u0443\u043c \u0447\u0438\u0442 \u0434\u043b\u044f Standoff 2</b>\n\n"
        "\u2728 <b>\u0412\u043e\u0437\u043c\u043e\u0436\u043d\u043e\u0441\u0442\u0438:</b>\n"
        "\U0001f6e1\ufe0f \u041f\u0440\u043e\u0434\u0432\u0438\u043d\u0443\u0442\u0430\u044f \u0437\u0430\u0449\u0438\u0442\u0430 \u043e\u0442 \u0431\u0430\u043d\u043e\u0432\n"
        "\U0001f3af \u0423\u043c\u043d\u044b\u0439 AimBot \u0441 \u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0430\u043c\u0438\n"
        "\U0001f441\ufe0f WallHack \u0438 ESP\n"
        "\U0001f4ca \u041f\u043e\u043b\u043d\u0430\u044f \u0438\u043d\u0444\u043e\u0440\u043c\u0430\u0446\u0438\u044f \u043e \u043f\u0440\u043e\u0442\u0438\u0432\u043d\u0438\u043a\u0430\u0445\n"
        "\u26a1 \u0411\u044b\u0441\u0442\u0440\u044b\u0435 \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u0438\u044f\n\n"
        "\U0001f680 <b>\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u043f\u043b\u0430\u0442\u0444\u043e\u0440\u043c\u0443:</b>"
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
                    "currency": "\u2b50",
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
        "\U0001f4cb <b>\u041f\u043e\u0434\u0440\u043e\u0431\u043d\u0430\u044f \u0438\u043d\u0444\u043e\u0440\u043c\u0430\u0446\u0438\u044f</b>\n\n"
        "\U0001f3ae <b>\u0412\u0435\u0440\u0441\u0438\u044f:</b> 0.37.1\n"
        "\U0001f525 <b>\u0421\u0442\u0430\u0442\u0443\u0441:</b> \u0410\u043a\u0442\u0438\u0432\u043d\u043e\n\n"
        "\U0001f6e0\ufe0f <b>\u0424\u0443\u043d\u043a\u0446\u0438\u043e\u043d\u0430\u043b:</b>\n"
        "\u2022 \U0001f3af AimBot\n"
        "\u2022 \U0001f441\ufe0f WallHack\n"
        "\u2022 \U0001f4cd ESP\n"
        "\u2022 \U0001f5fa\ufe0f \u0420\u0430\u0434\u0430\u0440\n"
        "\u2022 \u2699\ufe0f \u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438\n\n"
        "\U0001f4ac \u041f\u043e\u0434\u0434\u0435\u0440\u0436\u043a\u0430: @{}"
    ).format(Config.SUPPORT_CHAT_USERNAME)
    await callback.message.edit_text(text, reply_markup=about_keyboard())
    await callback.answer()


@dp.callback_query(F.data.startswith("platform_"))
async def process_platform(callback: types.CallbackQuery, state: FSMContext):
    platform = callback.data.split("_")[1]
    if platform not in ("apk", "ios"):
        await callback.answer("\u274c \u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u0430\u044f \u043f\u043b\u0430\u0442\u0444\u043e\u0440\u043c\u0430", show_alert=True)
        return
    await state.update_data(platform=platform)
    platform_info = {
        "apk": {
            "title": "\U0001f4f1 <b>Android Version</b>",
            "requirements": "\u2022 Android 10.0+\n\u2022 2 \u0413\u0411 RAM\n\u2022 Root \u043d\u0435 \u043d\u0443\u0436\u0435\u043d",
            "includes": "\u2022 APK \u0444\u0430\u0439\u043b\n\u2022 \u0418\u043d\u0441\u0442\u0440\u0443\u043a\u0446\u0438\u044f\n\u2022 \u041f\u043e\u0434\u0434\u0435\u0440\u0436\u043a\u0430"
        },
        "ios": {
            "title": "\U0001f34e <b>iOS Version</b>",
            "requirements": "\u2022 iOS 14.0 - 18.0\n\u2022 AltStore\n\u2022 Jailbreak \u043d\u0435 \u043d\u0443\u0436\u0435\u043d",
            "includes": "\u2022 IPA \u0444\u0430\u0439\u043b\n\u2022 \u0418\u043d\u0441\u0442\u0440\u0443\u043a\u0446\u0438\u044f\n\u2022 \u041f\u043e\u043c\u043e\u0449\u044c"
        }
    }
    info = platform_info[platform]
    text = (
        "{title}\n\n"
        "\U0001f527 <b>\u0422\u0440\u0435\u0431\u043e\u0432\u0430\u043d\u0438\u044f:</b>\n{requirements}\n\n"
        "\U0001f4e6 <b>\u0412\u0445\u043e\u0434\u0438\u0442:</b>\n{includes}\n\n"
        "\U0001f4b0 <b>\u0422\u0430\u0440\u0438\u0444:</b>"
    ).format(title=info['title'], requirements=info['requirements'], includes=info['includes'])
    await callback.message.edit_text(text, reply_markup=subscription_keyboard(platform))
    await state.set_state(OrderState.choosing_subscription)
    await callback.answer()


@dp.callback_query(F.data.startswith("sub_"))
async def process_subscription(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    if len(parts) < 3:
        await callback.answer("\u274c \u041e\u0448\u0438\u0431\u043a\u0430", show_alert=True)
        return
    product_key = "{}_{}".format(parts[1], parts[2])
    product = find_product_by_id(product_key)
    if not product:
        await callback.answer("\u274c \u041d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u043e", show_alert=True)
        return
    await state.update_data(selected_product=product)
    text = (
        "\U0001f6d2 <b>\u041e\u0444\u043e\u0440\u043c\u043b\u0435\u043d\u0438\u0435</b>\n\n"
        "{emoji} <b>{name}</b>\n"
        "\u23f1\ufe0f {duration}\n\n"
        "\U0001f48e <b>\u0421\u0442\u043e\u0438\u043c\u043e\u0441\u0442\u044c:</b>\n"
        "\U0001f4b3 \u041a\u0430\u0440\u0442\u043e\u0439: {price} \u20bd\n"
        "\u2b50 Stars: {price_stars} \u2b50\n"
        "\u20bf \u041a\u0440\u0438\u043f\u0442\u0430: {price_crypto} USDT\n"
        "\U0001f4b0 GOLD: {price_gold} \U0001fa99\n"
        "\U0001f3a8 NFT: {price_nft} \U0001f5bc\ufe0f\n\n"
        "\U0001f3af <b>\u0421\u043f\u043e\u0441\u043e\u0431 \u043e\u043f\u043b\u0430\u0442\u044b:</b>"
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
        await callback.answer("\u274c \u041d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u043d\u043e", show_alert=True)
        return
    parts = callback.data.split("_")
    if len(parts) < 4:
        await callback.answer("\u274c \u041e\u0448\u0438\u0431\u043a\u0430", show_alert=True)
        return
    product = find_product(parts[2], parts[3])
    if not product:
        await callback.answer("\u274c \u041d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u043e", show_alert=True)
        return
    user_id = callback.from_user.id
    if not rate_limiter.check(user_id):
        await callback.answer("\u23f3 \u041f\u043e\u0434\u043e\u0436\u0434\u0438\u0442\u0435...", show_alert=True)
        return
    order_id = generate_order_id()
    amount = product["price"]
    product_desc = "{} ({})".format(product['name'], product['duration'])
    payment_url = create_payment_link(amount, order_id, product_desc)
    await orders.add_pending(order_id, {
        "user_id": user_id, "user_name": callback.from_user.full_name,
        "product": product, "amount": amount, "currency": "\u20bd",
        "payment_method": "\u041a\u0430\u0440\u0442\u043e\u0439",
        "status": "pending", "created_at": time.time()
    })
    text = (
        "\U0001f4b3 <b>\u041e\u043f\u043b\u0430\u0442\u0430 \u043a\u0430\u0440\u0442\u043e\u0439</b>\n\n"
        "{emoji} {name}\n\u23f1\ufe0f {duration}\n"
        "\U0001f4b0 <b>{amount} \u20bd</b>\n"
        "\U0001f194 <code>{order_id}</code>\n\n"
        "1\ufe0f\u20e3 \u041d\u0430\u0436\u043c\u0438\u0442\u0435 \u00ab\u041e\u043f\u043b\u0430\u0442\u0438\u0442\u044c\u00bb\n"
        "2\ufe0f\u20e3 \u041e\u043f\u043b\u0430\u0442\u0438\u0442\u0435\n"
        "3\ufe0f\u20e3 \u041d\u0430\u0436\u043c\u0438\u0442\u0435 \u00ab\u041f\u0440\u043e\u0432\u0435\u0440\u0438\u0442\u044c\u00bb"
    ).format(
        emoji=product['emoji'], name=product['name'],
        duration=product['duration'], amount=amount, order_id=order_id
    )
    await callback.message.edit_text(text, reply_markup=payment_keyboard(payment_url, order_id))
    await send_admin_notification(callback.from_user, product, "\U0001f4b3 \u041a\u0430\u0440\u0442\u043e\u0439", "{} \u20bd".format(amount), order_id)
    await callback.answer()


@dp.callback_query(F.data.startswith("checkym_"))
async def check_yoomoney_callback(callback: types.CallbackQuery):
    order_id = callback.data.replace("checkym_", "", 1)
    order = await orders.get_pending(order_id)
    if not order:
        if await orders.is_confirmed(order_id):
            await callback.answer("\u2705 \u0423\u0436\u0435 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d!", show_alert=True)
        else:
            await callback.answer("\u274c \u041d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d", show_alert=True)
        return
    if not rate_limiter.check(callback.from_user.id):
        await callback.answer("\u23f3 \u041f\u043e\u0434\u043e\u0436\u0434\u0438\u0442\u0435...", show_alert=True)
        return
    await callback.answer("\U0001f50d \u041f\u0440\u043e\u0432\u0435\u0440\u044f\u0435\u043c...")
    checking_msg = await callback.message.edit_text(
        "\U0001f504 <b>\u041f\u0440\u043e\u0432\u0435\u0440\u043a\u0430...</b>\n\u23f3 15-25 \u0441\u0435\u043a\u0443\u043d\u0434"
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
        success = await process_successful_payment(order_id, "\u0410\u0432\u0442\u043e\u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0430")
        if success:
            await checking_msg.edit_text(
                "\u2705 <b>\u041f\u043b\u0430\u0442\u0435\u0436 \u043d\u0430\u0439\u0434\u0435\u043d!</b>\n\U0001f4e8 \u041f\u0440\u043e\u0432\u0435\u0440\u044c\u0442\u0435 \u043d\u043e\u0432\u043e\u0435 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435 \u2b06\ufe0f",
                reply_markup=support_keyboard()
            )
        else:
            await checking_msg.edit_text("\u2705 <b>\u0423\u0436\u0435 \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u0430\u043d</b>", reply_markup=support_keyboard())
    else:
        product = order['product']
        product_desc = "{} ({})".format(product['name'], product['duration'])
        payment_url = create_payment_link(order["amount"], order_id, product_desc)
        await checking_msg.edit_text(
            "\u23f3 <b>\u041d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d</b>\n\u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u0447\u0435\u0440\u0435\u0437 1-2 \u043c\u0438\u043d",
            reply_markup=payment_keyboard(payment_url, order_id)
        )


# ========== STARS ==========
@dp.callback_query(F.data.startswith("pay_stars_"))
async def process_stars_payment(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    if len(parts) < 4:
        await callback.answer("\u274c", show_alert=True)
        return
    product = find_product(parts[2], parts[3])
    if not product:
        await callback.answer("\u274c \u041d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u043e", show_alert=True)
        return
    if not rate_limiter.check(callback.from_user.id):
        await callback.answer("\u23f3", show_alert=True)
        return
    order_id = generate_order_id()
    await orders.add_pending(order_id, {
        "user_id": callback.from_user.id,
        "user_name": callback.from_user.full_name,
        "product": product, "amount": product['price_stars'],
        "currency": "\u2b50", "payment_method": "Telegram Stars",
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
        await callback.answer("\u274c \u041d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u043d\u043e", show_alert=True)
        return
    parts = callback.data.split("_")
    if len(parts) < 4:
        await callback.answer("\u274c", show_alert=True)
        return
    product = find_product(parts[2], parts[3])
    if not product:
        await callback.answer("\u274c", show_alert=True)
        return
    if not rate_limiter.check(callback.from_user.id):
        await callback.answer("\u23f3", show_alert=True)
        return
    order_id = generate_order_id()
    amount_usdt = product["price_crypto_usdt"]
    description = "AimNoob {} ({})".format(product['name'], product['duration'])
    invoice_data = await CryptoBotService.create_invoice(amount_usdt, order_id, description)
    if not invoice_data:
        await callback.answer("\u274c \u041e\u0448\u0438\u0431\u043a\u0430 \u0438\u043d\u0432\u043e\u0439\u0441\u0430", show_alert=True)
        return
    await orders.add_pending(order_id, {
        "user_id": callback.from_user.id,
        "user_name": callback.from_user.full_name,
        "product": product, "amount": amount_usdt, "currency": "USDT",
        "payment_method": "CryptoBot", "status": "pending",
        "invoice_id": invoice_data["invoice_id"], "created_at": time.time()
    })
    text = (
        "\u20bf <b>\u041a\u0440\u0438\u043f\u0442\u043e</b>\n\n"
        "{emoji} {name}\n\u23f1\ufe0f {duration}\n"
        "\U0001f4b0 <b>{amount} USDT</b>\n"
        "\U0001f194 <code>{order_id}</code>"
    ).format(
        emoji=product['emoji'], name=product['name'],
        duration=product['duration'], amount=amount_usdt, order_id=order_id
    )
    await callback.message.edit_text(text, reply_markup=crypto_payment_keyboard(invoice_data["pay_url"], order_id))
    await send_admin_notification(callback.from_user, product, "\u20bf CryptoBot", "{} USDT".format(amount_usdt), order_id)
    await callback.answer()


@dp.callback_query(F.data.startswith("checkcr_"))
async def check_crypto_callback(callback: types.CallbackQuery):
    order_id = callback.data.replace("checkcr_", "", 1)
    order = await orders.get_pending(order_id)
    if not order:
        if await orders.is_confirmed(order_id):
            await callback.answer("\u2705 \u0423\u0436\u0435 \u043e\u043f\u043b\u0430\u0447\u0435\u043d\u043e!", show_alert=True)
        else:
            await callback.answer("\u274c \u041d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d", show_alert=True)
        return
    if not rate_limiter.check(callback.from_user.id):
        await callback.answer("\u23f3", show_alert=True)
        return
    await callback.answer("\U0001f50d \u041f\u0440\u043e\u0432\u0435\u0440\u044f\u0435\u043c...")
    invoice_id = order.get("invoice_id")
    if not invoice_id:
        await callback.answer("\u274c \u041d\u0435\u0442 invoice_id", show_alert=True)
        return
    is_paid = await CryptoBotService.check_invoice(invoice_id)
    if is_paid:
        success = await process_successful_payment(order_id, "CryptoBot")
        if success:
            await callback.message.edit_text(
                "\u2705 <b>\u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u043e!</b>\n\U0001f4e8 \u041a\u043b\u044e\u0447 \u0432 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0438 \u2b06\ufe0f",
                reply_markup=support_keyboard()
            )
    else:
        await callback.answer("\u23f3 \u041d\u0435 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d. \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u043f\u043e\u0437\u0436\u0435.", show_alert=True)


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
        await callback.answer("\u274c", show_alert=True)
        return
    product = find_product(parts[2], parts[3])
    if not product:
        await callback.answer("\u274c", show_alert=True)
        return
    if not rate_limiter.check(callback.from_user.id):
        await callback.answer("\u23f3", show_alert=True)
        return
    cfg = {
        "gold": {"name": "GOLD", "icon": "\U0001f4b0", "price_key": "price_gold", "emoji": "\U0001fa99"},
        "nft": {"name": "NFT", "icon": "\U0001f3a8", "price_key": "price_nft", "emoji": "\U0001f5bc\ufe0f"}
    }[method]
    price = product[cfg["price_key"]]
    chat_message = "Привет! Хочу купить чит на Standoff 2. {} ({}) за {} {}".format(
        product['platform'], product['period_text'], price, cfg['name']
    )
    encoded_message = quote(chat_message, safe='')
    support_url = "https://t.me/{}?text={}".format(Config.SUPPORT_CHAT_USERNAME, encoded_message)
    text = (
        "{icon} <b>\u041e\u043f\u043b\u0430\u0442\u0430 {method_name}</b>\n\n"
        "{emoji} {{product_name}\n\u23f1\ufe0f {duration}\n"
        "\U0001f4b0 <b>{price} {method_name}</b>\n\n"
        "1\ufe0f\u20e3 \u041d\u0430\u0436\u043c\u0438\u0442\u0435 \u00ab\u041f\u0435\u0440\u0435\u0439\u0442\u0438\u00bb\n"
        "2\ufe0f\u20e3 \u041e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435\n"
        "3\ufe0f\u20e3 \u041e\u0436\u0438\u0434\u0430\u0439\u0442\u0435"
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
    icon = "\U0001f4b0" if callback.data == "gold_sent" else "\U0001f3a8"
    text = (
        "\u2705 <b>\u041f\u0440\u0438\u043d\u044f\u0442\u043e!</b>\n\n"
        "{icon} {method_name} \u0437\u0430\u043a\u0430\u0437 \u0432 \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u043a\u0435\n"
        "\u23f1\ufe0f \u0414\u043e 30 \u043c\u0438\u043d\u0443\u0442\n"
        "\U0001f4ac @{support}"
    ).format(icon=icon, method_name=method_name, support=Config.SUPPORT_CHAT_USERNAME)
    await callback.message.edit_text(text, reply_markup=support_keyboard())
    await callback.answer()


# ========== АДМИН ==========
@dp.callback_query(F.data.startswith("admin_confirm_"))
async def admin_confirm_payment(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("\u274c", show_alert=True)
        return
    order_id = callback.data.replace("admin_confirm_", "", 1)
    success = await process_successful_payment(order_id, "\U0001f468\u200d\U0001f4bc \u0410\u0434\u043c\u0438\u043d")
    if success:
        await callback.message.edit_text(
            "\u2705 <b>\u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d</b>\n\U0001f194 {}\n\U0001f468\u200d\U0001f4bc {}".format(
                order_id, callback.from_user.full_name
            )
        )
        await callback.answer("\u2705")
    else:
        await callback.answer("\u274c \u041d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d / \u0443\u0436\u0435 \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u0430\u043d", show_alert=True)


@dp.callback_query(F.data.startswith("admin_reject_"))
async def admin_reject_payment(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("\u274c", show_alert=True)
        return
    order_id = callback.data.replace("admin_reject_", "", 1)
    order = await orders.remove_pending(order_id)
    if order:
        await callback.message.edit_text(
            "\u274c <b>\u041e\u0442\u043a\u043b\u043e\u043d\u0435\u043d</b>\n\U0001f194 {}".format(order_id)
        )
        try:
            await bot.send_message(order['user_id'],
                "\u274c <b>\u0417\u0430\u043a\u0430\u0437 \u043e\u0442\u043a\u043b\u043e\u043d\u0435\u043d</b>\n\U0001f4ac @{}".format(Config.SUPPORT_CHAT_USERNAME)
            )
        except Exception:
            pass
    await callback.answer("\u274c \u041e\u0442\u043a\u043b\u043e\u043d\u0435\u043d")


@dp.message(Command("orders"))
async def cmd_orders(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    stats = await orders.get_stats()
    text = "\U0001f4ca <b>\u0421\u0442\u0430\u0442\u0438\u0441\u0442\u0438\u043a\u0430</b>\n\n"
    text += "\u23f3 \u041e\u0436\u0438\u0434\u0430\u044e\u0442: {}\n".format(stats['pending'])
    text += "\u2705 \u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u043e: {}\n".format(stats['confirmed'])
    balance = await YooMoneyService.get_balance()
    if balance is not None:
        text += "\U0001f4b0 \u0411\u0430\u043b\u0430\u043d\u0441: {} \u20bd\n".format(balance)
    await message.answer(text)


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "\U0001f527 <b>\u0410\u0434\u043c\u0438\u043d:</b>\n\n"
        "/orders \u2014 \u0421\u0442\u0430\u0442\u0438\u0441\u0442\u0438\u043a\u0430\n"
        "/help \u2014 \u0421\u043f\u0440\u0430\u0432\u043a\u0430"
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
    title = "\U0001f4f1 <b>Android</b>" if platform == "apk" else "\U0001f34e <b>iOS</b>"
    text = "{}\n\n\U0001f4b0 <b>\u0422\u0430\u0440\u0438\u0444:</b>".format(title)
    await callback.message.edit_text(text, reply_markup=subscription_keyboard(platform))
    await state.set_state(OrderState.choosing_subscription)
    await callback.answer()


# ========== ПРОФЕССИОНАЛЬНЫЙ MINIAPP ==========

MINIAPP_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,maximum-scale=1,user-scalable=no">
<title>AimNoob | Premium Store</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
* {{
    margin: 0;
    padding: 0;
    box-sizing: border-box;
    -webkit-tap-highlight-color: transparent;
}}

:root {{
    --bg-dark: #05050c;
    --bg-card: rgba(18, 18, 28, 0.8);
    --bg-surface: rgba(255, 255, 255, 0.05);
    --bg-surface-hover: rgba(255, 255, 255, 0.08);
    --accent-primary: #8b5cf6;
    --accent-secondary: #ec4899;
    --accent-glow: rgba(139, 92, 246, 0.4);
    --text-primary: #ffffff;
    --text-secondary: rgba(255, 255, 255, 0.7);
    --text-muted: rgba(255, 255, 255, 0.4);
    --success: #10b981;
    --warning: #f59e0b;
    --error: #ef4444;
    --gradient-primary: linear-gradient(135deg, #8b5cf6, #ec4899);
    --gradient-dark: linear-gradient(135deg, #6d28d9, #db2777);
    --shadow-sm: 0 2px 8px rgba(0, 0, 0, 0.2);
    --shadow-md: 0 4px 20px rgba(0, 0, 0, 0.3);
    --shadow-lg: 0 8px 32px rgba(0, 0, 0, 0.4);
    --shadow-glow: 0 0 20px rgba(139, 92, 246, 0.3);
}}

body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'SF Pro Display', system-ui, sans-serif;
    background: var(--bg-dark);
    color: var(--text-primary);
    min-height: 100vh;
    overflow-x: hidden;
}}

/* Animated Background */
.background {{
    position: fixed;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    z-index: 0;
    overflow: hidden;
}}

.gradient-orb {{
    position: absolute;
    border-radius: 50%;
    filter: blur(80px);
    opacity: 0.3;
    animation: float 20s ease-in-out infinite;
}}

.orb-1 {{
    width: 400px;
    height: 400px;
    background: #8b5cf6;
    top: -100px;
    left: -100px;
}}

.orb-2 {{
    width: 350px;
    height: 350px;
    background: #ec4899;
    bottom: -80px;
    right: -80px;
    animation-delay: -5s;
}}

.orb-3 {{
    width: 300px;
    height: 300px;
    background: #3b82f6;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    animation-delay: -10s;
    opacity: 0.2;
}}

.grain {{
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='3'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)' opacity='0.03'/%3E%3C/svg%3E");
    pointer-events: none;
}}

@keyframes float {{
    0%, 100% {{ transform: translate(0, 0) rotate(0deg); }}
    33% {{ transform: translate(30px, -30px) rotate(120deg); }}
    66% {{ transform: translate(-20px, 20px) rotate(240deg); }}
}}

/* Main Container */
.app {{
    position: relative;
    z-index: 1;
    max-width: 480px;
    margin: 0 auto;
    padding: 20px 16px 90px;
    min-height: 100vh;
}}

/* Header Section */
.header {{
    text-align: center;
    margin-bottom: 24px;
    animation: fadeInDown 0.6s cubic-bezier(0.2, 0.9, 0.4, 1.1);
}}

.logo-wrapper {{
    position: relative;
    display: inline-block;
    margin-bottom: 16px;
}}

.logo {{
    width: 80px;
    height: 80px;
    background: var(--gradient-primary);
    border-radius: 24px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 40px;
    box-shadow: var(--shadow-glow);
    animation: pulse 2s ease-in-out infinite;
    position: relative;
    z-index: 2;
}}

.logo-ring {{
    position: absolute;
    inset: -4px;
    border-radius: 28px;
    border: 2px solid rgba(139, 92, 246, 0.5);
    animation: spin 8s linear infinite;
}}

@keyframes pulse {{
    0%, 100% {{ transform: scale(1); box-shadow: 0 0 0 0 var(--accent-glow); }}
    50% {{ transform: scale(1.05); box-shadow: 0 0 0 15px rgba(139, 92, 246, 0); }}
}}

@keyframes spin {{
    from {{ transform: rotate(0deg); }}
    to {{ transform: rotate(360deg); }}
}}

h1 {{
    font-size: 28px;
    font-weight: 800;
    background: var(--gradient-primary);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    letter-spacing: -0.5px;
    margin-bottom: 4px;
}}

.tagline {{
    font-size: 12px;
    color: var(--text-secondary);
    letter-spacing: 0.5px;
}}

/* Status Badge */
.status {{
    display: inline-flex;
    align-items: center;
    gap: 8px;
    background: rgba(16, 185, 129, 0.1);
    backdrop-filter: blur(10px);
    border: 1px solid rgba(16, 185, 129, 0.2);
    padding: 6px 16px;
    border-radius: 40px;
    font-size: 11px;
    font-weight: 500;
    margin-bottom: 24px;
}}

.status-dot {{
    width: 6px;
    height: 6px;
    background: var(--success);
    border-radius: 50%;
    animation: blink 1.5s infinite;
}}

@keyframes blink {{
    0%, 100% {{ opacity: 1; transform: scale(1); }}
    50% {{ opacity: 0.5; transform: scale(0.8); }}
}}

/* Navigation Tabs */
.tabs {{
    display: flex;
    gap: 6px;
    background: rgba(255, 255, 255, 0.03);
    backdrop-filter: blur(20px);
    border-radius: 20px;
    padding: 5px;
    margin-bottom: 24px;
    border: 1px solid rgba(255, 255, 255, 0.05);
}}

.tab {{
    flex: 1;
    padding: 12px 8px;
    border: none;
    background: transparent;
    color: var(--text-secondary);
    font-size: 14px;
    font-weight: 600;
    border-radius: 16px;
    cursor: pointer;
    transition: all 0.3s cubic-bezier(0.2, 0.9, 0.4, 1.1);
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
}}

.tab-icon {{
    font-size: 20px;
    transition: transform 0.2s;
}}

.tab.active {{
    background: var(--gradient-primary);
    color: white;
    box-shadow: var(--shadow-sm);
}}

.tab.active .tab-icon {{
    transform: scale(1.1);
}}

.tab:not(.active):hover {{
    background: var(--bg-surface-hover);
    color: var(--text-primary);
}}

/* Product Grid */
.products-grid {{
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 14px;
    margin-bottom: 20px;
}}

.product-card {{
    background: var(--bg-card);
    backdrop-filter: blur(10px);
    border: 1px solid rgba(255, 255, 255, 0.05);
    border-radius: 24px;
    overflow: hidden;
    transition: all 0.3s cubic-bezier(0.2, 0.9, 0.4, 1.1);
    cursor: pointer;
    position: relative;
}}

.product-card:hover {{
    transform: translateY(-4px);
    border-color: var(--accent-primary);
    box-shadow: var(--shadow-md);
}}

.product-card:active {{
    transform: scale(0.98);
}}

.product-badge {{
    position: absolute;
    top: 12px;
    right: 12px;
    background: var(--gradient-primary);
    padding: 4px 10px;
    border-radius: 20px;
    font-size: 10px;
    font-weight: 700;
    z-index: 2;
    box-shadow: var(--shadow-sm);
}}

.product-content {{
    padding: 18px 14px;
    text-align: center;
}}

.product-icon {{
    width: 56px;
    height: 56px;
    background: linear-gradient(135deg, rgba(139, 92, 246, 0.2), rgba(236, 72, 153, 0.2));
    border-radius: 20px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 28px;
    margin: 0 auto 12px;
    transition: transform 0.2s;
}}

.product-card:hover .product-icon {{
    transform: scale(1.05);
}}

.product-name {{
    font-weight: 700;
    font-size: 15px;
    margin-bottom: 4px;
}}

.product-period {{
    font-size: 11px;
    color: var(--text-muted);
    margin-bottom: 12px;
}}

.product-price {{
    font-size: 22px;
    font-weight: 800;
    color: var(--warning);
    margin-bottom: 12px;
}}

.product-price small {{
    font-size: 11px;
    font-weight: 400;
    color: var(--text-muted);
}}

.product-features {{
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    justify-content: center;
    margin-bottom: 14px;
}}

.feature {{
    font-size: 9px;
    padding: 3px 8px;
    background: rgba(139, 92, 246, 0.15);
    border-radius: 8px;
    color: var(--accent-primary);
    font-weight: 500;
}}

.buy-btn {{
    width: 100%;
    padding: 10px;
    background: var(--gradient-primary);
    border: none;
    border-radius: 14px;
    color: white;
    font-weight: 700;
    font-size: 13px;
    cursor: pointer;
    transition: all 0.2s;
    position: relative;
    overflow: hidden;
}}

.buy-btn::before {{
    content: '';
    position: absolute;
    top: 0;
    left: -100%;
    width: 100%;
    height: 100%;
    background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.2), transparent);
    transition: left 0.5s;
}}

.buy-btn:hover::before {{
    left: 100%;
}}

.buy-btn:active {{
    transform: scale(0.96);
}}

/* Full Width Card */
.product-card.full-width {{
    grid-column: 1 / -1;
}}

.product-card.full-width .product-content {{
    display: flex;
    align-items: center;
    gap: 16px;
    text-align: left;
    padding: 16px;
}}

.product-card.full-width .product-icon {{
    margin: 0;
    width: 60px;
    height: 60px;
    font-size: 30px;
}}

.product-card.full-width .product-info {{
    flex: 1;
}}

.product-card.full-width .product-name {{
    font-size: 16px;
    margin-bottom: 2px;
}}

.product-card.full-width .product-period {{
    margin-bottom: 0;
}}

.product-card.full-width .product-price {{
    font-size: 24px;
    margin: 0;
    text-align: right;
}}

.product-card.full-width .buy-btn {{
    margin-top: 12px;
    grid-column: 1 / -1;
    width: calc(100% - 32px);
    margin-left: 16px;
    margin-bottom: 16px;
}}

/* Modal */
.modal {{
    display: none;
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0, 0, 0, 0.85);
    backdrop-filter: blur(20px);
    z-index: 1000;
    align-items: flex-end;
    justify-content: center;
}}

.modal.active {{
    display: flex;
    animation: fadeIn 0.3s ease;
}}

.modal-content {{
    background: linear-gradient(180deg, #111118 0%, #0a0a10 100%);
    border-radius: 32px 32px 0 0;
    width: 100%;
    max-width: 480px;
    max-height: 88vh;
    overflow-y: auto;
    animation: slideUp 0.4s cubic-bezier(0.2, 0.9, 0.4, 1.1);
    border-top: 1px solid rgba(255, 255, 255, 0.1);
}}

.modal-handle {{
    width: 40px;
    height: 4px;
    background: rgba(255, 255, 255, 0.2);
    border-radius: 4px;
    margin: 14px auto 0;
}}

.modal-header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 18px 20px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.05);
    position: sticky;
    top: 0;
    background: inherit;
    z-index: 5;
}}

.modal-header h3 {{
    font-size: 20px;
    font-weight: 700;
    background: var(--gradient-primary);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}}

.modal-close {{
    width: 36px;
    height: 36px;
    border-radius: 50%;
    background: var(--bg-surface);
    border: none;
    color: var(--text-secondary);
    font-size: 20px;
    cursor: pointer;
    transition: all 0.2s;
}}

.modal-close:active {{
    background: var(--error);
    color: white;
    transform: rotate(90deg);
}}

.modal-body {{
    padding: 20px;
}}

/* Product Summary */
.product-summary {{
    background: var(--bg-surface);
    border-radius: 24px;
    padding: 20px;
    text-align: center;
    margin-bottom: 20px;
    border: 1px solid rgba(255, 255, 255, 0.05);
}}

.summary-icon {{
    font-size: 56px;
    margin-bottom: 12px;
}}

.summary-title {{
    font-size: 18px;
    font-weight: 700;
    margin-bottom: 4px;
}}

.summary-duration {{
    font-size: 12px;
    color: var(--text-muted);
    margin-bottom: 12px;
}}

.summary-price {{
    font-size: 28px;
    font-weight: 800;
    color: var(--warning);
}}

/* Payment Methods */
.payment-methods {{
    display: flex;
    flex-direction: column;
    gap: 10px;
}}

.payment-method {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px;
    background: var(--bg-surface);
    border-radius: 20px;
    cursor: pointer;
    transition: all 0.2s;
    border: 1px solid transparent;
}}

.payment-method:active {{
    transform: scale(0.98);
    border-color: var(--accent-primary);
    background: rgba(139, 92, 246, 0.05);
}}

.payment-method-left {{
    display: flex;
    align-items: center;
    gap: 14px;
}}

.payment-icon {{
    width: 48px;
    height: 48px;
    background: rgba(139, 92, 246, 0.15);
    border-radius: 16px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 24px;
}}

.payment-info h4 {{
    font-size: 15px;
    font-weight: 700;
    margin-bottom: 2px;
}}

.payment-info p {{
    font-size: 11px;
    color: var(--text-muted);
}}

.payment-amount {{
    font-weight: 800;
    color: var(--warning);
    font-size: 16px;
}}

/* Status Views */
.status-view {{
    text-align: center;
    padding: 40px 20px;
}}

.status-icon {{
    font-size: 64px;
    margin-bottom: 20px;
}}

.status-icon.spin {{
    animation: spin 1s linear infinite;
}}

.status-title {{
    font-size: 22px;
    font-weight: 800;
    margin-bottom: 8px;
}}

.status-desc {{
    font-size: 14px;
    color: var(--text-secondary);
    line-height: 1.5;
}}

/* Key Display */
.key-box {{
    background: rgba(0, 0, 0, 0.5);
    border: 1px solid rgba(255, 255, 255, 0.1);
    padding: 16px;
    border-radius: 16px;
    font-family: 'SF Mono', monospace;
    font-size: 13px;
    text-align: center;
    word-break: break-all;
    color: var(--warning);
    margin: 20px 0;
    cursor: pointer;
    transition: all 0.2s;
    letter-spacing: 0.5px;
}}

.key-box:active {{
    background: rgba(139, 92, 246, 0.1);
    border-color: var(--accent-primary);
}}

/* Action Buttons */
.action-btn {{
    width: 100%;
    padding: 16px;
    border: none;
    border-radius: 18px;
    background: var(--gradient-primary);
    color: white;
    font-weight: 700;
    font-size: 16px;
    cursor: pointer;
    transition: all 0.2s;
    margin-bottom: 12px;
    position: relative;
    overflow: hidden;
}}

.action-btn::before {{
    content: '';
    position: absolute;
    top: 0;
    left: -100%;
    width: 100%;
    height: 100%;
    background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.2), transparent);
    transition: left 0.5s;
}}

.action-btn:hover::before {{
    left: 100%;
}}

.action-btn:active {{
    transform: scale(0.98);
}}

.action-btn.secondary {{
    background: var(--bg-surface);
    color: var(--text-primary);
    border: 1px solid rgba(255, 255, 255, 0.1);
}}

/* License Cards */
.license-card {{
    background: var(--bg-card);
    backdrop-filter: blur(10px);
    border-radius: 20px;
    padding: 16px;
    margin-bottom: 14px;
    border: 1px solid rgba(255, 255, 255, 0.05);
    transition: all 0.2s;
}}

.license-header {{
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 12px;
}}

.license-icon {{
    width: 44px;
    height: 44px;
    background: linear-gradient(135deg, rgba(139, 92, 246, 0.2), rgba(236, 72, 153, 0.2));
    border-radius: 14px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 22px;
}}

.license-info h4 {{
    font-size: 14px;
    font-weight: 700;
    margin-bottom: 2px;
}}

.license-date {{
    font-size: 10px;
    color: var(--text-muted);
}}

.license-key {{
    background: rgba(0, 0, 0, 0.4);
    padding: 12px;
    border-radius: 12px;
    font-family: 'SF Mono', monospace;
    font-size: 11px;
    text-align: center;
    color: var(--warning);
    cursor: pointer;
    margin-bottom: 10px;
    letter-spacing: 0.5px;
}}

.license-copy-btn {{
    width: 100%;
    padding: 10px;
    background: var(--bg-surface);
    border: none;
    border-radius: 12px;
    color: var(--text-secondary);
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s;
}}

.license-copy-btn:active {{
    background: var(--bg-surface-hover);
    transform: scale(0.98);
}}

/* Profile Card */
.profile-card {{
    background: var(--bg-card);
    backdrop-filter: blur(10px);
    border-radius: 28px;
    padding: 28px;
    text-align: center;
    margin-bottom: 20px;
    border: 1px solid rgba(255, 255, 255, 0.05);
}}

.profile-avatar {{
    width: 80px;
    height: 80px;
    background: var(--gradient-primary);
    border-radius: 28px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 40px;
    margin: 0 auto 16px;
    box-shadow: var(--shadow-glow);
}}

.profile-name {{
    font-size: 20px;
    font-weight: 800;
    margin-bottom: 4px;
}}

.profile-username {{
    font-size: 13px;
    color: var(--text-muted);
    margin-bottom: 20px;
}}

.profile-stats {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
}}

.stat-card {{
    background: var(--bg-surface);
    border-radius: 18px;
    padding: 14px;
    text-align: center;
}}

.stat-number {{
    font-size: 24px;
    font-weight: 800;
    color: var(--accent-primary);
}}

.stat-label {{
    font-size: 11px;
    color: var(--text-muted);
    margin-top: 4px;
}}

/* Empty State */
.empty-state {{
    text-align: center;
    padding: 60px 20px;
}}

.empty-icon {{
    font-size: 64px;
    margin-bottom: 16px;
    opacity: 0.5;
}}

.empty-text {{
    font-size: 15px;
    color: var(--text-secondary);
    margin-bottom: 20px;
}}

/* Toast Notification */
.toast {{
    position: fixed;
    bottom: 100px;
    left: 16px;
    right: 16px;
    padding: 14px 18px;
    border-radius: 18px;
    z-index: 1100;
    display: flex;
    align-items: center;
    gap: 12px;
    font-size: 14px;
    font-weight: 500;
    animation: slideUp 0.3s ease;
    max-width: 480px;
    margin: 0 auto;
    backdrop-filter: blur(20px);
}}

.toast.success {{
    background: rgba(16, 185, 129, 0.2);
    border: 1px solid rgba(16, 185, 129, 0.3);
    color: var(--success);
}}

.toast.error {{
    background: rgba(239, 68, 68, 0.2);
    border: 1px solid rgba(239, 68, 68, 0.3);
    color: var(--error);
}}

/* Pages */
.page {{
    display: none;
    animation: fadeInUp 0.4s ease;
}}

.page.active {{
    display: block;
}}

/* Bottom Navigation */
.bottom-nav {{
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    background: rgba(5, 5, 12, 0.95);
    backdrop-filter: blur(20px);
    border-top: 1px solid rgba(255, 255, 255, 0.05);
    display: flex;
    justify-content: space-around;
    padding: 10px 16px 18px;
    z-index: 100;
    max-width: 480px;
    margin: 0 auto;
}}

.nav-item {{
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
    background: none;
    border: none;
    color: var(--text-muted);
    font-size: 11px;
    font-weight: 600;
    padding: 8px 24px;
    border-radius: 20px;
    cursor: pointer;
    transition: all 0.2s;
}}

.nav-item.active {{
    color: var(--accent-primary);
    background: rgba(139, 92, 246, 0.1);
}}

.nav-icon {{
    font-size: 22px;
}}

/* Animations */
@keyframes fadeInDown {{
    from {{
        opacity: 0;
        transform: translateY(-30px);
    }}
    to {{
        opacity: 1;
        transform: translateY(0);
    }}
}}

@keyframes fadeInUp {{
    from {{
        opacity: 0;
        transform: translateY(30px);
    }}
    to {{
        opacity: 1;
        transform: translateY(0);
    }}
}}

@keyframes fadeIn {{
    from {{ opacity: 0; }}
    to {{ opacity: 1; }}
}}

@keyframes slideUp {{
    from {{
        transform: translateY(100%);
    }}
    to {{
        transform: translateY(0);
    }}
}}

@keyframes spin {{
    from {{ transform: rotate(0deg); }}
    to {{ transform: rotate(360deg); }}
}}

/* Scrollbar */
::-webkit-scrollbar {{
    width: 4px;
}}

::-webkit-scrollbar-track {{
    background: transparent;
}}

::-webkit-scrollbar-thumb {{
    background: rgba(139, 92, 246, 0.3);
    border-radius: 10px;
}}
</style>
</head>
<body>
<div class="background">
    <div class="gradient-orb orb-1"></div>
    <div class="gradient-orb orb-2"></div>
    <div class="gradient-orb orb-3"></div>
    <div class="grain"></div>
</div>

<div class="app">
    <div class="header">
        <div class="logo-wrapper">
            <div class="logo">
                <span>🎯</span>
            </div>
            <div class="logo-ring"></div>
        </div>
        <h1>AimNoob</h1>
        <div class="tagline">Premium Software • Standoff 2</div>
    </div>

    <div style="text-align: center;">
        <div class="status">
            <span class="status-dot"></span>
            <span>Online • v0.37.1 • Undetected</span>
        </div>
    </div>

    <div class="tabs">
        <button class="tab active" data-tab="shop">
            <span class="tab-icon">🛒</span>
            <span>Магазин</span>
        </button>
        <button class="tab" data-tab="licenses">
            <span class="tab-icon">🔑</span>
            <span>Лицензии</span>
        </button>
        <button class="tab" data-tab="profile">
            <span class="tab-icon">👤</span>
            <span>Профиль</span>
        </button>
    </div>

    <div id="page-shop" class="page active"></div>
    <div id="page-licenses" class="page"></div>
    <div id="page-profile" class="page"></div>
</div>

<div class="bottom-nav">
    <button class="nav-item active" data-tab="shop">
        <span class="nav-icon">🛒</span>
        <span>Магазин</span>
    </button>
    <button class="nav-item" data-tab="licenses">
        <span class="nav-icon">🔑</span>
        <span>Лицензии</span>
    </button>
    <button class="nav-item" data-tab="profile">
        <span class="nav-icon">👤</span>
        <span>Профиль</span>
    </button>
</div>

<div class="modal" id="paymentModal">
    <div class="modal-content">
        <div class="modal-handle"></div>
        <div class="modal-header">
            <h3>Оформление заказа</h3>
            <button class="modal-close" id="modalClose">×</button>
        </div>
        <div class="modal-body" id="modalBody"></div>
    </div>
</div>

<script>
(function() {{
    const tg = window.Telegram.WebApp;
    tg.expand();
    tg.enableClosingConfirmation();
    
    const user = tg.initDataUnsafe?.user || {{ id: Date.now(), first_name: 'Guest', username: 'user' }};
    let licenses = JSON.parse(localStorage.getItem('aimnoob_licenses') || '[]');
    let currentProduct = null;
    
    const PRODUCTS = {{
        android: [
            {{ id: 'apk_week', name: 'Android', period: 'Неделя', duration: '7 дней', price: 150, stars: 350, gold: 350, nft: 250, usdt: 2, icon: '📱', features: ['AimBot', 'WallHack', 'ESP'], hit: false }},
            {{ id: 'apk_month', name: 'Android', period: 'Месяц', duration: '30 дней', price: 350, stars: 800, gold: 800, nft: 600, usdt: 5, icon: '📱', features: ['AimBot', 'WallHack', 'ESP', 'Anti-Ban'], hit: true }},
            {{ id: 'apk_forever', name: 'Android', period: 'Навсегда', duration: '∞', price: 800, stars: 1800, gold: 1800, nft: 1400, usdt: 12, icon: '📱', features: ['AimBot', 'WallHack', 'ESP', 'Anti-Ban', 'Updates'], hit: false }}
        ],
        ios: [
            {{ id: 'ios_week', name: 'iOS', period: 'Неделя', duration: '7 дней', price: 300, stars: 700, gold: 700, nft: 550, usdt: 4, icon: '🍎', features: ['AimBot', 'WallHack', 'ESP'], hit: false }},
            {{ id: 'ios_month', name: 'iOS', period: 'Месяц', duration: '30 дней', price: 450, stars: 1000, gold: 1000, nft: 800, usdt: 6, icon: '🍎', features: ['AimBot', 'WallHack', 'ESP', 'Anti-Ban'], hit: true }},
            {{ id: 'ios_forever', name: 'iOS', period: 'Навсегда', duration: '∞', price: 850, stars: 2000, gold: 2000, nft: 1600, usdt: 12, icon: '🍎', features: ['AimBot', 'WallHack', 'ESP', 'Anti-Ban', 'Updates'], hit: false }}
        ]
    }};
    
    const API = window.location.origin + '/api';
    const SUPPORT = '{support}';
    const DOWNLOAD_URL = '{download_url}';
    
    function toast(message, type) {{
        type = type || 'success';
        const el = document.createElement('div');
        el.className = 'toast ' + type;
        el.innerHTML = '<span>' + (type === 'success' ? '✓' : '✗') + '</span><span>' + message + '</span>';
        document.body.appendChild(el);
        setTimeout(() => el.remove(), 3000);
    }}
    
    function copyToClipboard(text) {{
        navigator.clipboard.writeText(text);
        toast('Ключ скопирован в буфер обмена');
        if (tg.HapticFeedback) tg.HapticFeedback.notificationOccurred('success');
    }}
    
    function saveLicenses() {{
        localStorage.setItem('aimnoob_licenses', JSON.stringify(licenses));
    }}
    
    const modal = document.getElementById('paymentModal');
    const modalBody = document.getElementById('modalBody');
    const modalClose = document.getElementById('modalClose');
    
    function openModal() {{
        modal.classList.add('active');
    }}
    
    function closeModal() {{
        modal.classList.remove('active');
        currentProduct = null;
    }}
    
    modalClose.onclick = closeModal;
    modal.onclick = (e) => {{ if (e.target === modal) closeModal(); }};
    
    function openPaymentModal(product) {{
        currentProduct = product;
        modalBody.innerHTML = 
            '<div class="product-summary">' +
                '<div class="summary-icon">' + product.icon + '</div>' +
                '<div class="summary-title">' + product.name + ' • ' + product.period + '</div>' +
                '<div class="summary-duration">' + product.duration + '</div>' +
                '<div class="summary-price">' + product.price + ' ₽</div>' +
            '</div>' +
            '<div class="payment-methods">' +
                '<div class="payment-method" data-method="yoomoney">' +
                    '<div class="payment-method-left">' +
                        '<div class="payment-icon">💳</div>' +
                        '<div class="payment-info">' +
                            '<h4>Банковская карта</h4>' +
                            '<p>Visa • Mastercard • Мир • SBP</p>' +
                        '</div>' +
                    '</div>' +
                    '<div class="payment-amount">' + product.price + ' ₽</div>' +
                '</div>' +
                '<div class="payment-method" data-method="stars">' +
                    '<div class="payment-method-left">' +
                        '<div class="payment-icon">⭐️</div>' +
                        '<div class="payment-info">' +
                            '<h4>Telegram Stars</h4>' +
                            '<p>Мгновенная оплата</p>' +
                        '</div>' +
                    '</div>' +
                    '<div class="payment-amount">' + product.stars + ' ⭐️</div>' +
                '</div>' +
                '<div class="payment-method" data-method="crypto">' +
                    '<div class="payment-method-left">' +
                        '<div class="payment-icon">₿</div>' +
                        '<div class="payment-info">' +
                            '<h4>Криптовалюта</h4>' +
                            '<p>USDT • BTC • ETH • TON</p>' +
                        '</div>' +
                    '</div>' +
                    '<div class="payment-amount">' + product.usdt + ' USDT</div>' +
                '</div>' +
                '<div class="payment-method" data-method="gold">' +
                    '<div class="payment-method-left">' +
                        '<div class="payment-icon">💰</div>' +
                        '<div class="payment-info">' +
                            '<h4>GOLD</h4>' +
                            '<p>Игровая валюта</p>' +
                        '</div>' +
                    '</div>' +
                    '<div class="payment-amount">' + product.gold + ' 🪙</div>' +
                '</div>' +
                '<div class="payment-method" data-method="nft">' +
                    '<div class="payment-method-left">' +
                        '<div class="payment-icon">🎨</div>' +
                        '<div class="payment-info">' +
                            '<h4>NFT</h4>' +
                            '<p>Коллекционные токены</p>' +
                        '</div>' +
                    '</div>' +
                    '<div class="payment-amount">' + product.nft + ' 🖼️</div>' +
                '</div>' +
            '</div>';
        
        document.querySelectorAll('.payment-method').forEach(el => {{
            el.onclick = () => processPayment(el.dataset.method);
        }});
        openModal();
    }}
    
    async function processPayment(method) {{
        modalBody.innerHTML = '<div class="status-view"><div class="status-icon spin">⏳</div><div class="status-title">Создание платежа</div><div class="status-desc">Пожалуйста, подождите...</div></div>';
        
        try {{
            const res = await fetch(API + '/create_payment', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{
                    product_id: currentProduct.id,
                    method: method,
                    user_id: user.id,
                    user_name: user.first_name + (user.last_name || ''),
                    init_data: tg.initData
                }})
            }});
            const data = await res.json();
            if (!data.success) throw new Error(data.error);
            
            if (method === 'yoomoney') {{
                modalBody.innerHTML = 
                    '<div class="status-view">' +
                        '<div class="status-icon">💳</div>' +
                        '<div class="status-title">' + currentProduct.price + ' ₽</div>' +
                        '<div class="status-desc">Заказ #' + data.order_id.slice(-8) + '</div>' +
                        '<button class="action-btn" id="payBtn">🔗 Перейти к оплате</button>' +
                        '<button class="action-btn secondary" id="checkBtn">✓ Проверить оплату</button>' +
                    '</div>';
                document.getElementById('payBtn').onclick = () => window.open(data.payment_url, '_blank');
                document.getElementById('checkBtn').onclick = () => checkPayment(data.order_id);
            }} else if (method === 'stars') {{
                modalBody.innerHTML = 
                    '<div class="status-view">' +
                        '<div class="status-icon">⭐️</div>' +
                        '<div class="status-title">' + currentProduct.stars + ' Stars</div>' +
                        '<div class="status-desc">Оплата через Telegram Stars</div>' +
                        '<button class="action-btn" id="starsBtn">⭐️ Оплатить в боте</button>' +
                    '</div>';
                document.getElementById('starsBtn').onclick = () => tg.openTelegramLink('https://t.me/aimnoob_bot?start=buy_stars_' + currentProduct.id);
            }} else if (method === 'crypto') {{
                modalBody.innerHTML = 
                    '<div class="status-view">' +
                        '<div class="status-icon">₿</div>' +
                        '<div class="status-title">' + currentProduct.usdt + ' USDT</div>' +
                        '<div class="status-desc">Заказ #' + data.order_id.slice(-8) + '</div>' +
                        '<button class="action-btn" id="payBtn">🔗 Оплатить криптовалютой</button>' +
                        '<button class="action-btn secondary" id="checkBtn">✓ Проверить оплату</button>' +
                    '</div>';
                document.getElementById('payBtn').onclick = () => window.open(data.payment_url, '_blank');
                document.getElementById('checkBtn').onclick = () => checkCryptoPayment(data.order_id, data.invoice_id);
            }} else {{
                const msg = 'Привет! Хочу купить AimNoob ' + currentProduct.name + ' на ' + currentProduct.period + ' за ' + (method === 'gold' ? currentProduct.gold + ' GOLD' : currentProduct.nft + ' NFT');
                modalBody.innerHTML = 
                    '<div class="status-view">' +
                        '<div class="status-icon">' + (method === 'gold' ? '💰' : '🎨') + '</div>' +
                        '<div class="status-title">' + (method === 'gold' ? currentProduct.gold + ' GOLD' : currentProduct.nft + ' NFT') + '</div>' +
                        '<div class="status-desc">Заказ #' + data.order_id.slice(-8) + '</div>' +
                        '<button class="action-btn" id="msgBtn">💬 Написать в поддержку</button>' +
                        '<button class="action-btn secondary" id="doneBtn">✓ Я написал</button>' +
                    '</div>';
                document.getElementById('msgBtn').onclick = () => window.open('https://t.me/' + SUPPORT + '?text=' + encodeURIComponent(msg), '_blank');
                document.getElementById('doneBtn').onclick = () => {{ closeModal(); toast('Заказ отправлен! Ожидайте подтверждение'); }};
            }}
        }} catch(e) {{
            toast(e.message, 'error');
            setTimeout(() => openPaymentModal(currentProduct), 1000);
        }}
    }}
    
    async function checkPayment(orderId) {{
        modalBody.innerHTML = '<div class="status-view"><div class="status-icon spin">⏳</div><div class="status-title">Проверка платежа</div><div class="status-desc">Это может занять 15-25 секунд</div></div>';
        
        for (let i = 0; i < 5; i++) {{
            const res = await fetch(API + '/check_payment', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ order_id: orderId }})
            }});
            const data = await res.json();
            if (data.paid) {{
                showSuccess(data.license_key);
                return;
            }}
            await new Promise(r => setTimeout(r, 3000));
        }}
        
        modalBody.innerHTML = 
            '<div class="status-view">' +
                '<div class="status-icon">⏳</div>' +
                '<div class="status-title">Платеж не найден</div>' +
                '<div class="status-desc">Попробуйте через 1-2 минуты</div>' +
                '<button class="action-btn secondary" id="retryBtn">🔄 Повторить проверку</button>' +
            '</div>';
        document.getElementById('retryBtn').onclick = () => checkPayment(orderId);
    }}
    
    async function checkCryptoPayment(orderId, invoiceId) {{
        modalBody.innerHTML = '<div class="status-view"><div class="status-icon spin">⏳</div><div class="status-title">Проверка платежа</div><div class="status-desc">Ожидание подтверждения сети</div></div>';
        
        for (let i = 0; i < 5; i++) {{
            const res = await fetch(API + '/check_crypto', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ invoice_id: invoiceId, order_id: orderId }})
            }});
            const data = await res.json();
            if (data.paid) {{
                showSuccess(data.license_key);
                return;
            }}
            await new Promise(r => setTimeout(r, 5000));
        }}
        
        modalBody.innerHTML = 
            '<div class="status-view">' +
                '<div class="status-icon">⏳</div>' +
                '<div class="status-title">В обработке</div>' +
                '<div class="status-desc">Платеж не подтвержден</div>' +
                '<button class="action-btn secondary" id="retryBtn">🔄 Повторить проверку</button>' +
            '</div>';
        document.getElementById('retryBtn').onclick = () => checkCryptoPayment(orderId, invoiceId);
    }}
    
    function showSuccess(key) {{
        licenses.unshift({{
            key: key,
            product: currentProduct.name + ' • ' + currentProduct.period,
            date: new Date().toISOString()
        }});
        saveLicenses();
        
        modalBody.innerHTML = 
            '<div class="status-view">' +
                '<div class="status-icon">✓</div>' +
                '<div class="status-title">Оплата подтверждена!</div>' +
                '<div class="status-desc">Ваш лицензионный ключ активирован</div>' +
                '<div class="key-box" id="keyBox">🔑 ' + key + '</div>' +
                '<button class="action-btn" id="downloadBtn">📥 Скачать AimNoob</button>' +
                '<button class="action-btn secondary" id="myKeysBtn">🔑 Мои лицензии</button>' +
            '</div>';
        
        document.getElementById('keyBox').onclick = () => copyToClipboard(key);
        document.getElementById('downloadBtn').onclick = () => window.open(DOWNLOAD_URL, '_blank');
        document.getElementById('myKeysBtn').onclick = () => {{ closeModal(); switchTab('licenses'); }};
        
        if (tg.HapticFeedback) tg.HapticFeedback.notificationOccurred('success');
        renderLicenses();
    }}
    
    function renderShop() {{
        let html = '';
        ['android', 'ios'].forEach(platform => {{
            const platformName = platform === 'android' ? '📱 Android' : '🍎 iOS';
            html += '<div style="margin-bottom: 28px;"><div class="section-title" style="font-size: 18px; font-weight: 700; margin-bottom: 16px; padding-left: 4px;">' + platformName + '</div><div class="products-grid">';
            PRODUCTS[platform].forEach(p => {{
                const isLast = p.id.includes('forever');
                html += '<div class="product-card' + (isLast ? ' full-width' : '') + '" data-product=\'' + JSON.stringify(p) + '\'>';
                if (p.hit) html += '<div class="product-badge">🔥 HIT</div>';
                html += '<div class="product-content">';
                html += '<div class="product-icon">' + p.icon + '</div>';
                if (!isLast) {{
                    html += '<div class="product-name">' + p.name + '</div>';
                    html += '<div class="product-period">' + p.period + ' • ' + p.duration + '</div>';
                    html += '<div class="product-price">' + p.price + ' <small>₽</small></div>';
                    html += '<div class="product-features">' + p.features.map(f => '<span class="feature">' + f + '</span>').join('') + '</div>';
                    html += '<button class="buy-btn" data-id="' + p.id + '">Купить</button>';
                }} else {{
                    html += '<div class="product-info"><div class="product-name">' + p.name + ' ' + p.period + '</div><div class="product-period">' + p.duration + '</div></div>';
                    html += '<div class="product-price">' + p.price + ' <small>₽</small></div>';
                    html += '<button class="buy-btn" data-id="' + p.id + '">Купить</button>';
                }}
                html += '</div></div>';
            }});
            html += '</div></div>';
        }});
        document.getElementById('page-shop').innerHTML = html;
        
        document.querySelectorAll('.buy-btn').forEach(btn => {{
            btn.onclick = (e) => {{
                e.stopPropagation();
                let product = null;
                for (const p of [...PRODUCTS.android, ...PRODUCTS.ios]) {{
                    if (p.id === btn.dataset.id) product = p;
                }}
                if (product) openPaymentModal(product);
            }};
        }});
    }}
    
    function renderLicenses() {{
        const container = document.getElementById('page-licenses');
        if (licenses.length === 0) {{
            container.innerHTML = 
                '<div class="empty-state">' +
                    '<div class="empty-icon">🔑</div>' +
                    '<div class="empty-text">У вас пока нет активных лицензий</div>' +
                    '<button class="action-btn" id="goToShopBtn">Перейти в магазин</button>' +
                '</div>';
            document.getElementById('goToShopBtn').onclick = () => switchTab('shop');
            return;
        }}
        
        let html = '<div style="margin-bottom: 28px;"><div style="font-size: 18px; font-weight: 700; margin-bottom: 16px;">Мои лицензии</div>';
        licenses.forEach(l => {{
            const date = new Date(l.date).toLocaleDateString('ru-RU', {{ day: 'numeric', month: 'long', year: 'numeric' }});
            html += 
                '<div class="license-card">' +
                    '<div class="license-header">' +
                        '<div class="license-icon">🎯</div>' +
                        '<div class="license-info">' +
                            '<h4>' + l.product + '</h4>' +
                            '<div class="license-date">Активирована: ' + date + '</div>' +
                        '</div>' +
                    '</div>' +
                    '<div class="license-key" data-key="' + l.key + '">' + l.key + '</div>' +
                    '<button class="license-copy-btn" data-key="' + l.key + '">📋 Скопировать ключ</button>' +
                '</div>';
        }});
        html += '</div>';
        container.innerHTML = html;
        
        document.querySelectorAll('.license-key, .license-copy-btn').forEach(el => {{
            el.onclick = () => copyToClipboard(el.dataset.key);
        }});
    }}
    
    function renderProfile() {{
        const avatars = ['🎯', '🔥', '⚡', '💎', '⭐', '🎮', '🚀', '👑'];
        const avatar = avatars[Math.abs(user.id) % avatars.length];
        
        document.getElementById('page-profile').innerHTML = 
            '<div class="profile-card">' +
                '<div class="profile-avatar">' + avatar + '</div>' +
                '<div class="profile-name">' + (user.first_name || 'User') + (user.last_name ? ' ' + user.last_name : '') + '</div>' +
                '<div class="profile-username">@' + (user.username || 'user') + '</div>' +
                '<div class="profile-stats">' +
                    '<div class="stat-card"><div class="stat-number">' + licenses.length + '</div><div class="stat-label">Лицензий</div></div>' +
                    '<div class="stat-card"><div class="stat-number">v0.37</div><div class="stat-label">Версия</div></div>' +
                '</div>' +
            '</div>' +
            '<button class="action-btn" id="supportBtn">💬 Поддержка 24/7</button>' +
            '<button class="action-btn secondary" id="downloadAppBtn">📥 Скачать AimNoob</button>';
        
        document.getElementById('supportBtn').onclick = () => window.open('https://t.me/' + SUPPORT, '_blank');
        document.getElementById('downloadAppBtn').onclick = () => window.open(DOWNLOAD_URL, '_blank');
    }}
    
    function switchTab(tab) {{
        document.querySelectorAll('.tab, .nav-item').forEach(el => {{
            el.classList.toggle('active', el.dataset.tab === tab);
        }});
        document.querySelectorAll('.page').forEach(page => {{
            page.classList.toggle('active', page.id === 'page-' + tab);
        }});
        if (tab === 'shop') renderShop();
        else if (tab === 'licenses') renderLicenses();
        else if (tab === 'profile') renderProfile();
    }}
    
    document.querySelectorAll('.tab, .nav-item').forEach(el => {{
        el.onclick = () => switchTab(el.dataset.tab);
    }});
    
    renderShop();
    tg.ready();
}})();
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
                "amount": amount, "currency": "\u20bd", "payment_method": "\u041a\u0430\u0440\u0442\u043e\u0439",
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
                "amount": product['price_stars'], "currency": "\u2b50",
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
                    text="\U0001f3ae \u041c\u0430\u0433\u0430\u0437\u0438\u043d",
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
