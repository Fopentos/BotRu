import os
import logging
import asyncio
from datetime import datetime
from typing import Dict, List, Set

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    ReplyKeyboardMarkup, 
    KeyboardButton,
    ChatJoinRequest
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from apscheduler.schedulers.asyncio import AsyncIOScheduler

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
class ChannelAdd(StatesGroup):
    waiting_for_channel = State()

# База данных в памяти
class ChannelDB:
    def __init__(self):
        self.channels: Dict[str, Dict] = {}
        self.user_channels: Dict[str, Set[str]] = {}
        self.processed_users: Set[str] = set()
    
    def add_channel(self, channel_id: str, channel_title: str, owner_id: str):
        self.channels[channel_id] = {
            'title': channel_title,
            'owner_id': owner_id,
            'is_active': True,
            'auto_approve': True,
            'total_approved': 0,
            'last_processed': None,
            'added_date': datetime.now()
        }
        
        if owner_id not in self.user_channels:
            self.user_channels[owner_id] = set()
        self.user_channels[owner_id].add(channel_id)
        
        logger.info(f"✅ Channel added: {channel_title} (ID: {channel_id})")
        return True
    
    def get_user_channels(self, user_id: str) -> List[Dict]:
        user_channels = []
        for channel_id in self.user_channels.get(user_id, set()):
            if channel_data := self.channels.get(channel_id):
                user_channels.append({
                    'channel_id': channel_id,
                    **channel_data
                })
        return user_channels
    
    def get_channel(self, channel_id: str) -> Dict:
        return self.channels.get(channel_id)
    
    def mark_user_processed(self, channel_id: str, user_id: str):
        key = f"{channel_id}_{user_id}"
        self.processed_users.add(key)
    
    def is_user_processed(self, channel_id: str, user_id: str) -> bool:
        key = f"{channel_id}_{user_id}"
        return key in self.processed_users
    
    def increment_approved(self, channel_id: str):
        if channel := self.channels.get(channel_id):
            channel['total_approved'] = channel.get('total_approved', 0) + 1

# Инициализация базы данных
db = ChannelDB()

# Клавиатуры
def get_main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📢 Добавить канал")],
            [KeyboardButton(text="🚀 Принять все заявки")],
            [KeyboardButton(text="📊 Статус"), KeyboardButton(text="📋 Мои каналы")]
        ],
        resize_keyboard=True
    )

def get_management_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚀 Принять все заявки")],
            [KeyboardButton(text="📊 Статус"), KeyboardButton(text="📋 Мои каналы")],
            [KeyboardButton(text="📢 Добавить канал")]
        ],
        resize_keyboard=True
    )

# Команда /start
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user = message.from_user
    await message.answer(
        f"👋 <b>Привет, {user.first_name}!</b>\n\n"
        "🤖 Я бот для автоматического принятия заявок в приватных Telegram-каналах\n\n"
        "⚡ <b>Оптимизирован для массового принятия (3000+ заявок)</b>\n"
        "🚀 Скорость: 10 заявок в секунду\n\n"
        "📋 <b>Способы добавления канала:</b>\n"
        "1. 📢 Нажмите 'Добавить канал' для инструкций\n"
        "2. 🔄 Перешлите любое сообщение из канала\n\n"
        "🔧 <b>Требования:</b>\n"
        "• Бот должен быть администратором канала\n"
        "• Все права должны быть включены",
        reply_markup=get_main_keyboard()
    )

# Информация о добавлении канала
@dp.message(F.text == "📢 Добавить канал")
async def add_channel_info(message: types.Message, state: FSMContext):
    await message.answer(
        "📋 <b>Способы добавления канала:</b>\n\n"
        "1. <b>Перешлите сообщение</b> - просто перешлите любое сообщение из вашего канала\n"
        "2. <b>Убедитесь, что бот администратор</b> - бот должен быть администратором с правами:\n"
        "   ✓ Добавлять подписчиков\n"
        "   ✓ Приглашать пользователей\n"
        "   ✓ Одобрять заявки\n\n"
        "🔧 <b>Как добавить бота:</b>\n"
        "1. Зайдите в настройки канала\n"
        "2. Выберите 'Администраторы'\n"
        "3. Добавьте бота как администратора\n"
        "4. Включите ВСЕ права\n"
        "5. Перешлите сообщение из канала боту",
        reply_markup=get_main_keyboard()
    )

