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

# 🎮 НАСТРОЙКИ ИГРЫ "РАКЕТА"
ROCKET_CONFIG = {
    "min_bet": 1,
    "max_bet": 100000,
    "multiplier_step": 0.01,
    "time_step": 0.1,
    "max_multiplier": 10000,
    "rtp": 0.75,
}

# 🗃️ БАЗА ДАННЫХ В ПАМЯТИ
user_data = defaultdict(lambda: {
    'balance': 1000.0,
    'current_bet': 100,
    'total_games': 0,
    'games_won': 0,
    'total_wagered': 0,
    'total_won': 0,
})

active_games = {}

# 📊 ЛОГГИРОВАНИЕ
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 🚀 ФУНКЦИИ ИГРЫ
def generate_crash_point():
    r = random.random()
    crash_point = (1 - ROCKET_CONFIG['rtp']) / (1 - r)
    return min(crash_point, ROCKET_CONFIG['max_multiplier'])

async def rocket_game(user_id, bet_amount, message, context):
    try:
        crash_point = generate_crash_point()
        multiplier = 1.00
        
        active_games[user_id] = {
            'multiplier': multiplier,
            'bet_amount': bet_amount,
            'crash_point': crash_point,
            'running': True
        }
        
        while multiplier <= ROCKET_CONFIG['max_multiplier'] and active_games.get(user_id, {}).get('running', False):
            # Проверяем взрыв
            if multiplier >= crash_point:
                await message.edit_text(
                    f"💥 РАКЕТА ВЗОРВАЛАСЬ НА {multiplier:.2f}x!\n\n"
                    f"💰 Вы потеряли: {bet_amount} ⭐",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🎮 Играть снова", callback_data="play")
                    ]])
                )
                user_data[user_id]['total_games'] += 1
                user_data[user_id]['total_wagered'] += bet_amount
                break
            
            # Обновляем сообщение
            win_amount = bet_amount * multiplier
            
            await message.edit_text(
                f"🚀 РАКЕТА ЛЕТИТ...\n\n"
                f"📈 Множитель: {multiplier:.2f}x\n"
                f"💰 Выигрыш: {win_amount:.0f} ⭐\n"
                f"🎯 Взрыв на: {crash_point:.2f}x",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(f"🎯 Забрать {win_amount:.0f} ⭐", callback_data="cashout")
                ]])
            )
            
            await asyncio.sleep(ROCKET_CONFIG['time_step'])
            multiplier += ROCKET_CONFIG['multiplier_step']
            active_games[user_id]['multiplier'] = multiplier
            
        # Если игра не взорвалась и не завершена
        if active_games.get(user_id, {}).get('running', False):
            win_amount = bet_amount * multiplier
            user_data[user_id]['balance'] += win_amount
            user_data[user_id]['total_games'] += 1
            user_data[user_id]['games_won'] += 1
            user_data[user_id]['total_wagered'] += bet_amount
            user_data[user_id]['total_won'] += win_amount
            
            await message.edit_text(
                f"🎉 ВЫ ЗАБРАЛИ ВЫИГРЫШ!\n\n"
                f"💰 Ваш выигрыш: {win_amount:.0f} ⭐\n"
                f"📈 Множитель: {multiplier:.2f}x\n"
                f"💎 Баланс: {user_data[user_id]['balance']:.0f} ⭐",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🎮 Играть снова", callback_data="play")
                ]])
            )
            
    except Exception as e:
        logger.error(f"Ошибка в игре: {e}")
    finally:
        if user_id in active_games:
            del active_games[user_id]

