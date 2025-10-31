import os
import json
import random
import asyncio
import logging
import math
from datetime import datetime
from collections import defaultdict

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

# 🔧 КОНФИГУРАЦИЯ
BOT_TOKEN = "8295619077:AAH05zqWTC8Kv11dLJyaMlSWlXEJtmU_Too"
ADMIN_IDS = [123456789]  # Замените на ваш ID

# 🎮 НАСТРОЙКИ ИГРЫ "РАКЕТА"
ROCKET_CONFIG = {
    "min_bet": 1,
    "max_bet": 100000,
    "multiplier_step": 0.01,
    "time_step": 0.1,  # УМЕНЬШЕНА СКОРОСТЬ: 0.1 секунды между обновлениями
    "max_multiplier": 10000,
    "rtp": 0.75,  # RTP 75%
}

# 🗃️ БАЗА ДАННЫХ В ПАМЯТИ
user_data = defaultdict(lambda: {
    'balance': 1000.0,
    'current_bet': 100,
    'total_games': 0,
    'games_won': 0,
    'total_wagered': 0,
    'total_won': 0,
    'max_multiplier': 0
})

active_games = {}

# 📊 ЛОГГИРОВАНИЕ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 🚀 ФУНКЦИИ ИГРЫ "РАКЕТА"
def generate_crash_point():
    """
    Генерирует точку взрыва с RTP ~75%
    Формула: crash_point = (1 - RTP) / (1 - random())
    Это обеспечивает правильное распределение для заданного RTP
    """
    r = random.random()
    crash_point = (1 - ROCKET_CONFIG['rtp']) / (1 - r)
    
    # Ограничиваем максимальным множителем
    return min(crash_point, ROCKET_CONFIG['max_multiplier'])

def calculate_explosion_chance(current_multiplier, crash_point):
    """Вычисляет приблизительный шанс взрыва на основе текущего множителя и точки взрыва"""
    if current_multiplier >= crash_point:
        return 1.0
    
    # Вероятность взрыва увеличивается по мере приближения к crash_point
    progress = current_multiplier / crash_point
    base_chance = 0.01  # 1% базовый шанс
    progress_chance = progress * 0.5  # До 50% при приближении к crash_point
    
    return min(base_chance + progress_chance, 0.9)  # Максимум 90%

def create_progress_bar(multiplier, length=20):
    """Создает визуальный прогресс-бар"""
    progress = min(multiplier / ROCKET_CONFIG['max_multiplier'], 1.0)
    filled = int(length * progress)
    bar = "▰" * filled + "▱" * (length - filled)
    return f"[{bar}] {progress*100:.1f}%"

