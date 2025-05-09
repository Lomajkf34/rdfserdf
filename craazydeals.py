import os
import logging
from aiogram import Bot, Dispatcher, types, executor
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice
import sqlite3
import uuid
import datetime

# Настройка логгирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота
API_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
ADMIN_ID = os.getenv('ADMIN_TELEGRAM_ID')  # Ваш ID в Telegram
PROVIDER_TOKEN = os.getenv('TELEGRAM_PAYMENTS_PROVIDER_TOKEN')  # Токен платежного провайдера

bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('craazydeals.db')
    cursor = conn.cursor()
    
    # Таблица пользователей
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        balance REAL DEFAULT 0,
        rating REAL DEFAULT 5.0,
        deals_count INTEGER DEFAULT 0,
        registered_at TEXT DEFAULT CURRENT_TIMESTAMP,
        is_banned BOOLEAN DEFAULT FALSE
    )
    ''')
    
    # Таблица товаров
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS products (
        product_id INTEGER PRIMARY KEY AUTOINCREMENT,
        seller_id INTEGER,
        title TEXT,
        description TEXT,
        price REAL,
        category TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        is_active BOOLEAN DEFAULT TRUE,
        FOREIGN KEY (seller_id) REFERENCES users (user_id)
    ''')
    
    # Таблица сделок
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS deals (
        deal_id TEXT PRIMARY KEY,
        buyer_id INTEGER,
        seller_id INTEGER,
        product_id INTEGER,
        amount REAL,
        status TEXT DEFAULT 'pending',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        completed_at TEXT,
        admin_commission REAL,
        buyer_confirmed BOOLEAN DEFAULT FALSE,
        seller_confirmed BOOLEAN DEFAULT FALSE,
        FOREIGN KEY (buyer_id) REFERENCES users (user_id),
        FOREIGN KEY (seller_id) REFERENCES users (user_id),
        FOREIGN KEY (product_id) REFERENCES products (product_id)
    )
    ''')
    
    # Таблица сообщений в диспутах
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS dispute_messages (
        message_id INTEGER PRIMARY KEY AUTOINCREMENT,
        deal_id TEXT,
        user_id INTEGER,
        message TEXT,
        sent_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (deal_id) REFERENCES deals (deal_id),
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )
    ''')
    
    conn.commit()
    conn.close()

init_db()

# Состояния для FSM
class Form(StatesGroup):
    add_product_title = State()
    add_product_description = State()
    add_product_price = State()
    add_product_category = State()
    top_up_amount = State()
    withdraw_amount = State()
    dispute_message = State()
    admin_message = State()

# Комиссия администратора
ADMIN_COMMISSION = 0.08  # 8%

# Вспомогательные функции
def get_user(user_id):
    conn = sqlite3.connect('craazydeals.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user

def create_user(user_id, username):
    conn = sqlite3.connect('craazydeals.db')
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)', (user_id, username))
    conn.commit()
    conn.close()

def update_balance(user_id, amount):
    conn = sqlite3.connect('craazydeals.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (amount, user_id))
    conn.commit()
    conn.close()

def add_product(seller_id, title, description, price, category):
    conn = sqlite3.connect('craazydeals.db')
    cursor = conn.cursor()
    cursor.execute('INSERT INTO products (seller_id, title, description, price, category) VALUES (?, ?, ?, ?, ?)',
                   (seller_id, title, description, price, category))
    product_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return product_id

def get_product(product_id):
    conn = sqlite3.connect('craazydeals.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM products WHERE product_id = ?', (product_id,))
    product = cursor.fetchone()
    conn.close()
    return product

def get_user_products(user_id):
    conn = sqlite3.connect('craazydeals.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM products WHERE seller_id = ? AND is_active = TRUE', (user_id,))
    products = cursor.fetchall()
    conn.close()
    return products

def create_deal(buyer_id, seller_id, product_id, amount):
    deal_id = str(uuid.uuid4())
    commission = amount * ADMIN_COMMISSION
    conn = sqlite3.connect('craazydeals.db')
    cursor = conn.cursor()
    cursor.execute('''
    INSERT INTO deals (deal_id, buyer_id, seller_id, product_id, amount, admin_commission)
    VALUES (?, ?, ?, ?, ?, ?)
    ''', (deal_id, buyer_id, seller_id, product_id, amount, commission))
    conn.commit()
    conn.close()
    return deal_id

def get_deal(deal_id):
    conn = sqlite3.connect('craazydeals.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM deals WHERE deal_id = ?', (deal_id,))
    deal = cursor.fetchone()
    conn.close()
    return deal

def update_deal_status(deal_id, status):
    conn = sqlite3.connect('craazydeals.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE deals SET status = ? WHERE deal_id = ?', (status, deal_id))
    if status == 'completed':
        cursor.execute('UPDATE deals SET completed_at = CURRENT_TIMESTAMP WHERE deal_id = ?', (deal_id,))
    conn.commit()
    conn.close()

def confirm_deal_for_user(deal_id, user_type):
    conn = sqlite3.connect('craazydeals.db')
    cursor = conn.cursor()
    if user_type == 'buyer':
        cursor.execute('UPDATE deals SET buyer_confirmed = TRUE WHERE deal_id = ?', (deal_id,))
    else:
        cursor.execute('UPDATE deals SET seller_confirmed = TRUE WHERE deal_id = ?', (deal_id,))
    
    # Проверяем, подтвердили ли обе стороны
    cursor.execute('SELECT buyer_confirmed, seller_confirmed FROM deals WHERE deal_id = ?', (deal_id,))
    buyer_confirmed, seller_confirmed = cursor.fetchone()
    
    if buyer_confirmed and seller_confirmed:
        # Завершаем сделку и распределяем деньги
        cursor.execute('SELECT amount, admin_commission, seller_id FROM deals WHERE deal_id = ?', (deal_id,))
        amount, commission, seller_id = cursor.fetchone()
        
        # Переводим деньги продавцу (за вычетом комиссии)
        seller_amount = amount - commission
        cursor.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (seller_amount, seller_id))
        
        # Комиссия администратору
        cursor.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (commission, ADMIN_ID))
        
        # Обновляем статус сделки
        cursor.execute('UPDATE deals SET status = "completed" WHERE deal_id = ?', (deal_id,))
        
        # Увеличиваем счетчик сделок у участников
        cursor.execute('''
        UPDATE users SET deals_count = deals_count + 1 
        WHERE user_id IN (SELECT buyer_id FROM deals WHERE deal_id = ?)
           OR user_id IN (SELECT seller_id FROM deals WHERE deal_id = ?)
        ''', (deal_id, deal_id))
    
    conn.commit()
    conn.close()

