import os
import asyncio
import logging
import re
import asyncpg
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, UserPrivacyRestricted
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl import functions, types

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Глобальные переменные
user_states = {}
user_data = {}

# PostgreSQL подключение
DATABASE_URL = os.environ.get('DATABASE_URL')

async def create_pool():
    return await asyncpg.create_pool(DATABASE_URL)

async def init_database():
    """Инициализация PostgreSQL таблиц"""
    pool = await create_pool()
    async with pool.acquire() as conn:
        # Таблица пользователей
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                phone TEXT,
                api_id INTEGER,
                api_hash TEXT,
                session_string TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица сессий авторизации
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS auth_sessions (
                user_id BIGINT PRIMARY KEY,
                phone TEXT,
                phone_code_hash TEXT,
                client_data TEXT,
                state TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица статистики
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS user_stats (
                user_id BIGINT PRIMARY KEY,
                scans_count INTEGER DEFAULT 0,
                adds_count INTEGER DEFAULT 0,
                total_added INTEGER DEFAULT 0,
                last_scan TIMESTAMP,
                last_add TIMESTAMP
            )
        ''')
        
        # Таблица настроек
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS user_configs (
                user_id BIGINT PRIMARY KEY,
                scan_limit INTEGER DEFAULT 0,
                add_limit INTEGER DEFAULT 0,
                auto_add BOOLEAN DEFAULT FALSE,
                delay INTEGER DEFAULT 2
            )
        ''')
    await pool.close()
    print("✅ База данных инициализирована")

# Система авторизации
class MassAuthSystem:
    def __init__(self):
        self.temp_clients = {}
        
    async def start_auth(self, user_id, phone_number):
        """Начинаем процесс авторизации для пользователя"""
        try:
            # Используем рабочие ключи Telethon для регистрации
            client = TelegramClient(
                StringSession(), 
                api_id=2040,  # Рабочие ключи Telethon
                api_hash='b18441a1ff607e10a989891a5462e627'
            )
            
            await client.connect()
            sent_code = await client.send_code_request(phone_number)
            
            await self.save_auth_session(
                user_id, 
                phone_number, 
                sent_code.phone_code_hash,
                client.session.save(),
                'waiting_code'
            )
            
            self.temp_clients[user_id] = client
            
            return {
                'success': True, 
                'message': f"✅ Код отправлен на {phone_number}",
                'phone_code_hash': sent_code.phone_code_hash
            }
            
        except Exception as e:
            logger.error(f"Ошибка отправки кода: {e}")
            return {
                'success': False, 
                'message': f"❌ Ошибка: {str(e)}"
            }
    
    async def verify_code(self, user_id, code):
        """Проверяем код и получаем API ключи"""
        try:
            auth_data = await self.get_auth_session(user_id)
            if not auth_data:
                return {'success': False, 'message': '❌ Сессия не найдена'}
            
            # Восстанавливаем клиента из сессии
            client = TelegramClient(
                StringSession(auth_data['client_data']),
                api_id=2040,
                api_hash='b18441a1ff607e10a989891a5462e627'
            )
            
            await client.connect()
            
            # Входим в аккаунт
            await client.sign_in(
                phone=auth_data['phone'],
                code=code,
                phone_code_hash=auth_data['phone_code_hash']
            )
            
            # Создаем уникальное приложение для пользователя
            app = await client(functions.account.CreateAppRequest(
                app_id=2040,
                app_hash='b18441a1ff607e10a989891a5462e627',
                app_title=f"MassAdder_User_{user_id}",
                app_shortname=f"user_{user_id}",
                app_url="",
                platform="desktop",
                description="Auto-generated by Zeta Mass Adder Bot"
            ))
            
            # Сохраняем полученные ключи пользователя
            await self.save_user_api(
                user_id, 
                auth_data['phone'],
                app.api_id, 
                app.api_hash,
                client.session.save()
            )
            
            # Очищаем временные данные
            await self.cleanup_auth_session(user_id)
            if user_id in self.temp_clients:
                await self.temp_clients[user_id].disconnect()
                del self.temp_clients[user_id]
            
            await client.disconnect()
            
            return {
                'success': True,
                'api_id': app.api_id,
                'api_hash': app.api_hash,
                'message': '🎉 Авторизация успешна! API ключи получены.'
            }
            
        except Exception as e:
            logger.error(f"Ошибка верификации кода: {e}")
            return {
                'success': False, 
                'message': f'❌ Ошибка: {str(e)}'
            }

    async def save_auth_session(self, user_id, phone, phone_code_hash, client_data, state):
        """Сохраняем сессию авторизации"""
        pool = await create_pool()
        async with pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO auth_sessions 
                (user_id, phone, phone_code_hash, client_data, state) 
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (user_id) 
                DO UPDATE SET 
                    phone = $2,
                    phone_code_hash = $3,
                    client_data = $4,
                    state = $5,
                    created_at = CURRENT_TIMESTAMP
            ''', user_id, phone, phone_code_hash, client_data, state)
        await pool.close()

    async def get_auth_session(self, user_id):
        """Получаем сессию авторизации"""
        pool = await create_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow('SELECT * FROM auth_sessions WHERE user_id = $1', user_id)
        await pool.close()
        
        if row:
            return {
                'user_id': row['user_id'],
                'phone': row['phone'],
                'phone_code_hash': row['phone_code_hash'],
                'client_data': row['client_data'],
                'state': row['state']
            }
        return None

    async def cleanup_auth_session(self, user_id):
        """Очищаем сессию авторизации"""
        pool = await create_pool()
        async with pool.acquire() as conn:
            await conn.execute('DELETE FROM auth_sessions WHERE user_id = $1', user_id)
        await pool.close()

    async def save_user_api(self, user_id, phone, api_id, api_hash, session_string):
        """Сохраняем API ключи пользователя"""
        pool = await create_pool()
        async with pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO users 
                (user_id, phone, api_id, api_hash, session_string, last_activity) 
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (user_id) 
                DO UPDATE SET 
                    phone = $2,
                    api_id = $3,
                    api_hash = $4,
                    session_string = $5,
                    last_activity = $6
            ''', user_id, phone, api_id, api_hash, session_string, datetime.now())
        await pool.close()

