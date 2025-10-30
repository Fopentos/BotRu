import os
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import random

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton,
    CallbackQuery
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN not found!")
    exit(1)

# Инициализация бота
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode='HTML')
)
dp = Dispatcher(storage=MemoryStorage())

# Состояния для FSM
class TaskCreation(StatesGroup):
    waiting_for_channel = State()
    waiting_for_reward = State()
    waiting_for_description = State()

class SubscriptionCheck(StatesGroup):
    waiting_for_subscription = State()

# База данных SQLite
class Database:
    def __init__(self):
        self.conn = sqlite3.connect('pyara.db', check_same_thread=False)
        self.create_tables()
    
    def create_tables(self):
        cursor = self.conn.cursor()
        
        # Таблица пользователей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance INTEGER DEFAULT 1000,
                total_earned INTEGER DEFAULT 0,
                total_spent INTEGER DEFAULT 0,
                registered_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица заданий
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tasks (
                task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_id INTEGER,
                channel_id TEXT,
                channel_title TEXT,
                channel_username TEXT,
                reward INTEGER,
                description TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_by INTEGER,
                completed_date TIMESTAMP,
                FOREIGN KEY (creator_id) REFERENCES users (user_id)
            )
        ''')
        
        # Таблица выполненных заданий (для предотвращения повторного выполнения)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS completed_tasks (
                user_id INTEGER,
                task_id INTEGER,
                completed_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, task_id)
            )
        ''')
        
        # Таблица подписок (отслеживание кто на кого подписан)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id INTEGER,
                channel_id TEXT,
                subscribed_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, channel_id)
            )
        ''')
        
        self.conn.commit()
    
    def get_user(self, user_id: int):
        cursor = self.conn.cursor()
        cursor.execute(
            'SELECT * FROM users WHERE user_id = ?', 
            (user_id,)
        )
        return cursor.fetchone()
    
    def create_user(self, user_id: int, username: str, first_name: str):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR IGNORE INTO users (user_id, username, first_name, balance) 
            VALUES (?, ?, ?, 1000)
        ''', (user_id, username, first_name))
        self.conn.commit()
    
    def update_balance(self, user_id: int, amount: int):
        cursor = self.conn.cursor()
        cursor.execute(
            'UPDATE users SET balance = balance + ? WHERE user_id = ?',
            (amount, user_id)
        )
        
        if amount > 0:
            cursor.execute(
                'UPDATE users SET total_earned = total_earned + ? WHERE user_id = ?',
                (amount, user_id)
            )
        else:
            cursor.execute(
                'UPDATE users SET total_spent = total_spent + ? WHERE user_id = ?',
                (abs(amount), user_id)
            )
        
        self.conn.commit()
    
    def create_task(self, creator_id: int, channel_id: str, channel_title: str, 
                   channel_username: str, reward: int, description: str):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO tasks 
            (creator_id, channel_id, channel_title, channel_username, reward, description)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (creator_id, channel_id, channel_title, channel_username, reward, description))
        self.conn.commit()
        return cursor.lastrowid
    
    def get_active_tasks(self, exclude_user_id: int = None):
        cursor = self.conn.cursor()
        if exclude_user_id:
            cursor.execute('''
                SELECT t.*, u.username as creator_username 
                FROM tasks t
                JOIN users u ON t.creator_id = u.user_id
                WHERE t.is_active = TRUE 
                AND t.creator_id != ?
                ORDER BY t.created_date DESC
            ''', (exclude_user_id,))
        else:
            cursor.execute('''
                SELECT t.*, u.username as creator_username 
                FROM tasks t
                JOIN users u ON t.creator_id = u.user_id
                WHERE t.is_active = TRUE 
                ORDER BY t.created_date DESC
            ''')
        return cursor.fetchall()
    
    def get_user_tasks(self, user_id: int):
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT * FROM tasks 
            WHERE creator_id = ? 
            ORDER BY created_date DESC
        ''', (user_id,))
        return cursor.fetchall()
    
    def complete_task(self, task_id: int, user_id: int):
        cursor = self.conn.cursor()
        
        # Получаем информацию о задании
        cursor.execute('SELECT * FROM tasks WHERE task_id = ?', (task_id,))
        task = cursor.fetchone()
        
        if not task:
            return False
        
        # Помечаем задание выполненным
        cursor.execute('''
            UPDATE tasks 
            SET is_active = FALSE, completed_by = ?, completed_date = CURRENT_TIMESTAMP
            WHERE task_id = ?
        ''', (user_id, task_id))
        
        # Добавляем в историю выполненных
        cursor.execute('''
            INSERT OR IGNORE INTO completed_tasks (user_id, task_id)
            VALUES (?, ?)
        ''', (user_id, task_id))
        
        # Начисляем вознаграждение исполнителю
        reward = task[5]  # reward находится в 6-й колонке (индекс 5)
        self.update_balance(user_id, reward)
        
        self.conn.commit()
        return True
    
    def has_completed_task(self, user_id: int, task_id: int):
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT 1 FROM completed_tasks 
            WHERE user_id = ? AND task_id = ?
        ''', (user_id, task_id))
        return cursor.fetchone() is not None
    
    def add_subscription(self, user_id: int, channel_id: str):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR IGNORE INTO subscriptions (user_id, channel_id)
            VALUES (?, ?)
        ''', (user_id, channel_id))
        self.conn.commit()
    
    def get_user_subscriptions(self, user_id: int):
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT channel_id FROM subscriptions WHERE user_id = ?
        ''', (user_id,))
        return [row[0] for row in cursor.fetchall()]

