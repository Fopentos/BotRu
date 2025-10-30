import os
import logging
import asyncio
import re
from telegram import Update, Bot, ChatMemberAdministrator, Chat
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
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
        self.processed_users = set()
    
    def add_channel(self, channel_id, channel_title, owner_id, chat_type, invite_link):
        self.channels[channel_id] = {
            'channel_title': channel_title,
            'owner_id': owner_id,
            'is_active': True,
            'auto_approve': True,
            'chat_type': chat_type,
            'invite_link': invite_link,
            'max_daily_approvals': 5000,
            'last_processed': None,
            'total_approved': 0
        }
        
        if channel_id not in self.admins:
            self.admins[channel_id] = set()
        self.admins[channel_id].add(owner_id)
    
    def get_user_channels(self, user_id):
        return [channel for channel_id, channel in self.channels.items() 
                if user_id in self.admins.get(channel_id, set())]
    
    def get_channel_by_invite(self, invite_link):
        for channel_id, channel in self.channels.items():
            if channel['invite_link'] == invite_link:
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
    
    def increment_approved(self, channel_id):
        if channel_id in self.channels:
            self.channels[channel_id]['total_approved'] += 1

# Инициализируем базу данных
db = SimpleDB()

def extract_invite_link(text):
    """Извлекает пригласительную ссылку из текста"""
    # Паттерны для пригласительных ссылок
    patterns = [
        r'https?://t\.me/\+[\w-]+',
        r'https?://telegram\.me/\+[\w-]+',
        r'@[\w-]+',
        r'\+[\w-]+'
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, text)
        if matches:
            link = matches[0]
            # Если это не полная ссылка, преобразуем в полную
            if link.startswith('+'):
                return f"https://t.me/{link}"
            elif link.startswith('@'):
                return f"https://t.me/{link[1:]}"
            return link
    
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда начала работы с ботом"""
    user = update.effective_user
    await update.message.reply_text(
        f"👋 Привет, {user.first_name}!\n\n"
        "🤖 Я бот для автоматического принятия заявок в ПРИВАТНЫХ Telegram-каналах\n\n"
        "🔗 **Как добавить канал:**\n"
        "1. Добавьте бота в канал как администратора\n"
        "2. Скопируйте пригласительную ссылку канала\n"
        "3. Отправьте её боту\n\n"
        "⚡ **Оптимизирован для массового принятия (3000+ заявок)**\n"
        "🚀 Скорость: 10 заявок в секунду\n\n"
        "📋 **Команды:**\n"
        "/list - Мои каналы\n"
        "/turbo - Быстро принять ВСЕ заявки\n"
        "/status - Статус обработки\n"
        "/help - Подробная помощь\n\n"
        "🔧 **Просто отправьте пригласительную ссылку канала чтобы начать!**"
    )

async def handle_invite_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка пригласительных ссылок"""
    user_id = str(update.effective_user.id)
    text = update.message.text
    
    # Извлекаем пригласительную ссылку
    invite_link = extract_invite_link(text)
    
    if not invite_link:
        await update.message.reply_text(
            "❌ Не удалось найти пригласительную ссылку в сообщении.\n\n"
            "📋 **Примеры поддерживаемых форматов:**\n"
            "• https://t.me/+QDql_IQd_7Y0NTQy\n"
            "• @username_канала\n"
            "• +QDql_IQd_7Y0NTQy\n\n"
            "🔗 **Как получить ссылку:**\n"
            "1. Зайдите в настройки канала\n"
            "2. Нажмите 'Пригласительная ссылка'\n"
            "3. Скопируйте ссылку и отправьте боту"
        )
        return
    
    try:
        # Проверяем, есть ли бот в канале и его права
        bot = context.bot
        chat = await bot.get_chat(invite_link)
        
        # Проверяем тип чата - должен быть каналом
        if chat.type != Chat.CHANNEL:
            await update.message.reply_text(
                "❌ Это не канал! Бот работает только с Telegram-каналами.\n"
                "Для групп используйте других ботов."
            )
            return
        
        # Проверяем, является ли бот администратором
        bot_member = await chat.get_member(bot.id)
        
        if not isinstance(bot_member, ChatMemberAdministrator):
            await update.message.reply_text(
                "❌ Бот не является администратором канала!\n\n"
                "📋 **Чтобы добавить бота:**\n"
                "1. Зайдите в настройки канала\n"
                "2. Выберите 'Администраторы'\n"
                "3. Добавьте бота как администратора\n"
                "4. Дайте ВСЕ права (особенно важны):\n"
                "   ✓ Добавлять подписчиков\n"
                "   ✓ Приглашать пользователей\n"
                "   ✓ Одобрять заявки"
            )
            return
        
        # Проверяем КРИТИЧЕСКИ важные права для приватных каналов
        missing_permissions = []
        
        if not bot_member.can_invite_users:
            missing_permissions.append("❌ Приглашать пользователей")
        if not bot_member.can_promote_members:
            missing_permissions.append("❌ Добавлять участников")
        if not bot_member.can_restrict_members:
            missing_permissions.append("❌ Ограничивать участников")
        
        if missing_permissions:
            await update.message.reply_text(
                "❌ Недостаточно прав для принятия заявок!\n\n"
                "Боту нужны ВСЕ эти права:\n" +
                "\n".join(missing_permissions) +
                "\n\n🔧 **Обновите права бота в настройках канала**"
            )
            return
        
        # Проверяем, не добавлен ли уже канал
        existing_channel = db.get_channel_by_id(str(chat.id))
        if existing_channel:
            await update.message.reply_text(
                f"✅ Канал '{chat.title}' уже добавлен!\n\n"
                f"🚀 Для принятия заявок используйте:\n"
                f"/turbo\n"
                f"📊 Для проверки статуса:\n"
                f"/status"
            )
            return
        
        # Создаем новую пригласительную ссылку (для информации)
        try:
            if bot_member.can_invite_users:
                new_invite = await bot.create_chat_invite_link(chat.id, creates_join_request=True)
                final_invite_link = new_invite.invite_link
            else:
                final_invite_link = invite_link
        except:
            final_invite_link = invite_link
        
        # Сохраняем в базу данных
        db.add_channel(str(chat.id), chat.title, user_id, chat.type, final_invite_link)
        
        success_message = (
            f"✅ **Канал успешно добавлен!**\n\n"
            f"📝 **Название:** {chat.title}\n"
            f"🔗 **Ссылка:** {final_invite_link}\n"
            f"📊 **Статус:** 🟢 АКТИВЕН\n"
            f"🤖 **Автопринятие:** 🟢 ВКЛЮЧЕНО\n"
            f"⚡ **Скорость:** 10 заявок/секунду\n\n"
        )
        
        # Получаем текущие заявки для информации
        try:
            join_requests = await bot.get_chat_join_requests(chat.id)
            pending_count = len(list(join_requests))
            success_message += f"⏳ **Ожидающих заявок:** {pending_count}\n\n"
        except:
            pending_count = 0
        
        success_message += (
            "🚀 **Для массового принятия заявок:**\n"
            "/turbo\n\n"
            "📈 **Для проверки статуса:**\n"
            "/status"
        )
        
        await update.message.reply_text(success_message)
        
    except BadRequest as e:
        error_msg = str(e).lower()
        if "chat not found" in error_msg:
            await update.message.reply_text(
                "❌ Канал не найден или бот не добавлен как администратор!\n\n"
                "🔧 **Убедитесь, что:**\n"
                "1. Канал существует\n"
                "2. Бот добавлен как администратор\n"
                "3. У бота есть ВСЕ необходимые права\n"
                "4. Вы используете правильную пригласительную ссылку"
            )
        elif "not enough rights" in error_msg:
            await update.message.reply_text(
                "❌ Недостаточно прав! Дайте боту ВСЕ права администратора."
            )
        elif "invite link invalid" in error_msg:
            await update.message.reply_text(
                "❌ Недействительная пригласительная ссылка!\n\n"
                "🔗 **Получите новую ссылку:**\n"
                "1. Зайдите в настройки канала\n"
                "2. Нажмите 'Пригласительная ссылка'\n"
                "3. Создайте новую ссылку и отправьте боту"
            )
        else:
            await update.message.reply_text(f"❌ Ошибка: {e}")
    except Exception as e:
        logger.error(f"Error adding channel: {e}")
        await update.message.reply_text("❌ Произошла неизвестная ошибка при добавлении канала")

