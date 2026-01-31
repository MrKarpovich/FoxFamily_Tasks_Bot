"""
🦊 FoxFamilyTask Bot — Семейный менеджер задач (2026 Edition)
aiogram 3.22.0 + PyQt6 6.10.0
Полностью переработанная архитектура диалогов с контекстным меню
"""

import asyncio
import json
import logging
import secrets
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, List
from dotenv import load_dotenv  # Для безопасного хранения токена

# ────────────────────────────────────────────────
# Импорты PyQt6 (версия 6.10.0)
# ────────────────────────────────────────────────
from PyQt6.QtCore import QObject, QThread, pyqtSignal, Qt
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QLabel, QLineEdit, QMessageBox, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget, QMainWindow, QTextEdit, QComboBox,
    QCheckBox, QHBoxLayout, QFrame, QScrollArea, QGridLayout
)

# ────────────────────────────────────────────────
# Импорты aiogram (версия 3.22.0)
# ────────────────────────────────────────────────
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery, KeyboardButton, Message, ReplyKeyboardMarkup,
    ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
)
from aiogram.utils.formatting import Text, Bold, Italic, Code
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ────────────────────────────────────────────────
# Конфигурация и константы
# ────────────────────────────────────────────────
load_dotenv()  # Загружаем .env для токена

LOG_FILE = "foxfamily.log"
DB_PATH = Path("foxfamily_db.json")
ENV_PATH = Path(".env")

# Безопасная загрузка токена из .env
def get_telegram_token() -> str:
    if ENV_PATH.exists():
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip() and not line.startswith("#"):
                    key, _, value = line.partition("=")
                    if key.strip() == "TELEGRAM_BOT_TOKEN":
                        return value.strip().strip('"').strip("'")
    return ""

# Константы
KEY_LENGTH_BYTES = 48
KEY_EXPIRY_SEC = 600
MAX_FREE_MEMBERS = 25
WARN_MEMBERS_THRESHOLD = 20

REMINDER_OPTIONS = {
    "🚫 Без напоминаний": 0,
    "⏰ За 1 день": 86400,
    "⏰ За 3 часа": 10800,
    "⏰ За 1 час": 3600,
    "⏰ За 30 минут": 1800,
    "⏰ За 10 минут": 600,
}

TASK_TYPES = {
    "📝 Обычная": "regular",
    "🛒 Покупки": "shopping",
    "🚗 Поездка": "trip",
    "🧹 Уборка": "cleaning",
    "🎂 Событие": "event"
}

# ────────────────────────────────────────────────
# Настройка логирования
# ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    filename=LOG_FILE,
    filemode="a",
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8",
)

def log_info(msg: str) -> None:
    logging.info(msg)
    print(f"[INFO] {msg}")

def log_error(msg: str) -> None:
    logging.error(msg)
    print(f"[ERROR] {msg}")

# ────────────────────────────────────────────────
# FSM States — полностью переработано под контекстную навигацию
# ────────────────────────────────────────────────
class GlobalStates(StatesGroup):
    """Глобальные состояния вне контекста семьи"""
    join_key = State()
    join_nick = State()
    settings_timezone = State()

class FamilyStates(StatesGroup):
    """Состояния внутри контекста семьи"""
    change_name = State()
    create_task_type = State()
    create_task_desc = State()
    create_task_deadline = State()  # Объединённый ввод даты+времени
    create_task_reminder = State()
    create_task_items = State()
    update_task_progress = State()
    update_task_items = State()
    leave_family_confirm = State()

# ────────────────────────────────────────────────
# Утилиты для БД — исправлена гонка условий
# ────────────────────────────────────────────────
def load_db() -> Dict[str, Any]:
    """Безопасная загрузка БД с валидацией структуры"""
    if DB_PATH.exists():
        try:
            with open(DB_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Валидация структуры
                if not isinstance(data, dict):
                    raise ValueError("Invalid DB structure")
                data.setdefault("families", {})
                data.setdefault("users", {})
                data.setdefault("settings", {"default_timezone": "UTC"})
                return data
        except Exception as e:
            log_error(f"Load DB error: {e}. Creating new DB.")
    return {"families": {}, "users": {}, "settings": {"default_timezone": "UTC"}}

def atomic_save_db(db: Dict[str, Any]) -> None:
    """Атомарное сохранение БД без гонки условий"""
    temp = DB_PATH.with_suffix(".tmp")
    try:
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=2, ensure_ascii=False)
        temp.replace(DB_PATH)
    except Exception as e:
        log_error(f"Atomic save error: {e}")
        raise

def generate_family_key() -> Dict[str, Any]:
    """Генерация безопасного ключа приглашения"""
    return {
        "value": secrets.token_urlsafe(KEY_LENGTH_BYTES),
        "created": time.time(),
        "expires": time.time() + KEY_EXPIRY_SEC,
    }

def is_key_valid(key_input: str, family: Dict[str, Any]) -> bool:
    """Валидация ключа без гонки условий (изменения возвращаются через аргумент)"""
    kd = family.get("active_key")
    if not kd:
        return False
    if time.time() > kd["expires"]:
        family["active_key"] = None  # Изменение в переданном объекте
        return False
    return secrets.compare_digest(key_input.strip(), kd["value"])

# ────────────────────────────────────────────────
# Утилиты UI
# ────────────────────────────────────────────────
def progress_bar(pct: int) -> str:
    """Визуальный прогресс-бар с эмодзи"""
    filled = min(10, max(0, pct // 10))
    return f"[{'●' * filled}{'○' * (10 - filled)}] {pct}%"

def format_deadline(deadline_str: str) -> str:
    """Форматирование дедлайна для отображения"""
    try:
        dt = datetime.strptime(deadline_str, "%d.%m.%Y %H:%M")
        now = datetime.now()
        delta = dt - now

        if delta.days < 0:
            return f"⏱️ {deadline_str} (просрочено!)"
        elif delta.days == 0:
            hours = int(delta.total_seconds() // 3600)
            if hours <= 1:
                return f"🔥 {deadline_str} (менее часа!)"
            return f"⏰ {deadline_str} (сегодня)"
        elif delta.days == 1:
            return f"🌅 {deadline_str} (завтра)"
        else:
            return f"📅 {deadline_str} ({delta.days} дн.)"
    except:
        return f"📅 {deadline_str}"

def get_main_menu_kb() -> ReplyKeyboardMarkup:
    """Главное меню (вне семьи)"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Мои семьи")],
            [KeyboardButton(text="➕ Создать семью"), KeyboardButton(text="🔑 Присоединиться")],
            [KeyboardButton(text="⚙️ Настройки"), KeyboardButton(text="❓ Помощь")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Главное меню: выберите действие..."
    )

def get_family_menu_kb(family_name: str) -> ReplyKeyboardMarkup:
    """Меню внутри семьи"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=f"🏡 {family_name}")],
            [KeyboardButton(text="📋 Задачи"), KeyboardButton(text="➕ Новая задача")],
            [KeyboardButton(text="👥 Участники"), KeyboardButton(text="⚙️ Настройки семьи")],
            [KeyboardButton(text="🏠 Выйти из семьи")],
        ],
        resize_keyboard=True,
        input_field_placeholder=f"Семья «{family_name}»: выберите действие..."
    )

def get_cancel_kb() -> ReplyKeyboardMarkup:
    """Клавиатура отмены для любого состояния FSM"""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
        input_field_placeholder="Нажмите ❌ Отмена для выхода"
    )

async def notify_family(bot: Bot, fam_id: str, text: str) -> None:
    """Уведомление всех участников семьи с защитой от флуда"""
    db = load_db()
    fam = db["families"].get(fam_id, {})
    for uid_str in fam.get("members", {}):
        try:
            await bot.send_message(
                int(uid_str),
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=get_family_menu_kb(fam.get("name", "Семья"))
            )
            await asyncio.sleep(0.05)  # Защита от флуда
        except Exception as e:
            log_error(f"Notify error for {uid_str}: {e}")

async def notify_creator(bot: Bot, fam_id: str, text: str) -> None:
    """Уведомление только создателя семьи"""
    db = load_db()
    fam = db["families"].get(fam_id, {})
    creator_id = fam.get("creator_id")
    if creator_id:
        try:
            await bot.send_message(int(creator_id), text, parse_mode=ParseMode.HTML)
        except Exception as e:
            log_error(f"Notify creator error: {e}")

