import asyncio
import logging
import aiosqlite
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters.callback_data import CallbackData

# --- КОНФИГУРАЦИЯ ---
API_TOKEN = '8352293761:AAEnp71VgWe-einNIPXDnvuPKQGsZeTpDEs'
DB_NAME = 'manager.db'

# ID администраторов
ADMINS = {
    8509083541: "Артем",
    8463141592: "Никита"
}

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- CALLBACK DATA FACTORIES (Структура кнопок) ---
class MenuCB(CallbackData, prefix="menu"):
    action: str  # main, plans, stats, artem_money, nikita_money
    
class ViewCB(CallbackData, prefix="view"):
    category: str
    subcat: str

class TaskCB(CallbackData, prefix="task"):
    action: str  # toggle, delete, change_num, copy, move, edit
    id: int
    category: str
    subcat: str

class AddCB(CallbackData, prefix="add"):
    category: str
    subcat: str

class OwnerCB(CallbackData, prefix="owner"):
    name: str

class MoveCB(CallbackData, prefix="move"):
    task_id: int
    to_category: str
    to_subcat: str

# --- БАЗА ДАННЫХ (Асинхронная) ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        # Проверяем существует ли колонка task_number
        cursor = await db.execute("PRAGMA table_info(entries)")
        columns = await cursor.fetchall()
        column_names = [col[1] for col in columns]
        
        if 'task_number' not in column_names:
            # Добавляем колонку для нумерации, если её нет
            await db.execute('ALTER TABLE entries ADD COLUMN task_number INTEGER DEFAULT 0')
            await db.commit()
            print("✅ Добавлена колонка task_number")
        
        # Создаем таблицу если её нет
        await db.execute('''
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT,
                subcat TEXT,
                content TEXT,
                owner TEXT,
                status INTEGER DEFAULT 0,
                created_at TEXT,
                task_number INTEGER DEFAULT 0
            )
        ''')
        await db.commit()

async def db_fetch(query, params=()):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(query, params) as cursor:
            return await cursor.fetchall()

async def db_execute(query, params=()):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(query, params)
        await db.commit()

async def get_next_task_number(category, subcat):
    """Получает следующий номер задачи для категории"""
    query = "SELECT MAX(task_number) FROM entries WHERE category = ?"
    params = [category]
    if subcat != "none":
        query += " AND subcat = ?"
        params.append(subcat)
    
    result = await db_fetch(query, tuple(params))
    max_num = result[0][0] if result and result[0][0] else 0
    return max_num + 1

async def reorder_tasks(category, subcat):
    """Переназначает номера задачам в порядке их ID (самые старые = меньшие номера)"""
    query = "SELECT id FROM entries WHERE category = ? AND status = 0"
    params = [category]
    if subcat != "none":
        query += " AND subcat = ?"
        params.append(subcat)
    query += " ORDER BY id ASC"
    
    rows = await db_fetch(query, tuple(params))
    
    for idx, (task_id,) in enumerate(rows, start=1):
        await db_execute("UPDATE entries SET task_number = ? WHERE id = ?", (idx, task_id))

# --- СОСТОЯНИЯ ---
class Form(StatesGroup):
    waiting_for_content = State()
    waiting_for_owner = State()
    waiting_for_task_number = State()
    waiting_for_edit_text = State()

