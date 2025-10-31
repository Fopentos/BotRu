import os
import json
import random
import asyncio
import logging
from datetime import datetime
from collections import defaultdict

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# 🔧 КОНФИГУРАЦИЯ
BOT_TOKEN = "8295619077:AAH05zqWTC8Kv11dLJyaMlSWlXEJtmU_Too"
ADMIN_IDS = [123456789]  # Замените на ваш ID

# 🎮 НАСТРОЙКИ ИГРЫ "РАКЕТА"
ROCKET_CONFIG = {
    "min_bet": 1,
    "max_bet": 100000,
    "multiplier_step": 0.01,
    "time_step": 0.2,  # секунды между обновлениями
    "max_multiplier": 10000,
    "instant_explosion_chance": 0.01,  # 1% шанс мгновенного взрыва
    "base_explosion_chance": 0.005,    # Базовый шанс взрыва
    "chance_growth": 0.0001            # Рост шанса с множителем
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

# 🚀 ФУНКЦИИ ИГРЫ "РАКЕТА"
def calculate_explosion_chance(current_multiplier):
    """Вычисляет шанс взрыва на основе текущего множителя"""
    base_chance = ROCKET_CONFIG['base_explosion_chance']
    growth = ROCKET_CONFIG['chance_growth'] * current_multiplier
    return min(base_chance + growth, 0.5)  # Максимум 50% шанс

async def rocket_game_task(user_id, bet_amount, message, context):
    """Асинхронная задача для игры в ракету"""
    try:
        # Проверка мгновенного взрыва
        if random.random() < ROCKET_CONFIG['instant_explosion_chance']:
            await message.edit_text(
                "💥 РАКЕТА ВЗОРВАЛАСЬ СРАЗУ!\n"
                f"💰 Вы потеряли: {bet_amount} ⭐\n"
                f"📈 Множитель: 1.00x",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🎮 Играть снова", callback_data="play_rocket"),
                    InlineKeyboardButton("📊 Профиль", callback_data="profile")
                ]])
            )
            user_data[user_id]['total_games'] += 1
            user_data[user_id]['total_wagered'] += bet_amount
            if user_id in active_games:
                del active_games[user_id]
            return

        multiplier = 1.00
        start_time = datetime.now()
        
        while multiplier <= ROCKET_CONFIG['max_multiplier']:
            if user_id not in active_games:
                return  # Игра была отменена
            
            # Проверяем взрыв
            explosion_chance = calculate_explosion_chance(multiplier)
            if random.random() < explosion_chance:
                # ВЗРЫВ
                await message.edit_text(
                    f"💥 РАКЕТА ВЗОРВАЛАСЬ!\n"
                    f"💰 Вы потеряли: {bet_amount} ⭐\n"
                    f"📈 Достигнут множитель: {multiplier:.2f}x\n"
                    f"🎯 Шанс взрыва был: {explosion_chance*100:.1f}%",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🎮 Играть снова", callback_data="play_rocket"),
                        InlineKeyboardButton("📊 Профиль", callback_data="profile")
                    ]])
                )
                user_data[user_id]['total_games'] += 1
                user_data[user_id]['total_wagered'] += bet_amount
                if user_id in active_games:
                    del active_games[user_id]
                return
            
            # Обновляем сообщение
            potential_win = bet_amount * multiplier
            time_elapsed = (datetime.now() - start_time).total_seconds()
            
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
                    f"🎯 Шанс взрыва: {explosion_chance*100:.1f}%",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except Exception as e:
                logging.error(f"Ошибка редактирования сообщения: {e}")
                if user_id in active_games:
                    del active_games[user_id]
                return
            
            await asyncio.sleep(ROCKET_CONFIG['time_step'])
            multiplier += ROCKET_CONFIG['multiplier_step']
        
        # Достигнут максимальный множитель
        win_amount = bet_amount * ROCKET_CONFIG['max_multiplier']
        user_data[user_id]['balance'] += win_amount
        user_data[user_id]['total_games'] += 1
        user_data[user_id]['games_won'] += 1
        user_data[user_id]['total_wagered'] += bet_amount
        user_data[user_id]['total_won'] += win_amount
        user_data[user_id]['max_multiplier'] = max(user_data[user_id]['max_multiplier'], ROCKET_CONFIG['max_multiplier'])
        
        await message.edit_text(
            f"🎉 МАКСИМАЛЬНЫЙ МНОЖИТЕЛЬ ДОСТИГНУТ!\n"
            f"💰 Ваш выигрыш: {win_amount:.0f} ⭐\n"
            f"📈 Множитель: {ROCKET_CONFIG['max_multiplier']}x\n"
            f"💎 Новый баланс: {user_data[user_id]['balance']:.0f} ⭐",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🎮 Играть снова", callback_data="play_rocket"),
                InlineKeyboardButton("📊 Профиль", callback_data="profile")
            ]])
        )
        
    except Exception as e:
        logging.error(f"Ошибка в игре: {e}")
        if user_id in active_games:
            del active_games[user_id]