# Инициализация базы данных
db = Database()

# Клавиатуры
def get_main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💰 Баланс"), KeyboardButton(text="📊 Статистика")],
            [KeyboardButton(text="➕ Создать задание"), KeyboardButton(text="📋 Активные задания")],
            [KeyboardButton(text="🎯 Мои задания"), KeyboardButton(text="❓ Помощь")]
        ],
        resize_keyboard=True
    )

def get_cancel_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True
    )

# Команда /start
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user = message.from_user
    
    # Регистрируем пользователя если нужно
    db.create_user(user.id, user.username, user.first_name)
    
    await message.answer(
        f"👋 <b>Добро пожаловать, {user.first_name}!</b>\n\n"
        "🤝 <b>Бот взаимного пиара</b>\n\n"
        "💷 <b>Стартовый баланс:</b> 1000 💷\n\n"
        "🎯 <b>Как это работает:</b>\n"
        "• Создавайте задания на подписку за валюту\n"
        "• Выполняйте задания других пользователей\n"
        "• Зарабатывайте валюту для продвижения своих каналов\n\n"
        "🚀 <b>Начните с создания задания или просмотра доступных заданий!</b>",
        reply_markup=get_main_keyboard()
    )

# Баланс
@dp.message(F.text == "💰 Баланс")
async def show_balance(message: types.Message):
    user_data = db.get_user(message.from_user.id)
    if user_data:
        balance = user_data[3]  # balance в 4-й колонке
        total_earned = user_data[4]  # total_earned в 5-й колонке
        total_spent = user_data[5]  # total_spent в 6-й колонке
        
        await message.answer(
            f"💰 <b>Ваш баланс:</b> {balance} 💷\n\n"
            f"📈 <b>Всего заработано:</b> {total_earned} 💷\n"
            f"📉 <b>Всего потрачено:</b> {total_spent} 💷\n\n"
            f"💡 <b>Совет:</b> Выполняйте задания других пользователей чтобы увеличить баланс!"
        )

# Статистика
@dp.message(F.text == "📊 Статистика")
async def show_stats(message: types.Message):
    user_data = db.get_user(message.from_user.id)
    user_tasks = db.get_user_tasks(message.from_user.id)
    
    if user_data:
        balance = user_data[3]
        total_earned = user_data[4]
        total_spent = user_data[5]
        
        # Статистика по заданиям
        active_tasks = len([t for t in user_tasks if t[7]])  # is_active в 8-й колонке
        completed_tasks = len([t for t in user_tasks if not t[7] and t[9]])  # completed_by в 10-й колонке
        
        await message.answer(
            f"📊 <b>Ваша статистика:</b>\n\n"
            f"💰 <b>Баланс:</b> {balance} 💷\n"
            f"📈 <b>Заработано всего:</b> {total_earned} 💷\n"
            f"📉 <b>Потрачено всего:</b> {total_spent} 💷\n\n"
            f"🎯 <b>Задания:</b>\n"
            f"• Активных: {active_tasks}\n"
            f"• Выполненных: {completed_tasks}\n\n"
            f"🚀 <b>Продолжайте в том же духе!</b>"
        )