# --- КЛАВИАТУРЫ ---
async def get_main_kb(user_id):
    # Статистика по владельцам
    stats = await db_fetch("SELECT owner, COUNT(*) FROM entries WHERE status = 0 GROUP BY owner")
    s_dict = {row[0]: row[1] for row in stats}
    
    # Статистика по категориям
    cat_stats = await db_fetch("""
        SELECT category, subcat, COUNT(*) 
        FROM entries 
        WHERE status = 0 
        GROUP BY category, subcat
    """)
    
    # Собираем статистику
    projects_count = sum(row[2] for row in cat_stats if row[0] == "projects")
    today_count = sum(row[2] for row in cat_stats if row[0] == "today")
    plans_count = sum(row[2] for row in cat_stats if row[0] == "plans")
    debts_count = sum(row[2] for row in cat_stats if row[0] == "debts")
    notes_count = sum(row[2] for row in cat_stats if row[0] == "notes")
    money_count = sum(row[2] for row in cat_stats if row[0] == "money")
    
    # Общая сумма денег только для "Общее"
    money_result = await db_fetch("SELECT content FROM entries WHERE category = 'money' AND status = 0 AND owner = 'Общее'")
    total_money = 0
    for row in money_result:
        try:
            # Убираем все нечисловые символы кроме точки и минуса
            clean_value = ''.join(c for c in str(row[0]) if c.isdigit() or c in '.-')
            if clean_value and clean_value not in ['-', '.', '-.']:
                total_money += float(clean_value)
        except (ValueError, TypeError):
            pass  # Игнорируем записи, которые не являются числами
    
    # Заработок Артема (свой + 50% от общего)
    artem_money_result = await db_fetch("SELECT content FROM entries WHERE category = 'money' AND status = 0 AND owner = 'Артем'")
    artem_money = 0
    for row in artem_money_result:
        try:
            clean_value = ''.join(c for c in str(row[0]) if c.isdigit() or c in '.-')
            if clean_value and clean_value not in ['-', '.', '-.']:
                artem_money += float(clean_value)
        except (ValueError, TypeError):
            pass
    
    # Добавляем 50% от общего
    artem_money += total_money / 2
    
    # Заработок Никиты (свой + 50% от общего)
    nikita_money_result = await db_fetch("SELECT content FROM entries WHERE category = 'money' AND status = 0 AND owner = 'Никита'")
    nikita_money = 0
    for row in nikita_money_result:
        try:
            clean_value = ''.join(c for c in str(row[0]) if c.isdigit() or c in '.-')
            if clean_value and clean_value not in ['-', '.', '-.']:
                nikita_money += float(clean_value)
        except (ValueError, TypeError):
            pass
    
    # Добавляем 50% от общего
    nikita_money += total_money / 2

    text = f"🛡 **ПАНЕЛЬ УПРАВЛЕНИЯ**\n"
    text += f"━━━━━━━━━━━━━━\n"
    text += f"👤 Артем: {s_dict.get('Артем', 0)} активных\n"
    text += f"👤 Никита: {s_dict.get('Никита', 0)} активных\n"
    text += f"👥 Общие: {s_dict.get('Общее', 0)} активных\n"
    text += f"━━━━━━━━━━━━━━\n"
    text += f"💰 Общий баланс: {total_money:.2f} $"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"🚀 ПРОЕКТЫ ({projects_count})", 
                callback_data=ViewCB(category="projects", subcat="none").pack()
            ),
            InlineKeyboardButton(
                text=f"📅 СЕГОДНЯ ({today_count})", 
                callback_data=ViewCB(category="today", subcat="none").pack()
            )
        ],
        [InlineKeyboardButton(
            text=f"🗓 ПЛАНЫ ({plans_count})", 
            callback_data=MenuCB(action="plans").pack()
        )],
        [
            InlineKeyboardButton(
                text=f"💸 ДОЛГИ ({debts_count})", 
                callback_data=ViewCB(category="debts", subcat="none").pack()
            ),
            InlineKeyboardButton(
                text=f"📝 ЗАМЕТКИ ({notes_count})", 
                callback_data=ViewCB(category="notes", subcat="none").pack()
            )
        ],
        [InlineKeyboardButton(
            text=f"💰 MONEY ({money_count})", 
            callback_data=ViewCB(category="money", subcat="none").pack()
        )],
        [
            InlineKeyboardButton(
                text=f"🅰️ Артем: {artem_money:.2f}$", 
                callback_data=MenuCB(action="artem_money").pack()
            ),
            InlineKeyboardButton(
                text=f"🅽 Никита: {nikita_money:.2f}$", 
                callback_data=MenuCB(action="nikita_money").pack()
            )
        ],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=MenuCB(action="main").pack())]
    ])
    return text, kb

def get_owner_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👨‍💻 Артем", callback_data=OwnerCB(name="Артем").pack()),
            InlineKeyboardButton(text="👨‍💻 Никита", callback_data=OwnerCB(name="Никита").pack())
        ],
        [InlineKeyboardButton(text="🤝 Общее", callback_data=OwnerCB(name="Общее").pack())]
    ])

# --- ХЕНДЛЕРЫ ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id not in ADMINS:
        await message.answer("⛔ Доступ запрещен.")
        return
    text, kb = await get_main_kb(message.from_user.id)
    await message.answer(text, reply_markup=kb, parse_mode="Markdown")

# Главное меню
@dp.callback_query(MenuCB.filter(F.action == "main"))
async def go_main(callback: CallbackQuery):
    text, kb = await get_main_kb(callback.from_user.id)
    # Пытаемся редактировать, если текст не изменился - игнорируем ошибку
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    except:
        await callback.answer()

# Меню планов
@dp.callback_query(MenuCB.filter(F.action == "plans"))
async def go_plans(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="На Неделю", callback_data=ViewCB(category="plans", subcat="week").pack()),
            InlineKeyboardButton(text="На Месяц", callback_data=ViewCB(category="plans", subcat="month").pack())
        ],
        [InlineKeyboardButton(text="На Год", callback_data=ViewCB(category="plans", subcat="year").pack())],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=MenuCB(action="main").pack())]
    ])
    await callback.message.edit_text("⏳ Выберите период планирования:", reply_markup=kb)

# Просмотр заработка Артема
@dp.callback_query(MenuCB.filter(F.action == "artem_money"))
async def view_artem_money(callback: CallbackQuery):
    await view_personal_money(callback, "Артем")

# Просмотр заработка Никиты
@dp.callback_query(MenuCB.filter(F.action == "nikita_money"))
async def view_nikita_money(callback: CallbackQuery):
    await view_personal_money(callback, "Никита")

