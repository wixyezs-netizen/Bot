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
import signal
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
    BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "8225924716:AAFZ_8Eu8aJ4BF7pErZY5Ef3emG9Cl9PikE")
    CRYPTOBOT_TOKEN: str = os.environ.get("CRYPTOBOT_TOKEN", "493276:AAtS7R1zYy0gaPw8eax1EgiWo0tdnd6dQ9c")
    YOOMONEY_ACCESS_TOKEN: str = os.environ.get("YOOMONEY_ACCESS_TOKEN", "4100118889570559.3288B2E716CEEB922A26BD6BEAC58648FBFB680CCF64E4E1447D714D6FB5EA5F01F1478FAC686BEF394C8A186C98982DE563C1ABCDF9F2F61D971B61DA3C7E486CA818F98B9E0069F1C0891E090DD56A11319D626A40F0AE8302A8339DED9EB7969617F191D93275F64C4127A3ECB7AED33FCDE91CA68690EB7534C67E6C219E")
    YOOMONEY_WALLET: str = os.environ.get("YOOMONEY_WALLET", "4100118889570559")

    SUPPORT_CHAT_USERNAME: str = os.environ.get("SUPPORT_CHAT_USERNAME", "aimnoob_support")
    SHOP_URL: str = os.environ.get("SHOP_URL", "https://aimnoob.ru")
    MINIAPP_URL: str = os.environ.get("MINIAPP_URL", "https://aimnoob.bothost.ru")
    DOWNLOAD_URL: str = os.environ.get("DOWNLOAD_URL", "https://go.linkify.ru/2GPF")
    WEB_PORT: int = int(os.environ.get("PORT", "8080"))

    ADMIN_IDS: set = set()
    ADMIN_ID: int = 0
    SUPPORT_CHAT_ID: int = 0

    MAX_PENDING_ORDERS: int = 1000
    ORDER_EXPIRY_SECONDS: int = 3600
    MAX_BALANCE_HISTORY: int = 100
    RATE_LIMIT_SECONDS: int = 2
    MAX_PAYMENT_CHECK_ATTEMPTS: int = 5
    PAYMENT_CHECK_INTERVAL: int = 5

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
    def __init__(self, max_pending: int = 1000, expiry_seconds: int = 3600):
        self._pending: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._confirmed: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._max_pending = max_pending
        self._expiry_seconds = expiry_seconds

    async def add_pending(self, order_id: str, order_data: Dict[str, Any]):
        async with self._lock:
            await self._cleanup_expired()
            if len(self._pending) >= self._max_pending:
                self._pending.popitem(last=False)
            self._pending[order_id] = order_data

    async def get_pending(self, order_id: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            return self._pending.get(order_id)

    async def confirm(self, order_id: str, extra_data: Dict[str, Any]) -> bool:
        async with self._lock:
            if order_id in self._confirmed:
                return False
            order = self._pending.pop(order_id, None)
            if order is None:
                return False
            self._confirmed[order_id] = {**order, **extra_data}
            return True

    async def is_confirmed(self, order_id: str) -> bool:
        async with self._lock:
            return order_id in self._confirmed

    async def get_confirmed(self, order_id: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            return self._confirmed.get(order_id)

    async def remove_pending(self, order_id: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            return self._pending.pop(order_id, None)

    async def get_stats(self) -> Dict[str, int]:
        async with self._lock:
            return {
                "pending": len(self._pending),
                "confirmed": len(self._confirmed)
            }

    async def get_recent_pending(self, limit: int = 5) -> list:
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
            logger.info(f"Cleaned up {len(expired)} expired orders")


class RateLimiter:
    def __init__(self, interval: float = 2.0):
        self._last_action: Dict[int, float] = {}
        self._interval = interval

    def check(self, user_id: int) -> bool:
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
def generate_order_id() -> str:
    raw = f"{time.time()}_{random.randint(100000, 999999)}_{os.urandom(4).hex()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def generate_license_key(order_id: str, user_id: int) -> str:
    raw = f"{order_id}_{user_id}_{os.urandom(8).hex()}"
    h = hashlib.sha256(raw.encode()).hexdigest()[:16].upper()
    return f"AIMNOOB-{h[:4]}-{h[4:8]}-{h[8:12]}-{h[12:16]}"


def is_admin(user_id: int) -> bool:
    return user_id in Config.ADMIN_IDS


def find_product(platform_code: str, period: str) -> Optional[Dict]:
    for p in PRODUCTS.values():
        if p['platform_code'] == platform_code and p['period'] == period:
            return p
    return None


def find_product_by_id(product_id: str) -> Optional[Dict]:
    return PRODUCTS.get(product_id)


def validate_telegram_init_data(init_data: str, bot_token: str) -> Optional[Dict]:
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
                data_pairs.append(f"{key}={values[0]}")
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
        logger.warning(f"initData validation failed: {e}")
        return None


def create_payment_link(amount: float, order_id: str, product_name: str) -> str:
    comment = f"\u0417\u0430\u043a\u0430\u0437 {order_id}: {product_name}"
    safe_targets = quote(comment, safe='')
    return (
        f"https://yoomoney.ru/quickpay/confirm.xml"
        f"?receiver={Config.YOOMONEY_WALLET}"
        f"&quickpay-form=shop"
        f"&targets={safe_targets}"
        f"&sum={amount}"
        f"&label={order_id}"
        f"&successURL={quote(f'https://t.me/aimnoob_bot?start=success', safe='')}"
        f"&paymentType=AC"
    )


# ========== ПЛАТЁЖНЫЕ СЕРВИСЫ ==========
class YooMoneyService:
    @staticmethod
    async def get_balance() -> Optional[float]:
        if not Config.YOOMONEY_ACCESS_TOKEN:
            return None
        headers = {"Authorization": f"Bearer {Config.YOOMONEY_ACCESS_TOKEN}"}
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
                        logger.error(f"YooMoney account-info {resp.status}: {body}")
        except Exception as e:
            logger.error(f"YooMoney balance error: {e}")
        return None

    @staticmethod
    async def check_payment(order_id: str, expected_amount: float, order_time: float) -> bool:
        if not Config.YOOMONEY_ACCESS_TOKEN:
            logger.warning("YOOMONEY_ACCESS_TOKEN not set")
            return False
        headers = {"Authorization": f"Bearer {Config.YOOMONEY_ACCESS_TOKEN}"}
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
                    logger.info(
                        f"YooMoney: {len(operations)} ops, "
                        f"looking for label={order_id}, amount={expected_amount}"
                    )
                    for op in operations:
                        if (op.get("label") == order_id
                                and op.get("status") == "success"
                                and abs(float(op.get("amount", 0)) - expected_amount) <= 5):
                            logger.info(f"Found payment by label: {op}")
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
                                logger.info(f"Found payment by amount+time: {op}")
                                return True
                        except (ValueError, TypeError):
                            pass
        except Exception as e:
            logger.error(f"YooMoney check error: {e}")
        return False


class CryptoBotService:
    BASE_URL = "https://pay.crypt.bot/api"

    @staticmethod
    async def create_invoice(
        amount_usdt: float, order_id: str, description: str
    ) -> Optional[Dict]:
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
            "paid_btn_url": f"https://t.me/aimnoob_bot?start=paid_{order_id}"
        }
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{CryptoBotService.BASE_URL}/createInvoice",
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
                    logger.error(f"CryptoBot createInvoice {resp.status}: {body}")
        except Exception as e:
            logger.error(f"CryptoBot API error: {e}")
        return None

    @staticmethod
    async def check_invoice(invoice_id: int) -> bool:
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
                    f"{CryptoBotService.BASE_URL}/getInvoices",
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
            logger.error(f"CryptoBot check error: {e}")
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
def platform_keyboard() -> InlineKeyboardMarkup:
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
            url=f"https://t.me/{Config.SUPPORT_CHAT_USERNAME}"
        )]
    ])


def subscription_keyboard(platform: str) -> InlineKeyboardMarkup:
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


def payment_methods_keyboard(product: Dict) -> InlineKeyboardMarkup:
    pc = product['platform_code']
    p = product['period']
    buttons = [
        [InlineKeyboardButton(text="\U0001f4b3 \u041a\u0430\u0440\u0442\u043e\u0439", callback_data=f"pay_yoomoney_{pc}_{p}")],
        [InlineKeyboardButton(text="\u2b50 Telegram Stars", callback_data=f"pay_stars_{pc}_{p}")],
        [InlineKeyboardButton(text="\u20bf \u041a\u0440\u0438\u043f\u0442\u043e\u0431\u043e\u0442", callback_data=f"pay_crypto_{pc}_{p}")],
        [InlineKeyboardButton(text="\U0001f4b0 GOLD", callback_data=f"pay_gold_{pc}_{p}")],
        [InlineKeyboardButton(text="\U0001f3a8 NFT", callback_data=f"pay_nft_{pc}_{p}")],
        [InlineKeyboardButton(text="\u25c0\ufe0f \u041d\u0430\u0437\u0430\u0434", callback_data="back_to_subscription")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def payment_keyboard(payment_url: str, order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\U0001f4b3 \u041e\u043f\u043b\u0430\u0442\u0438\u0442\u044c \u043a\u0430\u0440\u0442\u043e\u0439", url=payment_url)],
        [InlineKeyboardButton(text="\u2705 \u041f\u0440\u043e\u0432\u0435\u0440\u0438\u0442\u044c \u043e\u043f\u043b\u0430\u0442\u0443", callback_data=f"checkym_{order_id}")],
        [InlineKeyboardButton(text="\u274c \u041e\u0442\u043c\u0435\u043d\u0430", callback_data="restart")]
    ])


def crypto_payment_keyboard(invoice_url: str, order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\u20bf \u041e\u043f\u043b\u0430\u0442\u0438\u0442\u044c \u043a\u0440\u0438\u043f\u0442\u043e\u0439", url=invoice_url)],
        [InlineKeyboardButton(text="\u2705 \u041f\u0440\u043e\u0432\u0435\u0440\u0438\u0442\u044c \u043f\u043b\u0430\u0442\u0435\u0436", callback_data=f"checkcr_{order_id}")],
        [InlineKeyboardButton(text="\u274c \u041e\u0442\u043c\u0435\u043d\u0430", callback_data="restart")]
    ])


def support_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="\U0001f4ac \u041f\u043e\u0434\u0434\u0435\u0440\u0436\u043a\u0430",
            url=f"https://t.me/{Config.SUPPORT_CHAT_USERNAME}"
        )],
        [InlineKeyboardButton(text="\U0001f310 \u0421\u0430\u0439\u0442", url=Config.SHOP_URL)],
        [InlineKeyboardButton(text="\U0001f504 \u041d\u043e\u0432\u0430\u044f \u043f\u043e\u043a\u0443\u043f\u043a\u0430", callback_data="restart")]
    ])