# ────────────────────────────────────────────────
# Фоновый цикл напоминаний — оптимизирован
# ────────────────────────────────────────────────
async def reminders_loop(bot: Bot):
    """Оптимизированный цикл напоминаний с кэшированием ближайших дедлайнов"""
    while True:
        await asyncio.sleep(30)  # Проверяем чаще для точности
        db = load_db()
        now = time.time()
        updated = False

        for fam_id, fam in db["families"].items():
            for task_id, task in list(fam.get("tasks", {}).items()):
                # Пропускаем если напоминание уже отправлено
                if task.get("reminder_sent"):
                    continue

                # Пропускаем если нет дедлайна или напоминания
                if "deadline" not in task or task.get("reminder_sec", 0) <= 0:
                    continue

                try:
                    deadline_dt = datetime.strptime(task["deadline"], "%d.%m.%Y %H:%M")
                    seconds_to_deadline = deadline_dt.timestamp() - now

                    # Отправляем напоминание если время пришло
                    if 0 < seconds_to_deadline <= task["reminder_sec"]:
                        emoji = "🚨" if seconds_to_deadline < 3600 else "🔔"
                        text = (
                            f"{emoji} <b>Напоминание о задаче</b>\n\n"
                            f"«{task['desc']}»\n"
                            f"Дедлайн: {format_deadline(task['deadline'])}\n\n"
                            f"Семья: {fam.get('name', 'Семья')}"
                        )
                        await notify_family(bot, fam_id, text)
                        task["reminder_sent"] = True
                        updated = True

                except Exception as e:
                    log_error(f"Reminder processing error for task {task_id}: {e}")

        if updated:
            try:
                atomic_save_db(db)
            except Exception as e:
                log_error(f"Failed to save DB after reminders: {e}")

# ────────────────────────────────────────────────
# GUI — полностью переработан под 2026 UX
# ────────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("🦊 FoxFamilyTask Bot — Настройка (2026)")
        self.resize(800, 600)
        self.db = load_db()

        # Центральный виджет со стеком
        self.stacked = QStackedWidget()
        self.setCentralWidget(self.stacked)

        # Создание страниц мастера
        self.page_intro = self.create_intro_page()
        self.page_token = self.create_token_page()
        self.page_paths = self.create_paths_page()
        self.page_ready = self.create_ready_page()

        self.stacked.addWidget(self.page_intro)
        self.stacked.addWidget(self.page_token)
        self.stacked.addWidget(self.page_paths)
        self.stacked.addWidget(self.page_ready)
        self.stacked.setCurrentIndex(0)

    def create_intro_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        title = QLabel("🦊 FoxFamilyTask Bot — Семейный менеджер задач")
        title.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        desc = QTextEdit()
        desc.setReadOnly(True)
        desc.setHtml("""
            <h3>✨ Что умеет бот:</h3>
            <ul>
                <li>Создание семей с приглашениями по ключу</li>
                <li>Умные задачи с дедлайнами и напоминаниями</li>
                <li>Прогресс выполнения и совместные списки покупок</li>
                <li>Полная синхронизация через Telegram</li>
                <li>Локальный запуск — ваши данные остаются у вас</li>
            </ul>
            <p><b>Версии:</b> aiogram 3.22.0 + PyQt6 6.10.0</p>
        """)
        desc.setMaximumHeight(200)
        lay.addWidget(desc)

        next_btn = QPushButton("🚀 Начать настройку")
        next_btn.clicked.connect(lambda: self.stacked.setCurrentIndex(1))
        lay.addStretch()
        lay.addWidget(next_btn)

        return w

    def create_token_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        title = QLabel("🔑 Шаг 1: Telegram API-токен")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        lay.addWidget(title)

        help_text = QLabel(
            "Найдите @BotFather в Telegram → /newbot → следуйте инструкциям.\n"
            "Скопируйте токен вида <code>123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11</code>"
        )
        help_text.setWordWrap(True)
        lay.addWidget(help_text)

        self.token_edit = QLineEdit()
        self.token_edit.setPlaceholderText("Вставьте токен сюда...")
        self.token_edit.setText(get_telegram_token())
        lay.addWidget(self.token_edit)

        btn_layout = QHBoxLayout()
        help_btn = QPushButton("❓ Как получить токен?")
        help_btn.clicked.connect(lambda: QMessageBox.information(
            self, "Инструкция",
            "1. Откройте Telegram\n2. Найдите @BotFather\n3. Отправьте /newbot\n"
            "4. Введите имя бота (например, FoxFamilyBot)\n5. Введите username (оканчивается на bot)\n"
            "6. Скопируйте токен из сообщения BotFather"
        ))
        test_btn = QPushButton("🔍 Проверить токен")
        test_btn.clicked.connect(self.test_token)
        next_btn = QPushButton("Далее →")
        next_btn.clicked.connect(self.save_token)

        btn_layout.addWidget(help_btn)
        btn_layout.addWidget(test_btn)
        btn_layout.addWidget(next_btn)
        lay.addLayout(btn_layout)

        self.token_status = QLabel("")
        lay.addWidget(self.token_status)

        back_btn = QPushButton("← Назад")
        back_btn.clicked.connect(lambda: self.stacked.setCurrentIndex(0))
        lay.addWidget(back_btn)

        return w

    def test_token(self) -> None:
        token = self.token_edit.text().strip()
        if not token:
            self.token_status.setText("❌ Токен не введён")
            return

        import re
        if re.match(r"^\d+:[A-Za-z0-9_-]+$", token):
            self.token_status.setText("✅ Формат токена корректный")
            self.token_status.setStyleSheet("color: green;")
        else:
            self.token_status.setText("❌ Неверный формат токена")
            self.token_status.setStyleSheet("color: red;")

    def save_token(self) -> None:
        token = self.token_edit.text().strip()
        if not token:
            QMessageBox.critical(self, "Ошибка", "Введите токен!")
            return

        # Сохраняем токен в .env (безопасно!)
        try:
            with open(ENV_PATH, "w", encoding="utf-8") as f:
                f.write(f"TELEGRAM_BOT_TOKEN={token}\n")
            self.token_status.setText("✅ Токен сохранён в .env")
            self.token_status.setStyleSheet("color: green;")
            self.stacked.setCurrentIndex(2)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить токен: {e}")

    def create_paths_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        title = QLabel("📁 Шаг 2: Папки для данных")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        lay.addWidget(title)

        # Папка данных
        lay.addWidget(QLabel("Папка для базы данных и логов:"))
        self.data_edit = QLineEdit(str(Path.cwd()))
        browse_data_btn = QPushButton("📁 Выбрать...")
        browse_data_btn.clicked.connect(lambda: self.browse_folder(self.data_edit))

        data_layout = QHBoxLayout()
        data_layout.addWidget(self.data_edit)
        data_layout.addWidget(browse_data_btn)
        lay.addLayout(data_layout)

        # Папка вывода
        lay.addWidget(QLabel("Папка для временных файлов (опционально):"))
        self.output_edit = QLineEdit(str(Path.cwd() / "output"))
        browse_output_btn = QPushButton("📁 Выбрать...")
        browse_output_btn.clicked.connect(lambda: self.browse_folder(self.output_edit))

        output_layout = QHBoxLayout()
        output_layout.addWidget(self.output_edit)
        output_layout.addWidget(browse_output_btn)
        lay.addLayout(output_layout)

        # Кнопки навигации
        btn_layout = QHBoxLayout()
        back_btn = QPushButton("← Назад")
        back_btn.clicked.connect(lambda: self.stacked.setCurrentIndex(1))
        next_btn = QPushButton("Далее →")
        next_btn.clicked.connect(self.save_paths)
        btn_layout.addWidget(back_btn)
        btn_layout.addWidget(next_btn)
        lay.addLayout(btn_layout)

        return w

    def browse_folder(self, line_edit: QLineEdit) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку")
        if folder:
            line_edit.setText(folder)

    def save_paths(self) -> None:
        data_path = Path(self.data_edit.text().strip())
        output_path = Path(self.output_edit.text().strip())

        if not data_path.exists():
            try:
                data_path.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось создать папку данных: {e}")
                return

        if not output_path.exists():
            try:
                output_path.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                QMessageBox.warning(self, "Предупреждение", f"Не удалось создать папку вывода: {e}")

        # Сохраняем пути в БД
        db = load_db()
        db["data_folder"] = str(data_path)
        db["output_base"] = str(output_path)
        try:
            atomic_save_db(db)
            self.stacked.setCurrentIndex(3)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить настройки: {e}")

    def create_ready_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        title = QLabel("✅ Настройка завершена!")
        title.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        info = QTextEdit()
        info.setReadOnly(True)
        info.setHtml("""
            <h3>Готово к запуску!</h3>
            <ul>
                <li>Токен сохранён в файле <code>.env</code> (безопасно!)</li>
                <li>Данные будут храниться в: <b>foxfamily_db.json</b></li>
                <li>Логи записываются в: <b>foxfamily.log</b></li>
            </ul>
            <p><b>Важно:</b> Не передавайте файл <code>.env</code> другим людям!</p>
        """)
        lay.addWidget(info)

        launch_btn = QPushButton("🚀 Запустить бота")
        launch_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 10px;")
        launch_btn.clicked.connect(self.launch_bot)
        lay.addWidget(launch_btn)

        status_label = QLabel("Статус: бот не запущен")
        status_label.setStyleSheet("font-weight: bold; color: #666;")
        lay.addWidget(status_label)
        self.status_label = status_label

        return w

    def launch_bot(self) -> None:
        token = get_telegram_token()
        if not token:
            QMessageBox.critical(self, "Ошибка", "Токен не найден в .env!")
            return

        self.bot_thread = BotThread(token)
        self.bot_thread.status_updated.connect(self.update_status)
        self.bot_thread.start()
        self.status_label.setText("🔄 Бот запускается...")
        log_info("Bot launch initiated via GUI")

    def update_status(self, msg: str) -> None:
        self.status_label.setText(f"📡 {msg}")
        if "ошибка" in msg.lower() or "error" in msg.lower():
            self.status_label.setStyleSheet("color: red;")
        elif "запущен" in msg.lower() or "polling" in msg.lower():
            self.status_label.setStyleSheet("color: green;")

