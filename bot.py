# bot.py
import logging
import asyncio
import aiohttp
import hashlib
import time
import random
import json
import os
from datetime import datetime, timedelta
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
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN", "8225924716:AAFZ_8Eu8aJ4BF7pErZY5Ef3emG9Cl9PikE")

admin_ids_str = os.getenv("ADMIN_ID", "8387532956,8354762345")
admin_ids_list = [int(x.strip()) for x in admin_ids_str.split(",") if x.strip().isdigit()]

if len(admin_ids_list) >= 2:
    ADMIN_ID = admin_ids_list[0]
    SUPPORT_CHAT_ID = admin_ids_list[1]
elif len(admin_ids_list) == 1:
    ADMIN_ID = admin_ids_list[0]
    SUPPORT_CHAT_ID = int(os.getenv("SUPPORT_CHAT_ID", "8354762345"))
else:
    ADMIN_ID = 8387532956
    SUPPORT_CHAT_ID = 8354762345

ADMIN_IDS = set(admin_ids_list) if admin_ids_list else {ADMIN_ID}

CRYPTOBOT_TOKEN = os.getenv("CRYPTOBOT_TOKEN", "493276:AAtS7R1zYy0gaPw8eax1EgiWo0tdnd6dQ9c")
YOOMONEY_ACCESS_TOKEN = os.getenv("YOOMONEY_ACCESS_TOKEN", "4100118889570559.3288B2E716CEEB922A26BD6BEAC58648FBFB680CCF64E4E1447D714D6FB5EA5F01F1478FAC686BEF394C8A186C98982DE563C1ABCDF9F2F61D971B61DA3C7E486CA818F98B9E0069F1C0891E090DD56A11319D626A40F0AE8302A8339DED9EB7969617F191D93275F64C4127A3ECB7AED33FCDE91CA68690EB7534C67E6C219E")
YOOMONEY_WALLET = os.getenv("YOOMONEY_WALLET", "4100118889570559")

SUPPORT_CHAT_USERNAME = os.getenv("SUPPORT_CHAT_USERNAME", "aimnoob_support")
SHOP_URL = os.getenv("SHOP_URL", "https://aimnoob.ru")
MINIAPP_URL = os.getenv("MINIAPP_URL", "https://aimnoob.bothost.ru")
WEB_PORT = int(os.getenv("PORT", "8080"))

# ========== ИНИЦИАЛИЗАЦИЯ ==========
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

pending_orders = {}
confirmed_payments = {}
balance_history = []