def add_dispute_message(deal_id, user_id, message):
    conn = sqlite3.connect('craazydeals.db')
    cursor = conn.cursor()
    cursor.execute('INSERT INTO dispute_messages (deal_id, user_id, message) VALUES (?, ?, ?)', 
                   (deal_id, user_id, message))
    conn.commit()
    conn.close()

def get_dispute_messages(deal_id):
    conn = sqlite3.connect('craazydeals.db')
    cursor = conn.cursor()
    cursor.execute('''
    SELECT dm.message_id, dm.user_id, u.username, dm.message, dm.sent_at 
    FROM dispute_messages dm
    JOIN users u ON dm.user_id = u.user_id
    WHERE dm.deal_id = ?
    ORDER BY dm.sent_at
    ''', (deal_id,))
    messages = cursor.fetchall()
    conn.close()
    return messages

# Обработчики команд
@dp.message_handler(commands=['start'])
async def send_welcome(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username
    create_user(user_id, username)
    
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("🛒 Магазин", callback_data="shop"))
    keyboard.add(InlineKeyboardButton("💰 Мой баланс", callback_data="balance"), 
                InlineKeyboardButton("📊 Мой профиль", callback_data="profile"))
    keyboard.add(InlineKeyboardButton("➕ Добавить товар", callback_data="add_product"))
    keyboard.add(InlineKeyboardButton("📦 Мои товары", callback_data="my_products"))
    keyboard.add(InlineKeyboardButton("🤝 Мои сделки", callback_data="my_deals"))
    
    await message.reply("""👋 Добро пожаловать в CraazyDeals - безопасную площадку для покупки и продажи цифровых товаров!

🔒 Все сделки защищены: деньги замораживаются до подтверждения получения товара
💼 Комиссия системы: 8% от суммы сделки

Выберите действие:""", reply_markup=keyboard)

@dp.callback_query_handler(lambda c: c.data == 'shop')
async def show_shop(callback_query: types.CallbackQuery):
    conn = sqlite3.connect('craazydeals.db')
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT category FROM products WHERE is_active = TRUE')
    categories = cursor.fetchall()
    conn.close()
    
    keyboard = InlineKeyboardMarkup()
    for category in categories:
        keyboard.add(InlineKeyboardButton(category[0], callback_data=f"category_{category[0]}"))
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="back_to_main"))
    
    await bot.edit_message_text(chat_id=callback_query.message.chat.id,
                              message_id=callback_query.message.message_id,
                              text="🛍 Выберите категорию товаров:",
                              reply_markup=keyboard)

@dp.callback_query_handler(lambda c: c.data.startswith('category_'))
async def show_category_products(callback_query: types.CallbackQuery):
    category = callback_query.data.replace('category_', '')
    
    conn = sqlite3.connect('craazydeals.db')
    cursor = conn.cursor()
    cursor.execute('''
    SELECT p.product_id, p.title, p.price, u.username 
    FROM products p
    JOIN users u ON p.seller_id = u.user_id
    WHERE p.category = ? AND p.is_active = TRUE
    ''', (category,))
    products = cursor.fetchall()
    conn.close()
    
    keyboard = InlineKeyboardMarkup()
    for product in products:
        keyboard.add(InlineKeyboardButton(f"{product[1]} - {product[2]}₽ ({product[3]})", 
                                         callback_data=f"product_{product[0]}"))
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="shop"))
    
    await bot.edit_message_text(chat_id=callback_query.message.chat.id,
                              message_id=callback_query.message.message_id,
                              text=f"📦 Товары в категории {category}:",
                              reply_markup=keyboard)