async def view_personal_money(callback: CallbackQuery, owner: str):
    """Отображает заработок конкретного человека с отчетом по месяцам"""
    # Получаем личный заработок
    query = "SELECT id, content, status, owner, task_number, created_at FROM entries WHERE category = 'money' AND owner = ? ORDER BY status ASC, task_number ASC, id DESC"
    rows = await db_fetch(query, (owner,))
    
    # Получаем общий заработок для деления
    common_query = "SELECT content, created_at FROM entries WHERE category = 'money' AND owner = 'Общее' AND status = 0"
    common_rows = await db_fetch(common_query)
    
    icon = "🅰️" if owner == "Артем" else "🅽"
    text = f"<b>{icon} ЗАРАБОТОК: {owner.upper()}</b>\n"
    text += f"━━━━━━━━━━━━━━\n"
    
    # Подсчет и группировка по месяцам
    personal_total = 0
    common_total = 0
    monthly_data = {}  # {(year, month): {'personal': amount, 'common': amount}}
    
    # Обрабатываем личный заработок
    for row in rows:
        if row[2] == 0:  # Только активные
            try:
                clean_value = ''.join(c for c in str(row[1]) if c.isdigit() or c in '.-')
                if clean_value and clean_value not in ['-', '.', '-.']:
                    amount = float(clean_value)
                    personal_total += amount
                    
                    created_at = row[5]
                    if created_at:
                        try:
                            date_parts = created_at.split()[0].split('.')
                            if len(date_parts) >= 2:
                                day, month = date_parts[0], date_parts[1]
                                from datetime import datetime
                                year = datetime.now().year
                                
                                month_key = (year, int(month))
                                if month_key not in monthly_data:
                                    monthly_data[month_key] = {'personal': 0, 'common': 0}
                                monthly_data[month_key]['personal'] += amount
                        except:
                            pass
            except (ValueError, TypeError):
                pass
    
    # Обрабатываем общий заработок (50%)
    for row in common_rows:
        try:
            clean_value = ''.join(c for c in str(row[0]) if c.isdigit() or c in '.-')
            if clean_value and clean_value not in ['-', '.', '-.']:
                amount = float(clean_value) / 2  # Делим на 2
                common_total += amount
                
                created_at = row[1]
                if created_at:
                    try:
                        date_parts = created_at.split()[0].split('.')
                        if len(date_parts) >= 2:
                            day, month = date_parts[0], date_parts[1]
                            from datetime import datetime
                            year = datetime.now().year
                            
                            month_key = (year, int(month))
                            if month_key not in monthly_data:
                                monthly_data[month_key] = {'personal': 0, 'common': 0}
                            monthly_data[month_key]['common'] += amount
                    except:
                        pass
        except (ValueError, TypeError):
            pass
    
    total = personal_total + common_total
    
    text += f"<b>Личный заработок: {personal_total:.2f} $</b>\n"
    text += f"<b>От общего (50%): {common_total:.2f} $</b>\n"
    text += f"<b>━━━━━━━━━━━━━━</b>\n"
    text += f"<b>Итого: {total:.2f} $</b>\n\n"
    
    # Отчет по месяцам
    if monthly_data:
        text += f"📊 <b>Отчет по месяцам:</b>\n"
        months_names = {
            1: 'Январь', 2: 'Февраль', 3: 'Март', 4: 'Апрель',
            5: 'Май', 6: 'Июнь', 7: 'Июль', 8: 'Август',
            9: 'Сентябрь', 10: 'Октябрь', 11: 'Ноябрь', 12: 'Декабрь'
        }
        
        for (year, month), amounts in sorted(monthly_data.items(), reverse=True):
            month_name = months_names.get(month, str(month))
            month_total = amounts['personal'] + amounts['common']
            text += f"\n🗓 <b>{month_name} {year}:</b>\n"
            if amounts['personal'] > 0:
                text += f"  💼 Личный: {amounts['personal']:.2f} $\n"
            if amounts['common'] > 0:
                text += f"  🤝 От общего: {amounts['common']:.2f} $\n"
            text += f"  💰 Итого: {month_total:.2f} $\n"
    
    if total == 0:
        text += "\n<i>Записи отсутствуют</i>"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=MenuCB(action="main").pack())]
    ])
    
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