async def list_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список каналов пользователя"""
    user_id = str(update.effective_user.id)
    
    user_channels = db.get_user_channels(user_id)
    
    if not user_channels:
        await update.message.reply_text(
            "❌ У вас нет добавленных каналов\n\n"
            "🔗 **Чтобы добавить канал:**\n"
            "1. Добавьте бота в канал как администратора\n"
            "2. Отправьте пригласительную ссылку боту"
        )
        return
    
    channels_text = "📋 **Ваши приватные каналы:**\n\n"
    for i, channel in enumerate(user_channels, 1):
        status = "🟢" if channel['is_active'] else "🔴"
        approved = channel.get('total_approved', 0)
        channels_text += f"{status} **{i}. {channel['channel_title']}**\n"
        channels_text += f"   🔗 {channel['invite_link']}\n"
        channels_text += f"   ✅ Принято: {approved} заявок\n\n"
    
    channels_text += "🚀 **Для массового принятия:** /turbo"
    await update.message.reply_text(channels_text)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статус обработки канала"""
    user_id = str(update.effective_user.id)
    
    user_channels = db.get_user_channels(user_id)
    
    if not user_channels:
        await update.message.reply_text(
            "❌ У вас нет добавленных каналов\n\n"
            "🔗 Отправьте пригласительную ссылку канала чтобы начать"
        )
        return
    
    # Если несколько каналов, показываем список
    if len(user_channels) > 1:
        channels_text = "📋 **Выберите канал для проверки статуса:**\n\n"
        for i, channel in enumerate(user_channels, 1):
            status = "🟢" if channel['is_active'] else "🔴"
            channels_text += f"{status} **{i}. {channel['channel_title']}**\n"
        
        channels_text += "\n🔗 **Или отправьте пригласительную ссылку канала**"
        await update.message.reply_text(channels_text)
        return
    
    # Если один канал, показываем его статус
    channel = user_channels[0]
    
    try:
        # Получаем текущие заявки
        join_requests = await context.bot.get_chat_join_requests(
            chat_id=channel['channel_id']
        )
        
        pending_count = len(list(join_requests))
        total_approved = channel.get('total_approved', 0)
        
        status_text = (
            f"📊 **Статус канала:** {channel['channel_title']}\n\n"
            f"⏳ **Ожидающих заявок:** {pending_count}\n"
            f"✅ **Всего принято:** {total_approved}\n"
            f"🔄 **Статус:** {'🟢 Активен' if channel['is_active'] else '🔴 Выключен'}\n"
            f"⚡ **Автопринятие:** {'🟢 ВКЛ' if channel['auto_approve'] else '🔴 ВЫКЛ'}\n\n"
        )
        
        if pending_count > 0:
            estimated_time = pending_count / 10  # 10 заявок в секунду
            if estimated_time > 60:
                status_text += f"⏱ **Примерное время обработки:** {estimated_time/60:.1f} минут\n"
            else:
                status_text += f"⏱ **Примерное время обработки:** {estimated_time:.1f} секунд\n"
            
            status_text += f"🚀 **Для запуска:** /turbo"
        else:
            status_text += "🎉 **Нет ожидающих заявок!**"
        
        await update.message.reply_text(status_text)
        
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        await update.message.reply_text(f"❌ Ошибка при получении статуса: {e}")