@dp.callback_query_handler(lambda c: c.data.startswith('product_'))
async def show_product(callback_query: types.CallbackQuery):
    product_id = int(callback_query.data.replace('product_', ''))
    product = get_product(product_id)
    
    if not product:
        await bot.answer_callback_query(callback_query.id, "Товар не найден!")
        return
    
    seller_info = get_user(product[1])
    seller_username = seller_info[1] if seller_info else "Неизвестный"
    seller_rating = seller_info[3] if seller_info else "Нет оценок"
    
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("🛒 Купить", callback_data=f"buy_{product_id}"))
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data=f"category_{product[4]}"))
    
    await bot.edit_message_text(chat_id=callback_query.message.chat.id,
                              message_id=callback_query.message.message_id,
                              text=f"""📦 <b>{product[2]}</b>

💰 Цена: <b>{product[4]}₽</b>
👤 Продавец: <b>{seller_username}</b> (рейтинг: {seller_rating})
📝 Описание:
{product[3]}

🛒 Нажмите кнопку ниже, чтобы купить товар.""",
                              parse_mode='HTML',
                              reply_markup=keyboard)

@dp.callback_query_handler(lambda c: c.data.startswith('buy_'))
async def buy_product(callback_query: types.CallbackQuery):
    product_id = int(callback_query.data.replace('buy_', ''))
    product = get_product(product_id)
    buyer_id = callback_query.from_user.id
    
    if not product:
        await bot.answer_callback_query(callback_query.id, "Товар не найден!")
        return
    
    if product[1] == buyer_id:
        await bot.answer_callback_query(callback_query.id, "Вы не можете купить свой собственный товар!")
        return
    
    # Проверяем баланс покупателя
    buyer = get_user(buyer_id)
    if buyer[2] < product[4]:
        await bot.answer_callback_query(callback_query.id, "Недостаточно средств на балансе!")
        return
    
    # Создаем сделку
    deal_id = create_deal(buyer_id, product[1], product_id, product[4])
    
    # Замораживаем деньги у покупателя
    update_balance(buyer_id, -product[4])
    
    # Уведомляем продавца
    seller_keyboard = InlineKeyboardMarkup()
    seller_keyboard.add(InlineKeyboardButton("📨 Отправить товар", callback_data=f"send_{deal_id}"))
    
    await bot.send_message(product[1], 
                          f"""🛒 Новый заказ!
Покупатель: @{callback_query.from_user.username}
Товар: {product[2]}
Сумма: {product[4]}₽

Нажмите кнопку ниже, чтобы отправить товар покупателю.""",
                          reply_markup=seller_keyboard)
    
    # Уведомляем покупателя
    deal_keyboard = InlineKeyboardMarkup()
    deal_keyboard.add(InlineKeyboardButton("✅ Подтвердить получение", callback_data=f"confirm_{deal_id}"))
    deal_keyboard.add(InlineKeyboardButton("⚠️ Открыть диспут", callback_data=f"dispute_{deal_id}"))
    
    await bot.edit_message_text(chat_id=callback_query.message.chat.id,
                              message_id=callback_query.message.message_id,
                              text=f"""🛒 Ваш заказ создан!
Товар: {product[2]}
Продавец: @{get_user(product[1])[1]}
Сумма: {product[4]}₽
Статус: Ожидает отправки

После получения товара нажмите кнопку подтверждения.""",
                              reply_markup=deal_keyboard)
    
    await bot.answer_callback_query(callback_query.id, "Заказ создан! Деньги заморожены.")

@dp.callback_query_handler(lambda c: c.data.startswith('send_'))
async def send_product(callback_query: types.CallbackQuery):
    deal_id = callback_query.data.replace('send_', '')
    deal = get_deal(deal_id)
    
    if not deal:
        await bot.answer_callback_query(callback_query.id, "Сделка не найдена!")
        return
    
    if callback_query.from_user.id != deal[2]:  # Проверяем, что это продавец
        await bot.answer_callback_query(callback_query.id, "Вы не являетесь продавцом!")
        return
    
    # Обновляем статус сделки
    update_deal_status(deal_id, 'sent')
    
    # Уведомляем покупателя
    deal_keyboard = InlineKeyboardMarkup()
    deal_keyboard.add(InlineKeyboardButton("✅ Подтвердить получение", callback_data=f"confirm_{deal_id}"))
    deal_keyboard.add(InlineKeyboardButton("⚠️ Открыть диспут", callback_data=f"dispute_{deal_id}"))
    
    await bot.send_message(deal[1],  # buyer_id
                          f"""📦 Продавец отправил товар!
Товар: {get_product(deal[3])[2]}
Сумма: {deal[4]}₽

После получения товара подтвердите его получение.""",
                          reply_markup=deal_keyboard)
    
    await bot.edit_message_text(chat_id=callback_query.message.chat.id,
                              message_id=callback_query.message.message_id,
                              text="✅ Вы отправили товар покупателю. Ожидайте подтверждения получения.")
    
    await bot.answer_callback_query(callback_query.id, "Товар отправлен!")