async def rocket_game_task(user_id, bet_amount, message, context):
    """Асинхронная задача для игры в ракету"""
    try:
        # Генерируем точку взрыва с RTP 75%
        crash_point = generate_crash_point()
        logger.info(f"User {user_id}: crash_point = {crash_point:.2f}x")
        
        multiplier = 1.00
        start_time = datetime.now()
        
        while multiplier <= ROCKET_CONFIG['max_multiplier']:
            if user_id not in active_games:
                return  # Игра была отменена
            
            # Обновляем множитель в активной игре
            active_games[user_id]['current_multiplier'] = multiplier
            active_games[user_id]['crash_point'] = crash_point
            
            # Проверяем взрыв
            if multiplier >= crash_point:
                # ВЗРЫВ
                explosion_text = (
                    f"💥 РАКЕТА ВЗОРВАЛАСЬ НА {multiplier:.2f}x!\n\n"
                    f"💰 Вы потеряли: {bet_amount} ⭐\n"
                    f"📈 Точка взрыва: {crash_point:.2f}x\n"
                    f"🎯 RTP системы: {ROCKET_CONFIG['rtp']*100}%\n\n"
                    "💡 Ракета может взорваться на любом множителе!"
                )
                
                keyboard = [
                    [InlineKeyboardButton("🎮 Играть снова", callback_data="play_rocket")],
                    [InlineKeyboardButton("📊 Профиль", callback_data="profile")]
                ]
                
                await message.edit_text(
                    explosion_text,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                
                user_data[user_id]['total_games'] += 1
                user_data[user_id]['total_wagered'] += bet_amount
                if user_id in active_games:
                    del active_games[user_id]
                return
            
            # Обновляем сообщение
            potential_win = bet_amount * multiplier
            time_elapsed = (datetime.now() - start_time).total_seconds()
            
            # Вычисляем приблизительный шанс взрыва
            explosion_chance = calculate_explosion_chance(multiplier, crash_point)
            
            keyboard = [
                [InlineKeyboardButton(f"🎯 ЗАБРАТЬ {potential_win:.0f} ⭐", callback_data="cashout")],
                [InlineKeyboardButton("💥 ОСТАНОВИТЬ", callback_data="stop_game")]
            ]
            
            progress_bar = create_progress_bar(multiplier)
            
            try:
                await message.edit_text(
                    f"🚀 РАКЕТА ВЗЛЕТАЕТ...\n\n"
                    f"{progress_bar}\n"
                    f"📈 Множитель: {multiplier:.2f}x\n"
                    f"💰 Выигрыш: {potential_win:.0f} ⭐\n"
                    f"⏰ Время: {time_elapsed:.1f} сек\n"
                    f"🎯 Приблизительный шанс взрыва: {explosion_chance*100:.1f}%",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except Exception as e:
                logger.error(f"Ошибка редактирования сообщения: {e}")
                if user_id in active_games:
                    del active_games[user_id]
                return
            
            await asyncio.sleep(ROCKET_CONFIG['time_step'])
            multiplier += ROCKET_CONFIG['multiplier_step']
        
        # Достигнут максимальный множитель (очень редкий случай)
        win_amount = bet_amount * ROCKET_CONFIG['max_multiplier']
        user_data[user_id]['balance'] += win_amount
        user_data[user_id]['total_games'] += 1
        user_data[user_id]['games_won'] += 1
        user_data[user_id]['total_wagered'] += bet_amount
        user_data[user_id]['total_won'] += win_amount
        user_data[user_id]['max_multiplier'] = max(user_data[user_id]['max_multiplier'], ROCKET_CONFIG['max_multiplier'])
        
        await message.edit_text(
            f"🎉 МАКСИМАЛЬНЫЙ МНОЖИТЕЛЬ ДОСТИГНУТ!\n\n"
            f"💰 Ваш выигрыш: {win_amount:.0f} ⭐\n"
            f"📈 Множитель: {ROCKET_CONFIG['max_multiplier']}x\n"
            f"💎 Новый баланс: {user_data[user_id]['balance']:.0f} ⭐",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🎮 Играть снова", callback_data="play_rocket"),
                InlineKeyboardButton("📊 Профиль", callback_data="profile")
            ]])
        )
        
        if user_id in active_games:
            del active_games[user_id]
        
    except Exception as e:
        logger.error(f"Ошибка в игре: {e}")
        if user_id in active_games:
            del active_games[user_id]

