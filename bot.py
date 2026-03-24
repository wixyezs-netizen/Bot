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
    raw = "{}_{}_{}" .format(time.time(), random.randint(100000, 999999), os.urandom(4).hex())
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def generate_license_key(order_id, user_id):
    raw = "{}_{}_{}" .format(order_id, user_id, os.urandom(8).hex())
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
        "\U0001f4e6 {product_name} ({duration})\n"
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
        "\U0001f4e6 {product_name} ({duration})\n"
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
        "{emoji} {product_name}\n\u23f1\ufe0f {duration}\n"
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


# =============================================
# ========== НОВЫЙ КРАСИВЫЙ MINIAPP ===========
# =============================================

MINIAPP_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,maximum-scale=1,user-scalable=no">
<title>AimNoob Shop</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');
*{{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}}
:root{{
--bg:#050510;--surface:rgba(255,255,255,.04);--surface2:rgba(255,255,255,.07);
--surface3:rgba(255,255,255,.12);--text:#fff;--text2:rgba(255,255,255,.6);
--text3:rgba(255,255,255,.35);--accent:#8b5cf6;--accent2:#a78bfa;
--pink:#ec4899;--amber:#f59e0b;--emerald:#10b981;--red:#ef4444;
--blue:#3b82f6;--grad1:linear-gradient(135deg,#8b5cf6,#ec4899);
--grad2:linear-gradient(135deg,#6366f1,#8b5cf6);--grad3:linear-gradient(135deg,#f59e0b,#ef4444);
--radius:16px;--radius2:20px;--radius3:28px;
--shadow:0 8px 32px rgba(0,0,0,.4);--shadow2:0 20px 60px rgba(0,0,0,.5)
}}
html{{height:100%}}
body{{
font-family:'Inter',system-ui,-apple-system,sans-serif;
background:var(--bg);color:var(--text);min-height:100%;
overflow-x:hidden;-webkit-font-smoothing:antialiased
}}

/* === CANVAS PARTICLES === */
#particles{{position:fixed;top:0;left:0;width:100%;height:100%;z-index:0;pointer-events:none}}

/* === GLOW ORBS === */
.orb{{position:fixed;border-radius:50%;filter:blur(80px);opacity:.15;z-index:0;pointer-events:none}}
.orb1{{width:300px;height:300px;background:#8b5cf6;top:-100px;left:-80px;animation:orbFloat1 15s ease-in-out infinite}}
.orb2{{width:250px;height:250px;background:#ec4899;bottom:-50px;right:-60px;animation:orbFloat2 18s ease-in-out infinite}}
.orb3{{width:200px;height:200px;background:#3b82f6;top:40%;left:50%;animation:orbFloat3 20s ease-in-out infinite}}
@keyframes orbFloat1{{0%,100%{{transform:translate(0,0)}}50%{{transform:translate(40px,60px)}}}}
@keyframes orbFloat2{{0%,100%{{transform:translate(0,0)}}50%{{transform:translate(-50px,-40px)}}}}
@keyframes orbFloat3{{0%,100%{{transform:translate(-50%,-50%)}}50%{{transform:translate(-30%,-30%)}}}}

/* === LAYOUT === */
.app{{max-width:480px;margin:0 auto;padding:16px 16px 100px;position:relative;z-index:1}}

/* === HEADER === */
.header{{text-align:center;padding:24px 0 28px;animation:fadeDown .6s ease}}
@keyframes fadeDown{{from{{opacity:0;transform:translateY(-20px)}}to{{opacity:1;transform:translateY(0)}}}}
.logo-wrap{{position:relative;display:inline-block;margin-bottom:14px}}
.logo{{
width:72px;height:72px;border-radius:22px;
background:var(--grad1);display:flex;align-items:center;justify-content:center;
font-size:36px;box-shadow:0 10px 40px rgba(139,92,246,.4);
animation:logoPulse 3s ease-in-out infinite
}}
@keyframes logoPulse{{0%,100%{{transform:scale(1)}}50%{{transform:scale(1.05)}}}}
.logo-ring{{
position:absolute;inset:-4px;border-radius:26px;
border:2px solid rgba(139,92,246,.3);
animation:ringRotate 8s linear infinite
}}
@keyframes ringRotate{{from{{transform:rotate(0)}}to{{transform:rotate(360deg)}}}}
.logo-dot{{
position:absolute;width:8px;height:8px;background:var(--amber);
border-radius:50%;top:-4px;left:50%;transform:translateX(-50%);
box-shadow:0 0 10px var(--amber)
}}
h1{{
font-size:26px;font-weight:900;letter-spacing:-.5px;
background:linear-gradient(135deg,#fff 0%,var(--accent2) 100%);
-webkit-background-clip:text;-webkit-text-fill-color:transparent
}}
.tagline{{color:var(--text2);font-size:12px;margin-top:4px;font-weight:500}}

/* === STATUS BAR === */
.status-bar{{
display:flex;align-items:center;justify-content:center;gap:8px;
padding:8px 16px;background:rgba(16,185,129,.1);border:1px solid rgba(16,185,129,.2);
border-radius:30px;margin:0 auto 24px;width:fit-content;font-size:11px;font-weight:600
}}
.status-dot{{width:6px;height:6px;background:var(--emerald);border-radius:50%;animation:blink 2s infinite}}
@keyframes blink{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}

/* === TABS === */
.tabs{{
display:flex;gap:4px;background:var(--surface);border-radius:14px;
padding:4px;margin-bottom:20px;position:sticky;top:0;z-index:50;
backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px)
}}
.tab{{
flex:1;padding:10px;border-radius:11px;border:none;
background:transparent;color:var(--text2);font-size:12px;
font-weight:600;cursor:pointer;transition:.3s;
display:flex;flex-direction:column;align-items:center;gap:3px
}}
.tab.active{{background:var(--surface3);color:var(--text);box-shadow:0 2px 12px rgba(0,0,0,.2)}}
.tab-icon{{font-size:18px}}

/* === PLATFORM SECTION === */
.section{{margin-bottom:24px;animation:fadeUp .5s ease}}
@keyframes fadeUp{{from{{opacity:0;transform:translateY(16px)}}to{{opacity:1;transform:translateY(0)}}}}
.section-head{{
display:flex;align-items:center;justify-content:space-between;
margin-bottom:14px;padding:0 4px
}}
.section-title{{font-size:17px;font-weight:700;display:flex;align-items:center;gap:8px}}
.section-badge{{
font-size:10px;font-weight:700;padding:3px 10px;
border-radius:20px;background:var(--surface2);color:var(--text2)
}}

/* === PRODUCT CARDS === */
.cards{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.card{{
background:var(--surface);border:1px solid rgba(255,255,255,.06);
border-radius:var(--radius2);overflow:hidden;cursor:pointer;
transition:all .35s cubic-bezier(.4,0,.2,1);position:relative
}}
.card:active{{transform:scale(.97)}}
.card::after{{
content:'';position:absolute;inset:0;border-radius:var(--radius2);
background:linear-gradient(135deg,rgba(139,92,246,.08),transparent 60%);
opacity:0;transition:.3s
}}
.card:hover::after,.card:active::after{{opacity:1}}
.card-shine{{
position:absolute;top:0;left:0;right:0;height:1px;
background:linear-gradient(90deg,transparent,rgba(255,255,255,.15),transparent);
transform:translateX(-100%);animation:shine 4s ease-in-out infinite
}}
@keyframes shine{{0%{{transform:translateX(-100%)}}40%,100%{{transform:translateX(100%)}}}}
.card-badge{{
position:absolute;top:8px;right:8px;z-index:2;
font-size:9px;font-weight:800;padding:3px 8px;border-radius:10px;
background:var(--grad3);color:#fff;text-transform:uppercase;letter-spacing:.5px
}}
.card-body{{padding:14px 12px;position:relative;z-index:1}}
.card-icon{{
width:44px;height:44px;border-radius:14px;
background:var(--surface2);display:flex;align-items:center;
justify-content:center;font-size:24px;margin:0 auto 10px
}}
.card-name{{font-size:14px;font-weight:700;text-align:center;margin-bottom:2px}}
.card-period{{font-size:10px;color:var(--text3);text-align:center;margin-bottom:10px;font-weight:500}}
.card-price{{text-align:center;margin-bottom:10px}}
.price-main{{font-size:20px;font-weight:900;color:var(--amber)}}
.price-old{{font-size:11px;color:var(--text3);text-decoration:line-through;margin-left:4px}}
.price-per{{font-size:9px;color:var(--text3);margin-top:2px}}
.card-features{{display:flex;flex-wrap:wrap;gap:4px;justify-content:center;margin-bottom:10px}}
.feat{{
font-size:8px;padding:2px 6px;border-radius:6px;
background:rgba(139,92,246,.1);color:var(--accent2);font-weight:600
}}
.card-btn{{
width:100%;padding:9px;border:none;border-radius:12px;
background:var(--grad1);color:#fff;font-size:12px;
font-weight:700;cursor:pointer;transition:.2s;
position:relative;overflow:hidden
}}
.card-btn:active{{transform:scale(.98)}}

/* === FULL WIDTH CARD === */
.card-full{{grid-column:1/-1}}
.card-full .card-body{{display:grid;grid-template-columns:auto 1fr auto;gap:14px;align-items:center}}
.card-full .card-icon{{margin:0}}
.card-full .card-info{{text-align:left}}
.card-full .card-info .card-name{{text-align:left;font-size:15px}}
.card-full .card-info .card-period{{text-align:left}}
.card-full .card-right{{text-align:right}}
.card-full .card-right .price-main{{font-size:22px}}
.card-full .card-btn{{grid-column:1/-1}}

/* === MODAL === */
.modal-overlay{{
display:none;position:fixed;inset:0;z-index:200;
background:rgba(0,0,0,.85);backdrop-filter:blur(12px);
-webkit-backdrop-filter:blur(12px);
align-items:flex-end;justify-content:center
}}
.modal-overlay.open{{display:flex}}
.modal{{
background:linear-gradient(180deg,#131325 0%,#0a0a18 100%);
border-radius:var(--radius3) var(--radius3) 0 0;
width:100%;max-width:480px;max-height:92vh;overflow-y:auto;
padding:0;animation:modalUp .4s cubic-bezier(.32,.72,.24,1.02);
border-top:1px solid rgba(255,255,255,.08)
}}
@keyframes modalUp{{from{{transform:translateY(100%)}}to{{transform:translateY(0)}}}}
.modal-handle{{
width:36px;height:4px;background:rgba(255,255,255,.2);
border-radius:4px;margin:10px auto 0
}}
.modal-head{{
display:flex;align-items:center;justify-content:space-between;
padding:16px 20px 12px;position:sticky;top:0;z-index:10;
background:linear-gradient(180deg,#131325,rgba(19,19,37,.95))
}}
.modal-title{{font-size:18px;font-weight:800}}
.modal-close{{
width:30px;height:30px;border-radius:50%;border:none;
background:var(--surface2);color:var(--text2);font-size:16px;
cursor:pointer;transition:.2s;display:flex;align-items:center;justify-content:center
}}
.modal-close:hover{{background:var(--red);color:#fff;transform:rotate(90deg)}}
.modal-body{{padding:0 20px 24px}}

/* === PAYMENT METHODS === */
.pay-list{{display:flex;flex-direction:column;gap:8px;margin:16px 0}}
.pay-item{{
display:flex;align-items:center;justify-content:space-between;
padding:14px 16px;background:var(--surface);border:1px solid transparent;
border-radius:var(--radius);cursor:pointer;transition:.3s
}}
.pay-item:active{{transform:scale(.98);border-color:var(--accent)}}
.pay-left{{display:flex;align-items:center;gap:12px}}
.pay-icon{{
width:42px;height:42px;border-radius:13px;background:var(--surface2);
display:flex;align-items:center;justify-content:center;font-size:20px
}}
.pay-name{{font-size:13px;font-weight:700}}
.pay-desc{{font-size:10px;color:var(--text3);margin-top:1px}}
.pay-amount{{font-size:15px;font-weight:800;color:var(--amber)}}

/* === ACTION BUTTON === */
.action-btn{{
width:100%;padding:15px;border:none;border-radius:var(--radius);
background:var(--grad1);color:#fff;font-size:15px;font-weight:700;
cursor:pointer;transition:.2s;position:relative;overflow:hidden;
box-shadow:0 4px 20px rgba(139,92,246,.3)
}}
.action-btn:active{{transform:scale(.98)}}
.action-btn.secondary{{background:var(--surface2);box-shadow:none;font-size:13px;padding:12px}}

/* === STATUS === */
.status-view{{text-align:center;padding:30px 0}}
.status-emoji{{font-size:56px;margin-bottom:12px}}
.status-emoji.spin{{animation:spin 1.2s linear infinite}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
.status-title{{font-size:18px;font-weight:700;margin-bottom:6px}}
.status-desc{{font-size:12px;color:var(--text2);line-height:1.5}}

/* === KEY BOX === */
.key-box{{
background:rgba(0,0,0,.4);border:1px solid var(--surface2);
padding:14px;border-radius:14px;font-family:'Courier New',monospace;
font-size:12px;text-align:center;word-break:break-all;
margin:12px 0;color:var(--amber);letter-spacing:1px;
cursor:pointer;transition:.2s
}}
.key-box:active{{background:rgba(139,92,246,.1)}}

/* === LICENSE CARD === */
.license-card{{
background:var(--surface);border:1px solid rgba(255,255,255,.06);
border-radius:var(--radius2);padding:16px;margin-bottom:10px
}}
.license-header{{display:flex;align-items:center;gap:10px;margin-bottom:10px}}
.license-icon{{
width:38px;height:38px;border-radius:12px;background:var(--grad1);
display:flex;align-items:center;justify-content:center;font-size:18px
}}
.license-name{{font-size:14px;font-weight:700}}
.license-date{{font-size:10px;color:var(--text3)}}
.license-key{{
background:rgba(0,0,0,.3);padding:10px;border-radius:10px;
font-family:monospace;font-size:11px;text-align:center;
color:var(--amber);cursor:pointer;margin-bottom:10px;
word-break:break-all;transition:.2s
}}
.license-key:active{{background:rgba(139,92,246,.1)}}
.license-copy{{
width:100%;padding:8px;border:none;border-radius:10px;
background:var(--surface2);color:var(--text);font-size:11px;
font-weight:600;cursor:pointer
}}

/* === PROFILE === */
.profile-card{{
background:var(--surface);border-radius:var(--radius3);
padding:24px;text-align:center;margin-bottom:16px;
border:1px solid rgba(255,255,255,.06)
}}
.profile-avatar{{
width:64px;height:64px;border-radius:20px;background:var(--grad2);
display:flex;align-items:center;justify-content:center;
font-size:30px;margin:0 auto 12px;
box-shadow:0 8px 24px rgba(99,102,241,.3)
}}
.profile-name{{font-size:18px;font-weight:800;margin-bottom:2px}}
.profile-username{{font-size:12px;color:var(--text3)}}
.profile-stats{{
display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:16px
}}
.stat-box{{
background:var(--surface2);border-radius:14px;padding:12px;text-align:center
}}
.stat-num{{font-size:20px;font-weight:900;color:var(--accent2)}}
.stat-label{{font-size:10px;color:var(--text3);margin-top:2px}}

/* === EMPTY === */
.empty{{text-align:center;padding:50px 20px}}
.empty-icon{{font-size:48px;margin-bottom:12px;opacity:.5}}
.empty-text{{font-size:14px;color:var(--text2);margin-bottom:16px}}

/* === PRODUCT SUMMARY IN MODAL === */
.product-summary{{
text-align:center;padding:16px;background:var(--surface);
border-radius:var(--radius);margin-bottom:16px
}}
.product-summary .ps-icon{{font-size:40px;margin-bottom:6px}}
.product-summary .ps-name{{font-size:16px;font-weight:700}}
.product-summary .ps-period{{font-size:11px;color:var(--text2)}}
.product-summary .ps-price{{font-size:24px;font-weight:900;color:var(--amber);margin-top:6px}}

/* === TOAST === */
.toast{{
position:fixed;bottom:80px;left:16px;right:16px;
padding:12px 16px;border-radius:14px;z-index:300;
display:flex;align-items:center;gap:10px;font-size:13px;font-weight:600;
animation:toastIn .3s ease;max-width:480px;margin:0 auto;
backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px)
}}
.toast.success{{background:rgba(16,185,129,.15);border:1px solid rgba(16,185,129,.3);color:var(--emerald)}}
.toast.error{{background:rgba(239,68,68,.15);border:1px solid rgba(239,68,68,.3);color:var(--red)}}
@keyframes toastIn{{from{{opacity:0;transform:translateY(20px)}}to{{opacity:1;transform:translateY(0)}}}}

/* === NAV === */
.nav{{
position:fixed;bottom:0;left:0;right:0;z-index:100;
background:rgba(10,10,22,.92);backdrop-filter:blur(20px);
-webkit-backdrop-filter:blur(20px);
border-top:1px solid rgba(255,255,255,.06);
display:flex;justify-content:space-around;padding:8px 16px 12px;
max-width:480px;margin:0 auto
}}
.nav-btn{{
display:flex;flex-direction:column;align-items:center;gap:2px;
background:none;border:none;color:var(--text3);font-size:10px;
font-weight:600;cursor:pointer;padding:6px 16px;border-radius:14px;transition:.3s
}}
.nav-btn.active{{color:var(--accent2);background:rgba(139,92,246,.1)}}
.nav-btn .ni{{font-size:20px;transition:.2s}}

.page{{display:none}}
.page.active{{display:block;animation:fadeUp .3s ease}}

/* scrollbar */
::-webkit-scrollbar{{width:3px}}
::-webkit-scrollbar-track{{background:transparent}}
::-webkit-scrollbar-thumb{{background:var(--surface3);border-radius:10px}}
</style>
</head>
<body>

<div class="orb orb1"></div>
<div class="orb orb2"></div>
<div class="orb orb3"></div>
<canvas id="particles"></canvas>

<div class="app">
  <div class="header">
    <div class="logo-wrap">
      <div class="logo">&#127919;</div>
      <div class="logo-ring"><div class="logo-dot"></div></div>
    </div>
    <h1>AimNoob</h1>
    <div class="tagline">Premium Cheat &#8226; Standoff 2</div>
  </div>

  <div class="status-bar">
    <span class="status-dot"></span>
    <span>v0.37.1 &#8226; Online &#8226; Undetected</span>
  </div>

  <div class="tabs">
    <button class="tab active" data-tab="shop"><span class="tab-icon">&#128722;</span>&#1052;&#1072;&#1075;&#1072;&#1079;&#1080;&#1085;</button>
    <button class="tab" data-tab="keys"><span class="tab-icon">&#128273;</span>&#1050;&#1083;&#1102;&#1095;&#1080;</button>
    <button class="tab" data-tab="profile"><span class="tab-icon">&#128100;</span>&#1055;&#1088;&#1086;&#1092;&#1080;&#1083;&#1100;</button>
  </div>

  <div id="page-shop" class="page active"></div>
  <div id="page-keys" class="page"></div>
  <div id="page-profile" class="page"></div>
</div>

<div class="nav">
  <button class="nav-btn active" data-tab="shop"><span class="ni">&#128722;</span><span>&#1064;&#1086;&#1087;</span></button>
  <button class="nav-btn" data-tab="keys"><span class="ni">&#128273;</span><span>&#1050;&#1083;&#1102;&#1095;&#1080;</span></button>
  <button class="nav-btn" data-tab="profile"><span class="ni">&#128100;</span><span>&#1055;&#1088;&#1086;&#1092;&#1080;&#1083;&#1100;</span></button>
</div>

<div class="modal-overlay" id="modal">
  <div class="modal">
    <div class="modal-handle"></div>
    <div class="modal-head">
      <div class="modal-title" id="mTitle">&#1047;&#1072;&#1082;&#1072;&#1079;</div>
      <button class="modal-close" id="mClose">&times;</button>
    </div>
    <div class="modal-body" id="mBody"></div>
  </div>
</div>

<script>
(function(){{
var DOWNLOAD_URL='{download_url}';
var SUPPORT='{support}';
var tg=window.Telegram.WebApp;
tg.expand();tg.enableClosingConfirmation();
try{{tg.MainButton.hide()}}catch(e){{}}

var API=window.location.origin+'/api';
var user=tg.initDataUnsafe&&tg.initDataUnsafe.user?tg.initDataUnsafe.user:{{id:Date.now(),first_name:'Guest',username:'user'}};
var licenses=JSON.parse(localStorage.getItem('an_lic')||'[]');
var selected=null;

var P={{
android:[
{{id:'apk_week',name:'Android',per:'&#1053;&#1077;&#1076;&#1077;&#1083;&#1103;',dur:'7 &#1076;&#1085;&#1077;&#1081;',price:150,stars:350,gold:350,nft:250,usdt:2,icon:'&#128241;',feats:['AimBot','WallHack','ESP'],pop:false,disc:0}},
{{id:'apk_month',name:'Android',per:'&#1052;&#1077;&#1089;&#1103;&#1094;',dur:'30 &#1076;&#1085;&#1077;&#1081;',price:350,stars:800,gold:800,nft:600,usdt:5,icon:'&#128241;',feats:['AimBot','WallHack','ESP','Anti-Ban'],pop:true,disc:15}},
{{id:'apk_forever',name:'Android',per:'&#1053;&#1072;&#1074;&#1089;&#1077;&#1075;&#1076;&#1072;',dur:'&#8734;',price:800,stars:1800,gold:1800,nft:1400,usdt:12,icon:'&#128241;',feats:['AimBot','WallHack','ESP','Anti-Ban','Updates'],pop:false,disc:30}}
],
ios:[
{{id:'ios_week',name:'iOS',per:'&#1053;&#1077;&#1076;&#1077;&#1083;&#1103;',dur:'7 &#1076;&#1085;&#1077;&#1081;',price:300,stars:700,gold:700,nft:550,usdt:4,icon:'&#127822;',feats:['AimBot','WallHack','ESP'],pop:false,disc:0}},
{{id:'ios_month',name:'iOS',per:'&#1052;&#1077;&#1089;&#1103;&#1094;',dur:'30 &#1076;&#1085;&#1077;&#1081;',price:450,stars:1000,gold:1000,nft:800,usdt:6,icon:'&#127822;',feats:['AimBot','WallHack','ESP','Anti-Ban'],pop:true,disc:10}},
{{id:'ios_forever',name:'iOS',per:'&#1053;&#1072;&#1074;&#1089;&#1077;&#1075;&#1076;&#1072;',dur:'&#8734;',price:850,stars:2000,gold:2000,nft:1600,usdt:12,icon:'&#127822;',feats:['AimBot','WallHack','ESP','Anti-Ban','Updates'],pop:false,disc:25}}
]
}};

// === PARTICLES ===
var cv=document.getElementById('particles'),cx=cv.getContext('2d');
function resizeCanvas(){{cv.width=window.innerWidth;cv.height=window.innerHeight}}
resizeCanvas();window.addEventListener('resize',resizeCanvas);
var pts=[];for(var i=0;i<40;i++)pts.push({{x:Math.random()*cv.width,y:Math.random()*cv.height,r:Math.random()*1.5+.5,dx:(Math.random()-.5)*.3,dy:(Math.random()-.5)*.3,o:Math.random()*.4+.1}});
function drawParticles(){{
cx.clearRect(0,0,cv.width,cv.height);
for(var i=0;i<pts.length;i++){{
var p=pts[i];p.x+=p.dx;p.y+=p.dy;
if(p.x<0||p.x>cv.width)p.dx*=-1;
if(p.y<0||p.y>cv.height)p.dy*=-1;
cx.beginPath();cx.arc(p.x,p.y,p.r,0,Math.PI*2);
cx.fillStyle='rgba(139,92,246,'+p.o+')';cx.fill();
for(var j=i+1;j<pts.length;j++){{
var q=pts[j],d=Math.hypot(p.x-q.x,p.y-q.y);
if(d<120){{cx.beginPath();cx.moveTo(p.x,p.y);cx.lineTo(q.x,q.y);cx.strokeStyle='rgba(139,92,246,'+(1-d/120)*.08+')';cx.stroke()}}
}}
}}
requestAnimationFrame(drawParticles);
}}
drawParticles();

// === TABS ===
function switchTab(t){{
document.querySelectorAll('.tab,.nav-btn').forEach(function(b){{b.classList.toggle('active',b.dataset.tab===t)}});
document.querySelectorAll('.page').forEach(function(p){{p.classList.toggle('active',p.id==='page-'+t)}});
if(t==='shop')renderShop();
else if(t==='keys')renderKeys();
else if(t==='profile')renderProfile();
}}
document.querySelectorAll('.tab,.nav-btn').forEach(function(b){{b.addEventListener('click',function(){{switchTab(b.dataset.tab)}})}});

// === RENDER SHOP ===
function renderShop(){{
var h='';
['android','ios'].forEach(function(pl){{
var icon=pl==='android'?'&#128241;':'&#127822;';
var name=pl==='android'?'Android':'iOS';
h+='<div class="section"><div class="section-head"><div class="section-title"><span>'+icon+'</span><span>'+name+'</span></div><div class="section-badge">3 &#1090;&#1072;&#1088;&#1080;&#1092;&#1072;</div></div><div class="cards">';
P[pl].forEach(function(p,i){{
var old=p.disc?Math.round(p.price*(1+p.disc/100)):'';
var days=parseInt(p.dur);var ppd=(!isNaN(days)&&days>0)?(p.price/days).toFixed(0)+'&#8381;/&#1076;&#1077;&#1085;&#1100;':'&#1083;&#1091;&#1095;&#1096;&#1072;&#1103; &#1094;&#1077;&#1085;&#1072;';
if(i===2){{
h+='<div class="card card-full" data-pid="'+p.id+'">';
h+='<div class="card-shine"></div>';
if(p.pop)h+='<div class="card-badge">&#128293; HIT</div>';
h+='<div class="card-body">';
h+='<div class="card-icon">'+p.icon+'</div>';
h+='<div class="card-info"><div class="card-name">'+p.name+' '+p.per+'</div><div class="card-period">'+p.dur+' &#8226; '+ppd+'</div></div>';
h+='<div class="card-right"><div class="price-main">'+p.price+' &#8381;</div>'+(old?'<div class="price-old">'+old+' &#8381;</div>':'')+'</div>';
h+='<button class="card-btn buy-btn" data-pid="'+p.id+'">&#128142; &#1050;&#1091;&#1087;&#1080;&#1090;&#1100;</button>';
h+='</div></div>';
}}else{{
h+='<div class="card" data-pid="'+p.id+'">';
h+='<div class="card-shine"></div>';
if(p.pop)h+='<div class="card-badge">&#128293; HIT</div>';
h+='<div class="card-body">';
h+='<div class="card-icon">'+p.icon+'</div>';
h+='<div class="card-name">'+p.name+'</div>';
h+='<div class="card-period">'+p.per+' &#8226; '+p.dur+'</div>';
h+='<div class="card-price"><span class="price-main">'+p.price+'&#8381;</span>'+(old?'<span class="price-old">'+old+'&#8381;</span>':'')+'</div>';
h+='<div class="price-per">'+ppd+'</div>';
h+='<div class="card-features">'+p.feats.map(function(f){{return '<span class="feat">'+f+'</span>'}}).join('')+'</div>';
h+='<button class="card-btn buy-btn" data-pid="'+p.id+'">'+(p.pop?'&#128293;':'&#128722;')+' &#1050;&#1091;&#1087;&#1080;&#1090;&#1100;</button>';
h+='</div></div>';
}}
}});
h+='</div></div>';
}});
document.getElementById('page-shop').innerHTML=h;
document.querySelectorAll('.buy-btn').forEach(function(b){{
b.addEventListener('click',function(e){{e.stopPropagation();var p=findP(b.dataset.pid);if(p)openPayModal(p)}});
}});
}}

function findP(id){{
var all=P.android.concat(P.ios);return all.find(function(x){{return x.id===id}})
}}

// === MODAL ===
function openModal(title){{document.getElementById('mTitle').textContent=title;document.getElementById('modal').classList.add('open')}}
function closeModal(){{document.getElementById('modal').classList.remove('open')}}
document.getElementById('mClose').addEventListener('click',closeModal);
document.getElementById('modal').addEventListener('click',function(e){{if(e.target===this)closeModal()}});

// === PAYMENT MODAL ===
function openPayModal(p){{
selected=p;
openModal('&#1057;&#1087;&#1086;&#1089;&#1086;&#1073; &#1086;&#1087;&#1083;&#1072;&#1090;&#1099;');
var mb=document.getElementById('mBody');
mb.innerHTML='<div class="product-summary"><div class="ps-icon">'+p.icon+'</div><div class="ps-name">'+p.name+' &#8226; '+p.per+'</div><div class="ps-period">'+p.dur+'</div><div class="ps-price">'+p.price+' &#8381;</div></div>'
+'<div class="pay-list">'
+'<div class="pay-item" data-m="yoomoney"><div class="pay-left"><div class="pay-icon">&#128179;</div><div><div class="pay-name">&#1050;&#1072;&#1088;&#1090;&#1086;&#1081;</div><div class="pay-desc">Visa, MC, &#1052;&#1080;&#1088;, SBP</div></div></div><div class="pay-amount">'+p.price+' &#8381;</div></div>'
+'<div class="pay-item" data-m="stars"><div class="pay-left"><div class="pay-icon">&#11088;</div><div><div class="pay-name">Telegram Stars</div><div class="pay-desc">&#1042;&#1089;&#1090;&#1088;&#1086;&#1077;&#1085;&#1085;&#1099;&#1077; &#1087;&#1083;&#1072;&#1090;&#1077;&#1078;&#1080;</div></div></div><div class="pay-amount">'+p.stars+' &#11088;</div></div>'
+'<div class="pay-item" data-m="crypto"><div class="pay-left"><div class="pay-icon">&#8383;</div><div><div class="pay-name">&#1050;&#1088;&#1080;&#1087;&#1090;&#1072;</div><div class="pay-desc">USDT, BTC, ETH, TON</div></div></div><div class="pay-amount">'+p.usdt+' USDT</div></div>'
+'<div class="pay-item" data-m="gold"><div class="pay-left"><div class="pay-icon">&#128176;</div><div><div class="pay-name">GOLD</div><div class="pay-desc">&#1048;&#1075;&#1088;&#1086;&#1074;&#1072;&#1103; &#1074;&#1072;&#1083;&#1102;&#1090;&#1072;</div></div></div><div class="pay-amount">'+p.gold+' &#129689;</div></div>'
+'<div class="pay-item" data-m="nft"><div class="pay-left"><div class="pay-icon">&#127912;</div><div><div class="pay-name">NFT</div><div class="pay-desc">&#1050;&#1086;&#1083;&#1083;&#1077;&#1082;&#1094;&#1080;&#1086;&#1085;&#1085;&#1099;&#1077;</div></div></div><div class="pay-amount">'+p.nft+' &#128444;</div></div>'
+'</div>';
mb.querySelectorAll('.pay-item').forEach(function(el){{
el.addEventListener('click',function(){{processPayment(el.dataset.m)}});
}});
}}

function processPayment(method){{
var mb=document.getElementById('mBody');
mb.innerHTML='<div class="status-view"><div class="status-emoji spin">&#9203;</div><div class="status-title">&#1057;&#1086;&#1079;&#1076;&#1072;&#1085;&#1080;&#1077; &#1087;&#1083;&#1072;&#1090;&#1077;&#1078;&#1072;...</div></div>';
fetch(API+'/create_payment',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{product_id:selected.id,method:method,user_id:user.id,user_name:user.first_name+' '+(user.last_name||''),init_data:tg.initData}})}}
).then(function(r){{return r.json()}}).then(function(res){{
if(!res.success)throw new Error(res.error||'Error');
if(method==='yoomoney')showPayView(res.payment_url,res.order_id,'&#128179;',selected.price+' &#8381;','ym');
else if(method==='stars'){{
mb.innerHTML='<div class="status-view"><div class="status-emoji">&#11088;</div><div class="status-title">'+selected.stars+' Stars</div><div class="status-desc">&#1054;&#1087;&#1083;&#1072;&#1090;&#1080;&#1090;&#1077; &#1074; &#1073;&#1086;&#1090;&#1077;</div></div><button class="action-btn" id="starsBtn">&#11088; &#1054;&#1087;&#1083;&#1072;&#1090;&#1080;&#1090;&#1100; &#1074; &#1073;&#1086;&#1090;&#1077;</button>';
document.getElementById('starsBtn').addEventListener('click',function(){{tg.openTelegramLink('https://t.me/aimnoob_bot?start=buy_stars_'+selected.id)}});
}}
else if(method==='crypto')showPayView(res.payment_url,res.order_id,'&#8383;',selected.usdt+' USDT','cr',res.invoice_id);
else showManual(method,res.order_id);
}}).catch(function(e){{toast(e.message,'error');setTimeout(function(){{openPayModal(selected)}},1200)}});
}}

function showPayView(url,oid,icon,amount,type,invoiceId){{
var mb=document.getElementById('mBody');
mb.innerHTML='<div class="status-view"><div class="status-emoji">'+icon+'</div><div class="status-title">'+amount+'</div><div class="status-desc">&#1047;&#1072;&#1082;&#1072;&#1079; #'+oid.slice(-8)+'</div></div><button class="action-btn" id="goPayBtn">&#128279; &#1054;&#1087;&#1083;&#1072;&#1090;&#1080;&#1090;&#1100;</button><button class="action-btn secondary" id="goCheckBtn" style="margin-top:8px">&#9989; &#1055;&#1088;&#1086;&#1074;&#1077;&#1088;&#1080;&#1090;&#1100; &#1086;&#1087;&#1083;&#1072;&#1090;&#1091;</button>';
document.getElementById('goPayBtn').addEventListener('click',function(){{window.open(url,'_blank')}});
document.getElementById('goCheckBtn').addEventListener('click',function(){{
if(type==='ym')checkYM(oid);else checkCR(oid,invoiceId);
}});
}}

function showManual(method,oid){{
var names={{gold:'GOLD',nft:'NFT'}},amounts={{gold:selected.gold,nft:selected.nft}},icons={{gold:'&#128176;',nft:'&#127912;'}};
var msg='&#1055;&#1088;&#1080;&#1074;&#1077;&#1090;! &#1061;&#1086;&#1095;&#1091; &#1082;&#1091;&#1087;&#1080;&#1090;&#1100; AimNoob '+selected.name+' &#1085;&#1072; '+selected.per+' &#1079;&#1072; '+amounts[method]+' '+names[method];
var mb=document.getElementById('mBody');
mb.innerHTML='<div class="status-view"><div class="status-emoji">'+icons[method]+'</div><div class="status-title">'+amounts[method]+' '+names[method]+'</div><div class="status-desc" style="background:var(--surface);padding:10px;border-radius:10px;margin:8px 0;font-size:11px;color:var(--text)">'+msg+'</div></div><button class="action-btn" id="manPayBtn">&#128172; &#1053;&#1072;&#1087;&#1080;&#1089;&#1072;&#1090;&#1100;</button><button class="action-btn secondary" id="manDoneBtn" style="margin-top:8px">&#9989; &#1071; &#1085;&#1072;&#1087;&#1080;&#1089;&#1072;&#1083;</button>';
document.getElementById('manPayBtn').addEventListener('click',function(){{window.open('https://t.me/'+SUPPORT+'?text='+encodeURIComponent(msg),'_blank')}});
document.getElementById('manDoneBtn').addEventListener('click',function(){{closeModal();toast('&#1047;&#1072;&#1082;&#1072;&#1079; &#1089;&#1086;&#1079;&#1076;&#1072;&#1085;!')}});
}}

function checkYM(oid){{
var mb=document.getElementById('mBody');
mb.innerHTML='<div class="status-view"><div class="status-emoji spin">&#9203;</div><div class="status-title">&#1055;&#1088;&#1086;&#1074;&#1077;&#1088;&#1082;&#1072;...</div><div class="status-desc">15-25 &#1089;&#1077;&#1082;&#1091;&#1085;&#1076;</div></div>';
fetch(API+'/check_payment',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{order_id:oid}})}}).then(function(r){{return r.json()}}).then(function(res){{
if(res.paid)showSuccess(res.license_key);
else{{mb.innerHTML='<div class="status-view"><div class="status-emoji">&#9203;</div><div class="status-title">&#1053;&#1077; &#1085;&#1072;&#1081;&#1076;&#1077;&#1085;</div><div class="status-desc">&#1055;&#1086;&#1087;&#1088;&#1086;&#1073;&#1091;&#1081;&#1090;&#1077; &#1095;&#1077;&#1088;&#1077;&#1079; 1-2 &#1084;&#1080;&#1085;</div></div><button class="action-btn secondary" id="retryBtn">&#128260; &#1055;&#1086;&#1074;&#1090;&#1086;&#1088;&#1080;&#1090;&#1100;</button>';document.getElementById('retryBtn').addEventListener('click',function(){{checkYM(oid)}})}}
}}).catch(function(){{toast('&#1054;&#1096;&#1080;&#1073;&#1082;&#1072;','error')}});
}}

function checkCR(oid,iid){{
var mb=document.getElementById('mBody');
mb.innerHTML='<div class="status-view"><div class="status-emoji spin">&#9203;</div><div class="status-title">&#1055;&#1088;&#1086;&#1074;&#1077;&#1088;&#1082;&#1072;...</div></div>';
fetch(API+'/check_crypto',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{invoice_id:iid,order_id:oid}})}}).then(function(r){{return r.json()}}).then(function(res){{
if(res.paid)showSuccess(res.license_key);
else{{mb.innerHTML='<div class="status-view"><div class="status-emoji">&#9203;</div><div class="status-title">&#1042; &#1086;&#1073;&#1088;&#1072;&#1073;&#1086;&#1090;&#1082;&#1077;</div></div><button class="action-btn secondary" id="retryCBtn">&#128260; &#1055;&#1086;&#1074;&#1090;&#1086;&#1088;&#1080;&#1090;&#1100;</button>';document.getElementById('retryCBtn').addEventListener('click',function(){{checkCR(oid,iid)}})}}
}}).catch(function(){{toast('&#1054;&#1096;&#1080;&#1073;&#1082;&#1072;','error')}});
}}

function showSuccess(key){{
licenses.push({{key:key,product:selected.name+' &#8226; '+selected.per,date:new Date().toISOString()}});
localStorage.setItem('an_lic',JSON.stringify(licenses));
var mb=document.getElementById('mBody');
document.getElementById('mTitle').textContent='&#1059;&#1089;&#1087;&#1077;&#1093;!';
mb.innerHTML='<div class="status-view"><div class="status-emoji">&#9989;</div><div class="status-title">&#1054;&#1087;&#1083;&#1072;&#1090;&#1072; &#1087;&#1086;&#1076;&#1090;&#1074;&#1077;&#1088;&#1078;&#1076;&#1077;&#1085;&#1072;!</div><div class="status-desc">&#1042;&#1072;&#1096; &#1082;&#1083;&#1102;&#1095; &#1072;&#1082;&#1090;&#1080;&#1074;&#1080;&#1088;&#1086;&#1074;&#1072;&#1085;</div></div><div class="key-box" id="keyBox">&#128273; '+key+'</div><button class="action-btn" id="dlBtn">&#128229; &#1057;&#1082;&#1072;&#1095;&#1072;&#1090;&#1100; AimNoob</button><button class="action-btn secondary" id="toKeysBtn" style="margin-top:8px">&#128203; &#1052;&#1086;&#1080; &#1082;&#1083;&#1102;&#1095;&#1080;</button>';
document.getElementById('keyBox').addEventListener('click',function(){{copyKey(key)}});
document.getElementById('dlBtn').addEventListener('click',function(){{window.open(DOWNLOAD_URL,'_blank')}});
document.getElementById('toKeysBtn').addEventListener('click',function(){{closeModal();switchTab('keys')}});
try{{tg.HapticFeedback.notificationOccurred('success')}}catch(e){{}}
}}

// === KEYS ===
function renderKeys(){{
var el=document.getElementById('page-keys');
if(!licenses.length){{
el.innerHTML='<div class="empty"><div class="empty-icon">&#128273;</div><div class="empty-text">&#1053;&#1077;&#1090; &#1072;&#1082;&#1090;&#1080;&#1074;&#1085;&#1099;&#1093; &#1082;&#1083;&#1102;&#1095;&#1077;&#1081;</div><button class="action-btn" id="goShopBtn">&#128722; &#1042; &#1084;&#1072;&#1075;&#1072;&#1079;&#1080;&#1085;</button></div>';
document.getElementById('goShopBtn').addEventListener('click',function(){{switchTab('shop')}});
return;
}}
var h='<div class="section"><div class="section-head"><div class="section-title"><span>&#128273;</span><span>&#1052;&#1086;&#1080; &#1083;&#1080;&#1094;&#1077;&#1085;&#1079;&#1080;&#1080;</span></div><div class="section-badge">'+licenses.length+'</div></div>';
licenses.forEach(function(l){{
h+='<div class="license-card"><div class="license-header"><div class="license-icon">&#127919;</div><div><div class="license-name">'+l.product+'</div><div class="license-date">'+new Date(l.date).toLocaleDateString('ru-RU')+'</div></div></div><div class="license-key" data-key="'+l.key+'">'+l.key+'</div><button class="license-copy" data-key="'+l.key+'">&#128203; &#1057;&#1082;&#1086;&#1087;&#1080;&#1088;&#1086;&#1074;&#1072;&#1090;&#1100;</button></div>';
}});
h+='</div>';
el.innerHTML=h;
el.querySelectorAll('.license-key,.license-copy').forEach(function(b){{
b.addEventListener('click',function(){{copyKey(b.dataset.key)}});
}});
}}

// === PROFILE ===
function renderProfile(){{
var avatars=['&#127919;','&#128293;','&#9889;','&#128142;','&#127775;','&#127918;','&#128640;','&#128170;'];
var av=avatars[Math.abs(user.id)%avatars.length];
var ln=user.last_name||'';
var un=user.username||'user';
var el=document.getElementById('page-profile');
el.innerHTML='<div class="profile-card"><div class="profile-avatar">'+av+'</div><div class="profile-name">'+user.first_name+' '+ln+'</div><div class="profile-username">@'+un+'</div><div class="profile-stats"><div class="stat-box"><div class="stat-num">'+licenses.length+'</div><div class="stat-label">&#1050;&#1083;&#1102;&#1095;&#1077;&#1081;</div></div><div class="stat-box"><div class="stat-num">v0.37</div><div class="stat-label">&#1042;&#1077;&#1088;&#1089;&#1080;&#1103;</div></div></div></div><button class="action-btn" id="supBtn">&#128172; &#1055;&#1086;&#1076;&#1076;&#1077;&#1088;&#1078;&#1082;&#1072;</button><button class="action-btn secondary" id="dlBtn2" style="margin-top:8px">&#128229; &#1057;&#1082;&#1072;&#1095;&#1072;&#1090;&#1100;</button>';
document.getElementById('supBtn').addEventListener('click',function(){{window.open('https://t.me/'+SUPPORT,'_blank')}});
document.getElementById('dlBtn2').addEventListener('click',function(){{window.open(DOWNLOAD_URL,'_blank')}});
}}

// === UTILS ===
function copyKey(k){{
navigator.clipboard.writeText(k).then(function(){{toast('&#1050;&#1083;&#1102;&#1095; &#1089;&#1082;&#1086;&#1087;&#1080;&#1088;&#1086;&#1074;&#1072;&#1085;!')}}).catch(function(){{toast('&#1054;&#1096;&#1080;&#1073;&#1082;&#1072;','error')}});
try{{tg.HapticFeedback.impactOccurred('light')}}catch(e){{}}
}}
function toast(msg,type){{
type=type||'success';
var t=document.createElement('div');t.className='toast '+type;
t.innerHTML='<span>'+(type==='success'?'&#9989;':'&#10060;')+'</span><span>'+msg+'</span>';
document.body.appendChild(t);setTimeout(function(){{t.remove()}},3000);
}}

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