@dp.callback_query_handler(lambda c: c.data.startswith('confirm_'))
async def confirm_deal(callback_query: types.CallbackQuery):
    deal_id = callback_query.data.replace('confirm_', '')
    deal = get_deal(deal_id)
    
    if not deal:
        await bot.answer_callback_query(callback_query.id, "Сделка не найдена!")
        return
    
    if callback_query.from_user.id != deal[1]:  # Проверяем, что это покупатель
        await bot.answer_callback_query(callback_query.id, "Вы не являетесь покупателем!")
        return
    
    if deal[5] != 'sent':  # Проверяем, что товар отправлен
        await bot.answer_callback_query(callback_query.id, "Товар еще не отправлен!")
        return
    
    # Подтверждаем сделку от покупателя
    confirm_deal_for_user(deal_id, 'buyer')
    
    # Уведомляем продавца
    await bot.send_message(deal[2],  # seller_id
                         f"""✅ Покупатель подтвердил получение товара!
Сделка #{deal_id} завершена.
Сумма: {deal[4]}₽
Ваш заработок: {deal[4] - deal[8]}₽ (за вычетом комиссии)""")
    
    await bot.edit_message_text(chat_id=callback_query.message.chat.id,
                              message_id=callback_query.message.message_id,
                              text="✅ Вы подтвердили получение товара. Сделка завершена!")
    
    await bot.answer_callback_query(callback_query.id, "Сделка подтверждена!")

@dp.callback_query_handler(lambda c: c.data.startswith('dispute_'))
async def start_dispute(callback_query: types.CallbackQuery):
    deal_id = callback_query.data.replace('dispute_', '')
    deal = get_deal(deal_id)
    
    if not deal:
        await bot.answer_callback_query(callback_query.id, "Сделка не найдена!")
        return
    
    user_id = callback_query.from_user.id
    if user_id not in (deal[1], deal[2]):  # Проверяем, что это участник сделки
        await bot.answer_callback_query(callback_query.id, "Вы не участник сделки!")
        return
    
    # Устанавливаем статус диспута
    update_deal_status(deal_id, 'dispute')
    
    # Уведомляем администратора
    product = get_product(deal[3])
    buyer = get_user(deal[1])
    seller = get_user(deal[2])
    
    dispute_keyboard = InlineKeyboardMarkup()
    dispute_keyboard.add(InlineKeyboardButton("💬 Ответить", callback_data=f"admin_reply_{deal_id}"))
    dispute_keyboard.add(InlineKeyboardButton("🔙 Вернуть деньги покупателю", callback_data=f"refund_{deal_id}"))
    dispute_keyboard.add(InlineKeyboardButton("💰 Передать деньги продавцу", callback_data=f"pay_seller_{deal_id}"))
    
    await bot.send_message(ADMIN_ID,
                         f"""⚠️ ОТКРЫТ ДИСПУТ!
Сделка: #{deal_id}
Товар: {product[2]}
Покупатель: @{buyer[1]} (ID: {buyer[0]})
Продавец: @{seller[1]} (ID: {seller[0]})
Сумма: {deal[4]}₽

Выберите действие:""",
                         reply_markup=dispute_keyboard)
    
    # Уведомляем участников
    await bot.send_message(deal[1], 
                         f"""⚠️ По сделке #{deal_id} открыт диспут. 
Администратор рассмотрит вашу ситуацию в ближайшее время.""")
    
    await bot.send_message(deal[2], 
                         f"""⚠️ По сделке #{deal_id} открыт диспут. 
Администратор рассмотрит вашу ситуацию в ближайшее время.""")
    
    await bot.edit_message_text(chat_id=callback_query.message.chat.id,
                              message_id=callback_query.message.message_id,
                              text="""⚠️ Диспут открыт! 
Опишите проблему в одном сообщении, и администратор рассмотрит ваш случай.""")
    
    await Form.dispute_message.set()
    state = Dispatcher.get_current().current_state()
    await state.update_data(deal_id=deal_id, user_id=user_id)
    
    await bot.answer_callback_query(callback_query.id, "Диспут открыт!")

