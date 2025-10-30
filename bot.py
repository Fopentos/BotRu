import os
import logging
from telegram import Update, Bot, ChatMemberAdministrator
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackContext
from telegram.error import TelegramError, BadRequest
from sqlalchemy import create_engine, Column, Integer, String, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import sqlite3

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# База данных
Base = declarative_base()

class ChannelSettings(Base):
    __tablename__ = 'channel_settings'
    
    id = Column(Integer, primary_key=True)
    channel_id = Column(String, unique=True)
    channel_username = Column(String)
    owner_id = Column(String)
    is_active = Column(Boolean, default=True)
    auto_approve = Column(Boolean, default=True)
    max_daily_approvals = Column(Integer, default=1000)

class Admin(Base):
    __tablename__ = 'admins'
    
    id = Column(Integer, primary_key=True)
    channel_id = Column(String)
    admin_id = Column(String)

# Инициализация базы данных
engine = create_engine('sqlite:///bot_data.db')
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

# Конфигурация
BOT_TOKEN = os.getenv('BOT_TOKEN', '8295619077:AAH05zqWTC8Kv11dLJyaMlSWlXEJtmU_Too')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда начала работы с ботом"""
    user = update.effective_user
    await update.message.reply_text(
        f"👋 Привет, {user.first_name}!\n\n"
        "🤖 Я бот для автоматического принятия заявок в Telegram-каналах\n\n"
        "📋 Доступные команды:\n"
        "/add_channel - Добавить канал\n"
        "/settings - Настройки канала\n"
        "/stats - Статистика\n"
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
        session = Session()
        try:
            # Проверяем, не добавлен ли уже канал
            existing_channel = session.query(ChannelSettings).filter_by(
                channel_id=str(chat.id)
            ).first()
            
            if existing_channel:
                await update.message.reply_text("✅ Этот канал уже добавлен!")
                return
            
            # Добавляем новый канал
            new_channel = ChannelSettings(
                channel_id=str(chat.id),
                channel_username=channel_username,
                owner_id=user_id,
                is_active=True,
                auto_approve=True,
                max_daily_approvals=1000
            )
            
            # Добавляем владельца как администратора
            new_admin = Admin(
                channel_id=str(chat.id),
                admin_id=user_id
            )
            
            session.add(new_channel)
            session.add(new_admin)
            session.commit()
            
            await update.message.reply_text(
                f"✅ Канал @{channel_username} успешно добавлен!\n\n"
                "⚡ Бот теперь будет автоматически принимать заявки\n"
                "⚙ Используйте /settings для настройки"
            )
            
        finally:
            session.close()
            
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

async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Настройки канала"""
    user_id = str(update.effective_user.id)
    
    session = Session()
    try:
        # Получаем каналы пользователя
        user_channels = session.query(ChannelSettings).join(
            Admin, ChannelSettings.channel_id == Admin.channel_id
        ).filter(Admin.admin_id == user_id).all()
        
        if not user_channels:
            await update.message.reply_text(
                "❌ У вас нет добавленных каналов\n"
                "Используйте /add_channel чтобы добавить канал"
            )
            return
        
        if context.args:
            # Показать настройки конкретного канала
            channel_username = context.args[0].lstrip('@')
            channel = session.query(ChannelSettings).filter_by(
                channel_username=channel_username
            ).first()
            
            if channel and any(admin.admin_id == user_id for admin in channel.admins):
                settings_text = (
                    f"⚙ Настройки канала @{channel.channel_username}\n\n"
                    f"📊 Статус: {'🟢 ВКЛ' if channel.is_active else '🔴 ВЫКЛ'}\n"
                    f"🤖 Автопринятие: {'🟢 ВКЛ' if channel.auto_approve else '🔴 ВЫКЛ'}\n"
                    f"📈 Лимит принятий/день: {channel.max_daily_approvals}\n\n"
                    "Команды для изменения:\n"
                    "/enable @channel - Включить бота\n"
                    "/disable @channel - Выключить бота\n"
                    "/set_limit @channel число - Установить лимит\n"
                    "/toggle_auto @channel - Вкл/Выкл авто-принятие"
                )
                await update.message.reply_text(settings_text)
            else:
                await update.message.reply_text("❌ Канал не найден или нет доступа")
        else:
            # Показать список каналов
            channels_text = "📋 Ваши каналы:\n\n"
            for channel in user_channels:
                status = "🟢" if channel.is_active else "🔴"
                channels_text += f"{status} @{channel.channel_username}\n"
            
            channels_text += "\n📝 Для просмотра настроек: /settings @username"
            await update.message.reply_text(channels_text)
            
    finally:
        session.close()

async def enable_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Включить бота для канала"""
    await toggle_channel(update, context, True)

async def disable_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выключить бота для канала"""
    await toggle_channel(update, context, False)