def download_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\U0001f4e5 \u0421\u043a\u0430\u0447\u0430\u0442\u044c AimNoob", url=Config.DOWNLOAD_URL)],
        [InlineKeyboardButton(
            text="\U0001f4ac \u041f\u043e\u0434\u0434\u0435\u0440\u0436\u043a\u0430",
            url=f"https://t.me/{Config.SUPPORT_CHAT_USERNAME}"
        )],
        [InlineKeyboardButton(text="\U0001f310 \u0421\u0430\u0439\u0442", url=Config.SHOP_URL)],
        [InlineKeyboardButton(text="\U0001f504 \u041d\u043e\u0432\u0430\u044f \u043f\u043e\u043a\u0443\u043f\u043a\u0430", callback_data="restart")]
    ])


def about_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\u25c0\ufe0f \u041d\u0430\u0437\u0430\u0434", callback_data="back_to_platform")]
    ])


def admin_confirm_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\u2705 \u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044c", callback_data=f"admin_confirm_{order_id}")],
        [InlineKeyboardButton(text="\u274c \u041e\u0442\u043a\u043b\u043e\u043d\u0438\u0442\u044c", callback_data=f"admin_reject_{order_id}")]
    ])


def manual_payment_keyboard(support_url: str, sent_callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\U0001f4ac \u041f\u0435\u0440\u0435\u0439\u0442\u0438 \u043a \u043e\u043f\u043b\u0430\u0442\u0435", url=support_url)],
        [InlineKeyboardButton(text="\u2705 \u042f \u043d\u0430\u043f\u0438\u0441\u0430\u043b", callback_data=sent_callback)],
        [InlineKeyboardButton(text="\u274c \u041e\u0442\u043c\u0435\u043d\u0430", callback_data="restart")]
    ])


# ========== БИЗНЕС-ЛОГИКА ==========
async def process_successful_payment(order_id: str, source: str = "API") -> bool:
    order = await orders.get_pending(order_id)
    if not order:
        if await orders.is_confirmed(order_id):
            logger.info(f"Order {order_id} already confirmed")
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
        logger.warning(f"Order {order_id} confirm race condition, skipping")
        return False

    success_text = (
        f"\U0001f389 <b>\u041e\u043f\u043b\u0430\u0442\u0430 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0430!</b>\n\n"
        f"\u2728 \u0414\u043e\u0431\u0440\u043e \u043f\u043e\u0436\u0430\u043b\u043e\u0432\u0430\u0442\u044c \u0432 AimNoob!\n\n"
        f"\U0001f4e6 <b>\u0412\u0430\u0448\u0430 \u043f\u043e\u043a\u0443\u043f\u043a\u0430:</b>\n"
        f"{product['emoji']} {product['name']}\n"
        f"\u23f1\ufe0f \u0421\u0440\u043e\u043a: {product['duration']}\n"
        f"\U0001f50d \u041c\u0435\u0442\u043e\u0434: {source}\n\n"
        f"\U0001f511 <b>\u0412\u0430\u0448 \u043b\u0438\u0446\u0435\u043d\u0437\u0438\u043e\u043d\u043d\u044b\u0439 \u043a\u043b\u044e\u0447:</b>\n"
        f"<code>{license_key}</code>\n\n"
        f"\U0001f4e5 <b>\u0421\u043a\u0430\u0447\u0438\u0432\u0430\u043d\u0438\u0435:</b>\n"
        f"\U0001f447 \u041d\u0430\u0436\u043c\u0438\u0442\u0435 \u043a\u043d\u043e\u043f\u043a\u0443 \u043d\u0438\u0436\u0435 \u0434\u043b\u044f \u0437\u0430\u0433\u0440\u0443\u0437\u043a\u0438\n\n"
        f"\U0001f4ab <b>\u0410\u043a\u0442\u0438\u0432\u0430\u0446\u0438\u044f:</b>\n"
        f"1\ufe0f\u20e3 \u0421\u043a\u0430\u0447\u0430\u0439\u0442\u0435 \u0444\u0430\u0439\u043b \u043f\u043e \u043a\u043d\u043e\u043f\u043a\u0435 \u043d\u0438\u0436\u0435\n"
        f"2\ufe0f\u20e3 \u0423\u0441\u0442\u0430\u043d\u043e\u0432\u0438\u0442\u0435 \u043f\u0440\u0438\u043b\u043e\u0436\u0435\u043d\u0438\u0435\n"
        f"3\ufe0f\u20e3 \u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u043a\u043b\u044e\u0447 \u043f\u0440\u0438 \u0437\u0430\u043f\u0443\u0441\u043a\u0435\n"
        f"4\ufe0f\u20e3 \u041d\u0430\u0441\u043b\u0430\u0436\u0434\u0430\u0439\u0442\u0435\u0441\u044c \u0438\u0433\u0440\u043e\u0439! \U0001f3ae\n\n"
        f"\U0001f4ac \u041f\u043e\u0434\u0434\u0435\u0440\u0436\u043a\u0430: @{Config.SUPPORT_CHAT_USERNAME}"
    )

    try:
        await bot.send_message(
            user_id, success_text,
            reply_markup=download_keyboard()
        )
    except Exception as e:
        logger.error(f"Error sending to user {user_id}: {e}")

    admin_text = (
        f"\U0001f48e <b>\u041d\u041e\u0412\u0410\u042f \u041f\u0420\u041e\u0414\u0410\u0416\u0410 ({source})</b>\n\n"
        f"\U0001f464 {order['user_name']}\n"
        f"\U0001f194 {user_id}\n"
        f"\U0001f4e6 {product['name']} ({product['duration']})\n"
        f"\U0001f4b0 {order.get('amount', product['price'])} {order.get('currency', '\u20bd')}\n"
        f"\U0001f511 <code>{license_key}</code>\n"
        f"\U0001f4c5 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )
    for aid in Config.ADMIN_IDS:
        try:
            await bot.send_message(aid, admin_text)
        except Exception as e:
            logger.error(f"Error notifying admin {aid}: {e}")

    return True


async def send_admin_notification(
    user: types.User,
    product: Dict,
    payment_method: str,
    price: str,
    order_id: str
):
    message = (
        f"\U0001f514 <b>\u041d\u041e\u0412\u042b\u0419 \u0417\u0410\u041a\u0410\u0417</b>\n\n"
        f"\U0001f464 {user.full_name}\n"
        f"\U0001f194 <code>{user.id}</code>\n"
        f"\U0001f4e6 {product['name']} ({product['duration']})\n"
        f"\U0001f4b0 {price}\n"
        f"\U0001f4b3 {payment_method}\n"
        f"\U0001f194 <code>{order_id}</code>\n\n"
        f"\U0001f4c5 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )
    for aid in Config.ADMIN_IDS:
        try:
            await bot.send_message(
                aid, message,
                reply_markup=admin_confirm_keyboard(order_id)
            )
        except Exception as e:
            logger.error(f"Error sending to admin {aid}: {e}")


async def send_start_message(target, state: FSMContext):
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
            product_id = deep_link.removeprefix("buy_stars_")
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
                await bot.send_invoice(
                    chat_id=message.from_user.id,
                    title=f"AimNoob \u2014 {product['name']}",
                    description=f"\u041f\u043e\u0434\u043f\u0438\u0441\u043a\u0430 \u043d\u0430 {product['duration']} \u0434\u043b\u044f {product['platform']}",
                    payload=f"stars_{order_id}",
                    provider_token="",
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
        "\U0001f3ae <b>\u0412\u0435\u0440\u0441\u0438\u044f:</b> 0.37.1 (\u041c\u0430\u0440\u0442 2026)\n"
        "\U0001f525 <b>\u0421\u0442\u0430\u0442\u0443\u0441:</b> \u0410\u043a\u0442\u0438\u0432\u043d\u043e \u043e\u0431\u043d\u043e\u0432\u043b\u044f\u0435\u0442\u0441\u044f\n\n"
        "\U0001f6e0\ufe0f <b>\u0424\u0443\u043d\u043a\u0446\u0438\u043e\u043d\u0430\u043b:</b>\n"
        "\u2022 \U0001f3af \u0423\u043c\u043d\u044b\u0439 AimBot \u0441 \u043f\u043b\u0430\u0432\u043d\u043e\u0441\u0442\u044c\u044e\n"
        "\u2022 \U0001f441\ufe0f WallHack \u0447\u0435\u0440\u0435\u0437 \u043f\u0440\u0435\u043f\u044f\u0442\u0441\u0442\u0432\u0438\u044f\n"
        "\u2022 \U0001f4cd ESP \u0441 \u0438\u043d\u0444\u043e\u0440\u043c\u0430\u0446\u0438\u0435\u0439 \u043e\u0431 \u0438\u0433\u0440\u043e\u043a\u0430\u0445\n"
        "\u2022 \U0001f5fa\ufe0f \u041c\u0438\u043d\u0438-\u0440\u0430\u0434\u0430\u0440\n"
        "\u2022 \u2699\ufe0f \u0413\u0438\u0431\u043a\u0438\u0435 \u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438\n\n"
        "\U0001f6e1\ufe0f <b>\u0411\u0435\u0437\u043e\u043f\u0430\u0441\u043d\u043e\u0441\u0442\u044c:</b>\n"
        "\u2022 \u041e\u0431\u0445\u043e\u0434 \u0430\u043d\u0442\u0438\u0447\u0438\u0442\u043e\u0432\n"
        "\u2022 \u0420\u0435\u0433\u0443\u043b\u044f\u0440\u043d\u044b\u0435 \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u0438\u044f\n"
        "\u2022 \u0422\u0435\u0441\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435 \u043d\u0430 \u0431\u0435\u0437\u043e\u043f\u0430\u0441\u043d\u043e\u0441\u0442\u044c\n\n"
        f"\U0001f4ac \u041f\u043e\u0434\u0434\u0435\u0440\u0436\u043a\u0430: @{Config.SUPPORT_CHAT_USERNAME}"
    )
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
            "requirements": "\u2022 Android 10.0+\n\u2022 2 \u0413\u0411 \u0441\u0432\u043e\u0431\u043e\u0434\u043d\u043e\u0439 \u043f\u0430\u043c\u044f\u0442\u0438\n\u2022 Root \u043d\u0435 \u0442\u0440\u0435\u0431\u0443\u0435\u0442\u0441\u044f",
            "includes": "\u2022 APK \u0444\u0430\u0439\u043b \u0441 \u0447\u0438\u0442\u043e\u043c\n\u2022 \u0418\u043d\u0441\u0442\u0440\u0443\u043a\u0446\u0438\u044f \u043f\u043e \u0443\u0441\u0442\u0430\u043d\u043e\u0432\u043a\u0435\n\u2022 \u0422\u0435\u0445\u043d\u0438\u0447\u0435\u0441\u043a\u0430\u044f \u043f\u043e\u0434\u0434\u0435\u0440\u0436\u043a\u0430"
        },
        "ios": {
            "title": "\U0001f34e <b>iOS Version</b>",
            "requirements": "\u2022 iOS 14.0 - 18.0\n\u2022 \u0423\u0441\u0442\u0430\u043d\u043e\u0432\u043a\u0430 \u0447\u0435\u0440\u0435\u0437 AltStore\n\u2022 Jailbreak \u043d\u0435 \u0442\u0440\u0435\u0431\u0443\u0435\u0442\u0441\u044f",
            "includes": "\u2022 IPA \u0444\u0430\u0439\u043b \u0441 \u0447\u0438\u0442\u043e\u043c\n\u2022 \u041f\u043e\u0434\u0440\u043e\u0431\u043d\u0430\u044f \u0438\u043d\u0441\u0442\u0440\u0443\u043a\u0446\u0438\u044f\n\u2022 \u041f\u043e\u043c\u043e\u0449\u044c \u0432 \u0443\u0441\u0442\u0430\u043d\u043e\u0432\u043a\u0435"
        }
    }

    info = platform_info[platform]
    text = (
        f"{info['title']}\n\n"
        f"\U0001f527 <b>\u0422\u0440\u0435\u0431\u043e\u0432\u0430\u043d\u0438\u044f:</b>\n{info['requirements']}\n\n"
        f"\U0001f4e6 <b>\u0427\u0442\u043e \u0432\u0445\u043e\u0434\u0438\u0442:</b>\n{info['includes']}\n\n"
        f"\U0001f4b0 <b>\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0442\u0430\u0440\u0438\u0444:</b>"
    )

    await callback.message.edit_text(
        text, reply_markup=subscription_keyboard(platform)
    )
    await state.set_state(OrderState.choosing_subscription)
    await callback.answer()