@dp.message_handler(state=Form.dispute_message)
async def process_dispute_message(message: types.Message, state: FSMContext):
    data = await state.get_data()
    deal_id = data['deal_id']
    user_id = data['user_id']
    
    # Сохраняем сообщение в диспуте
    add_dispute_message(deal_id, user_id, message.text)
    
    # Пересылаем сообщение администратору
    user = get_user(user_id)
    await bot.send_message(ADMIN_ID,
                         f"""✉️ Новое сообщение в диспуте #{deal_id}
От: @{user[1]} (ID: {user[0]})
Сообщение:
{message.text}""")
    
    await message.reply("Ваше сообщение отправлено администратору. Ожидайте решения.")
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith('admin_reply_'))
async def admin_reply_to_dispute(callback_query: types.CallbackQuery):
    deal_id = callback_query.data.replace('admin_reply_', '')
    
    await bot.edit_message_text(chat_id=callback_query.message.chat.id,
                              message_id=callback_query.message.message_id,
                              text="Введите ваш ответ на диспут:")
    
    await Form.admin_message.set()
    state = Dispatcher.get_current().current_state()
    await state.update_data(deal_id=deal_id, is_admin=True)
    
    await bot.answer_callback_query(callback_query.id, "Введите сообщение")

@dp.message_handler(state=Form.admin_message)
async def process_admin_message(message: types.Message, state: FSMContext):
    data = await state.get_data()
    deal_id = data['deal_id']
    is_admin = data.get('is_admin', False)
    
    if is_admin:
        # Получаем участников сделки
        deal = get_deal(deal_id)
        buyer_id = deal[1]
        seller_id = deal[2]
        
        # Отправляем сообщение обоим участникам
        await bot.send_message(buyer_id,
                             f"""✉️ Сообщение администратора по диспуту #{deal_id}:
{message.text}""")
        
        await bot.send_message(seller_id,
                             f"""✉️ Сообщение администратора по диспуту #{deal_id}:
{message.text}""")
        
        await message.reply("Ваше сообщение отправлено участникам сделки.")
    else:
        # Логика для обычных пользователей (если нужно)
        pass
    
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith('refund_'))
async def refund_to_buyer(callback_query: types.CallbackQuery):
    deal_id = callback_query.data.replace('refund_', '')
    deal = get_deal(deal_id)
    
    if not deal:
        await bot.answer_callback_query(callback_query.id, "Сделка не найдена!")
        return
    
    if callback_query.from_user.id != ADMIN_ID:
        await bot.answer_callback_query(callback_query.id, "Только администратор может выполнить это действие!")
        return
    
    # Возвращаем деньги покупателю
    update_balance(deal[1], deal[4])
    
    # Обновляем статус сделки
    update_deal_status(deal_id, 'refunded')
    
    # Уведомляем участников
    await bot.send_message(deal[1],  # buyer
                         f"""💰 По диспуту #{deal_id} администратор принял решение вернуть вам деньги.
Сумма {deal[4]}₽ возвращена на ваш баланс.""")
    
    await bot.send_message(deal[2],  # seller
                         f"""ℹ️ По диспуту #{deal_id} администратор принял решение вернуть деньги покупателю.""")
    
    await bot.edit_message_text(chat_id=callback_query.message.chat.id,
                              message_id=callback_query.message.message_id,
                              text=f"✅ Деньги возвращены покупателю по сделке #{deal_id}")
    
    await bot.answer_callback_query(callback_query.id, "Деньги возвращены!")

@dp.callback_query_handler(lambda c: c.data.startswith('pay_seller_'))
async def pay_to_seller(callback_query: types.CallbackQuery):
    deal_id = callback_query.data.replace('pay_seller_', '')
    deal = get_deal(deal_id)
    
    if not deal:
        await bot.answer_callback_query(callback_query.id, "Сделка не найдена!")
        return
    
    if callback_query.from_user.id != ADMIN_ID:
        await bot.answer_callback_query(callback_query.id, "Только администратор может выполнить это действие!")
        return
    
    # Передаем деньги продавцу (за вычетом комиссии)
    seller_amount = deal[4] - deal[8]
    update_balance(deal[2], seller_amount)
    
    # Комиссия администратору
    update_balance(ADMIN_ID, deal[8])
    
    # Обновляем статус сделки
    update_deal_status(deal_id, 'completed')
    
    # Уведомляем участников
    await bot.send_message(deal[1],  # buyer
                         f"""ℹ️ По диспуту #{deal_id} администратор принял решение передать деньги продавцу.""")
    
    await bot.send_message(deal[2],  # seller
                         f"""💰 По диспуту #{deal_id} администратор принял решение передать вам деньги.
Сумма {seller_amount}₽ зачислена на ваш баланс (за вычетом комиссии {deal[8]}₽).""")
    
    await bot.edit_message_text(chat_id=callback_query.message.chat.id,
                              message_id=callback_query.message.message_id,
                              text=f"✅ Деньги переданы продавцу по сделке #{deal_id}")
    
    await bot.answer_callback_query(callback_query.id, "Деньги переданы!")

@dp.callback_query_handler(lambda c: c.data == 'balance')
async def show_balance(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    user = get_user(user_id)
    
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("💳 Пополнить баланс", callback_data="top_up"))
    keyboard.add(InlineKeyboardButton("💰 Вывести средства", callback_data="withdraw"))
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="back_to_main"))
    
    await bot.edit_message_text(chat_id=callback_query.message.chat.id,
                              message_id=callback_query.message.message_id,
                              text=f"""💰 Ваш баланс: <b>{user[2]}₽</b>

Вы можете пополнить баланс или вывести средства.""",
                              parse_mode='HTML',
                              reply_markup=keyboard)

