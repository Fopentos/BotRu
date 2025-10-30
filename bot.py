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

# Временная база данных в памяти
class Database:
    def __init__(self):
        self.users = {}
        self.tasks = []
        self.completed_tasks = set()  # храним (user_id, task_id)
        self.subscriptions = set()    # храним (user_id, channel_id)
        self.task_counter = 1
    
    async def connect(self):
        logger.info("✅ Используется база данных в памяти")
        pass
    
    async def get_user(self, user_id: int):
        return self.users.get(user_id)
    
    async def create_user(self, user_id: int, username: str, first_name: str):
        if user_id not in self.users:
            self.users[user_id] = {
                'user_id': user_id,
                'username': username,
                'first_name': first_name,
                'balance': 1000,
                'total_earned': 0,
                'total_spent': 0,
                'registered_date': datetime.now()
            }
    
    async def update_balance(self, user_id: int, amount: int):
        if user_id in self.users:
            self.users[user_id]['balance'] += amount
            if amount > 0:
                self.users[user_id]['total_earned'] += amount
            else:
                self.users[user_id]['total_spent'] += abs(amount)
    
    async def create_task(self, creator_id: int, channel_id: str, channel_title: str, 
                         channel_username: str, reward: int, description: str):
        task_id = self.task_counter
        self.task_counter += 1
        task = {
            'task_id': task_id,
            'creator_id': creator_id,
            'channel_id': channel_id,
            'channel_title': channel_title,
            'channel_username': channel_username,
            'reward': reward,
            'description': description,
            'is_active': True,
            'created_date': datetime.now(),
            'completed_by': None,
            'completed_date': None
        }
        self.tasks.append(task)
        return task_id
    
    async def get_active_tasks(self, exclude_user_id: int = None):
        active_tasks = [t for t in self.tasks if t['is_active']]
        if exclude_user_id:
            active_tasks = [t for t in active_tasks if t['creator_id'] != exclude_user_id]
        # Добавляем имя создателя
        for task in active_tasks:
            user = self.users.get(task['creator_id'])
            task['creator_username'] = user.get('username', 'Пользователь') if user else 'Пользователь'
        return active_tasks
    
    async def get_user_tasks(self, user_id: int):
        return [t for t in self.tasks if t['creator_id'] == user_id]
    
    async def complete_task(self, task_id: int, user_id: int):
        task = next((t for t in self.tasks if t['task_id'] == task_id), None)
        if not task:
            return False
        task['is_active'] = False
        task['completed_by'] = user_id
        task['completed_date'] = datetime.now()
        self.completed_tasks.add((user_id, task_id))
        # Начисляем вознаграждение
        await self.update_balance(user_id, task['reward'])
        return True
    
    async def has_completed_task(self, user_id: int, task_id: int):
        return (user_id, task_id) in self.completed_tasks
    
    async def add_subscription(self, user_id: int, channel_id: str):
        self.subscriptions.add((user_id, channel_id))

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
    await db.create_user(user.id, user.username, user.first_name)
    
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
    user_data = await db.get_user(message.from_user.id)
    if user_data:
        balance = user_data['balance']
        total_earned = user_data['total_earned']
        total_spent = user_data['total_spent']
        
        await message.answer(
            f"💰 <b>Ваш баланс:</b> {balance} 💷\n\n"
            f"📈 <b>Всего заработано:</b> {total_earned} 💷\n"
            f"📉 <b>Всего потрачено:</b> {total_spent} 💷\n\n"
            f"💡 <b>Совет:</b> Выполняйте задания других пользователей чтобы увеличить баланс!"
        )

