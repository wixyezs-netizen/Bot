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
    """Централизованная конфигурация из переменных окружения"""

    BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "")
    CRYPTOBOT_TOKEN: str = os.environ.get("CRYPTOBOT_TOKEN", "")
    YOOMONEY_ACCESS_TOKEN: str = os.environ.get("YOOMONEY_ACCESS_TOKEN", "")
    YOOMONEY_WALLET: str = os.environ.get("YOOMONEY_WALLET", "")

    SUPPORT_CHAT_USERNAME: str = os.environ.get("SUPPORT_CHAT_USERNAME", "aimnoob_support")
    SHOP_URL: str = os.environ.get("SHOP_URL", "https://aimnoob.ru")
    MINIAPP_URL: str = os.environ.get("MINIAPP_URL", "https://aimnoob.bothost.ru")
    WEB_PORT: int = int(os.environ.get("PORT", "8080"))

    ADMIN_IDS: set
    ADMIN_ID: int
    SUPPORT_CHAT_ID: int

    # Лимиты
    MAX_PENDING_ORDERS: int = 1000
    ORDER_EXPIRY_SECONDS: int = 3600  # 1 час
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

        # Предупреждения о недостающих токенах
        if not cls.CRYPTOBOT_TOKEN:
            logger.warning("CRYPTOBOT_TOKEN not set — crypto payments disabled")
        if not cls.YOOMONEY_ACCESS_TOKEN:
            logger.warning("YOOMONEY_ACCESS_TOKEN not set — card payments disabled")
        if not cls.YOOMONEY_WALLET:
            logger.warning("YOOMONEY_WALLET not set — card payments disabled")


# ========== ХРАНИЛИЩЕ ДАННЫХ ==========
class OrderStorage:
    """
    Потокобезопасное хранилище заказов с автоочисткой.
    В продакшене заменить на Redis/PostgreSQL.
    """

    def __init__(self, max_pending: int = 1000, expiry_seconds: int = 3600):
        self._pending: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._confirmed: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._max_pending = max_pending
        self._expiry_seconds = expiry_seconds

    async def add_pending(self, order_id: str, order_data: Dict[str, Any]):
        async with self._lock:
            # Автоочистка старых заказов
            await self._cleanup_expired()

            if len(self._pending) >= self._max_pending:
                # Удаляем самый старый
                self._pending.popitem(last=False)

            self._pending[order_id] = order_data

    async def get_pending(self, order_id: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            return self._pending.get(order_id)

    async def confirm(self, order_id: str, extra_data: Dict[str, Any]) -> bool:
        """Атомарно переносит заказ из pending в confirmed. Возвращает False если уже подтверждён."""
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
        """Удаление просроченных заказов (вызывать под lock)"""
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
    """Простой rate limiter по user_id"""

    def __init__(self, interval: float = 2.0):
        self._last_action: Dict[int, float] = {}
        self._interval = interval

    def check(self, user_id: int) -> bool:
        """Возвращает True если действие разрешено"""
        now = time.time()
        last = self._last_action.get(user_id, 0)
        if now - last < self._interval:
            return False
        self._last_action[user_id] = now

        # Очистка старых записей
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
        "price_stars": 350,
        "price_gold": 350,
        "price_nft": 250,
        "price_crypto_usdt": 2,
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
        "price_stars": 800,
        "price_gold": 800,
        "price_nft": 600,
        "price_crypto_usdt": 5,
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
        "price_stars": 1800,
        "price_gold": 1800,
        "price_nft": 1400,
        "price_crypto_usdt": 12,
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
        "price_stars": 700,
        "price_gold": 700,
        "price_nft": 550,
        "price_crypto_usdt": 4,
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
        "price_stars": 1000,
        "price_gold": 1000,
        "price_nft": 800,
        "price_crypto_usdt": 6,
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
        "price_stars": 2000,
        "price_gold": 2000,
        "price_nft": 1600,
        "price_crypto_usdt": 12,
        "platform": "iOS",
        "period": "НАВСЕГДА",
        "platform_code": "ios",
        "emoji": "🍎",
        "duration": "Навсегда"
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
    """Валидация initData из Telegram WebApp"""
    if not init_data:
        return None

    try:
        parsed = parse_qs(init_data)
        received_hash = parsed.get('hash', [None])[0]
        if not received_hash:
            return None

        # Собираем data-check-string
        data_pairs = []
        for key, values in parsed.items():
            if key != 'hash':
                data_pairs.append(f"{key}={values[0]}")
        data_pairs.sort()
        data_check_string = '\n'.join(data_pairs)

        # Вычисляем secret key
        secret_key = hmac.new(
            b"WebAppData", bot_token.encode(), hashlib.sha256
        ).digest()

        # Вычисляем hash
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
    """Создание ссылки на оплату YooMoney"""
    comment = f"Заказ {order_id}: {product_name}"
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
    """Работа с YooMoney API"""

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

                    # 1. Поиск по label (точное совпадение)
                    for op in operations:
                        if (op.get("label") == order_id
                                and op.get("status") == "success"
                                and abs(float(op.get("amount", 0)) - expected_amount) <= 5):
                            logger.info(f"Found payment by label: {op}")
                            return True

                    # 2. Поиск по сумме + времени (fallback)
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
    """Работа с CryptoBot API"""

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
            "description": description[:256],  # Лимит длины
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
        [InlineKeyboardButton(text="📱 Android", callback_data="platform_apk")],
        [InlineKeyboardButton(text="🍎 iOS", callback_data="platform_ios")],
        [InlineKeyboardButton(
            text="🎮 Открыть магазин",
            web_app=WebAppInfo(url=Config.MINIAPP_URL)
        )],
        [InlineKeyboardButton(text="ℹ️ О программе", callback_data="about")],
        [InlineKeyboardButton(
            text="💬 Поддержка",
            url=f"https://t.me/{Config.SUPPORT_CHAT_USERNAME}"
        )]
    ])