@dp.callback_query_handler(lambda c: c.data == 'top_up')
async def top_up_balance(callback_query: types.CallbackQuery):
    await bot.edit_message_text(chat_id=callback_query.message.chat.id,
                              message_id=callback_query.message.message_id,
                              text="💳 Введите сумму для пополнения (в рублях):")
    
    await Form.top_up_amount.set()
    await bot.answer_callback_query(callback_query.id)

@dp.message_handler(state=Form.top_up_amount)
async def process_top_up_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.reply("Пожалуйста, введите корректную сумму (число больше нуля).")
        return
    
    # Создаем счет для оплаты через Telegram Payments
    prices = [LabeledPrice(label="Пополнение баланса", amount=int(amount * 100))]
    
    await bot.send_invoice(
        message.chat.id,
        title="Пополнение баланса",
        description=f"Пополнение баланса на {amount}₽ в CraazyDeals",
        provider_token=PROVIDER_TOKEN,
        currency="rub",
        prices=prices,
        payload=f"topup_{message.from_user.id}_{amount}"
    )
    
    await state.finish()

@dp.pre_checkout_query_handler()
async def process_pre_checkout_query(pre_checkout_query: types.PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message_handler(content_types=types.ContentType.SUCCESSFUL_PAYMENT)
async def process_successful_payment(message: types.Message):
    # Обрабатываем успешный платеж
    payload = message.successful_payment.invoice_payload
    user_id = int(payload.split('_')[1])
    amount = float(payload.split('_')[2])
    
    # Зачисляем средства на баланс
    update_balance(user_id, amount)
    
    await bot.send_message(user_id,
                          f"""✅ Баланс успешно пополнен на {amount}₽!
Текущий баланс: {get_user(user_id)[2]}₽""")

@dp.callback_query_handler(lambda c: c.data == 'withdraw')
async def withdraw_funds(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    user = get_user(user_id)
    
    if user[2] <= 0:
        await bot.answer_callback_query(callback_query.id, "На вашем балансе нет средств для вывода!")
        return
    
    await bot.edit_message_text(chat_id=callback_query.message.chat.id,
                              message_id=callback_query.message.message_id,
                              text=f"💰 Введите сумму для вывода (доступно: {user[2]}₽):")
    
    await Form.withdraw_amount.set()
    state = Dispatcher.get_current().current_state()
    await state.update_data(current_balance=user[2])
    
    await bot.answer_callback_query(callback_query.id)

@dp.message_handler(state=Form.withdraw_amount)
async def process_withdraw_amount(message: types.Message, state: FSMContext):
    data = await state.get_data()
    current_balance = data['current_balance']
    
    try:
        amount = float(message.text)
        if amount <= 0:
            await message.reply("Сумма должна быть больше нуля!")
            return
        if amount > current_balance:
            await message.reply("Недостаточно средств на балансе!")
            return
    except ValueError:
        await message.reply("Пожалуйста, введите корректную сумму.")
        return
    
    user_id = message.from_user.id
    
    # Списываем средства с баланса
    update_balance(user_id, -amount)
    
    # Уведомляем администратора о запросе на вывод
    await bot.send_message(ADMIN_ID,
                         f"""⚠️ ЗАПРОС НА ВЫВОД СРЕДСТВ
Пользователь: @{message.from_user.username} (ID: {user_id})
Сумма: {amount}₽
Реквизиты: (пользователь должен предоставить)""")
    
    await message.reply(f"""✅ Запрос на вывод {amount}₽ отправлен администратору. 

Отправьте реквизиты для вывода (номер карты или другие платежные данные) ответным сообщением, и администратор обработает ваш запрос в ближайшее время.""")
    
    await state.finish()

@dp.callback_query_handler(lambda c: c.data == 'profile')
async def show_profile(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    user = get_user(user_id)
    
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="back_to_main"))
    
    await bot.edit_message_text(chat_id=callback_query.message.chat.id,
                              message_id=callback_query.message.message_id,
                              text=f"""📊 <b>Ваш профиль</b>

👤 Имя пользователя: @{user[1]}
💰 Баланс: {user[2]}₽
⭐ Рейтинг: {user[3]}
🛒 Всего сделок: {user[4]}
📅 Дата регистрации: {user[5]}""",
                              parse_mode='HTML',
                              reply_markup=keyboard)

@dp.callback_query_handler(lambda c: c.data == 'add_product')
async def add_product_start(callback_query: types.CallbackQuery):
    await bot.edit_message_text(chat_id=callback_query.message.chat.id,
                              message_id=callback_query.message.message_id,
                              text="✏️ Введите название товара:")
    
    await Form.add_product_title.set()
    await bot.answer_callback_query(callback_query.id)

@dp.message_handler(state=Form.add_product_title)
async def process_product_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text)
    
    await message.reply("📝 Теперь введите описание товара:")
    
    await Form.add_product_description.set()