# Просмотр списка задач
@dp.callback_query(ViewCB.filter())
async def view_items(callback: CallbackQuery, callback_data: ViewCB):
    cat = callback_data.category
    sub = callback_data.subcat

    query = "SELECT id, content, status, owner, task_number, created_at FROM entries WHERE category = ?"
    params = [cat]
    if sub != "none":
        query += " AND subcat = ?"
        params.append(sub)
    
    # Сортировка: сначала невыполненные, потом по номеру задачи
    query += " ORDER BY status ASC, task_number ASC, id DESC"
    
    rows = await db_fetch(query, tuple(params))

    title_map = {
        "projects": "🚀 ПРОЕКТЫ", 
        "today": "📅 ЗАДАЧИ НА СЕГОДНЯ", 
        "plans": f"🗓 ПЛАНЫ ({sub})", 
        "debts": "💸 ДОЛГИ", 
        "notes": "📝 ЗАМЕТКИ",
        "money": "💰 ЗАРАБОТОК"
    }
    
    text = f"<b>{title_map.get(cat, cat.upper())}</b>\n"
    text += f"━━━━━━━━━━━━━━\n"

    kb = []
    if not rows:
        text += "<i>Список пока пуст...</i>"
    else:
        # Для категории money показываем сумму и отчет по месяцам
        if cat == "money":
            # Разделяем по владельцам
            artem_total = 0
            nikita_total = 0
            common_total = 0
            monthly_data = {}  # {(year, month): {'Артем': amount, 'Никита': amount, 'Общее': amount}}
            
            for row in rows:
                if row[2] == 0:  # Только активные
                    owner = row[3]
                    try:
                        clean_value = ''.join(c for c in str(row[1]) if c.isdigit() or c in '.-')
                        if clean_value and clean_value not in ['-', '.', '-.']:
                            amount = float(clean_value)
                            
                            if owner == 'Артем':
                                artem_total += amount
                            elif owner == 'Никита':
                                nikita_total += amount
                            elif owner == 'Общее':
                                common_total += amount
                            
                            # Парсим дату для группировки по месяцам
                            created_at = row[5]
                            if created_at:
                                try:
                                    date_parts = created_at.split()[0].split('.')
                                    if len(date_parts) >= 2:
                                        day, month = date_parts[0], date_parts[1]
                                        from datetime import datetime
                                        year = datetime.now().year
                                        
                                        month_key = (year, int(month))
                                        if month_key not in monthly_data:
                                            monthly_data[month_key] = {'Артем': 0, 'Никита': 0, 'Общее': 0}
                                        monthly_data[month_key][owner] += amount
                                except:
                                    pass
                    except (ValueError, TypeError):
                        pass
            
            text += f"<b>🅰️ Артем: {artem_total:.2f} $</b>\n"
            text += f"<b>🅽 Никита: {nikita_total:.2f} $</b>\n"
            text += f"<b>👥 Общее: {common_total:.2f} $</b>\n\n"
            
            # Отчет по месяцам
            if monthly_data:
                text += f"📊 <b>Отчет по месяцам:</b>\n"
                months_names = {
                    1: 'Январь', 2: 'Февраль', 3: 'Март', 4: 'Апрель',
                    5: 'Май', 6: 'Июнь', 7: 'Июль', 8: 'Август',
                    9: 'Сентябрь', 10: 'Октябрь', 11: 'Ноябрь', 12: 'Декабрь'
                }
                
                # Сортируем по месяцам (год, месяц)
                for (year, month), owners in sorted(monthly_data.items(), reverse=True):
                    month_name = months_names.get(month, str(month))
                    text += f"\n🗓 <b>{month_name} {year}:</b>\n"
                    
                    if owners['Артем'] > 0:
                        text += f"  🅰️ Артем: {owners['Артем']:.2f} $\n"
                    if owners['Никита'] > 0:
                        text += f"  🅽 Никита: {owners['Никита']:.2f} $\n"
                    if owners['Общее'] > 0:
                        text += f"  👥 Общее: {owners['Общее']:.2f} $\n"
                
                text += "\n"
        
        for eid, content, status, owner, task_num, created_at in rows:
            icon = "✅" if status == 1 else "⭕️"
            # Иконка владельца
            own_icon = "🅰️" if owner == "Артем" else ("🅽" if owner == "Никита" else "👥")
            
            # Показываем номер только для активных задач
            num_display = f"#{task_num} " if status == 0 and task_num > 0 else ""
            
            # Зачеркивание текста если выполнено
            display_text = f"<s>{content}</s>" if status == 1 else content
            
            # Для money добавляем символ валюты и дату
            if cat == "money":
                date_display = f" ({created_at})" if created_at else ""
                display_text = f"{display_text} ${date_display}"
            
            text += f"{icon} {own_icon} {num_display}{display_text}\n"
            
            # Кнопки управления задачей
            btn_text = f"↩️ Вернуть" if status == 1 else f"✅ #{task_num}" if task_num > 0 else f"✅ {content[:10]}..."
            
            row_buttons = [
                InlineKeyboardButton(
                    text=btn_text, 
                    callback_data=TaskCB(action="toggle", id=eid, category=cat, subcat=sub).pack()
                )
            ]
            
            # Дополнительные кнопки только для активных задач
            if status == 0:
                row_buttons.append(
                    InlineKeyboardButton(
                        text="🔢", 
                        callback_data=TaskCB(action="change_num", id=eid, category=cat, subcat=sub).pack()
                    )
                )
                row_buttons.append(
                    InlineKeyboardButton(
                        text="✏️", 
                        callback_data=TaskCB(action="edit", id=eid, category=cat, subcat=sub).pack()
                    )
                )
            
            row_buttons.append(
                InlineKeyboardButton(
                    text="❌", 
                    callback_data=TaskCB(action="delete", id=eid, category=cat, subcat=sub).pack()
                )
            )
            
            kb.append(row_buttons)
            
            # Вторая строка кнопок для активных задач - копировать и переместить
            if status == 0:
                kb.append([
                    InlineKeyboardButton(
                        text="📋 Копировать", 
                        callback_data=TaskCB(action="copy", id=eid, category=cat, subcat=sub).pack()
                    ),
                    InlineKeyboardButton(
                        text="📁 Переместить", 
                        callback_data=TaskCB(action="move", id=eid, category=cat, subcat=sub).pack()
                    )
                ])

    # Кнопки действий
    kb.append([InlineKeyboardButton(text="➕ Добавить запись", callback_data=AddCB(category=cat, subcat=sub).pack())])
    
    # Кнопка очистки только если есть выполненные задачи
    has_completed = any(r[2] == 1 for r in rows)
    if has_completed:
        kb.append([InlineKeyboardButton(text="🧹 Удалить выполненные", callback_data=f"clear_done:{cat}:{sub}")])
    
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=MenuCB(action="main").pack())])

    try:
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    except Exception as e:
        # Если не можем отредактировать (например, контент не изменился), просто отвечаем на callback
        logging.error(f"Could not edit message: {e}")
        await callback.answer()