def subscription_keyboard(platform: str) -> InlineKeyboardMarkup:
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


def payment_methods_keyboard(product: Dict) -> InlineKeyboardMarkup:
    pc = product['platform_code']
    p = product['period']
    buttons = [
        [InlineKeyboardButton(text="💳 Картой", callback_data=f"pay_yoomoney_{pc}_{p}")],
        [InlineKeyboardButton(text="⭐ Telegram Stars", callback_data=f"pay_stars_{pc}_{p}")],
        [InlineKeyboardButton(text="₿ Криптобот", callback_data=f"pay_crypto_{pc}_{p}")],
        [InlineKeyboardButton(text="💰 GOLD", callback_data=f"pay_gold_{pc}_{p}")],
        [InlineKeyboardButton(text="🎨 NFT", callback_data=f"pay_nft_{pc}_{p}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_subscription")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def payment_keyboard(payment_url: str, order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить картой", url=payment_url)],
        [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"checkym_{order_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="restart")]
    ])


def crypto_payment_keyboard(invoice_url: str, order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="₿ Оплатить криптой", url=invoice_url)],
        [InlineKeyboardButton(text="✅ Проверить платеж", callback_data=f"checkcr_{order_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="restart")]
    ])


def support_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="💬 Поддержка",
            url=f"https://t.me/{Config.SUPPORT_CHAT_USERNAME}"
        )],
        [InlineKeyboardButton(text="🌐 Сайт", url=Config.SHOP_URL)],
        [InlineKeyboardButton(text="🔄 Новая покупка", callback_data="restart")]
    ])


def about_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_platform")]
    ])


def admin_confirm_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"admin_confirm_{order_id}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"admin_reject_{order_id}")]
    ])


def manual_payment_keyboard(support_url: str, sent_callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Перейти к оплате", url=support_url)],
        [InlineKeyboardButton(text="✅ Я написал", callback_data=sent_callback)],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="restart")]
    ])