# ========== MINIAPP HTML ==========
MINIAPP_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
    <title>AimNoob | Premium Shop</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        :root {
            --primary: #7c3aed;
            --primary-dark: #5b21b6;
            --primary-light: #8b5cf6;
            --secondary: #ec489a;
            --accent: #f59e0b;
            --dark: #0f0f1a;
            --darker: #0a0a0f;
            --glass: rgba(15, 15, 26, 0.8);
            --glass-light: rgba(255, 255, 255, 0.1);
            --success: #10b981;
            --danger: #ef4444;
            --warning: #f59e0b;
        }

        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, var(--darker) 0%, var(--dark) 100%);
            min-height: 100vh;
            color: #fff;
            overflow-x: hidden;
        }

        .animated-bg {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            z-index: -1;
            overflow: hidden;
        }

        .animated-bg::before {
            content: '';
            position: absolute;
            width: 200%;
            height: 200%;
            background: radial-gradient(circle at 20% 50%, rgba(124, 58, 237, 0.3) 0%, transparent 50%),
                        radial-gradient(circle at 80% 80%, rgba(236, 72, 153, 0.3) 0%, transparent 50%);
            animation: bgMove 20s ease-in-out infinite;
        }

        @keyframes bgMove {
            0%, 100% { transform: translate(-10%, -10%) rotate(0deg); }
            50% { transform: translate(10%, 10%) rotate(5deg); }
        }

        .app {
            max-width: 500px;
            margin: 0 auto;
            padding: 20px;
            padding-bottom: 90px;
            position: relative;
            z-index: 1;
        }

        .header {
            text-align: center;
            padding: 20px 0 30px;
            animation: fadeInDown 0.6s cubic-bezier(0.68, -0.55, 0.265, 1.55);
        }

        .logo-wrapper {
            position: relative;
            display: inline-block;
        }

        .logo {
            width: 80px;
            height: 80px;
            background: linear-gradient(135deg, var(--primary), var(--secondary));
            border-radius: 25px;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0 auto 12px;
            font-size: 42px;
            box-shadow: 0 10px 30px rgba(124, 58, 237, 0.3);
            animation: float 3s ease-in-out infinite;
        }

        @keyframes float {
            0%, 100% { transform: translateY(0px); }
            50% { transform: translateY(-8px); }
        }

        h1 {
            font-size: 28px;
            font-weight: 800;
            background: linear-gradient(135deg, #fff, var(--primary-light));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 6px;
        }

        .subtitle {
            opacity: 0.7;
            font-size: 13px;
        }

        .platform-group {
            margin-bottom: 30px;
        }

        .platform-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 15px;
            padding: 0 8px;
        }

        .platform-title {
            font-size: 20px;
            font-weight: 700;
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .platform-badge {
            background: var(--glass-light);
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: normal;
        }

        .products-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 14px;
        }

        .product-card {
            background: var(--glass);
            backdrop-filter: blur(20px);
            border-radius: 20px;
            border: 1px solid var(--glass-light);
            overflow: hidden;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            cursor: pointer;
            position: relative;
        }

        .product-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 3px;
            background: linear-gradient(90deg, var(--primary), var(--secondary), var(--accent));
            transform: scaleX(0);
            transition: transform 0.3s;
        }

        .product-card:hover {
            transform: translateY(-4px);
            border-color: var(--primary);
            box-shadow: 0 10px 25px rgba(124, 58, 237, 0.2);
        }

        .product-card:hover::before {
            transform: scaleX(1);
        }

        .card-content {
            padding: 16px;
        }

        .popular-badge {
            position: absolute;
            top: 10px;
            right: 10px;
            background: linear-gradient(135deg, var(--accent), #ff6b6b);
            padding: 4px 8px;
            border-radius: 12px;
            font-size: 10px;
            font-weight: 700;
            z-index: 2;
            box-shadow: 0 2px 8px rgba(0,0,0,0.2);
        }

        .card-header {
            text-align: center;
            margin-bottom: 12px;
        }

        .product-icon {
            width: 50px;
            height: 50px;
            background: linear-gradient(135deg, rgba(124, 58, 237, 0.2), rgba(236, 72, 153, 0.2));
            border-radius: 16px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 28px;
            margin: 0 auto 10px;
        }

        .product-name {
            font-size: 16px;
            font-weight: 700;
            margin-bottom: 2px;
        }

        .product-platform {
            font-size: 10px;
            opacity: 0.6;
        }

        .price-section {
            text-align: center;
            margin: 12px 0;
        }

        .price-current {
            font-size: 22px;
            font-weight: 800;
            color: var(--accent);
        }

        .price-old {
            font-size: 12px;
            opacity: 0.5;
            text-decoration: line-through;
            margin-left: 6px;
        }

        .price-save {
            font-size: 10px;
            background: rgba(16, 185, 129, 0.2);
            color: var(--success);
            padding: 2px 6px;
            border-radius: 12px;
            display: inline-block;
            margin-top: 4px;
        }

        .duration-badge {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            background: var(--glass-light);
            padding: 4px 8px;
            border-radius: 16px;
            font-size: 10px;
            margin-bottom: 12px;
            justify-content: center;
        }

        .features-list {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin: 10px 0;
            justify-content: center;
        }

        .feature {
            font-size: 9px;
            background: rgba(255, 255, 255, 0.05);
            padding: 3px 8px;
            border-radius: 10px;
            display: flex;
            align-items: center;
            gap: 3px;
        }

        .buy-btn {
            width: 100%;
            margin-top: 12px;
            padding: 10px;
            background: linear-gradient(135deg, var(--primary), var(--secondary));
            border: none;
            border-radius: 12px;
            color: white;
            font-weight: 700;
            font-size: 14px;
            cursor: pointer;
            transition: all 0.3s;
            position: relative;
            overflow: hidden;
        }

        .buy-btn::before {
            content: '';
            position: absolute;
            top: 50%;
            left: 50%;
            width: 0;
            height: 0;
            border-radius: 50%;
            background: rgba(255, 255, 255, 0.3);
            transform: translate(-50%, -50%);
            transition: width 0.6s, height 0.6s;
        }

        .buy-btn:active::before {
            width: 200px;
            height: 200px;
        }

        .buy-btn:active {
            transform: scale(0.98);
        }

        .modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0, 0, 0, 0.95);
            backdrop-filter: blur(20px);
            z-index: 1000;
            align-items: center;
            justify-content: center;
            padding: 20px;
            animation: fadeIn 0.3s;
        }

        .modal.active {
            display: flex;
        }

        .modal-content {
            background: linear-gradient(135deg, var(--dark), var(--darker));
            border-radius: 28px;
            padding: 20px;
            max-width: 400px;
            width: 100%;
            max-height: 85vh;
            overflow-y: auto;
            border: 1px solid var(--glass-light);
            animation: slideUp 0.4s cubic-bezier(0.68, -0.55, 0.265, 1.55);
        }

        @keyframes slideUp {
            from {
                opacity: 0;
                transform: translateY(30px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }

        .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            padding-bottom: 12px;
            border-bottom: 1px solid var(--glass-light);
        }

        .modal-title {
            font-size: 20px;
            font-weight: 700;
            background: linear-gradient(135deg, #fff, var(--primary-light));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .close-modal {
            background: var(--glass-light);
            border: none;
            width: 32px;
            height: 32px;
            border-radius: 50%;
            font-size: 18px;
            color: #fff;
            cursor: pointer;
            transition: all 0.2s;
        }

        .close-modal:hover {
            background: var(--danger);
            transform: rotate(90deg);
        }

        .payment-methods {
            display: flex;
            flex-direction: column;
            gap: 10px;
            margin: 15px 0;
        }

        .payment-method-card {
            background: var(--glass-light);
            border-radius: 16px;
            padding: 12px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            cursor: pointer;
            transition: all 0.3s;
            border: 1px solid transparent;
        }

        .payment-method-card:hover {
            border-color: var(--primary);
            transform: translateX(4px);
            background: rgba(124, 58, 237, 0.1);
        }

        .payment-method-left {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .payment-icon {
            width: 44px;
            height: 44px;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 14px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 22px;
        }

        .payment-info h4 {
            font-size: 14px;
            margin-bottom: 2px;
        }

        .payment-info p {
            font-size: 10px;
            opacity: 0.6;
        }

        .payment-amount {
            font-size: 16px;
            font-weight: 700;
            color: var(--accent);
        }

        .pay-btn {
            width: 100%;
            padding: 14px;
            background: linear-gradient(135deg, var(--primary), var(--secondary));
            border: none;
            border-radius: 14px;
            color: white;
            font-weight: 700;
            font-size: 16px;
            cursor: pointer;
            margin-top: 10px;
            transition: all 0.3s;
        }

        .pay-btn:active {
            transform: scale(0.98);
        }

        .qr-container {
            text-align: center;
            margin: 15px 0;
        }

        .qr-code {
            background: white;
            padding: 12px;
            border-radius: 16px;
            display: inline-block;
        }

        .qr-code canvas {
            width: 160px;
            height: 160px;
        }

        .payment-status {
            text-align: center;
            padding: 20px;
        }

        .status-icon {
            font-size: 56px;
            margin-bottom: 12px;
        }

        .status-loading {
            animation: spin 1s linear infinite;
        }

        @keyframes spin {
            to { transform: rotate(360deg); }
        }

        .bottom-nav {
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            background: var(--glass);
            backdrop-filter: blur(20px);
            display: flex;
            justify-content: space-around;
            padding: 10px 20px;
            border-top: 1px solid var(--glass-light);
            z-index: 100;
        }

        .nav-item {
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 4px;
            background: none;
            border: none;
            color: rgba(255, 255, 255, 0.5);
            font-size: 11px;
            cursor: pointer;
            transition: all 0.3s;
            padding: 6px 12px;
            border-radius: 30px;
        }

        .nav-item.active {
            color: var(--accent);
            background: rgba(245, 158, 11, 0.1);
        }

        .nav-icon {
            font-size: 22px;
        }

        .toast {
            position: fixed;
            bottom: 90px;
            left: 20px;
            right: 20px;
            background: rgba(0, 0, 0, 0.95);
            backdrop-filter: blur(10px);
            padding: 12px 16px;
            border-radius: 14px;
            display: flex;
            align-items: center;
            gap: 10px;
            z-index: 1100;
            animation: slideUp 0.3s;
            border-left: 3px solid var(--success);
        }

        .toast.error {
            border-left-color: var(--danger);
        }

        @keyframes fadeIn {
            from { opacity: 0; }
            to { opacity: 1; }
        }

        @keyframes fadeInDown {
            from {
                opacity: 0;
                transform: translateY(-20px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }

        ::-webkit-scrollbar {
            width: 4px;
        }

        ::-webkit-scrollbar-track {
            background: rgba(255, 255, 255, 0.05);
        }

        ::-webkit-scrollbar-thumb {
            background: var(--primary);
            border-radius: 10px;
        }

        .license-key {
            background: rgba(0, 0, 0, 0.5);
            padding: 10px;
            border-radius: 10px;
            font-family: monospace;
            font-size: 11px;
            text-align: center;
            word-break: break-all;
            margin: 10px 0;
        }
    </style>
</head>
<body>
    <div class="animated-bg"></div>
    <div class="app">
        <div class="header">
            <div class="logo-wrapper">
                <div class="logo">&#127919;</div>
            </div>
            <h1>AimNoob</h1>
            <div class="subtitle">Премиум чит для Standoff 2</div>
        </div>

        <div id="content"></div>
    </div>

    <div class="bottom-nav">
        <button class="nav-item active" data-page="shop">
            <span class="nav-icon">&#128722;</span>
            <span>Магазин</span>
        </button>
        <button class="nav-item" data-page="orders">
            <span class="nav-icon">&#128273;</span>
            <span>Ключи</span>
        </button>
        <button class="nav-item" data-page="profile">
            <span class="nav-icon">&#128100;</span>
            <span>Профиль</span>
        </button>
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
    <script src="https://cdn.jsdelivr.net/npm/qrcodejs2-fix@0.0.1/qrcode.min.js"></script>
    <script>
        const tg = window.Telegram.WebApp;
        tg.expand();
        tg.enableClosingConfirmation();
        tg.MainButton.hide();

        const API_BASE = window.location.origin + '/api';

        const PRODUCTS = {
            android: [
                {
                    id: "apk_week",
                    name: "Android",
                    period: "Неделя",
                    duration: "7 дней",
                    price: 150,
                    price_stars: 350,
                    price_gold: 350,
                    price_nft: 250,
                    price_crypto_usdt: 2,
                    icon: "\\ud83d\\udcf1",
                    features: ["AimBot", "WallHack", "ESP"],
                    popular: false,
                    discount: 0
                },
                {
                    id: "apk_month",
                    name: "Android",
                    period: "Месяц",
                    duration: "30 дней",
                    price: 350,
                    price_stars: 800,
                    price_gold: 800,
                    price_nft: 600,
                    price_crypto_usdt: 5,
                    icon: "\\ud83d\\udcf1",
                    features: ["AimBot", "WallHack", "ESP", "Anti-Ban"],
                    popular: true,
                    discount: 15
                },
                {
                    id: "apk_forever",
                    name: "Android",
                    period: "Навсегда",
                    duration: "\\u221e",
                    price: 800,
                    price_stars: 1800,
                    price_gold: 1800,
                    price_nft: 1400,
                    price_crypto_usdt: 12,
                    icon: "\\ud83d\\udcf1",
                    features: ["AimBot", "WallHack", "ESP", "Anti-Ban", "Обновления"],
                    popular: false,
                    discount: 30
                }
            ],
            ios: [
                {
                    id: "ios_week",
                    name: "iOS",
                    period: "Неделя",
                    duration: "7 дней",
                    price: 300,
                    price_stars: 700,
                    price_gold: 700,
                    price_nft: 550,
                    price_crypto_usdt: 4,
                    icon: "\\ud83c\\udf4e",
                    features: ["AimBot", "WallHack", "ESP"],
                    popular: false,
                    discount: 0
                },
                {
                    id: "ios_month",
                    name: "iOS",
                    period: "Месяц",
                    duration: "30 дней",
                    price: 450,
                    price_stars: 1000,
                    price_gold: 1000,
                    price_nft: 800,
                    price_crypto_usdt: 6,
                    icon: "\\ud83c\\udf4e",
                    features: ["AimBot", "WallHack", "ESP", "Anti-Ban"],
                    popular: true,
                    discount: 10
                },
                {
                    id: "ios_forever",
                    name: "iOS",
                    period: "Навсегда",
                    duration: "\\u221e",
                    price: 850,
                    price_stars: 2000,
                    price_gold: 2000,
                    price_nft: 1600,
                    price_crypto_usdt: 12,
                    icon: "\\ud83c\\udf4e",
                    features: ["AimBot", "WallHack", "ESP", "Anti-Ban", "Обновления"],
                    popular: false,
                    discount: 25
                }
            ]
        };

        let currentUser = null;
        let selectedProduct = null;
        let userLicenses = [];

        document.addEventListener('DOMContentLoaded', () => {
            currentUser = tg.initDataUnsafe?.user || { id: Date.now(), first_name: 'Гость', username: 'user' };
            loadUserLicenses();
            renderShop();

            document.querySelectorAll('.nav-item').forEach(btn => {
                btn.addEventListener('click', () => switchPage(btn.dataset.page));
            });

            document.querySelector('.close-modal').addEventListener('click', closeModal);
            document.getElementById('modal').addEventListener('click', (e) => {
                if (e.target === document.getElementById('modal')) closeModal();
            });
        });

        function switchPage(page) {
            document.querySelectorAll('.nav-item').forEach(btn => {
                btn.classList.toggle('active', btn.dataset.page === page);
            });

            if (page === 'shop') renderShop();
            else if (page === 'orders') renderOrders();
            else if (page === 'profile') renderProfile();
        }

        function renderShop() {
            const content = document.getElementById('content');

            content.innerHTML = `
                <div class="platform-group">
                    <div class="platform-header">
                        <div class="platform-title">
                            <span>\\ud83d\\udcf1</span>
                            <span>Android</span>
                        </div>
                        <div class="platform-badge">3 тарифа</div>
                    </div>
                    <div class="products-grid">
                        ${PRODUCTS.android.map(product => renderProductCard(product)).join('')}
                    </div>
                </div>

                <div class="platform-group">
                    <div class="platform-header">
                        <div class="platform-title">
                            <span>\\ud83c\\udf4e</span>
                            <span>iOS</span>
                        </div>
                        <div class="platform-badge">3 тарифа</div>
                    </div>
                    <div class="products-grid">
                        ${PRODUCTS.ios.map(product => renderProductCard(product)).join('')}
                    </div>
                </div>
            `;

            document.querySelectorAll('.product-card').forEach(card => {
                card.addEventListener('click', (e) => {
                    if (!e.target.classList.contains('buy-btn')) {
                        const productId = card.dataset.productId;
                        const allProducts = [...PRODUCTS.android, ...PRODUCTS.ios];
                        const product = allProducts.find(p => p.id === productId);
                        if (product) showProductDetail(product);
                    }
                });

                const buyBtn = card.querySelector('.buy-btn');
                if (buyBtn) {
                    buyBtn.addEventListener('click', (e) => {
                        e.stopPropagation();
                        const productId = card.dataset.productId;
                        const allProducts = [...PRODUCTS.android, ...PRODUCTS.ios];
                        const product = allProducts.find(p => p.id === productId);
                        if (product) showPaymentModal(product);
                    });
                }
            });
        }

        function renderProductCard(product) {
            const oldPrice = product.discount ? Math.round(product.price * (1 + product.discount / 100)) : null;
            const dur = product.duration;
            const days = parseInt(dur);
            const pricePerDay = (!isNaN(days) && days > 0) ? (product.price / days).toFixed(0) : null;

            return `
                <div class="product-card" data-product-id="${product.id}">
                    ${product.popular ? '<div class="popular-badge">\\ud83d\\udd25 ХИТ</div>' : ''}
                    <div class="card-content">
                        <div class="card-header">
                            <div class="product-icon">${product.icon}</div>
                            <div class="product-name">${product.name}</div>
                            <div class="product-platform">${product.period}</div>
                        </div>

                        <div class="price-section">
                            <span class="price-current">${product.price} \\u20bd</span>
                            ${oldPrice ? '<span class="price-old">' + oldPrice + ' \\u20bd</span>' : ''}
                            ${product.discount ? '<div class="price-save">-' + product.discount + '%</div>' : ''}
                        </div>

                        ${pricePerDay ? '<div class="duration-badge">\\ud83d\\udcc5 ' + pricePerDay + ' \\u20bd/день</div>' : ''}

                        <div class="features-list">
                            ${product.features.map(f => '<span class="feature">\\u2728 ' + f + '</span>').join('')}
                        </div>

                        <button class="buy-btn">
                            ${product.popular ? '\\ud83d\\udd25 Купить' : '\\ud83d\\uded2 Купить'}
                        </button>
                    </div>
                </div>
            `;
        }

        function showProductDetail(product) {
            const modalBody = document.getElementById('modal-body');
            document.getElementById('modal-title').textContent = product.name + ' \\u2022 ' + product.period;

            const oldPrice = product.discount ? Math.round(product.price * (1 + product.discount / 100)) : null;

            modalBody.innerHTML = `
                <div style="text-align: center; margin-bottom: 20px;">
                    <div style="font-size: 56px; margin-bottom: 8px;">${product.icon}</div>
                    <div style="font-size: 20px; font-weight: 700;">${product.name}</div>
                    <div style="font-size: 14px; opacity: 0.7;">${product.period} \\u2022 ${product.duration}</div>
                </div>

                <div class="price-section" style="justify-content: center; margin-bottom: 20px;">
                    <span class="price-current" style="font-size: 32px;">${product.price} \\u20bd</span>
                    ${oldPrice ? '<span class="price-old">' + oldPrice + ' \\u20bd</span>' : ''}
                </div>

                <div class="features-list" style="justify-content: center; margin-bottom: 20px;">
                    ${product.features.map(f => '<span class="feature">\\u2728 ' + f + '</span>').join('')}
                </div>

                <button class="pay-btn" onclick="showPaymentModalFromDetail('${product.id}')">
                    \\ud83d\\udcb3 Перейти к оплате
                </button>
            `;

            openModal();
        }

        function showPaymentModalFromDetail(productId) {
            const allProducts = [...PRODUCTS.android, ...PRODUCTS.ios];
            const product = allProducts.find(p => p.id === productId);
            if (product) {
                closeModal();
                setTimeout(() => showPaymentModal(product), 100);
            }
        }

        function showPaymentModal(product) {
            selectedProduct = product;

            const modalBody = document.getElementById('modal-body');
            document.getElementById('modal-title').textContent = 'Способ оплаты';

            modalBody.innerHTML = `
                <div style="text-align: center; margin-bottom: 16px;">
                    <div style="font-size: 40px;">${product.icon}</div>
                    <div style="font-size: 16px; font-weight: 600;">${product.name} \\u2022 ${product.period}</div>
                    <div style="font-size: 20px; font-weight: 700; color: var(--accent); margin-top: 5px;">${product.price} \\u20bd</div>
                </div>

                <div class="payment-methods">
                    <div class="payment-method-card" data-method="yoomoney">
                        <div class="payment-method-left">
                            <div class="payment-icon">\\ud83d\\udcb3</div>
                            <div class="payment-info">
                                <h4>Картой</h4>
                                <p>Карты, СБП, Apple Pay</p>
                            </div>
                        </div>
                        <div class="payment-amount">${product.price} \\u20bd</div>
                    </div>

                    <div class="payment-method-card" data-method="stars">
                        <div class="payment-method-left">
                            <div class="payment-icon">\\u2b50</div>
                            <div class="payment-info">
                                <h4>Telegram Stars</h4>
                                <p>Встроенные платежи</p>
                            </div>
                        </div>
                        <div class="payment-amount">${product.price_stars} \\u2b50</div>
                    </div>

                    <div class="payment-method-card" data-method="crypto">
                        <div class="payment-method-left">
                            <div class="payment-icon">\\u20bf</div>
                            <div class="payment-info">
                                <h4>Криптовалюта</h4>
                                <p>USDT, BTC, ETH, TON</p>
                            </div>
                        </div>
                        <div class="payment-amount">${product.price_crypto_usdt} USDT</div>
                    </div>

                    <div class="payment-method-card" data-method="gold">
                        <div class="payment-method-left">
                            <div class="payment-icon">\\ud83d\\udcb0</div>
                            <div class="payment-info">
                                <h4>GOLD</h4>
                                <p>Игровая валюта</p>
                            </div>
                        </div>
                        <div class="payment-amount">${product.price_gold} \\ud83e\\ude99</div>
                    </div>

                    <div class="payment-method-card" data-method="nft">
                        <div class="payment-method-left">
                            <div class="payment-icon">\\ud83c\\udfa8</div>
                            <div class="payment-info">
                                <h4>NFT</h4>
                                <p>Коллекционные токены</p>
                            </div>
                        </div>
                        <div class="payment-amount">${product.price_nft} \\ud83d\\uddbc</div>
                    </div>
                </div>
            `;

            document.querySelectorAll('.payment-method-card').forEach(card => {
                card.addEventListener('click', () => {
                    const method = card.dataset.method;
                    processPayment(method);
                });
            });

            openModal();
        }

        async function processPayment(method) {
            const modalBody = document.getElementById('modal-body');

            modalBody.innerHTML = `
                <div class="payment-status">
                    <div class="status-icon status-loading">\\u23f3</div>
                    <h3>Создание платежа...</h3>
                    <p style="opacity: 0.7; margin-top: 8px;">Пожалуйста, подождите</p>
                </div>
            `;

            try {
                const response = await fetch(API_BASE + '/create_payment', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        product_id: selectedProduct.id,
                        method: method,
                        user_id: currentUser.id,
                        user_name: currentUser.first_name + ' ' + (currentUser.last_name || ''),
                        init_data: tg.initData
                    })
                });

                const result = await response.json();

                if (result.success) {
                    if (method === 'yoomoney') {
                        showYooMoneyPayment(result.payment_url, result.order_id);
                    } else if (method === 'stars') {
                        showStarsPayment(result.order_id);
                    } else if (method === 'crypto') {
                        showCryptoPayment(result.payment_url, result.invoice_id, result.order_id);
                    } else {
                        showManualPayment(method, result.order_id);
                    }
                } else {
                    throw new Error(result.error || 'Ошибка создания платежа');
                }
            } catch (error) {
                showToast(error.message, 'error');
                setTimeout(() => showPaymentModal(selectedProduct), 1500);
            }
        }

        function showYooMoneyPayment(paymentUrl, orderId) {
            const modalBody = document.getElementById('modal-body');
            document.getElementById('modal-title').textContent = 'Оплата картой';

            modalBody.innerHTML = `
                <div class="payment-status">
                    <div class="status-icon">\\ud83d\\udcb3</div>
                    <h3>${selectedProduct.price} \\u20bd</h3>
                    <p style="opacity: 0.7; margin: 8px 0;">Заказ #${orderId.slice(-8)}</p>
                    <button class="pay-btn" onclick="window.open('${paymentUrl}', '_blank')">
                        \\ud83d\\udd17 Оплатить картой
                    </button>
                    <button class="pay-btn" style="background: var(--glass-light); margin-top: 8px;" onclick="checkPayment('${orderId}')">
                        \\u2705 Проверить оплату
                    </button>
                </div>
            `;
        }

        function showStarsPayment(orderId) {
            const modalBody = document.getElementById('modal-body');
            document.getElementById('modal-title').textContent = 'Оплата Stars';

            modalBody.innerHTML = `
                <div class="payment-status">
                    <div class="status-icon">\\u2b50</div>
                    <h3>${selectedProduct.price_stars} Stars</h3>
                    <p style="opacity: 0.7; margin: 8px 0;">Перейдите в бота для оплаты Stars</p>
                    <button class="pay-btn" onclick="tg.openTelegramLink('https://t.me/aimnoob_bot?start=buy_stars_${selectedProduct.id}')">
                        \\u2b50 Оплатить в боте
                    </button>
                </div>
            `;
        }

        function showCryptoPayment(paymentUrl, invoiceId, orderId) {
            const modalBody = document.getElementById('modal-body');
            document.getElementById('modal-title').textContent = 'Криптооплата';

            modalBody.innerHTML = `
                <div class="payment-status">
                    <div class="status-icon">\\u20bf</div>
                    <h3>${selectedProduct.price_crypto_usdt} USDT</h3>
                    <div class="qr-container" id="qr-code"></div>
                    <button class="pay-btn" onclick="window.open('${paymentUrl}', '_blank')">
                        \\ud83d\\udd17 Оплатить криптой
                    </button>
                    <button class="pay-btn" style="background: var(--glass-light); margin-top: 8px;" onclick="checkCryptoPayment('${invoiceId}', '${orderId}')">
                        \\u2705 Проверить оплату
                    </button>
                </div>
            `;

            setTimeout(() => {
                if (typeof QRCode !== 'undefined' && document.getElementById('qr-code')) {
                    new QRCode(document.getElementById('qr-code'), {
                        text: paymentUrl,
                        width: 140,
                        height: 140,
                        colorDark: '#000000',
                        colorLight: '#ffffff',
                        correctLevel: QRCode.CorrectLevel.H
                    });
                }
            }, 100);
        }

        function showManualPayment(method, orderId) {
            const methodNames = { gold: 'GOLD', nft: 'NFT' };
            const amounts = { gold: selectedProduct.price_gold, nft: selectedProduct.price_nft };
            const icons = { gold: '\\ud83d\\udcb0', nft: '\\ud83c\\udfa8' };

            const modalBody = document.getElementById('modal-body');
            document.getElementById('modal-title').textContent = 'Оплата ' + methodNames[method];

            const message = 'Привет! Хочу купить чит на Standoff 2. Версия 0.37.1, подписка на ' + selectedProduct.period + ' (' + selectedProduct.name + '). Готов купить за ' + amounts[method] + ' ' + methodNames[method];
            const encodedMessage = encodeURIComponent(message);

            modalBody.innerHTML = `
                <div class="payment-status">
                    <div class="status-icon">${icons[method]}</div>
                    <h3>${amounts[method]} ${methodNames[method]}</h3>
                    <div style="background: var(--glass-light); padding: 10px; border-radius: 12px; margin: 10px 0; font-size: 11px; word-break: break-all;">
                        ${message}
                    </div>
                    <button class="pay-btn" onclick="window.open('https://t.me/aimnoob_support?text=${encodedMessage}', '_blank')">
                        \\ud83d\\udcac Написать в поддержку
                    </button>
                    <button class="pay-btn" style="background: var(--glass-light); margin-top: 8px;" onclick="closeModal(); showToast('Заказ создан! Ожидайте подтверждения.', 'success')">
                        \\u2705 Я написал
                    </button>
                </div>
            `;
        }

        async function checkPayment(orderId) {
            const modalBody = document.getElementById('modal-body');
            modalBody.innerHTML = `
                <div class="payment-status">
                    <div class="status-icon status-loading">\\u23f3</div>
                    <h3>Проверка платежа...</h3>
                </div>
            `;

            try {
                const response = await fetch(API_BASE + '/check_payment', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ order_id: orderId })
                });

                const result = await response.json();

                if (result.paid) {
                    modalBody.innerHTML = `
                        <div class="payment-status">
                            <div class="status-icon">\\u2705</div>
                            <h3>Платеж подтвержден!</h3>
                            <div class="license-key">
                                \\ud83d\\udd11 ${result.license_key}
                            </div>
                            <button class="pay-btn" onclick="closeModal(); switchPage('orders'); showToast('Ключ сохранен!', 'success')">
                                \\ud83d\\udccb Перейти к ключам
                            </button>
                        </div>
                    `;
                    userLicenses.push({
                        key: result.license_key,
                        product: selectedProduct.name + ' \\u2022 ' + selectedProduct.period,
                        date: new Date().toISOString()
                    });
                    saveUserLicenses();
                } else {
                    modalBody.innerHTML = `
                        <div class="payment-status">
                            <div class="status-icon">\\u23f3</div>
                            <h3>Платеж не найден</h3>
                            <p style="opacity: 0.7; margin: 8px 0;">Попробуйте через 1-2 минуты</p>
                            <button class="pay-btn" onclick="checkPayment('${orderId}')">
                                \\ud83d\\udd04 Проверить еще раз
                            </button>
                        </div>
                    `;
                }
            } catch (error) {
                showToast('Ошибка проверки', 'error');
            }
        }

        async function checkCryptoPayment(invoiceId, orderId) {
            const modalBody = document.getElementById('modal-body');
            modalBody.innerHTML = `
                <div class="payment-status">
                    <div class="status-icon status-loading">\\u23f3</div>
                    <h3>Проверка криптоплатежа...</h3>
                </div>
            `;

            try {
                const response = await fetch(API_BASE + '/check_crypto', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ invoice_id: invoiceId, order_id: orderId })
                });

                const result = await response.json();

                if (result.paid) {
                    modalBody.innerHTML = `
                        <div class="payment-status">
                            <div class="status-icon">\\u2705</div>
                            <h3>Криптоплатеж подтвержден!</h3>
                            <div class="license-key">
                                \\ud83d\\udd11 ${result.license_key}
                            </div>
                            <button class="pay-btn" onclick="closeModal(); switchPage('orders')">
                                \\ud83d\\udccb Перейти к ключам
                            </button>
                        </div>
                    `;
                    userLicenses.push({
                        key: result.license_key,
                        product: selectedProduct.name + ' \\u2022 ' + selectedProduct.period,
                        date: new Date().toISOString()
                    });
                    saveUserLicenses();
                } else {
                    modalBody.innerHTML = `
                        <div class="payment-status">
                            <div class="status-icon">\\u23f3</div>
                            <h3>Платеж в обработке</h3>
                            <button class="pay-btn" onclick="checkCryptoPayment('${invoiceId}', '${orderId}')">
                                \\ud83d\\udd04 Проверить снова
                            </button>
                        </div>
                    `;
                }
            } catch (error) {
                showToast('Ошибка проверки', 'error');
            }
        }

        function renderOrders() {
            const content = document.getElementById('content');

            if (userLicenses.length === 0) {
                content.innerHTML = `
                    <div style="text-align: center; padding: 50px 20px;">
                        <div style="font-size: 56px; margin-bottom: 16px;">\\ud83d\\udd11</div>
                        <div style="font-size: 18px; font-weight: 600; margin-bottom: 8px;">Нет активных ключей</div>
                        <div style="opacity: 0.7; margin-bottom: 20px;">Приобретите подписку, чтобы получить ключ</div>
                        <button class="pay-btn" onclick="switchPage('shop')">\\ud83d\\uded2 Перейти в магазин</button>
                    </div>
                `;
                return;
            }

            content.innerHTML = `
                <div class="platform-group">
                    <div class="platform-header">
                        <div class="platform-title">
                            <span>\\ud83d\\udd11</span>
                            <span>Мои лицензии</span>
                        </div>
                        <div class="platform-badge">${userLicenses.length} шт</div>
                    </div>
                    <div class="products-grid">
                        ${userLicenses.map(license => `
                            <div class="product-card">
                                <div class="card-content">
                                    <div class="card-header">
                                        <div class="product-icon">\\ud83c\\udfaf</div>
                                        <div class="product-name">${license.product}</div>
                                        <div class="product-platform">${new Date(license.date).toLocaleDateString('ru-RU')}</div>
                                    </div>
                                    <div class="license-key">
                                        ${license.key}
                                    </div>
                                    <button class="buy-btn" onclick="copyToClipboard('${license.key}')">
                                        \\ud83d\\udccb Скопировать
                                    </button>
                                </div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            `;
        }

        function renderProfile() {
            const content = document.getElementById('content');

            content.innerHTML = `
                <div class="platform-group">
                    <div class="platform-header">
                        <div class="platform-title">
                            <span>\\ud83d\\udc64</span>
                            <span>Профиль</span>
                        </div>
                    </div>
                    <div style="display: grid; grid-template-columns: 1fr; gap: 14px;">
                        <div class="product-card">
                            <div class="card-content" style="text-align: center;">
                                <div class="product-icon" style="margin: 0 auto 12px;">${getAvatarEmoji()}</div>
                                <div class="product-name">${currentUser.first_name} ${currentUser.last_name || ''}</div>
                                <div class="product-platform">@${currentUser.username || 'username'}</div>
                                <div style="margin: 15px 0; padding: 12px; background: var(--glass-light); border-radius: 14px;">
                                    <div style="display: flex; justify-content: space-between; margin-bottom: 8px;">
                                        <span>Активных ключей:</span>
                                        <span style="font-weight: 700;">${userLicenses.length}</span>
                                    </div>
                                    <div style="display: flex; justify-content: space-between;">
                                        <span>Всего покупок:</span>
                                        <span style="font-weight: 700;">${userLicenses.length}</span>
                                    </div>
                                </div>
                                <button class="pay-btn" onclick="window.open('https://t.me/aimnoob_support', '_blank')">
                                    \\ud83d\\udcac Поддержка
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            `;
        }

        function getAvatarEmoji() {
            const emojis = ['\\ud83c\\udfaf', '\\ud83d\\udd25', '\\u26a1', '\\ud83d\\udc8e', '\\ud83c\\udf1f', '\\ud83c\\udfae', '\\ud83d\\ude80', '\\ud83d\\udcaa'];
            return emojis[Math.abs(currentUser.id) % emojis.length];
        }

        function copyToClipboard(text) {
            navigator.clipboard.writeText(text);
            showToast('Ключ скопирован!', 'success');
        }

        function loadUserLicenses() {
            const saved = localStorage.getItem('aimnoob_licenses');
            if (saved) {
                userLicenses = JSON.parse(saved);
            }
        }

        function saveUserLicenses() {
            localStorage.setItem('aimnoob_licenses', JSON.stringify(userLicenses));
        }

        function showToast(message, type) {
            type = type || 'success';
            const toast = document.createElement('div');
            toast.className = 'toast ' + type;
            toast.innerHTML = '<span>' + (type === 'success' ? '\\u2705' : '\\u274c') + '</span><span>' + message + '</span>';
            document.body.appendChild(toast);
            setTimeout(() => toast.remove(), 3000);
        }

        function openModal() {
            document.getElementById('modal').classList.add('active');
        }

        function closeModal() {
            document.getElementById('modal').classList.remove('active');
        }

        window.checkPayment = checkPayment;
        window.checkCryptoPayment = checkCryptoPayment;
        window.showPaymentModalFromDetail = showPaymentModalFromDetail;
        window.copyToClipboard = copyToClipboard;
        window.switchPage = switchPage;
        window.closeModal = closeModal;
        window.showToast = showToast;

        tg.ready();
    </script>
</body>
</html>"""

# ========== ПРОДУКТЫ ==========
PRODUCTS = {
    "apk_week": {
        "name": "\U0001f4f1 AimNoob Android",
        "period_text": "НЕДЕЛЮ",
        "price": 150,
        "price_stars": 350,
        "price_gold": 350,
        "price_nft": 250,
        "price_crypto_usdt": 2,
        "platform": "Android",
        "period": "НЕДЕЛЮ",
        "platform_code": "apk",
        "emoji": "\U0001f4f1",
        "duration": "7 дней"
    },
    "apk_month": {
        "name": "\U0001f4f1 AimNoob Android",
        "period_text": "МЕСЯЦ",
        "price": 350,
        "price_stars": 800,
        "price_gold": 800,
        "price_nft": 600,
        "price_crypto_usdt": 5,
        "platform": "Android",
        "period": "МЕСЯЦ",
        "platform_code": "apk",
        "emoji": "\U0001f4f1",
        "duration": "30 дней"
    },
    "apk_forever": {
        "name": "\U0001f4f1 AimNoob Android",
        "period_text": "НАВСЕГДА",
        "price": 800,
        "price_stars": 1800,
        "price_gold": 1800,
        "price_nft": 1400,
        "price_crypto_usdt": 12,
        "platform": "Android",
        "period": "НАВСЕГДА",
        "platform_code": "apk",
        "emoji": "\U0001f4f1",
        "duration": "Навсегда"
    },
    "ios_week": {
        "name": "\U0001f34e AimNoob iOS",
        "period_text": "НЕДЕЛЮ",
        "price": 300,
        "price_stars": 700,
        "price_gold": 700,
        "price_nft": 550,
        "price_crypto_usdt": 4,
        "platform": "iOS",
        "period": "НЕДЕЛЮ",
        "platform_code": "ios",
        "emoji": "\U0001f34e",
        "duration": "7 дней"
    },
    "ios_month": {
        "name": "\U0001f34e AimNoob iOS",
        "period_text": "МЕСЯЦ",
        "price": 450,
        "price_stars": 1000,
        "price_gold": 1000,
        "price_nft": 800,
        "price_crypto_usdt": 6,
        "platform": "iOS",
        "period": "МЕСЯЦ",
        "platform_code": "ios",
        "emoji": "\U0001f34e",
        "duration": "30 дней"
    },
    "ios_forever": {
        "name": "\U0001f34e AimNoob iOS",
        "period_text": "НАВСЕГДА",
        "price": 850,
        "price_stars": 2000,
        "price_gold": 2000,
        "price_nft": 1600,
        "price_crypto_usdt": 12,
        "platform": "iOS",
        "period": "НАВСЕГДА",
        "platform_code": "ios",
        "emoji": "\U0001f34e",
        "duration": "Навсегда"
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
        [InlineKeyboardButton(text="\U0001f4f1 Android", callback_data="platform_apk")],
        [InlineKeyboardButton(text="\U0001f34e iOS", callback_data="platform_ios")],
        [InlineKeyboardButton(text="\U0001f3ae Открыть магазин", web_app=WebAppInfo(url=MINIAPP_URL))],
        [InlineKeyboardButton(text="\u2139\ufe0f О программе", callback_data="about")],
        [InlineKeyboardButton(text="\U0001f4ac Поддержка", url=f"https://t.me/{SUPPORT_CHAT_USERNAME}")]
    ])

def apk_subscription_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\u26a1 НЕДЕЛЯ \u2014 150\u20bd", callback_data="sub_apk_week")],
        [InlineKeyboardButton(text="\U0001f525 МЕСЯЦ \u2014 350\u20bd", callback_data="sub_apk_month")],
        [InlineKeyboardButton(text="\U0001f48e НАВСЕГДА \u2014 800\u20bd", callback_data="sub_apk_forever")],
        [InlineKeyboardButton(text="\u25c0\ufe0f Назад", callback_data="back_to_platform")]
    ])

def ios_subscription_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\u26a1 НЕДЕЛЯ \u2014 300\u20bd", callback_data="sub_ios_week")],
        [InlineKeyboardButton(text="\U0001f525 МЕСЯЦ \u2014 450\u20bd", callback_data="sub_ios_month")],
        [InlineKeyboardButton(text="\U0001f48e НАВСЕГДА \u2014 850\u20bd", callback_data="sub_ios_forever")],
        [InlineKeyboardButton(text="\u25c0\ufe0f Назад", callback_data="back_to_platform")]
    ])

def payment_methods_keyboard(product):
    buttons = [
        [InlineKeyboardButton(text="\U0001f4b3 Картой", callback_data=f"pay_yoomoney_{product['platform_code']}_{product['period']}")],
        [InlineKeyboardButton(text="\u2b50 Telegram Stars", callback_data=f"pay_stars_{product['platform_code']}_{product['period']}")],
        [InlineKeyboardButton(text="\u20bf Криптобот", callback_data=f"pay_crypto_{product['platform_code']}_{product['period']}")],
        [InlineKeyboardButton(text="\U0001f4b0 GOLD", callback_data=f"pay_gold_{product['platform_code']}_{product['period']}")],
        [InlineKeyboardButton(text="\U0001f3a8 NFT", callback_data=f"pay_nft_{product['platform_code']}_{product['period']}")],
        [InlineKeyboardButton(text="\u25c0\ufe0f Назад", callback_data="back_to_subscription")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def payment_keyboard(payment_url, order_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\U0001f4b3 Оплатить картой", url=payment_url)],
        [InlineKeyboardButton(text="\u2705 Проверить оплату", callback_data=f"checkym_{order_id}")],
        [InlineKeyboardButton(text="\u274c Отмена", callback_data="restart")]
    ])

def crypto_payment_keyboard(invoice_url, order_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\u20bf Оплатить криптой", url=invoice_url)],
        [InlineKeyboardButton(text="\u2705 Проверить платеж", callback_data=f"checkcr_{order_id}")],
        [InlineKeyboardButton(text="\u274c Отмена", callback_data="restart")]
    ])

def support_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\U0001f4ac Поддержка", url=f"https://t.me/{SUPPORT_CHAT_USERNAME}")],
        [InlineKeyboardButton(text="\U0001f310 Сайт", url=SHOP_URL)],
        [InlineKeyboardButton(text="\U0001f504 Новая покупка", callback_data="restart")]
    ])

def about_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\u25c0\ufe0f Назад", callback_data="back_to_platform")]
    ])

def admin_confirm_keyboard(order_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\u2705 Подтвердить", callback_data=f"admin_confirm_{order_id}")],
        [InlineKeyboardButton(text="\u274c Отклонить", callback_data=f"admin_reject_{order_id}")]
    ])

# ========== ФУНКЦИИ ==========
def generate_order_id():
    return hashlib.md5(f"{time.time()}_{random.randint(1000, 9999)}".encode()).hexdigest()[:12]

def create_payment_link(amount, order_id, product_name):
    comment = f"Заказ {order_id}: {product_name}"
    params = (
        f"receiver={YOOMONEY_WALLET}"
        f"&quickpay-form=shop"
        f"&targets={comment}"
        f"&sum={amount}"
        f"&label={order_id}"
        f"&successURL=https://t.me/aimnoob_bot?start=success"
        f"&paymentType=AC"
    )
    # Ручная кодировка без + вместо пробелов
    import urllib.parse
    safe_targets = urllib.parse.quote(comment, safe='')
    return (
        f"https://yoomoney.ru/quickpay/confirm.xml"
        f"?receiver={YOOMONEY_WALLET}"
        f"&quickpay-form=shop"
        f"&targets={safe_targets}"
        f"&sum={amount}"
        f"&label={order_id}"
        f"&successURL=https%3A%2F%2Ft.me%2Faimnoob_bot%3Fstart%3Dsuccess"
        f"&paymentType=AC"
    )

def generate_license_key(order_id, user_id):
    return f"AIMNOOB-{order_id[:8]}-{user_id % 10000}"

def is_admin(user_id):
    return user_id in ADMIN_IDS

def find_product(platform_code, period):
    for p in PRODUCTS.values():
        if p['platform_code'] == platform_code and p['period'] == period:
            return p
    return None

# ========== КРИПТОБОТ API ==========
async def create_crypto_invoice(amount_usdt, order_id, description):
    if not CRYPTOBOT_TOKEN:
        return None

    url = "https://pay.crypt.bot/api/createInvoice"
    headers = {
        "Crypto-Pay-API-Token": CRYPTOBOT_TOKEN,
        "Content-Type": "application/json"
    }
    data = {
        "asset": "USDT",
        "amount": str(amount_usdt),
        "description": description,
        "payload": order_id,
        "paid_btn_name": "callback",
        "paid_btn_url": f"https://t.me/aimnoob_bot?start=paid_{order_id}"
    }

    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=data) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if result.get("ok"):
                        inv = result["result"]
                        return {
                            "invoice_id": inv.get("invoice_id"),
                            "pay_url": inv.get("pay_url"),
                            "amount": inv.get("amount")
                        }
                else:
                    body = await resp.text()
                    logger.error(f"CryptoBot createInvoice {resp.status}: {body}")
    except Exception as e:
        logger.error(f"CryptoBot API error: {e}")
    return None

async def check_crypto_invoice(invoice_id):
    if not CRYPTOBOT_TOKEN:
        return False

    url = "https://pay.crypt.bot/api/getInvoices"
    headers = {
        "Crypto-Pay-API-Token": CRYPTOBOT_TOKEN,
        "Content-Type": "application/json"
    }
    data = {"invoice_ids": [invoice_id]}

    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=data) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if result.get("ok"):
                        items = result.get("result", {}).get("items", [])
                        if items:
                            return items[0].get("status") == "paid"
    except Exception as e:
        logger.error(f"CryptoBot check error: {e}")
    return False

# ========== ЮMONEY ФУНКЦИИ ==========
async def get_yoomoney_balance():
    if not YOOMONEY_ACCESS_TOKEN:
        return None

    headers = {"Authorization": f"Bearer {YOOMONEY_ACCESS_TOKEN}"}
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get("https://yoomoney.ru/api/account-info", headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return float(data.get('balance', 0))
                else:
                    body = await resp.text()
                    logger.error(f"YooMoney account-info {resp.status}: {body}")
    except Exception as e:
        logger.error(f"YooMoney balance error: {e}")
    return None

async def check_yoomoney_payment(order_id, expected_amount):
    if not YOOMONEY_ACCESS_TOKEN:
        logger.warning("YOOMONEY_ACCESS_TOKEN not set")
        return False

    headers = {"Authorization": f"Bearer {YOOMONEY_ACCESS_TOKEN}"}
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
                logger.info(f"YooMoney: {len(operations)} ops, looking for label={order_id}, amount={expected_amount}")

                for op in operations:
                    if op.get("label") == order_id and op.get("status") == "success":
                        if abs(float(op.get("amount", 0)) - expected_amount) <= 5:
                            logger.info(f"Found payment by label: {op}")
                            return True

                order_data = pending_orders.get(order_id)
                order_time = order_data.get("created_at", time.time()) if order_data else time.time()

                for op in operations:
                    if op.get("status") != "success":
                        continue
                    op_amount = float(op.get("amount", 0))
                    if abs(op_amount - expected_amount) > 2:
                        continue
                    try:
                        dt_str = op.get("datetime", "")
                        op_time = datetime.fromisoformat(dt_str.replace("Z", "+00:00")).timestamp()
                        if abs(op_time - order_time) <= 1800:
                            logger.info(f"Found payment by amount+time: {op}")
                            return True
                    except Exception:
                        pass

    except Exception as e:
        logger.error(f"YooMoney check error: {e}")
    return False

# ========== ОБРАБОТКА УСПЕШНОГО ПЛАТЕЖА ==========
async def process_successful_payment(order_id, source="API"):
    order = pending_orders.get(order_id)
    if not order or order_id in confirmed_payments:
        return False

    product = order["product"]
    user_id = order["user_id"]
    license_key = generate_license_key(order_id, user_id)

    confirmed_payments[order_id] = {
        **order,
        'confirmed_at': time.time(),
        'confirmed_by': source,
        'license_key': license_key
    }

    success_text = (
        f"\U0001f389 <b>Оплата подтверждена!</b>\n\n"
        f"\u2728 Добро пожаловать в AimNoob!\n\n"
        f"\U0001f4e6 <b>Ваша покупка:</b>\n"
        f"{product['emoji']} {product['name']}\n"
        f"\u23f1\ufe0f Срок: {product['duration']}\n"
        f"\U0001f50d Метод: {source}\n\n"
        f"\U0001f511 <b>Ваш лицензионный ключ:</b>\n"
        f"<code>{license_key}</code>\n\n"
        f"\U0001f4e5 <b>Скачивание:</b>\n"
        f"\U0001f517 {SHOP_URL}/download/{product['platform_code']}_{user_id}\n\n"
        f"\U0001f4ab <b>Активация:</b>\n"
        f"1\ufe0f\u20e3 Скачайте файл по ссылке\n"
        f"2\ufe0f\u20e3 Введите ключ при запуске\n"
        f"3\ufe0f\u20e3 Наслаждайтесь игрой! \U0001f3ae\n\n"
        f"\U0001f4ac Поддержка: @{SUPPORT_CHAT_USERNAME}"
    )

    try:
        await bot.send_message(user_id, success_text, parse_mode="HTML", reply_markup=support_keyboard())
    except Exception as e:
        logger.error(f"Error sending to user: {e}")

    admin_text = (
        f"\U0001f48e <b>НОВАЯ ПРОДАЖА ({source})</b>\n\n"
        f"\U0001f464 {order['user_name']}\n"
        f"\U0001f194 {user_id}\n"
        f"\U0001f4e6 {product['name']} ({product['duration']})\n"
        f"\U0001f4b0 {order.get('amount', product['price'])} {order.get('currency', '\u20bd')}\n"
        f"\U0001f511 <code>{license_key}</code>\n"
        f"\U0001f4c5 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(aid, admin_text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error notifying admin {aid}: {e}")

    pending_orders.pop(order_id, None)
    return True

async def send_admin_notification(user, product, payment_method, price, order_id):
    message = (
        f"\U0001f514 <b>НОВЫЙ ЗАКАЗ</b>\n\n"
        f"\U0001f464 {user.full_name}\n"
        f"\U0001f194 <code>{user.id}</code>\n"
        f"\U0001f4e6 {product['name']} ({product['duration']})\n"
        f"\U0001f4b0 {price}\n"
        f"\U0001f4b3 {payment_method}\n"
        f"\U0001f194 <code>{order_id}</code>\n\n"
        f"\U0001f4c5 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(aid, message, parse_mode="HTML", reply_markup=admin_confirm_keyboard(order_id))
        except Exception as e:
            logger.error(f"Error sending to admin {aid}: {e}")

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()

    # Обработка deep link для Stars оплаты из MiniApp
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("buy_stars_"):
        product_id = args[1].replace("buy_stars_", "")
        product = PRODUCTS.get(product_id)
        if product:
            order_id = generate_order_id()
            pending_orders[order_id] = {
                "user_id": message.from_user.id,
                "user_name": message.from_user.full_name,
                "product": product,
                "amount": product['price_stars'],
                "currency": "\u2b50",
                "payment_method": "Telegram Stars",
                "status": "pending",
                "created_at": time.time()
            }
            await bot.send_invoice(
                chat_id=message.from_user.id,
                title=f"AimNoob \u2014 {product['name']}",
                description=f"Подписка на {product['duration']} для {product['platform']}",
                payload=f"stars_{order_id}",
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice(label="XTR", amount=product['price_stars'])],
                start_parameter="aimnoob_payment"
            )
            return

    balance = await get_yoomoney_balance()
    if balance is not None:
        balance_history.append({'time': time.time(), 'balance': balance})

    text = (
        "\U0001f3af <b>AimNoob \u2014 Премиум чит для Standoff 2</b>\n\n"
        "\u2728 <b>Возможности:</b>\n"
        "\U0001f6e1\ufe0f Продвинутая защита от банов\n"
        "\U0001f3af Умный AimBot с настройками\n"
        "\U0001f441\ufe0f WallHack и ESP\n"
        "\U0001f4ca Полная информация о противниках\n"
        "\u26a1 Быстрые обновления\n\n"
        "\U0001f680 <b>Выберите платформу:</b>"
    )

    await message.answer(text, parse_mode="HTML", reply_markup=platform_keyboard())
    await state.set_state(OrderState.choosing_platform)

@dp.callback_query(F.data == "about")
async def about_cheat(callback: types.CallbackQuery):
    text = (
        "\U0001f4cb <b>Подробная информация</b>\n\n"
        "\U0001f3ae <b>Версия:</b> 0.37.1 (Март 2026)\n"
        "\U0001f525 <b>Статус:</b> Активно обновляется\n\n"
        "\U0001f6e0\ufe0f <b>Функционал:</b>\n"
        "\u2022 \U0001f3af Умный AimBot с плавностью\n"
        "\u2022 \U0001f441\ufe0f WallHack через препятствия\n"
        "\u2022 \U0001f4cd ESP с информацией об игроках\n"
        "\u2022 \U0001f5fa\ufe0f Мини-радар\n"
        "\u2022 \u2699\ufe0f Гибкие настройки\n\n"
        "\U0001f6e1\ufe0f <b>Безопасность:</b>\n"
        "\u2022 Обход античитов\n"
        "\u2022 Регулярные обновления\n"
        "\u2022 Тестирование на безопасность\n\n"
        f"\U0001f4ac Поддержка: @{SUPPORT_CHAT_USERNAME}"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=about_keyboard())
    await callback.answer()

@dp.callback_query(F.data.startswith("platform_"))
async def process_platform(callback: types.CallbackQuery, state: FSMContext):
    platform = callback.data.split("_")[1]
    await state.update_data(platform=platform)

    if platform == "apk":
        text = (
            "\U0001f4f1 <b>Android Version</b>\n\n"
            "\U0001f527 <b>Требования:</b>\n"
            "\u2022 Android 10.0+\n"
            "\u2022 2 ГБ свободной памяти\n"
            "\u2022 Root не требуется\n\n"
            "\U0001f4e6 <b>Что входит:</b>\n"
            "\u2022 APK файл с читом\n"
            "\u2022 Инструкция по установке\n"
            "\u2022 Техническая поддержка\n\n"
            "\U0001f4b0 <b>Выберите тариф:</b>"
        )
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=apk_subscription_keyboard())
    else:
        text = (
            "\U0001f34e <b>iOS Version</b>\n\n"
            "\U0001f527 <b>Требования:</b>\n"
            "\u2022 iOS 14.0 - 18.0\n"
            "\u2022 Установка через AltStore\n"
            "\u2022 Jailbreak не требуется\n\n"
            "\U0001f4e6 <b>Что входит:</b>\n"
            "\u2022 IPA файл с читом\n"
            "\u2022 Подробная инструкция\n"
            "\u2022 Помощь в установке\n\n"
            "\U0001f4b0 <b>Выберите тариф:</b>"
        )
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=ios_subscription_keyboard())

    await state.set_state(OrderState.choosing_subscription)
    await callback.answer()

@dp.callback_query(F.data.startswith("sub_"))
async def process_subscription(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    product_key = f"{parts[1]}_{parts[2]}"
    product = PRODUCTS.get(product_key)

    if not product:
        await callback.answer("\u274c Ошибка", show_alert=True)
        return

    await state.update_data(selected_product=product)

    text = (
        f"\U0001f6d2 <b>Оформление покупки</b>\n\n"
        f"{product['emoji']} <b>{product['name']}</b>\n"
        f"\u23f1\ufe0f Длительность: {product['duration']}\n\n"
        f"\U0001f48e <b>Стоимость:</b>\n"
        f"\U0001f4b3 Картой: {product['price']} \u20bd\n"
        f"\u2b50 Stars: {product['price_stars']} \u2b50\n"
        f"\u20bf Крипта: {product['price_crypto_usdt']} USDT\n"
        f"\U0001f4b0 GOLD: {product['price_gold']} \U0001fa99\n"
        f"\U0001f3a8 NFT: {product['price_nft']} \U0001f5bc\ufe0f\n\n"
        f"\U0001f3af <b>Способ оплаты:</b>"
    )

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=payment_methods_keyboard(product))
    await state.set_state(OrderState.choosing_payment)
    await callback.answer()

# ========== ОПЛАТА КАРТОЙ (ЮMONEY) ==========
@dp.callback_query(F.data.startswith("pay_yoomoney_"))
async def process_yoomoney_payment(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    product = find_product(parts[2], parts[3])
    if not product:
        await callback.answer("\u274c Ошибка", show_alert=True)
        return

    user_id = callback.from_user.id
    order_id = generate_order_id()
    amount = product["price"]
    payment_url = create_payment_link(amount, order_id, f"{product['name']} ({product['duration']})")

    pending_orders[order_id] = {
        "user_id": user_id,
        "user_name": callback.from_user.full_name,
        "product": product,
        "amount": amount,
        "currency": "\u20bd",
        "payment_method": "Картой",
        "status": "pending",
        "created_at": time.time()
    }

    text = (
        f"\U0001f4b3 <b>Оплата картой</b>\n\n"
        f"{product['emoji']} {product['name']}\n"
        f"\u23f1\ufe0f {product['duration']}\n"
        f"\U0001f4b0 К оплате: <b>{amount} \u20bd</b>\n"
        f"\U0001f194 Номер заказа: <code>{order_id}</code>\n\n"
        f"\U0001f504 <b>Инструкция:</b>\n"
        f"1\ufe0f\u20e3 Нажмите \u00abОплатить картой\u00bb\n"
        f"2\ufe0f\u20e3 Оплатите банковской картой\n"
        f"3\ufe0f\u20e3 Вернитесь и нажмите \u00abПроверить оплату\u00bb\n\n"
        f"\U0001f4ab <b>Автоматическая проверка платежа</b>"
    )

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=payment_keyboard(payment_url, order_id))
    await send_admin_notification(callback.from_user, product, "\U0001f4b3 Картой", f"{amount} \u20bd", order_id)
    await callback.answer()

# ========== ПРОВЕРКА ЮMONEY ==========
@dp.callback_query(F.data.startswith("checkym_"))
async def check_yoomoney_callback(callback: types.CallbackQuery):
    order_id = callback.data.removeprefix("checkym_")
    order = pending_orders.get(order_id)

    if not order:
        await callback.answer("\u274c Заказ не найден или уже обработан", show_alert=True)
        return

    if order_id in confirmed_payments:
        await callback.answer("\u2705 Заказ уже подтвержден!", show_alert=True)
        return

    await callback.answer("\U0001f50d Проверяем платеж...")

    checking_msg = await callback.message.edit_text(
        "\U0001f504 <b>Проверка платежа...</b>\n\n"
        "\U0001f50d Поиск транзакции в системе\n"
        "\u23f3 Подождите 15-25 секунд...",
        parse_mode="HTML"
    )

    payment_found = False
    for attempt in range(5):
        logger.info(f"Checking YooMoney payment {order_id}, attempt {attempt + 1}/5")
        payment_found = await check_yoomoney_payment(order_id, order["amount"])
        if payment_found:
            break
        await asyncio.sleep(5)

    if payment_found:
        await process_successful_payment(order_id, "Автопроверка")
        await checking_msg.edit_text(
            "\u2705 <b>Платеж найден!</b>\n\n"
            "\U0001f389 Ваш заказ обработан\n"
            "\U0001f4e8 Проверьте новое сообщение \u2b06\ufe0f",
            parse_mode="HTML",
            reply_markup=support_keyboard()
        )
    else:
        fail_text = (
            f"\u23f3 <b>Платеж пока не обнаружен</b>\n\n"
            f"\U0001f4b0 Сумма: {order['amount']} \u20bd\n"
            f"\U0001f194 Заказ: <code>{order_id}</code>\n\n"
            f"\U0001f50d <b>Возможные причины:</b>\n"
            f"\u2022 Платеж еще обрабатывается (1-3 мин)\n"
            f"\u2022 Оплачена неточная сумма\n"
            f"\u2022 Проблема на стороне банка\n\n"
            f"\u23f0 Попробуйте через 1-2 минуты\n"
            f"\U0001f4ac Или обратитесь в поддержку"
        )
        payment_url = create_payment_link(order["amount"], order_id, f"{order['product']['name']} ({order['product']['duration']})")
        await checking_msg.edit_text(fail_text, parse_mode="HTML", reply_markup=payment_keyboard(payment_url, order_id))

# ========== ОПЛАТА STARS ==========
@dp.callback_query(F.data.startswith("pay_stars_"))
async def process_stars_payment(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    product = find_product(parts[2], parts[3])
    if not product:
        await callback.answer("\u274c Ошибка", show_alert=True)
        return

    order_id = generate_order_id()

    pending_orders[order_id] = {
        "user_id": callback.from_user.id,
        "user_name": callback.from_user.full_name,
        "product": product,
        "amount": product['price_stars'],
        "currency": "\u2b50",
        "payment_method": "Telegram Stars",
        "status": "pending",
        "created_at": time.time()
    }

    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"AimNoob \u2014 {product['name']}",
        description=f"Подписка на {product['duration']} для {product['platform']}",
        payload=f"stars_{order_id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label="XTR", amount=product['price_stars'])],
        start_parameter="aimnoob_payment"
    )

    await callback.message.delete()
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
    parts = callback.data.split("_")
    product = find_product(parts[2], parts[3])
    if not product:
        await callback.answer("\u274c Ошибка", show_alert=True)
        return

    order_id = generate_order_id()
    amount_usdt = product["price_crypto_usdt"]
    description = f"AimNoob {product['name']} ({product['duration']})"

    invoice_data = await create_crypto_invoice(amount_usdt, order_id, description)
    if not invoice_data:
        await callback.answer("\u274c Ошибка создания инвойса. Попробуйте позже.", show_alert=True)
        return

    pending_orders[order_id] = {
        "user_id": callback.from_user.id,
        "user_name": callback.from_user.full_name,
        "product": product,
        "amount": amount_usdt,
        "currency": "USDT",
        "payment_method": "CryptoBot",
        "status": "pending",
        "invoice_id": invoice_data["invoice_id"],
        "created_at": time.time()
    }

    text = (
        f"\u20bf <b>Криптооплата</b>\n\n"
        f"{product['emoji']} {product['name']}\n"
        f"\u23f1\ufe0f {product['duration']}\n"
        f"\U0001f4b0 К оплате: <b>{amount_usdt} USDT</b>\n"
        f"\U0001f194 Заказ: <code>{order_id}</code>\n\n"
        f"\U0001fa99 <b>Принимаемые валюты:</b>\n"
        f"USDT, BTC, ETH, TON, LTC, BNB, TRX и др.\n\n"
        f"\U0001f504 <b>Инструкция:</b>\n"
        f"1\ufe0f\u20e3 Нажмите \u00abОплатить криптой\u00bb\n"
        f"2\ufe0f\u20e3 Выберите валюту и переведите\n"
        f"3\ufe0f\u20e3 Нажмите \u00abПроверить платеж\u00bb"
    )

    await callback.message.edit_text(
        text, parse_mode="HTML",
        reply_markup=crypto_payment_keyboard(invoice_data["pay_url"], order_id)
    )
    await send_admin_notification(callback.from_user, product, "\u20bf CryptoBot", f"{amount_usdt} USDT", order_id)
    await callback.answer()

# ========== ПРОВЕРКА КРИПТО ==========
@dp.callback_query(F.data.startswith("checkcr_"))
async def check_crypto_callback(callback: types.CallbackQuery):
    order_id = callback.data.removeprefix("checkcr_")
    order = pending_orders.get(order_id)

    if not order:
        await callback.answer("\u274c Заказ не найден", show_alert=True)
        return

    if order_id in confirmed_payments:
        await callback.answer("\u2705 Уже оплачено!", show_alert=True)
        return

    await callback.answer("\U0001f50d Проверяем...")

    invoice_id = order.get("invoice_id")
    if not invoice_id:
        await callback.answer("\u274c Ошибка: нет invoice_id", show_alert=True)
        return

    is_paid = await check_crypto_invoice(invoice_id)
    if is_paid:
        await process_successful_payment(order_id, "CryptoBot")
        await callback.message.edit_text(
            "\u2705 <b>Криптоплатеж подтвержден!</b>\n\n"
            "\U0001f389 Заказ обработан\n"
            "\U0001f4e8 Ключ отправлен в новом сообщении \u2b06\ufe0f",
            parse_mode="HTML",
            reply_markup=support_keyboard()
        )
    else:
        await callback.answer("\u23f3 Платеж пока не подтвержден. Попробуйте через минуту.", show_alert=True)

# ========== ОПЛАТА GOLD ==========
@dp.callback_query(F.data.startswith("pay_gold_"))
async def process_gold_payment(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    product = find_product(parts[2], parts[3])
    if not product:
        await callback.answer("\u274c Ошибка", show_alert=True)
        return

    price_gold = product['price_gold']
    chat_message = (
        f"Привет! Хочу купить чит на Standoff 2. "
        f"Версия 0.37.1, подписка на {product['period_text']} ({product['platform']}). "
        f"Готов купить за {price_gold} голды прямо сейчас"
    )

    import urllib.parse
    encoded_message = urllib.parse.quote(chat_message, safe='')

    text = (
        f"\U0001f4b0 <b>Оплата GOLD</b>\n\n"
        f"{product['emoji']} {product['name']}\n"
        f"\u23f1\ufe0f {product['duration']}\n"
        f"\U0001f4b0 Стоимость: <b>{price_gold} GOLD</b>\n\n"
        f"\U0001f4dd <b>Ваше сообщение для чата:</b>\n"
        f"<code>{chat_message}</code>\n\n"
        f"\U0001f504 <b>Инструкция:</b>\n"
        f"1\ufe0f\u20e3 Нажмите \u00abПерейти к оплате\u00bb\n"
        f"2\ufe0f\u20e3 Отправьте сообщение в чат\n"
        f"3\ufe0f\u20e3 Ожидайте обработки"
    )

    support_url = f"https://t.me/{SUPPORT_CHAT_USERNAME}?text={encoded_message}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\U0001f4b0 Перейти к оплате", url=support_url)],
        [InlineKeyboardButton(text="\u2705 Я написал", callback_data="gold_sent")],
        [InlineKeyboardButton(text="\u274c Отмена", callback_data="restart")]
    ])

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    order_id = f"GOLD_{callback.from_user.id}_{int(time.time())}"
    await send_admin_notification(callback.from_user, product, "\U0001f4b0 GOLD", f"{price_gold} \U0001fa99", order_id)
    await callback.answer()

# ========== ОПЛАТА NFT ==========
@dp.callback_query(F.data.startswith("pay_nft_"))
async def process_nft_payment(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    product = find_product(parts[2], parts[3])
    if not product:
        await callback.answer("\u274c Ошибка", show_alert=True)
        return

    price_nft = product['price_nft']
    chat_message = (
        f"Привет! Хочу купить чит на Standoff 2. "
        f"Версия 0.37.1, подписка на {product['period_text']} ({product['platform']}). "
        f"Готов купить за {price_nft} NFT прямо сейчас"
    )

    import urllib.parse
    encoded_message = urllib.parse.quote(chat_message, safe='')

    text = (
        f"\U0001f3a8 <b>Оплата NFT</b>\n\n"
        f"{product['emoji']} {product['name']}\n"
        f"\u23f1\ufe0f {product['duration']}\n"
        f"\U0001f4b0 Стоимость: <b>{price_nft} NFT</b>\n\n"
        f"\U0001f4dd <b>Ваше сообщение для чата:</b>\n"
        f"<code>{chat_message}</code>\n\n"
        f"\U0001f504 <b>Инструкция:</b>\n"
        f"1\ufe0f\u20e3 Нажмите \u00abПерейти к оплате\u00bb\n"
        f"2\ufe0f\u20e3 Отправьте сообщение в чат\n"
        f"3\ufe0f\u20e3 Ожидайте обработки"
    )

    support_url = f"https://t.me/{SUPPORT_CHAT_USERNAME}?text={encoded_message}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\U0001f3a8 Перейти к оплате", url=support_url)],
        [InlineKeyboardButton(text="\u2705 Я написал", callback_data="nft_sent")],
        [InlineKeyboardButton(text="\u274c Отмена", callback_data="restart")]
    ])

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    order_id = f"NFT_{callback.from_user.id}_{int(time.time())}"
    await send_admin_notification(callback.from_user, product, "\U0001f3a8 NFT", f"{price_nft} \U0001f5bc\ufe0f", order_id)
    await callback.answer()

@dp.callback_query(F.data == "gold_sent")
async def gold_sent(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "\u2705 <b>Отлично!</b>\n\n"
        "\U0001f4ab Ваш запрос принят в обработку\n"
        "\u23f1\ufe0f Время обработки: до 30 минут\n"
        "\U0001f4e8 Уведомим о готовности заказа\n\n"
        f"\U0001f4ac Поддержка: @{SUPPORT_CHAT_USERNAME}",
        parse_mode="HTML",
        reply_markup=support_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "nft_sent")
async def nft_sent(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "\u2705 <b>Превосходно!</b>\n\n"
        "\U0001f3a8 Ваш NFT заказ принят\n"
        "\u23f1\ufe0f Время обработки: до 30 минут\n"
        "\U0001f4e8 Отправим ключ после проверки\n\n"
        f"\U0001f4ac Поддержка: @{SUPPORT_CHAT_USERNAME}",
        parse_mode="HTML",
        reply_markup=support_keyboard()
    )
    await callback.answer()

# ========== АДМИНСКИЕ КОМАНДЫ ==========
@dp.callback_query(F.data.startswith("admin_confirm_"))
async def admin_confirm_payment(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("\u274c Доступ запрещен", show_alert=True)
        return

    order_id = callback.data.removeprefix("admin_confirm_")

    if order_id in confirmed_payments:
        await callback.answer("\u2705 Уже подтвержден", show_alert=True)
        return

    success = await process_successful_payment(order_id, "\U0001f468\u200d\U0001f4bc Админ")

    if success:
        await callback.message.edit_text(
            f"\u2705 <b>Заказ подтвержден</b>\n\n"
            f"\U0001f194 {order_id}\n"
            f"\U0001f468\u200d\U0001f4bc Подтвердил: {callback.from_user.full_name}\n"
            f"\U0001f4e8 Ключ отправлен пользователю",
            parse_mode="HTML"
        )
        await callback.answer("\u2705 Готово!")
    else:
        await callback.answer("\u274c Ошибка (заказ не найден или уже обработан)", show_alert=True)

@dp.callback_query(F.data.startswith("admin_reject_"))
async def admin_reject_payment(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("\u274c Доступ запрещен", show_alert=True)
        return

    order_id = callback.data.removeprefix("admin_reject_")
    order = pending_orders.pop(order_id, None)

    if order:
        await callback.message.edit_text(
            f"\u274c <b>Заказ отклонен</b>\n\n"
            f"\U0001f194 {order_id}\n"
            f"\U0001f468\u200d\U0001f4bc Отклонил: {callback.from_user.full_name}",
            parse_mode="HTML"
        )
        try:
            await bot.send_message(
                order['user_id'],
                f"\u274c <b>Заказ отклонен</b>\n\n"
                f"\U0001f194 {order_id}\n"
                f"\U0001f4de Обратитесь в поддержку\n"
                f"\U0001f4ac @{SUPPORT_CHAT_USERNAME}",
                parse_mode="HTML"
            )
        except:
            pass

    await callback.answer("\u274c Отклонен")

@dp.message(Command("orders"))
async def cmd_orders(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    text = "\U0001f4ca <b>СТАТИСТИКА ЗАКАЗОВ</b>\n\n"

    if pending_orders:
        text += f"\u23f3 <b>Ожидают оплаты:</b> {len(pending_orders)}\n"
        for oid, order in list(pending_orders.items())[:5]:
            t = datetime.fromtimestamp(order['created_at']).strftime('%H:%M')
            text += f"\u2022 {t} | {order['user_name']} | {order['product']['name']}\n"
    else:
        text += "\u23f3 <b>Ожидают оплаты:</b> 0\n"

    text += f"\n\u2705 <b>Подтверждено:</b> {len(confirmed_payments)}\n"

    balance = await get_yoomoney_balance()
    text += f"\U0001f4b0 <b>Баланс ЮМoney:</b> {balance} \u20bd\n" if balance else "\U0001f4b0 <b>Баланс ЮМoney:</b> ошибка\n"

    await message.answer(text, parse_mode="HTML")

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

    if platform == "apk":
        text = (
            "\U0001f4f1 <b>Android Version</b>\n\n"
            "\U0001f527 <b>Требования:</b> Android 10.0+\n"
            "\U0001f4e6 <b>Что входит:</b> APK + Инструкция\n\n"
            "\U0001f4b0 <b>Выберите тариф:</b>"
        )
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=apk_subscription_keyboard())
    else:
        text = (
            "\U0001f34e <b>iOS Version</b>\n\n"
            "\U0001f527 <b>Требования:</b> iOS 14.0 - 18.0\n"
            "\U0001f4e6 <b>Что входит:</b> IPA + Инструкция\n\n"
            "\U0001f4b0 <b>Выберите тариф:</b>"
        )
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=ios_subscription_keyboard())

    await state.set_state(OrderState.choosing_subscription)
    await callback.answer()

# ========== WEB SERVER (MiniApp + API) ==========
async def handle_miniapp(request):
    """Отдаем HTML MiniApp"""
    return web.Response(text=MINIAPP_HTML, content_type='text/html', charset='utf-8')

async def handle_api_create_payment(request):
    """API: создание платежа из MiniApp"""
    try:
        data = await request.json()
        product_id = data.get('product_id')
        method = data.get('method')
        user_id = data.get('user_id')
        user_name = data.get('user_name', 'MiniApp User')

        product = PRODUCTS.get(product_id)
        if not product:
            return web.json_response({"success": False, "error": "Product not found"})

        order_id = generate_order_id()

        if method == 'yoomoney':
            amount = product['price']
            payment_url = create_payment_link(amount, order_id, f"{product['name']} ({product['duration']})")

            pending_orders[order_id] = {
                "user_id": user_id,
                "user_name": user_name,
                "product": product,
                "amount": amount,
                "currency": "\u20bd",
                "payment_method": "Картой",
                "status": "pending",
                "created_at": time.time()
            }

            return web.json_response({
                "success": True,
                "payment_url": payment_url,
                "order_id": order_id
            })

        elif method == 'crypto':
            amount_usdt = product['price_crypto_usdt']
            description = f"AimNoob {product['name']} ({product['duration']})"
            invoice_data = await create_crypto_invoice(amount_usdt, order_id, description)

            if not invoice_data:
                return web.json_response({"success": False, "error": "Failed to create crypto invoice"})

            pending_orders[order_id] = {
                "user_id": user_id,
                "user_name": user_name,
                "product": product,
                "amount": amount_usdt,
                "currency": "USDT",
                "payment_method": "CryptoBot",
                "status": "pending",
                "invoice_id": invoice_data["invoice_id"],
                "created_at": time.time()
            }

            return web.json_response({
                "success": True,
                "payment_url": invoice_data["pay_url"],
                "invoice_id": invoice_data["invoice_id"],
                "order_id": order_id
            })

        elif method == 'stars':
            pending_orders[order_id] = {
                "user_id": user_id,
                "user_name": user_name,
                "product": product,
                "amount": product['price_stars'],
                "currency": "\u2b50",
                "payment_method": "Telegram Stars",
                "status": "pending",
                "created_at": time.time()
            }

            return web.json_response({
                "success": True,
                "order_id": order_id,
                "method": "stars"
            })

        elif method in ('gold', 'nft'):
            pending_orders[order_id] = {
                "user_id": user_id,
                "user_name": user_name,
                "product": product,
                "amount": product.get(f'price_{method}', 0),
                "currency": method.upper(),
                "payment_method": method.upper(),
                "status": "pending",
                "created_at": time.time()
            }

            return web.json_response({
                "success": True,
                "order_id": order_id,
                "method": method
            })

        return web.json_response({"success": False, "error": "Unknown method"})

    except Exception as e:
        logger.error(f"API create_payment error: {e}")
        return web.json_response({"success": False, "error": str(e)})

async def handle_api_check_payment(request):
    """API: проверка оплаты ЮMoney"""
    try:
        data = await request.json()
        order_id = data.get('order_id')
        order = pending_orders.get(order_id)

        if not order:
            if order_id in confirmed_payments:
                cp = confirmed_payments[order_id]
                return web.json_response({"paid": True, "license_key": cp.get('license_key', '')})
            return web.json_response({"paid": False, "error": "Order not found"})

        payment_found = False
        for attempt in range(3):
            payment_found = await check_yoomoney_payment(order_id, order["amount"])
            if payment_found:
                break
            await asyncio.sleep(3)

        if payment_found:
            await process_successful_payment(order_id, "MiniApp Автопроверка")
            cp = confirmed_payments.get(order_id, {})
            return web.json_response({"paid": True, "license_key": cp.get('license_key', '')})

        return web.json_response({"paid": False})

    except Exception as e:
        logger.error(f"API check_payment error: {e}")
        return web.json_response({"paid": False, "error": str(e)})

async def handle_api_check_crypto(request):
    """API: проверка криптоплатежа"""
    try:
        data = await request.json()
        invoice_id = data.get('invoice_id')
        order_id = data.get('order_id')

        if not invoice_id:
            return web.json_response({"paid": False, "error": "No invoice_id"})

        is_paid = await check_crypto_invoice(invoice_id)

        if is_paid:
            await process_successful_payment(order_id, "MiniApp CryptoBot")
            cp = confirmed_payments.get(order_id, {})
            return web.json_response({"paid": True, "license_key": cp.get('license_key', '')})

        return web.json_response({"paid": False})

    except Exception as e:
        logger.error(f"API check_crypto error: {e}")
        return web.json_response({"paid": False, "error": str(e)})

async def handle_health(request):
    """Health check"""
    return web.json_response({
        "status": "ok",
        "pending_orders": len(pending_orders),
        "confirmed": len(confirmed_payments),
        "uptime": time.time()
    })

# ========== CORS middleware ==========
@web.middleware
async def cors_middleware(request, handler):
    if request.method == 'OPTIONS':
        response = web.Response(status=200)
    else:
        response = await handler(request)

    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response

# ========== ЗАПУСК ==========
async def main():
    print("\U0001f3af" + "=" * 50 + "\U0001f3af")
    print("\U0001f680      AIMNOOB PREMIUM SHOP BOT       \U0001f680")
    print("\U0001f48e" + "=" * 50 + "\U0001f48e")
    print(f"\U0001f527 ADMIN_ID:        {ADMIN_ID}")
    print(f"\U0001f527 SUPPORT_CHAT_ID: {SUPPORT_CHAT_ID}")
    print(f"\U0001f527 ALL ADMIN_IDS:   {ADMIN_IDS}")
    print(f"\U0001f527 MINIAPP_URL:     {MINIAPP_URL}")
    print(f"\U0001f527 WEB_PORT:        {WEB_PORT}")

    if not BOT_TOKEN:
        print("\u274c BOT_TOKEN not found!")
        return

    try:
        balance = await get_yoomoney_balance()
        if balance is not None:
            print(f"\u2705 YooMoney: connected (balance: {balance} \u20bd)")
            balance_history.append({'time': time.time(), 'balance': balance})
        else:
            print("\u26a0\ufe0f  YooMoney: connection issues")

        me = await bot.get_me()
        print(f"\n\U0001f916 Bot: @{me.username}")
        print(f"\U0001f4ac Support: @{SUPPORT_CHAT_USERNAME}")
        print(f"\U0001f310 Site: {SHOP_URL}")
        print(f"\U0001f3ae MiniApp: {MINIAPP_URL}")

        # Устанавливаем кнопку MiniApp в меню бота
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="\U0001f3ae Магазин",
                    web_app=WebAppInfo(url=MINIAPP_URL)
                )
            )
            print("\u2705 Menu button set to MiniApp")
        except Exception as e:
            logger.warning(f"Could not set menu button: {e}")

        print(f"\n\U0001f4b3 PAYMENT METHODS:")
        print(f"\u2022 \U0001f4b3 Картой (YooMoney)")
        print(f"\u2022 \u2b50 Telegram Stars")
        print(f"\u2022 \u20bf  CryptoBot")
        print(f"\u2022 \U0001f4b0 GOLD (manual)")
        print(f"\u2022 \U0001f3a8 NFT (manual)")

        print(f"\n\U0001f4e6 PRODUCTS:")
        for key, product in PRODUCTS.items():
            print(f"\u2022 {product['emoji']} {product['name']} ({product['duration']}) \u2014 {product['price']}\u20bd")

        print("\U0001f3af" + "=" * 50 + "\U0001f3af")
        print("\u2728 Bot + MiniApp starting!")
        print("\U0001f48e" + "=" * 50 + "\U0001f48e")

        # Создаем web-приложение
        app = web.Application(middlewares=[cors_middleware])
        app.router.add_get('/', handle_miniapp)
        app.router.add_get('/health', handle_health)
        app.router.add_post('/api/create_payment', handle_api_create_payment)
        app.router.add_post('/api/check_payment', handle_api_check_payment)
        app.router.add_post('/api/check_crypto', handle_api_check_crypto)

        # Запускаем polling бота и web-сервер одновременно
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', WEB_PORT)

        await site.start()
        print(f"\U0001f310 Web server started on port {WEB_PORT}")

        # Запускаем polling
        await dp.start_polling(bot)

    except Exception as e:
        print(f"\u274c Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