@dp.message_handler(state=Form.add_product_description)
async def process_product_description(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    
    await message.reply("💰 Введите цену товара (в рублях):")
    
    await Form.add_product_price.set()

@dp.message_handler(state=Form.add_product_price)
async def process_product_price(message: types.Message, state: FSMContext):
    try:
        price = float(message.text)
        if price <= 0:
            raise ValueError
    except ValueError:
        await message.reply("Пожалуйста, введите корректную цену (число больше нуля).")
        return
    
    await state.update_data(price=price)
    
    await message.reply("📂 Введите категорию товара (например, 'Игры', 'Аккаунты', 'Программы'):")
    
    await Form.add_product_category.set()

@dp.message_handler(state=Form.add_product_category)
async def process_product_category(message: types.Message, state: FSMContext):
    category = message.text
    data = await state.get_data()
    
    # Добавляем товар в базу данных
    product_id = add_product(message.from_user.id, data['title'], data['description'], data['price'], category)
    
    await message.reply(f"""✅ Товар "{data['title']}" успешно добавлен в магазин!

Вы можете управлять своими товарами через меню "Мои товары".""")
    
    await state.finish()

@dp.callback_query_handler(lambda c: c.data == 'my_products')
async def show_my_products(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    products = get_user_products(user_id)
    
    if not products:
        await bot.edit_message_text(chat_id=callback_query.message.chat.id,
                                  message_id=callback_query.message.message_id,
                                  text="У вас пока нет активных товаров.")
        return
    
    keyboard = InlineKeyboardMarkup()
    for product in products:
        keyboard.add(InlineKeyboardButton(f"{product[2]} - {product[4]}₽", callback_data=f"manage_product_{product[0]}"))
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="back_to_main"))
    
    await bot.edit_message_text(chat_id=callback_query.message.chat.id,
                              message_id=callback_query.message.message_id,
                              text="📦 Ваши товары:",
                              reply_markup=keyboard)

@dp.callback_query_handler(lambda c: c.data.startswith('manage_product_'))
async def manage_product(callback_query: types.CallbackQuery):
    product_id = int(callback_query.data.replace('manage_product_', ''))
    product = get_product(product_id)
    
    if not product or product[1] != callback_query.from_user.id:
        await bot.answer_callback_query(callback_query.id, "Товар не найден!")
        return
    
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_product_{product_id}"))
    keyboard.add(InlineKeyboardButton("❌ Удалить", callback_data=f"delete_product_{product_id}"))
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="my_products"))
    
    await bot.edit_message_text(chat_id=callback_query.message.chat.id,
                              message_id=callback_query.message.message_id,
                              text=f"""📦 Управление товаром:
                              
Название: {product[2]}
Описание: {product[3]}
Цена: {product[4]}₽
Категория: {product[5]}""",
                              reply_markup=keyboard)

@dp.callback_query_handler(lambda c: c.data.startswith('delete_product_'))
async def delete_product(callback_query: types.CallbackQuery):
    product_id = int(callback_query.data.replace('delete_product_', ''))
    product = get_product(product_id)
    
    if not product or product[1] != callback_query.from_user.id:
        await bot.answer_callback_query(callback_query.id, "Товар не найден!")
        return
    
    # "Удаляем" товар (делаем неактивным)
    conn = sqlite3.connect('craazydeals.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE products SET is_active = FALSE WHERE product_id = ?', (product_id,))
    conn.commit()
    conn.close()
    
    await bot.edit_message_text(chat_id=callback_query.message.chat.id,
                              message_id=callback_query.message.message_id,
                              text=f"✅ Товар \"{product[2]}\" удален.")
    
    await bot.answer_callback_query(callback_query.id, "Товар удален!")

@dp.callback_query_handler(lambda c: c.data == 'my_deals')
async def show_my_deals(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    conn = sqlite3.connect('craazydeals.db')
    cursor = conn.cursor()
    
    # Получаем сделки, где пользователь является покупателем или продавцом
    cursor.execute('''
    SELECT d.deal_id, d.status, d.amount, p.title, 
           CASE WHEN d.buyer_id = ? THEN 'buyer' ELSE 'seller' END AS role,
           CASE WHEN d.buyer_id = ? THEN u2.username ELSE u1.username END AS counterparty
    FROM deals d
    JOIN products p ON d.product_id = p.product_id
    JOIN users u1 ON d.buyer_id = u1.user_id
    JOIN users u2 ON d.seller_id = u2.user_id
    WHERE d.buyer_id = ? OR d.seller_id = ?
    ORDER BY d.created_at DESC
    LIMIT 10
    ''', (user_id, user_id, user_id, user_id))
    
    deals = cursor.fetchall()
    conn.close()
    
    if not deals:
        await bot.edit_message_text(chat_id=callback_query.message.chat.id,
                                  message_id=callback_query.message.message_id,
                                  text="У вас пока нет сделок.")
        return
    
    keyboard = InlineKeyboardMarkup()
    for deal in deals:
        status_emoji = "🟢" if deal[1] == 'completed' else "🟡" if deal[1] == 'sent' else "🔴"
        keyboard.add(InlineKeyboardButton(
            f"{status_emoji} {deal[3]} - {deal[2]}₽ ({deal[4]})",
            callback_data=f"view_deal_{deal[0]}"
        ))
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="back_to_main"))
    
    await bot.edit_message_text(chat_id=callback_query.message.chat.id,
                              message_id=callback_query.message.message_id,
                              text="🤝 Ваши последние сделки:",
                              reply_markup=keyboard)

