import os
import logging
import asyncio
from telegram import Update, Bot, ChatMemberAdministrator
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import TelegramError, BadRequest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime
import time

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Получаем токен бота
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN not found in environment variables!")
    exit(1)

# Простая in-memory база данных (без SQLite)
class SimpleDB:
    def __init__(self):
        self.channels = {}
        self.admins = {}
        self.stats = {}
    
    def add_channel(self, channel_id, channel_username, owner_id):
        self.channels[channel_id] = {
            'channel_username': channel_username,
            'owner_id': owner_id,
            'is_active': True,
            'auto_approve': True,
            'max_daily_approvals': 1000
        }
        
        if channel_id not in self.admins:
            self.admins[channel_id] = set()
        self.admins[channel_id].add(owner_id)
    
    def get_user_channels(self, user_id):
        return [channel for channel_id, channel in self.channels.items() 
                if user_id in self.admins.get(channel_id, set())]
    
    def get_channel(self, channel_username):
        for channel_id, channel in self.channels.items():
            if channel['channel_username'] == channel_username:
                return channel
        return None

# Инициализируем базу данных
db = SimpleDB()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда начала работы с ботом"""
    user = update.effective_user
    await update.message.reply_text(
        f"👋 Привет, {user.first_name}!\n\n"
        "🤖 Я бот для автоматического принятия заявок в Telegram-каналах\n\n"
        "📋 Доступные команды:\n"
        "/add_channel - Добавить канал\n"
        "/list_channels - Мои каналы\n"
        "/turbo @channel - Быстро принять все заявки\n"
        "/help - Помощь"
    )

async def add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавление нового канала"""
    user_id = str(update.effective_user.id)
    
    if not context.args:
        await update.message.reply_text(
            "❌ Укажите username канала после команды:\n"
            "Пример: /add_channel @my_channel"
        )
        return
    
    channel_username = context.args[0].lstrip('@')
    
    try:
        # Проверяем, есть ли бот в канале и его права
        bot = context.bot
        chat = await bot.get_chat(f"@{channel_username}")
        
        # Проверяем, является ли бот администратором
        bot_member = await chat.get_member(bot.id)
        
        if not isinstance(bot_member, ChatMemberAdministrator):
            await update.message.reply_text(
                "❌ Бот не является администратором канала!\n"
                "Добавьте бота как администратора с правом:\n"
                "✓ Приглашать пользователей\n"
                "✓ Добавлять участников"
            )
            return
        
        # Проверяем необходимые права
        if not (bot_member.can_invite_users and bot_member.can_promote_members):
            await update.message.reply_text(
                "❌ Недостаточно прав!\n"
                "Боту нужны права:\n"
                "✓ Приглашать пользователей\n"
                "✓ Добавлять участников"
            )
            return
        
        # Сохраняем в базу данных
        db.add_channel(str(chat.id), channel_username, user_id)
        
        await update.message.reply_text(
            f"✅ Канал @{channel_username} успешно добавлен!\n\n"
            "⚡ Бот теперь будет автоматически принимать заявки\n"
            "🚀 Используйте /turbo @channel для быстрого принятия заявок"
        )
        
    except BadRequest as e:
        await update.message.reply_text(
            "❌ Не удалось найти канал или бот не добавлен как администратор!\n"
            "Убедитесь, что:\n"
            "1. Канал существует\n"
            "2. Бот добавлен как администратор\n"
            "3. У бота есть нужные права"
        )
    except Exception as e:
        logger.error(f"Error adding channel: {e}")
        await update.message.reply_text("❌ Произошла ошибка при добавлении канала")