# --- ДОБАВЛЕНИЕ ЗАДАЧИ ---
@dp.callback_query(AddCB.filter())
async def start_add(callback: CallbackQuery, callback_data: AddCB, state: FSMContext):
    await state.update_data(c_cat=callback_data.category, c_sub=callback_data.subcat)
    await callback.message.answer("📝 Введите текст:", reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="cancel_add")]]
    ))
    await state.set_state(Form.waiting_for_content)

@dp.callback_query(F.data == "cancel_add")
async def cancel_add(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    text, kb = await get_main_kb(callback.from_user.id)
    await callback.message.answer("Отменено.", reply_markup=kb, parse_mode="Markdown")

@dp.message(Form.waiting_for_content)
async def process_content(message: types.Message, state: FSMContext):
    await state.update_data(c_text=message.text)
    await message.answer("👥 Для кого эта задача?", reply_markup=get_owner_kb())
    await state.set_state(Form.waiting_for_owner)

@dp.callback_query(OwnerCB.filter(), Form.waiting_for_owner)
async def process_owner(callback: CallbackQuery, callback_data: OwnerCB, state: FSMContext):
    owner = callback_data.name
    data = await state.get_data()
    
    now = datetime.now().strftime("%d.%m %H:%M")
    
    # Получаем следующий номер задачи
    task_num = await get_next_task_number(data['c_cat'], data['c_sub'])
    
    await db_execute(
        "INSERT INTO entries (category, subcat, content, owner, created_at, task_number) VALUES (?, ?, ?, ?, ?, ?)",
        (data['c_cat'], data['c_sub'], data['c_text'], owner, now, task_num)
    )
    
    await state.clear()
    await callback.message.answer(f"✅ Добавлено #{task_num}: {data['c_text']} ({owner})")
    
    # Возвращаемся в список, откуда начали
    await view_items(callback, ViewCB(category=data['c_cat'], subcat=data['c_sub']))

# --- ДЕЙСТВИЯ С ЗАДАЧАМИ ---

# Изменение статуса (Выполнено/Не выполнено)
@dp.callback_query(TaskCB.filter(F.action == "toggle"))
async def process_toggle(callback: CallbackQuery, callback_data: TaskCB):
    await db_execute("UPDATE entries SET status = 1 - status WHERE id = ?", (callback_data.id,))
    
    # Если задача возвращается в активные, даем ей новый номер
    result = await db_fetch("SELECT status FROM entries WHERE id = ?", (callback_data.id,))
    if result and result[0][0] == 0:  # Задача теперь активна
        new_num = await get_next_task_number(callback_data.category, callback_data.subcat)
        await db_execute("UPDATE entries SET task_number = ? WHERE id = ?", (new_num, callback_data.id))
    
    # Обновляем view без уведомления
    await view_items(callback, ViewCB(category=callback_data.category, subcat=callback_data.subcat))

# Удаление одной задачи
@dp.callback_query(TaskCB.filter(F.action == "delete"))
async def process_del(callback: CallbackQuery, callback_data: TaskCB):
    await db_execute("DELETE FROM entries WHERE id = ?", (callback_data.id,))
    await callback.answer("Удалено!")
    await view_items(callback, ViewCB(category=callback_data.category, subcat=callback_data.subcat))

# Изменение номера задачи
@dp.callback_query(TaskCB.filter(F.action == "change_num"))
async def start_change_num(callback: CallbackQuery, callback_data: TaskCB, state: FSMContext):
    # Получаем текущую информацию о задаче
    result = await db_fetch("SELECT content, task_number FROM entries WHERE id = ?", (callback_data.id,))
    if not result:
        await callback.answer("Задача не найдена!")
        return
    
    content, current_num = result[0]
    
    await state.update_data(
        task_id=callback_data.id,
        task_category=callback_data.category,
        task_subcat=callback_data.subcat
    )
    
    await callback.message.answer(
        f"🔢 Задача: <b>{content}</b>\n"
        f"Текущий номер: <b>#{current_num}</b>\n\n"
        f"Введите новый номер (число):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="cancel_change_num")]]
        )
    )
    await state.set_state(Form.waiting_for_task_number)
    await callback.answer()

