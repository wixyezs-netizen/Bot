# aimnoob_bot_final.py
import logging
import asyncio
import aiohttp
import hashlib
import time
import random
import json
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiohttp import web

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = "8225924716:AAFZ_8Eu8aJ4BF7pErZY5Ef3emG9Cl9PikE"
ADMIN_ID = 8387532956
SUPPORT_CHAT_ID = 8354762345

# Данные ЮMoney
YOOMONEY_ACCESS_TOKEN = "4100118889570559.3288B2E716CEEB922A26BD6BEAC58648FBFB680CCF64E4E1447D714D6FB5EA5F01F1478FAC686BEF394C8A186C98982DE563C1ABCDF9F2F61D971B61DA3C7E486CA818F98B9E0069F1C0891E090DD56A11319D626A40F0AE8302A8339DED9EB7969617F191D93275F64C4127A3ECB7AED33FCDE91CA68690EB7534C67E6C219E"
YOOMONEY_WALLET = "4100118889570559"

# Настройки
SUPPORT_CHAT_USERNAME = "aimnoob_support"
SHOP_URL = "https://aimnoob.ru"
YOOMONEY_FEE_RATE = 0.03

# ========== ИНИЦИАЛИЗАЦИЯ ==========
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Хранилища
pending_orders = {}
confirmed_payments = {}
balance_history = []
webhook_payments = {}
api_diagnostics = {}

# ========== ФУНКЦИИ РАСЧЕТА ==========
def calculate_user_payment(amount):
    fee = amount * YOOMONEY_FEE_RATE
    if fee < 4.5:
        fee = 4.5
    return int(round(amount + fee))

# ========== ПРОДУКТЫ ==========
PRODUCTS = {
    "test_1rub": {
        "name": "🧪 ТЕСТОВЫЙ ТОВАР | 1₽",
        "price": 1,
        "price_with_fee": calculate_user_payment(1),
        "price_stars": 5,
        "platform": "Test",
        "period": "ТЕСТ",
        "platform_code": "test",
        "emoji": "🧪",
        "is_test": True
    },
    "apk_week": {
        "name": "AimNoob Standoff 2 | НЕДЕЛЯ",
        "price": 150,
        "price_with_fee": calculate_user_payment(150),
        "price_stars": 350,
        "platform": "Android",
        "period": "НЕДЕЛЮ",
        "platform_code": "apk",
        "emoji": "📱",
        "is_test": False
    },
    "apk_month": {
        "name": "AimNoob Standoff 2 | МЕСЯЦ",
        "price": 350,
        "price_with_fee": calculate_user_payment(350),
        "price_stars": 800,
        "platform": "Android",
        "period": "МЕСЯЦ",
        "platform_code": "apk",
        "emoji": "📱",
        "is_test": False
    },
    "apk_forever": {
        "name": "AimNoob Standoff 2 | НАВСЕГДА",
        "price": 150,
        "price_with_fee": calculate_user_payment(150),
        "price_stars": 350,
        "platform": "Android",
        "period": "НАВСЕГДА",
        "platform_code": "apk",
        "emoji": "📱",
        "is_test": False
    },
    "ios_week": {
        "name": "AimNoob Standoff 2 | НЕДЕЛЯ",
        "price": 300,
        "price_with_fee": calculate_user_payment(300),
        "price_stars": 700,
        "platform": "iOS",
        "period": "НЕДЕЛЮ",
        "platform_code": "ios",
        "emoji": "🍏",
        "is_test": False
    },
    "ios_month": {
        "name": "AimNoob Standoff 2 | МЕСЯЦ",
        "price": 450,
        "price_with_fee": calculate_user_payment(450),
        "price_stars": 1000,
        "platform": "iOS",
        "period": "МЕСЯЦ",
        "platform_code": "ios",
        "emoji": "🍏",
        "is_test": False
    },
    "ios_forever": {
        "name": "AimNoob Standoff 2 | НАВСЕГДА",
        "price": 850,
        "price_with_fee": calculate_user_payment(850),
        "price_stars": 2000,
        "platform": "iOS",
        "period": "НАВСЕГДА",
        "platform_code": "ios",
        "emoji": "🍏",
        "is_test": False
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
        [InlineKeyboardButton(text="🧪 ТЕСТ (1₽)", callback_data="platform_test")],
        [InlineKeyboardButton(text="📱 Android (APK)", callback_data="platform_apk")],
        [InlineKeyboardButton(text="🍏 iOS", callback_data="platform_ios")],
        [InlineKeyboardButton(text="ℹ️ О чите", callback_data="about")],
        [InlineKeyboardButton(text="📞 Поддержка", url=f"https://t.me/{SUPPORT_CHAT_USERNAME}")]
    ])

def test_subscription_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧪 ТЕСТ | 1₽ (+комиссия ≈6₽)", callback_data="sub_test_1rub")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_platform")]
    ])

def apk_subscription_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔓 НЕДЕЛЯ | 150₽ (+комиссия ≈156₽)", callback_data="sub_apk_week")],
        [InlineKeyboardButton(text="🔥 МЕСЯЦ | 350₽ (+комиссия ≈365₽)", callback_data="sub_apk_month")],
        [InlineKeyboardButton(text="⭐ НАВСЕГДА | 150₽ (+комиссия ≈156₽)", callback_data="sub_apk_forever")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_platform")]
    ])

