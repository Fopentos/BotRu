import os
import logging
import asyncio
from telegram import Update, Bot, ChatMemberAdministrator, Chat
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

# Простая in-memory база данных
class SimpleDB:
    def __init__(self):
        self.channels = {}
        self.admins = {}
        self.stats = {}
        self.processed_users = set()  # Чтобы избежать дубликатов
    
    def add_channel(self, channel_id, channel_username, owner_id, chat_type):
        self.channels[channel_id] = {
            'channel_username': channel_username,
            'owner_id': owner_id,
            'is_active': True,
            'auto_approve': True,
            'chat_type': chat_type,
            'max_daily_approvals': 1000,
            'last_processed': None
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
    
    def get_channel_by_id(self, channel_id):
        return self.channels.get(channel_id)

    def mark_user_processed(self, channel_id, user_id):
        key = f"{channel_id}_{user_id}"
        self.processed_users.add(key)
    
    def is_user_processed(self, channel_id, user_id):
        key = f"{channel_id}_{user_id}"
        return key in self.processed_users

# Инициализируем базу данных
db = SimpleDB()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда начала работы с ботом"""
    user = update.effective_user
    await update.message.reply_text(
        f"👋 Привет, {user.first_name}!\n\n"
        "🤖 Я бот для автоматического принятия заявок в Telegram-каналах и группах\n\n"
        "📋 Доступные команды:\n"
        "/add_channel - Добавить канал/группу\n"
        "/list_channels - Мои каналы\n"
        "/turbo @channel - Быстро принять все заявки\n"
        "/help - Помощь\n\n"
        "⚡ Скорость: до 10 заявок в секунду"
    )

async def add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавление нового канала или группы"""
    user_id = str(update.effective_user.id)
    
    if not context.args:
        await update.message.reply_text(
            "❌ Укажите username канала/группы после команды:\n"
            "Пример: /add_channel @my_channel\n"
            "Или: /add_channel @my_group"
        )
        return
    
    channel_username = context.args[0].lstrip('@')
    
    try:
        # Проверяем, есть ли бот в канале/группе и его права
        bot = context.bot
        chat = await bot.get_chat(f"@{channel_username}")
        
        # Проверяем, является ли бот администратором
        bot_member = await chat.get_member(bot.id)
        
        if not isinstance(bot_member, ChatMemberAdministrator):
            await update.message.reply_text(
                "❌ Бот не является администратором!\n"
                "Добавьте бота как администратора с правами:\n"
                "✓ Приглашать пользователей\n"
                "✓ Добавлять участников\n"
                "✓ Одобрять заявки (если есть)"
            )
            return
        
        # Проверяем необходимые права для разных типов чатов
        required_permissions = []
        
        if chat.type in [Chat.CHANNEL, Chat.SUPERGROUP]:
            if not bot_member.can_invite_users:
                required_permissions.append("✓ Приглашать пользователей")
            if not bot_member.can_promote_members:
                required_permissions.append("✓ Добавлять участников")
            if not bot_member.can_restrict_members:
                required_permissions.append("✓ Ограничивать участников")
        else:
            await update.message.reply_text("❌ Поддерживаются только каналы и супергруппы")
            return
        
        if required_permissions:
            await update.message.reply_text(
                "❌ Недостаточно прав!\n"
                "Боту нужны права:\n" + "\n".join(required_permissions)
            )
            return
        
        # Сохраняем в базу данных
        db.add_channel(str(chat.id), channel_username, user_id, chat.type)
        
        await update.message.reply_text(
            f"✅ {'Канал' if chat.type == Chat.CHANNEL else 'Группа'} @{channel_username} успешно добавлен!\n\n"
            f"📝 Тип: {'Канал' if chat.type == Chat.CHANNEL else 'Группа'}\n"
            "⚡ Бот теперь будет автоматически принимать заявки\n"
            "🚀 Используйте /turbo @channel для быстрого принятия заявок\n\n"
            "⚡ Скорость: до 10 заявок в секунду"
        )
        
    except BadRequest as e:
        error_message = str(e).lower()
        if "chat not found" in error_message:
            await update.message.reply_text(
                "❌ Чат не найден или бот не добавлен как администратор!\n"
                "Убедитесь, что:\n"
                "1. Чат существует\n"
                "2. Бот добавлен как администратор\n"
                "3. У бота есть нужные права"
            )
        else:
            await update.message.reply_text(f"❌ Ошибка: {e}")
    except Exception as e:
        logger.error(f"Error adding channel: {e}")
        await update.message.reply_text("❌ Произошла ошибка при добавлении чата")

async def list_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список каналов/групп пользователя"""
    user_id = str(update.effective_user.id)
    
    user_channels = db.get_user_channels(user_id)
    
    if not user_channels:
        await update.message.reply_text(
            "❌ У вас нет добавленных чатов\n"
            "Используйте /add_channel чтобы добавить канал или группу"
        )
        return
    
    channels_text = "📋 Ваши чаты:\n\n"
    for channel in user_channels:
        status = "🟢" if channel['is_active'] else "🔴"
        chat_type = "Канал" if channel['chat_type'] == Chat.CHANNEL else "Группа"
        channels_text += f"{status} @{channel['channel_username']} ({chat_type})\n"
    
    channels_text += "\n🚀 Для быстрого принятия заявок: /turbo @channel"
    await update.message.reply_text(channels_text)

async def approve_single_request(context, channel_id, user_id, channel_username):
    """Принятие одной заявки с обработкой ошибок"""
    try:
        await context.bot.approve_chat_join_request(
            chat_id=channel_id,
            user_id=user_id
        )
        logger.info(f"✅ Approved user {user_id} for @{channel_username}")
        return True
    except TelegramError as e:
        error_msg = str(e).lower()
        if "user not found" in error_msg or "user already participant" in error_msg:
            logger.info(f"⚠️ User {user_id} already approved or not found for @{channel_username}")
        elif "too many requests" in error_msg:
            logger.warning(f"⚠️ Rate limit hit for @{channel_username}, slowing down...")
            await asyncio.sleep(2)  # Задержка при лимите
        else:
            logger.error(f"❌ Error approving user {user_id} for @{channel_username}: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Unexpected error approving user {user_id}: {e}")
        return False

async def turbo_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Режим турбо-принятия для большого количества заявок"""
    user_id = str(update.effective_user.id)
    
    if not context.args:
        await update.message.reply_text("❌ Укажите username чата: /turbo @channel")
        return
    
    channel_username = context.args[0].lstrip('@')
    channel = db.get_channel(channel_username)
    
    if not channel or user_id not in db.admins.get(channel['channel_id'], set()):
        await update.message.reply_text("❌ Чат не найден или нет доступа")
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
        message = await update.message.reply_text(
            f"🚀 Запуск TURBO режима...\n"
            f"📊 Найдено заявок: {total}\n"
            f"⚡ Скорость: до 10 заявок в секунду\n"
            f"⏱ Примерное время: {total/10:.1f} секунд"
        )
        
        approved = 0
        failed = 0
        start_time = time.time()
        
        # Обработка с ограничением скорости - 10 заявок в секунду
        requests_per_second = 10
        batch_delay = 1.0 / requests_per_second  # 0.1 секунды между заявками
        
        for i, request in enumerate(requests_list):
            if db.is_user_processed(channel['channel_id'], request.user.id):
                logger.info(f"⏭️ Skipping already processed user {request.user.id}")
                continue
                
            success = await approve_single_request(
                context, 
                channel['channel_id'], 
                request.user.id,
                channel['channel_username']
            )
            
            if success:
                approved += 1
                db.mark_user_processed(channel['channel_id'], request.user.id)
            else:
                failed += 1
            
            # Обновляем прогресс каждые 50 заявок или каждые 5 секунд
            if i % 50 == 0 or i == total - 1:
                elapsed = time.time() - start_time
                speed = approved / elapsed if elapsed > 0 else 0
                remaining = total - i - 1
                eta = remaining / requests_per_second if speed > 0 else 0
                
                progress = (
                    f"🚀 TURBO режим\n"
                    f"✅ Принято: {approved}/{total}\n"
                    f"❌ Ошибок: {failed}\n"
                    f"⚡ Скорость: {speed:.1f} заявок/сек\n"
                    f"⏱ Осталось: ~{eta:.1f} сек"
                )
                try:
                    await message.edit_text(progress)
                except:
                    pass
            
            # Задержка для ограничения скорости (10 заявок в секунду)
            if i < total - 1:  # Не ждем после последней заявки
                await asyncio.sleep(batch_delay)
        
        # Финальная статистика
        total_time = time.time() - start_time
        actual_speed = approved / total_time if total_time > 0 else 0
        
        await message.edit_text(
            f"🎉 TURBO режим завершен!\n\n"
            f"📊 Статистика:\n"
            f"✅ Принято: {approved}/{total}\n"
            f"❌ Ошибок: {failed}\n"
            f"⏱ Затрачено времени: {total_time:.1f} сек\n"
            f"⚡ Средняя скорость: {actual_speed:.1f} заявок/сек\n\n"
            f"💡 Новые заявки будут обрабатываться автоматически"
        )
        
    except Exception as e:
        logger.error(f"Error in turbo mode: {e}")
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

async def process_join_requests(context: ContextTypes.DEFAULT_TYPE):
    """Фоновая обработка заявок с ограничением скорости"""
    for channel_id, channel in db.channels.items():
        if not channel['is_active'] or not channel['auto_approve']:
            continue
            
        try:
            join_requests = await context.bot.get_chat_join_requests(chat_id=channel_id)
            requests_list = list(join_requests)
            
            if not requests_list:
                continue
            
            logger.info(f"Processing {len(requests_list)} requests for {channel['channel_username']}")
            
            # Ограничение скорости - 10 заявок в секунду
            requests_per_second = 10
            batch_delay = 1.0 / requests_per_second
            
            processed = 0
            for request in requests_list:
                if db.is_user_processed(channel_id, request.user.id):
                    continue
                    
                success = await approve_single_request(
                    context, 
                    channel_id, 
                    request.user.id,
                    channel['channel_username']
                )
                
                if success:
                    processed += 1
                    db.mark_user_processed(channel_id, request.user.id)
                
                # Задержка для ограничения скорости
                await asyncio.sleep(batch_delay)
            
            if processed > 0:
                logger.info(f"✅ Approved {processed} requests for {channel['channel_username']}")
            
            # Обновляем время последней обработки
            channel['last_processed'] = datetime.now()
            
        except TelegramError as e:
            error_msg = str(e).lower()
            if "chat not found" in error_msg or "bot was kicked" in error_msg:
                logger.warning(f"Bot was removed from {channel['channel_username']}")
                channel['is_active'] = False
            elif "not enough rights" in error_msg:
                logger.warning(f"Not enough rights in {channel['channel_username']}")
            else:
                logger.error(f"Error processing requests for {channel['channel_username']}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error for {channel['channel_username']}: {e}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Помощь по командам"""
    help_text = """
🤖 **Auto-Join Bot - Помощь**

**Основные команды:**
/start - Начать работу
/add_channel @username - Добавить канал или группу
/list_channels - Мои чаты
/turbo @channel - Быстро принять все заявки

**Как использовать:**
1. Добавьте бота в канал/группу как администратора
2. Дайте права: "Приглашать пользователей", "Добавлять участников"
3. Используйте /add_channel @ваш_чат
4. Для массового принятия: /turbo @ваш_чат

**Поддерживаемые типы чатов:**
- 📢 Публичные каналы
- 🔒 Закрытые каналы  
- 👥 Супергруппы
- 🔐 Закрытые группы

**Скорость работы:**
- ⚡ До 10 заявок в секунду
- 🛡 Защита от ограничений Telegram
- 🔄 Автоматическое возобновление
    """
    await update.message.reply_text(help_text)

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
        seconds=15,  # Проверка каждые 15 секунд
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
            logger.info("🚀 Starting bot in POLLING mode...")
            application.run_polling()
    else:
        # Polling режим для локальной разработки
        logger.info("🚀 Starting bot in POLLING mode...")
        application.run_polling()

if __name__ == '__main__':
    main()
