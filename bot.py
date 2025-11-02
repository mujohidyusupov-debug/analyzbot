import os
import sqlite3
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import google.generativeai as genai
from flask import Flask, request
from threading import Thread

# ============ НАСТРОЙКИ ============
# Bot Token (получите через @BotFather)
BOT_TOKEN = os.environ.get('BOT_TOKEN', '8013327854:AAGp-1yKhiMt8lKTxC5Ex2VblsE4uPr-Hjo')

# Google AI Studio API Key
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', 'AIzaSyDmlL1IYHGIPzUJZl-P8MRriXHtwU_Z8bo')

# ID администраторов (через запятую можно добавить несколько)
ADMIN_IDS = os.environ.get('ADMIN_IDS', '1777308158,509067967,6568844507')
ADMIN_LIST = [int(id.strip()) for id in ADMIN_IDS.split(',') if id.strip()]

# База данных
DB_NAME = 'messages.db'

# Порт для Flask (Render использует переменную PORT)
PORT = int(os.environ.get('PORT', 5000))

# ============ FLASK APP ============
flask_app = Flask(__name__)

@flask_app.route('/')
def index():
    return 'Telegram Bot is running! ✅'

@flask_app.route('/health')
def health():
    return {'status': 'ok', 'admins': len(ADMIN_LIST)}

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    """Webhook для Telegram (опционально)"""
    return 'OK'

def run_flask():
    """Запуск Flask в отдельном потоке"""
    flask_app.run(host='0.0.0.0', port=PORT)

# ============ ИНИЦИАЛИЗАЦИЯ ============
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash')

# ============ РАБОТА С БАЗОЙ ДАННЫХ ============

def init_db():
    """Инициализация базы данных"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Таблица групп
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS groups (
            group_id INTEGER PRIMARY KEY,
            group_title TEXT,
            added_date TEXT,
            active INTEGER DEFAULT 1
        )
    ''')
    
    # Таблица сообщений
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER,
            group_id INTEGER,
            user_id INTEGER,
            username TEXT,
            first_name TEXT,
            message_text TEXT,
            message_date TEXT,
            FOREIGN KEY (group_id) REFERENCES groups(group_id)
        )
    ''')
    
    # Индексы для быстрого поиска
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_group_date ON messages(group_id, message_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_date ON messages(message_date)')
    
    conn.commit()
    conn.close()


def save_group(group_id, group_title):
    """Сохранить информацию о группе"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT OR REPLACE INTO groups (group_id, group_title, added_date, active)
        VALUES (?, ?, ?, 1)
    ''', (group_id, group_title, datetime.now().isoformat()))
    
    conn.commit()
    conn.close()


def save_message(message_id, group_id, user_id, username, first_name, text, date):
    """Сохранить сообщение в базу"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO messages (message_id, group_id, user_id, username, first_name, message_text, message_date)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (message_id, group_id, user_id, username, first_name, text, date))
    
    conn.commit()
    conn.close()


def get_groups():
    """Получить список всех групп"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT g.group_id, g.group_title, COUNT(m.id) as message_count,
               MAX(m.message_date) as last_message
        FROM groups g
        LEFT JOIN messages m ON g.group_id = m.group_id
        WHERE g.active = 1
        GROUP BY g.group_id, g.group_title
        ORDER BY last_message DESC
    ''')
    
    groups = cursor.fetchall()
    conn.close()
    return groups