# 👤 КОМАНДЫ ПОЛЬЗОВАТЕЛЯ
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    welcome_text = f"""
🚀 ДОБРО ПОЖАЛОВАТЬ В ROCKET CASINO!

Основные команды:
/profile - ваш профиль и баланс
/bet [сумма] - изменить ставку
/rocket - запустить игру

🎮 ИГРА "РАКЕТА":
• Ставка умножается на растущий множитель
• Заберите выигрыш до взрыва ракеты
• Множитель растет до {ROCKET_CONFIG['max_multiplier']}x
• Ракета может взорваться в любой момент
• RTP системы: {ROCKET_CONFIG['rtp']*100}%
    """
    
    keyboard = [
        [InlineKeyboardButton("🎮 Играть в Rocket", callback_data="play_rocket")],
        [InlineKeyboardButton("📊 Профиль", callback_data="profile")],
        [InlineKeyboardButton("🎯 Изменить ставку", callback_data="change_bet")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        await update.message.reply_text(welcome_text, reply_markup=reply_markup)
    else:
        await update.callback_query.message.reply_text(welcome_text, reply_markup=reply_markup)

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = user_data[user_id]
    
    win_rate = (data['games_won'] / data['total_games'] * 100) if data['total_games'] > 0 else 0
    
    profile_text = f"""
📊 ПРОФИЛЬ ИГРОКА

💰 Баланс: {data['balance']:.0f} ⭐
🎯 Текущая ставка: {data['current_bet']} ⭐

📈 Статистика:
🎮 Всего игр: {data['total_games']}
🏆 Побед: {data['games_won']}
📊 Винрейт: {win_rate:.1f}%
💎 Макс. множитель: {data['max_multiplier']:.2f}x
💰 Всего поставлено: {data['total_wagered']:.0f} ⭐
🎁 Всего выиграно: {data['total_won']:.0f} ⭐
    """
    
    keyboard = [
        [InlineKeyboardButton("🎮 Играть в Rocket", callback_data="play_rocket")],
        [InlineKeyboardButton("🎯 Изменить ставку", callback_data="change_bet")]
    ]
    
    if user_id in ADMIN_IDS:
        keyboard.append([InlineKeyboardButton("👑 Админ Панель", callback_data="admin_panel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(profile_text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(profile_text, reply_markup=reply_markup)

async def bet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not context.args:
        await update.message.reply_text(
            f"🎯 Текущая ставка: {user_data[user_id]['current_bet']} ⭐\n\n"
            f"Использование: /bet <сумма>\n"
            f"Минимум: {ROCKET_CONFIG['min_bet']} ⭐\n"
            f"Максимум: {ROCKET_CONFIG['max_bet']} ⭐"
        )
        return
    
    try:
        new_bet = int(context.args[0])
        
        if new_bet < ROCKET_CONFIG['min_bet']:
            await update.message.reply_text(f"❌ Минимальная ставка: {ROCKET_CONFIG['min_bet']} ⭐")
            return
            
        if new_bet > ROCKET_CONFIG['max_bet']:
            await update.message.reply_text(f"❌ Максимальная ставка: {ROCKET_CONFIG['max_bet']} ⭐")
            return
            
        user_data[user_id]['current_bet'] = new_bet
        
        await update.message.reply_text(f"✅ Ставка изменена на {new_bet} ⭐")
        
    except ValueError:
        await update.message.reply_text("❌ Пожалуйста, введите корректное число")

async def rocket_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_rocket_game(update, context)

# 🎮 ОБРАБОТЧИКИ ИГРЫ
async def start_rocket_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = user_data[user_id]
    bet_amount = data['current_bet']
    
    # Проверка активной игры
    if user_id in active_games:
        if update.callback_query:
            await update.callback_query.answer("❌ У вас уже есть активная игра!", show_alert=True)
        else:
            await update.message.reply_text("❌ У вас уже есть активная игра!")
        return
    
    # Проверка баланса
    if data['balance'] < bet_amount:
        if update.callback_query:
            await update.callback_query.answer("❌ Недостаточно средств!", show_alert=True)
            await update.callback_query.edit_message_text(
                f"❌ Недостаточно средств!\n\n"
                f"💰 Ваш баланс: {data['balance']:.0f} ⭐\n"
                f"🎯 Требуется: {bet_amount} ⭐",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📊 Профиль", callback_data="profile"),
                    InlineKeyboardButton("🎯 Изменить ставку", callback_data="change_bet")
                ]])
            )
        else:
            await update.message.reply_text(
                f"❌ Недостаточно средств!\n"
                f"💰 Баланс: {data['balance']:.0f} ⭐\n"
                f"🎯 Ставка: {bet_amount} ⭐"
            )
        return
    
    # Списываем ставку
    user_data[user_id]['balance'] -= bet_amount
    
    # Создаем сообщение игры
    keyboard = [[InlineKeyboardButton("🔄 Запуск...", callback_data="loading")]]
    
    if update.callback_query:
        message = await update.callback_query.edit_message_text(
            f"🚀 ПОДГОТОВКА РАКЕТЫ...\n\n"
            f"💰 Ставка: {bet_amount} ⭐\n"
            f"📈 Множитель: 1.00x\n"
            f"⏰ Ожидание старта...",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        message = await update.message.reply_text(
            f"🚀 ПОДГОТОВКА РАКЕТЫ...\n\n"
            f"💰 Ставка: {bet_amount} ⭐\n"
            f"📈 Множитель: 1.00x\n"
            f"⏰ Ожидание старта...",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    # Запускаем игру
    active_games[user_id] = {
        'bet_amount': bet_amount,
        'message_id': message.message_id,
        'chat_id': message.chat_id,
        'current_multiplier': 1.00
    }
    
    # Запускаем асинхронную задачу
    asyncio.create_task(rocket_game_task(user_id, bet_amount, message, context))

async def cashout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("✅ Вы успешно забрали выигрыш!")
    
    user_id = query.from_user.id
    
    if user_id not in active_games:
        await query.answer("❌ Игра не найдена или уже завершена!", show_alert=True)
        return
    
    # Получаем текущий множитель из активной игры
    game_data = active_games[user_id]
    multiplier = game_data.get('current_multiplier', 1.0)
    bet_amount = game_data['bet_amount']
    win_amount = bet_amount * multiplier
    
    # Начисляем выигрыш
    user_data[user_id]['balance'] += win_amount
    user_data[user_id]['total_games'] += 1
    user_data[user_id]['games_won'] += 1
    user_data[user_id]['total_wagered'] += bet_amount
    user_data[user_id]['total_won'] += win_amount
    user_data[user_id]['max_multiplier'] = max(user_data[user_id]['max_multiplier'], multiplier)
    
    # Удаляем игру
    del active_games[user_id]
    
    await query.edit_message_text(
        f"🎉 ВЫ УСПЕШНО ЗАБРАЛИ ВЫИГРЫШ!\n\n"
        f"💰 Ваш выигрыш: {win_amount:.0f} ⭐\n"
        f"📈 Множитель: {multiplier:.2f}x\n"
        f"💎 Новый баланс: {user_data[user_id]['balance']:.0f} ⭐",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🎮 Играть снова", callback_data="play_rocket"),
            InlineKeyboardButton("📊 Профиль", callback_data="profile")
        ]])
    )

async def stop_game_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("🛑 Игра остановлена!")
    
    user_id = query.from_user.id
    
    if user_id in active_games:
        bet_amount = active_games[user_id]['bet_amount']
        user_data[user_id]['balance'] += bet_amount  # Возвращаем ставку
        del active_games[user_id]
        
        await query.edit_message_text(
            f"🛑 ИГРА ОСТАНОВЛЕНА\n\n"
            f"💰 Возвращено: {bet_amount} ⭐\n"
            f"💎 Баланс: {user_data[user_id]['balance']:.0f} ⭐",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🎮 Играть снова", callback_data="play_rocket"),
                InlineKeyboardButton("📊 Профиль", callback_data="profile")
            ]])
        )