# ========== БИЗНЕС-ЛОГИКА ==========
async def process_successful_payment(order_id: str, source: str = "API") -> bool:
    """Обработка успешного платежа — атомарная операция"""

    # Получаем данные до confirm
    order = await orders.get_pending(order_id)
    if not order:
        if await orders.is_confirmed(order_id):
            logger.info(f"Order {order_id} already confirmed")
        return False

    product = order["product"]
    user_id = order["user_id"]
    license_key = generate_license_key(order_id, user_id)

    # Атомарное подтверждение (защита от дублей)
    confirmed = await orders.confirm(order_id, {
        'confirmed_at': time.time(),
        'confirmed_by': source,
        'license_key': license_key
    })

    if not confirmed:
        logger.warning(f"Order {order_id} confirm race condition, skipping")
        return False

    # Отправка пользователю
    success_text = (
        f"🎉 <b>Оплата подтверждена!</b>\n\n"
        f"✨ Добро пожаловать в AimNoob!\n\n"
        f"📦 <b>Ваша покупка:</b>\n"
        f"{product['emoji']} {product['name']}\n"
        f"⏱️ Срок: {product['duration']}\n"
        f"🔍 Метод: {source}\n\n"
        f"🔑 <b>Ваш лицензионный ключ:</b>\n"
        f"<code>{license_key}</code>\n\n"
        f"📥 <b>Скачивание:</b>\n"
        f"🔗 {Config.SHOP_URL}/download/{product['platform_code']}_{user_id}\n\n"
        f"💫 <b>Активация:</b>\n"
        f"1️⃣ Скачайте файл по ссылке\n"
        f"2️⃣ Введите ключ при запуске\n"
        f"3️⃣ Наслаждайтесь игрой! 🎮\n\n"
        f"💬 Поддержка: @{Config.SUPPORT_CHAT_USERNAME}"
    )

    try:
        await bot.send_message(user_id, success_text, reply_markup=support_keyboard())
    except Exception as e:
        logger.error(f"Error sending to user {user_id}: {e}")

    # Уведомление админов
    admin_text = (
        f"💎 <b>НОВАЯ ПРОДАЖА ({source})</b>\n\n"
        f"👤 {order['user_name']}\n"
        f"🆔 {user_id}\n"
        f"📦 {product['name']} ({product['duration']})\n"
        f"💰 {order.get('amount', product['price'])} {order.get('currency', '₽')}\n"
        f"🔑 <code>{license_key}</code>\n"
        f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
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
        f"🔔 <b>НОВЫЙ ЗАКАЗ</b>\n\n"
        f"👤 {user.full_name}\n"
        f"🆔 <code>{user.id}</code>\n"
        f"📦 {product['name']} ({product['duration']})\n"
        f"💰 {price}\n"
        f"💳 {payment_method}\n"
        f"🆔 <code>{order_id}</code>\n\n"
        f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
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
    """Универсальная отправка стартового сообщения (для Message и CallbackQuery)"""
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

    # Deep link для Stars оплаты из MiniApp
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
                    "currency": "⭐",
                    "payment_method": "Telegram Stars",
                    "status": "pending",
                    "created_at": time.time()
                })
                await bot.send_invoice(
                    chat_id=message.from_user.id,
                    title=f"AimNoob — {product['name']}",
                    description=f"Подписка на {product['duration']} для {product['platform']}",
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
        "📋 <b>Подробная информация</b>\n\n"
        "🎮 <b>Версия:</b> 0.37.1 (Март 2026)\n"
        "🔥 <b>Статус:</b> Активно обновляется\n\n"
        "🛠️ <b>Функционал:</b>\n"
        "• 🎯 Умный AimBot с плавностью\n"
        "• 👁️ WallHack через препятствия\n"
        "• 📍 ESP с информацией об игроках\n"
        "• 🗺️ Мини-радар\n"
        "• ⚙️ Гибкие настройки\n\n"
        "🛡️ <b>Безопасность:</b>\n"
        "• Обход античитов\n"
        "• Регулярные обновления\n"
        "• Тестирование на безопасность\n\n"
        f"💬 Поддержка: @{Config.SUPPORT_CHAT_USERNAME}"
    )
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
            "requirements": "• Android 10.0+\n• 2 ГБ свободной памяти\n• Root не требуется",
            "includes": "• APK файл с читом\n• Инструкция по установке\n• Техническая поддержка"
        },
        "ios": {
            "title": "🍎 <b>iOS Version</b>",
            "requirements": "• iOS 14.0 - 18.0\n• Установка через AltStore\n• Jailbreak не требуется",
            "includes": "• IPA файл с читом\n• Подробная инструкция\n• Помощь в установке"
        }
    }

    info = platform_info[platform]
    text = (
        f"{info['title']}\n\n"
        f"🔧 <b>Требования:</b>\n{info['requirements']}\n\n"
        f"📦 <b>Что входит:</b>\n{info['includes']}\n\n"
        f"💰 <b>Выберите тариф:</b>"
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
        await callback.answer("❌ Ошибка", show_alert=True)
        return

    product_key = f"{parts[1]}_{parts[2]}"
    product = find_product_by_id(product_key)

    if not product:
        await callback.answer("❌ Продукт не найден", show_alert=True)
        return

    await state.update_data(selected_product=product)

    text = (
        f"🛒 <b>Оформление покупки</b>\n\n"
        f"{product['emoji']} <b>{product['name']}</b>\n"
        f"⏱️ Длительность: {product['duration']}\n\n"
        f"💎 <b>Стоимость:</b>\n"
        f"💳 Картой: {product['price']} ₽\n"
        f"⭐ Stars: {product['price_stars']} ⭐\n"
        f"₿ Крипта: {product['price_crypto_usdt']} USDT\n"
        f"💰 GOLD: {product['price_gold']} 🪙\n"
        f"🎨 NFT: {product['price_nft']} 🖼️\n\n"
        f"🎯 <b>Способ оплаты:</b>"
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
        await callback.answer("❌ Оплата картой временно недоступна", show_alert=True)
        return

    parts = callback.data.split("_")
    if len(parts) < 4:
        await callback.answer("❌ Ошибка", show_alert=True)
        return

    product = find_product(parts[2], parts[3])
    if not product:
        await callback.answer("❌ Продукт не найден", show_alert=True)
        return

    user_id = callback.from_user.id
    if not rate_limiter.check(user_id):
        await callback.answer("⏳ Подождите немного...", show_alert=True)
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
        "currency": "₽",
        "payment_method": "Картой",
        "status": "pending",
        "created_at": time.time()
    })

    text = (
        f"💳 <b>Оплата картой</b>\n\n"
        f"{product['emoji']} {product['name']}\n"
        f"⏱️ {product['duration']}\n"
        f"💰 К оплате: <b>{amount} ₽</b>\n"
        f"🆔 Номер заказа: <code>{order_id}</code>\n\n"
        f"🔄 <b>Инструкция:</b>\n"
        f"1️⃣ Нажмите «Оплатить картой»\n"
        f"2️⃣ Оплатите банковской картой\n"
        f"3️⃣ Вернитесь и нажмите «Проверить оплату»\n\n"
        f"💫 <b>Автоматическая проверка платежа</b>"
    )

    await callback.message.edit_text(
        text, reply_markup=payment_keyboard(payment_url, order_id)
    )
    await send_admin_notification(
        callback.from_user, product, "💳 Картой", f"{amount} ₽", order_id
    )
    await callback.answer()