def get_messages(group_id, start_date=None, end_date=None, limit=None):
    """Получить сообщения из группы за период"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    query = '''
        SELECT message_id, user_id, username, first_name, message_text, message_date
        FROM messages
        WHERE group_id = ?
    '''
    params = [group_id]
    
    if start_date:
        query += ' AND message_date >= ?'
        params.append(start_date)
    
    if end_date:
        query += ' AND message_date <= ?'
        params.append(end_date)
    
    query += ' ORDER BY message_date DESC'
    
    if limit:
        query += ' LIMIT ?'
        params.append(limit)
    
    cursor.execute(query, params)
    messages = cursor.fetchall()
    conn.close()
    
    return messages


def get_statistics(group_id, start_date=None, end_date=None):
    """Получить статистику по группе"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    query = '''
        SELECT 
            COUNT(*) as total_messages,
            COUNT(DISTINCT user_id) as unique_users,
            MIN(message_date) as first_message,
            MAX(message_date) as last_message
        FROM messages
        WHERE group_id = ?
    '''
    params = [group_id]
    
    if start_date:
        query += ' AND message_date >= ?'
        params.append(start_date)
    
    if end_date:
        query += ' AND message_date <= ?'
        params.append(end_date)
    
    cursor.execute(query, params)
    stats = cursor.fetchone()
    conn.close()
    
    return stats


# ============ ПРОВЕРКА ДОСТУПА ============

def is_admin(user_id):
    """Проверка, является ли пользователь администратором"""
    return user_id in ADMIN_LIST


# ============ ОБРАБОТЧИКИ КОМАНД ============

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ У вас нет доступа к этому боту.")
        return
    
    welcome_text = f"""
👋 Добро пожаловать в бот анализа групп!

👤 Ваш ID: <code>{user_id}</code>

📋 Доступные команды:
/groups - Список отслеживаемых групп
/analyze - Запросить анализ
/stats - Статистика по группам
/help - Помощь

🤖 Просто добавьте меня в группу, и я начну записывать сообщения.
Я НЕ буду отправлять сообщения в группу, только слушаю!
"""
    await update.message.reply_text(welcome_text, parse_mode='HTML')


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help"""
    help_text = """
📚 Руководство по использованию:

1️⃣ Добавьте бота в группу с курьерами
2️⃣ Дайте боту права на чтение сообщений
3️⃣ Бот автоматически начнет сохранять сообщения

📊 Анализ:
/analyze - Выбрать группу и период для анализа

📈 Статистика:
/stats - Посмотреть статистику по всем группам

📋 Группы:
/groups - Список всех отслеживаемых групп

⚠️ Примечание:
Бот НЕ отправляет сообщения в группы!
Он только читает и анализирует.
"""
    await update.message.reply_text(help_text)


async def groups_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /groups - показать список групп"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ У вас нет доступа.")
        return
    
    groups = get_groups()
    
    if not groups:
        await update.message.reply_text("📭 Пока нет отслеживаемых групп.\n\nДобавьте бота в группу для начала работы.")
        return
    
    text = "📋 Отслеживаемые группы:\n\n"
    
    for group_id, title, msg_count, last_msg in groups:
        last_msg_date = "Нет сообщений"
        if last_msg:
            dt = datetime.fromisoformat(last_msg)
            last_msg_date = dt.strftime("%d.%m.%Y %H:%M")
        
        text += f"📌 {title}\n"
        text += f"   ID: <code>{group_id}</code>\n"
        text += f"   Сообщений: {msg_count}\n"
        text += f"   Последнее: {last_msg_date}\n\n"
    
    await update.message.reply_text(text, parse_mode='HTML')


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /stats - показать статистику"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ У вас нет доступа.")
        return
    
    groups = get_groups()
    
    if not groups:
        await update.message.reply_text("📭 Нет данных для статистики.")
        return
    
    text = "📊 СТАТИСТИКА ПО ГРУППАМ\n\n"
    
    for group_id, title, msg_count, last_msg in groups:
        stats = get_statistics(group_id)
        total, users, first, last = stats
        
        text += f"📌 <b>{title}</b>\n"
        text += f"   Всего сообщений: {total}\n"
        text += f"   Уникальных пользователей: {users}\n"
        
        if first:
            first_dt = datetime.fromisoformat(first)
            text += f"   Первое сообщение: {first_dt.strftime('%d.%m.%Y')}\n"
        
        if last:
            last_dt = datetime.fromisoformat(last)
            text += f"   Последнее: {last_dt.strftime('%d.%m.%Y %H:%M')}\n"
        
        text += "\n"
    
    await update.message.reply_text(text, parse_mode='HTML')


async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /analyze - выбор группы для анализа"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ У вас нет доступа.")
        return
    
    groups = get_groups()
    
    if not groups:
        await update.message.reply_text("📭 Нет групп для анализа.")
        return
    
    keyboard = []
    for group_id, title, msg_count, _ in groups:
        keyboard.append([InlineKeyboardButton(
            f"📌 {title} ({msg_count} сообщ.)",
            callback_data=f"select_group_{group_id}"
        )])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Выберите группу для анализа:",
        reply_markup=reply_markup
    )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    # Выбор группы
    if data.startswith("select_group_"):
        group_id = int(data.replace("select_group_", ""))
        context.user_data['selected_group'] = group_id
        
        keyboard = [
            [InlineKeyboardButton("📅 Последние 24 часа", callback_data="period_1d")],
            [InlineKeyboardButton("📅 Последние 3 дня", callback_data="period_3d")],
            [InlineKeyboardButton("📅 Последняя неделя", callback_data="period_7d")],
            [InlineKeyboardButton("📅 Последний месяц", callback_data="period_30d")],
            [InlineKeyboardButton("📅 Весь период", callback_data="period_all")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "Выберите период для анализа:",
            reply_markup=reply_markup
        )
    
    # Выбор периода
    elif data.startswith("period_"):
        period = data.replace("period_", "")
        group_id = context.user_data.get('selected_group')
        
        if not group_id:
            await query.edit_message_text("❌ Ошибка: группа не выбрана.")
            return
        
        await query.edit_message_text("⏳ Собираю данные и анализирую... Это может занять минуту.")
        
        # Определяем период
        end_date = datetime.now()
        start_date = None
        
        if period == "1d":
            start_date = end_date - timedelta(days=1)
            period_text = "последние 24 часа"
        elif period == "3d":
            start_date = end_date - timedelta(days=3)
            period_text = "последние 3 дня"
        elif period == "7d":
            start_date = end_date - timedelta(days=7)
            period_text = "последняя неделя"
        elif period == "30d":
            start_date = end_date - timedelta(days=30)
            period_text = "последний месяц"
        else:
            period_text = "весь период"
        
        # Получаем сообщения
        messages = get_messages(
            group_id,
            start_date.isoformat() if start_date else None,
            end_date.isoformat()
        )
        
        if not messages:
            await query.edit_message_text(f"📭 Нет сообщений за {period_text}.")
            return
        
        # Анализируем
        analysis = await analyze_group_messages(group_id, messages, period_text)
        
        # Отправляем результат
        if len(analysis) > 4000:
            # Разбиваем на части
            parts = [analysis[i:i+4000] for i in range(0, len(analysis), 4000)]
            await query.edit_message_text(parts[0], parse_mode='HTML')
            for part in parts[1:]:
                await query.message.reply_text(part, parse_mode='HTML')
        else:
            await query.edit_message_text(analysis, parse_mode='HTML')


async def analyze_group_messages(group_id, messages, period_text):
    """Анализ сообщений с помощью Gemini"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT group_title FROM groups WHERE group_id = ?', (group_id,))
    group_title = cursor.fetchone()[0]
    conn.close()
    
    # Формируем текст для анализа
    messages_text = "\n\n".join([
        f"[{msg[5]}] {msg[3] or msg[2]}: {msg[4]}"
        for msg in messages[:200]  # Ограничиваем 200 сообщениями
    ])
    
    stats = get_statistics(group_id)
    total_messages, unique_users, _, _ = stats
    
    prompt = f"""
Проанализируй сообщения курьеров в Telegram группе "{group_title}" за {period_text}.

СТАТИСТИКА:
- Всего сообщений: {len(messages)}
- Уникальных пользователей: {unique_users}

СООБЩЕНИЯ (последние до 200):
{messages_text}

Составь КРАТКИЙ отчёт простыми словами, как будто ты рассказываешь коллеге что было в группе.

Формат:

Отчёт за {period_text}:
[Расскажи простым языком что происходило. Пиши как обычный человек, без официоза. 2-3 абзаца.]

Что было:
- [Какие проблемы были]
- [О чем спрашивали]
- [На что жаловались]
- [Какое настроение]

ВАЖНО:
- Пиши простым разговорным языком, без канцелярщины
- Вместо "поступали обращения" - пиши "курьеры спрашивали"
- Вместо "наблюдался сбой" - пиши "не работал", "сломался"
- Вместо "технические проблемы" - пиши "глюки", "баги", "не грузится"
- Группируй похожие вопросы
- Коротко - максимум 150 слов
- НЕ используй символы форматирования: *, **, #, _, ~
- Если ничего важного не было - так и напиши "всё спокойно"
"""
    
    try:
        response = model.generate_content(prompt)
        
        # Форматируем заголовок с HTML разметкой
        header = f"<b>📊 ОТЧЁТ:</b> {group_title}\n"
        header += f"<b>📅 Период:</b> {period_text}\n"
        header += f"<b>📝 Сообщений:</b> {len(messages)} | <b>👥 Участников:</b> {unique_users}\n"
        header += f"{'═'*40}\n\n"
        
        # Обрабатываем текст отчета - убираем markdown символы
        report_text = response.text
        # Убираем markdown форматирование
        report_text = report_text.replace('**', '').replace('__', '').replace('##', '').replace('*', '')
        
        # Добавляем жирный текст для ключевых фраз
        keywords = [
            'Отчёт за', 'Что было:', 'массовый сбой', 'проблема', 'жалобы',
            'обращения', 'ошибка', 'сбой', 'не работает', 'технические проблемы',
            'частые вопросы', 'атмосфера', 'работа в штатном режиме', 'всё спокойно'
        ]
        
        for keyword in keywords:
            if keyword in report_text:
                report_text = report_text.replace(keyword, f'<b>{keyword}</b>')
        
        return header + report_text
        
    except Exception as e:
        return f"❌ Ошибка при анализе: {str(e)}"


# ============ ОБРАБОТЧИК СООБЩЕНИЙ В ГРУППАХ ============

async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка сообщений в группах (только сохранение, без отправки)"""
    # Проверяем, что есть сообщение
    if not update.message:
        return
    
    message = update.message
    
    # Проверяем, что это сообщение из группы
    if not message.chat or message.chat.type not in ['group', 'supergroup']:
        return
    
    # Сохраняем информацию о группе
    save_group(message.chat_id, message.chat.title)
    
    # Сохраняем сообщение
    if message.text:
        save_message(
            message_id=message.message_id,
            group_id=message.chat_id,
            user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            text=message.text,
            date=message.date.isoformat()
        )


# ============ ГЛАВНАЯ ФУНКЦИЯ ============

def main():
    """Запуск бота"""
    print("🔄 Инициализация базы данных...")
    init_db()
    print("✅ База данных готова!")
    
    print(f"👥 Администраторы: {ADMIN_LIST}")
    
    print("🌐 Запуск Flask сервера...")
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print(f"✅ Flask запущен на порту {PORT}")
    
    print("🤖 Запуск бота...")
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("groups", groups_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("analyze", analyze_command))
    
    # Обработчик кнопок
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Обработчик сообщений в группах
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_group_message
    ))
    
    print("✅ Бот запущен и готов к работе!")
    print(f"📋 Администраторы: {len(ADMIN_LIST)}")
    print("💾 База данных: messages.db")
    print("\nБот будет сохранять все текстовые сообщения из групп.")
    print("Для анализа напишите боту в личку команду /analyze")
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':

    main()



