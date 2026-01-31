"""
🦊 FoxFamilyTask Bot — Семейный менеджер задач
aiogram 3.22.0 + PyQt6 6.10.0
Полная версия с GUI, FSM, напоминаниями и защитой от ошибок
Эта часть содержит импорты, настройку логирования, константы, FSM состояния и утилиты для БД
"""

# ────────────────────────────────────────────────
# Импорты стандартных библиотек Python
# ────────────────────────────────────────────────
import asyncio  # Для асинхронного программирования, необходим для aiogram
import json  # Для работы с JSON-файлами (БД бота)
import logging  # Для логирования событий, ошибок и информации
import secrets  # Для генерации крипто-стойких ключей приглашения
import sys  # Для выхода из приложения и доступа к аргументам
import time  # Для работы с временными метками (ключи, дедлайны)
import uuid  # Для генерации уникальных ID семей и задач
from datetime import datetime, timedelta  # Для дат, времени и дельт (напоминания, дедлайны)
from pathlib import Path  # Для работы с путями файлов (БД, папки)
from typing import Any, Dict, Optional  # Для аннотаций типов (читаемость кода)

# ────────────────────────────────────────────────
# Импорты PyQt6 (версия 6.10.0)
# ────────────────────────────────────────────────
from PyQt6.QtCore import QObject, QThread, pyqtSignal  # Для потоков и сигналов (запуск бота в фоне)
from PyQt6.QtGui import QFont  # Для шрифтов в GUI
from PyQt6.QtWidgets import (
    QApplication,  # Основное приложение Qt
    QFileDialog,  # Диалог выбора файлов/папок
    QLabel,  # Метки для текста в GUI
    QLineEdit,  # Поле ввода текста
    QMessageBox,  # Диалоги сообщений/ошибок
    QPushButton,  # Кнопки
    QStackedWidget,  # Стек виджетов для wizard
    QVBoxLayout,  # Вертикальный макет
    QWidget,  # Базовый виджет
    QMainWindow,  # Основное окно
)

# ────────────────────────────────────────────────
# Импорты aiogram (версия 3.22.0)
# ────────────────────────────────────────────────
from aiogram import Bot, Dispatcher, F  # Бот, диспетчер, фильтры
from aiogram.enums import ParseMode  # Парсинг HTML
from aiogram.filters import Command  # Фильтр команд
from aiogram.fsm.context import FSMContext  # Контекст состояний
from aiogram.fsm.state import State, StatesGroup  # Состояния FSM
from aiogram.fsm.storage.memory import MemoryStorage  # Хранение состояний в памяти
from aiogram.types import CallbackQuery, KeyboardButton, Message, ReplyKeyboardMarkup  # Типы сообщений, кнопок
from aiogram.utils.keyboard import InlineKeyboardBuilder  # Построитель inline-кнопок

# ────────────────────────────────────────────────
# Настройка логирования
# ────────────────────────────────────────────────
LOG_FILE = "foxfamily.log"  # Файл логов
logging.basicConfig(
    level=logging.INFO,  # Уровень логирования (INFO и выше)
    filename=LOG_FILE,  # Файл для записи
    filemode="a",  # Добавление в конец файла
    format="%(asctime)s [%(levelname)s] %(message)s",  # Формат лога
    encoding="utf-8",  # Кодировка
)


def log_info(msg: str) -> None:
    """Функция для вывода информации в лог и консоль."""
    logging.info(msg)
    print(msg)


def log_error(msg: str) -> None:
    """Функция для вывода ошибок в лог и консоль."""
    logging.error(msg)
    print(f"ERROR: {msg}")


# ────────────────────────────────────────────────
# Константы бота
# ────────────────────────────────────────────────
DB_PATH = Path("foxfamily_db.json")  # Путь к файлу БД
KEY_LENGTH_BYTES = 48  # Длина ключа в байтах для secrets.token_urlsafe (~64 символа)
KEY_EXPIRY_SEC = 600  # Время жизни ключа в секундах (10 минут)
MAX_FREE_MEMBERS = 25  # Максимум участников в семье бесплатно
WARN_MEMBERS_THRESHOLD = 20  # Порог для предупреждения о приближении к лимиту

# Опции напоминаний (ключ: название, значение: секунды)
REMINDER_OPTIONS = {
    "Без напоминаний": 0,
    "За 1 день": 86400,
    "За 3 часа": 10800,
    "За 1 час": 3600,
    "За 30 минут": 1800,
    "За 10 минут": 600,
}