# ========== ПРОВЕРКА ЮMONEY ==========
@dp.callback_query(F.data.startswith("checkym_"))
async def check_yoomoney_callback(callback: types.CallbackQuery):
    order_id = callback.data.removeprefix("checkym_")
    order = await orders.get_pending(order_id)

    if not order:
        if await orders.is_confirmed(order_id):
            await callback.answer("✅ Заказ уже подтвержден!", show_alert=True)
        else:
            await callback.answer("❌ Заказ не найден или истёк", show_alert=True)
        return

    user_id = callback.from_user.id
    if not rate_limiter.check(user_id):
        await callback.answer("⏳ Подождите перед повторной проверкой...", show_alert=True)
        return

    await callback.answer("🔍 Проверяем платеж...")

    checking_msg = await callback.message.edit_text(
        "🔄 <b>Проверка платежа...</b>\n\n"
        "🔍 Поиск транзакции в системе\n"
        "⏳ Подождите 15-25 секунд..."
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
        success = await process_successful_payment(order_id, "Автопроверка")
        if success:
            await checking_msg.edit_text(
                "✅ <b>Платеж найден!</b>\n\n"
                "🎉 Ваш заказ обработан\n"
                "📨 Проверьте новое сообщение ⬆️",
                reply_markup=support_keyboard()
            )
        else:
            await checking_msg.edit_text(
                "✅ <b>Заказ уже был обработан</b>",
                reply_markup=support_keyboard()
            )
    else:
        product = order['product']
        payment_url = create_payment_link(
            order["amount"], order_id,
            f"{product['name']} ({product['duration']})"
        )
        fail_text = (
            f"⏳ <b>Платеж пока не обнаружен</b>\n\n"
            f"💰 Сумма: {order['amount']} ₽\n"
            f"🆔 Заказ: <code>{order_id}</code>\n\n"
            f"🔍 <b>Возможные причины:</b>\n"
            f"• Платеж еще обрабатывается (1-3 мин)\n"
            f"• Оплачена неточная сумма\n"
            f"• Проблема на стороне банка\n\n"
            f"⏰ Попробуйте через 1-2 минуты\n"
            f"💬 Или обратитесь в поддержку"
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
        await callback.answer("❌ Ошибка", show_alert=True)
        return

    product = find_product(parts[2], parts[3])
    if not product:
        await callback.answer("❌ Продукт не найден", show_alert=True)
        return

    user_id = callback.from_user.id
    if not rate_limiter.check(user_id):
        await callback.answer("⏳ Подождите...", show_alert=True)
        return

    order_id = generate_order_id()
    await orders.add_pending(order_id, {
        "user_id": user_id,
        "user_name": callback.from_user.full_name,
        "product": product,
        "amount": product['price_stars'],
        "currency": "⭐",
        "payment_method": "Telegram Stars",
        "status": "pending",
        "created_at": time.time()
    })

    await bot.send_invoice(
        chat_id=user_id,
        title=f"AimNoob — {product['name']}",
        description=f"Подписка на {product['duration']} для {product['platform']}",
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
        await callback.answer("❌ Криптооплата временно недоступна", show_alert=True)
        return

    parts = callback.data.split("_")
    if len(parts) < 4:
        await callback.answer("❌ Ошибка", show_alert=True)
        return

    product = find_product(parts[2], parts[3])
    if not product:
        await callback.answer("❌ Продукт не найден", show_alert=True)
        return

    user_id = callback.from_user.id
    if not rate_limiter.check(user_id):
        await callback.answer("⏳ Подождите...", show_alert=True)
        return

    order_id = generate_order_id()
    amount_usdt = product["price_crypto_usdt"]
    description = f"AimNoob {product['name']} ({product['duration']})"

    invoice_data = await CryptoBotService.create_invoice(
        amount_usdt, order_id, description
    )
    if not invoice_data:
        await callback.answer(
            "❌ Ошибка создания инвойса. Попробуйте позже.",
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
        f"₿ <b>Криптооплата</b>\n\n"
        f"{product['emoji']} {product['name']}\n"
        f"⏱️ {product['duration']}\n"
        f"💰 К оплате: <b>{amount_usdt} USDT</b>\n"
        f"🆔 Заказ: <code>{order_id}</code>\n\n"
        f"🪙 <b>Принимаемые валюты:</b>\n"
        f"USDT, BTC, ETH, TON, LTC, BNB, TRX и др.\n\n"
        f"🔄 <b>Инструкция:</b>\n"
        f"1️⃣ Нажмите «Оплатить криптой»\n"
        f"2️⃣ Выберите валюту и переведите\n"
        f"3️⃣ Нажмите «Проверить платеж»"
    )

    await callback.message.edit_text(
        text,
        reply_markup=crypto_payment_keyboard(invoice_data["pay_url"], order_id)
    )
    await send_admin_notification(
        callback.from_user, product, "₿ CryptoBot",
        f"{amount_usdt} USDT", order_id
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("checkcr_"))
async def check_crypto_callback(callback: types.CallbackQuery):
    order_id = callback.data.removeprefix("checkcr_")
    order = await orders.get_pending(order_id)

    if not order:
        if await orders.is_confirmed(order_id):
            await callback.answer("✅ Уже оплачено!", show_alert=True)
        else:
            await callback.answer("❌ Заказ не найден", show_alert=True)
        return

    user_id = callback.from_user.id
    if not rate_limiter.check(user_id):
        await callback.answer("⏳ Подождите...", show_alert=True)
        return

    await callback.answer("🔍 Проверяем...")

    invoice_id = order.get("invoice_id")
    if not invoice_id:
        await callback.answer("❌ Ошибка: нет invoice_id", show_alert=True)
        return

    is_paid = await CryptoBotService.check_invoice(invoice_id)
    if is_paid:
        success = await process_successful_payment(order_id, "CryptoBot")
        if success:
            await callback.message.edit_text(
                "✅ <b>Криптоплатеж подтвержден!</b>\n\n"
                "🎉 Заказ обработан\n"
                "📨 Ключ отправлен в новом сообщении ⬆️",
                reply_markup=support_keyboard()
            )
    else:
        await callback.answer(
            "⏳ Платеж пока не подтвержден. Попробуйте через минуту.",
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
        await callback.answer("❌ Ошибка", show_alert=True)
        return

    product = find_product(parts[2], parts[3])
    if not product:
        await callback.answer("❌ Продукт не найден", show_alert=True)
        return

    user_id = callback.from_user.id
    if not rate_limiter.check(user_id):
        await callback.answer("⏳ Подождите...", show_alert=True)
        return

    method_config = {
        "gold": {
            "name": "GOLD",
            "icon": "💰",
            "price_key": "price_gold",
            "emoji": "🪙"
        },
        "nft": {
            "name": "NFT",
            "icon": "🎨",
            "price_key": "price_nft",
            "emoji": "🖼️"
        }
    }

    cfg = method_config[method]
    price = product[cfg["price_key"]]

    chat_message = (
        f"Привет! Хочу купить чит на Standoff 2. "
        f"Версия 0.37.1, подписка на {product['period_text']} "
        f"({product['platform']}). "
        f"Готов купить за {price} {cfg['name']} прямо сейчас"
    )
    encoded_message = quote(chat_message, safe='')
    support_url = f"https://t.me/{Config.SUPPORT_CHAT_USERNAME}?text={encoded_message}"

    text = (
        f"{cfg['icon']} <b>Оплата {cfg['name']}</b>\n\n"
        f"{product['emoji']} {product['name']}\n"
        f"⏱️ {product['duration']}\n"
        f"💰 Стоимость: <b>{price} {cfg['name']}</b>\n\n"
        f"📝 <b>Ваше сообщение для чата:</b>\n"
        f"<code>{chat_message}</code>\n\n"
        f"🔄 <b>Инструкция:</b>\n"
        f"1️⃣ Нажмите «Перейти к оплате»\n"
        f"2️⃣ Отправьте сообщение в чат\n"
        f"3️⃣ Ожидайте обработки"
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
    icon = "💰" if callback.data == "gold_sent" else "🎨"

    await callback.message.edit_text(
        f"✅ <b>Отлично!</b>\n\n"
        f"{icon} Ваш {method_name} заказ принят в обработку\n"
        f"⏱️ Время обработки: до 30 минут\n"
        f"📨 Уведомим о готовности заказа\n\n"
        f"💬 Поддержка: @{Config.SUPPORT_CHAT_USERNAME}",
        reply_markup=support_keyboard()
    )
    await callback.answer()


# ========== АДМИНСКИЕ КОМАНДЫ ==========
@dp.callback_query(F.data.startswith("admin_confirm_"))
async def admin_confirm_payment(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return

    order_id = callback.data.removeprefix("admin_confirm_")
    success = await process_successful_payment(order_id, "👨‍💼 Админ")

    if success:
        await callback.message.edit_text(
            f"✅ <b>Заказ подтвержден</b>\n\n"
            f"🆔 {order_id}\n"
            f"👨‍💼 Подтвердил: {callback.from_user.full_name}\n"
            f"📨 Ключ отправлен пользователю"
        )
        await callback.answer("✅ Готово!")
    else:
        await callback.answer(
            "❌ Заказ не найден или уже обработан",
            show_alert=True
        )


@dp.callback_query(F.data.startswith("admin_reject_"))
async def admin_reject_payment(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return

    order_id = callback.data.removeprefix("admin_reject_")
    order = await orders.remove_pending(order_id)

    if order:
        await callback.message.edit_text(
            f"❌ <b>Заказ отклонен</b>\n\n"
            f"🆔 {order_id}\n"
            f"👨‍💼 Отклонил: {callback.from_user.full_name}"
        )
        try:
            await bot.send_message(
                order['user_id'],
                f"❌ <b>Заказ отклонен</b>\n\n"
                f"🆔 {order_id}\n"
                f"📞 Обратитесь в поддержку\n"
                f"💬 @{Config.SUPPORT_CHAT_USERNAME}"
            )
        except Exception:
            pass

    await callback.answer("❌ Отклонен")


@dp.message(Command("orders"))
async def cmd_orders(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    stats = await orders.get_stats()
    text = "📊 <b>СТАТИСТИКА ЗАКАЗОВ</b>\n\n"

    recent = await orders.get_recent_pending(5)
    text += f"⏳ <b>Ожидают оплаты:</b> {stats['pending']}\n"
    for oid, order in recent:
        t = datetime.fromtimestamp(order['created_at']).strftime('%H:%M')
        text += f"• {t} | {order['user_name']} | {order['product']['name']}\n"

    text += f"\n✅ <b>Подтверждено:</b> {stats['confirmed']}\n"

    balance = await YooMoneyService.get_balance()
    if balance is not None:
        text += f"💰 <b>Баланс ЮМoney:</b> {balance} ₽\n"
    else:
        text += "💰 <b>Баланс ЮМoney:</b> ошибка\n"

    await message.answer(text)


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    await message.answer(
        "🔧 <b>Админ-команды:</b>\n\n"
        "/orders — Статистика заказов\n"
        "/help — Эта справка\n\n"
        "Подтверждение/отклонение заказов — "
        "через кнопки в уведомлениях"
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
        "apk": "📱 <b>Android Version</b>",
        "ios": "🍎 <b>iOS Version</b>"
    }

    text = (
        f"{platform_info.get(platform, '📱 <b>Version</b>')}\n\n"
        f"💰 <b>Выберите тариф:</b>"
    )
    await callback.message.edit_text(
        text, reply_markup=subscription_keyboard(platform)
    )
    await state.set_state(OrderState.choosing_subscription)
    await callback.answer()


# ========== WEB SERVER API ==========
class WebHandlers:
    """Обработчики HTTP запросов для MiniApp"""

    @staticmethod
    async def handle_miniapp(request: web.Request) -> web.Response:
        """Отдаем HTML MiniApp"""
        miniapp_path = os.path.join(
            os.path.dirname(__file__), 'miniapp.html'
        )
        try:
            with open(miniapp_path, 'r', encoding='utf-8') as f:
                html = f.read()
        except FileNotFoundError:
            html = "<html><body><h1>MiniApp not found</h1><p>Create miniapp.html</p></body></html>"
            logger.error(f"miniapp.html not found at {miniapp_path}")

        return web.Response(text=html, content_type='text/html', charset='utf-8')

    @staticmethod
    async def handle_health(request: web.Request) -> web.Response:
        stats = await orders.get_stats()
        return web.json_response({
            "status": "ok",
            **stats,
            "uptime": time.time()
        })

    @staticmethod
    async def handle_create_payment(request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response(
                {"success": False, "error": "Invalid JSON"}, status=400
            )

        product_id = data.get('product_id')
        method = data.get('method')
        user_id = data.get('user_id')
        user_name = data.get('user_name', 'MiniApp User')
        init_data = data.get('init_data', '')

        # Валидация initData
        if init_data:
            validated_user = validate_telegram_init_data(init_data, Config.BOT_TOKEN)
            if validated_user:
                user_id = validated_user.get('id', user_id)
                user_name = validated_user.get('first_name', user_name)
            else:
                logger.warning(f"Invalid initData from user_id={user_id}")

        if not product_id or not method or not user_id:
            return web.json_response(
                {"success": False, "error": "Missing required fields"},
                status=400
            )

        product = find_product_by_id(product_id)
        if not product:
            return web.json_response(
                {"success": False, "error": "Product not found"},
                status=404
            )

        if method not in ('yoomoney', 'crypto', 'stars', 'gold', 'nft'):
            return web.json_response(
                {"success": False, "error": "Unknown payment method"},
                status=400
            )

        order_id = generate_order_id()

        if method == 'yoomoney':
            if not Config.YOOMONEY_WALLET:
                return web.json_response(
                    {"success": False, "error": "Card payments unavailable"}
                )
            amount = product['price']
            payment_url = create_payment_link(
                amount, order_id,
                f"{product['name']} ({product['duration']})"
            )
            await orders.add_pending(order_id, {
                "user_id": user_id,
                "user_name": user_name,
                "product": product,
                "amount": amount,
                "currency": "₽",
                "payment_method": "Картой",
                "status": "pending",
                "created_at": time.time()
            })
            return web.json_response({
                "success": True,
                "payment_url": payment_url,
                "order_id": order_id
            })

        elif method == 'crypto':
            if not Config.CRYPTOBOT_TOKEN:
                return web.json_response(
                    {"success": False, "error": "Crypto payments unavailable"}
                )
            amount_usdt = product['price_crypto_usdt']
            description = f"AimNoob {product['name']} ({product['duration']})"
            invoice_data = await CryptoBotService.create_invoice(
                amount_usdt, order_id, description
            )
            if not invoice_data:
                return web.json_response(
                    {"success": False, "error": "Failed to create invoice"}
                )
            await orders.add_pending(order_id, {
                "user_id": user_id,
                "user_name": user_name,
                "product": product,
                "amount": amount_usdt,
                "currency": "USDT",
                "payment_method": "CryptoBot",
                "status": "pending",
                "invoice_id": invoice_data["invoice_id"],
                "created_at": time.time()
            })
            return web.json_response({
                "success": True,
                "payment_url": invoice_data["pay_url"],
                "invoice_id": invoice_data["invoice_id"],
                "order_id": order_id
            })

        elif method == 'stars':
            await orders.add_pending(order_id, {
                "user_id": user_id,
                "user_name": user_name,
                "product": product,
                "amount": product['price_stars'],
                "currency": "⭐",
                "payment_method": "Telegram Stars",
                "status": "pending",
                "created_at": time.time()
            })
            return web.json_response({
                "success": True,
                "order_id": order_id,
                "method": "stars"
            })

        else:  # gold, nft
            price_key = f'price_{method}'
            await orders.add_pending(order_id, {
                "user_id": user_id,
                "user_name": user_name,
                "product": product,
                "amount": product.get(price_key, 0),
                "currency": method.upper(),
                "payment_method": method.upper(),
                "status": "pending",
                "created_at": time.time()
            })
            return web.json_response({
                "success": True,
                "order_id": order_id,
                "method": method
            })

    @staticmethod
    async def handle_check_payment(request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"paid": False, "error": "Invalid JSON"})

        order_id = data.get('order_id')
        if not order_id:
            return web.json_response({"paid": False, "error": "No order_id"})

        # Уже подтверждён?
        confirmed = await orders.get_confirmed(order_id)
        if confirmed:
            return web.json_response({
                "paid": True,
                "license_key": confirmed.get('license_key', '')
            })

        order = await orders.get_pending(order_id)
        if not order:
            return web.json_response({"paid": False, "error": "Order not found"})

        payment_found = False
        for attempt in range(3):
            payment_found = await YooMoneyService.check_payment(
                order_id, order["amount"],
                order.get("created_at", time.time())
            )
            if payment_found:
                break
            await asyncio.sleep(3)

        if payment_found:
            await process_successful_payment(order_id, "MiniApp Автопроверка")
            cp = await orders.get_confirmed(order_id)
            return web.json_response({
                "paid": True,
                "license_key": cp.get('license_key', '') if cp else ''
            })

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
            return web.json_response({
                "paid": True,
                "license_key": cp.get('license_key', '') if cp else ''
            })

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
    logger.info("🚀 AIMNOOB PREMIUM SHOP BOT")
    logger.info("=" * 50)
    logger.info(f"ADMIN_IDS: {Config.ADMIN_IDS}")
    logger.info(f"MINIAPP_URL: {Config.MINIAPP_URL}")
    logger.info(f"WEB_PORT: {Config.WEB_PORT}")

    runner = None

    try:
        me = await bot.get_me()
        logger.info(f"🤖 Bot: @{me.username}")

        # Проверка YooMoney
        balance = await YooMoneyService.get_balance()
        if balance is not None:
            logger.info(f"✅ YooMoney connected (balance: {balance} ₽)")
        else:
            logger.warning("⚠️ YooMoney connection issues")

        # Установка кнопки MiniApp
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="🎮 Магазин",
                    web_app=WebAppInfo(url=Config.MINIAPP_URL)
                )
            )
            logger.info("✅ Menu button set")
        except Exception as e:
            logger.warning(f"Could not set menu button: {e}")

        # Логируем продукты
        for key, product in PRODUCTS.items():
            logger.info(
                f"📦 {product['emoji']} {product['name']} "
                f"({product['duration']}) — {product['price']}₽"
            )

        # Создаем web-приложение
        app = web.Application(middlewares=[cors_middleware])
        app.router.add_get('/', WebHandlers.handle_miniapp)
        app.router.add_get('/health', WebHandlers.handle_health)
        app.router.add_post('/api/create_payment', WebHandlers.handle_create_payment)
        app.router.add_post('/api/check_payment', WebHandlers.handle_check_payment)
        app.router.add_post('/api/check_crypto', WebHandlers.handle_check_crypto)

        # Запускаем web-сервер
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', Config.WEB_PORT)
        await site.start()
        logger.info(f"🌐 Web server started on port {Config.WEB_PORT}")

        # Запускаем polling
        logger.info("✨ Bot starting polling...")
        await dp.start_polling(bot)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if runner:
            await runner.cleanup()
        await bot.session.close()
        logger.info("Bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
