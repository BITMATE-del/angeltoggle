import asyncio
import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QTextEdit, QPushButton, QLabel, QMessageBox, QSplitter, QWidget
)

from app.services.telegram_service import TelegramService


class ChatViewerBridge(QObject):
    dialogs_loaded = Signal(object)
    messages_loaded = Signal(object)
    failed = Signal(str)


class ChatViewerDialog(QDialog):
    def __init__(self, db, logs, account_id, parent=None):
        super().__init__(parent)
        self.db = db
        self.logs = logs
        self.account_id = account_id
        self.telegram = TelegramService(db, logs)
        self.bridge = ChatViewerBridge()
        self.bridge.dialogs_loaded.connect(self._show_dialogs)
        self.bridge.messages_loaded.connect(self._show_messages)
        self.bridge.failed.connect(self._show_error)

        self.account = self.db.fetchone(
            "SELECT * FROM telegram_accounts WHERE id=?",
            (account_id,)
        )
        account_name = self.account["name"] if self.account else str(account_id)

        self.setWindowTitle(f"엔젤토글 · 대화창 보기 · {account_name}")
        self.resize(980, 680)

        root = QVBoxLayout(self)

        top = QHBoxLayout()
        self.status = QLabel("대화목록을 불러오는 중입니다...")
        refresh = QPushButton("대화목록 새로고침")
        refresh.clicked.connect(self.load_dialogs)
        top.addWidget(self.status)
        top.addStretch()
        top.addWidget(refresh)
        root.addLayout(top)

        splitter = QSplitter()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("현재 대화목록"))
        self.dialogs = QListWidget()
        self.dialogs.itemClicked.connect(self.load_selected_messages)
        left_layout.addWidget(self.dialogs)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.chat_title = QLabel("대화방을 선택하세요.")
        self.messages = QTextEdit()
        self.messages.setReadOnly(True)
        right_layout.addWidget(self.chat_title)
        right_layout.addWidget(self.messages)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([330, 650])
        root.addWidget(splitter)

        close_btn = QPushButton("닫기")
        close_btn.clicked.connect(self.close)
        root.addWidget(close_btn)

        self.load_dialogs()

    def load_dialogs(self):
        if not self.account:
            QMessageBox.critical(self, "계정 오류", "계정 정보를 찾을 수 없습니다.")
            return
        self.status.setText("대화목록 불러오는 중...")
        self.dialogs.setEnabled(False)
        threading.Thread(target=self._dialogs_worker, daemon=True).start()

    def _dialogs_worker(self):
        try:
            result = asyncio.run(self.telegram.list_dialogs(self.account, limit=150))
            self.bridge.dialogs_loaded.emit(result)
        except Exception as e:
            self.bridge.failed.emit(str(e))

    def _show_dialogs(self, rows):
        self.dialogs.clear()
        for row in rows:
            suffix = ""
            if row["unread_count"]:
                suffix = f"  · 안읽음 {row['unread_count']}"
            item = QListWidgetItem(f"{row['title']}{suffix}")
            item.setData(32, row)
            self.dialogs.addItem(item)

        self.dialogs.setEnabled(True)
        self.status.setText(f"대화방 {len(rows)}개")
        if not rows:
            self.messages.setPlainText("표시할 대화방이 없습니다.")

    def load_selected_messages(self, item):
        data = item.data(32)
        if not data:
            return
        self.chat_title.setText(data["title"])
        self.messages.setPlainText("최근 메시지를 불러오는 중입니다...")
        threading.Thread(
            target=self._messages_worker,
            args=(data["id"],),
            daemon=True,
        ).start()

    def _messages_worker(self, dialog_id):
        try:
            result = asyncio.run(
                self.telegram.list_messages(self.account, dialog_id, limit=80)
            )
            self.bridge.messages_loaded.emit(result)
        except Exception as e:
            self.bridge.failed.emit(str(e))

    def _show_messages(self, rows):
        lines = []
        for row in rows:
            direction = "보냄" if row["out"] else "받음"
            text = row["text"] or "[내용 없음]"
            lines.append(
                f"[{row['date']}] [{direction}] {row['sender']}\n{text}\n"
            )
        self.messages.setPlainText("\n".join(lines) if lines else "메시지가 없습니다.")

    def _show_error(self, message):
        self.dialogs.setEnabled(True)
        self.status.setText("불러오기 실패")
        QMessageBox.critical(self, "대화조회 오류", message)