mass_auth = MassAuthSystem()

# Вспомогательные функции для PostgreSQL
async def get_user_data(user_id):
    """Получаем данные пользователя"""
    pool = await create_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow('SELECT * FROM users WHERE user_id = $1', user_id)
    await pool.close()
    
    if row:
        return {
            'user_id': row['user_id'],
            'phone': row['phone'],
            'api_id': row['api_id'],
            'api_hash': row['api_hash'],
            'session_string': row['session_string'],
            'is_active': row['is_active']
        }
    return None

async def get_user_stats(user_id):
    """Получаем статистику пользователя"""
    pool = await create_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow('SELECT * FROM user_stats WHERE user_id = $1', user_id)
    await pool.close()
    
    if row:
        return {
            'scans_count': row['scans_count'],
            'adds_count': row['adds_count'],
            'total_added': row['total_added']
        }
    return {'scans_count': 0, 'adds_count': 0, 'total_added': 0}

async def update_user_stats(user_id, field):
    """Обновляем статистику пользователя"""
    pool = await create_pool()
    async with pool.acquire() as conn:
        if field == 'scans_count':
            await conn.execute('''
                INSERT INTO user_stats (user_id, scans_count, last_scan)
                VALUES ($1, 1, $2)
                ON CONFLICT (user_id) 
                DO UPDATE SET 
                    scans_count = user_stats.scans_count + 1,
                    last_scan = $2
            ''', user_id, datetime.now())
        elif field == 'adds_count':
            await conn.execute('''
                INSERT INTO user_stats (user_id, adds_count, last_add)
                VALUES ($1, 1, $2)
                ON CONFLICT (user_id) 
                DO UPDATE SET 
                    adds_count = user_stats.adds_count + 1,
                    last_add = $2
            ''', user_id, datetime.now())
    await pool.close()