# Начало создания задания
@dp.message(F.text == "➕ Создать задание")
async def start_task_creation(message: types.Message, state: FSMContext):
    user_data = db.get_user(message.from_user.id)
    if not user_data:
        await message.answer("❌ Сначала зарегистрируйтесь через /start")
        return
    
    balance = user_data[3]
    if balance < 100:
        await message.answer(
            "❌ <b>Недостаточно средств!</b>\n\n"
            f"💰 Ваш баланс: {balance} 💷\n"
            f"💡 Минимальная стоимость задания: 100 💷\n\n"
            "🎯 Выполните задания других пользователей чтобы пополнить баланс!",
            reply_markup=get_main_keyboard()
        )
        return
    
    await message.answer(
        "📝 <b>Создание нового задания</b>\n\n"
        "🔗 <b>Шаг 1:</b> Перешлите мне любое сообщение из канала или отправьте @username канала\n\n"
        "⚠️ <b>Важно:</b> Бот должен быть администратором канала для проверки подписок!",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(TaskCreation.waiting_for_channel)

# Обработка пересланного сообщения или username канала
@dp.message(TaskCreation.waiting_for_channel)
async def process_channel(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Создание задания отменено", reply_markup=get_main_keyboard())
        return
    
    channel_info = None
    
    # Обработка пересланного сообщения из канала
    if message.forward_from_chat and message.forward_from_chat.type == "channel":
        chat = message.forward_from_chat
        channel_info = {
            'id': str(chat.id),
            'title': chat.title,
            'username': chat.username
        }
    
    # Обработка username канала
    elif message.text and message.text.startswith('@'):
        username = message.text[1:]
        try:
            chat = await bot.get_chat(f"@{username}")
            if chat.type == "channel":
                channel_info = {
                    'id': str(chat.id),
                    'title': chat.title,
                    'username': chat.username
                }
        except Exception as e:
            await message.answer("❌ Не удалось найти канал. Проверьте правильность @username")
            return
    
    if not channel_info:
        await message.answer("❌ Пожалуйста, перешлите сообщение из канала или отправьте @username канала")
        return
    
    # Проверяем, является ли бот администратором канала
    try:
        bot_member = await bot.get_chat_member(int(channel_info['id']), bot.id)
        if not bot_member.is_chat_admin():
            await message.answer(
                "❌ <b>Бот не является администратором этого канала!</b>\n\n"
                "Добавьте бота как администратора с правом просмотра участников, "
                "чтобы можно было проверять подписки.",
                reply_markup=get_main_keyboard()
            )
            await state.clear()
            return
    except Exception as e:
        await message.answer(
            f"❌ <b>Ошибка доступа к каналу:</b> {str(e)}\n\n"
            "Убедитесь что бот добавлен как администратор.",
            reply_markup=get_main_keyboard()
        )
        await state.clear()
        return
    
    await state.update_data(channel_info=channel_info)
    
    await message.answer(
        f"✅ <b>Канал получен:</b> {channel_info['title']}\n\n"
        f"💷 <b>Шаг 2:</b> Введите сумму вознаграждения (от 100 до 5000 💷)\n\n"
        f"💰 <b>Ваш текущий баланс:</b> {db.get_user(message.from_user.id)[3]} 💷",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(TaskCreation.waiting_for_reward)

# Обработка суммы вознаграждения
@dp.message(TaskCreation.waiting_for_reward)
async def process_reward(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Создание задания отменено", reply_markup=get_main_keyboard())
        return
    
    try:
        reward = int(message.text)
        user_data = db.get_user(message.from_user.id)
        balance = user_data[3]
        
        if reward < 100:
            await message.answer("❌ Минимальная сумма вознаграждения: 100 💷")
            return
        
        if reward > 5000:
            await message.answer("❌ Максимальная сумма вознаграждения: 5000 💷")
            return
        
        if reward > balance:
            await message.answer(
                f"❌ <b>Недостаточно средств!</b>\n\n"
                f"💰 Ваш баланс: {balance} 💷\n"
                f"💸 Требуется: {reward} 💷\n\n"
                "🎯 Выполните задания других пользователей чтобы пополнить баланс!"
            )
            return
        
        await state.update_data(reward=reward)
        
        await message.answer(
            f"💰 <b>Сумма вознаграждения:</b> {reward} 💷\n\n"
            "📝 <b>Шаг 3:</b> Введите описание задания (необязательно)\n\n"
            "Пример: \"Подпишитесь на канал о криптовалюте и технологиях\"",
            reply_markup=get_cancel_keyboard()
        )
        await state.set_state(TaskCreation.waiting_for_description)
        
    except ValueError:
        await message.answer("❌ Пожалуйста, введите корректную сумму (только цифры)")

# Обработка описания и создание задания
@dp.message(TaskCreation.waiting_for_description)
async def process_description(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Создание задания отменено", reply_markup=get_main_keyboard())
        return
    
    data = await state.get_data()
    channel_info = data['channel_info']
    reward = data['reward']
    description = message.text if message.text != "❌ Отмена" else "Подписка на канал"
    
    # Списываем средства
    db.update_balance(message.from_user.id, -reward)
    
    # Создаем задание
    task_id = db.create_task(
        creator_id=message.from_user.id,
        channel_id=channel_info['id'],
        channel_title=channel_info['title'],
        channel_username=channel_info.get('username'),
        reward=reward,
        description=description
    )
    
    await state.clear()
    
    await message.answer(
        f"✅ <b>Задание успешно создано!</b>\n\n"
        f"📺 <b>Канал:</b> {channel_info['title']}\n"
        f"💷 <b>Вознаграждение:</b> {reward} 💷\n"
        f"📝 <b>Описание:</b> {description}\n\n"
        f"🆔 <b>ID задания:</b> {task_id}\n\n"
        f"💰 <b>Новый баланс:</b> {db.get_user(message.from_user.id)[3]} 💷\n\n"
        f"👥 Теперь другие пользователи смогут выполнить ваше задание!",
        reply_markup=get_main_keyboard()
    )

# Список активных заданий
@dp.message(F.text == "📋 Активные задания")
async def show_active_tasks(message: types.Message):
    tasks = db.get_active_tasks(exclude_user_id=message.from_user.id)
    
    if not tasks:
        await message.answer(
            "😔 <b>Нет доступных заданий</b>\n\n"
            "В данный момент нет активных заданий от других пользователей.\n\n"
            "💡 <b>Совет:</b> Создайте свое задание чтобы привлечь подписчиков!",
            reply_markup=get_main_keyboard()
        )
        return
    
    await message.answer(
        f"🎯 <b>Доступные задания:</b> {len(tasks)}\n\n"
        "Выберите задание для выполнения:"
    )
    
    for task in tasks[:10]:  # Показываем первые 10 заданий
        task_id = task[0]
        channel_title = task[3]
        reward = task[5]
        description = task[6] or "Подписка на канал"
        creator_username = task[11] or "Пользователь"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🎯 Выполнить за {reward} 💷", callback_data=f"do_task_{task_id}")]
        ])
        
        await message.answer(
            f"📺 <b>Канал:</b> {channel_title}\n"
            f"💷 <b>Вознаграждение:</b> {reward} 💷\n"
            f"👤 <b>Создатель:</b> {creator_username}\n"
            f"📝 <b>Описание:</b> {description}",
            reply_markup=keyboard
        )

# Мои задания
@dp.message(F.text == "🎯 Мои задания")
async def show_my_tasks(message: types.Message):
    tasks = db.get_user_tasks(message.from_user.id)
    
    if not tasks:
        await message.answer(
            "📝 <b>У вас пока нет заданий</b>\n\n"
            "Создайте первое задание чтобы привлечь подписчиков на ваши каналы!",
            reply_markup=get_main_keyboard()
        )
        return
    
    active_tasks = [t for t in tasks if t[7]]  # is_active
    completed_tasks = [t for t in tasks if not t[7] and t[9]]  # completed_by
    
    await message.answer(
        f"📋 <b>Ваши задания:</b>\n\n"
        f"🟢 Активных: {len(active_tasks)}\n"
        f"✅ Выполненных: {len(completed_tasks)}"
    )
    
    for task in tasks[:10]:  # Показываем первые 10
        task_id = task[0]
        channel_title = task[3]
        reward = task[5]
        is_active = task[7]
        completed_by = task[9]
        
        status = "🟢 Активно" if is_active else "✅ Выполнено"
        
        await message.answer(
            f"📺 <b>Канал:</b> {channel_title}\n"
            f"💷 <b>Вознаграждение:</b> {reward} 💷\n"
            f"🆔 <b>ID:</b> {task_id}\n"
            f"📊 <b>Статус:</b> {status}"
        )

# Обработка выполнения задания
@dp.callback_query(F.data.startswith("do_task_"))
async def process_task_completion(callback: CallbackQuery, state: FSMContext):
    task_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id
    
    # Проверяем, не выполнял ли пользователь уже это задание
    if db.has_completed_task(user_id, task_id):
        await callback.answer("❌ Вы уже выполняли это задание!", show_alert=True)
        return
    
    # Получаем информацию о задании
    tasks = db.get_active_tasks()
    task = next((t for t in tasks if t[0] == task_id), None)
    
    if not task:
        await callback.answer("❌ Задание уже выполнено или удалено!", show_alert=True)
        return
    
    channel_id = task[2]
    channel_title = task[3]
    channel_username = task[4]
    reward = task[5]
    
    # Проверяем подписку пользователя
    try:
        chat_member = await bot.get_chat_member(int(channel_id), user_id)
        is_subscribed = chat_member.status in ['member', 'administrator', 'creator']
        
        if is_subscribed:
            # Пользователь уже подписан
            db.complete_task(task_id, user_id)
            db.add_subscription(user_id, channel_id)
            
            user_data = db.get_user(user_id)
            new_balance = user_data[3]
            
            await callback.message.edit_text(
                f"✅ <b>Задание выполнено!</b>\n\n"
                f"📺 <b>Канал:</b> {channel_title}\n"
                f"💷 <b>Получено:</b> {reward} 💷\n\n"
                f"💰 <b>Ваш баланс:</b> {new_balance} 💷\n\n"
                f"🎯 Продолжайте выполнять задания чтобы заработать больше!",
                reply_markup=None
            )
        else:
            # Просим подписаться
            channel_link = f"https://t.me/{channel_username}" if channel_username else f"https://t.me/c/{channel_id[4:]}"
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📺 Перейти в канал", url=channel_link)],
                [InlineKeyboardButton(text="✅ Я подписался", callback_data=f"check_sub_{task_id}")]
            ])
            
            await callback.message.edit_text(
                f"📺 <b>Для выполнения задания:</b>\n\n"
                f"1. Подпишитесь на канал: {channel_title}\n"
                f"2. Нажмите кнопку '✅ Я подписался' для проверки\n\n"
                f"💷 <b>Вознаграждение:</b> {reward} 💷",
                reply_markup=keyboard
            )
            
    except Exception as e:
        await callback.answer(f"❌ Ошибка проверки подписки: {str(e)}", show_alert=True)

# Проверка подписки после того как пользователь утверждает что подписался
@dp.callback_query(F.data.startswith("check_sub_"))
async def check_subscription(callback: CallbackQuery):
    task_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id
    
    # Получаем информацию о задании
    tasks = db.get_active_tasks()
    task = next((t for t in tasks if t[0] == task_id), None)
    
    if not task:
        await callback.answer("❌ Задание уже выполнено или удалено!", show_alert=True)
        return
    
    channel_id = task[2]
    channel_title = task[3]
    reward = task[5]
    
    try:
        # Проверяем подписку
        chat_member = await bot.get_chat_member(int(channel_id), user_id)
        is_subscribed = chat_member.status in ['member', 'administrator', 'creator']
        
        if is_subscribed:
            # Задание выполнено успешно
            db.complete_task(task_id, user_id)
            db.add_subscription(user_id, channel_id)
            
            user_data = db.get_user(user_id)
            new_balance = user_data[3]
            
            await callback.message.edit_text(
                f"✅ <b>Задание выполнено!</b>\n\n"
                f"📺 <b>Канал:</b> {channel_title}\n"
                f"💷 <b>Получено:</b> {reward} 💷\n\n"
                f"💰 <b>Ваш баланс:</b> {new_balance} 💷\n\n"
                f"🎯 Продолжайте выполнять задания чтобы заработать больше!",
                reply_markup=None
            )
        else:
            await callback.answer("❌ Вы еще не подписались на канал! Подпишитесь и попробуйте снова.", show_alert=True)
            
    except Exception as e:
        await callback.answer(f"❌ Ошибка проверки подписки: {str(e)}", show_alert=True)

# Помощь
@dp.message(F.text == "❓ Помощь")
async def show_help(message: types.Message):
    await message.answer(
        "🤝 <b>Помощь по боту взаимного пиара</b>\n\n"
        "💷 <b>Экономика:</b>\n"
        "• Стартовый баланс: 1000 💷\n"
        "• Минимальная цена задания: 100 💷\n"
        "• Максимальная цена задания: 5000 💷\n\n"
        "🎯 <b>Создание задания:</b>\n"
        "1. Нажмите '➕ Создать задание'\n"
        "2. Перешлите сообщение из вашего канала\n"
        "3. Укажите сумму вознаграждения\n"
        "4. Добавьте описание (необязательно)\n\n"
        "📋 <b>Выполнение заданий:</b>\n"
        "1. Нажмите '📋 Активные задания'\n"
        "2. Выберите задание\n"
        "3. Подпишитесь на канал\n"
        "4. Получите вознаграждение\n\n"
        "💡 <b>Советы:</b>\n"
        "• Устанавливайте адекватные цены за подписки\n"
        "• Выполняйте задания чтобы пополнить баланс\n"
        "• Привлекайте друзей для большего охвата\n\n"
        "📞 <b>Поддержка:</b> @ваш_аккаунт",
        reply_markup=get_main_keyboard()
    )

# Запуск бота
async def main():
    logger.info("🚀 Бот взаимного пиара запускается...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