# 👤 КОМАНДЫ
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🎮 Играть", callback_data="play")],
        [InlineKeyboardButton("📊 Профиль", callback_data="profile")],
        [InlineKeyboardButton("🎯 Ставка: 100 ⭐", callback_data="bet")]
    ]
    
    await update.message.reply_text(
        "🚀 Добро пожаловать в Rocket Casino!\n\n"
        "Нажмите 'Играть' чтобы начать!",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = user_data[user_id]
    
    win_rate = (data['games_won'] / data['total_games'] * 100) if data['total_games'] > 0 else 0
    
    text = f"""
📊 ПРОФИЛЬ

💰 Баланс: {data['balance']:.0f} ⭐
🎯 Ставка: {data['current_bet']} ⭐

🎮 Игр: {data['total_games']}
🏆 Побед: {data['games_won']}
📊 Винрейт: {win_rate:.1f}%
    """
    
    keyboard = [
        [InlineKeyboardButton("🎮 Играть", callback_data="play")],
        [InlineKeyboardButton("🎯 Изменить ставку", callback_data="bet")],
        [InlineKeyboardButton("💎 Пополнить баланс", callback_data="deposit")]
    ]
    
    if update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def bet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if context.args:
        try:
            new_bet = int(context.args[0])
            if ROCKET_CONFIG['min_bet'] <= new_bet <= ROCKET_CONFIG['max_bet']:
                user_data[user_id]['current_bet'] = new_bet
                await update.message.reply_text(f"✅ Ставка изменена на {new_bet} ⭐")
            else:
                await update.message.reply_text(f"❌ Ставка должна быть от {ROCKET_CONFIG['min_bet']} до {ROCKET_CONFIG['max_bet']} ⭐")
        except:
            await update.message.reply_text("❌ Введите число!")
    else:
        await update.message.reply_text(
            f"🎯 Текущая ставка: {user_data[user_id]['current_bet']} ⭐\n"
            f"Использование: /bet 100"
        )

# 🔄 ОБРАБОТЧИКИ КНОПОК
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    
    await query.answer()  # Важно: всегда отвечаем на callback
    
    logger.info(f"Callback received: {data} from user {user_id}")
    
    if data == "play":
        # Проверяем активную игру
        if user_id in active_games:
            await query.answer("❌ У вас уже есть активная игра!", show_alert=True)
            return
        
        # Проверяем баланс
        bet_amount = user_data[user_id]['current_bet']
        if user_data[user_id]['balance'] < bet_amount:
            await query.answer("❌ Недостаточно средств!", show_alert=True)
            await query.edit_message_text(
                f"❌ Недостаточно средств!\n\n"
                f"💰 Баланс: {user_data[user_id]['balance']:.0f} ⭐\n"
                f"🎯 Нужно: {bet_amount} ⭐",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("💎 Пополнить", callback_data="deposit"),
                    InlineKeyboardButton("📊 Профиль", callback_data="profile")
                ]])
            )
            return
        
        # Списываем ставку
        user_data[user_id]['balance'] -= bet_amount
        
        # Запускаем игру
        message = await query.edit_message_text(
            "🚀 РАКЕТА СТАРТУЕТ...\n\n"
            "Подготовка к запуску...",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Запуск...", callback_data="loading")
            ]])
        )
        
        # Запускаем игру в фоне
        asyncio.create_task(rocket_game(user_id, bet_amount, message, context))
        
    elif data == "cashout":
        if user_id in active_games:
            game = active_games[user_id]
            multiplier = game['multiplier']
            bet_amount = game['bet_amount']
            win_amount = bet_amount * multiplier
            
            # Начисляем выигрыш
            user_data[user_id]['balance'] += win_amount
            user_data[user_id]['total_games'] += 1
            user_data[user_id]['games_won'] += 1
            user_data[user_id]['total_wagered'] += bet_amount
            user_data[user_id]['total_won'] += win_amount
            
            # Завершаем игру
            game['running'] = False
            
            await query.answer(f"🎉 Вы забрали {win_amount:.0f} ⭐!")
            await query.edit_message_text(
                f"🎉 ВЫ ЗАБРАЛИ ВЫИГРЫШ!\n\n"
                f"💰 Выигрыш: {win_amount:.0f} ⭐\n"
                f"📈 Множитель: {multiplier:.2f}x\n"
                f"💎 Баланс: {user_data[user_id]['balance']:.0f} ⭐",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🎮 Играть снова", callback_data="play")
                ]])
            )
        else:
            await query.answer("❌ Игра не найдена!", show_alert=True)
            
    elif data == "profile":
        await profile(update, context)
        
    elif data == "bet":
        await query.edit_message_text(
            f"🎯 Изменить ставку\n\n"
            f"Текущая ставка: {user_data[user_id]['current_bet']} ⭐\n"
            f"Используйте команду: /bet 100\n"
            f"Диапазон: {ROCKET_CONFIG['min_bet']}-{ROCKET_CONFIG['max_bet']} ⭐",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Назад", callback_data="profile")
            ]])
        )
        
    elif data == "deposit":
        await query.edit_message_text(
            "💎 ПОПОЛНЕНИЕ БАЛАНСА\n\n"
            "Для тестирования используйте команду:\n"
            "/addbalance 1000\n\n"
            "Эта команда добавит 1000 ⭐ на ваш баланс.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Назад", callback_data="profile")
            ]])
        )

# 👑 АДМИН КОМАНДА (для тестов)
async def add_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if context.args:
        try:
            amount = float(context.args[0])
            user_data[user_id]['balance'] += amount
            await update.message.reply_text(f"✅ Баланс пополнен на {amount} ⭐\n💰 Новый баланс: {user_data[user_id]['balance']:.0f} ⭐")
        except:
            await update.message.reply_text("❌ Использование: /addbalance 1000")
    else:
        await update.message.reply_text("❌ Использование: /addbalance 1000")

# 🚀 ЗАПУСК БОТА
def main():
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("profile", profile))
    application.add_handler(CommandHandler("bet", bet_command))
    application.add_handler(CommandHandler("addbalance", add_balance))
    
    # Обработчик ВСЕХ кнопок
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    print("🚀 Rocket Casino Bot запущен!")
    print("✨ Кнопки ДОЛЖНЫ работать!")
    
    application.run_polling()

if __name__ == "__main__":
    main()