@dp.callback_query(F.data == "cancel_change_num")
async def cancel_change_num(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    await callback.message.delete()
    
    # Возвращаемся к списку
    if data.get('task_category'):
        await view_items(callback, ViewCB(category=data['task_category'], subcat=data['task_subcat']))

@dp.message(Form.waiting_for_task_number)
async def process_new_number(message: types.Message, state: FSMContext):
    # Проверяем что введено число
    try:
        new_number = int(message.text.strip())
        if new_number < 1:
            await message.answer("❌ Номер должен быть больше 0. Попробуйте еще раз:")
            return
    except ValueError:
        await message.answer("❌ Пожалуйста, введите число. Попробуйте еще раз:")
        return
    
    data = await state.get_data()
    task_id = data['task_id']
    
    # Получаем все задачи в этой категории
    query = "SELECT id, task_number FROM entries WHERE category = ? AND status = 0 AND id != ?"
    params = [data['task_category']]
    if data['task_subcat'] != "none":
        query += " AND subcat = ?"
        params.append(data['task_subcat'])
    params.append(task_id)
    query += " ORDER BY task_number ASC"
    
    other_tasks = await db_fetch(query, tuple(params))
    
    # Получаем текущий номер изменяемой задачи
    current = await db_fetch("SELECT task_number FROM entries WHERE id = ?", (task_id,))
    old_number = current[0][0] if current else 0
    
    # Обновляем номер целевой задачи
    await db_execute("UPDATE entries SET task_number = ? WHERE id = ?", (new_number, task_id))
    
    # Сдвигаем другие задачи
    if new_number < old_number:
        # Сдвигаем вниз задачи между new_number и old_number
        for tid, tnum in other_tasks:
            if new_number <= tnum < old_number:
                await db_execute("UPDATE entries SET task_number = ? WHERE id = ?", (tnum + 1, tid))
    elif new_number > old_number:
        # Сдвигаем вверх задачи между old_number и new_number
        for tid, tnum in other_tasks:
            if old_number < tnum <= new_number:
                await db_execute("UPDATE entries SET task_number = ? WHERE id = ?", (tnum - 1, tid))
    
    await state.clear()
    await message.answer(f"✅ Номер изменен: #{old_number} → #{new_number}")
    
    # Создаем фейковый callback для возврата к списку
    # Используем bot для отправки нового сообщения со списком
    query = "SELECT id, content, status, owner, task_number FROM entries WHERE category = ?"
    params = [data['task_category']]
    if data['task_subcat'] != "none":
        query += " AND subcat = ?"
        params.append(data['task_subcat'])
    query += " ORDER BY status ASC, task_number ASC, id DESC"
    
    rows = await db_fetch(query, tuple(params))
    
    title_map = {
        "projects": "🚀 ПРОЕКТЫ", 
        "today": "📅 ЗАДАЧИ НА СЕГОДНЯ", 
        "plans": f"🗓 ПЛАНЫ ({data['task_subcat']})", 
        "debts": "💸 ДОЛГИ", 
        "notes": "📝 ЗАМЕТКИ"
    }
    
    text = f"<b>{title_map.get(data['task_category'], data['task_category'].upper())}</b>\n"
    text += f"━━━━━━━━━━━━━━\n"
    
    kb = []
    for eid, content, status, owner, task_num in rows:
        icon = "✅" if status == 1 else "⭕️"
        own_icon = "🅰️" if owner == "Артем" else ("🅽" if owner == "Никита" else "👥")
        num_display = f"#{task_num} " if status == 0 and task_num > 0 else ""
        display_text = f"<s>{content}</s>" if status == 1 else content
        text += f"{icon} {own_icon} {num_display}{display_text}\n"
        
        btn_text = f"↩️ Вернуть" if status == 1 else f"✅ #{task_num}" if task_num > 0 else f"✅ {content[:10]}..."
        
        row_buttons = [
            InlineKeyboardButton(
                text=btn_text, 
                callback_data=TaskCB(action="toggle", id=eid, category=data['task_category'], subcat=data['task_subcat']).pack()
            )
        ]
        
        if status == 0:
            row_buttons.append(
                InlineKeyboardButton(
                    text="🔢", 
                    callback_data=TaskCB(action="change_num", id=eid, category=data['task_category'], subcat=data['task_subcat']).pack()
                )
            )
        
        row_buttons.append(
            InlineKeyboardButton(
                text="❌", 
                callback_data=TaskCB(action="delete", id=eid, category=data['task_category'], subcat=data['task_subcat']).pack()
            )
        )
        
        kb.append(row_buttons)
    
    kb.append([InlineKeyboardButton(text="➕ Добавить запись", callback_data=AddCB(category=data['task_category'], subcat=data['task_subcat']).pack())])
    
    active_tasks = [r for r in rows if r[2] == 0]
    if len(active_tasks) > 1:
        kb.append([InlineKeyboardButton(text="🔢 Переупорядочить номера", callback_data=f"reorder:{data['task_category']}:{data['task_subcat']}")])
    
    has_completed = any(r[2] == 1 for r in rows)
    if has_completed:
        kb.append([InlineKeyboardButton(text="🧹 Удалить выполненные", callback_data=f"clear_done:{data['task_category']}:{data['task_subcat']}")])
    
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=MenuCB(action="main").pack())])
    
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")