@dp.callback_query(F.data.startswith("sub_"))
async def process_subscription(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    if len(parts) < 3:
        await callback.answer("\u274c \u041e\u0448\u0438\u0431\u043a\u0430", show_alert=True)
        return

    product_key = f"{parts[1]}_{parts[2]}"
    product = find_product_by_id(product_key)

    if not product:
        await callback.answer("\u274c \u041f\u0440\u043e\u0434\u0443\u043a\u0442 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d", show_alert=True)
        return

    await state.update_data(selected_product=product)

    text = (
        f"\U0001f6d2 <b>\u041e\u0444\u043e\u0440\u043c\u043b\u0435\u043d\u0438\u0435 \u043f\u043e\u043a\u0443\u043f\u043a\u0438</b>\n\n"
        f"{product['emoji']} <b>{product['name']}</b>\n"
        f"\u23f1\ufe0f \u0414\u043b\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0441\u0442\u044c: {product['duration']}\n\n"
        f"\U0001f48e <b>\u0421\u0442\u043e\u0438\u043c\u043e\u0441\u0442\u044c:</b>\n"
        f"\U0001f4b3 \u041a\u0430\u0440\u0442\u043e\u0439: {product['price']} \u20bd\n"
        f"\u2b50 Stars: {product['price_stars']} \u2b50\n"
        f"\u20bf \u041a\u0440\u0438\u043f\u0442\u0430: {product['price_crypto_usdt']} USDT\n"
        f"\U0001f4b0 GOLD: {product['price_gold']} \U0001fa99\n"
        f"\U0001f3a8 NFT: {product['price_nft']} \U0001f5bc\ufe0f\n\n"
        f"\U0001f3af <b>\u0421\u043f\u043e\u0441\u043e\u0431 \u043e\u043f\u043b\u0430\u0442\u044b:</b>"
    )

    await callback.message.edit_text(
        text, reply_markup=payment_methods_keyboard(product)
    )
    await state.set_state(OrderState.choosing_payment)
    await callback.answer()


# ========== ОПЛАТА КАРТОЙ ==========
@dp.callback_query(F.data.startswith("pay_yoomoney_"))
async def process_yoomoney_payment(callback: types.CallbackQuery):
    if not Config.YOOMONEY_WALLET:
        await callback.answer("\u274c \u041e\u043f\u043b\u0430\u0442\u0430 \u043a\u0430\u0440\u0442\u043e\u0439 \u0432\u0440\u0435\u043c\u0435\u043d\u043d\u043e \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u043d\u0430", show_alert=True)
        return

    parts = callback.data.split("_")
    if len(parts) < 4:
        await callback.answer("\u274c \u041e\u0448\u0438\u0431\u043a\u0430", show_alert=True)
        return

    product = find_product(parts[2], parts[3])
    if not product:
        await callback.answer("\u274c \u041f\u0440\u043e\u0434\u0443\u043a\u0442 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d", show_alert=True)
        return

    user_id = callback.from_user.id
    if not rate_limiter.check(user_id):
        await callback.answer("\u23f3 \u041f\u043e\u0434\u043e\u0436\u0434\u0438\u0442\u0435 \u043d\u0435\u043c\u043d\u043e\u0433\u043e...", show_alert=True)
        return

    order_id = generate_order_id()
    amount = product["price"]
    payment_url = create_payment_link(
        amount, order_id, f"{product['name']} ({product['duration']})"
    )

    await orders.add_pending(order_id, {
        "user_id": user_id,
        "user_name": callback.from_user.full_name,
        "product": product,
        "amount": amount,
        "currency": "\u20bd",
        "payment_method": "\u041a\u0430\u0440\u0442\u043e\u0439",
        "status": "pending",
        "created_at": time.time()
    })

    text = (
        f"\U0001f4b3 <b>\u041e\u043f\u043b\u0430\u0442\u0430 \u043a\u0430\u0440\u0442\u043e\u0439</b>\n\n"
        f"{product['emoji']} {product['name']}\n"
        f"\u23f1\ufe0f {product['duration']}\n"
        f"\U0001f4b0 \u041a \u043e\u043f\u043b\u0430\u0442\u0435: <b>{amount} \u20bd</b>\n"
        f"\U0001f194 \u041d\u043e\u043c\u0435\u0440 \u0437\u0430\u043a\u0430\u0437\u0430: <code>{order_id}</code>\n\n"
        f"\U0001f504 <b>\u0418\u043d\u0441\u0442\u0440\u0443\u043a\u0446\u0438\u044f:</b>\n"
        f"1\ufe0f\u20e3 \u041d\u0430\u0436\u043c\u0438\u0442\u0435 \u00ab\u041e\u043f\u043b\u0430\u0442\u0438\u0442\u044c \u043a\u0430\u0440\u0442\u043e\u0439\u00bb\n"
        f"2\ufe0f\u20e3 \u041e\u043f\u043b\u0430\u0442\u0438\u0442\u0435 \u0431\u0430\u043d\u043a\u043e\u0432\u0441\u043a\u043e\u0439 \u043a\u0430\u0440\u0442\u043e\u0439\n"
        f"3\ufe0f\u20e3 \u0412\u0435\u0440\u043d\u0438\u0442\u0435\u0441\u044c \u0438 \u043d\u0430\u0436\u043c\u0438\u0442\u0435 \u00ab\u041f\u0440\u043e\u0432\u0435\u0440\u0438\u0442\u044c \u043e\u043f\u043b\u0430\u0442\u0443\u00bb\n\n"
        f"\U0001f4ab <b>\u0410\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u0430\u044f \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 \u043f\u043b\u0430\u0442\u0435\u0436\u0430</b>"
    )

    await callback.message.edit_text(
        text, reply_markup=payment_keyboard(payment_url, order_id)
    )
    await send_admin_notification(
        callback.from_user, product, "\U0001f4b3 \u041a\u0430\u0440\u0442\u043e\u0439", f"{amount} \u20bd", order_id
    )
    await callback.answer()


# ========== ПРОВЕРКА ЮMONEY ==========
@dp.callback_query(F.data.startswith("checkym_"))
async def check_yoomoney_callback(callback: types.CallbackQuery):
    order_id = callback.data.removeprefix("checkym_")
    order = await orders.get_pending(order_id)

    if not order:
        if await orders.is_confirmed(order_id):
            await callback.answer("\u2705 \u0417\u0430\u043a\u0430\u0437 \u0443\u0436\u0435 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d!", show_alert=True)
        else:
            await callback.answer("\u274c \u0417\u0430\u043a\u0430\u0437 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d \u0438\u043b\u0438 \u0438\u0441\u0442\u0451\u043a", show_alert=True)
        return

    user_id = callback.from_user.id
    if not rate_limiter.check(user_id):
        await callback.answer("\u23f3 \u041f\u043e\u0434\u043e\u0436\u0434\u0438\u0442\u0435 \u043f\u0435\u0440\u0435\u0434 \u043f\u043e\u0432\u0442\u043e\u0440\u043d\u043e\u0439 \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u043e\u0439...", show_alert=True)
        return

    await callback.answer("\U0001f50d \u041f\u0440\u043e\u0432\u0435\u0440\u044f\u0435\u043c \u043f\u043b\u0430\u0442\u0435\u0436...")

    checking_msg = await callback.message.edit_text(
        "\U0001f504 <b>\u041f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 \u043f\u043b\u0430\u0442\u0435\u0436\u0430...</b>\n\n"
        "\U0001f50d \u041f\u043e\u0438\u0441\u043a \u0442\u0440\u0430\u043d\u0437\u0430\u043a\u0446\u0438\u0438 \u0432 \u0441\u0438\u0441\u0442\u0435\u043c\u0435\n"
        "\u23f3 \u041f\u043e\u0434\u043e\u0436\u0434\u0438\u0442\u0435 15-25 \u0441\u0435\u043a\u0443\u043d\u0434..."
    )

    payment_found = False
    for attempt in range(Config.MAX_PAYMENT_CHECK_ATTEMPTS):
        logger.info(f"Checking YooMoney {order_id}, attempt {attempt + 1}")
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
                "\u2705 <b>\u041f\u043b\u0430\u0442\u0435\u0436 \u043d\u0430\u0439\u0434\u0435\u043d!</b>\n\n"
                "\U0001f389 \u0412\u0430\u0448 \u0437\u0430\u043a\u0430\u0437 \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u0430\u043d\n"
                "\U0001f4e8 \u041f\u0440\u043e\u0432\u0435\u0440\u044c\u0442\u0435 \u043d\u043e\u0432\u043e\u0435 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435 \u2b06\ufe0f",
                reply_markup=support_keyboard()
            )
        else:
            await checking_msg.edit_text(
                "\u2705 <b>\u0417\u0430\u043a\u0430\u0437 \u0443\u0436\u0435 \u0431\u044b\u043b \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u0430\u043d</b>",
                reply_markup=support_keyboard()
            )
    else:
        product = order['product']
        payment_url = create_payment_link(
            order["amount"], order_id,
            f"{product['name']} ({product['duration']})"
        )
        fail_text = (
            f"\u23f3 <b>\u041f\u043b\u0430\u0442\u0435\u0436 \u043f\u043e\u043a\u0430 \u043d\u0435 \u043e\u0431\u043d\u0430\u0440\u0443\u0436\u0435\u043d</b>\n\n"
            f"\U0001f4b0 \u0421\u0443\u043c\u043c\u0430: {order['amount']} \u20bd\n"
            f"\U0001f194 \u0417\u0430\u043a\u0430\u0437: <code>{order_id}</code>\n\n"
            f"\U0001f50d <b>\u0412\u043e\u0437\u043c\u043e\u0436\u043d\u044b\u0435 \u043f\u0440\u0438\u0447\u0438\u043d\u044b:</b>\n"
            f"\u2022 \u041f\u043b\u0430\u0442\u0435\u0436 \u0435\u0449\u0435 \u043e\u0431\u0440\u0430\u0431\u0430\u0442\u044b\u0432\u0430\u0435\u0442\u0441\u044f (1-3 \u043c\u0438\u043d)\n"
            f"\u2022 \u041e\u043f\u043b\u0430\u0447\u0435\u043d\u0430 \u043d\u0435\u0442\u043e\u0447\u043d\u0430\u044f \u0441\u0443\u043c\u043c\u0430\n"
            f"\u2022 \u041f\u0440\u043e\u0431\u043b\u0435\u043c\u0430 \u043d\u0430 \u0441\u0442\u043e\u0440\u043e\u043d\u0435 \u0431\u0430\u043d\u043a\u0430\n\n"
            f"\u23f0 \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u0447\u0435\u0440\u0435\u0437 1-2 \u043c\u0438\u043d\u0443\u0442\u044b\n"
            f"\U0001f4ac \u0418\u043b\u0438 \u043e\u0431\u0440\u0430\u0442\u0438\u0442\u0435\u0441\u044c \u0432 \u043f\u043e\u0434\u0434\u0435\u0440\u0436\u043a\u0443"
        )
        await checking_msg.edit_text(
            fail_text,
            reply_markup=payment_keyboard(payment_url, order_id)
        )