# ────────────────────────────────────────────────
# BotThread — корректная интеграция asyncio + PyQt6
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
            self.status_updated.emit("Инициализация бота...")
            loop.run_until_complete(start_bot(self.token, self.status_updated))
        except Exception as e:
            log_error(f"BotThread fatal error: {e}")
            self.status_updated.emit(f"❌ Критическая ошибка: {str(e)}")
        finally:
            loop.close()

# ────────────────────────────────────────────────
# Telegram Bot Logic — полностью переработанная архитектура диалогов
# ────────────────────────────────────────────────
async def start_bot(token: str, status_signal: pyqtSignal) -> None:
    bot = Bot(token=token)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    status_signal.emit("Бот запущен. Ожидание команд...")

    # ─── ГЛОБАЛЬНЫЕ КОМАНДЫ ────────────────────────────────────────────
    @dp.message(Command("start"))
    async def cmd_start(message: Message, state: FSMContext) -> None:
        await state.clear()
        db = load_db()
        uid = str(message.from_user.id)

        # Инициализация пользователя если новый
        if uid not in db["users"]:
            db["users"][uid] = {
                "families": [],
                "current_family": "",
                "settings": {"timezone": "UTC"}
            }
            atomic_save_db(db)

        user = db["users"][uid]
        current_fam_id = user["current_family"]

        if current_fam_id and current_fam_id in db["families"]:
            # Пользователь внутри семьи — показываем меню семьи
            fam = db["families"][current_fam_id]
            await message.answer(
                f"🦊 Добро пожаловать в семью «{fam['name']}»!",
                reply_markup=get_family_menu_kb(fam["name"])
            )
        else:
            # Пользователь вне семьи — главное меню
            await message.answer(
                "🏠 <b>Главное меню</b>\n\n"
                "Выберите действие для управления семьями:",
                reply_markup=get_main_menu_kb(),
                parse_mode=ParseMode.HTML
            )

    @dp.message(Command("cancel"))
    @dp.message(F.text == "❌ Отмена")
    async def cmd_cancel(message: Message, state: FSMContext) -> None:
        current_state = await state.get_state()
        if current_state is None:
            await message.answer("Нет активных операций для отмены.", reply_markup=ReplyKeyboardRemove())
            return

        await state.clear()
        db = load_db()
        uid = str(message.from_user.id)
        user = db["users"].get(uid, {})
        current_fam_id = user.get("current_family")

        if current_fam_id and current_fam_id in db["families"]:
            fam = db["families"][current_fam_id]
            await message.answer(
                "❌ Операция отменена. Возврат в меню семьи.",
                reply_markup=get_family_menu_kb(fam["name"])
            )
        else:
            await message.answer(
                "❌ Операция отменена. Возврат в главное меню.",
                reply_markup=get_main_menu_kb()
            )

    # ─── ГЛАВНОЕ МЕНЮ (вне семьи) ───────────────────────────────────────
    @dp.message(F.text == "📋 Мои семьи")
    async def my_families(message: Message, state: FSMContext) -> None:
        await state.clear()
        db = load_db()
        uid = str(message.from_user.id)
        user = db["users"].get(uid, {"families": []})

        if not user["families"]:
            await message.answer(
                "📭 У вас пока нет семей.\n"
                "Создайте новую или присоединитесь по ключу!",
                reply_markup=get_main_menu_kb()
            )
            return

        # Формируем список семей
        text = "🏠 <b>Ваши семьи:</b>\n\n"
        builder = InlineKeyboardBuilder()

        for idx, fam_id in enumerate(user["families"], 1):
            fam = db["families"].get(fam_id, {})
            name = fam.get("name", "Без названия")
            members_count = len(fam.get("members", {}))
            is_current = fam_id == user.get("current_family")

            prefix = "✅ " if is_current else f"{idx}. "
            text += f"{prefix}{name} ({members_count} участников)\n"
            builder.button(text=f"→ {name}", callback_data=f"enter_family:{fam_id}")

        builder.adjust(1)
        builder.row(InlineKeyboardButton(text="➕ Создать новую", callback_data="create_family"))

        await message.answer(
            text,
            reply_markup=builder.as_markup(),
            parse_mode=ParseMode.HTML
        )

    @dp.callback_query(F.data.startswith("enter_family:"))
    async def enter_family(cq: CallbackQuery, state: FSMContext) -> None:
        fam_id = cq.data.split(":")[1]
        db = load_db()
        uid = str(cq.from_user.id)
        user = db["users"].get(uid, {})

        if fam_id not in user.get("families", []):
            await cq.answer("❌ Вы не состоите в этой семье!", show_alert=True)
            return

        # Переключаем текущую семью
        user["current_family"] = fam_id
        atomic_save_db(db)

        fam = db["families"][fam_id]
        await cq.message.edit_text(
            f"✅ Вы вошли в семью «{fam['name']}»",
            reply_markup=None
        )
        await cq.message.answer(
            f"🏡 <b>{fam['name']}</b>\n\n"
            f"Участников: {len(fam['members'])}\n"
            f"Активных задач: {len(fam.get('tasks', {}))}",
            reply_markup=get_family_menu_kb(fam["name"]),
            parse_mode=ParseMode.HTML
        )
        await cq.answer()

    @dp.callback_query(F.data == "create_family")
    async def create_family_callback(cq: CallbackQuery, state: FSMContext) -> None:
        db = load_db()
        uid = str(cq.from_user.id)

        # Создаём новую семью
        fam_id = str(uuid.uuid4())
        key_data = generate_family_key()
        db["families"][fam_id] = {
            "name": "🦊 Моя семья",
            "created_at": time.time(),
            "creator_id": uid,
            "members": {uid: {"nick": cq.from_user.first_name or "Участник", "joined": time.time()}},
            "active_key": key_data,
            "tasks": {},
            "completed_tasks": {},
        }

        # Добавляем семью пользователю
        user = db["users"].setdefault(uid, {"families": [], "current_family": "", "settings": {"timezone": "UTC"}})
        user["families"].append(fam_id)
        user["current_family"] = fam_id

        atomic_save_db(db)

        # Отправляем приглашение
        await cq.message.edit_text(
            f"✅ Семья «{db['families'][fam_id]['name']}» создана!\n\n"
            f"🔑 <b>Ключ приглашения</b> (действует 10 минут):\n"
            f"<code>{key_data['value']}</code>\n\n"
            "Поделитесь этим ключом с членами семьи!",
            parse_mode=ParseMode.HTML,
            reply_markup=get_family_menu_kb(db['families'][fam_id]['name'])
        )
        await cq.answer("Семья создана!")

    @dp.message(F.text == "➕ Создать семью")
    async def create_family_handler(message: Message, state: FSMContext) -> None:
        db = load_db()
        uid = str(message.from_user.id)

        # Создаём новую семью
        fam_id = str(uuid.uuid4())
        key_data = generate_family_key()
        db["families"][fam_id] = {
            "name": "🦊 Моя семья",
            "created_at": time.time(),
            "creator_id": uid,
            "members": {uid: {"nick": message.from_user.first_name or "Участник", "joined": time.time()}},
            "active_key": key_data,
            "tasks": {},
            "completed_tasks": {},
        }

        # Добавляем семью пользователю
        user = db["users"].setdefault(uid, {"families": [], "current_family": "", "settings": {"timezone": "UTC"}})
        user["families"].append(fam_id)
        user["current_family"] = fam_id

        atomic_save_db(db)

        # Отправляем приглашение
        await message.answer(
            f"✅ Семья «{db['families'][fam_id]['name']}» создана!\n\n"
            f"🔑 <b>Ключ приглашения</b> (действует 10 минут):\n"
            f"<code>{key_data['value']}</code>\n\n"
            "Поделитесь этим ключом с членами семьи!",
            parse_mode=ParseMode.HTML,
            reply_markup=get_family_menu_kb(db['families'][fam_id]['name'])
        )

    @dp.message(F.text == "🔑 Присоединиться")
    async def join_family(message: Message, state: FSMContext) -> None:
        await state.set_state(GlobalStates.join_key)
        await message.answer(
            "🔑 Введите ключ приглашения для присоединения к семье:",
            reply_markup=get_cancel_kb()
        )

    @dp.message(GlobalStates.join_key)
    async def join_key_handler(message: Message, state: FSMContext) -> None:
        if message.text == "❌ Отмена":
            await cmd_cancel(message, state)
            return

        key_input = message.text.strip()
        db = load_db()
        uid = str(message.from_user.id)
        found_family = None

        # Поиск семьи по ключу
        for fam_id, fam in db["families"].items():
            if is_key_valid(key_input, fam):
                found_family = fam_id
                break

        if not found_family:
            await message.answer(
                "❌ Неверный или истёкший ключ.\nПопробуйте снова или запросите новый у создателя семьи.",
                reply_markup=get_cancel_kb()
            )
            return

        # Проверка лимита участников
        fam = db["families"][found_family]
        if len(fam["members"]) >= MAX_FREE_MEMBERS and fam.get("subscription") is None:
            await message.answer(
                f"🚫 Семья достигла лимита ({MAX_FREE_MEMBERS} участников).\n"
                "Для увеличения лимита требуется подписка.",
                reply_markup=get_main_menu_kb()
            )
            await state.clear()
            return

        if len(fam["members"]) >= WARN_MEMBERS_THRESHOLD:
            await message.answer(
                f"⚠️ В семье уже {len(fam['members'])} участников.\n"
                f"Бесплатный лимит: {MAX_FREE_MEMBERS} человек."
            )

        # Проверка уникальности ника
        base_nick = message.from_user.first_name or "Участник"
        nick = base_nick
        counter = 1
        while any(m["nick"] == nick for m in fam["members"].values()):
            nick = f"{base_nick}_{counter}"
            counter += 1

        # Сохраняем данные для следующего шага
        await state.update_data(fam_id=found_family, suggested_nick=nick)
        await state.set_state(GlobalStates.join_nick)
        await message.answer(
            f"✏️ Введите ваш никнейм в семье:\n"
            f"(предложено: <code>{nick}</code>)",
            parse_mode=ParseMode.HTML,
            reply_markup=get_cancel_kb()
        )

    @dp.message(GlobalStates.join_nick)
    async def join_nick_handler(message: Message, state: FSMContext) -> None:
        if message.text == "❌ Отмена":
            await cmd_cancel(message, state)
            return

        nick = message.text.strip()[:32]  # Ограничение длины
        if not nick:
            await message.answer("❌ Никнейм не может быть пустым. Попробуйте снова:", reply_markup=get_cancel_kb())
            return

        data = await state.get_data()
        fam_id = data.get("fam_id")
        if not fam_id:
            await message.answer("❌ Ошибка состояния. Начните заново.", reply_markup=get_main_menu_kb())
            await state.clear()
            return

        db = load_db()
        fam = db["families"].get(fam_id)
        if not fam:
            await message.answer("❌ Семья не найдена. Ключ мог истечь.", reply_markup=get_main_menu_kb())
            await state.clear()
            return

        # Проверка уникальности ника
        if any(m["nick"] == nick for m in fam["members"].values()):
            await message.answer(
                f"❌ Ник «{nick}» уже занят. Выберите другой:",
                reply_markup=get_cancel_kb()
            )
            return

        uid = str(message.from_user.id)
        fam["members"][uid] = {"nick": nick, "joined": time.time()}

        # Добавляем семью пользователю
        user = db["users"].setdefault(uid, {"families": [], "current_family": "", "settings": {"timezone": "UTC"}})
        if fam_id not in user["families"]:
            user["families"].append(fam_id)
        user["current_family"] = fam_id

        # Генерируем новый ключ для будущих приглашений
        fam["active_key"] = generate_family_key()
        atomic_save_db(db)

        # Уведомляем семью
        await notify_family(
            message.bot,
            fam_id,
            f"🎉 <b>{nick}</b> присоединился к семье «{fam['name']}»!"
        )

        await message.answer(
            f"✅ Добро пожаловать в семью «{fam['name']}»!\n\n"
            f"Ваш ник: <b>{nick}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=get_family_menu_kb(fam["name"])
        )
        await state.clear()

    @dp.message(F.text == "⚙️ Настройки")
    async def global_settings(message: Message, state: FSMContext) -> None:
        db = load_db()
        uid = str(message.from_user.id)
        tz = db["users"].get(uid, {}).get("settings", {}).get("timezone", "UTC")

        text = (
            "⚙️ <b>Настройки</b>\n\n"
            f"Часовой пояс: <code>{tz}</code> (серверное время)\n"
            "ℹ️ В 2026 году бот работает в часовом поясе сервера (UTC).\n"
            "Для персонализации времени требуется облачная синхронизация."
        )
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=get_main_menu_kb())

    @dp.message(F.text == "❓ Помощь")
    async def help_handler(message: Message, state: FSMContext) -> None:
        text = (
            "❓ <b>Помощь по FoxFamilyTask</b>\n\n"
            "🏠 <b>Главное меню</b>\n"
            "• 📋 Мои семьи — список и переключение\n"
            "• ➕ Создать — новая семья с ключом приглашения\n"
            "• 🔑 Присоединиться — по ключу от создателя\n\n"
            "🏡 <b>Меню семьи</b>\n"
            "• 📋 Задачи — просмотр и обновление прогресса\n"
            "• ➕ Новая задача — с дедлайнами и напоминаниями\n"
            "• 👥 Участники — управление членами семьи\n"
            "• ⚙️ Настройки — только для создателя\n"
            "• 🏠 Выйти — возврат в главное меню\n\n"
            "💡 Совет: Используйте /cancel для отмены любой операции"
        )
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=get_main_menu_kb())

    # ─── МЕНЮ СЕМЬИ ────────────────────────────────────────────────────
    @dp.message(F.text == "🏠 Выйти из семьи")
    async def leave_family_menu(message: Message, state: FSMContext) -> None:
        db = load_db()
        uid = str(message.from_user.id)
        user = db["users"].get(uid, {})
        fam_id = user.get("current_family")

        if not fam_id or fam_id not in db["families"]:
            await message.answer("❌ Вы не в семье!", reply_markup=get_main_menu_kb())
            return

        fam = db["families"][fam_id]
        await message.answer(
            f"❓ Вы уверены, что хотите выйти из семьи «{fam['name']}»?\n\n"
            "Ваши задачи и прогресс останутся, но вы перестанете получать уведомления.",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="✅ Да, выйти")],
                    [KeyboardButton(text="❌ Нет, остаться")]
                ],
                resize_keyboard=True
            )
        )
        await state.set_state(FamilyStates.leave_family_confirm)

    @dp.message(FamilyStates.leave_family_confirm)
    async def leave_family_confirm(message: Message, state: FSMContext) -> None:
        if message.text == "✅ Да, выйти":
            db = load_db()
            uid = str(message.from_user.id)
            user = db["users"].get(uid, {})
            fam_id = user.get("current_family")

            if fam_id and fam_id in db["families"]:
                fam = db["families"][fam_id]
                # Удаляем пользователя из семьи
                fam["members"].pop(uid, None)
                # Удаляем семью из списка пользователя
                if fam_id in user["families"]:
                    user["families"].remove(fam_id)
                user["current_family"] = ""

                # Если семья осталась без участников — удаляем её
                if not fam["members"]:
                    db["families"].pop(fam_id, None)
                    await notify_creator(
                        message.bot,
                        fam_id,
                        f"⚠️ Семья «{fam['name']}» удалена (последний участник вышел)."
                    )
                else:
                    await notify_family(
                        message.bot,
                        fam_id,
                        f"🚪 Участник {fam['members'].get(uid, {}).get('nick', '???')} покинул семью."
                    )

                atomic_save_db(db)
                await message.answer(
                    "✅ Вы вышли из семьи.\nВозврат в главное меню:",
                    reply_markup=get_main_menu_kb()
                )
            else:
                await message.answer("❌ Ошибка: семья не найдена.", reply_markup=get_main_menu_kb())
        else:
            db = load_db()
            fam_id = db["users"][str(message.from_user.id)].get("current_family")
            fam_name = db["families"].get(fam_id, {}).get("name", "Семья")
            await message.answer("↩️ Вы остались в семье.", reply_markup=get_family_menu_kb(fam_name))

        await state.clear()

    @dp.message(F.text.startswith("🏡 "))
    async def family_overview(message: Message, state: FSMContext) -> None:
        db = load_db()
        uid = str(message.from_user.id)
        fam_id = db["users"].get(uid, {}).get("current_family")

        if not fam_id or fam_id not in db["families"]:
            await message.answer("❌ Вы не в семье! Возврат в главное меню.", reply_markup=get_main_menu_kb())
            return

        fam = db["families"][fam_id]
        members_list = "\n".join(
            f"• {m['nick']} (с {datetime.fromtimestamp(m['joined']).strftime('%d.%m')})"
            for m in fam["members"].values()
        )

        await message.answer(
            f"🏡 <b>{fam['name']}</b>\n\n"
            f"👥 Участники ({len(fam['members'])}):\n{members_list}\n\n"
            f"✅ Завершённые задачи: {len(fam.get('completed_tasks', {}))}",
            parse_mode=ParseMode.HTML,
            reply_markup=get_family_menu_kb(fam["name"])
        )

    @dp.message(F.text == "👥 Участники")
    async def family_members(message: Message, state: FSMContext) -> None:
        db = load_db()
        uid = str(message.from_user.id)
        fam_id = db["users"].get(uid, {}).get("current_family")

        if not fam_id or fam_id not in db["families"]:
            await message.answer("❌ Вы не в семье!", reply_markup=get_main_menu_kb())
            return

        fam = db["families"][fam_id]
        creator_id = fam.get("creator_id")
        is_creator = (uid == creator_id)

        # Формируем список участников
        members_text = "👥 <b>Участники семьи:</b>\n\n"
        for member_id, member in fam["members"].items():
            nick = member["nick"]
            joined = datetime.fromtimestamp(member["joined"]).strftime("%d.%m.%Y")
            role = "👑 Создатель" if member_id == creator_id else "👤 Участник"
            you = " ← вы" if member_id == uid else ""
            members_text += f"• {nick} ({role}, с {joined}){you}\n"

        if is_creator:
            key_str = fam.get("active_key", {}).get("value", "ключ не сгенерирован")
            members_text += f"\n🔑 <b>Текущий ключ приглашения:</b>\n<code>{key_str}</code>\n(действует 10 минут)"

        await message.answer(
            members_text,
            parse_mode=ParseMode.HTML,
            reply_markup=get_family_menu_kb(fam["name"])
        )

    @dp.message(F.text == "⚙️ Настройки семьи")
    async def family_settings(message: Message, state: FSMContext) -> None:
        db = load_db()
        uid = str(message.from_user.id)
        fam_id = db["users"].get(uid, {}).get("current_family")

        if not fam_id or fam_id not in db["families"]:
            await message.answer("❌ Вы не в семье!", reply_markup=get_main_menu_kb())
            return

        fam = db["families"][fam_id]
        if fam.get("creator_id") != uid:
            await message.answer(
                "❌ Только создатель семьи может изменять настройки.",
                reply_markup=get_family_menu_kb(fam["name"])
            )
            return

        builder = InlineKeyboardBuilder()
        builder.button(text="✏️ Изменить название", callback_data="fam_settings:name")
        builder.button(text="🔑 Новый ключ приглашения", callback_data="fam_settings:new_key")
        builder.button(text="🏆 Подписка", callback_data="fam_settings:subscription")
        builder.button(text="🗑️ Удалить семью", callback_data="fam_settings:delete")
        builder.adjust(1)

        await message.answer(
            f"⚙️ <b>Настройки семьи «{fam['name']}»</b>\n\n"
            f"Участников: {len(fam['members'])}/{MAX_FREE_MEMBERS} (бесплатно)\n"
            f"Задач создано: {len(fam.get('tasks', {})) + len(fam.get('completed_tasks', {}))}",
            reply_markup=builder.as_markup(),
            parse_mode=ParseMode.HTML
        )

    @dp.callback_query(F.data == "fam_settings:name")
    async def change_name_start(cq: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(FamilyStates.change_name)
        await cq.message.answer("✏️ Введите новое название семьи (до 50 символов):", reply_markup=get_cancel_kb())
        await cq.answer()

    @dp.message(FamilyStates.change_name)
    async def change_name_handler(message: Message, state: FSMContext) -> None:
        if message.text == "❌ Отмена":
            await cmd_cancel(message, state)
            return

        new_name = message.text.strip()[:50]
        if not new_name:
            await message.answer("❌ Название не может быть пустым. Попробуйте снова:", reply_markup=get_cancel_kb())
            return

        db = load_db()
        uid = str(message.from_user.id)
        fam_id = db["users"].get(uid, {}).get("current_family")

        if not fam_id or fam_id not in db["families"]:
            await message.answer("❌ Ошибка: семья не найдена.", reply_markup=get_main_menu_kb())
            await state.clear()
            return

        db["families"][fam_id]["name"] = new_name
        atomic_save_db(db)

        await notify_family(
            message.bot,
            fam_id,
            f"🏷️ Название семьи изменено на «{new_name}»"
        )
        await message.answer(
            f"✅ Название изменено на «{new_name}»",
            reply_markup=get_family_menu_kb(new_name)
        )
        await state.clear()

    @dp.callback_query(F.data == "fam_settings:new_key")
    async def generate_new_key(cq: CallbackQuery, state: FSMContext) -> None:
        db = load_db()
        uid = str(cq.from_user.id)
        fam_id = db["users"].get(uid, {}).get("current_family")

        if not fam_id or fam_id not in db["families"] or db["families"][fam_id].get("creator_id") != uid:
            await cq.answer("❌ Только создатель может генерировать ключи!", show_alert=True)
            return

        # Генерируем новый ключ
        new_key = generate_family_key()
        db["families"][fam_id]["active_key"] = new_key
        atomic_save_db(db)

        await cq.message.edit_text(
            f"✅ Новый ключ приглашения сгенерирован!\n\n"
            f"🔑 <code>{new_key['value']}</code>\n"
            f"Действует 10 минут.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад к настройкам", callback_data="fam_settings:back")]
            ])
        )
        await cq.answer("Ключ обновлён!")

    @dp.callback_query(F.data == "fam_settings:subscription")
    async def subscription_info(cq: CallbackQuery, state: FSMContext) -> None:
        text = (
            "🏆 <b>Подписка FoxFamily Pro</b>\n\n"
            "Расширяет возможности семьи:\n"
            "• До 50 участников — 100 ⭐/мес\n"
            "• До 75 участников — 200 ⭐/мес\n"
            "• До 100 участников — 350 ⭐/мес\n"
            "• Приоритетная поддержка\n"
            "• Облачная синхронизация\n\n"
            "ℹ️ Оплата через Telegram Stars. Для активации обратитесь к @FoxFamilySupport"
        )
        await cq.message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="fam_settings:back")]
            ])
        )
        await cq.answer()

    @dp.callback_query(F.data == "fam_settings:delete")
    async def delete_family_confirm(cq: CallbackQuery, state: FSMContext) -> None:
        await cq.message.edit_text(
            "⚠️ <b>Внимание!</b>\n\n"
            "Удаление семьи приведёт к:\n"
            "• Удалению всех задач и прогресса\n"
            "• Удалению всех участников\n"
            "• Безвозвратной потере данных\n\n"
            "Вы уверены?",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Да, удалить", callback_data="fam_settings:delete_confirm")],
                [InlineKeyboardButton(text="❌ Нет, отмена", callback_data="fam_settings:back")]
            ])
        )
        await cq.answer()

    @dp.callback_query(F.data == "fam_settings:delete_confirm")
    async def delete_family(cq: CallbackQuery, state: FSMContext) -> None:
        db = load_db()
        uid = str(cq.from_user.id)
        fam_id = db["users"].get(uid, {}).get("current_family")

        if not fam_id or fam_id not in db["families"] or db["families"][fam_id].get("creator_id") != uid:
            await cq.answer("❌ Ошибка доступа!", show_alert=True)
            return

        fam_name = db["families"][fam_id]["name"]
        # Удаляем семью
        del db["families"][fam_id]
        # Удаляем семью из всех пользователей
        for user in db["users"].values():
            if fam_id in user.get("families", []):
                user["families"].remove(fam_id)
            if user.get("current_family") == fam_id:
                user["current_family"] = ""

        atomic_save_db(db)

        await cq.message.edit_text(
            f"✅ Семья «{fam_name}» удалена.\nВозврат в главное меню:",
            reply_markup=None
        )
        await cq.message.answer("🏠 Главное меню:", reply_markup=get_main_menu_kb())
        await cq.answer("Семья удалена!")

    @dp.callback_query(F.data == "fam_settings:back")
    async def settings_back(cq: CallbackQuery, state: FSMContext) -> None:
        db = load_db()
        uid = str(cq.from_user.id)
        fam_id = db["users"].get(uid, {}).get("current_family")
        fam = db["families"].get(fam_id, {})

        builder = InlineKeyboardBuilder()
        builder.button(text="✏️ Изменить название", callback_data="fam_settings:name")
        builder.button(text="🔑 Новый ключ приглашения", callback_data="fam_settings:new_key")
        builder.button(text="🏆 Подписка", callback_data="fam_settings:subscription")
        builder.button(text="🗑️ Удалить семью", callback_data="fam_settings:delete")
        builder.adjust(1)

        await cq.message.edit_text(
            f"⚙️ <b>Настройки семьи «{fam.get('name', 'Семья')}»</b>",
            reply_markup=builder.as_markup(),
            parse_mode=ParseMode.HTML
        )
        await cq.answer()

    # ─── ЗАДАЧИ ────────────────────────────────────────────────────────
    @dp.message(F.text == "📋 Задачи")
    async def tasks_list(message: Message, state: FSMContext) -> None:
        db = load_db()
        uid = str(message.from_user.id)
        fam_id = db["users"].get(uid, {}).get("current_family")

        if not fam_id or fam_id not in db["families"]:
            await message.answer("❌ Вы не в семье!", reply_markup=get_main_menu_kb())
            return

        fam = db["families"][fam_id]
        tasks = fam.get("tasks", {})

        if not tasks:
            await message.answer(
                "📭 Нет активных задач.\nСоздайте первую с помощью ➕ Новая задача",
                reply_markup=get_family_menu_kb(fam["name"])
            )
            return

        # Сортируем задачи по приближению дедлайна
        sorted_tasks = sorted(
            tasks.items(),
            key=lambda x: datetime.strptime(x[1]["deadline"], "%d.%m.%Y %H:%M").timestamp()
            if "deadline" in x[1] else float('inf')
        )

        text = "📋 <b>Активные задачи:</b>\n\n"
        builder = InlineKeyboardBuilder()

        for idx, (task_id, task) in enumerate(sorted_tasks, 1):
            deadline_str = format_deadline(task["deadline"]) if "deadline" in task else "⏱️ Без дедлайна"
            bar = progress_bar(task.get("progress", 0))
            assignees = ", ".join(task.get("assignees", [])) or "не назначена"

            text += (
                f"{idx}. {task['desc']}\n"
                f"   {bar} | {deadline_str}\n"
                f"   Исполнители: {assignees}\n\n"
            )
            builder.button(text=f"✏️ {idx}. {task['desc'][:20]}...", callback_data=f"task:edit:{task_id}")

        builder.adjust(1)
        builder.row(InlineKeyboardButton(text="✅ Завершённые задачи", callback_data="tasks:completed"))
        builder.row(InlineKeyboardButton(text="➕ Новая задача", callback_data="tasks:new"))

        await message.answer(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=builder.as_markup()
        )

    @dp.callback_query(F.data == "tasks:new")
    async def new_task_start(cq: CallbackQuery, state: FSMContext) -> None:
        builder = InlineKeyboardBuilder()
        for display, value in TASK_TYPES.items():
            builder.button(text=display, callback_data=f"task_type:{value}")
        builder.adjust(2)

        await cq.message.answer(
            "📝 <b>Выберите тип задачи:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=builder.as_markup()
        )
        await cq.answer()
        await state.set_state(FamilyStates.create_task_type)

    @dp.callback_query(F.data.startswith("task_type:"))
    async def task_type_selected(cq: CallbackQuery, state: FSMContext) -> None:
        task_type = cq.data.split(":")[1]
        display_type = next((k for k, v in TASK_TYPES.items() if v == task_type), "Обычная")
        await state.update_data(task_type=task_type, display_type=display_type)
        await state.set_state(FamilyStates.create_task_desc)
        await cq.message.answer(
            f"✏️ Опишите задачу ({display_type}):\n\n"
            "Пример: «Купить продукты к ужину»",
            reply_markup=get_cancel_kb()
        )
        await cq.answer()

    @dp.message(FamilyStates.create_task_desc)
    async def task_desc_handler(message: Message, state: FSMContext) -> None:
        if message.text == "❌ Отмена":
            await cmd_cancel(message, state)
            return

        desc = message.text.strip()
        if not desc or len(desc) > 200:
            await message.answer(
                "❌ Описание должно быть от 1 до 200 символов. Попробуйте снова:",
                reply_markup=get_cancel_kb()
            )
            return

        await state.update_data(desc=desc)
        await state.set_state(FamilyStates.create_task_deadline)
        await message.answer(
            "⏰ Укажите дедлайн в формате:\n<b>ДД.ММ.ГГГГ ЧЧ:ММ</b>\n\n"
            "Пример: <code>05.02.2026 18:30</code>\n"
            "Или напишите «без дедлайна»",
            parse_mode=ParseMode.HTML,
            reply_markup=get_cancel_kb()
        )

    @dp.message(FamilyStates.create_task_deadline)
    async def task_deadline_handler(message: Message, state: FSMContext) -> None:
        if message.text == "❌ Отмена":
            await cmd_cancel(message, state)
            return

        deadline_input = message.text.strip().lower()
        data = await state.get_data()
        task_type = data["task_type"]

        if deadline_input in ["без дедлайна", "нет", "без"]:
            await state.update_data(deadline=None)
        else:
            try:
                # Поддержка форматов: "05.02.2026 18:30" и "05.02 18:30"
                if len(deadline_input) == 16 and deadline_input[2] == '.' and deadline_input[5] == ' ':
                    # Формат ДД.ММ ЧЧ:ММ — добавляем текущий год
                    today = datetime.now()
                    deadline_input = f"{deadline_input[:5]}.{today.year} {deadline_input[6:]}"

                deadline_dt = datetime.strptime(deadline_input, "%d.%m.%Y %H:%M")
                if deadline_dt < datetime.now() - timedelta(hours=1):
                    await message.answer(
                        "❌ Дедлайн не может быть в прошлом. Укажите будущее время:",
                        reply_markup=get_cancel_kb()
                    )
                    return
                await state.update_data(deadline=deadline_dt.strftime("%d.%m.%Y %H:%M"))
            except ValueError:
                await message.answer(
                    "❌ Неверный формат. Пример: <code>05.02.2026 18:30</code>\nПопробуйте снова:",
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_cancel_kb()
                )
                return

        # Для списка покупок сразу запрашиваем элементы
        if task_type == "shopping":
            await state.set_state(FamilyStates.create_task_items)
            await message.answer(
                "🛒 Введите список покупок (по одной на строку):\n\n"
                "Пример:\nМолоко\nХлеб\nЯйца",
                reply_markup=get_cancel_kb()
            )
        else:
            # Выбор напоминания
            builder = InlineKeyboardBuilder()
            for display, seconds in REMINDER_OPTIONS.items():
                builder.button(text=display, callback_data=f"reminder:{seconds}")
            builder.adjust(2)

            await state.set_state(FamilyStates.create_task_reminder)
            await message.answer(
                "🔔 Нужно ли напомнить о задаче заранее?",
                reply_markup=builder.as_markup()
            )

    @dp.callback_query(F.data.startswith("reminder:"))
    async def reminder_selected(cq: CallbackQuery, state: FSMContext) -> None:
        seconds = int(cq.data.split(":")[1])
        await state.update_data(reminder_sec=seconds)
        await create_task_finish(cq.message, state, cq.from_user.id)
        await cq.answer()

    @dp.message(FamilyStates.create_task_items)
    async def task_items_handler(message: Message, state: FSMContext) -> None:
        if message.text == "❌ Отмена":
            await cmd_cancel(message, state)
            return

        items_raw = message.text.strip().split("\n")
        items = [i.strip() for i in items_raw if i.strip()]

        if not items:
            await message.answer("❌ Список не может быть пустым. Введите хотя бы один элемент:", reply_markup=get_cancel_kb())
            return

        if len(items) > 50:
            await message.answer("❌ Слишком много элементов (макс. 50). Сократите список:", reply_markup=get_cancel_kb())
            return

        await state.update_data(items=items)
        # Для списка покупок напоминание не требуется — сразу завершаем
        await create_task_finish(message, state, message.from_user.id)

    async def create_task_finish(message: Message, state: FSMContext, user_id: int) -> None:
        data = await state.get_data()
        db = load_db()
        uid = str(user_id)
        fam_id = db["users"].get(uid, {}).get("current_family")

        if not fam_id or fam_id not in db["families"]:
            await message.answer("❌ Ошибка: не удалось определить семью.", reply_markup=get_main_menu_kb())
            await state.clear()
            return

        fam = db["families"][fam_id]
        task_id = str(uuid.uuid4())
        nick = fam["members"].get(uid, {}).get("nick", "Участник")

        task = {
            "creator_id": uid,
            "creator_nick": nick,
            "desc": data["desc"],
            "type": data["task_type"],
            "display_type": data.get("display_type", "Обычная"),
            "deadline": data.get("deadline"),
            "reminder_sec": data.get("reminder_sec", 0),
            "progress": 0,
            "assignees": [nick],
            "updates": [],
            "items": data.get("items", []),
            "items_checked": [False] * len(data.get("items", [])),
            "created_at": time.time(),
            "reminder_sent": False,
        }

        fam.setdefault("tasks", {})[task_id] = task
        atomic_save_db(db)

        # Формируем уведомление
        deadline_str = format_deadline(task["deadline"]) if task.get("deadline") else "⏱️ Без дедлайна"
        reminder_str = f"\n🔔 Напоминание: за {list(REMINDER_OPTIONS.keys())[list(REMINDER_OPTIONS.values()).index(task['reminder_sec'])]}" if task["reminder_sec"] > 0 else ""

        notification = (
            f"🆕 <b>Новая задача в семье «{fam['name']}»</b>\n\n"
            f"«{task['desc']}» ({task['display_type']})\n"
            f"{deadline_str}{reminder_str}\n"
            f"Исполнитель: {nick}"
        )

        await notify_family(message.bot, fam_id, notification)
        await message.answer(
            "✅ Задача создана!",
            reply_markup=get_family_menu_kb(fam["name"])
        )
        await state.clear()

    @dp.callback_query(F.data.startswith("task:edit:"))
    async def edit_task(cq: CallbackQuery, state: FSMContext) -> None:
        task_id = cq.data.split(":")[2]
        db = load_db()
        uid = str(cq.from_user.id)
        fam_id = db["users"].get(uid, {}).get("current_family")

        if not fam_id or fam_id not in db["families"]:
            await cq.answer("❌ Ошибка доступа!", show_alert=True)
            return

        fam = db["families"][fam_id]
        task = fam.get("tasks", {}).get(task_id)
        if not task:
            await cq.answer("❌ Задача не найдена!", show_alert=True)
            return

        # Меню действий с задачей
        builder = InlineKeyboardBuilder()
        builder.button(text="📈 Обновить прогресс", callback_data=f"task:progress:{task_id}")
        if task["type"] == "shopping":
            builder.button(text="🛒 Список покупок", callback_data=f"task:items:{task_id}")
        builder.button(text="✅ Завершить задачу", callback_data=f"task:complete:{task_id}")
        builder.button(text="⬅️ Назад к задачам", callback_data="tasks:list")
        builder.adjust(1)

        deadline_str = format_deadline(task["deadline"]) if task.get("deadline") else "⏱️ Без дедлайна"
        bar = progress_bar(task["progress"])

        await cq.message.edit_text(
            f"📝 <b>{task['desc']}</b> ({task['display_type']})\n\n"
            f"Прогресс: {bar}\n"
            f"Дедлайн: {deadline_str}\n"
            f"Исполнители: {', '.join(task['assignees'])}",
            parse_mode=ParseMode.HTML,
            reply_markup=builder.as_markup()
        )
        await cq.answer()

    @dp.callback_query(F.data.startswith("task:progress:"))
    async def update_progress_start(cq: CallbackQuery, state: FSMContext) -> None:
        task_id = cq.data.split(":")[2]
        await state.update_data(task_id=task_id)
        await state.set_state(FamilyStates.update_task_progress)

        await cq.message.answer(
            "📈 Введите новый прогресс в процентах (0-100):",
            reply_markup=get_cancel_kb()
        )
        await cq.answer()

    @dp.message(FamilyStates.update_task_progress)
    async def update_progress_handler(message: Message, state: FSMContext) -> None:
        if message.text == "❌ Отмена":
            await cmd_cancel(message, state)
            return

        try:
            pct = int(message.text.strip())
            if not 0 <= pct <= 100:
                raise ValueError
        except:
            await message.answer("❌ Введите число от 0 до 100:", reply_markup=get_cancel_kb())
            return

        data = await state.get_data()
        task_id = data["task_id"]
        db = load_db()
        uid = str(message.from_user.id)
        fam_id = db["users"].get(uid, {}).get("current_family")
        nick = db["families"][fam_id]["members"][uid]["nick"]

        if not fam_id or fam_id not in db["families"]:
            await message.answer("❌ Ошибка доступа.", reply_markup=get_main_menu_kb())
            await state.clear()
            return

        fam = db["families"][fam_id]
        task = fam.get("tasks", {}).get(task_id)
        if not task:
            await message.answer("❌ Задача не найдена.", reply_markup=get_family_menu_kb(fam["name"]))
            await state.clear()
            return

        # Сохраняем обновление прогресса
        old_pct = task.get("progress", 0)
        task["progress"] = pct
        task["updates"].append({
            "user": nick,
            "from": old_pct,
            "to": pct,
            "timestamp": time.time()
        })

        # Если задача завершена — перемещаем в завершённые
        if pct == 100:
            task["completed_at"] = time.time()
            fam.setdefault("completed_tasks", {})[task_id] = task
            fam["tasks"].pop(task_id, None)
            atomic_save_db(db)

            await notify_family(
                message.bot,
                fam_id,
                f"✅ Задача «{task['desc']}» завершена участником {nick}!"
            )
            await message.answer(
                f"🎉 Задача «{task['desc']}» завершена!",
                reply_markup=get_family_menu_kb(fam["name"])
            )
        else:
            atomic_save_db(db)
            await notify_family(
                message.bot,
                fam_id,
                f"📈 {nick} обновил прогресс задачи «{task['desc']}»: {old_pct}% → {pct}%"
            )
            await message.answer(
                f"✅ Прогресс обновлён: {progress_bar(pct)}",
                reply_markup=get_family_menu_kb(fam["name"])
            )

        await state.clear()

    @dp.callback_query(F.data.startswith("task:items:"))
    async def show_shopping_list(cq: CallbackQuery) -> None:
        task_id = cq.data.split(":")[2]
        db = load_db()
        uid = str(cq.from_user.id)
        fam_id = db["users"].get(uid, {}).get("current_family")

        if not fam_id or fam_id not in db["families"]:
            await cq.answer("❌ Ошибка доступа!", show_alert=True)
            return

        fam = db["families"][fam_id]
        task = fam.get("tasks", {}).get(task_id)
        if not task or task["type"] != "shopping":
            await cq.answer("❌ Неверная задача!", show_alert=True)
            return

        # Формируем список покупок с чекбоксами
        items_text = "🛒 <b>Список покупок:</b>\n\n"
        builder = InlineKeyboardBuilder()

        for idx, (item, checked) in enumerate(zip(task["items"], task["items_checked"])):
            mark = "✅" if checked else "🔲"
            items_text += f"{mark} {item}\n"
            if not checked:
                builder.button(text=f"✓ {item}", callback_data=f"item:check:{task_id}:{idx}")

        builder.adjust(1)
        builder.row(InlineKeyboardButton(text="⬅️ Назад к задаче", callback_data=f"task:edit:{task_id}"))

        await cq.message.edit_text(
            items_text,
            parse_mode=ParseMode.HTML,
            reply_markup=builder.as_markup()
        )
        await cq.answer()

    @dp.callback_query(F.data.startswith("item:check:"))
    async def check_item(cq: CallbackQuery, state: FSMContext) -> None:
        parts = cq.data.split(":")
        task_id, item_idx = parts[2], int(parts[3])

        db = load_db()
        uid = str(cq.from_user.id)
        fam_id = db["users"].get(uid, {}).get("current_family")
        nick = db["families"][fam_id]["members"][uid]["nick"]

        if not fam_id or fam_id not in db["families"]:
            await cq.answer("❌ Ошибка доступа!", show_alert=True)
            return

        fam = db["families"][fam_id]
        task = fam.get("tasks", {}).get(task_id)
        if not task or task["type"] != "shopping":
            await cq.answer("❌ Ошибка задачи!", show_alert=True)
            return

        # Отмечаем элемент как купленный
        if not task["items_checked"][item_idx]:
            task["items_checked"][item_idx] = True
            atomic_save_db(db)

            # Проверяем завершённость списка
            if all(task["items_checked"]):
                task["progress"] = 100
                task["completed_at"] = time.time()
                fam.setdefault("completed_tasks", {})[task_id] = task
                fam["tasks"].pop(task_id, None)
                atomic_save_db(db)

                await notify_family(
                    cq.message.bot,
                    fam_id,
                    f"✅ Список покупок «{task['desc']}» полностью выполнен {nick}!"
                )
                await cq.message.edit_text(
                    f"🎉 Список покупок завершён!\n«{task['desc']}»",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="⬅️ К задачам", callback_data="tasks:list")]
                    ])
                )
            else:
                await cq.message.edit_reply_markup(reply_markup=None)
                await show_shopping_list(cq, state)
                await cq.answer(f"✅ {task['items'][item_idx]} куплено!")
        else:
            await cq.answer("❌ Уже куплено!", show_alert=True)

    @dp.callback_query(F.data.startswith("task:complete:"))
    async def complete_task(cq: CallbackQuery) -> None:
        task_id = cq.data.split(":")[2]
        db = load_db()
        uid = str(cq.from_user.id)
        fam_id = db["users"].get(uid, {}).get("current_family")
        nick = db["families"][fam_id]["members"][uid]["nick"]

        if not fam_id or fam_id not in db["families"]:
            await cq.answer("❌ Ошибка доступа!", show_alert=True)
            return

        fam = db["families"][fam_id]
        task = fam.get("tasks", {}).get(task_id)
        if not task:
            await cq.answer("❌ Задача не найдена!", show_alert=True)
            return

        # Перемещаем задачу в завершённые
        task["progress"] = 100
        task["completed_at"] = time.time()
        task["completed_by"] = nick
        fam.setdefault("completed_tasks", {})[task_id] = task
        fam["tasks"].pop(task_id, None)
        atomic_save_db(db)

        await notify_family(
            cq.message.bot,
            fam_id,
            f"✅ Задача «{task['desc']}» завершена участником {nick}!"
        )
        await cq.message.edit_text(
            f"✅ Задача «{task['desc']}» завершена!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ К задачам", callback_data="tasks:list")]
            ])
        )
        await cq.answer()

    @dp.callback_query(F.data == "tasks:completed")
    async def show_completed_tasks(cq: CallbackQuery) -> None:
        db = load_db()
        uid = str(cq.from_user.id)
        fam_id = db["users"].get(uid, {}).get("current_family")

        if not fam_id or fam_id not in db["families"]:
            await cq.answer("❌ Ошибка доступа!", show_alert=True)
            return

        fam = db["families"][fam_id]
        completed = fam.get("completed_tasks", {})

        if not completed:
            await cq.answer("📭 Нет завершённых задач", show_alert=True)
            return

        text = "✅ <b>Завершённые задачи:</b>\n\n"
        for task_id, task in list(completed.items())[:10]:  # Последние 10
            created = datetime.fromtimestamp(task["created_at"]).strftime("%d.%m")
            completed_at = datetime.fromtimestamp(task.get("completed_at", task["created_at"])).strftime("%d.%m %H:%M")
            by = task.get("completed_by", task.get("creator_nick", "???"))
            text += f"• {task['desc']} ({task['display_type']})\n  Завершена {completed_at} участником {by}\n\n"

        if len(completed) > 10:
            text += f"\n... и ещё {len(completed) - 10} задач"

        await cq.message.answer(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад к задачам", callback_data="tasks:list")]
            ])
        )
        await cq.answer()

    @dp.callback_query(F.data == "tasks:list")
    async def back_to_tasks(cq: CallbackQuery, state: FSMContext) -> None:
        db = load_db()
        uid = str(cq.from_user.id)
        fam_id = db["users"].get(uid, {}).get("current_family")
        fam = db["families"].get(fam_id, {})

        await tasks_list(cq.message, state)
        await cq.answer()

    # ─── ЗАПУСК БОТА ────────────────────────────────────────────────────
    asyncio.create_task(reminders_loop(bot))
    status_signal.emit("Бот запущен. Ожидание команд...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

# ────────────────────────────────────────────────
# Точка входа
# ────────────────────────────────────────────────
if __name__ == "__main__":
    log_info("FoxFamilyTask Bot starting...")
    app = QApplication(sys.argv)
    app.setApplicationName("FoxFamilyTask Bot")
    app.setApplicationVersion("2026.1")

    # Проверка наличия .env
    if not ENV_PATH.exists():
        with open(ENV_PATH, "w", encoding="utf-8") as f:
            f.write("# Telegram Bot Token\nTELEGRAM_BOT_TOKEN=\n")
        log_info("Created empty .env file")

    win = MainWindow()
    win.show()
    sys.exit(app.exec())