# Статистика
@dp.message(F.text == "📊 Статистика")
async def show_stats(message: types.Message):
    user_data = await db.get_user(message.from_user.id)
    user_tasks = await db.get_user_tasks(message.from_user.id)
    
    if user_data:
        balance = user_data['balance']
        total_earned = user_data['total_earned']
        total_spent = user_data['total_spent']
        
        # Статистика по заданиям
        active_tasks = len([t for t in user_tasks if t['is_active']])
        completed_tasks = len([t for t in user_tasks if not t['is_active'] and t['completed_by']])
        
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
    user_data = await db.get_user(message.from_user.id)
    if not user_data:
        await message.answer("❌ Сначала зарегистрируйтесь через /start")
        return
    
    balance = user_data['balance']
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
    # Используем channel_info вместо forwarded_chat
    bot_member = await bot.get_chat_member(int(channel_info['id']), bot.id)
    
    # Правильная проверка: является ли бот администратором
    if not isinstance(bot_member, types.ChatMemberAdministrator):
        await message.answer(
            f"❌ <b>Бот не является администратором канала '{channel_info['title']}'!</b>\n\n"
            "Добавьте бота как администратора с правом просмотра участников, "
            "чтобы можно было проверять подписки.",
            reply_markup=get_main_keyboard()
        )
        await state.clear()
        return
    
    # Проверяем конкретные права
    missing_permissions = []
    if not bot_member.can_invite_users:
        missing_permissions.append("❌ Приглашать пользователей")
    # Для принятия заявок на вступление нужно право can_invite_users
    if not bot_member.can_invite_users:
        missing_permissions.append("❌ Одобрять заявки")
    
    if missing_permissions:
        await message.answer(
            f"❌ <b>Недостаточно прав в канале '{channel_info['title']}'!</b>\n\n"
            "Боту нужны ВСЕ эти права:\n" +
            "\n".join(missing_permissions) +
            "\n\nОбновите права бота и попробуйте снова."
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
    f"💰 <b>Ваш текущий баланс:</b> {(await db.get_user(message.from_user.id))['balance']} 💷",
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
        user_data = await db.get_user(message.from_user.id)
        balance = user_data['balance']
        
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
    await db.update_balance(message.from_user.id, -reward)
    
    # Создаем задание
    task_id = await db.create_task(
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
        f"💰 <b>Новый баланс:</b> {(await db.get_user(message.from_user.id))['balance']} 💷\n\n"
        f"👥 Теперь другие пользователи смогут выполнить ваше задание!",
        reply_markup=get_main_keyboard()
    )

# Список активных заданий
@dp.message(F.text == "📋 Активные задания")
async def show_active_tasks(message: types.Message):
    tasks = await db.get_active_tasks(exclude_user_id=message.from_user.id)
    
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
        task_id = task['task_id']
        channel_title = task['channel_title']
        reward = task['reward']
        description = task['description'] or "Подписка на канал"
        creator_username = task['creator_username'] or "Пользователь"
        
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
    tasks = await db.get_user_tasks(message.from_user.id)
    
    if not tasks:
        await message.answer(
            "📝 <b>У вас пока нет заданий</b>\n\n"
            "Создайте первое задание чтобы привлечь подписчиков на ваши каналы!",
            reply_markup=get_main_keyboard()
        )
        return
    
    active_tasks = [t for t in tasks if t['is_active']]
    completed_tasks = [t for t in tasks if not t['is_active'] and t['completed_by']]
    
    await message.answer(
        f"📋 <b>Ваши задания:</b>\n\n"
        f"🟢 Активных: {len(active_tasks)}\n"
        f"✅ Выполненных: {len(completed_tasks)}"
    )
    
    for task in tasks[:10]:  # Показываем первые 10
        task_id = task['task_id']
        channel_title = task['channel_title']
        reward = task['reward']
        is_active = task['is_active']
        completed_by = task['completed_by']
        
        status = "🟢 Активно" if is_active else "✅ Выполнено"
        
        await message.answer(
            f"📺 <b>Канал:</b> {channel_title}\n"
            f"💷 <b>Вознаграждение:</b> {reward} 💷\n"
            f"🆔 <b>ID:</b> {task_id}\n"
            f"📊 <b>Статус:</b> {status}"
        )

# Обработка выполнения задания
@dp.callback_query(F.data.startswith("do_task_"))
async def process_task_completion(callback: CallbackQuery):
    task_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id
    
    # Проверяем, не выполнял ли пользователь уже это задание
    if await db.has_completed_task(user_id, task_id):
        await callback.answer("❌ Вы уже выполняли это задание!", show_alert=True)
        return
    
    # Получаем информацию о задании
    tasks = await db.get_active_tasks()
    task = next((t for t in tasks if t['task_id'] == task_id), None)
    
    if not task:
        await callback.answer("❌ Задание уже выполнено или удалено!", show_alert=True)
        return
    
    channel_id = task['channel_id']
    channel_title = task['channel_title']
    channel_username = task['channel_username']
    reward = task['reward']
    
    # Проверяем подписку пользователя
    try:
        chat_member = await bot.get_chat_member(int(channel_id), user_id)
        is_subscribed = chat_member.status in ['member', 'administrator', 'creator']
        
        if is_subscribed:
            # Пользователь уже подписан
            await db.complete_task(task_id, user_id)
            await db.add_subscription(user_id, channel_id)
            
            user_data = await db.get_user(user_id)
            new_balance = user_data['balance']
            
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
    tasks = await db.get_active_tasks()
    task = next((t for t in tasks if t['task_id'] == task_id), None)
    
    if not task:
        await callback.answer("❌ Задание уже выполнено или удалено!", show_alert=True)
        return
    
    channel_id = task['channel_id']
    channel_title = task['channel_title']
    reward = task['reward']
    
    try:
        # Проверяем подписку
        chat_member = await bot.get_chat_member(int(channel_id), user_id)
        is_subscribed = chat_member.status in ['member', 'administrator', 'creator']
        
        if is_subscribed:
            # Задание выполнено успешно
            await db.complete_task(task_id, user_id)
            await db.add_subscription(user_id, channel_id)
            
            user_data = await db.get_user(user_id)
            new_balance = user_data['balance']
            
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
    # Подключаемся к базе данных
    await db.connect()
    logger.info("✅ Бот запускается с базой данных в памяти")
    
    # Запускаем бота
    logger.info("🚀 Бот взаимного пиара запускается...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