async def turbo_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ТУРБО-режим для массового принятия заявок"""
    user_id = str(update.effective_user.id)
    
    user_channels = db.get_user_channels(user_id)
    
    if not user_channels:
        await update.message.reply_text(
            "❌ У вас нет добавленных каналов\n\n"
            "🔗 Отправьте пригласительную ссылку канала чтобы начать"
        )
        return
    
    # Если несколько каналов, используем первый
    if len(user_channels) > 1:
        await update.message.reply_text(
            "🔗 **Обнаружено несколько каналов.**\n\n"
            "🚀 Запускаю TURBO-режим для первого канала:\n"
            f"**{user_channels[0]['channel_title']}**\n\n"
            "📋 Чтобы выбрать другой канал, используйте /list"
        )
    
    channel = user_channels[0]
    
    try:
        # Получаем все заявки
        join_requests = await context.bot.get_chat_join_requests(
            chat_id=channel['channel_id']
        )
        
        requests_list = list(join_requests)
        total = len(requests_list)
        
        if total == 0:
            await update.message.reply_text("🎉 **Нет заявок для принятия!**")
            return
        
        # Расчет времени
        estimated_time = total / 10  # 10 заявок в секунду
        time_display = f"{estimated_time/60:.1f} минут" if estimated_time > 60 else f"{estimated_time:.0f} секунд"
        
        # Запускаем турбо-режим
        message = await update.message.reply_text(
            f"🚀 **ЗАПУСК TURBO-РЕЖИМА**\n\n"
            f"📝 **Канал:** {channel['channel_title']}\n"
            f"📊 **Обнаружено заявок:** {total}\n"
            f"⚡ **Скорость:** 10 заявок/секунду\n"
            f"⏱ **Примерное время:** {time_display}\n"
            f"🔧 **Начинаем обработку...**"
        )
        
        approved = 0
        failed = 0
        start_time = time.time()
        
        # Обработка с ограничением скорости - 10 заявок в секунду
        requests_per_second = 10
        batch_delay = 1.0 / requests_per_second  # 0.1 секунды между заявками
        
        for i, request in enumerate(requests_list):
            # Пропускаем уже обработанных пользователей
            if db.is_user_processed(channel['channel_id'], request.user.id):
                continue
                
            success = await approve_single_request(
                context, 
                channel['channel_id'], 
                request.user.id,
                channel['channel_title']
            )
            
            if success:
                approved += 1
                db.mark_user_processed(channel['channel_id'], request.user.id)
            else:
                failed += 1
            
            # Обновляем прогресс каждые 100 заявок
            if i % 100 == 0 or i == total - 1:
                elapsed = time.time() - start_time
                current_speed = approved / elapsed if elapsed > 0 else 0
                remaining = total - i - 1
                eta = remaining / requests_per_second if current_speed > 0 else 0
                
                # Форматируем ETA
                if eta > 60:
                    eta_display = f"{eta/60:.1f} мин"
                else:
                    eta_display = f"{eta:.0f} сек"
                
                progress = (
                    f"🚀 **TURBO-РЕЖИМ**\n\n"
                    f"📝 **Канал:** {channel['channel_title']}\n"
                    f"📊 **Прогресс:** {i+1}/{total}\n"
                    f"✅ **Принято:** {approved}\n"
                    f"❌ **Ошибок:** {failed}\n"
                    f"⚡ **Текущая скорость:** {current_speed:.1f}/сек\n"
                    f"⏱ **Осталось:** ~{eta_display}"
                )
                try:
                    await message.edit_text(progress)
                except:
                    pass
            
            # Задержка для ограничения скорости (10 заявок в секунду)
            if i < total - 1:
                await asyncio.sleep(batch_delay)
        
        # Финальная статистика
        total_time = time.time() - start_time
        actual_speed = approved / total_time if total_time > 0 else 0
        
        result_message = (
            f"🎉 **TURBO-РЕЖИМ ЗАВЕРШЕН!**\n\n"
            f"📝 **Канал:** {channel['channel_title']}\n"
            f"📊 **ИТОГИ:**\n"
            f"✅ **Успешно принято:** {approved}/{total}\n"
            f"❌ **Ошибок:** {failed}\n"
            f"⏱ **Затрачено времени:** {total_time:.1f} сек\n"
            f"⚡ **Средняя скорость:** {actual_speed:.1f} заявок/сек\n\n"
        )
        
        if approved == total:
            result_message += "🎯 **Все заявки успешно обработаны!**"
        else:
            result_message += "⚠️ **Некоторые заявки не удалось обработать**"
        
        await message.edit_text(result_message)
        
    except Exception as e:
        logger.error(f"Error in turbo mode: {e}")
        await update.message.reply_text(f"❌ **Критическая ошибка:** {str(e)}")

async def approve_single_request(context, channel_id, user_id, channel_title):
    """Принятие одной заявки с обработкой ошибок"""
    try:
        await context.bot.approve_chat_join_request(
            chat_id=channel_id,
            user_id=user_id
        )
        db.increment_approved(channel_id)
        logger.info(f"✅ Approved user {user_id} for {channel_title}")
        return True
        
    except TelegramError as e:
        error_msg = str(e).lower()
        
        # Пользователь уже принят или отозвал заявку
        if "user not found" in error_msg or "user already participant" in error_msg:
            logger.info(f"⚠️ User {user_id} already approved or not found for {channel_title}")
            return True  # Считаем как успех, т.к. заявки больше нет
            
        # Слишком много запросов - замедляемся
        elif "too many requests" in error_msg:
            logger.warning(f"⚠️ Rate limit hit for {channel_title}, slowing down...")
            await asyncio.sleep(5)  # Увеличиваем задержку при лимите
            return False
            
        # Нет прав - деактивируем канал
        elif "not enough rights" in error_msg:
            logger.error(f"❌ Not enough rights in {channel_title}. Deactivating.")
            channel = db.get_channel_by_id(channel_id)
            if channel:
                channel['is_active'] = False
            return False
            
        else:
            logger.error(f"❌ Error approving user {user_id} for {channel_title}: {e}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Unexpected error approving user {user_id}: {e}")
        return False

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Помощь по командам"""
    help_text = """
🤖 **Auto-Join Bot для ПРИВАТНЫХ КАНАЛОВ**

⚡ **Оптимизирован для 3000+ заявок**

**🔗 КАК НАЧАТЬ:**
1. Добавьте бота в канал как администратора
2. Дайте ВСЕ права администратора
3. Отправьте боту пригласительную ссылку канала

**📋 КОМАНДЫ:**
/start - Начать работу
/list - Мои каналы  
/turbo - Быстро принять ВСЕ заявки
/status - Статус обработки
/help - Эта справка

**🔧 НАСТРОЙКА ПРАВ БОТА:**
В настройках канала дайте боту ВСЕ права:
✓ Добавлять подписчиков
✓ Приглашать пользователей  
✓ Одобрять заявки
✓ Ограничивать участников

**⚡ ПРОИЗВОДИТЕЛЬНОСТЬ:**
- До 10 заявок в секунду
- 3200 заявок = ~5.5 минут
- Автоматическое возобновление
- Защита от ограничений Telegram

**📊 ПРИМЕР ДЛЯ 3200 ЗАЯВОК:**
⏱ Время: ~5.5 минут
⚡ Скорость: 10/сек
✅ Результат: 3200 принятых заявок

**🚀 ИСПОЛЬЗОВАНИЕ:**
1. Добавить бота в канал → Отправить ссылку
2. /turbo
3. Ждем 5-6 минут
4. Готово!
    """
    await update.message.reply_text(help_text)