async def toggle_channel(update: Update, context: ContextTypes.DEFAULT_TYPE, enable: bool):
    """Включение/выключение канала"""
    user_id = str(update.effective_user.id)
    
    if not context.args:
        await update.message.reply_text("❌ Укажите username канала")
        return
    
    channel_username = context.args[0].lstrip('@')
    
    session = Session()
    try:
        channel = session.query(ChannelSettings).filter_by(
            channel_username=channel_username
        ).first()
        
        if not channel or not any(admin.admin_id == user_id for admin in channel.admins):
            await update.message.reply_text("❌ Канал не найден или нет доступа")
            return
        
        channel.is_active = enable
        session.commit()
        
        status = "включен" if enable else "выключен"
        await update.message.reply_text(f"✅ Бот для @{channel_username} {status}")
        
    finally:
        session.close()

async def process_join_requests(context: ContextTypes.DEFAULT_TYPE):
    """Обработка заявок на вступление"""
    session = Session()
    try:
        active_channels = session.query(ChannelSettings).filter_by(
            is_active=True,
            auto_approve=True
        ).all()
        
        for channel in active_channels:
            try:
                # Получаем заявки на вступление
                join_requests = await context.bot.get_chat_join_requests(
                    chat_id=channel.channel_id
                )
                
                approved_count = 0
                for request in join_requests:
                    try:
                        # Принимаем заявку
                        await context.bot.approve_chat_join_request(
                            chat_id=channel.channel_id,
                            user_id=request.user.id
                        )
                        approved_count += 1
                        
                        # Логируем каждые 100 принятий
                        if approved_count % 100 == 0:
                            logger.info(f"Approved {approved_count} requests for {channel.channel_username}")
                            
                    except TelegramError as e:
                        logger.error(f"Error approving user {request.user.id}: {e}")
                        continue
                
                if approved_count > 0:
                    logger.info(f"Approved {approved_count} requests for {channel.channel_username}")
                    
            except TelegramError as e:
                logger.error(f"Error processing join requests for {channel.channel_username}: {e}")
                continue
                
    finally:
        session.close()

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика бота"""
    user_id = str(update.effective_user.id)
    
    session = Session()
    try:
        user_channels = session.query(ChannelSettings).join(
            Admin, ChannelSettings.channel_id == Admin.channel_id
        ).filter(Admin.admin_id == user_id).all()
        
        if not user_channels:
            await update.message.reply_text("❌ У вас нет добавленных каналов")
            return
        
        stats_text = "📊 Статистика ваших каналов:\n\n"
        
        for channel in user_channels:
            try:
                # Получаем информацию о канале
                chat = await context.bot.get_chat(channel.channel_id)
                members_count = await chat.get_member_count()
                
                stats_text += (
                    f"🔹 @{channel.channel_username}\n"
                    f"   👥 Участников: {members_count}\n"
                    f"   🤖 Статус: {'🟢 Активен' if channel.is_active else '🔴 Выключен'}\n"
                    f"   ⚡ Автопринятие: {'🟢 ВКЛ' if channel.auto_approve else '🔴 ВЫКЛ'}\n\n"
                )
            except:
                stats_text += f"🔹 @{channel.channel_username} - ❌ Ошибка получения данных\n\n"
        
        await update.message.reply_text(stats_text)
        
    finally:
        session.close()

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Помощь по командам"""
    help_text = """
🤖 **Auto-Join Bot - Помощь**

**Основные команды:**
/start - Начать работу
/add_channel @username - Добавить канал
/settings - Настройки каналов
/stats - Статистика

**Управление каналами:**
/enable @channel - Включить бота
/disable @channel - Выключить бота
/set_limit @channel число - Лимит принятий
/toggle_auto @channel - Вкл/Выкл авто-принятие

**Важно:**
1. Бот должен быть администратором канала
2. Нужны права: "Приглашать пользователей", "Добавлять участников"
3. Бот обрабатывает заявки каждые 30 секунд

**Поддержка:**
По вопросам: @your_support
    """
    await update.message.reply_text(help_text)

def main():
    """Запуск бота"""
    # Создаем приложение
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("add_channel", add_channel))
    application.add_handler(CommandHandler("settings", settings))
    application.add_handler(CommandHandler("enable", enable_channel))
    application.add_handler(CommandHandler("disable", disable_channel))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("help", help_command))
    
    # Настраиваем планировщик для обработки заявок
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
    webhook_url = os.environ.get('RAILWAY_STATIC_URL')
    
    if webhook_url:
        # Используем webhook на Railway
        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=BOT_TOKEN,
            webhook_url=f"{webhook_url}/{BOT_TOKEN}"
        )
    else:
        # Используем polling для разработки
        application.run_polling()

if __name__ == '__main__':
    main()