async def list_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список каналов пользователя"""
    user_id = str(update.effective_user.id)
    
    user_channels = db.get_user_channels(user_id)
    
    if not user_channels:
        await update.message.reply_text(
            "❌ У вас нет добавленных каналов\n"
            "Используйте /add_channel чтобы добавить канал"
        )
        return
    
    channels_text = "📋 Ваши каналы:\n\n"
    for channel in user_channels:
        status = "🟢" if channel['is_active'] else "🔴"
        channels_text += f"{status} @{channel['channel_username']}\n"
    
    channels_text += "\n🚀 Для быстрого принятия заявок: /turbo @channel"
    await update.message.reply_text(channels_text)

async def turbo_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Режим турбо-принятия для большого количества заявок"""
    user_id = str(update.effective_user.id)
    
    if not context.args:
        await update.message.reply_text("❌ Укажите username канала: /turbo @channel")
        return
    
    channel_username = context.args[0].lstrip('@')
    channel = db.get_channel(channel_username)
    
    if not channel or user_id not in db.admins.get(channel['channel_id'], set()):
        await update.message.reply_text("❌ Канал не найден или нет доступа")
        return
    
    try:
        # Получаем все заявки
        join_requests = await context.bot.get_chat_join_requests(
            chat_id=channel['channel_id']
        )
        
        requests_list = list(join_requests)
        total = len(requests_list)
        
        if total == 0:
            await update.message.reply_text("❌ Нет заявок для принятия")
            return
        
        # Запускаем турбо-режим
        message = await update.message.reply_text(f"🚀 Запуск TURBO режима...\nОбработка {total} заявок")
        
        approved = 0
        failed = 0
        start_time = time.time()
        
        # Быстрая обработка - 10 параллельных запросов
        batch_size = 10
        
        for i in range(0, total, batch_size):
            batch = requests_list[i:i + batch_size]
            
            tasks = []
            for request in batch:
                task = context.bot.approve_chat_join_request(
                    chat_id=channel['channel_id'],
                    user_id=request.user.id
                )
                tasks.append(task)
            
            # Параллельное выполнение
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Анализ результатов
            batch_approved = sum(1 for r in results if not isinstance(r, Exception))
            batch_failed = len(results) - batch_approved
            
            approved += batch_approved
            failed += batch_failed
            
            # Обновляем прогресс каждые 100 заявок
            if i % 100 == 0 or i + batch_size >= total:
                elapsed = time.time() - start_time
                speed = approved / elapsed if elapsed > 0 else 0
                
                progress = (
                    f"🚀 TURBO режим\n"
                    f"✅ Принято: {approved}/{total}\n"
                    f"❌ Ошибок: {failed}\n"
                    f"⚡ Скорость: {speed:.1f} заявок/сек"
                )
                try:
                    await message.edit_text(progress)
                except:
                    pass
            
            # Минимальная пауза
            await asyncio.sleep(0.05)
        
        # Финальная статистика
        total_time = time.time() - start_time
        await message.edit_text(
            f"🎉 TURBO режим завершен!\n\n"
            f"📊 Статистика:\n"
            f"✅ Принято: {approved}/{total}\n"
            f"❌ Ошибок: {failed}\n"
            f"⏱ Время: {total_time:.1f} сек\n"
            f"⚡ Средняя скорость: {approved/total_time:.1f} заявок/сек\n\n"
            f"💡 Для автоматического принятия новых заявок используйте /add_channel"
        )
        
    except Exception as e:
        logger.error(f"Error in turbo mode: {e}")
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Помощь по командам"""
    help_text = """
🤖 **Auto-Join Bot - Помощь**

**Основные команды:**
/start - Начать работу
/add_channel @username - Добавить канал
/list_channels - Мои каналы
/turbo @channel - Быстро принять все заявки

**Как использовать:**
1. Добавьте бота в канал как администратора
2. Дайте права: "Приглашать пользователей", "Добавлять участников"
3. Используйте /add_channel @ваш_канал
4. Для массового принятия: /turbo @ваш_канал

**Скорость работы:**
- До 1000+ заявок в минуту
- Параллельная обработка
- Автоматическое возобновление при ошибках
    """
    await update.message.reply_text(help_text)

async def process_join_requests(context: ContextTypes.DEFAULT_TYPE):
    """Фоновая обработка заявок"""
    for channel_id, channel in db.channels.items():
        if not channel['is_active'] or not channel['auto_approve']:
            continue
            
        try:
            join_requests = await context.bot.get_chat_join_requests(chat_id=channel_id)
            requests_list = list(join_requests)
            
            if not requests_list:
                continue
                
            # Быстрая обработка небольшими батчами
            batch_size = 5
            for i in range(0, len(requests_list), batch_size):
                batch = requests_list[i:i + batch_size]
                
                tasks = []
                for request in batch:
                    task = context.bot.approve_chat_join_request(
                        chat_id=channel_id,
                        user_id=request.user.id
                    )
                    tasks.append(task)
                
                await asyncio.gather(*tasks, return_exceptions=True)
                await asyncio.sleep(0.1)
                
            logger.info(f"Processed {len(requests_list)} requests for {channel['channel_username']}")
            
        except Exception as e:
            logger.error(f"Error processing requests for {channel['channel_username']}: {e}")

def main():
    """Запуск бота"""
    # Создаем приложение
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("add_channel", add_channel))
    application.add_handler(CommandHandler("list_channels", list_channels))
    application.add_handler(CommandHandler("turbo", turbo_approve))
    application.add_handler(CommandHandler("help", help_command))
    
    # Настраиваем планировщик для фоновой обработки
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        process_join_requests,
        'interval',
        seconds=30,
        args=[application]
    )
    scheduler.start()
    
    # Запускаем бота
    port = int(os.environ.get('PORT', 8443))
    
    # Проверяем, на Railway ли мы
    if 'RAILWAY_STATIC_URL' in os.environ or 'PORT' in os.environ:
        # Webhook режим для Railway
        webhook_url = os.environ.get('RAILWAY_STATIC_URL', '')
        if webhook_url:
            application.run_webhook(
                listen="0.0.0.0",
                port=port,
                url_path=BOT_TOKEN,
                webhook_url=f"{webhook_url}/{BOT_TOKEN}"
            )
        else:
            # Используем polling как fallback
            application.run_polling()
    else:
        # Polling режим для локальной разработки
        application.run_polling()

if __name__ == '__main__':
    main()