def main():
    """Запуск бота"""
    # Создаем приложение
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("list", list_channels))
    application.add_handler(CommandHandler("turbo", turbo_approve))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("help", help_command))
    
    # Обработчик пригласительных ссылок
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_invite_link))
    
    # Настраиваем планировщик для фоновой обработки
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        process_join_requests,
        'interval',
        seconds=20,
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
            logger.info("🚀 Starting bot in POLLING mode...")
            application.run_polling()
    else:
        logger.info("🚀 Starting bot in POLLING mode...")
        application.run_polling()

async def process_join_requests(context: ContextTypes.DEFAULT_TYPE):
    """Фоновая обработка новых заявок"""
    for channel_id, channel in db.channels.items():
        if not channel['is_active'] or not channel['auto_approve']:
            continue
            
        try:
            join_requests = await context.bot.get_chat_join_requests(chat_id=channel_id)
            requests_list = list(join_requests)
            
            if not requests_list:
                continue
            
            logger.info(f"Processing {len(requests_list)} new requests for {channel['channel_title']}")
            
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
                    channel['channel_title']
                )
                
                if success:
                    processed += 1
                    db.mark_user_processed(channel_id, request.user.id)
                
                # Задержка для ограничения скорости
                await asyncio.sleep(batch_delay)
            
            if processed > 0:
                logger.info(f"✅ Approved {processed} new requests for {channel['channel_title']}")
            
            # Обновляем время последней обработки
            channel['last_processed'] = datetime.now()
            
        except TelegramError as e:
            error_msg = str(e).lower()
            if "chat not found" in error_msg or "bot was kicked" in error_msg:
                logger.warning(f"Bot was removed from {channel['channel_title']}")
                channel['is_active'] = False
            elif "not enough rights" in error_msg:
                logger.warning(f"Not enough rights in {channel['channel_title']}")
                channel['is_active'] = False
            else:
                logger.error(f"Error processing requests for {channel['channel_title']}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error for {channel['channel_title']}: {e}")

if __name__ == '__main__':
    main()