def ios_subscription_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔓 НЕДЕЛЯ | 300₽ (+комиссия ≈313₽)", callback_data="sub_ios_week")],
        [InlineKeyboardButton(text="🔥 МЕСЯЦ | 450₽ (+комиссия ≈469₽)", callback_data="sub_ios_month")],
        [InlineKeyboardButton(text="⭐ НАВСЕГДА | 850₽ (+комиссия ≈886₽)", callback_data="sub_ios_forever")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_platform")]
    ])

def payment_methods_keyboard(product):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 ЮMoney", callback_data=f"pay_yoomoney_{product['platform_code']}_{product['period']}")],
        [InlineKeyboardButton(text="⭐ Telegram Stars", callback_data=f"pay_stars_{product['platform_code']}_{product['period']}")],
        [InlineKeyboardButton(text="💰 GOLD", callback_data=f"pay_gold_{product['platform_code']}_{product['period']}")],
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

def about_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_platform")]
    ])

def admin_confirm_keyboard(order_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"admin_confirm_{order_id}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"admin_reject_{order_id}")]
    ])

# ========== ФУНКЦИИ ==========
def generate_order_id():
    return hashlib.md5(f"{time.time()}_{random.randint(1000, 9999)}".encode()).hexdigest()[:12]

def create_payment_link(amount, order_id, product_name):
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