# Копирование задачи
@dp.callback_query(TaskCB.filter(F.action == "copy"))
async def process_copy(callback: CallbackQuery, callback_data: TaskCB):
    # Получаем задачу
    result = await db_fetch("SELECT content, owner, category, subcat FROM entries WHERE id = ?", (callback_data.id,))
    if not result:
        await callback.answer("Задача не найдена!")
        return
    
    content, owner, cat, sub = result[0]
    now = datetime.now().strftime("%d.%m %H:%M")
    task_num = await get_next_task_number(cat, sub)
    
    # Создаем копию
    await db_execute(
        "INSERT INTO entries (category, subcat, content, owner, created_at, task_number) VALUES (?, ?, ?, ?, ?, ?)",
        (cat, sub, content, owner, now, task_num)
    )
    
    await callback.answer(f"✅ Задача скопирована как #{task_num}")
    await view_items(callback, ViewCB(category=callback_data.category, subcat=callback_data.subcat))

# Перемещение задачи - показываем меню категорий
@dp.callback_query(TaskCB.filter(F.action == "move"))
async def process_move_menu(callback: CallbackQuery, callback_data: TaskCB):
    result = await db_fetch("SELECT content FROM entries WHERE id = ?", (callback_data.id,))
    if not result:
        await callback.answer("Задача не найдена!")
        return
    
    content = result[0][0]
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🚀 ПРОЕКТЫ", 
                callback_data=MoveCB(task_id=callback_data.id, to_category="projects", to_subcat="none").pack()
            ),
            InlineKeyboardButton(
                text="📅 СЕГОДНЯ", 
                callback_data=MoveCB(task_id=callback_data.id, to_category="today", to_subcat="none").pack()
            )
        ],
        [
            InlineKeyboardButton(
                text="📅 Неделя", 
                callback_data=MoveCB(task_id=callback_data.id, to_category="plans", to_subcat="week").pack()
            ),
            InlineKeyboardButton(
                text="📅 Месяц", 
                callback_data=MoveCB(task_id=callback_data.id, to_category="plans", to_subcat="month").pack()
            ),
            InlineKeyboardButton(
                text="📅 Год", 
                callback_data=MoveCB(task_id=callback_data.id, to_category="plans", to_subcat="year").pack()
            )
        ],
        [
            InlineKeyboardButton(
                text="💸 ДОЛГИ", 
                callback_data=MoveCB(task_id=callback_data.id, to_category="debts", to_subcat="none").pack()
            ),
            InlineKeyboardButton(
                text="📝 ЗАМЕТКИ", 
                callback_data=MoveCB(task_id=callback_data.id, to_category="notes", to_subcat="none").pack()
            )
        ],
        [
            InlineKeyboardButton(
                text="💰 MONEY", 
                callback_data=MoveCB(task_id=callback_data.id, to_category="money", to_subcat="none").pack()
            )
        ],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"back_to_list:{callback_data.category}:{callback_data.subcat}")]
    ])
    
    await callback.message.edit_text(
        f"📁 Переместить задачу:\n<b>{content}</b>\n\nВыберите категорию:",
        reply_markup=kb,
        parse_mode="HTML"
    )

# Выполнение перемещения
@dp.callback_query(MoveCB.filter())
async def process_move_execute(callback: CallbackQuery, callback_data: MoveCB):
    # Получаем исходную категорию для возврата
    result = await db_fetch("SELECT category, subcat FROM entries WHERE id = ?", (callback_data.task_id,))
    if not result:
        await callback.answer("Задача не найдена!")
        return
    
    old_cat, old_sub = result[0]
    
    # Получаем новый номер для целевой категории
    new_num = await get_next_task_number(callback_data.to_category, callback_data.to_subcat)
    
    # Перемещаем задачу
    await db_execute(
        "UPDATE entries SET category = ?, subcat = ?, task_number = ? WHERE id = ?",
        (callback_data.to_category, callback_data.to_subcat, new_num, callback_data.task_id)
    )
    
    await callback.answer(f"✅ Задача перемещена!")
    await view_items(callback, ViewCB(category=old_cat, subcat=old_sub))

# Кнопка возврата к списку
@dp.callback_query(F.data.startswith("back_to_list"))
async def back_to_list(callback: CallbackQuery):
    _, cat, sub = callback.data.split(":")
    await view_items(callback, ViewCB(category=cat, subcat=sub))

# Редактирование текста задачи
@dp.callback_query(TaskCB.filter(F.action == "edit"))
async def start_edit_task(callback: CallbackQuery, callback_data: TaskCB, state: FSMContext):
    result = await db_fetch("SELECT content FROM entries WHERE id = ?", (callback_data.id,))
    if not result:
        await callback.answer("Задача не найдена!")
        return
    
    content = result[0][0]
    
    await state.update_data(
        edit_task_id=callback_data.id,
        edit_category=callback_data.category,
        edit_subcat=callback_data.subcat
    )
    
    await callback.message.answer(
        f"✏️ Текущий текст:\n<b>{content}</b>\n\n"
        f"Введите новый текст:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_edit")]]
        )
    )
    await state.set_state(Form.waiting_for_edit_text)
    await callback.answer()