@dp.callback_query_handler(lambda c: c.data.startswith('view_deal_'))
async def view_deal(callback_query: types.CallbackQuery):
    deal_id = callback_query.data.replace('view_deal_', '')
    deal = get_deal(deal_id)
    user_id = callback_query.from_user.id
    
    if not deal or user_id not in (deal[1], deal[2]):
        await bot.answer_callback_query(callback_query.id, "Сделка не найдена!")
        return
    
    product = get_product(deal[3])
    buyer = get_user(deal[1])
    seller = get_user(deal[2])
    
    status_text = {
        'pending': "Ожидает отправки",
        'sent': "Товар отправлен",
        'completed': "Завершена",
        'refunded': "Деньги возвращены",
        'dispute': "Диспут"
    }.get(deal[5], deal[5])
    
    role = "покупатель" if user_id == deal[1] else "продавец"
    
    text = f"""📝 Сделка #{deal_id}

🛒 Товар: {product[2]}
💰 Сумма: {deal[4]}₽
👤 Продавец: @{seller[1]}
👤 Покупатель: @{buyer[1]}
📅 Дата создания: {deal[6]}
🔄 Статус: {status_text}
🤝 Ваша роль: {role}"""

    keyboard = InlineKeyboardMarkup()
    
    if deal[5] == 'sent' and user_id == deal[1]:  # Покупатель может подтвердить получение
        keyboard.add(InlineKeyboardButton("✅ Подтвердить получение", callback_data=f"confirm_{deal_id}"))
    
    if deal[5] in ('pending', 'sent') and user_id in (deal[1], deal[2]):  # Участники могут открыть диспут
        keyboard.add(InlineKeyboardButton("⚠️ Открыть диспут", callback_data=f"dispute_{deal_id}"))
    
    if deal[5] == 'dispute':
        # Показать историю сообщений в диспуте
        messages = get_dispute_messages(deal_id)
        for msg in messages:
            text += f"\n\n@{msg[2]}: {msg[3]}"
        
        if user_id in (deal[1], deal[2]):
            keyboard.add(InlineKeyboardButton("💬 Ответить в диспуте", callback_data=f"reply_dispute_{deal_id}"))
    
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="my_deals"))
    
    await bot.edit_message_text(chat_id=callback_query.message.chat.id,
                              message_id=callback_query.message.message_id,
                              text=text,
                              reply_markup=keyboard)

@dp.callback_query_handler(lambda c: c.data.startswith('reply_dispute_'))
async def reply_to_dispute(callback_query: types.CallbackQuery):
    deal_id = callback_query.data.replace('reply_dispute_', '')
    deal = get_deal(deal_id)
    user_id = callback_query.from_user.id
    
    if not deal or user_id not in (deal[1], deal[2]):
        await bot.answer_callback_query(callback_query.id, "Сделка не найдена!")
        return
    
    await bot.edit_message_text(chat_id=callback_query.message.chat.id,
                              message_id=callback_query.message.message_id,
                              text="Введите ваше сообщение для диспута:")
    
    await Form.dispute_message.set()
    state = Dispatcher.get_current().current_state()
    await state.update_data(deal_id=deal_id, user_id=user_id)
    
    await bot.answer_callback_query(callback_query.id)

@dp.callback_query_handler(lambda c: c.data == 'back_to_main')
async def back_to_main(callback_query: types.CallbackQuery):
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("🛒 Магазин", callback_data="shop"))
    keyboard.add(InlineKeyboardButton("💰 Мой баланс", callback_data="balance"), 
                InlineKeyboardButton("📊 Мой профиль", callback_data="profile"))
    keyboard.add(InlineKeyboardButton("➕ Добавить товар", callback_data="add_product"))
    keyboard.add(InlineKeyboardButton("📦 Мои товары", callback_data="my_products"))
    keyboard.add(InlineKeyboardButton("🤝 Мои сделки", callback_data="my_deals"))
    
    await bot.edit_message_text(chat_id=callback_query.message.chat.id,
                              message_id=callback_query.message.message_id,
                              text="""👋 Добро пожаловать в CraazyDeals - безопасную площадку для покупки и продажи цифровых товаров!

🔒 Все сделки защищены: деньги замораживаются до подтверждения получения товара
💼 Комиссия системы: 8% от суммы сделки

Выберите действие:""",
                              reply_markup=keyboard)

# Запуск бота
if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)