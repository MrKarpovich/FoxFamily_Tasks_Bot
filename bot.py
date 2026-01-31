from __future__ import annotations

import sys
import os
import asyncio
import sqlite3
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Optional

from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QMessageBox,
)
from PyQt6.QtCore import QThread, pyqtSignal

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage


# ==========================================================
# CONFIG
# ==========================================================

APP_NAME = "🦊 FoxFamilyTask Bot"
DB_FILE = "fox_family.db"

INVITE_KEY_LEN = 24
INVITE_TTL_MIN = 30


# ==========================================================
# UTILS
# ==========================================================

def gen_key() -> str:
    return "".join(
        secrets.choice(string.ascii_letters + string.digits)
        for _ in range(INVITE_KEY_LEN)
    )


# ==========================================================
# DATABASE
# ==========================================================

class Database:
    def __init__(self, path: str):
        self.path = path
        self.init_db()

    def init_db(self) -> None:
        with sqlite3.connect(self.path) as c:
            cur = c.cursor()

            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tg_id INTEGER UNIQUE NOT NULL
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS families (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_families (
                    user_id INTEGER,
                    family_id INTEGER,
                    PRIMARY KEY (user_id, family_id)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS invite_keys (
                    key TEXT PRIMARY KEY,
                    family_id INTEGER,
                    expires TEXT
                )
            """)

            c.commit()

    def ensure_user(self, tg_id: int) -> int:
        with sqlite3.connect(self.path) as c:
            cur = c.cursor()
            cur.execute(
                "INSERT OR IGNORE INTO users (tg_id) VALUES (?)",
                (tg_id,),
            )
            c.commit()

            cur.execute(
                "SELECT id FROM users WHERE tg_id = ?",
                (tg_id,),
            )
            return cur.fetchone()[0]

    def create_family(self, user_id: int) -> int:
        with sqlite3.connect(self.path) as c:
            cur = c.cursor()
            cur.execute("INSERT INTO families (name) VALUES ('Семья')")
            family_id = cur.lastrowid
            cur.execute(
                "INSERT INTO user_families VALUES (?, ?)",
                (user_id, family_id),
            )
            c.commit()
            return family_id

    def create_invite(self, family_id: int) -> str:
        key = gen_key()
        expires = datetime.now(timezone.utc) + timedelta(minutes=INVITE_TTL_MIN)

        with sqlite3.connect(self.path) as c:
            c.execute(
                "INSERT INTO invite_keys VALUES (?, ?, ?)",
                (key, family_id, expires.isoformat()),
            )
            c.commit()
        return key

    def use_invite(self, key: str, user_id: int) -> bool:
        now = datetime.now(timezone.utc)

        with sqlite3.connect(self.path) as c:
            cur = c.cursor()
            cur.execute(
                "SELECT family_id, expires FROM invite_keys WHERE key=?",
                (key,),
            )
            row = cur.fetchone()
            if not row:
                return False

            family_id, expires = row
            expires = datetime.fromisoformat(expires)

            cur.execute("DELETE FROM invite_keys WHERE key=?", (key,))

            if now > expires:
                c.commit()
                return False

            cur.execute(
                "INSERT OR IGNORE INTO user_families VALUES (?, ?)",
                (user_id, family_id),
            )
            c.commit()
            return True

    def get_families(self, user_id: int):
        with sqlite3.connect(self.path) as c:
            cur = c.cursor()
            cur.execute("""
                SELECT f.id, f.name
                FROM families f
                JOIN user_families uf ON uf.family_id = f.id
                WHERE uf.user_id = ?
            """, (user_id,))
            return cur.fetchall()


# ==========================================================
# FSM
# ==========================================================

class JoinFamilyFSM(StatesGroup):
    waiting_key = State()


# ==========================================================
# BOT WORKER (FIXED)
# ==========================================================

class BotWorker(QThread):
    status = pyqtSignal(str)

    def __init__(self, token: str, db: Database):
        super().__init__()
        self.token = token
        self.db = db

    def run(self) -> None:
        asyncio.run(self.bot_main())

    async def bot_main(self) -> None:
        bot = Bot(self.token)
        dp = Dispatcher(storage=MemoryStorage())

        def main_menu():
            return InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="👨‍👩‍👧 Создать семью", callback_data="create_family")],
                [InlineKeyboardButton(text="🔑 Присоединиться", callback_data="join_family")],
                [InlineKeyboardButton(text="🏠 Мои семьи", callback_data="my_families")],
            ])

        @dp.message(F.text == "/start")
        async def start_cmd(msg: Message):
            self.db.ensure_user(msg.from_user.id)
            await msg.answer(
                "🦊 Привет!\nЯ семейный помощник 💛",
                reply_markup=main_menu(),
            )

        @dp.callback_query(F.data == "create_family")
        async def create_family(cb: CallbackQuery):
            uid = self.db.ensure_user(cb.from_user.id)
            fid = self.db.create_family(uid)
            key = self.db.create_invite(fid)

            await cb.message.edit_text(
                f"🎉 Семья создана!\n\n"
                f"🔑 Ключ (30 мин):\n`{key}`",
                parse_mode="Markdown",
                reply_markup=main_menu(),
            )

        @dp.callback_query(F.data == "join_family")
        async def join_family(cb: CallbackQuery, state: FSMContext):
            await state.set_state(JoinFamilyFSM.waiting_key)
            await cb.message.edit_text("🔑 Отправь ключ семьи")

        @dp.message(JoinFamilyFSM.waiting_key)
        async def process_key(msg: Message, state: FSMContext):
            uid = self.db.ensure_user(msg.from_user.id)
            ok = self.db.use_invite(msg.text.strip(), uid)
            await state.clear()

            if ok:
                await msg.answer("🎉 Готово! Ты в семье!", reply_markup=main_menu())
            else:
                await msg.answer("😕 Ключ неверный", reply_markup=main_menu())

        @dp.callback_query(F.data == "my_families")
        async def my_families(cb: CallbackQuery):
            uid = self.db.ensure_user(cb.from_user.id)
            families = self.db.get_families(uid)

            if not families:
                await cb.message.edit_text(
                    "📭 У тебя нет семей",
                    reply_markup=main_menu(),
                )
                return

            text = "🏠 Твои семьи:\n\n"
            for fid, name in families:
                text += f"🦊 {name} (ID {fid})\n"

            await cb.message.edit_text(text, reply_markup=main_menu())

        self.status.emit("🟢 Бот запущен")
        await dp.start_polling(bot)


# ==========================================================
# GUI
# ==========================================================

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(420, 200)

        self.db = Database(DB_FILE)
        self.worker: Optional[BotWorker] = None

        central = QWidget()
        layout = QVBoxLayout(central)

        self.token_input = QLineEdit()
        self.token_input.setPlaceholderText("Telegram Bot Token")

        self.btn = QPushButton("🚀 Запустить бота")
        self.btn.clicked.connect(self.start_bot)

        self.status = QLabel("⏸ Остановлен")

        layout.addWidget(self.token_input)
        layout.addWidget(self.btn)
        layout.addWidget(self.status)
        self.setCentralWidget(central)

    def start_bot(self):
        token = self.token_input.text().strip()
        if not token:
            QMessageBox.critical(self, "Ошибка", "Введите токен")
            return

        self.worker = BotWorker(token, self.db)
        self.worker.status.connect(self.status.setText)
        self.worker.start()


# ==========================================================
# ENTRY POINT
# ==========================================================

def main():
    if os.path.exists(DB_FILE) and not os.path.exists(DB_FILE + ".backup"):
        import shutil
        shutil.copy(DB_FILE, DB_FILE + ".backup")

    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