# 👑 АДМИН КОМАНДЫ
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if user_id not in ADMIN_IDS:
        await query.answer("❌ Доступ запрещен!", show_alert=True)
        return
    
    total_balance = sum(data['balance'] for data in user_data.values())
    total_games = sum(data['total_games'] for data in user_data.values())
    total_wagered = sum(data['total_wagered'] for data in user_data.values())
    total_won = sum(data['total_won'] for data in user_data.values())
    
    # Расчет RTP на основе реальных данных
    actual_rtp = (total_won / total_wagered * 100) if total_wagered > 0 else 0
    
    admin_text = f"""
👑 АДМИН ПАНЕЛЬ

📊 Статистика:
👥 Пользователей: {len(user_data)}
💰 Общий баланс: {total_balance:.0f} ⭐
🎮 Всего игр: {total_games}
💸 Общий оборот: {total_wagered:.0f} ⭐
🎁 Выплачено: {total_won:.0f} ⭐
📈 Реальный RTP: {actual_rtp:.1f}%

⚡ Активных игр: {len(active_games)}
    """
    
    keyboard = [
        [InlineKeyboardButton("➕ Пополнить баланс", callback_data="admin_add_balance")],
        [InlineKeyboardButton("📊 Общая статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("🔙 Назад", callback_data="profile")]
    ]
    
    await query.edit_message_text(admin_text, reply_markup=InlineKeyboardMarkup(keyboard))

async def add_balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ Доступ запрещен!")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /addbalance <user_id> <amount>")
        return
    
    try:
        target_id = int(context.args[0])
        amount = float(context.args[1])
        
        user_data[target_id]['balance'] += amount
        
        await update.message.reply_text(
            f"✅ Баланс пользователя {target_id} пополнен на {amount} ⭐\n"
            f"💰 Новый баланс: {user_data[target_id]['balance']:.0f} ⭐"
        )
        
    except ValueError:
        await update.message.reply_text("❌ Неверный формат данных!")

async def admin_add_balance_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if user_id not in ADMIN_IDS:
        await query.answer("❌ Доступ запрещен!", show_alert=True)
        return
    
    await query.edit_message_text(
        "➕ ПОПОЛНЕНИЕ БАЛАНСА\n\n"
        "Используйте команду:\n"
        "/addbalance <user_id> <amount>\n\n"
        "Пример:\n"
        "/addbalance 123456789 1000",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")
        ]])
    )