# ========== ОПЛАТА STARS ==========
@dp.callback_query(F.data.startswith("pay_stars_"))
async def process_stars_payment(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    if len(parts) < 4:
        await callback.answer("\u274c \u041e\u0448\u0438\u0431\u043a\u0430", show_alert=True)
        return

    product = find_product(parts[2], parts[3])
    if not product:
        await callback.answer("\u274c \u041f\u0440\u043e\u0434\u0443\u043a\u0442 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d", show_alert=True)
        return

    user_id = callback.from_user.id
    if not rate_limiter.check(user_id):
        await callback.answer("\u23f3 \u041f\u043e\u0434\u043e\u0436\u0434\u0438\u0442\u0435...", show_alert=True)
        return

    order_id = generate_order_id()
    await orders.add_pending(order_id, {
        "user_id": user_id,
        "user_name": callback.from_user.full_name,
        "product": product,
        "amount": product['price_stars'],
        "currency": "\u2b50",
        "payment_method": "Telegram Stars",
        "status": "pending",
        "created_at": time.time()
    })

    await bot.send_invoice(
        chat_id=user_id,
        title=f"AimNoob \u2014 {product['name']}",
        description=f"\u041f\u043e\u0434\u043f\u0438\u0441\u043a\u0430 \u043d\u0430 {product['duration']} \u0434\u043b\u044f {product['platform']}",
        payload=f"stars_{order_id}",
        provider_token="",
        currency="XTR",
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
        order_id = payload.removeprefix("stars_")
        await process_successful_payment(order_id, "Telegram Stars")


# ========== ОПЛАТА КРИПТО ==========
@dp.callback_query(F.data.startswith("pay_crypto_"))
async def process_crypto_payment(callback: types.CallbackQuery):
    if not Config.CRYPTOBOT_TOKEN:
        await callback.answer("\u274c \u041a\u0440\u0438\u043f\u0442\u043e\u043e\u043f\u043b\u0430\u0442\u0430 \u0432\u0440\u0435\u043c\u0435\u043d\u043d\u043e \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u043d\u0430", show_alert=True)
        return

    parts = callback.data.split("_")
    if len(parts) < 4:
        await callback.answer("\u274c \u041e\u0448\u0438\u0431\u043a\u0430", show_alert=True)
        return

    product = find_product(parts[2], parts[3])
    if not product:
        await callback.answer("\u274c \u041f\u0440\u043e\u0434\u0443\u043a\u0442 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d", show_alert=True)
        return

    user_id = callback.from_user.id
    if not rate_limiter.check(user_id):
        await callback.answer("\u23f3 \u041f\u043e\u0434\u043e\u0436\u0434\u0438\u0442\u0435...", show_alert=True)
        return

    order_id = generate_order_id()
    amount_usdt = product["price_crypto_usdt"]
    description = f"AimNoob {product['name']} ({product['duration']})"

    invoice_data = await CryptoBotService.create_invoice(
        amount_usdt, order_id, description
    )
    if not invoice_data:
        await callback.answer(
            "\u274c \u041e\u0448\u0438\u0431\u043a\u0430 \u0441\u043e\u0437\u0434\u0430\u043d\u0438\u044f \u0438\u043d\u0432\u043e\u0439\u0441\u0430. \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u043f\u043e\u0437\u0436\u0435.",
            show_alert=True
        )
        return

    await orders.add_pending(order_id, {
        "user_id": user_id,
        "user_name": callback.from_user.full_name,
        "product": product,
        "amount": amount_usdt,
        "currency": "USDT",
        "payment_method": "CryptoBot",
        "status": "pending",
        "invoice_id": invoice_data["invoice_id"],
        "created_at": time.time()
    })

    text = (
        f"\u20bf <b>\u041a\u0440\u0438\u043f\u0442\u043e\u043e\u043f\u043b\u0430\u0442\u0430</b>\n\n"
        f"{product['emoji']} {product['name']}\n"
        f"\u23f1\ufe0f {product['duration']}\n"
        f"\U0001f4b0 \u041a \u043e\u043f\u043b\u0430\u0442\u0435: <b>{amount_usdt} USDT</b>\n"
        f"\U0001f194 \u0417\u0430\u043a\u0430\u0437: <code>{order_id}</code>\n\n"
        f"\U0001fa99 <b>\u041f\u0440\u0438\u043d\u0438\u043c\u0430\u0435\u043c\u044b\u0435 \u0432\u0430\u043b\u044e\u0442\u044b:</b>\n"
        f"USDT, BTC, ETH, TON, LTC, BNB, TRX \u0438 \u0434\u0440.\n\n"
        f"\U0001f504 <b>\u0418\u043d\u0441\u0442\u0440\u0443\u043a\u0446\u0438\u044f:</b>\n"
        f"1\ufe0f\u20e3 \u041d\u0430\u0436\u043c\u0438\u0442\u0435 \u00ab\u041e\u043f\u043b\u0430\u0442\u0438\u0442\u044c \u043a\u0440\u0438\u043f\u0442\u043e\u0439\u00bb\n"
        f"2\ufe0f\u20e3 \u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0432\u0430\u043b\u044e\u0442\u0443 \u0438 \u043f\u0435\u0440\u0435\u0432\u0435\u0434\u0438\u0442\u0435\n"
        f"3\ufe0f\u20e3 \u041d\u0430\u0436\u043c\u0438\u0442\u0435 \u00ab\u041f\u0440\u043e\u0432\u0435\u0440\u0438\u0442\u044c \u043f\u043b\u0430\u0442\u0435\u0436\u00bb"
    )

    await callback.message.edit_text(
        text,
        reply_markup=crypto_payment_keyboard(invoice_data["pay_url"], order_id)
    )
    await send_admin_notification(
        callback.from_user, product, "\u20bf CryptoBot",
        f"{amount_usdt} USDT", order_id
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("checkcr_"))
async def check_crypto_callback(callback: types.CallbackQuery):
    order_id = callback.data.removeprefix("checkcr_")
    order = await orders.get_pending(order_id)

    if not order:
        if await orders.is_confirmed(order_id):
            await callback.answer("\u2705 \u0423\u0436\u0435 \u043e\u043f\u043b\u0430\u0447\u0435\u043d\u043e!", show_alert=True)
        else:
            await callback.answer("\u274c \u0417\u0430\u043a\u0430\u0437 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d", show_alert=True)
        return

    user_id = callback.from_user.id
    if not rate_limiter.check(user_id):
        await callback.answer("\u23f3 \u041f\u043e\u0434\u043e\u0436\u0434\u0438\u0442\u0435...", show_alert=True)
        return

    await callback.answer("\U0001f50d \u041f\u0440\u043e\u0432\u0435\u0440\u044f\u0435\u043c...")

    invoice_id = order.get("invoice_id")
    if not invoice_id:
        await callback.answer("\u274c \u041e\u0448\u0438\u0431\u043a\u0430: \u043d\u0435\u0442 invoice_id", show_alert=True)
        return

    is_paid = await CryptoBotService.check_invoice(invoice_id)
    if is_paid:
        success = await process_successful_payment(order_id, "CryptoBot")
        if success:
            await callback.message.edit_text(
                "\u2705 <b>\u041a\u0440\u0438\u043f\u0442\u043e\u043f\u043b\u0430\u0442\u0435\u0436 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d!</b>\n\n"
                "\U0001f389 \u0417\u0430\u043a\u0430\u0437 \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u0430\u043d\n"
                "\U0001f4e8 \u041a\u043b\u044e\u0447 \u043e\u0442\u043f\u0440\u0430\u0432\u043b\u0435\u043d \u0432 \u043d\u043e\u0432\u043e\u043c \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0438 \u2b06\ufe0f",
                reply_markup=support_keyboard()
            )
    else:
        await callback.answer(
            "\u23f3 \u041f\u043b\u0430\u0442\u0435\u0436 \u043f\u043e\u043a\u0430 \u043d\u0435 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d. \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u0447\u0435\u0440\u0435\u0437 \u043c\u0438\u043d\u0443\u0442\u0443.",
            show_alert=True
        )


# ========== ОПЛАТА GOLD / NFT ==========
@dp.callback_query(F.data.startswith("pay_gold_"))
async def process_gold_payment(callback: types.CallbackQuery):
    await _process_manual_payment(callback, "gold")


@dp.callback_query(F.data.startswith("pay_nft_"))
async def process_nft_payment(callback: types.CallbackQuery):
    await _process_manual_payment(callback, "nft")


async def _process_manual_payment(callback: types.CallbackQuery, method: str):
    parts = callback.data.split("_")
    if len(parts) < 4:
        await callback.answer("\u274c \u041e\u0448\u0438\u0431\u043a\u0430", show_alert=True)
        return

    product = find_product(parts[2], parts[3])
    if not product:
        await callback.answer("\u274c \u041f\u0440\u043e\u0434\u0443\u043a\u0442 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d", show_alert=True)
        return

    user_id = callback.from_user.id
    if not rate_limiter.check(user_id):
        await callback.answer("\u23f3 \u041f\u043e\u0434\u043e\u0436\u0434\u0438\u0442\u0435...", show_alert=True)
        return

    method_config = {
        "gold": {
            "name": "GOLD",
            "icon": "\U0001f4b0",
            "price_key": "price_gold",
            "emoji": "\U0001fa99"
        },
        "nft": {
            "name": "NFT",
            "icon": "\U0001f3a8",
            "price_key": "price_nft",
            "emoji": "\U0001f5bc\ufe0f"
        }
    }

    cfg = method_config[method]
    price = product[cfg["price_key"]]

    chat_message = (
        f"\u041f\u0440\u0438\u0432\u0435\u0442! \u0425\u043e\u0447\u0443 \u043a\u0443\u043f\u0438\u0442\u044c \u0447\u0438\u0442 \u043d\u0430 Standoff 2. "
        f"\u0412\u0435\u0440\u0441\u0438\u044f 0.37.1, \u043f\u043e\u0434\u043f\u0438\u0441\u043a\u0430 \u043d\u0430 {product['period_text']} "
        f"({product['platform']}). "
        f"\u0413\u043e\u0442\u043e\u0432 \u043a\u0443\u043f\u0438\u0442\u044c \u0437\u0430 {price} {cfg['name']} \u043f\u0440\u044f\u043c\u043e \u0441\u0435\u0439\u0447\u0430\u0441"
    )
    encoded_message = quote(chat_message, safe='')
    support_url = f"https://t.me/{Config.SUPPORT_CHAT_USERNAME}?text={encoded_message}"

    text = (
        f"{cfg['icon']} <b>\u041e\u043f\u043b\u0430\u0442\u0430 {cfg['name']}</b>\n\n"
        f"{product['emoji']} {product['name']}\n"
        f"\u23f1\ufe0f {product['duration']}\n"
        f"\U0001f4b0 \u0421\u0442\u043e\u0438\u043c\u043e\u0441\u0442\u044c: <b>{price} {cfg['name']}</b>\n\n"
        f"\U0001f4dd <b>\u0412\u0430\u0448\u0435 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435 \u0434\u043b\u044f \u0447\u0430\u0442\u0430:</b>\n"
        f"<code>{chat_message}</code>\n\n"
        f"\U0001f504 <b>\u0418\u043d\u0441\u0442\u0440\u0443\u043a\u0446\u0438\u044f:</b>\n"
        f"1\ufe0f\u20e3 \u041d\u0430\u0436\u043c\u0438\u0442\u0435 \u00ab\u041f\u0435\u0440\u0435\u0439\u0442\u0438 \u043a \u043e\u043f\u043b\u0430\u0442\u0435\u00bb\n"
        f"2\ufe0f\u20e3 \u041e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435 \u0432 \u0447\u0430\u0442\n"
        f"3\ufe0f\u20e3 \u041e\u0436\u0438\u0434\u0430\u0439\u0442\u0435 \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u043a\u0438"
    )

    order_id = generate_order_id()
    await orders.add_pending(order_id, {
        "user_id": user_id,
        "user_name": callback.from_user.full_name,
        "product": product,
        "amount": price,
        "currency": cfg["name"],
        "payment_method": cfg["name"],
        "status": "pending",
        "created_at": time.time()
    })

    await callback.message.edit_text(
        text,
        reply_markup=manual_payment_keyboard(support_url, f"{method}_sent")
    )
    await send_admin_notification(
        callback.from_user, product,
        f"{cfg['icon']} {cfg['name']}",
        f"{price} {cfg['emoji']}",
        order_id
    )
    await callback.answer()


@dp.callback_query(F.data.in_({"gold_sent", "nft_sent"}))
async def manual_payment_sent(callback: types.CallbackQuery):
    method_name = "GOLD" if callback.data == "gold_sent" else "NFT"
    icon = "\U0001f4b0" if callback.data == "gold_sent" else "\U0001f3a8"

    await callback.message.edit_text(
        f"\u2705 <b>\u041e\u0442\u043b\u0438\u0447\u043d\u043e!</b>\n\n"
        f"{icon} \u0412\u0430\u0448 {method_name} \u0437\u0430\u043a\u0430\u0437 \u043f\u0440\u0438\u043d\u044f\u0442 \u0432 \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u043a\u0443\n"
        f"\u23f1\ufe0f \u0412\u0440\u0435\u043c\u044f \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u043a\u0438: \u0434\u043e 30 \u043c\u0438\u043d\u0443\u0442\n"
        f"\U0001f4e8 \u0423\u0432\u0435\u0434\u043e\u043c\u0438\u043c \u043e \u0433\u043e\u0442\u043e\u0432\u043d\u043e\u0441\u0442\u0438 \u0437\u0430\u043a\u0430\u0437\u0430\n\n"
        f"\U0001f4ac \u041f\u043e\u0434\u0434\u0435\u0440\u0436\u043a\u0430: @{Config.SUPPORT_CHAT_USERNAME}",
        reply_markup=support_keyboard()
    )
    await callback.answer()


# ========== АДМИНСКИЕ КОМАНДЫ ==========
@dp.callback_query(F.data.startswith("admin_confirm_"))
async def admin_confirm_payment(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("\u274c \u0414\u043e\u0441\u0442\u0443\u043f \u0437\u0430\u043f\u0440\u0435\u0449\u0435\u043d", show_alert=True)
        return

    order_id = callback.data.removeprefix("admin_confirm_")
    success = await process_successful_payment(order_id, "\U0001f468\u200d\U0001f4bc \u0410\u0434\u043c\u0438\u043d")

    if success:
        await callback.message.edit_text(
            f"\u2705 <b>\u0417\u0430\u043a\u0430\u0437 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d</b>\n\n"
            f"\U0001f194 {order_id}\n"
            f"\U0001f468\u200d\U0001f4bc \u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u043b: {callback.from_user.full_name}\n"
            f"\U0001f4e8 \u041a\u043b\u044e\u0447 \u043e\u0442\u043f\u0440\u0430\u0432\u043b\u0435\u043d \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044e"
        )
        await callback.answer("\u2705 \u0413\u043e\u0442\u043e\u0432\u043e!")
    else:
        await callback.answer(
            "\u274c \u0417\u0430\u043a\u0430\u0437 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d \u0438\u043b\u0438 \u0443\u0436\u0435 \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u0430\u043d",
            show_alert=True
        )


@dp.callback_query(F.data.startswith("admin_reject_"))
async def admin_reject_payment(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("\u274c \u0414\u043e\u0441\u0442\u0443\u043f \u0437\u0430\u043f\u0440\u0435\u0449\u0435\u043d", show_alert=True)
        return

    order_id = callback.data.removeprefix("admin_reject_")
    order = await orders.remove_pending(order_id)

    if order:
        await callback.message.edit_text(
            f"\u274c <b>\u0417\u0430\u043a\u0430\u0437 \u043e\u0442\u043a\u043b\u043e\u043d\u0435\u043d</b>\n\n"
            f"\U0001f194 {order_id}\n"
            f"\U0001f468\u200d\U0001f4bc \u041e\u0442\u043a\u043b\u043e\u043d\u0438\u043b: {callback.from_user.full_name}"
        )
        try:
            await bot.send_message(
                order['user_id'],
                f"\u274c <b>\u0417\u0430\u043a\u0430\u0437 \u043e\u0442\u043a\u043b\u043e\u043d\u0435\u043d</b>\n\n"
                f"\U0001f194 {order_id}\n"
                f"\U0001f4de \u041e\u0431\u0440\u0430\u0442\u0438\u0442\u0435\u0441\u044c \u0432 \u043f\u043e\u0434\u0434\u0435\u0440\u0436\u043a\u0443\n"
                f"\U0001f4ac @{Config.SUPPORT_CHAT_USERNAME}"
            )
        except Exception:
            pass

    await callback.answer("\u274c \u041e\u0442\u043a\u043b\u043e\u043d\u0435\u043d")


@dp.message(Command("orders"))
async def cmd_orders(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    stats = await orders.get_stats()
    text = "\U0001f4ca <b>\u0421\u0422\u0410\u0422\u0418\u0421\u0422\u0418\u041a\u0410 \u0417\u0410\u041a\u0410\u0417\u041e\u0412</b>\n\n"

    recent = await orders.get_recent_pending(5)
    text += f"\u23f3 <b>\u041e\u0436\u0438\u0434\u0430\u044e\u0442 \u043e\u043f\u043b\u0430\u0442\u044b:</b> {stats['pending']}\n"
    for oid, order in recent:
        t = datetime.fromtimestamp(order['created_at']).strftime('%H:%M')
        text += f"\u2022 {t} | {order['user_name']} | {order['product']['name']}\n"

    text += f"\n\u2705 <b>\u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u043e:</b> {stats['confirmed']}\n"

    balance = await YooMoneyService.get_balance()
    if balance is not None:
        text += f"\U0001f4b0 <b>\u0411\u0430\u043b\u0430\u043d\u0441 \u042e\u041c\u043e\u043d\u0435\u0439:</b> {balance} \u20bd\n"
    else:
        text += "\U0001f4b0 <b>\u0411\u0430\u043b\u0430\u043d\u0441 \u042e\u041c\u043e\u043d\u0435\u0439:</b> \u043e\u0448\u0438\u0431\u043a\u0430\n"

    await message.answer(text)


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    await message.answer(
        "\U0001f527 <b>\u0410\u0434\u043c\u0438\u043d-\u043a\u043e\u043c\u0430\u043d\u0434\u044b:</b>\n\n"
        "/orders \u2014 \u0421\u0442\u0430\u0442\u0438\u0441\u0442\u0438\u043a\u0430 \u0437\u0430\u043a\u0430\u0437\u043e\u0432\n"
        "/help \u2014 \u042d\u0442\u0430 \u0441\u043f\u0440\u0430\u0432\u043a\u0430\n\n"
        "\u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u0435/\u043e\u0442\u043a\u043b\u043e\u043d\u0435\u043d\u0438\u0435 \u0437\u0430\u043a\u0430\u0437\u043e\u0432 \u2014 "
        "\u0447\u0435\u0440\u0435\u0437 \u043a\u043d\u043e\u043f\u043a\u0438 \u0432 \u0443\u0432\u0435\u0434\u043e\u043c\u043b\u0435\u043d\u0438\u044f\u0445"
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

    platform_info = {
        "apk": "\U0001f4f1 <b>Android Version</b>",
        "ios": "\U0001f34e <b>iOS Version</b>"
    }

    text = (
        f"{platform_info.get(platform, '\U0001f4f1 <b>Version</b>')}\n\n"
        f"\U0001f4b0 <b>\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0442\u0430\u0440\u0438\u0444:</b>"
    )
    await callback.message.edit_text(
        text, reply_markup=subscription_keyboard(platform)
    )
    await state.set_state(OrderState.choosing_subscription)
    await callback.answer()


# ========== MINIAPP HTML ==========
MINIAPP_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
    <title>AimNoob | Premium Shop</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        :root {
            --primary: #7c3aed; --primary-dark: #5b21b6; --primary-light: #8b5cf6;
            --secondary: #ec489a; --accent: #f59e0b; --dark: #0f0f1a; --darker: #0a0a0f;
            --glass: rgba(15, 15, 26, 0.8); --glass-light: rgba(255, 255, 255, 0.1);
            --success: #10b981; --danger: #ef4444; --warning: #f59e0b;
        }
        body { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; background: linear-gradient(135deg, var(--darker), var(--dark)); min-height: 100vh; color: #fff; overflow-x: hidden; }
        .animated-bg { position: fixed; top: 0; left: 0; width: 100%; height: 100%; z-index: -1; }
        .animated-bg::before { content: ''; position: absolute; width: 200%; height: 200%; background: radial-gradient(circle at 20% 50%, rgba(124,58,237,0.3), transparent 50%), radial-gradient(circle at 80% 80%, rgba(236,72,153,0.3), transparent 50%); animation: bgMove 20s ease-in-out infinite; }
        @keyframes bgMove { 0%,100% { transform: translate(-10%,-10%) rotate(0deg); } 50% { transform: translate(10%,10%) rotate(5deg); } }
        .app { max-width: 500px; margin: 0 auto; padding: 20px; padding-bottom: 90px; position: relative; z-index: 1; }
        .header { text-align: center; padding: 20px 0 30px; animation: fadeInDown 0.6s cubic-bezier(0.68,-0.55,0.265,1.55); }
        .logo { width: 80px; height: 80px; background: linear-gradient(135deg, var(--primary), var(--secondary)); border-radius: 25px; display: flex; align-items: center; justify-content: center; margin: 0 auto 12px; font-size: 42px; box-shadow: 0 10px 30px rgba(124,58,237,0.3); animation: float 3s ease-in-out infinite; }
        @keyframes float { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-8px); } }
        h1 { font-size: 28px; font-weight: 800; background: linear-gradient(135deg, #fff, var(--primary-light)); -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 6px; }
        .subtitle { opacity: 0.7; font-size: 13px; }
        .platform-group { margin-bottom: 30px; }
        .platform-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 15px; padding: 0 8px; }
        .platform-title { font-size: 20px; font-weight: 700; display: flex; align-items: center; gap: 10px; }
        .platform-badge { background: var(--glass-light); padding: 4px 10px; border-radius: 20px; font-size: 12px; }
        .products-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 14px; }
        .product-card { background: var(--glass); backdrop-filter: blur(20px); border-radius: 20px; border: 1px solid var(--glass-light); overflow: hidden; transition: all 0.3s; cursor: pointer; position: relative; }
        .product-card::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px; background: linear-gradient(90deg, var(--primary), var(--secondary), var(--accent)); transform: scaleX(0); transition: transform 0.3s; }
        .product-card:hover { transform: translateY(-4px); border-color: var(--primary); box-shadow: 0 10px 25px rgba(124,58,237,0.2); }
        .product-card:hover::before { transform: scaleX(1); }
        .card-content { padding: 16px; }
        .popular-badge { position: absolute; top: 10px; right: 10px; background: linear-gradient(135deg, var(--accent), #ff6b6b); padding: 4px 8px; border-radius: 12px; font-size: 10px; font-weight: 700; z-index: 2; }
        .card-header { text-align: center; margin-bottom: 12px; }
        .product-icon { width: 50px; height: 50px; background: linear-gradient(135deg, rgba(124,58,237,0.2), rgba(236,72,153,0.2)); border-radius: 16px; display: flex; align-items: center; justify-content: center; font-size: 28px; margin: 0 auto 10px; }
        .product-name { font-size: 16px; font-weight: 700; margin-bottom: 2px; }
        .product-platform { font-size: 10px; opacity: 0.6; }
        .price-section { text-align: center; margin: 12px 0; }
        .price-current { font-size: 22px; font-weight: 800; color: var(--accent); }
        .price-old { font-size: 12px; opacity: 0.5; text-decoration: line-through; margin-left: 6px; }
        .price-save { font-size: 10px; background: rgba(16,185,129,0.2); color: var(--success); padding: 2px 6px; border-radius: 12px; display: inline-block; margin-top: 4px; }
        .duration-badge { display: inline-flex; align-items: center; gap: 4px; background: var(--glass-light); padding: 4px 8px; border-radius: 16px; font-size: 10px; margin-bottom: 12px; }
        .features-list { display: flex; flex-wrap: wrap; gap: 6px; margin: 10px 0; justify-content: center; }
        .feature { font-size: 9px; background: rgba(255,255,255,0.05); padding: 3px 8px; border-radius: 10px; display: flex; align-items: center; gap: 3px; }
        .buy-btn { width: 100%; margin-top: 12px; padding: 10px; background: linear-gradient(135deg, var(--primary), var(--secondary)); border: none; border-radius: 12px; color: white; font-weight: 700; font-size: 14px; cursor: pointer; transition: all 0.3s; position: relative; overflow: hidden; }
        .buy-btn:active { transform: scale(0.98); }
        .modal { display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.95); backdrop-filter: blur(20px); z-index: 1000; align-items: center; justify-content: center; padding: 20px; }
        .modal.active { display: flex; }
        .modal-content { background: linear-gradient(135deg, var(--dark), var(--darker)); border-radius: 28px; padding: 20px; max-width: 400px; width: 100%; max-height: 85vh; overflow-y: auto; border: 1px solid var(--glass-light); animation: slideUp 0.4s cubic-bezier(0.68,-0.55,0.265,1.55); }
        @keyframes slideUp { from { opacity: 0; transform: translateY(30px); } to { opacity: 1; transform: translateY(0); } }
        .modal-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; padding-bottom: 12px; border-bottom: 1px solid var(--glass-light); }
        .modal-title { font-size: 20px; font-weight: 700; background: linear-gradient(135deg, #fff, var(--primary-light)); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .close-modal { background: var(--glass-light); border: none; width: 32px; height: 32px; border-radius: 50%; font-size: 18px; color: #fff; cursor: pointer; transition: all 0.2s; }
        .close-modal:hover { background: var(--danger); transform: rotate(90deg); }
        .payment-methods { display: flex; flex-direction: column; gap: 10px; margin: 15px 0; }
        .payment-method-card { background: var(--glass-light); border-radius: 16px; padding: 12px; display: flex; align-items: center; justify-content: space-between; cursor: pointer; transition: all 0.3s; border: 1px solid transparent; }
        .payment-method-card:hover { border-color: var(--primary); transform: translateX(4px); background: rgba(124,58,237,0.1); }
        .payment-method-left { display: flex; align-items: center; gap: 12px; }
        .payment-icon { width: 44px; height: 44px; background: rgba(255,255,255,0.1); border-radius: 14px; display: flex; align-items: center; justify-content: center; font-size: 22px; }
        .payment-info h4 { font-size: 14px; margin-bottom: 2px; }
        .payment-info p { font-size: 10px; opacity: 0.6; }
        .payment-amount { font-size: 16px; font-weight: 700; color: var(--accent); }
        .pay-btn { width: 100%; padding: 14px; background: linear-gradient(135deg, var(--primary), var(--secondary)); border: none; border-radius: 14px; color: white; font-weight: 700; font-size: 16px; cursor: pointer; margin-top: 10px; transition: all 0.3s; }
        .pay-btn:active { transform: scale(0.98); }
        .payment-status { text-align: center; padding: 20px; }
        .status-icon { font-size: 56px; margin-bottom: 12px; }
        .status-loading { animation: spin 1s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
        .bottom-nav { position: fixed; bottom: 0; left: 0; right: 0; background: var(--glass); backdrop-filter: blur(20px); display: flex; justify-content: space-around; padding: 10px 20px; border-top: 1px solid var(--glass-light); z-index: 100; }
        .nav-item { display: flex; flex-direction: column; align-items: center; gap: 4px; background: none; border: none; color: rgba(255,255,255,0.5); font-size: 11px; cursor: pointer; transition: all 0.3s; padding: 6px 12px; border-radius: 30px; }
        .nav-item.active { color: var(--accent); background: rgba(245,158,11,0.1); }
        .nav-icon { font-size: 22px; }
        .toast { position: fixed; bottom: 90px; left: 20px; right: 20px; background: rgba(0,0,0,0.95); backdrop-filter: blur(10px); padding: 12px 16px; border-radius: 14px; display: flex; align-items: center; gap: 10px; z-index: 1100; animation: slideUp 0.3s; border-left: 3px solid var(--success); }
        .toast.error { border-left-color: var(--danger); }
        @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
        @keyframes fadeInDown { from { opacity: 0; transform: translateY(-20px); } to { opacity: 1; transform: translateY(0); } }
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-track { background: rgba(255,255,255,0.05); }
        ::-webkit-scrollbar-thumb { background: var(--primary); border-radius: 10px; }
        .license-key { background: rgba(0,0,0,0.5); padding: 10px; border-radius: 10px; font-family: monospace; font-size: 11px; text-align: center; word-break: break-all; margin: 10px 0; }
    </style>
</head>
<body>
    <div class="animated-bg"></div>
    <div class="app">
        <div class="header">
            <div class="logo">&#127919;</div>
            <h1>AimNoob</h1>
            <div class="subtitle">Премиум чит для Standoff 2</div>
        </div>
        <div id="content"></div>
    </div>
    <div class="bottom-nav">
        <button class="nav-item active" data-page="shop"><span class="nav-icon">&#128722;</span><span>Магазин</span></button>
        <button class="nav-item" data-page="orders"><span class="nav-icon">&#128273;</span><span>Ключи</span></button>
        <button class="nav-item" data-page="profile"><span class="nav-icon">&#128100;</span><span>Профиль</span></button>
    </div>
    <div id="modal" class="modal">
        <div class="modal-content">
            <div class="modal-header">
                <div class="modal-title" id="modal-title">Оформление заказа</div>
                <button class="close-modal">&times;</button>
            </div>
            <div id="modal-body"></div>
        </div>
    </div>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <script>
        const tg = window.Telegram.WebApp;
        tg.expand();
        tg.enableClosingConfirmation();
        tg.MainButton.hide();
        const API_BASE = window.location.origin + '/api';
        const PRODUCTS = {
            android: [
                { id:"apk_week", name:"Android", period:"Неделя", duration:"7 дней", price:150, price_stars:350, price_gold:350, price_nft:250, price_crypto_usdt:2, icon:"📱", features:["AimBot","WallHack","ESP"], popular:false, discount:0 },
                { id:"apk_month", name:"Android", period:"Месяц", duration:"30 дней", price:350, price_stars:800, price_gold:800, price_nft:600, price_crypto_usdt:5, icon:"📱", features:["AimBot","WallHack","ESP","Anti-Ban"], popular:true, discount:15 },
                { id:"apk_forever", name:"Android", period:"Навсегда", duration:"∞", price:800, price_stars:1800, price_gold:1800, price_nft:1400, price_crypto_usdt:12, icon:"📱", features:["AimBot","WallHack","ESP","Anti-Ban","Обновления"], popular:false, discount:30 }
            ],
            ios: [
                { id:"ios_week", name:"iOS", period:"Неделя", duration:"7 дней", price:300, price_stars:700, price_gold:700, price_nft:550, price_crypto_usdt:4, icon:"🍎", features:["AimBot","WallHack","ESP"], popular:false, discount:0 },
                { id:"ios_month", name:"iOS", period:"Месяц", duration:"30 дней", price:450, price_stars:1000, price_gold:1000, price_nft:800, price_crypto_usdt:6, icon:"🍎", features:["AimBot","WallHack","ESP","Anti-Ban"], popular:true, discount:10 },
                { id:"ios_forever", name:"iOS", period:"Навсегда", duration:"∞", price:850, price_stars:2000, price_gold:2000, price_nft:1600, price_crypto_usdt:12, icon:"🍎", features:["AimBot","WallHack","ESP","Anti-Ban","Обновления"], popular:false, discount:25 }
            ]
        };
        let currentUser = null, selectedProduct = null, userLicenses = [];
        document.addEventListener('DOMContentLoaded', () => {
            currentUser = tg.initDataUnsafe?.user || { id: Date.now(), first_name: 'Гость', username: 'user' };
            loadUserLicenses();
            renderShop();
            document.querySelectorAll('.nav-item').forEach(btn => btn.addEventListener('click', () => switchPage(btn.dataset.page)));
            document.querySelector('.close-modal').addEventListener('click', closeModal);
            document.getElementById('modal').addEventListener('click', e => { if (e.target === document.getElementById('modal')) closeModal(); });
        });
        function switchPage(page) {
            document.querySelectorAll('.nav-item').forEach(btn => btn.classList.toggle('active', btn.dataset.page === page));
            if (page === 'shop') renderShop();
            else if (page === 'orders') renderOrders();
            else if (page === 'profile') renderProfile();
        }
        function renderShop() {
            const content = document.getElementById('content');
            content.innerHTML = ['android','ios'].map(platform => {
                const icon = platform === 'android' ? '📱' : '🍎';
                const name = platform === 'android' ? 'Android' : 'iOS';
                return '<div class="platform-group"><div class="platform-header"><div class="platform-title"><span>'+icon+'</span><span>'+name+'</span></div><div class="platform-badge">3 тарифа</div></div><div class="products-grid">'+PRODUCTS[platform].map(p => renderProductCard(p)).join('')+'</div></div>';
            }).join('');
            document.querySelectorAll('.product-card').forEach(card => {
                card.querySelector('.buy-btn').addEventListener('click', e => { e.stopPropagation(); const p = [...PRODUCTS.android,...PRODUCTS.ios].find(x => x.id === card.dataset.productId); if(p) showPaymentModal(p); });
                card.addEventListener('click', e => { if(!e.target.classList.contains('buy-btn')){ const p = [...PRODUCTS.android,...PRODUCTS.ios].find(x => x.id === card.dataset.productId); if(p) showProductDetail(p); }});
            });
        }
        function renderProductCard(p) {
            const oldPrice = p.discount ? Math.round(p.price*(1+p.discount/100)) : null;
            const days = parseInt(p.duration); const ppd = (!isNaN(days)&&days>0) ? (p.price/days).toFixed(0) : null;
            return '<div class="product-card" data-product-id="'+p.id+'">'+(p.popular?'<div class="popular-badge">🔥 ХИТ</div>':'')+'<div class="card-content"><div class="card-header"><div class="product-icon">'+p.icon+'</div><div class="product-name">'+p.name+'</div><div class="product-platform">'+p.period+'</div></div><div class="price-section"><span class="price-current">'+p.price+' ₽</span>'+(oldPrice?'<span class="price-old">'+oldPrice+' ₽</span>':'')+(p.discount?'<div class="price-save">-'+p.discount+'%</div>':'')+'</div>'+(ppd?'<div class="duration-badge">📅 '+ppd+' ₽/день</div>':'')+'<div class="features-list">'+p.features.map(f=>'<span class="feature">✨ '+f+'</span>').join('')+'</div><button class="buy-btn">'+(p.popular?'🔥 Купить':'🛒 Купить')+'</button></div></div>';
        }
        function showProductDetail(p) {
            document.getElementById('modal-title').textContent = p.name+' • '+p.period;
            const oldPrice = p.discount ? Math.round(p.price*(1+p.discount/100)) : null;
            document.getElementById('modal-body').innerHTML = '<div style="text-align:center;margin-bottom:20px"><div style="font-size:56px;margin-bottom:8px">'+p.icon+'</div><div style="font-size:20px;font-weight:700">'+p.name+'</div><div style="font-size:14px;opacity:0.7">'+p.period+' • '+p.duration+'</div></div><div class="price-section"><span class="price-current" style="font-size:32px">'+p.price+' ₽</span>'+(oldPrice?'<span class="price-old">'+oldPrice+' ₽</span>':'')+'</div><div class="features-list" style="justify-content:center;margin-bottom:20px">'+p.features.map(f=>'<span class="feature">✨ '+f+'</span>').join('')+'</div><button class="pay-btn" onclick="closeModal();setTimeout(()=>{const pr=[...PRODUCTS.android,...PRODUCTS.ios].find(x=>x.id===\''+p.id+'\');if(pr)showPaymentModal(pr)},100)">💳 Перейти к оплате</button>';
            openModal();
        }
        function showPaymentModal(p) {
            selectedProduct = p;
            document.getElementById('modal-title').textContent = 'Способ оплаты';
            document.getElementById('modal-body').innerHTML = '<div style="text-align:center;margin-bottom:16px"><div style="font-size:40px">'+p.icon+'</div><div style="font-size:16px;font-weight:600">'+p.name+' • '+p.period+'</div><div style="font-size:20px;font-weight:700;color:var(--accent);margin-top:5px">'+p.price+' ₽</div></div><div class="payment-methods">'+[
                {m:'yoomoney',i:'💳',t:'Картой',d:'Карты, СБП, Apple Pay',a:p.price+' ₽'},
                {m:'stars',i:'⭐',t:'Telegram Stars',d:'Встроенные платежи',a:p.price_stars+' ⭐'},
                {m:'crypto',i:'₿',t:'Криптовалюта',d:'USDT, BTC, ETH, TON',a:p.price_crypto_usdt+' USDT'},
                {m:'gold',i:'💰',t:'GOLD',d:'Игровая валюта',a:p.price_gold+' 🪙'},
                {m:'nft',i:'🎨',t:'NFT',d:'Коллекционные токены',a:p.price_nft+' 🖼'}
            ].map(x=>'<div class="payment-method-card" data-method="'+x.m+'"><div class="payment-method-left"><div class="payment-icon">'+x.i+'</div><div class="payment-info"><h4>'+x.t+'</h4><p>'+x.d+'</p></div></div><div class="payment-amount">'+x.a+'</div></div>').join('')+'</div>';
            document.querySelectorAll('.payment-method-card').forEach(c => c.addEventListener('click', () => processPayment(c.dataset.method)));
            openModal();
        }
        async function processPayment(method) {
            document.getElementById('modal-body').innerHTML = '<div class="payment-status"><div class="status-icon status-loading">⏳</div><h3>Создание платежа...</h3></div>';
            try {
                const r = await fetch(API_BASE+'/create_payment',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({product_id:selectedProduct.id,method,user_id:currentUser.id,user_name:currentUser.first_name+' '+(currentUser.last_name||''),init_data:tg.initData})});
                const res = await r.json();
                if(!res.success) throw new Error(res.error||'Ошибка');
                if(method==='yoomoney') showUrlPayment(res.payment_url,res.order_id,'💳',selectedProduct.price+' ₽','checkPayment');
                else if(method==='stars'){ document.getElementById('modal-body').innerHTML='<div class="payment-status"><div class="status-icon">⭐</div><h3>'+selectedProduct.price_stars+' Stars</h3><p style="opacity:0.7;margin:8px 0">Перейдите в бота для оплаты</p><button class="pay-btn" onclick="tg.openTelegramLink(\'https://t.me/aimnoob_bot?start=buy_stars_'+selectedProduct.id+'\')">⭐ Оплатить в боте</button></div>'; }
                else if(method==='crypto') showUrlPayment(res.payment_url,res.order_id,'₿',selectedProduct.price_crypto_usdt+' USDT','checkCrypto',res.invoice_id);
                else showManualPayment(method,res.order_id);
            } catch(e){ showToast(e.message,'error'); setTimeout(()=>showPaymentModal(selectedProduct),1500); }
        }
        function showUrlPayment(url,orderId,icon,amount,checkFn,invoiceId) {
            const extra = invoiceId ? ",'"+invoiceId+"'" : '';
            document.getElementById('modal-body').innerHTML='<div class="payment-status"><div class="status-icon">'+icon+'</div><h3>'+amount+'</h3><p style="opacity:0.7;margin:8px 0">Заказ #'+orderId.slice(-8)+'</p><button class="pay-btn" onclick="window.open(\''+url+'\',\'_blank\')">🔗 Оплатить</button><button class="pay-btn" style="background:var(--glass-light);margin-top:8px" onclick="'+checkFn+'(\''+orderId+'\''+extra+')">✅ Проверить оплату</button></div>';
        }
        function showManualPayment(method,orderId) {
            const names={gold:'GOLD',nft:'NFT'}, amounts={gold:selectedProduct.price_gold,nft:selectedProduct.price_nft}, icons={gold:'💰',nft:'🎨'};
            const msg='Привет! Хочу купить чит на Standoff 2. Подписка на '+selectedProduct.period+' ('+selectedProduct.name+'). Готов купить за '+amounts[method]+' '+names[method];
            document.getElementById('modal-body').innerHTML='<div class="payment-status"><div class="status-icon">'+icons[method]+'</div><h3>'+amounts[method]+' '+names[method]+'</h3><div style="background:var(--glass-light);padding:10px;border-radius:12px;margin:10px 0;font-size:11px">'+msg+'</div><button class="pay-btn" onclick="window.open(\'https://t.me/aimnoob_support?text='+encodeURIComponent(msg)+'\',\'_blank\')">💬 Написать в поддержку</button><button class="pay-btn" style="background:var(--glass-light);margin-top:8px" onclick="closeModal();showToast(\'Заказ создан!\')">✅ Я написал</button></div>';
        }
        async function checkPayment(orderId) {
            document.getElementById('modal-body').innerHTML='<div class="payment-status"><div class="status-icon status-loading">⏳</div><h3>Проверка...</h3></div>';
            try {
                const r=await fetch(API_BASE+'/check_payment',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({order_id:orderId})});
                const res=await r.json();
                if(res.paid){ showSuccess(res.license_key); } else { document.getElementById('modal-body').innerHTML='<div class="payment-status"><div class="status-icon">⏳</div><h3>Платеж не найден</h3><p style="opacity:0.7;margin:8px 0">Попробуйте через 1-2 минуты</p><button class="pay-btn" onclick="checkPayment(\''+orderId+'\')">🔄 Проверить снова</button></div>'; }
            } catch(e){ showToast('Ошибка проверки','error'); }
        }
        async function checkCrypto(orderId,invoiceId) {
            document.getElementById('modal-body').innerHTML='<div class="payment-status"><div class="status-icon status-loading">⏳</div><h3>Проверка...</h3></div>';
            try {
                const r=await fetch(API_BASE+'/check_crypto',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({invoice_id:invoiceId,order_id:orderId})});
                const res=await r.json();
                if(res.paid){ showSuccess(res.license_key); } else { document.getElementById('modal-body').innerHTML='<div class="payment-status"><div class="status-icon">⏳</div><h3>Платеж в обработке</h3><button class="pay-btn" onclick="checkCrypto(\''+orderId+'\',\''+invoiceId+'\')">🔄 Проверить снова</button></div>'; }
            } catch(e){ showToast('Ошибка','error'); }
        }
        function showSuccess(key) {
            userLicenses.push({key,product:selectedProduct.name+' • '+selectedProduct.period,date:new Date().toISOString()});
            saveUserLicenses();
            document.getElementById('modal-body').innerHTML='<div class="payment-status"><div class="status-icon">✅</div><h3>Оплата подтверждена!</h3><div class="license-key">🔑 '+key+'</div><button class="pay-btn" onclick="window.open(\'""" + Config.DOWNLOAD_URL + """\',\'_blank\')">📥 Скачать AimNoob</button><button class="pay-btn" style="background:var(--glass-light);margin-top:8px" onclick="closeModal();switchPage(\'orders\')">📋 Перейти к ключам</button></div>';
        }
        function renderOrders() {
            const content=document.getElementById('content');
            if(!userLicenses.length){ content.innerHTML='<div style="text-align:center;padding:50px 20px"><div style="font-size:56px;margin-bottom:16px">🔑</div><div style="font-size:18px;font-weight:600;margin-bottom:8px">Нет активных ключей</div><div style="opacity:0.7;margin-bottom:20px">Приобретите подписку</div><button class="pay-btn" onclick="switchPage(\'shop\')">🛒 В магазин</button></div>'; return; }
            content.innerHTML='<div class="platform-group"><div class="platform-header"><div class="platform-title"><span>🔑</span><span>Мои лицензии</span></div><div class="platform-badge">'+userLicenses.length+' шт</div></div><div class="products-grid">'+userLicenses.map(l=>'<div class="product-card"><div class="card-content"><div class="card-header"><div class="product-icon">🎯</div><div class="product-name">'+l.product+'</div><div class="product-platform">'+new Date(l.date).toLocaleDateString('ru-RU')+'</div></div><div class="license-key">'+l.key+'</div><button class="buy-btn" onclick="copyToClipboard(\''+l.key+'\')">📋 Скопировать</button></div></div>').join('')+'</div></div>';
        }
        function renderProfile() {
            const emojis=['🎯','🔥','⚡','💎','🌟','🎮','🚀','💪'];
            const avatar=emojis[Math.abs(currentUser.id)%emojis.length];
            document.getElementById('content').innerHTML='<div class="platform-group"><div class="platform-header"><div class="platform-title"><span>👤</span><span>Профиль</span></div></div><div class="product-card"><div class="card-content" style="text-align:center"><div class="product-icon" style="margin:0 auto 12px">'+avatar+'</div><div class="product-name">'+currentUser.first_name+' '+(currentUser.last_name||'')+'</div><div class="product-platform">@'+(currentUser.username||'user')+'</div><div style="margin:15px 0;padding:12px;background:var(--glass-light);border-radius:14px"><div style="display:flex;justify-content:space-between;margin-bottom:8px"><span>Активных ключей:</span><span style="font-weight:700">'+userLicenses.length+'</span></div></div><button class="pay-btn" onclick="window.open(\'https://t.me/aimnoob_support\',\'_blank\')">💬 Поддержка</button></div></div></div>';
        }
        function copyToClipboard(t){ navigator.clipboard.writeText(t); showToast('Ключ скопирован!'); }
        function loadUserLicenses(){ const s=localStorage.getItem('aimnoob_licenses'); if(s) userLicenses=JSON.parse(s); }
        function saveUserLicenses(){ localStorage.setItem('aimnoob_licenses',JSON.stringify(userLicenses)); }
        function showToast(msg,type){ type=type||'success'; const t=document.createElement('div'); t.className='toast '+type; t.innerHTML='<span>'+(type==='success'?'✅':'❌')+'</span><span>'+msg+'</span>'; document.body.appendChild(t); setTimeout(()=>t.remove(),3000); }
        function openModal(){ document.getElementById('modal').classList.add('active'); }
        function closeModal(){ document.getElementById('modal').classList.remove('active'); }
        window.checkPayment=checkPayment; window.checkCrypto=checkCrypto; window.copyToClipboard=copyToClipboard;
        window.switchPage=switchPage; window.closeModal=closeModal; window.showToast=showToast; window.showPaymentModal=showPaymentModal;
        tg.ready();
    </script>
</body>
</html>"""


# ========== WEB SERVER API ==========
class WebHandlers:
    @staticmethod
    async def handle_miniapp(request: web.Request) -> web.Response:
        return web.Response(text=MINIAPP_HTML, content_type='text/html', charset='utf-8')

    @staticmethod
    async def handle_health(request: web.Request) -> web.Response:
        stats = await orders.get_stats()
        return web.json_response({"status": "ok", **stats, "uptime": time.time()})

    @staticmethod
    async def handle_create_payment(request: web.Request) -> web.Response:
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
            payment_url = create_payment_link(amount, order_id, f"{product['name']} ({product['duration']})")
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
            invoice_data = await CryptoBotService.create_invoice(amount_usdt, order_id, f"AimNoob {product['name']}")
            if not invoice_data:
                return web.json_response({"success": False, "error": "Invoice creation failed"})
            await orders.add_pending(order_id, {
                "user_id": user_id, "user_name": user_name, "product": product,
                "amount": amount_usdt, "currency": "USDT", "payment_method": "CryptoBot",
                "status": "pending", "invoice_id": invoice_data["invoice_id"], "created_at": time.time()
            })
            return web.json_response({"success": True, "payment_url": invoice_data["pay_url"], "invoice_id": invoice_data["invoice_id"], "order_id": order_id})

        elif method == 'stars':
            await orders.add_pending(order_id, {
                "user_id": user_id, "user_name": user_name, "product": product,
                "amount": product['price_stars'], "currency": "\u2b50", "payment_method": "Telegram Stars",
                "status": "pending", "created_at": time.time()
            })
            return web.json_response({"success": True, "order_id": order_id, "method": "stars"})

        else:
            price_key = f'price_{method}'
            await orders.add_pending(order_id, {
                "user_id": user_id, "user_name": user_name, "product": product,
                "amount": product.get(price_key, 0), "currency": method.upper(),
                "payment_method": method.upper(), "status": "pending", "created_at": time.time()
            })
            return web.json_response({"success": True, "order_id": order_id, "method": method})

    @staticmethod
    async def handle_check_payment(request: web.Request) -> web.Response:
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
            await process_successful_payment(order_id, "MiniApp \u0410\u0432\u0442\u043e\u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0430")
            cp = await orders.get_confirmed(order_id)
            return web.json_response({"paid": True, "license_key": cp.get('license_key', '') if cp else ''})

        return web.json_response({"paid": False})

    @staticmethod
    async def handle_check_crypto(request: web.Request) -> web.Response:
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
            return web.json_response({"paid": True, "license_key": cp.get('license_key', '') if cp else ''})

        return web.json_response({"paid": False})


# ========== CORS MIDDLEWARE ==========
@web.middleware
async def cors_middleware(request: web.Request, handler):
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
    logger.info("\U0001f680 AIMNOOB PREMIUM SHOP BOT")
    logger.info("=" * 50)
    logger.info(f"ADMIN_IDS: {Config.ADMIN_IDS}")
    logger.info(f"MINIAPP_URL: {Config.MINIAPP_URL}")
    logger.info(f"DOWNLOAD_URL: {Config.DOWNLOAD_URL}")
    logger.info(f"WEB_PORT: {Config.WEB_PORT}")

    runner = None

    try:
        me = await bot.get_me()
        logger.info(f"\U0001f916 Bot: @{me.username}")

        balance = await YooMoneyService.get_balance()
        if balance is not None:
            logger.info(f"\u2705 YooMoney connected (balance: {balance} \u20bd)")
        else:
            logger.warning("\u26a0\ufe0f YooMoney connection issues")

        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="\U0001f3ae \u041c\u0430\u0433\u0430\u0437\u0438\u043d",
                    web_app=WebAppInfo(url=Config.MINIAPP_URL)
                )
            )
            logger.info("\u2705 Menu button set")
        except Exception as e:
            logger.warning(f"Could not set menu button: {e}")

        for key, product in PRODUCTS.items():
            logger.info(f"\U0001f4e6 {product['emoji']} {product['name']} ({product['duration']}) \u2014 {product['price']}\u20bd")

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
        logger.info(f"\U0001f310 Web server started on port {Config.WEB_PORT}")

        logger.info("\u2728 Bot starting polling...")
        await dp.start_polling(bot)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.error(f"\u274c Fatal error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if runner:
            await runner.cleanup()
        await bot.session.close()
        logger.info("Bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