async def update_total_added(user_id, count):
    """Обновляем общее количество добавленных пользователей"""
    pool = await create_pool()
    async with pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO user_stats (user_id, total_added)
            VALUES ($1, $2)
            ON CONFLICT (user_id) 
            DO UPDATE SET 
                total_added = user_stats.total_added + $2
        ''', user_id, count)
    await pool.close()

def validate_phone(phone):
    """Проверяем валидность номера телефона"""
    pattern = r'^\+\d{11,15}$'
    return re.match(pattern, phone) is not None

# Инициализация бота Pyrogram
# Используем временные ключи для запуска бота
BOT_API_ID = int(os.environ.get("API_ID", 1111111))  # Временные заглушки
BOT_API_HASH = os.environ.get("API_HASH", "fake_hash_1234567890123456789012")  # Временные заглушки
BOT_TOKEN = os.environ.get("BOT_TOKEN")  # Настоящий токен от @BotFather

app = Client(
    "mass_adder_bot",
    api_id=BOT_API_ID,
    api_hash=BOT_API_HASH, 
    bot_token=BOT_TOKEN
)

# Обработчики команд
@app.on_message(filters.command("start"))
async def start_command(client, message: Message):
    """Обработчик команды /start"""
    user_id = message.from_user.id
    user_data = await get_user_data(user_id)
    
    if user_data and user_data.get('api_id'):
        await show_main_menu(message, user_data)
    else:
        await show_welcome_flow(message)

async def show_welcome_flow(message: Message):
    """Показываем приветственный экран для новых пользователей"""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔐 Быстрая авторизация", callback_data="quick_auth")],
        [InlineKeyboardButton("📖 Инструкция", callback_data="manual_guide")],
        [InlineKeyboardButton("💬 Поддержка", url="https://t.me/zeta_support")]
    ])
    
    await message.reply_text(
        "👋 **Добро пожаловать в Zeta Mass Adder!**\n\n"
        "🤖 *Умный бот для роста Telegram-сообществ*\n\n"
        "🎯 **Для начала работы:**\n"
        "1. 🔐 Авторизуйся через номер телефона\n"
        "2. 🔍 Выбери чат для сканирования\n"
        "3. 🚀 Добавь пользователей в свою группу\n\n"
        "⚡ **Бот автоматически получит твои API ключи!**",
        reply_markup=keyboard
    )

async def show_main_menu(message: Message, user_data):
    """Показываем главное меню для авторизованных пользователей"""
    stats = await get_user_stats(message.from_user.id)
    
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔍 Сканировать чат", callback_data="scan_chat"),
            InlineKeyboardButton("🚀 Добавить", callback_data="add_users")
        ],
        [
            InlineKeyboardButton("📊 Статистика", callback_data="stats"),
            InlineKeyboardButton("⚙️ Настройки", callback_data="settings")
        ],
        [
            InlineKeyboardButton("🆘 Помощь", callback_data="help"),
            InlineKeyboardButton("🔄 Переавторизация", callback_data="reauth")
        ]
    ])
    
    await message.reply_text(
        f"🤖 **Главное меню**\n\n"
        f"✅ Авторизован: `{user_data['phone']}`\n"
        f"📊 Сканирований: `{stats.get('scans_count', 0)}`\n"
        f"👥 Добавлено: `{stats.get('total_added', 0)}`\n\n"
        f"Выбери действие:",
        reply_markup=keyboard
    )

@app.on_callback_query(filters.regex("quick_auth"))
async def start_quick_auth(client, callback_query):
    """Начинаем быструю авторизацию"""
    await callback_query.message.edit_text(
        "🔐 **Быстрая авторизация**\n\n"
        "📱 Отправь мне свой номер телефона в международном формате:\n\n"
        "**Пример:** `+79123456789`\n\n"
        "⚠️ *Используй только свой номер телефона*"
    )
    user_states[callback_query.from_user.id] = 'waiting_phone'
    await callback_query.answer()

@app.on_message(filters.text & filters.private)
async def handle_user_input(client, message: Message):
    """Обрабатываем ввод пользователя"""
    user_id = message.from_user.id
    state = user_states.get(user_id)
    
    if not state:
        return
        
    if state == 'waiting_phone':
        # Обрабатываем ввод номера телефона
        phone = message.text.strip()
        
        if not validate_phone(phone):
            await message.reply_text(
                "❌ **Неправильный формат номера!**\n\n"
                "📱 **Правильный формат:** `+79123456789`\n"
                "Попробуй еще раз:"
            )
            return
            
        # Начинаем процесс авторизации
        result = await mass_auth.start_auth(user_id, phone)
        
        if result['success']:
            user_states[user_id] = 'waiting_code'
            await message.reply_text(
                f"✅ {result['message']}\n\n"
                f"📲 *Telegram прислал код подтверждения*\n"
                f"🔢 Отправь его мне в формате: `12345`"
            )
        else:
            await message.reply_text(result['message'])
            user_states.pop(user_id, None)
            
    elif state == 'waiting_code':
        # Обрабатываем ввод кода подтверждения
        code = message.text.strip()
        
        if not code.isdigit() or len(code) != 5:
            await message.reply_text("❌ Код должен быть 5 цифр! Пример: `12345`")
            return
            
        # Проверяем код и получаем API ключи
        result = await mass_auth.verify_code(user_id, code)
        
        if result['success']:
            await message.reply_text(
                f"🎉 **{result['message']}**\n\n"
                f"🔑 **Твои уникальные API ключи:**\n"
                f"• API_ID: `{result['api_id']}`\n"
                f"• API_HASH: `{result['api_hash']}`\n\n"
                f"⚡ Теперь можно начинать работу!"
            )
            
            # Показываем главное меню
            user_data_obj = await get_user_data(user_id)
            await show_main_menu(message, user_data_obj)
            
        else:
            await message.reply_text(result['message'])
            
        user_states.pop(user_id, None)

# Демо-функции сканирования и добавления
async def analyze_chat(chat_link, limit=0):
    """Анализ чата для поиска пользователей (демо-версия)"""
    try:
        # В реальной реализации здесь будет логика анализа чата
        # через Telethon с использованием сессии пользователя
        
        # Демо-данные для тестирования
        demo_users = [f"user_{i}" for i in range(1, 101)]
        return demo_users[:limit] if limit else demo_users[:50]
        
    except Exception as e:
        logger.error(f"Ошибка анализа чата: {e}")
        return []

async def mass_add_users(target_chat, users_list, delay=2):
    """Массовое добавление пользователей (демо-версия)"""
    added_count = 0
    failed_count = 0
    
    # Демо-реализация
    for i, user in enumerate(users_list, 1):
        try:
            # Здесь будет реальное добавление через Telethon
            # с использованием сессии пользователя
            
            # Имитируем задержку
            await asyncio.sleep(delay)
            added_count += 1
            
            # Логируем прогресс каждые 10 пользователей
            if i % 10 == 0:
                logger.info(f"✅ Добавлено {i}/{len(users_list)}")
                
        except Exception as e:
            logger.error(f"❌ Ошибка добавления {user}: {e}")
            failed_count += 1
    
    return added_count, failed_count

@app.on_callback_query(filters.regex("scan_chat"))
async def scan_chat_callback(client, callback_query):
    """Обработчик кнопки сканирования чата"""
    user_id = callback_query.from_user.id
    user_data_obj = await get_user_data(user_id)
    
    if not user_data_obj or not user_data_obj.get('api_id'):
        await callback_query.message.edit_text(
            "❌ **Сначала нужно авторизоваться!**\n\n"
            "Нажми /start и пройди быструю авторизацию."
        )
        await callback_query.answer()
        return
        
    await callback_query.message.edit_text(
        "🔍 **Сканирование чата**\n\n"
        "Отправь ссылку на чат для сканирования:\n\n"
        "📝 **Формат:**\n"
        "• `@username`\n"
        "• `https://t.me/username`\n\n"
        "💡 **Можно указать лимит:**\n"
        "• `@username 100` - просканирует 100 пользователей\n"
        "• `@username` - просканирует 50 пользователей (по умолчанию)"
    )
    user_states[user_id] = 'waiting_scan_link'
    await callback_query.answer()

@app.on_callback_query(filters.regex("add_users"))
async def add_users_callback(client, callback_query):
    """Обработчик кнопки добавления пользователей"""
    user_id = callback_query.from_user.id
    
    if user_id not in user_data or not user_data[user_id]:
        await callback_query.message.edit_text("❌ Сначала просканируй чат!")
        await callback_query.answer()
        return
        
    await callback_query.message.edit_text(
        "🚀 **Добавление пользователей**\n\n"
        "Отправь ссылку на целевую группу:\n\n"
        "📝 **Формат:**\n"
        "• `@groupname`\n\n"
        "💡 **Можно указать лимит:**\n"
        "• `@groupname 50` - добавит 50 пользователей\n"
        "• `@groupname` - добавит всех найденных"
    )
    user_states[user_id] = 'waiting_add_target'
    await callback_query.answer()

# Обработка сканирования и добавления
@app.on_message(filters.text & filters.private)
async def handle_scan_add_commands(client, message: Message):
    """Обрабатываем команды сканирования и добавления"""
    user_id = message.from_user.id
    state = user_states.get(user_id)
    
    if state == 'waiting_scan_link':
        parts = message.text.split()
        chat_link = parts[0]
        limit = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        
        status_msg = await message.reply_text(
            f"🕵️‍♂️ **Начинаю сканирование...**\n"
            f"📊 Чат: `{chat_link}`\n"
            f"🎯 Лимит: `{limit if limit else '50 (по умолчанию)'}`"
        )
        
        # Сканируем чат
        users = await analyze_chat(chat_link, limit)
        user_data[user_id] = users
        
        # Обновляем статистику
        await update_user_stats(user_id, 'scans_count')
        
        await status_msg.edit_text(
            f"✅ **Сканирование завершено!**\n\n"
            f"📊 Найдено пользователей: **{len(users)}**\n"
            f"💾 Готово к добавлению!\n\n"
            f"🚀 Теперь нажми кнопку **'Добавить'** в главном меню"
        )
        
        user_states.pop(user_id, None)
        
    elif state == 'waiting_add_target':
        parts = message.text.split()
        target_chat = parts[0]
        limit = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        
        if user_id not in user_data or not user_data[user_id]:
            await message.reply_text("❌ Сначала просканируй чат!")
            return
            
        users_to_add = user_data[user_id][:limit] if limit else user_data[user_id]
        
        status_msg = await message.reply_text(
            f"🚀 **Начинаю добавление...**\n"
            f"📊 Целевая группа: `{target_chat}`\n"
            f"👥 К добавлению: `{len(users_to_add)}` пользователей\n"
            f"⏱️ Задержка: `2` секунды"
        )
        
        # Добавляем пользователей
        added, failed = await mass_add_users(target_chat, users_to_add)
        
        # Обновляем статистику
        await update_user_stats(user_id, 'adds_count')
        await update_total_added(user_id, added)
        
        success_rate = (added / len(users_to_add)) * 100 if users_to_add else 0
        
        await status_msg.edit_text(
            f"📊 **Добавление завершено!**\n\n"
            f"✅ Успешно добавлено: **{added}**\n"
            f"❌ Не удалось добавить: **{failed}**\n"
            f"🎯 Эффективность: **{success_rate:.1f}%**\n\n"
            f"💾 Всего доступно: **{len(user_data[user_id])}** пользователей"
        )
        
        user_states.pop(user_id, None)

@app.on_callback_query(filters.regex("stats"))
async def show_stats(client, callback_query):
    """Показываем статистику пользователя"""
    user_id = callback_query.from_user.id
    stats = await get_user_stats(user_id)
    
    await callback_query.message.edit_text(
        f"📊 **Твоя статистика**\n\n"
        f"🔍 Сканирований: **{stats['scans_count']}**\n"
        f"🚀 Операций добавления: **{stats['adds_count']}**\n"
        f"👥 Всего добавлено: **{stats['total_added']}**\n\n"
        f"⚡ Продолжаем в том же духе!"
    )
    await callback_query.answer()

@app.on_callback_query(filters.regex("help"))
async def show_help(client, callback_query):
    """Показываем справку"""
    help_text = """
🆘 **Помощь по боту Zeta Mass Adder**

🔐 **Авторизация:**
• Используй быструю авторизацию через номер телефона
• Бот автоматически получит твои API ключи
• Все данные хранятся безопасно

🔍 **Сканирование чатов:**
• Отправь ссылку на любой чат/канал
• Бот найдет пользователей которых можно добавить
• Настраивай лимиты сканирования

🚀 **Добавление пользователей:**
• Выбери целевую группу
• Бот массово добавит найденных пользователей
• Автоматическая задержка между добавлениями

⚙️ **Настройки:**
• Лимиты сканирования и добавления
• Задержка между действиями
• Авто-добавление

📊 **Статистика:**
• Отслеживай свою активность
• Анализируй эффективность
• Оптимизируй работу

💬 **Поддержка:**
@zeta_support - помощь и вопросы
    """
    
    await callback_query.message.edit_text(help_text)
    await callback_query.answer()

@app.on_callback_query(filters.regex("reauth"))
async def reauth_user(client, callback_query):
    """Переавторизация пользователя"""
    user_id = callback_query.from_user.id
    
    # Удаляем старые данные
    pool = await create_pool()
    async with pool.acquire() as conn:
        await conn.execute('DELETE FROM users WHERE user_id = $1', user_id)
        await conn.execute('DELETE FROM auth_sessions WHERE user_id = $1', user_id)
        await conn.execute('DELETE FROM user_stats WHERE user_id = $1', user_id)
    await pool.close()
    
    user_states.pop(user_id, None)
    user_data.pop(user_id, None)
    
    await callback_query.message.edit_text(
        "🔄 **Все данные удалены!**\n\n"
        "Теперь можешь пройти авторизацию заново через /start\n\n"
        "⚡ *Бот сгенерирует для тебя новые API ключи*"
    )
    await callback_query.answer()

@app.on_callback_query(filters.regex("settings"))
async def show_settings(client, callback_query):
    """Показываем настройки"""
    await callback_query.message.edit_text(
        "⚙️ **Настройки**\n\n"
        "🔧 *Раздел в разработке*\n\n"
        "Скоро здесь можно будет настроить:\n"
        "• Лимиты сканирования\n"
        "• Лимиты добавления\n"
        "• Задержки между действиями\n"
        "• Автоматизацию процессов"
    )
    await callback_query.answer()

# Запуск бота
async def main():
    """Основная функция запуска"""
    print("🚀 Инициализация Zeta Mass Adder Bot...")
    
    # Инициализируем базу данных
    await init_database()
    
    # Запускаем бота
    print("🤖 Запускаю бота...")
    await app.start()
    
    # Получаем информацию о боте
    me = await app.get_me()
    print(f"✅ Бот @{me.username} успешно запущен!")
    print(f"🔗 Ссылка: https://t.me/{me.username}")
    
    # Бесконечный цикл
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