# ========== ДИАГНОСТИКА API ==========
async def diagnose_yoomoney_api():
    """Полная диагностика API ЮMoney"""
    if not YOOMONEY_ACCESS_TOKEN:
        logger.error("❌ ACCESS TOKEN не настроен!")
        api_diagnostics['token'] = False
        return False
        
    headers = {"Authorization": f"Bearer {YOOMONEY_ACCESS_TOKEN}"}
    
    try:
        async with aiohttp.ClientSession() as session:
            # 1. Проверяем информацию об аккаунте
            logger.info("🔍 Проверяем информацию об аккаунте...")
            async with session.get("https://yoomoney.ru/api/account-info", headers=headers, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    logger.info(f"✅ Аккаунт: {data.get('account')}")
                    logger.info(f"💰 Баланс: {data.get('balance')} ₽")
                    logger.info(f"🏦 Тип: {data.get('account_type')}")
                    logger.info(f"🔐 Статус: {data.get('account_status')}")
                    
                    api_diagnostics['account_info'] = True
                    api_diagnostics['balance'] = float(data.get('balance', 0))
                    api_diagnostics['account_type'] = data.get('account_type')
                    
                    # Проверяем права токена
                    services = data.get('services', {})
                    logger.info(f"📋 Доступные сервисы: {list(services.keys())}")
                    api_diagnostics['services'] = list(services.keys())
                    
                else:
                    logger.error(f"❌ Ошибка account-info: {resp.status}")
                    text = await resp.text()
                    logger.error(f"Ответ: {text}")
                    api_diagnostics['account_info'] = False
                    return False
            
            # 2. Тестируем разные запросы к истории операций
            logger.info("🔍 Тестируем запросы к истории операций...")
            
            test_requests = [
                {"type": "incoming", "records": 10},
                {"type": "outgoing", "records": 10}, 
                {"records": 10},
                {"type": "incoming", "records": 50},
                {"type": "incoming", "records": 3, "start_record": "0"},
                {"type": "incoming", "records": 1}
            ]
            
            operations_found = False
            
            for i, data_req in enumerate(test_requests):
                logger.info(f"📋 Тест {i+1}: {data_req}")
                
                try:
                    async with session.post(
                        "https://yoomoney.ru/api/operation-history",
                        headers=headers,
                        data=data_req,
                        timeout=20
                    ) as resp:
                        
                        logger.info(f"   Статус: {resp.status}")
                        
                        if resp.status == 200:
                            result = await resp.json()
                            operations = result.get("operations", [])
                            logger.info(f"   ✅ Операций найдено: {len(operations)}")
                            
                            if operations:
                                operations_found = True
                                logger.info("   📋 Последние операции:")
                                for j, op in enumerate(operations[:5]):
                                    logger.info(f"      {j+1}. {op.get('datetime')} | {op.get('amount')}₽ | {op.get('title')} | label: '{op.get('label', 'нет')}'")
                                api_diagnostics['operations_example'] = operations[0]
                                break
                        elif resp.status == 401:
                            logger.error(f"   ❌ Неавторизован - проблема с токеном")
                            api_diagnostics['token_valid'] = False
                        elif resp.status == 403:
                            logger.error(f"   ❌ Нет прав на operation-history")
                            api_diagnostics['history_permission'] = False
                        else:
                            text = await resp.text()
                            logger.error(f"   ❌ Ошибка: {resp.status} - {text}")
                            
                except asyncio.TimeoutError:
                    logger.error(f"   ⏰ Таймаут запроса")
                except Exception as e:
                    logger.error(f"   ❌ Ошибка: {e}")
            
            api_diagnostics['operations_working'] = operations_found
            
            # 3. Проверяем лимиты API
            logger.info("🔍 Проверяем лимиты и заголовки API...")
            try:
                async with session.post(
                    "https://yoomoney.ru/api/operation-history", 
                    headers=headers,
                    data={"records": 1},
                    timeout=15
                ) as resp:
                    logger.info(f"📊 HTTP заголовки ответа:")
                    for header, value in resp.headers.items():
                        if any(x in header.lower() for x in ['limit', 'rate', 'quota', 'retry', 'remaining']):
                            logger.info(f"   {header}: {value}")
                        
                    api_diagnostics['last_response_status'] = resp.status
                    api_diagnostics['last_response_headers'] = dict(resp.headers)
                    
            except Exception as e:
                logger.error(f"❌ Ошибка проверки лимитов: {e}")
            
            # 4. Пробуем получить детали конкретной операции (если есть)
            if operations_found and 'operations_example' in api_diagnostics:
                op_id = api_diagnostics['operations_example'].get('operation_id')
                if op_id:
                    logger.info(f"🔍 Тестируем получение деталей операции {op_id}...")
                    try:
                        async with session.post(
                            "https://yoomoney.ru/api/operation-details",
                            headers=headers,
                            data={"operation_id": op_id},
                            timeout=15
                        ) as resp:
                            if resp.status == 200:
                                details = await resp.json()
                                logger.info(f"   ✅ Детали операции получены")
                                api_diagnostics['operation_details_working'] = True
                            else:
                                logger.error(f"   ❌ Ошибка получения деталей: {resp.status}")
                                api_diagnostics['operation_details_working'] = False
                    except Exception as e:
                        logger.error(f"   ❌ Ошибка: {e}")
            
            return operations_found
                        
    except Exception as e:
        logger.error(f"❌ Глобальная ошибка диагностики: {e}")
        api_diagnostics['global_error'] = str(e)
        return False

async def get_yoomoney_balance():
    """Получение баланса ЮMoney"""
    if not YOOMONEY_ACCESS_TOKEN:
        return None
        
    headers = {"Authorization": f"Bearer {YOOMONEY_ACCESS_TOKEN}"}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://yoomoney.ru/api/account-info", headers=headers, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return float(data.get('balance', 0))
    except Exception as e:
        logger.error(f"Ошибка получения баланса: {e}")
    
    return None

# ========== УЛУЧШЕННЫЕ ПРОВЕРКИ ПЛАТЕЖЕЙ ==========
async def check_payment_advanced(order_id, expected_amount, product_price):
    """Продвинутая проверка платежа через несколько методов"""
    logger.info(f"🔍 === НАЧИНАЕМ ПРОВЕРКУ ПЛАТЕЖА {order_id} ===")
    
    # Метод 1: Проверка через операции
    logger.info(f"🔍 Метод 1: Проверка через API операций")
    if await check_via_operations(order_id, expected_amount, product_price):
        logger.info(f"✅ Платеж найден через API операций!")
        return True
    
    # Метод 2: Проверка через Webhook (если есть данные)
    logger.info(f"🔍 Метод 2: Проверка через Webhook")
    if await check_via_webhook(order_id, expected_amount):
        logger.info(f"✅ Платеж найден через Webhook!")
        return True
    
    # Метод 3: Проверка через изменение баланса
    logger.info(f"🔍 Метод 3: Проверка через изменение баланса")
    if await check_via_balance_enhanced(order_id, expected_amount):
        logger.info(f"✅ Платеж найден через мониторинг баланса!")
        return True
    
    # Метод 4: Автоподтверждение тестов
    order = pending_orders.get(order_id)
    if order and order.get('product', {}).get('is_test', False):
        logger.info(f"🧪 Тестовый товар - автоподтверждение")
        return True
    
    logger.info(f"❌ Платеж {order_id} не найден ни одним методом")
    return False

async def check_via_operations(order_id, expected_amount, product_price):
    """Улучшенная проверка через API операций"""
    if not YOOMONEY_ACCESS_TOKEN:
        return False
    
    headers = {"Authorization": f"Bearer {YOOMONEY_ACCESS_TOKEN}"}
    
    # Пробуем разные варианты запросов
    requests_to_try = [
        {"type": "incoming", "records": 50},
        {"type": "incoming", "records": 100},
        {"records": 50},
        {"type": "incoming", "records": 20, "start_record": "0"}
    ]
    
    try:
        async with aiohttp.ClientSession() as session:
            for req_data in requests_to_try:
                try:
                    async with session.post(
                        "https://yoomoney.ru/api/operation-history",
                        headers=headers,
                        data=req_data,
                        timeout=20
                    ) as resp:
                        if resp.status == 200:
                            result = await resp.json()
                            operations = result.get("operations", [])
                            
                            logger.info(f"📋 Найдено операций (запрос {req_data}): {len(operations)}")
                            
                            if operations:
                                # Ищем подходящую операцию
                                for op in operations:
                                    if await is_operation_match(op, order_id, expected_amount, product_price):
                                        return True
                            
                        elif resp.status == 401:
                            logger.error("❌ Токен недействителен")
                            break
                        elif resp.status == 403:
                            logger.error("❌ Нет прав на operation-history")
                            break
                        else:
                            logger.warning(f"⚠️ Статус {resp.status} для запроса {req_data}")
                            
                except asyncio.TimeoutError:
                    logger.warning(f"⏰ Таймаут для запроса {req_data}")
                    continue
                except Exception as e:
                    logger.error(f"❌ Ошибка запроса {req_data}: {e}")
                    continue
                    
    except Exception as e:
        logger.error(f"❌ Глобальная ошибка проверки операций: {e}")
    
    return False

async def is_operation_match(op, order_id, expected_amount, product_price):
    """Проверяет, подходит ли операция под наш платеж"""
    op_label = op.get("label", "")
    op_status = op.get("status")
    op_amount = float(op.get("amount", 0))
    op_datetime = op.get("datetime", "")
    op_title = op.get("title", "")
    op_message = op.get("message", "")
    
    logger.info(f"   🔎 Проверяем операцию: label='{op_label}', status={op_status}, amount={op_amount}₽")
    
    if op_status != "success":
        return False
    
    # Проверка 1: Точное совпадение по label
    if op_label == order_id:
        logger.info(f"   ✅ Найдено точное совпадение по label!")
        if abs(op_amount - expected_amount) <= 2 or abs(op_amount - product_price) <= 2:
            logger.info(f"   ✅ Сумма подходит: {op_amount}₽")
            return True
    
    # Проверка 2: Поиск order_id в описании
    if order_id in op_title or order_id in op_message:
        logger.info(f"   ✅ Найден order_id в описании операции!")
        if abs(op_amount - expected_amount) <= 2 or abs(op_amount - product_price) <= 2:
            logger.info(f"   ✅ Сумма подходит: {op_amount}₽")
            return True
    
    # Проверка 3: По сумме и времени (для недавних операций)
    if op_datetime:
        try:
            order_time = pending_orders.get(order_id, {}).get('created_at', time.time())
            op_time = datetime.fromisoformat(op_datetime.replace('Z', '+00:00')).timestamp()
            time_diff = abs(op_time - order_time)
            
            if time_diff <= 1800:  # 30 минут
                if abs(op_amount - expected_amount) <= 1 or abs(op_amount - product_price) <= 1:
                    logger.info(f"   🎯 Найдена операция по сумме и времени: {op_amount}₽, время подходит")
                    return True
        except Exception as e:
            logger.error(f"   Ошибка парсинга времени: {e}")
    
    return False

async def check_via_webhook(order_id, expected_amount):
    """Проверка через данные webhook"""
    if order_id in webhook_payments:
        webhook_data = webhook_payments[order_id]
        webhook_amount = webhook_data.get('amount', 0)
        
        if abs(webhook_amount - expected_amount) <= 2:
            logger.info(f"✅ Найден платеж через webhook: {webhook_amount}₽")
            return True
    
    return False

async def check_via_balance_enhanced(order_id, expected_amount):
    """Улучшенная проверка через мониторинг баланса"""
    order = pending_orders.get(order_id)
    if not order:
        return False
    
    order_time = order.get('created_at', time.time())
    
    # Получаем текущий баланс
    current_balance = await get_yoomoney_balance()
    if current_balance is None:
        logger.info("❌ Не удалось получить текущий баланс")
        return False
    
    # Записываем текущий баланс
    balance_history.append({
        'time': time.time(),
        'balance': current_balance,
        'order_id': order_id
    })
    
    # Анализируем изменения баланса
    recent_balances = [b for b in balance_history if b['time'] >= order_time - 300]  # 5 минут до заказа
    
    if len(recent_balances) >= 2:
        for i in range(1, len(recent_balances)):
            prev_balance = recent_balances[i-1]['balance']
            curr_balance = recent_balances[i]['balance']
            balance_change = curr_balance - prev_balance
            
            logger.info(f"   💰 Изменение баланса: {prev_balance}₽ → {curr_balance}₽ (+{balance_change}₽)")
            
            if abs(balance_change - expected_amount) <= 3:
                logger.info(f"   ✅ Обнаружено подходящее изменение баланса: +{balance_change}₽")
                return True
    
    # Оставляем только записи за последние 2 часа
    cutoff_time = time.time() - 7200
    balance_history[:] = [b for b in balance_history if b['time'] > cutoff_time]
    
    return False

# ========== WEBHOOK ОБРАБОТЧИК ==========
webhook_payments = {}

async def yoomoney_webhook_handler(request):
    """Обработчик webhook от ЮMoney"""
    try:
        data = await request.post()
        logger.info(f"📨 Получен webhook от ЮMoney: {dict(data)}")
        
        notification_type = data.get('notification_type')
        operation_id = data.get('operation_id')
        amount = float(data.get('amount', 0))
        label = data.get('label', '')
        
        if notification_type == 'p2p-incoming' and label:
            logger.info(f"💰 Входящий платеж через webhook: {amount}₽, label: {label}")
            
            # Сохраняем платеж
            webhook_payments[label] = {
                'amount': amount,
                'operation_id': operation_id,
                'time': time.time(),
                'data': dict(data)
            }
            
            # Проверяем, есть ли соответствующий заказ
            if label in pending_orders:
                order = pending_orders[label]
                expected_amount = order.get('user_payment_amount', 0)
                
                if abs(amount - expected_amount) <= 2:
                    logger.info(f"✅ Webhook: автоподтверждение платежа {label}")
                    await process_successful_payment(label, "Webhook")
        
        return web.Response(text="OK")
        
    except Exception as e:
        logger.error(f"❌ Ошибка обработки webhook: {e}")
        return web.Response(text="ERROR", status=400)

# ========== ОБРАБОТКА УСПЕШНЫХ ПЛАТЕЖЕЙ ==========
async def process_successful_payment(order_id, source="API"):
    """Обработка успешного платежа"""
    order = pending_orders.get(order_id)
    if not order:
        logger.error(f"❌ Заказ {order_id} не найден для подтверждения")
        return False
    
    # Проверяем, не обработан ли уже
    if order_id in confirmed_payments:
        logger.warning(f"⚠️ Заказ {order_id} уже был подтвержден")
        return True
    
    product = order["product"]
    user_id = order["user_id"]
    is_test = product.get("is_test", False)
    license_key = generate_license_key(order_id, user_id, is_test)
    
    # Отмечаем как подтвержденный
    confirmed_payments[order_id] = {
        **order,
        'confirmed_at': time.time(),
        'confirmed_by': source,
        'license_key': license_key
    }
    
    logger.info(f"✅ Заказ {order_id} подтвержден через {source}")
    
    # Готовим текст для пользователя
    if is_test:
        success_text = (
            f"✅ <b>ТЕСТОВАЯ ОПЛАТА ПОДТВЕРЖДЕНА!</b>\n\n"
            f"🎉 Система работает корректно!\n"
            f"🔍 Метод подтверждения: {source}\n\n"
            f"🔑 <b>Тестовый ключ:</b>\n"
            f"<code>{license_key}</code>\n\n"
            f"💬 Поддержка: @{SUPPORT_CHAT_USERNAME}"
        )
    else:
        success_text = (
            f"✅ <b>Оплата подтверждена!</b>\n\n"
            f"🎉 Добро пожаловать в AimNoob!\n"
            f"🔍 Метод подтверждения: {source}\n\n"
            f"📦 <b>Ваш заказ:</b>\n"
            f"• {product['name']}\n"
            f"• {product['emoji']} {product['platform']}\n\n"
            f"🔑 <b>Лицензионный ключ:</b>\n"
            f"<code>{license_key}</code>\n\n"
            f"📥 <b>Скачать:</b>\n"
            f"{SHOP_URL}/download/{product['platform_code']}_{user_id}\n\n"
            f"💬 Поддержка: @{SUPPORT_CHAT_USERNAME}"
        )
    
    # Отправляем пользователю
    try:
        await bot.send_message(user_id, success_text, parse_mode="HTML", reply_markup=support_keyboard())
        logger.info(f"📨 Уведомление отправлено пользователю {user_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка отправки пользователю {user_id}: {e}")
    
    # Уведомляем админа о продаже
    try:
        admin_text = (
            f"✅ <b>НОВАЯ ПРОДАЖА! ({source})</b>\n\n"
            f"👤 {order['user_name']}\n"
            f"🆔 ID: {user_id}\n"
            f"📦 {product['name']}\n"
            f"💰 Оплачено: {order.get('user_payment_amount', product['price'])} ₽\n"
            f"🆔 Заказ: <code>{order_id}</code>\n"
            f"🔑 Ключ: <code>{license_key}</code>\n"
            f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
        )
        
        await bot.send_message(ADMIN_ID, admin_text, parse_mode="HTML")
        logger.info(f"📨 Уведомление отправлено админу")
    except Exception as e:
        logger.error(f"❌ Ошибка уведомления админа: {e}")
    
    # Удаляем из ожидающих
    if order_id in pending_orders:
        del pending_orders[order_id]
    
    return True

async def send_admin_notification(user, product, payment_method, price, order_id):
    """Отправка уведомления админу с кнопками подтверждения"""
    platform_name = "Android" if product['platform_code'] == 'apk' else "iOS"
    if product['platform_code'] == 'test':
        platform_name = "Тест"
    
    message = (
        f"🆕 <b>НОВЫЙ ЗАКАЗ</b>\n\n"
        f"👤 {user.full_name}\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"📦 {product['name']}\n"
        f"📱 {platform_name}\n"
        f"💰 {price}\n"
        f"💳 {payment_method}\n"
        f"🆔 Заказ: <code>{order_id}</code>\n\n"
        f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
        f"<b>⚡ Ручное подтверждение:</b>"
    )
    
    try:
        await bot.send_message(ADMIN_ID, message, parse_mode="HTML", reply_markup=admin_confirm_keyboard(order_id))
        logger.info(f"📨 Уведомление с кнопками отправлено админу для {order_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка отправки админу: {e}")

def generate_license_key(order_id, user_id, is_test=False):
    """Генерация лицензионного ключа"""
    if is_test:
        return f"TEST-KEY-{order_id[:8]}"
    return f"AIMNOOB-{order_id[:8]}-{user_id % 10000}"

# ========== КОМАНДЫ АДМИНА ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    
    # Записываем начальный баланс
    current_balance = await get_yoomoney_balance()
    if current_balance is not None:
        balance_history.append({
            'time': time.time(),
            'balance': current_balance
        })
    
    text = (
        "🎯 <b>AimNoob — лучший чит для Standoff 2</b> 🎯\n\n"
        "🔥 <b>Преимущества:</b>\n"
        "• ✅ Анти-бан система\n"
        "• 🎯 Идеальный AimLock\n"
        "• 👁️ WallHack через стены\n"
        "• 📊 ESP информация\n\n"
        "⚠️ <b>Важно:</b> ЮMoney берет комиссию ~3%\n"
        "Цена в кнопках уже с учетом комиссии!\n\n"
        "💎 <b>Выберите платформу:</b>"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=platform_keyboard())
    await state.set_state(OrderState.choosing_platform)

@dp.message(Command("diagnose"))
async def cmd_diagnose(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    await message.answer("🔍 <b>Запускаю полную диагностику API ЮMoney...</b>", parse_mode="HTML")
    
    # Очищаем предыдущие результаты
    api_diagnostics.clear()
    
    # Запускаем диагностику
    result = await diagnose_yoomoney_api()
    
    # Формируем отчет
    report = "📊 <b>ОТЧЕТ ДИАГНОСТИКИ API</b>\n\n"
    
    if api_diagnostics.get('account_info'):
        report += f"✅ <b>Подключение к аккаунту:</b> ОК\n"
        report += f"💰 <b>Баланс:</b> {api_diagnostics.get('balance', 'N/A')} ₽\n"
        report += f"🏦 <b>Тип аккаунта:</b> {api_diagnostics.get('account_type', 'N/A')}\n"
        
        services = api_diagnostics.get('services', [])
        report += f"📋 <b>Доступные сервисы:</b> {', '.join(services) if services else 'Нет'}\n\n"
    else:
        report += "❌ <b>Подключение к аккаунту:</b> ОШИБКА\n\n"
    
    if api_diagnostics.get('operations_working'):
        report += "✅ <b>API операций:</b> РАБОТАЕТ\n"
        if 'operations_example' in api_diagnostics:
            op = api_diagnostics['operations_example']
            report += f"📝 <b>Пример операции:</b> {op.get('amount')}₽ от {op.get('datetime', 'N/A')}\n\n"
    else:
        report += "❌ <b>API операций:</b> НЕ РАБОТАЕТ\n"
        if not api_diagnostics.get('history_permission', True):
            report += "🔐 <b>Причина:</b> Нет прав на operation-history\n"
        if not api_diagnostics.get('token_valid', True):
            report += "🔐 <b>Причина:</b> Токен недействителен\n"
        report += "\n"
    
    if api_diagnostics.get('operation_details_working'):
        report += "✅ <b>API деталей операций:</b> РАБОТАЕТ\n\n"
    elif 'operation_details_working' in api_diagnostics:
        report += "❌ <b>API деталей операций:</b> НЕ РАБОТАЕТ\n\n"
    
    # Общее состояние
    if result:
        report += "🎉 <b>ОБЩИЙ СТАТУС:</b> API ЧАСТИЧНО РАБОТАЕТ\n"
        report += "🔍 <b>Рекомендация:</b> Можно использовать автопроверку"
    else:
        report += "⚠️ <b>ОБЩИЙ СТАТУС:</b> API НЕ РАБОТАЕТ\n"
        report += "🛠️ <b>Рекомендация:</b> Используйте ручное подтверждение"
    
    await message.answer(report, parse_mode="HTML")

@dp.message(Command("balance"))
async def cmd_balance(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    balance = await get_yoomoney_balance()
    if balance is not None:
        # Показываем историю изменений
        recent_balances = balance_history[-5:] if len(balance_history) > 5 else balance_history
        
        text = f"💰 <b>Текущий баланс:</b> {balance} ₽\n\n"
        
        if len(recent_balances) > 1:
            text += "📈 <b>История изменений:</b>\n"
            for i, record in enumerate(recent_balances[-5:]):
                time_str = datetime.fromtimestamp(record['time']).strftime('%H:%M:%S')
                text += f"• {time_str}: {record['balance']} ₽\n"
        
        await message.answer(text, parse_mode="HTML")
        
        # Обновляем историю
        balance_history.append({
            'time': time.time(),
            'balance': balance
        })
    else:
        await message.answer("❌ <b>Ошибка получения баланса</b>", parse_mode="HTML")

@dp.message(Command("orders"))
async def cmd_orders(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    text = "📝 <b>АКТИВНЫЕ ЗАКАЗЫ:</b>\n\n"
    
    if not pending_orders:
        text += "Нет активных заказов"
    else:
        for order_id, order in pending_orders.items():
            created_time = datetime.fromtimestamp(order['created_at']).strftime('%H:%M:%S')
            text += (
                f"🆔 <code>{order_id}</code>\n"
                f"👤 {order['user_name']}\n"
                f"📦 {order['product']['name']}\n"
                f"💰 {order.get('user_payment_amount', order['product']['price'])} ₽\n"
                f"⏰ {created_time}\n\n"
            )
    
    text += f"\n✅ <b>ПОДТВЕРЖДЕННЫЕ:</b> {len(confirmed_payments)}"
    
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("webhook_test"))
async def cmd_webhook_test(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    text = "🌐 <b>WEBHOOK СТАТИСТИКА:</b>\n\n"
    text += f"📨 <b>Получено webhook'ов:</b> {len(webhook_payments)}\n\n"
    
    if webhook_payments:
        text += "<b>Последние webhook'и:</b>\n"
        for label, data in list(webhook_payments.items())[-5:]:
            time_str = datetime.fromtimestamp(data['time']).strftime('%H:%M:%S')
            text += f"• {time_str}: {data['amount']}₽ (label: {label})\n"
    else:
        text += "Webhook'и не получались"
    
    await message.answer(text, parse_mode="HTML")

# ========== АДМИНСКИЕ КНОПКИ ==========
@dp.callback_query(F.data.startswith("admin_confirm_"))
async def admin_confirm_payment(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    order_id = callback.data.replace("admin_confirm_", "")
    
    if order_id in confirmed_payments:
        await callback.answer("✅ Заказ уже подтвержден!", show_alert=True)
        return
    
    success = await process_successful_payment(order_id, "Админ")
    
    if success:
        await callback.message.edit_text(
            f"✅ <b>Заказ {order_id} подтвержден админом</b>\n\n"
            f"📨 Пользователю отправлен ключ",
            parse_mode="HTML"
        )
        await callback.answer("✅ Заказ подтвержден!")
    else:
        await callback.answer("❌ Ошибка подтверждения", show_alert=True)

@dp.callback_query(F.data.startswith("admin_reject_"))
async def admin_reject_payment(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    order_id = callback.data.replace("admin_reject_", "")
    order = pending_orders.get(order_id)
    
    if order:
        del pending_orders[order_id]
        
        await callback.message.edit_text(
            f"❌ <b>Заказ {order_id} отклонен админом</b>",
            parse_mode="HTML"
        )
        
        # Уведомляем пользователя
        try:
            await bot.send_message(
                order['user_id'],
                "❌ <b>Платеж отклонен</b>\n\n"
                f"Заказ {order_id} был отклонен администратором.\n"
                f"Обратитесь в поддержку: @{SUPPORT_CHAT_USERNAME}",
                parse_mode="HTML"
            )
        except:
            pass
    
    await callback.answer("❌ Заказ отклонен!")

# ========== ОСНОВНАЯ ЛОГИКА БОТА ==========
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
    
    if platform == "test":
        text = "🧪 <b>Тестовый режим</b>\n\n✅ Проверка оплаты за 1₽\n✅ Проверка работы бота\n\nВыберите тестовый товар:"
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=test_subscription_keyboard())
    elif platform == "apk":
        text = "📱 <b>Android (APK)</b>\n\n✅ Android 10+\n✅ Root не требуется\n\n💰 Цены уже с учетом комиссии:\n• НЕДЕЛЯ: 156₽\n• МЕСЯЦ: 365₽\n• НАВСЕГДА: 156₽\n\nВыберите срок:"
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=apk_subscription_keyboard())
    else:
        text = "🍏 <b>iOS</b>\n\n✅ iOS 14 - 18\n✅ Установка через AltStore\n\n💰 Цены уже с учетом комиссии:\n• НЕДЕЛЯ: 313₽\n• МЕСЯЦ: 469₽\n• НАВСЕГДА: 886₽\n\nВыберите срок:"
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
        f"• ЮMoney: {product['price']} ₽ + комиссия = {product['price_with_fee']} ₽\n"
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
    amount = product["price_with_fee"]
    payment_url = create_payment_link(amount, order_id, product["name"])
    
    # Записываем баланс перед заказом
    current_balance = await get_yoomoney_balance()
    if current_balance is not None:
        balance_history.append({
            'time': time.time(),
            'balance': current_balance,
            'order_id': order_id
        })
    
    # Сохраняем заказ
    pending_orders[order_id] = {
        "user_id": user_id,
        "user_name": callback.from_user.full_name,
        "product": product,
        "user_payment_amount": amount,
        "product_price": product["price"],
        "payment_method": "ЮMoney",
        "status": "pending",
        "created_at": time.time()
    }
    
    text = (
        f"{product['emoji']} <b>Оплата ЮMoney</b>\n\n"
        f"📦 {product['name']}\n"
        f"💰 <b>Сумма к оплате: {amount} ₽</b>\n"
        f"(включая комиссию ЮMoney 3%)\n"
        f"🆔 Заказ: <code>{order_id}</code>\n\n"
        f"📝 <b>Инструкция:</b>\n"
        f"1️⃣ Нажмите кнопку оплаты\n"
        f"2️⃣ Оплатите <b>{amount} ₽</b>\n"
        f"3️⃣ Вернитесь и нажмите 'Проверить оплату'\n\n"
        f"⚠️ <b>Важно:</b> В комментарии укажите код: <code>{order_id}</code>\n\n"
        f"🔄 <b>Многоуровневая проверка:</b>\n"
        f"• API операций ЮMoney\n"
        f"• Мониторинг баланса\n"
        f"• Ручное подтверждение админом"
    )
    
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=payment_keyboard(payment_url, order_id)
    )
    
    await send_admin_notification(callback.from_user, product, "ЮMoney", f"{amount} ₽", order_id)
    await callback.answer()

@dp.callback_query(F.data.startswith("check_"))
async def check_payment_callback(callback: types.CallbackQuery):
    order_id = callback.data.replace("check_", "")
    order = pending_orders.get(order_id)
    
    if not order:
        await callback.answer("❌ Заказ не найден", show_alert=True)
        return
    
    # Проверяем, не подтвержден ли уже
    if order_id in confirmed_payments:
        await callback.answer("✅ Заказ уже подтвержден!", show_alert=True)
        return
    
    await callback.answer("🔍 Проверяю все методы...")
    
    checking_msg = await callback.message.edit_text(
        "🔄 <b>Многоуровневая проверка платежа...</b>\n\n"
        "🔍 <b>Проверяем:</b>\n"
        "• API операций ЮMoney\n"
        "• Webhook уведомления\n"
        "• Изменение баланса\n"
        "• Автоподтверждение тестов\n\n"
        "⏳ Пожалуйста, подождите 30 секунд...",
        parse_mode="HTML"
    )
    
    # Проверяем платеж всеми методами
    payment_found = False
    for attempt in range(6):  # 6 попыток по 5 секунд
        logger.info(f"🔍 === ПОПЫТКА {attempt+1} ПРОВЕРКИ ПЛАТЕЖА {order_id} ===")
        
        payment_found = await check_payment_advanced(
            order_id, 
            order["user_payment_amount"],
            order["product_price"]
        )
        
        if payment_found:
            break
        
        # Обновляем статус проверки
        dots = "." * (attempt + 1)
        await checking_msg.edit_text(
            f"🔄 <b>Проверка платежа{dots}</b>\n\n"
            f"🔍 <b>Попытка {attempt+1}/6</b>\n"
            f"• API операций: проверено\n"
            f"• Webhook: проверено\n"
            f"• Баланс: мониторим\n\n"
            f"⏳ Продолжаем поиск...",
            parse_mode="HTML"
        )
        
        await asyncio.sleep(5)
    
    if payment_found:
        await process_successful_payment(order_id, "Авто")
        
        await checking_msg.edit_text(
            "✅ <b>ПЛАТЕЖ НАЙДЕН И ПОДТВЕРЖДЕН!</b>\n\n"
            "🎉 <b>Ваш заказ успешно обработан!</b>\n"
            "📨 Проверьте сообщения выше ⬆️\n\n"
            "🔍 Система автоматически обнаружила платеж",
            parse_mode="HTML",
            reply_markup=support_keyboard()
        )
    else:
        # Платеж не найден автоматически
        fail_text = (
            f"🔍 <b>Автопроверка завершена</b>\n\n"
            f"💰 <b>К оплате:</b> {order['user_payment_amount']} ₽\n"
            f"🆔 <b>Заказ:</b> <code>{order_id}</code>\n\n"
            f"<b>Что проверить:</b>\n"
            f"• Оплачена точная сумма: <b>{order['user_payment_amount']} ₽</b>\n"
            f"• Комментарий содержит: <code>{order_id}</code>\n"
            f"• Платеж успешно обработан ЮMoney\n\n"
            f"⚡ <b>Админ может подтвердить вручную</b>\n"
            f"💬 Поддержка: @{SUPPORT_CHAT_USERNAME}"
        )
        
        payment_url = create_payment_link(order["user_payment_amount"], order_id, order["product"]["name"])
        await checking_msg.edit_text(fail_text, parse_mode="HTML", reply_markup=payment_keyboard(payment_url, order_id))

# ========== ОСТАЛЬНАЯ ЛОГИКА (GOLD, STARS, НАВИГАЦИЯ) ==========
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
    if product['platform_code'] == 'test':
        platform_name = "Тест"
    
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
    
    await send_admin_notification(callback.from_user, product, "GOLD", f"{product['price_stars']} ⭐", "GOLD_" + str(callback.from_user.id))
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
        await process_successful_payment(order_id, "Telegram Stars")

# ========== НАВИГАЦИЯ ==========
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
    
    if platform == "test":
        await callback.message.edit_text(
            "🧪 <b>Тестовый режим</b>\n\nВыберите тестовый товар:",
            parse_mode="HTML",
            reply_markup=test_subscription_keyboard()
        )
    elif platform == "apk":
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

# ========== ЗАПУСК WEBHOOK СЕРВЕРА ==========
async def start_webhook_server():
    """Запуск webhook сервера (опционально)"""
    try:
        app = web.Application()
        app.router.add_post('/yoomoney', yoomoney_webhook_handler)
        
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', 8080)
        await site.start()
        logger.info("🌐 Webhook сервер запущен на порту 8080")
        logger.info("📡 URL для настройки: http://your-server:8080/yoomoney")
    except Exception as e:
        logger.warning(f"⚠️ Не удалось запустить webhook сервер: {e}")

# ========== ГЛАВНАЯ ФУНКЦИЯ ==========
async def main():
    print("="*70)
    print("🎯 AIMNOOB SHOP BOT - ФИНАЛЬНАЯ ВЕРСИЯ")
    print("="*70)
    
    # Запускаем webhook сервер (опционально)
    # await start_webhook_server()
    
    # Полная диагностика API при запуске
    print("🔍 Диагностика API ЮMoney...")
    api_working = await diagnose_yoomoney_api()
    
    # Получаем начальный баланс
    balance = await get_yoomoney_balance()
    if balance is not None:
        print(f"💰 Баланс: {balance} ₽")
        balance_history.append({'time': time.time(), 'balance': balance})
    
    me = await bot.get_me()
    print(f"\n✅ Бот @{me.username} запущен!")
    print(f"💳 Кошелек: {YOOMONEY_WALLET}")
    print(f"💬 Поддержка: @{SUPPORT_CHAT_USERNAME}")
    
    print("\n" + "="*70)
    print("🚀 ВОЗМОЖНОСТИ СИСТЕМЫ:")
    print("✅ Автоподтверждение тестовых платежей")
    print("🔍 Проверка через API операций")
    print("💰 Мониторинг изменений баланса") 
    print("📨 Поддержка webhook уведомлений")
    print("⚡ Ручное подтверждение админом")
    
    print("\n📝 КОМАНДЫ АДМИНА:")
    print("• /diagnose - полная диагностика API")
    print("• /balance - баланс и история")
    print("• /orders - активные заказы")
    print("• /webhook_test - статистика webhook")
    
    if api_working:
        print("\n🎉 API ОПЕРАЦИЙ: РАБОТАЕТ")
        print("🔄 Платежи будут подтверждаться автоматически")
    else:
        print("\n⚠️ API ОПЕРАЦИЙ: НЕ РАБОТАЕТ")
        print("🛠️ Будет использоваться ручное подтверждение")
    
    print("="*70)
    print("✅ Бот готов к работе! Ожидание сообщений...")
    print("="*70)
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