def create_progress_bar(multiplier, length=20):
    """Создает визуальный прогресс-бар"""
    progress = min(multiplier / ROCKET_CONFIG['max_multiplier'], 1.0)
    filled = int(length * progress)
    bar = "▰" * filled + "▱" * (length - filled)
    return f"[{bar}] {progress*100:.1f}%"

# 👤 КОМАНДЫ ПОЛЬЗОВАТЕЛЯ
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    welcome_text = """
🚀 ДОБРО ПОЖАЛОВАТЬ В ROCKET CASINO!

Основные команды:
/profile - ваш профиль и баланс
/bet [сумма] - изменить ставку
/rocket - запустить игру

🎮 ИГРА "РАКЕТА":
• Ставка умножается на растущий множитель
• Заберите выигрыш до взрыва ракеты
• Множитель растет до 10000x
• Ракета может взорваться в любой момент
    """
    
    keyboard = [
        [InlineKeyboardButton("🎮 Играть в Rocket", callback_data="play_rocket")],
        [InlineKeyboardButton("📊 Профиль", callback_data="profile")],
        [InlineKeyboardButton("🎯 Изменить ставку", callback_data="change_bet")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(welcome_text, reply_markup=reply_markup)

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
            await update.callback_query.answer("У вас уже есть активная игра!", show_alert=True)
        else:
            await update.message.reply_text("❌ У вас уже есть активная игра!")
        return
    
    # Проверка баланса
    if data['balance'] < bet_amount:
        if update.callback_query:
            await update.callback_query.answer("Недостаточно средств!", show_alert=True)
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
            "🚀 ПОДГОТОВКА РАКЕТЫ...\n\n"
            "💰 Ставка: {bet_amount} ⭐\n"
            "📈 Множитель: 1.00x\n"
            "⏰ Ожидание старта...".format(bet_amount=bet_amount),
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        message = await update.message.reply_text(
            "🚀 ПОДГОТОВКА РАКЕТЫ...\n\n"
            "💰 Ставка: {bet_amount} ⭐\n"
            "📈 Множитель: 1.00x\n"
            "⏰ Ожидание старта...".format(bet_amount=bet_amount),
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    # Запускаем игру
    active_games[user_id] = {
        'bet_amount': bet_amount,
        'message_id': message.message_id,
        'chat_id': message.chat_id
    }
    
    # Запускаем асинхронную задачу
    asyncio.create_task(rocket_game_task(user_id, bet_amount, message, context))

async def cashout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if user_id not in active_games:
        await query.answer("Игра не найдена!", show_alert=True)
        return
    
    # Находим текущий множитель из текста сообщения
    try:
        message_text = query.message.text
        multiplier_line = [line for line in message_text.split('\n') if 'Множитель:' in line][0]
        multiplier = float(multiplier_line.split(':')[1].replace('x', '').strip())
    except:
        multiplier = 1.0
    
    bet_amount = active_games[user_id]['bet_amount']
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
    await query.answer()
    
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
    user_id = query.from_user.id
    
    if user_id not in ADMIN_IDS:
        await query.answer("Доступ запрещен!", show_alert=True)
        return
    
    total_balance = sum(data['balance'] for data in user_data.values())
    total_games = sum(data['total_games'] for data in user_data.values())
    total_wagered = sum(data['total_wagered'] for data in user_data.values())
    
    admin_text = f"""
👑 АДМИН ПАНЕЛЬ

📊 Статистика:
👥 Пользователей: {len(user_data)}
💰 Общий баланс: {total_balance:.0f} ⭐
🎮 Всего игр: {total_games}
💸 Общий оборот: {total_wagered:.0f} ⭐

⚡ Активных игр: {len(active_games)}
    """
    
    keyboard = [
        [InlineKeyboardButton("📊 Общая статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("👥 Управление пользователями", callback_data="admin_users")],
        [InlineKeyboardButton("🔙 Назад", callback_data="profile")]
    ]
    
    await query.edit_message_text(admin_text, reply_markup=InlineKeyboardMarkup(keyboard))

async def add_balance_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    elif callback_data == "cashout":
        await cashout_handler(update, context)
    elif callback_data == "stop_game":
        await stop_game_handler(update, context)
    elif callback_data == "admin_panel":
        await admin_panel(update, context)
    elif callback_data == "admin_stats":
        await query.edit_message_text(
            "📊 Статистика в разработке...",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")
            ]])
        )

# 🚀 ЗАПУСК БОТА
def main():
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Команды пользователя
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("profile", profile))
    application.add_handler(CommandHandler("bet", bet_command))
    application.add_handler(CommandHandler("rocket", rocket_command))
    
    # Админ команды
    application.add_handler(CommandHandler("addbalance", add_balance_handler))
    
    # Обработчики callback
    application.add_handler(CallbackQueryHandler(handle_callbacks))
    
    print("🚀 Rocket Casino Bot запущен!")
    application.run_polling()

if __name__ == "__main__":
    main()