# Обработка пересланных сообщений
@dp.message(F.forward_from_chat)
async def handle_forwarded_message(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    forwarded_chat = message.forward_from_chat
    
    if forwarded_chat.type != "channel":
        await message.answer("❌ <b>Это не канал!</b> Пожалуйста, перешлите сообщение из канала.")
        return
    
    try:
        # Проверяем права бота в канале
        bot_member = await bot.get_chat_member(forwarded_chat.id, bot.id)
        
        # Правильная проверка: является ли бот администратором
        if not isinstance(bot_member, types.ChatMemberAdministrator):
            await message.answer(
                f"❌ <b>Бот не является администратором канала '{forwarded_chat.title}'!</b>\n\n"
                "📋 <b>Чтобы добавить бота:</b>\n"
                "1. Зайдите в настройки канала\n"
                "2. Выберите 'Администраторы'\n"
                "3. Добавьте бота как администратора\n"
                "4. Дайте ВСЕ права\n\n"
                "После этого перешлите сообщение из канала снова"
            )
            return
        
        # Проверяем конкретные права
        missing_permissions = []
        if not bot_member.can_invite_users:
            missing_permissions.append("❌ Приглашать пользователей")
        if not bot_member.can_invite_users:  # В aiogram это право для принятия заявок
            missing_permissions.append("❌ Одобрять заявки")
        
        if missing_permissions:
            await message.answer(
                f"❌ <b>Недостаточно прав в канале '{forwarded_chat.title}'!</b>\n\n"
                "Боту нужны ВСЕ эти права:\n" +
                "\n".join(missing_permissions) +
                "\n\nОбновите права бота и попробуйте снова."
            )
            return
        
        # Проверяем, не добавлен ли уже канал
        existing_channel = db.get_channel(str(forwarded_chat.id))
        if existing_channel:
            await message.answer(
                f"✅ <b>Канал '{forwarded_chat.title}' уже добавлен!</b>\n\n"
                f"🚀 Для принятия заявок нажмите '🚀 Принять все заявки'",
                reply_markup=get_management_keyboard()
            )
            return
        
        # Добавляем канал в базу
        db.add_channel(str(forwarded_chat.id), forwarded_chat.title, user_id)
        
        # Получаем количество ожидающих заявок
        pending_count = 0
        try:
            # Используем правильный метод для получения заявок
            join_requests = await bot.get_chat_join_requests(chat_id=forwarded_chat.id)
            async for _ in join_requests:
                pending_count += 1
        except Exception as e:
            logger.warning(f"Could not get join requests: {e}")
        
        success_message = (
            f"✅ <b>Канал добавлен через пересланное сообщение!</b>\n\n"
            f"📝 <b>Название:</b> {forwarded_chat.title}\n"
            f"🆔 <b>ID:</b> <code>{forwarded_chat.id}</code>\n"
            f"⏳ <b>Ожидающих заявок:</b> {pending_count}\n\n"
            f"🚀 <b>Для принятия заявок нажмите '🚀 Принять все заявки'</b>"
        )
        
        await message.answer(success_message, reply_markup=get_management_keyboard())
        
    except Exception as e:
        logger.error(f"Error processing forwarded message: {e}")
        await message.answer(f"❌ <b>Ошибка:</b> {str(e)}")

# Список каналов пользователя
@dp.message(F.text == "📋 Мои каналы")
async def list_user_channels(message: types.Message):
    user_id = str(message.from_user.id)
    user_channels = db.get_user_channels(user_id)
    
    if not user_channels:
        await message.answer(
            "❌ <b>У вас нет добавленных каналов</b>\n\n"
            "Нажмите '📢 Добавить канал' чтобы начать",
            reply_markup=get_main_keyboard()
        )
        return
    
    channels_text = "📋 <b>Ваши каналы:</b>\n\n"
    for i, channel in enumerate(user_channels, 1):
        status = "🟢" if channel['is_active'] else "🔴"
        approved = channel.get('total_approved', 0)
        channels_text += f"{status} <b>{i}. {channel['title']}</b>\n"
        channels_text += f"   ✅ Принято: {approved} заявок\n"
        channels_text += f"   🆔 ID: <code>{channel['channel_id']}</code>\n\n"
    
    await message.answer(channels_text, reply_markup=get_management_keyboard())

# Статус канала
@dp.message(F.text == "📊 Статус")
async def channel_status(message: types.Message):
    user_id = str(message.from_user.id)
    user_channels = db.get_user_channels(user_id)
    
    if not user_channels:
        await message.answer(
            "❌ <b>У вас нет добавленных каналов</b>\n\n"
            "Нажмите '📢 Добавить канал' чтобы начать",
            reply_markup=get_main_keyboard()
        )
        return
    
    # Берем первый канал пользователя
    channel = user_channels[0]
    
    try:
        # Получаем текущие заявки
        pending_count = 0
        try:
            join_requests = await bot.get_chat_join_requests(chat_id=int(channel['channel_id']))
            async for _ in join_requests:
                pending_count += 1
        except Exception as e:
            logger.warning(f"Could not get join requests: {e}")
        
        total_approved = channel.get('total_approved', 0)
        
        status_text = (
            f"📊 <b>Статус канала:</b> {channel['title']}\n\n"
            f"⏳ <b>Ожидающих заявок:</b> {pending_count}\n"
            f"✅ <b>Всего принято:</b> {total_approved}\n"
            f"🔄 <b>Статус:</b> {'🟢 Активен' if channel['is_active'] else '🔴 Выключен'}\n"
            f"⚡ <b>Автопринятие:</b> {'🟢 ВКЛ' if channel['auto_approve'] else '🔴 ВЫКЛ'}\n\n"
        )
        
        if pending_count > 0:
            estimated_time = pending_count / 10  # 10 заявок в секунду
            if estimated_time > 60:
                status_text += f"⏱ <b>Примерное время обработки:</b> {estimated_time/60:.1f} минут\n"
            else:
                status_text += f"⏱ <b>Примерное время обработки:</b> {estimated_time:.1f} секунд\n"
            
            status_text += f"🚀 <b>Для запуска нажмите '🚀 Принять все заявки'</b>"
        else:
            status_text += "🎉 <b>Нет ожидающих заявок!</b>"
        
        await message.answer(status_text, reply_markup=get_management_keyboard())
        
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        await message.answer(f"❌ <b>Ошибка при получении статуса:</b> {e}")

# Турбо-режим принятия заявок
@dp.message(F.text == "🚀 Принять все заявки")
async def turbo_approve(message: types.Message):
    user_id = str(message.from_user.id)
    user_channels = db.get_user_channels(user_id)
    
    if not user_channels:
        await message.answer(
            "❌ <b>У вас нет добавленных каналов</b>\n\n"
            "Нажмите '📢 Добавить канал' чтобы начать",
            reply_markup=get_main_keyboard()
        )
        return
    
    # Используем первый канал пользователя
    channel = user_channels[0]
    channel_id = int(channel['channel_id'])
    
    try:
        # Получаем все заявки через правильный метод
        join_requests = await bot.get_chat_join_requests(chat_id=channel_id)
        requests_list = []
        async for request in join_requests:
            requests_list.append(request)
        
        total = len(requests_list)
        
        if total == 0:
            await message.answer("🎉 <b>Нет заявок для принятия!</b>")
            return
        
        # Запускаем процесс принятия
        status_msg = await message.answer(
            f"🚀 <b>ЗАПУСК ОБРАБОТКИ</b>\n\n"
            f"📝 <b>Канал:</b> {channel['title']}\n"
            f"📊 <b>Обнаружено заявок:</b> {total}\n"
            f"⚡ <b>Начинаем обработку...</b>"
        )
        
        approved = 0
        failed = 0
        
        for i, request in enumerate(requests_list):
            try:
                # Принимаем заявку через правильный метод
                await bot.approve_chat_join_request(
                    chat_id=channel_id,
                    user_id=request.user.id
                )
                approved += 1
                db.increment_approved(channel['channel_id'])
                
                # Обновляем статус каждые 20 заявок
                if i % 20 == 0:
                    await status_msg.edit_text(
                        f"🚀 <b>ОБРАБОТКА</b>\n\n"
                        f"📝 <b>Канал:</b> {channel['title']}\n"
                        f"📊 <b>Прогресс:</b> {i+1}/{total}\n"
                        f"✅ <b>Принято:</b> {approved}\n"
                        f"❌ <b>Ошибок:</b> {failed}"
                    )
                
                # Задержка для избежания лимитов
                await asyncio.sleep(0.1)
                
            except Exception as e:
                logger.error(f"Ошибка принятия заявки: {e}")
                failed += 1
        
        # Финальный результат
        await status_msg.edit_text(
            f"🎉 <b>ОБРАБОТКА ЗАВЕРШЕНА!</b>\n\n"
            f"📝 <b>Канал:</b> {channel['title']}\n"
            f"📊 <b>Итоги:</b>\n"
            f"✅ <b>Успешно принято:</b> {approved}/{total}\n"
            f"❌ <b>Ошибок:</b> {failed}\n\n"
            f"💰 <b>Всего принято в канале:</b> {channel['total_approved']}"
        )
        
    except Exception as e:
        logger.error(f"Error in turbo_approve: {e}")
        await message.answer(f"❌ <b>Ошибка:</b> {str(e)}")

# Обработчик входящих заявок в реальном времени
@dp.chat_join_request()
async def handle_chat_join_request(chat_join: ChatJoinRequest):
    """Автоматическое принятие заявок при их поступлении"""
    channel_id = str(chat_join.chat.id)
    user_id = chat_join.from_user.id
    
    # Проверяем, есть ли канал в базе и активен ли он
    channel = db.get_channel(channel_id)
    if not channel or not channel['is_active'] or not channel['auto_approve']:
        return
    
    try:
        # Одобряем заявку
        await chat_join.approve()
        db.increment_approved(channel_id)
        db.mark_user_processed(channel_id, str(user_id))
        
        logger.info(f"✅ Автоматически принята заявка от {user_id} в канале {channel['title']}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка автоматического принятия заявки: {e}")

# Фоновая обработка заявок
async def process_pending_requests():
    """Фоновая задача для автоматического принятия заявок"""
    for channel_id, channel in db.channels.items():
        if not channel['is_active'] or not channel['auto_approve']:
            continue
            
        try:
            join_requests = await bot.get_chat_join_requests(chat_id=int(channel_id))
            requests_list = []
            async for request in join_requests:
                requests_list.append(request)
            
            if not requests_list:
                continue
            
            logger.info(f"🔄 Processing {len(requests_list)} requests for {channel['title']}")
            
            approved = 0
            for request in requests_list:
                if db.is_user_processed(channel_id, str(request.user.id)):
                    continue
                    
                try:
                    await bot.approve_chat_join_request(
                        chat_id=int(channel_id),
                        user_id=request.user.id
                    )
                    approved += 1
                    db.increment_approved(channel_id)
                    db.mark_user_processed(channel_id, str(request.user.id))
                    
                    # Задержка между запросами
                    await asyncio.sleep(0.1)
                    
                except Exception as e:
                    logger.error(f"Error approving request: {e}")
            
            if approved > 0:
                logger.info(f"✅ Approved {approved} requests for {channel['title']}")
            
            channel['last_processed'] = datetime.now()
            
        except Exception as e:
            logger.error(f"Error processing requests for {channel['title']}: {e}")

# Запуск бота
async def main():
    # Настраиваем планировщик для фоновой обработки
    scheduler = AsyncIOScheduler()
    scheduler.add_job(process_pending_requests, 'interval', seconds=30)
    scheduler.start()
    
    logger.info("✅ Бот для принятия заявок запускается...")
    
    # Запускаем бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