# 🔄 CALLBACK ОБРАБОТЧИКИ
async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    callback_data = query.data
    
    if callback_data == "profile":
        await profile(update, context)
    elif callback_data == "play_rocket":
        await start_rocket_game(update, context)
    elif callback_data == "change_bet":
        await query.edit_message_text(
            "🎯 Изменение ставки\n\n"
            "Используйте команду /bet <сумма>\n"
            f"Диапазон: {ROCKET_CONFIG['min_bet']}-{ROCKET_CONFIG['max_bet']} ⭐",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Назад", callback_data="profile")
            ]])
        )
    elif callback_data == "admin_panel":
        await admin_panel(update, context)
    elif callback_data == "admin_stats":
        await query.edit_message_text(
            "📊 Статистика в разработке...",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")
            ]])
        )
    elif callback_data == "admin_add_balance":
        await admin_add_balance_handler(update, context)

# 🚀 ЗАПУСК БОТА
def main():
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Команды пользователя
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("profile", profile))
    application.add_handler(CommandHandler("bet", bet_command))
    application.add_handler(CommandHandler("rocket", rocket_command))
    application.add_handler(CommandHandler("addbalance", add_balance_command))
    
    # Обработчики callback
    application.add_handler(CallbackQueryHandler(handle_callbacks))
    
    # Обработчики для кнопок игры (ВАЖНО: регистрируем отдельно с явными паттернами)
    application.add_handler(CallbackQueryHandler(cashout_handler, pattern="^cashout$"))
    application.add_handler(CallbackQueryHandler(stop_game_handler, pattern="^stop_game$"))
    
    # Обработчик для всех остальных callback (должен быть последним)
    application.add_handler(CallbackQueryHandler(handle_callbacks))
    
    print("🚀 Rocket Casino Bot запущен!")
    print(f"⚡ Скорость игры: {ROCKET_CONFIG['time_step']} сек")
    print(f"📈 Максимальный множитель: {ROCKET_CONFIG['max_multiplier']}x")
    print(f"🎯 RTP системы: {ROCKET_CONFIG['rtp']*100}%")
    
    application.run_polling()

if __name__ == "__main__":
    main()
