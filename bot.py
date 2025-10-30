import os
import logging
import asyncio
from telegram import (
    Update, 
    Bot, 
    ChatMemberAdministrator, 
    Chat,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove
)
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
    
    def add_channel(self, channel_id, channel_title, owner_id, chat_type):
        self.channels[channel_id] = {
            'channel_title': channel_title,
            'owner_id': owner_id,
            'is_active': True,
            'auto_approve': True,
            'chat_type': chat_type,
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда начала работы с ботом"""
    user = update.effective_user
    
    # Создаем кнопку "Добавить канал"
    keyboard = [
        [KeyboardButton("📢 Добавить канал", request_chat=KeyboardButton.request_chat(
            request_id=1,
            chat_is_channel=True,
            bot_is_member=True
        ))]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    
    await update.message.reply_text(
        f"👋 Привет, {user.first_name}!\n\n"
        "🤖 Я бот для автоматического принятия заявок в приватных Telegram-каналах\n\n"
        "⚡ **Оптимизирован для массового принятия (3000+ заявок)**\n"
        "🚀 Скорость: 10 заявок в секунду\n\n"
        "📋 **Чтобы начать:**\n"
        "1. Нажмите кнопку '📢 Добавить канал' ниже\n"
        "2. Выберите ваш приватный канал\n"
        "3. Бот автоматически проверит права и добавит канал\n\n"
        "🔧 **Требования:**\n"
        "• Бот должен быть администратором канала\n"
        "• Все права должны быть включены\n"
        "• Канал должен быть приватным",
        reply_markup=reply_markup
    )

async def handle_chat_shared(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора канала через кнопку"""
    user_id = str(update.effective_user.id)
    
    if not update.message.chat_shared:
        await update.message.reply_text(
            "❌ Не удалось получить информацию о канале.\n"
            "Попробуйте еще раз, нажав кнопку '📢 Добавить канал'",
            reply_markup=ReplyKeyboardRemove()
        )
        return
    
    chat_shared = update.message.chat_shared
    channel_id = chat_shared.chat_id
    
    try:
        # Получаем информацию о канале
        bot = context.bot
        chat = await bot.get_chat(channel_id)
        
        # Проверяем тип чата - должен быть каналом
        if chat.type != Chat.CHANNEL:
            await update.message.reply_text(
                "❌ Это не канал! Бот работает только с Telegram-каналами.\n"
                "Пожалуйста, выберите канал.",
                reply_markup=ReplyKeyboardRemove()
            )
            return
        
        # Проверяем, является ли бот администратором
        try:
            bot_member = await chat.get_member(bot.id)
        except BadRequest as e:
            if "Bot is not a member" in str(e):
                await update.message.reply_text(
                    "❌ Бот не является администратором этого канала!\n\n"
                    "📋 **Чтобы добавить бота:**\n"
                    "1. Зайдите в настройки канала\n"
                    "2. Выберите 'Администраторы'\n"
                    "3. Добавьте бота как администратора\n"
                    "4. Дайте ВСЕ права:\n"
                    "   ✓ Добавлять подписчиков\n"
                    "   ✓ Приглашать пользователей\n"
                    "   ✓ Одобрять заявки\n\n"
                    "После этого нажмите кнопку '📢 Добавить канал' снова",
                    reply_markup=ReplyKeyboardRemove()
                )
                return
            else:
                raise e
        
        if not isinstance(bot_member, ChatMemberAdministrator):
            await update.message.reply_text(
                "❌ Бот не является администратором канала!\n\n"
                "Дайте боту права администратора и попробуйте снова.",
                reply_markup=ReplyKeyboardRemove()
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
                "\n\n🔧 **Обновите права бота в настройках канала:**\n"
                "1. Зайдите в 'Администраторы'\n"
                "2. Выберите бота\n"
                "3. Включите ВСЕ права\n"
                "4. Сохраните изменения\n"
                "5. Нажмите кнопку '📢 Добавить канал' снова",
                reply_markup=ReplyKeyboardRemove()
            )
            return
        
        # Проверяем, не добавлен ли уже канал
        existing_channel = db.get_channel_by_id(str(chat.id))
        if existing_channel:
            # Создаем кнопку для принятия заявок
            keyboard = [
                [KeyboardButton("🚀 Принять все заявки")],
                [KeyboardButton("📊 Статус"), KeyboardButton("📋 Мои каналы")]
            ]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            
            await update.message.reply_text(
                f"✅ Канал '{chat.title}' уже добавлен!\n\n"
                f"🚀 Для принятия заявок нажмите '🚀 Принять все заявки'\n"
                f"📊 Для проверки статуса нажмите '📊 Статус'",
                reply_markup=reply_markup
            )
            return
        
        # Сохраняем в базу данных
        db.add_channel(str(chat.id), chat.title, user_id, chat.type)
        
        # Создаем кнопки для управления
        keyboard = [
            [KeyboardButton("🚀 Принять все заявки")],
            [KeyboardButton("📊 Статус"), KeyboardButton("📋 Мои каналы")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        # Получаем текущие заявки для информации
        try:
            join_requests = await bot.get_chat_join_requests(chat.id)
            pending_count = len(list(join_requests))
        except Exception as e:
            logger.warning(f"Could not get join requests: {e}")
            pending_count = 0
        
        success_message = (
            f"✅ **Канал успешно добавлен!**\n\n"
            f"📝 **Название:** {chat.title}\n"
            f"📊 **Статус:** 🟢 АКТИВЕН\n"
            f"🤖 **Автопринятие:** 🟢 ВКЛЮЧЕНО\n"
            f"⚡ **Скорость:** 10 заявок/секунду\n"
            f"⏳ **Ожидающих заявок:** {pending_count}\n\n"
            f"🚀 **Для принятия заявок нажмите '🚀 Принять все заявки'**\n"
            f"📊 **Для проверки статуса нажмите '📊 Статус'**"
        )
        
        await update.message.reply_text(success_message, reply_markup=reply_markup)
        
    except Exception as e:
        logger.error(f"Error adding channel: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при добавлении канала\n\n"
            "Попробуйте еще раз или проверьте права бота в канале.",
            reply_markup=ReplyKeyboardRemove()
        )

async def handle_button_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий на кнопки"""
    user_id = str(update.effective_user.id)
    text = update.message.text
    
    if text == "📢 Добавить канал":
        # Показываем кнопку для добавления канала
        keyboard = [
            [KeyboardButton("📢 Добавить канал", request_chat=KeyboardButton.request_chat(
                request_id=1,
                chat_is_channel=True,
                bot_is_member=True
            ))]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text(
            "Выберите канал из списка:",
            reply_markup=reply_markup
        )
        
    elif text == "🚀 Принять все заявки":
        await turbo_approve(update, context)
        
    elif text == "📊 Статус":
        await status_command(update, context)
        
    elif text == "📋 Мои каналы":
        await list_channels(update, context)
        
    else:
        # Если сообщение не распознано, показываем главное меню
        keyboard = [
            [KeyboardButton("📢 Добавить канал")],
            [KeyboardButton("🚀 Принять все заявки")],
            [KeyboardButton("📊 Статус"), KeyboardButton("📋 Мои каналы")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text(
            "Выберите действие:",
            reply_markup=reply_markup
        )

async def list_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список каналов пользователя"""
    user_id = str(update.effective_user.id)
    
    user_channels = db.get_user_channels(user_id)
    
    if not user_channels:
        keyboard = [
            [KeyboardButton("📢 Добавить канал")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "❌ У вас нет добавленных каналов\n\n"
            "Нажмите '📢 Добавить канал' чтобы начать",
            reply_markup=reply_markup
        )
        return
    
    channels_text = "📋 **Ваши каналы:**\n\n"
    for i, channel in enumerate(user_channels, 1):
        status = "🟢" if channel['is_active'] else "🔴"
        approved = channel.get('total_approved', 0)
        channels_text += f"{status} **{i}. {channel['channel_title']}**\n"
        channels_text += f"   ✅ Принято: {approved} заявок\n\n"
    
    # Добавляем кнопки управления
    keyboard = [
        [KeyboardButton("🚀 Принять все заявки")],
        [KeyboardButton("📊 Статус"), KeyboardButton("📢 Добавить канал")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(channels_text, reply_markup=reply_markup)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статус обработки канала"""
    user_id = str(update.effective_user.id)
    
    user_channels = db.get_user_channels(user_id)
    
    if not user_channels:
        keyboard = [
            [KeyboardButton("📢 Добавить канал")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "❌ У вас нет добавленных каналов\n\n"
            "Нажмите '📢 Добавить канал' чтобы начать",
            reply_markup=reply_markup
        )
        return
    
    # Используем первый канал (можно расширить для выбора)
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
            
            status_text += f"🚀 **Для запуска нажмите '🚀 Принять все заявки'**"
        else:
            status_text += "🎉 **Нет ожидающих заявок!**"
        
        # Кнопки управления
        keyboard = [
            [KeyboardButton("🚀 Принять все заявки")],
            [KeyboardButton("📋 Мои каналы"), KeyboardButton("📢 Добавить канал")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(status_text, reply_markup=reply_markup)
        
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        await update.message.reply_text(f"❌ Ошибка при получении статуса: {e}")

async def turbo_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ТУРБО-режим для массового принятия заявок"""
    user_id = str(update.effective_user.id)
    
    user_channels = db.get_user_channels(user_id)
    
    if not user_channels:
        keyboard = [
            [KeyboardButton("📢 Добавить канал")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "❌ У вас нет добавленных каналов\n\n"
            "Нажмите '📢 Добавить канал' чтобы начать",
            reply_markup=reply_markup
        )
        return
    
    # Используем первый канал
    channel = user_channels[0]
    
    try:
        # Получаем все заявки
        join_requests = await context.bot.get_chat_join_requests(
            chat_id=channel['channel_id']
        )
        
        requests_list = list(join_requests)
        total = len(requests_list)
        
        if total == 0:
            keyboard = [
                [KeyboardButton("📊 Статус"), KeyboardButton("📋 Мои каналы")]
            ]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            
            await update.message.reply_text(
                "🎉 **Нет заявок для принятия!**",
                reply_markup=reply_markup
            )
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
        
        # Кнопки после завершения
        keyboard = [
            [KeyboardButton("📊 Статус"), KeyboardButton("📋 Мои каналы")],
            [KeyboardButton("📢 Добавить канал")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await message.edit_text(result_message)
        await update.message.reply_text("Выберите следующее действие:", reply_markup=reply_markup)
        
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
1. Нажмите "📢 Добавить канал"
2. Выберите ваш канал из списка
3. Бот автоматически проверит права и добавит канал

**📋 КНОПКИ УПРАВЛЕНИЯ:**
📢 Добавить канал - Выбрать канал для работы
🚀 Принять все заявки - Быстро принять ВСЕ заявки
📊 Статус - Проверить статус обработки
📋 Мои каналы - Список ваших каналов

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

**🚀 ДЛЯ 3200 ЗАЯВОК:**
1. Нажмите "📢 Добавить канал" и выберите канал
2. Нажмите "🚀 Принять все заявки"
3. Ждем ~5.5 минут
4. Готово!
    """
    
    # Главное меню
    keyboard = [
        [KeyboardButton("📢 Добавить канал")],
        [KeyboardButton("🚀 Принять все заявки")],
        [KeyboardButton("📊 Статус"), KeyboardButton("📋 Мои каналы")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(help_text, reply_markup=reply_markup)

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

def main():
    """Запуск бота"""
    # Создаем приложение
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("list", list_channels))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("turbo", turbo_approve))
    
    # Обработчик выбора канала через кнопку
    application.add_handler(MessageHandler(filters.CHAT_SHARED, handle_chat_shared))
    
    # Обработчик нажатий на кнопки
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_button_actions))
    
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

if __name__ == '__main__':
    main()