# ────────────────────────────────────────────────
# FSM состояния
# ────────────────────────────────────────────────
class FamilyStates(StatesGroup):
    """Класс для состояний FSM (Finite State Machine)."""
    join_key = State()  # Состояние ввода ключа для присоединения к семье
    join_nick = State()  # Состояние ввода ника для присоединения
    change_name = State()  # Состояние изменения имени семьи
    create_task_type = State()  # Состояние выбора типа задачи
    create_task_desc = State()  # Состояние ввода описания задачи
    create_task_date = State()  # Состояние ввода даты дедлайна
    create_task_time = State()  # Состояние ввода времени дедлайна
    create_task_time_confirm = State()  # Состояние подтверждения времени
    create_task_confirm_datetime = State()  # Состояние финального подтверждения даты и времени
    create_task_reminder = State()  # Состояние выбора напоминания
    create_task_items = State()  # Состояние ввода списка для покупок
    task_progress = State()  # Состояние обновления прогресса задачи


# ────────────────────────────────────────────────
# Утилиты для БД
# ────────────────────────────────────────────────

def load_db() -> Dict[str, Any]:
    """Загружает данные из JSON-файла БД.

    Если файл не существует или повреждён, возвращает шаблонную структуру БД.
    Шаблон включает токен, семьи и пользователей.

    Возвращает:
        Dict[str, Any]: Словарь с данными БД.

    Пример использования:
        db = load_db()
        families = db['families']
    """
    if DB_PATH.exists():
        try:
            with open(DB_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Проверка на наличие ключевых полей
                if 'families' not in data:
                    data['families'] = {}
                if 'users' not in data:
                    data['users'] = {}
                return data
        except Exception as e:
            log_error(f"Load DB error: {e}")
            return {"telegram_token": "", "families": {}, "users": {}}
    return {"telegram_token": "", "families": {}, "users": {}}


def atomic_save_json(data: Dict[str, Any], path: Path = DB_PATH) -> None:
    """Атомарно сохраняет данные в JSON-файл.

    Сначала пишет в временный файл .tmp, затем заменяет основной, чтобы избежать повреждений при сбое.

    Аргументы:
        data (Dict[str, Any]): Данные для сохранения.
        path (Path): Путь к файлу (по умолчанию DB_PATH).

    Пример использования:
        db = load_db()
        db['families']['new'] = {}
        atomic_save_json(db)
    """
    temp = path.with_suffix(".tmp")
    try:
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        temp.replace(path)
    except Exception as e:
        log_error(f"Save DB error: {e}")


### Часть 2: Утилиты для ключей, прогресса, уведомлений и напоминаний

# ────────────────────────────────────────────────
# Утилиты для ключей
# ────────────────────────────────────────────────

def generate_family_key() -> Dict[str, Any]:
    """Генерирует крипто-стойкий ключ приглашения.

    Использует secrets.token_urlsafe для безопасной генерации.

    Возвращает:
        Dict[str, Any]: Словарь с ключом, временем создания и истечения.

    Пример возврата:
        {
            "value": "long_random_string",
            "created": 1706634251.123,
            "expires": 1706634851.123
        }
    """
    return {
        "value": secrets.token_urlsafe(KEY_LENGTH_BYTES),
        "created": time.time(),
        "expires": time.time() + KEY_EXPIRY_SEC,
    }


def is_key_valid(key_input: str, family: Dict[str, Any]) -> bool:
    """Проверяет валидность ключа приглашения.

    Если ключ истёк, удаляет его из семьи и сохраняет БД.

    Аргументы:
        key_input (str): Введённый ключ.
        family (Dict[str, Any]): Данные семьи из БД.

    Возвращает:
        bool: True, если ключ валиден, False иначе.
    """
    kd = family.get("active_key")
    if not kd:
        return False
    if time.time() > kd["expires"]:
        family["active_key"] = None
        atomic_save_json(load_db())
        return False
    return key_input.strip() == kd["value"]


# ────────────────────────────────────────────────
# Утилиты
# ────────────────────────────────────────────────

def progress_bar(pct: int) -> str:
    """Генерирует строку прогресс-бара.

    Использует символы ■ и □ для заполненной и пустой части.

    Аргументы:
        pct (int): Процент прогресса (0-100).

    Возвращает:
        str: Строка бара, например "■■■■■■□□□□" для 60%.

    Пример:
        print(progress_bar(75))  # ■■■■■■■■□□
    """
    filled = pct // 10
    return "■" * filled + "□" * (10 - filled)


async def notify_family(bot: Bot, fam_id: str, text: str, markup=None) -> None:
    """Отправляет уведомление всем членам семьи.

    С задержкой 0.1 сек между сообщениями для защиты от флуда Telegram.

    Аргументы:
        bot (Bot): Экземпляр бота.
        fam_id (str): ID семьи.
        text (str): Текст уведомления.
        markup (Optional[ReplyKeyboardMarkup]): Клавиатура (опционально).
    """
    db = load_db()
    fam = db["families"].get(fam_id, {})
    for uid_str in fam.get("members", {}):
        try:
            await bot.send_message(int(uid_str), text, reply_markup=markup, parse_mode=ParseMode.HTML)
            await asyncio.sleep(0.1)
        except Exception as e:
            log_error(f"Notify error for {uid_str}: {e}")


async def reminders_loop(bot: Bot):
    """Фоновый цикл для отправки напоминаний о задачах.

    Проверяет каждые 60 секунд все задачи во всех семьях.
    Если дедлайн близко — отправляет уведомление и отмечает 'reminder_sent'.
    Сохраняет БД только если были изменения.
    """
    while True:
        await asyncio.sleep(60)
        db = load_db()
        now = time.time()
        updated = False
        for fam_id, fam in db["families"].items():
            for task_id, task in fam.get("tasks", {}).items():
                if "reminder_sent" in task:
                    continue
                rs = task.get("reminder_sec", 0)
                if rs <= 0 or "deadline" not in task:
                    continue
                try:
                    dl = datetime.strptime(task["deadline"], "%d.%m.%Y %H:%M")
                    if dl.timestamp() - now <= rs:
                        text = f"🦊 Напоминание: задача «{task['desc']}» скоро нужно выполнить."
                        await notify_family(bot, fam_id, text)
                        task["reminder_sent"] = True
                        updated = True
                except Exception as e:
                    log_error(f"Reminder error for task {task_id}: {e}")
        if updated:
            atomic_save_json(db)


# ────────────────────────────────────────────────
# GUI (полный wizard как в оригинале)
# ────────────────────────────────────────────────

class MainWindow(QMainWindow):
    """Основное окно GUI для настройки бота.

    Использует QStackedWidget для шагов настройки (wizard).

    Шаги:
        1. Папка данных
        2. База данных
        3. Папка временных файлов
        4. Токен и запуск бота

    После настройки запускает BotThread.
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("🦊 FoxFamilyTask Bot — Настройка")
        self.resize(720, 480)
        self.db = load_db()
        self.stacked = QStackedWidget(self)
        self.setCentralWidget(self.stacked)

        self.page1 = self.create_page1()
        self.page2 = self.create_page2()
        self.page3 = self.create_page3()
        self.page4 = self.create_page4()

        self.stacked.addWidget(self.page1)
        self.stacked.addWidget(self.page2)
        self.stacked.addWidget(self.page3)
        self.stacked.addWidget(self.page4)
        self.stacked.setCurrentIndex(0)

    def create_page1(self) -> QWidget:
        """Создаёт первую страницу: выбор папки данных."""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Шаг 1: Папка для данных (db, логи)", font=QFont("Arial", 11)))
        self.data_edit = QLineEdit(self.db.get("data_folder", str(Path.cwd())))
        browse_btn = QPushButton("Выбрать папку...")
        browse_btn.clicked.connect(self.browse_data)
        lay.addWidget(self.data_edit)
        lay.addWidget(browse_btn)
        lay.addStretch()
        next_btn = QPushButton("Далее →")
        next_btn.clicked.connect(self.to_page2)
        lay.addWidget(next_btn)
        return w

    def browse_data(self) -> None:
        """Диалог выбора папки данных."""
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку данных")
        if folder:
            self.data_edit.setText(folder)

    def to_page2(self) -> None:
        """Переход ко второй странице после проверки."""
        path = self.data_edit.text().strip()
        if not path or not Path(path).is_dir():
            QMessageBox.critical(self, "Ошибка", "Укажите корректную папку!")
            return
        self.db["data_folder"] = path
        atomic_save_json(self.db)
        self.stacked.setCurrentIndex(1)

    def create_page2(self) -> QWidget:
        """Создаёт вторую страницу: информация о БД."""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Шаг 2: База данных (foxfamily_db.json)", font=QFont("Arial", 11)))
        lay.addStretch()
        back_btn = QPushButton("← Назад")
        back_btn.clicked.connect(lambda: self.stacked.setCurrentIndex(0))
        next_btn = QPushButton("Далее →")
        next_btn.clicked.connect(lambda: self.stacked.setCurrentIndex(2))
        lay.addWidget(back_btn)
        lay.addWidget(next_btn)
        return w

    def create_page3(self) -> QWidget:
        """Создаёт третью страницу: папка временных файлов."""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Шаг 3: Папка для временных файлов (опционально)", font=QFont("Arial", 11)))
        self.output_edit = QLineEdit(self.db.get("output_base", ""))
        browse_btn = QPushButton("Выбрать папку...")
        browse_btn.clicked.connect(self.browse_output)
        lay.addWidget(self.output_edit)
        lay.addWidget(browse_btn)
        lay.addStretch()
        back_btn = QPushButton("← Назад")
        back_btn.clicked.connect(lambda: self.stacked.setCurrentIndex(1))
        next_btn = QPushButton("Далее →")
        next_btn.clicked.connect(self.to_page4)
        lay.addWidget(back_btn)
        lay.addWidget(next_btn)
        return w

    def browse_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку для временных файлов")
        if folder:
            self.output_edit.setText(folder)

    def to_page4(self) -> None:
        path = self.output_edit.text().strip()
        if path and not Path(path).is_dir():
            QMessageBox.critical(self, "Ошибка", "Укажите корректную папку!")
            return
        self.db["output_base"] = path
        atomic_save_json(self.db)
        self.stacked.setCurrentIndex(3)

    def create_page4(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Шаг 4: Telegram API-токен", font=QFont("Arial", 11)))
        self.token_edit = QLineEdit(self.db.get("telegram_token", ""))
        lay.addWidget(self.token_edit)
        help_btn = QPushButton("Как получить токен?")
        help_btn.clicked.connect(self.show_token_help)
        lay.addWidget(help_btn)
        save_btn = QPushButton("Сохранить и запустить бота")
        save_btn.clicked.connect(self.save_and_launch)
        lay.addWidget(save_btn)
        back_btn = QPushButton("← Назад")
        back_btn.clicked.connect(lambda: self.stacked.setCurrentIndex(2))
        lay.addWidget(back_btn)
        self.status_label = QLabel("Статус: бот не запущен")
        lay.addWidget(self.status_label)
        return w

    def show_token_help(self) -> None:
        QMessageBox.information(self, "Инструкция", "Найдите @BotFather в Telegram и создайте бота. Скопируйте токен.")

    def save_and_launch(self) -> None:
        token = self.token_edit.text().strip()
        if not token:
            QMessageBox.critical(self, "Ошибка", "Введите токен!")
            return
        self.db["telegram_token"] = token
        atomic_save_json(self.db)
        self.bot_thread = BotThread(token)
        self.bot_thread.status_updated.connect(self.update_status)
        self.bot_thread.start()
        self.status_label.setText("Статус: бот запущен (в фоне)...")
        log_info("Admin started bot.")

    def update_status(self, msg: str) -> None:
        self.status_label.setText("Статус: " + msg)


# ────────────────────────────────────────────────
# BotThread
# ────────────────────────────────────────────────

class BotThread(QThread):
    status_updated = pyqtSignal(str)

    def __init__(self, token: str):
        super().__init__()
        self.token = token

    def run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(start_bot(self.token, self.status_updated))
        except Exception as e:
            log_error(f"BotThread error: {e}")
            self.status_updated.emit(f"Ошибка: {str(e)}")


# ────────────────────────────────────────────────
# Telegram Bot logic
# ────────────────────────────────────────────────

async def start_bot(token: str, status_signal: pyqtSignal) -> None:
    bot = Bot(token=token)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    status_signal.emit("Бот: polling...")

    # Клавиатуры
    def get_main_menu(current_name: str = "") -> ReplyKeyboardMarkup:
        kb = [
            [KeyboardButton(text=f"🦊 {current_name or 'Мои семьи'}"), KeyboardButton(text="➕ Создать семью")],
            [KeyboardButton(text="🔑 Присоединиться"), KeyboardButton(text="📋 Задачи")],
            [KeyboardButton(text="⚙️ Настройки семьи"), KeyboardButton(text="❓ Помощь")],
        ]
        return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True, input_field_placeholder="Выберите действие…")

    def get_process_kb():
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="⬅️ Назад")],
                [KeyboardButton(text="❌ Отмена")],
            ],
            resize_keyboard=True,
        )

    # /start
    @dp.message(Command("start"))
    async def cmd_start(message: Message, state: FSMContext) -> None:
        await state.clear()
        db = load_db()
        uid = str(message.from_user.id)
        user = db["users"].setdefault(uid, {"families": [], "current_family": ""})
        current_fam = user["current_family"]
        name = db["families"].get(current_fam, {}).get("name", "") if current_fam else ""
        text = "🦊 Добро пожаловать в My Fox Family!\n\nСоздайте свою семью или присоединитесь."
        await message.answer(text, reply_markup=get_main_menu(name), parse_mode=ParseMode.HTML)

    # Мои семьи
    @dp.message(F.text.contains("🦊"))
    async def my_families(message: Message, state: FSMContext) -> None:
        db = load_db()
        uid = str(message.from_user.id)
        user = db["users"].get(uid, {"families": []})
        if not user["families"]:
            text = "Пока вы ни в одной семье. Создайте свою (даже для одного) — удобно для задач с тачбордом!"
            await message.answer(text, reply_markup=get_main_menu())
            return

        current = user["current_family"]
        builder = InlineKeyboardBuilder()
        text = "Ваши семьи:\n"
        for fam_id in user["families"]:
            fam = db["families"].get(fam_id, {})
            name = fam.get("name", "🦊 My Fox Family")
            prefix = "★ " if fam_id == current else ""
            text += f"{prefix}{name} ({len(fam.get('members', {}))} участников)\n"
            builder.button(text=name, callback_data=f"switch:{fam_id}")
        builder.adjust(1)
        await message.answer(text, reply_markup=builder.as_markup())

    @dp.callback_query(F.data.startswith("switch:"))
    async def switch_family(cq: CallbackQuery, state: FSMContext) -> None:
        fam_id = cq.data.split(":")[1]
        db = load_db()
        uid = str(cq.from_user.id)
        if fam_id in db["users"].get(uid, {}).get("families", []):
            db["users"][uid]["current_family"] = fam_id
            atomic_save_json(db)
            name = db["families"][fam_id].get("name", "🦊 My Fox Family")
            await cq.message.edit_text(f"Переключились на {name}")
            await cq.message.answer("Главное меню:", reply_markup=get_main_menu(name))
        await cq.answer()

    # Создать семью
    @dp.message(F.text == "➕ Создать семью")
    async def create_family_handler(message: Message, state: FSMContext) -> None:
        db = load_db()
        uid = str(message.from_user.id)
        fam_id = str(uuid.uuid4())
        key_data = generate_family_key()
        db["families"][fam_id] = {
            "name": "🦊 My Fox Family",
            "created_at": time.time(),
            "creator_id": uid,
            "members": {uid: {"nick": "Создатель", "joined": time.time()}},
            "active_key": key_data,
            "tasks": {},
            "completed_tasks": {},
        }
        db["users"].setdefault(uid, {"families": [], "current_family": ""})
        db["users"][uid]["families"].append(fam_id)
        db["users"][uid]["current_family"] = fam_id
        atomic_save_json(db)

        text = (
            f"Семья создана: {db['families'][fam_id]['name']}\n"
            f"Ключ (10 мин): <code>{key_data['value']}</code>\n"
            "Поделитесь с семьей!"
        )
        await message.answer(text, parse_mode=ParseMode.HTML,
                             reply_markup=get_main_menu(db["families"][fam_id]["name"]))

    # Присоединиться
    @dp.message(F.text == "🔑 Присоединиться")
    async def join_family(message: Message, state: FSMContext) -> None:
        await state.set_state(FamilyStates.join_key)
        await message.answer("Введите ключ приглашения:")

    @dp.message(FamilyStates.join_key)
    async def join_key_handler(message: Message, state: FSMContext) -> None:
        key_input = message.text
        db = load_db()
        uid = str(message.from_user.id)
        found = False
        for fam_id, fam in db["families"].items():
            if is_key_valid(key_input, fam):
                members = fam["members"]
                if len(members) >= MAX_FREE_MEMBERS and fam.get("subscription") is None:
                    text = "Семья полная (25 чел.). Нужна подписка для большего количества."
                    builder = InlineKeyboardBuilder()
                    builder.button(text="Подписка (Stars)", callback_data="subscribe")
                    await message.answer(text, reply_markup=builder.as_markup())
                    await state.clear()
                    return
                if len(members) >= WARN_MEMBERS_THRESHOLD:
                    text = f"В семье уже {len(members)} чел. Лимит бесплатно 25."
                    await message.answer(text)
                if uid in members:
                    await message.answer("Вы уже в этой семье.")
                    await state.clear()
                    return
                await state.set_state(FamilyStates.join_nick)
                await state.update_data(fam_id=fam_id)
                await message.answer("Введите ваш никнейм в семье:")
                found = True
                break
        if not found:
            await message.answer("Неверный или истёкший ключ. Попробуйте снова.")
            await state.clear()

    @dp.message(FamilyStates.join_nick)
    async def join_nick_handler(message: Message, state: FSMContext) -> None:
        nick = message.text.strip()
        data = await state.get_data()
        fam_id = data.get("fam_id")
        if not fam_id:
            await message.answer("Ошибка, начните заново.")
            await state.clear()
            return
        db = load_db()
        fam = db["families"].get(fam_id, {})
        if any(m["nick"] == nick for m in fam.get("members", {}).values()):
            await message.answer("Никнейм занят. Выберите другой.")
            return
        uid = str(message.from_user.id)
        fam["members"][uid] = {"nick": nick, "joined": time.time()}
        db["users"].setdefault(uid, {"families": [], "current_family": ""})
        if fam_id not in db["users"][uid]["families"]:
            db["users"][uid]["families"].append(fam_id)
        db["users"][uid]["current_family"] = fam_id
        atomic_save_json(db)

        text = f"{nick} присоединился к семье!"
        await notify_family(message.bot, fam_id, text)
        name = fam["name"]
        await message.answer(f"Вы в семье {name}!", reply_markup=get_main_menu(name))
        await state.clear()

    # Задачи
    @dp.message(F.text == "📋 Задачи")
    async def tasks_handler(message: Message, state: FSMContext) -> None:
        db = load_db()
        uid = str(message.from_user.id)
        user = db["users"].get(uid, {})
        fam_id = user.get("current_family")
        if not fam_id:
            await message.answer("Сначала выберите семью.")
            return
        fam = db["families"].get(fam_id, {})
        tasks = fam.get("tasks", {})
        completed = fam.get("completed_tasks", {})
        builder = InlineKeyboardBuilder()
        builder.button(text="➕ Новая задача", callback_data="new_task")
        builder.button(text="Завершённые", callback_data="completed")
        builder.adjust(1)
        text = "Активные задачи:\n" if tasks else "Нет активных задач.\n"
        for task_id, task in tasks.items():
            pct = task.get("progress", 0)
            bar = progress_bar(pct)
            text += f"{task['desc']} ({bar} {pct}%)\n"
        await message.answer(text, reply_markup=builder.as_markup())

    @dp.callback_query(F.data == "new_task")
    async def new_task(cq: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(FamilyStates.create_task_type)
        builder = InlineKeyboardBuilder()
        types = ["Обычная", "Покупки", "Поездка", "Уборка"]
        for t in types:
            builder.button(text=t, callback_data=f"task_type:{t}")
        builder.adjust(1)
        await cq.message.answer("Выберите тип задачи:", reply_markup=builder.as_markup())
        await cq.answer()

    @dp.callback_query(F.data.startswith("task_type:"))
    async def task_type_handler(cq: CallbackQuery, state: FSMContext) -> None:
        task_type = cq.data.split(":")[1]
        await state.update_data(task_type=task_type)
        await state.set_state(FamilyStates.create_task_desc)
        await cq.message.answer("Опишите задачу:", reply_markup=get_process_kb())
        await cq.answer()

    @dp.message(FamilyStates.create_task_desc)
    async def task_desc_handler(message: Message, state: FSMContext) -> None:
        desc = message.text.strip()
        if not desc:
            await message.answer("Описание не может быть пустым. Попробуйте снова.")
            return
        await state.update_data(desc=desc)
        current_time = datetime.fromtimestamp(time.time()).strftime("%d.%m.%Y %H:%M")
        text = (
            f"Текущее время сервера: {current_time} (UTC+0).\n"
            "Какое у вас время? Укажите дату (ДД.ММ.ГГГГ, например 31.01.2026):"
        )
        await message.answer(text, reply_markup=get_process_kb())
        await state.set_state(FamilyStates.create_task_date)

    @dp.message(FamilyStates.create_task_date)
    async def task_date_handler(message: Message, state: FSMContext) -> None:
        if message.text in ("⬅️ Назад", "❌ Отмена"):
            if message.text == "❌ Отмена":
                await state.clear()
                await message.answer("Отмена. Вернулись в меню.", reply_markup=get_main_menu())
            else:
                await state.set_state(FamilyStates.create_task_desc)
                await message.answer("Вернулись к описанию задачи. Напишите заново.")
            return

        date_str = message.text.strip()
        try:
            dt = datetime.strptime(date_str, "%d.%m.%Y")
            if dt < datetime.now() - timedelta(hours=1):
                await message.answer("Дата уже прошла или слишком близко. Выберите будущую дату.")
                return
            await state.update_data(deadline_date=date_str)
        except ValueError:
            await message.answer("Неверный формат. Пример: 05.02.2026\nПопробуйте снова.")
            return

        await state.set_state(FamilyStates.create_task_time)
        await message.answer("Теперь укажите время (ЧЧ:ММ)\nПример: 18:30")

    @dp.message(FamilyStates.create_task_time)
    async def task_time_handler(message: Message, state: FSMContext) -> None:
        time_str = message.text.strip()
        try:
            datetime.strptime(time_str, "%H:%M")
            data = await state.get_data()
            full = f"{data['deadline_date']} {time_str}"
            dt = datetime.strptime(full, "%d.%m.%Y %H:%M")
            if dt < datetime.now():
                await message.answer("Это время уже прошло. Выберите будущее.")
                return
            await state.update_data(deadline=full)

            kb = InlineKeyboardBuilder()
            kb.button(text="Да", callback_data="time_confirm:yes")
            kb.button(text="Нет", callback_data="time_confirm:no")
            kb.adjust(2)

            await message.answer(
                f"Нужно успеть {full}\n"
                "Это время примерно совпадает с вашим? (сервер в UTC+0)",
                reply_markup=kb.as_markup()
            )
            await state.set_state(FamilyStates.create_task_time_confirm)
        except ValueError:
            await message.answer("Неверный формат времени. Пример: 14:45\nПопробуйте снова.")

    @dp.callback_query(F.data.startswith("time_confirm:"))
    async def time_confirm(cq: CallbackQuery, state: FSMContext) -> None:
        confirm = cq.data.split(":")[1]
        if confirm == "yes":
            await state.set_state(FamilyStates.create_task_reminder)
            kb = InlineKeyboardBuilder()
            for opt in REMINDER_OPTIONS:
                kb.button(text=opt, callback_data=f"reminder:{opt}")
            kb.adjust(2)
            await cq.message.answer("Нужно ли напомнить заранее? Выберите:", reply_markup=kb.as_markup())
        else:
            await state.set_state(FamilyStates.create_task_date)
            await cq.message.answer("Укажите дату заново (ДД.ММ.ГГГГ):")
        await cq.answer()

    @dp.callback_query(F.data.startswith("reminder:"))
    async def task_reminder_handler(cq: CallbackQuery, state: FSMContext) -> None:
        option = cq.data.split(":")[1]
        reminder_sec = REMINDER_OPTIONS.get(option, 0)
        await state.update_data(reminder_sec=reminder_sec)
        data = await state.get_data()
        if data.get("task_type") == "Покупки":
            await state.set_state(FamilyStates.create_task_items)
            await cq.message.answer("Введите список продуктов (по строкам):")
        else:
            await create_task_finish(cq.message, state)
        await cq.answer()

    @dp.message(FamilyStates.create_task_items)
    async def task_items_handler(message: Message, state: FSMContext) -> None:
        items = message.text.strip().split("\n")
        items = [i.strip() for i in items if i.strip()]
        if not items:
            await message.answer("Список не может быть пустым. Попробуйте снова.")
            return
        await state.update_data(items=items)
        await create_task_finish(message, state)

    async def create_task_finish(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        db = load_db()
        uid = str(message.from_user.id)
        fam_id = db["users"][uid]["current_family"]
        task_id = str(uuid.uuid4())
        task = {
            "creator_id": uid,
            "desc": data["desc"],
            "type": data["task_type"],
            "deadline": data["deadline"],
            "reminder_sec": data["reminder_sec"],
            "progress": 0,
            "assignees": [],
            "updates": [],
            "items": data.get("items", []),
            "created_at": time.time(),
        }
        db["families"][fam_id]["tasks"][task_id] = task
        atomic_save_json(db)
        text = f"Новая задача от {db['families'][fam_id]['members'][uid]['nick']}: {task['desc']}\nДедлайн: {task['deadline']}"
        await notify_family(message.bot, fam_id, text)
        await message.answer("Задача создана!", reply_markup=get_main_menu())
        await state.clear()

    # Настройки семьи
    @dp.message(F.text == "⚙️ Настройки семьи")
    async def settings_handler(message: Message, state: FSMContext) -> None:
        db = load_db()
        uid = str(message.from_user.id)
        fam_id = db["users"].get(uid, {}).get("current_family")
        if not fam_id or db["families"][fam_id].get("creator_id") != uid:
            await message.answer("Только создатель может менять настройки.")
            return
        builder = InlineKeyboardBuilder()
        builder.button(text="Изменить название", callback_data="change_name")
        builder.button(text="Генерировать новый ключ", callback_data="new_key")
        builder.button(text="Завершённые задачи", callback_data="completed_tasks")
        builder.adjust(1)
        await message.answer("Настройки семьи:", reply_markup=builder.as_markup())

    @dp.callback_query(F.data == "change_name")
    async def change_name(cq: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(FamilyStates.change_name)
        await cq.message.answer("Введите новое название семьи:")
        await cq.answer()

    @dp.message(FamilyStates.change_name)
    async def change_name_handler(message: Message, state: FSMContext) -> None:
        new_name = message.text.strip()
        if not new_name:
            await message.answer("Название не может быть пустым.")
            return
        db = load_db()
        uid = str(message.from_user.id)
        fam_id = db["users"][uid]["current_family"]
        db["families"][fam_id]["name"] = new_name
        atomic_save_json(db)
        text = f"Название семьи изменено на {new_name}"
        await notify_family(message.bot, fam_id, text)
        await message.answer("Название изменено!", reply_markup=get_main_menu(new_name))
        await state.clear()

    # Завершённые задачи
    @dp.callback_query(F.data == "completed_tasks")
    async def completed_tasks(cq: CallbackQuery, state: FSMContext) -> None:
        db = load_db()
        uid = str(cq.from_user.id)
        fam_id = db["users"][uid]["current_family"]
        completed = db["families"][fam_id].get("completed_tasks", {})
        text = "Завершённые задачи:\n" if completed else "Нет завершённых задач.\n"
        for task_id, task in completed.items():
            created = datetime.fromtimestamp(task["created_at"]).strftime("%d.%m.%Y %H:%M")
            completed_at = datetime.fromtimestamp(task.get("completed_at", time.time())).strftime("%d.%m.%Y %H:%M")
            contrib = ", ".join([f"{u['nick']} ({u.get('percent', 0)}%)" for u in task.get("updates", [])])
            text += f"{task['desc']} (создана {created}, завершена {completed_at}, участники: {contrib or 'не указано'})\n"
        await cq.message.answer(text)
        await cq.answer()

    # Подписка
    @dp.callback_query(F.data == "subscribe")
    async def subscribe(cq: CallbackQuery, state: FSMContext) -> None:
        text = "Подписка через Telegram Stars:\n- До 50: 100 Stars/мес\n- До 75: 200 Stars/мес\n- До 100: 350 Stars/мес\nСвяжитесь с админом для активации."
        await cq.message.answer(text)
        await cq.answer()

    # Помощь
    @dp.message(F.text == "❓ Помощь")
    async def help_handler(message: Message, state: FSMContext) -> None:
        text = "Помощь по боту:\n- Создайте семью\n- Присоединяйтесь по ключу\n- Создавайте задачи с прогрессом\n- Уведомления и напоминания\nДля подписки — кнопка в настройках."
        await message.answer(text)

    asyncio.create_task(reminders_loop(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