@dp.callback_query(F.data == "cancel_edit")
async def cancel_edit(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    await callback.message.delete()
    
    if data.get('edit_category'):
        await view_items(callback, ViewCB(category=data['edit_category'], subcat=data['edit_subcat']))

@dp.message(Form.waiting_for_edit_text)
async def process_edit_text(message: types.Message, state: FSMContext):
    new_text = message.text.strip()
    if not new_text:
        await message.answer("❌ Текст не может быть пустым. Попробуйте еще раз:")
        return
    
    data = await state.get_data()
    await db_execute("UPDATE entries SET content = ? WHERE id = ?", (new_text, data['edit_task_id']))
    
    await state.clear()
    await message.answer(f"✅ Текст задачи обновлен!")
    
    # Возвращаемся к списку
    query = "SELECT id, content, status, owner, task_number FROM entries WHERE category = ?"
    params = [data['edit_category']]
    if data['edit_subcat'] != "none":
        query += " AND subcat = ?"
        params.append(data['edit_subcat'])
    query += " ORDER BY status ASC, task_number ASC, id DESC"
    
    rows = await db_fetch(query, tuple(params))
    
    title_map = {
        "projects": "🚀 ПРОЕКТЫ", 
        "today": "📅 ЗАДАЧИ НА СЕГОДНЯ", 
        "plans": f"🗓 ПЛАНЫ ({data['edit_subcat']})", 
        "debts": "💸 ДОЛГИ", 
        "notes": "📝 ЗАМЕТКИ"
    }
    
    text = f"<b>{title_map.get(data['edit_category'], data['edit_category'].upper())}</b>\n"
    text += f"━━━━━━━━━━━━━━\n"
    
    kb = []
    for eid, content, status, owner, task_num in rows:
        icon = "✅" if status == 1 else "⭕️"
        own_icon = "🅰️" if owner == "Артем" else ("🅽" if owner == "Никита" else "👥")
        num_display = f"#{task_num} " if status == 0 and task_num > 0 else ""
        display_text = f"<s>{content}</s>" if status == 1 else content
        text += f"{icon} {own_icon} {num_display}{display_text}\n"
        
        btn_text = f"↩️ Вернуть" if status == 1 else f"✅ #{task_num}" if task_num > 0 else f"✅ {content[:10]}..."
        
        row_buttons = [
            InlineKeyboardButton(
                text=btn_text, 
                callback_data=TaskCB(action="toggle", id=eid, category=data['edit_category'], subcat=data['edit_subcat']).pack()
            )
        ]
        
        if status == 0:
            row_buttons.append(
                InlineKeyboardButton(
                    text="🔢", 
                    callback_data=TaskCB(action="change_num", id=eid, category=data['edit_category'], subcat=data['edit_subcat']).pack()
                )
            )
            row_buttons.append(
                InlineKeyboardButton(
                    text="✏️", 
                    callback_data=TaskCB(action="edit", id=eid, category=data['edit_category'], subcat=data['edit_subcat']).pack()
                )
            )
        
        row_buttons.append(
            InlineKeyboardButton(
                text="❌", 
                callback_data=TaskCB(action="delete", id=eid, category=data['edit_category'], subcat=data['edit_subcat']).pack()
            )
        )
        
        kb.append(row_buttons)
        
        if status == 0:
            kb.append([
                InlineKeyboardButton(
                    text="📋 Копировать", 
                    callback_data=TaskCB(action="copy", id=eid, category=data['edit_category'], subcat=data['edit_subcat']).pack()
                ),
                InlineKeyboardButton(
                    text="📁 Переместить", 
                    callback_data=TaskCB(action="move", id=eid, category=data['edit_category'], subcat=data['edit_subcat']).pack()
                )
            ])
    
    kb.append([InlineKeyboardButton(text="➕ Добавить запись", callback_data=AddCB(category=data['edit_category'], subcat=data['edit_subcat']).pack())])
    
    has_completed = any(r[2] == 1 for r in rows)
    if has_completed:
        kb.append([InlineKeyboardButton(text="🧹 Удалить выполненные", callback_data=f"clear_done:{data['edit_category']}:{data['edit_subcat']}")])
    
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=MenuCB(action="main").pack())])
    
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")

# Переупорядочивание задач
@dp.callback_query(F.data.startswith("reorder"))
async def process_reorder(callback: CallbackQuery):
    _, cat, sub = callback.data.split(":")
    
    await reorder_tasks(cat, sub)
    
    # Сначала отвечаем на callback
    await callback.answer("✅ Номера переупорядочены!")
    
    # Потом обновляем сообщение
    try:
        await view_items(callback, ViewCB(category=cat, subcat=sub))
    except Exception as e:
        logging.error(f"Error updating message: {e}")

# Очистка выполненных
@dp.callback_query(F.data.startswith("clear_done"))
async def process_clear(callback: CallbackQuery):
    _, cat, sub = callback.data.split(":")
    
    query = "DELETE FROM entries WHERE category = ? AND status = 1"
    params = [cat]
    if sub != "none":
        query += " AND subcat = ?"
        params.append(sub)
        
    await db_execute(query, tuple(params))
    await callback.answer("Выполненные задачи очищены")
    
    # Возврат к просмотру
    await view_items(callback, ViewCB(category=cat, subcat=sub))

# --- ЗАПУСК ---
async def main():
    await init_db()
    print("Бот запущен...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен")