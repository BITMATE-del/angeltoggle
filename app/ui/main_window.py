from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import *
from app.services.db_import_service import DBImportService
from app.services.session_service import SessionService

TERMINAL_STYLE = """
QMainWindow, QWidget {
    background-color: #050706;
    color: #D9FFE7;
    font-family: Consolas, "D2Coding", monospace;
    font-size: 13px;
}
QListWidget {
    background-color: #07100B;
    border: 1px solid #173A27;
    border-radius: 10px;
    padding: 8px;
    outline: 0;
}
QListWidget::item {
    color: #85E89D;
    padding: 12px 10px;
    margin: 2px 0;
    border-radius: 6px;
}
QListWidget::item:selected {
    background-color: #0D2A19;
    color: #6CFF9B;
    border: 1px solid #2CE66D;
}
QPushButton {
    background-color: #0B1B12;
    color: #79FF9E;
    border: 1px solid #28D862;
    border-radius: 7px;
    padding: 10px 16px;
    font-weight: 700;
}
QPushButton:hover {
    background-color: #12361F;
    border: 1px solid #74FF9C;
}
QPushButton:pressed {
    background-color: #1B4B2B;
}
QLineEdit, QTextEdit, QTableWidget {
    background-color: #020403;
    color: #C8FFD8;
    border: 1px solid #1D5A35;
    border-radius: 6px;
    selection-background-color: #145A2E;
    selection-color: #E9FFF0;
}
QLineEdit {
    padding: 9px;
}
QTextEdit {
    padding: 10px;
}
QTableWidget {
    gridline-color: #143721;
}
QHeaderView::section {
    background-color: #0A160F;
    color: #6CFF9B;
    border: 0;
    border-bottom: 1px solid #245739;
    padding: 8px;
    font-weight: 700;
}
QGroupBox {
    border: 1px solid #1E5E36;
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 14px;
    font-weight: 700;
    color: #75FF9A;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
}
QLabel#TerminalTitle {
    color: #67FF91;
    font-size: 24px;
    font-weight: 800;
}
QLabel#TerminalSub {
    color: #75A985;
}
QLabel#StatusPanel {
    background-color: #07100B;
    border: 1px solid #1C5A33;
    border-radius: 8px;
    padding: 16px;
    color: #BFFFD0;
}
QFrame#TerminalPanel {
    background-color: #07100B;
    border: 1px solid #173A27;
    border-radius: 10px;
}
"""

class MainWindow(QMainWindow):
    def __init__(self, db, logs):
        super().__init__()
        self.db = db
        self.logs = logs
        self.db_import = DBImportService(db, logs)
        self.session_import = SessionService(db, logs)

        self.setWindowTitle("엔젤토글 // ANGEL TOGGLE")
        self.setMinimumSize(1100, 700)
        self.setStyleSheet(TERMINAL_STYLE)

        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(10)

        top = QHBoxLayout()
        brand = QLabel("ANGELTOGGLE.exe")
        brand.setObjectName("TerminalTitle")
        state = QLabel("[ SYSTEM ONLINE ]")
        state.setStyleSheet("color:#6CFF9B;font-weight:800;")
        top.addWidget(brand)
        top.addStretch()
        top.addWidget(state)
        outer.addLayout(top)

        shell = QFrame()
        shell.setObjectName("TerminalPanel")
        layout = QHBoxLayout(shell)
        layout.setContentsMargins(10, 10, 10, 10)

        self.nav = QListWidget()
        self.nav.setFixedWidth(220)
        self.nav.addItems([
            "> DASHBOARD",
            "> ONE-CLICK SEND",
            "> CUSTOMER DB",
            "> POSTBOT",
            "> TELEGRAM ACCOUNTS",
            "> WORK LOG",
            "> SETTINGS",
        ])
        self.pages = QStackedWidget()

        layout.addWidget(self.nav)
        layout.addWidget(self.pages, 1)
        outer.addWidget(shell, 1)

        footer = QLabel("C:\\ANGELTOGGLE> ready_")
        footer.setObjectName("TerminalSub")
        outer.addWidget(footer)

        self.pages.addWidget(self.dashboard())
        self.pages.addWidget(self.oneclick())
        self.pages.addWidget(self.dbpage())
        self.pages.addWidget(self.postbot())
        self.pages.addWidget(self.accounts())
        self.pages.addWidget(self.logpage())
        self.pages.addWidget(self.settings())

        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)
        self.logs.subscribe(self.append_log)

    def title(self, text, command=None):
        box = QWidget()
        l = QVBoxLayout(box)
        l.setContentsMargins(0, 0, 0, 8)
        cmd = QLabel(command or f"C:\\ANGELTOGGLE> {text.lower().replace(' ', '_')}")
        cmd.setObjectName("TerminalSub")
        h = QLabel(text)
        h.setObjectName("TerminalTitle")
        l.addWidget(cmd)
        l.addWidget(h)
        return box

    def panel(self):
        p = QFrame()
        p.setObjectName("TerminalPanel")
        return p

    def dashboard(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(self.title("엔젤토글 CONTROL CENTER", "C:\\ANGELTOGGLE> status --all"))

        self.summary = QLabel()
        self.summary.setObjectName("StatusPanel")
        self.summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        l.addWidget(self.summary)

        row = QHBoxLayout()
        refresh = QPushButton("[ REFRESH STATUS ]")
        refresh.clicked.connect(self.refresh_summary)
        row.addWidget(refresh)
        row.addStretch()
        l.addLayout(row)

        console = QTextEdit()
        console.setReadOnly(True)
        console.setPlainText(
            "[BOOT] ANGELTOGGLE controller loaded\n"
            "[OK] local database ready\n"
            "[WAIT] Telegram API / Session / DB / PostBot input\n"
            "[TIP] ONE-CLICK SEND performs all preflight checks automatically"
        )
        console.setMaximumHeight(180)
        l.addWidget(console)
        l.addStretch()
        self.refresh_summary()
        return w

    def refresh_summary(self):
        ac = self.db.fetchall("SELECT COUNT(*) c FROM telegram_accounts")[0]["c"]
        rc = self.db.fetchall("SELECT COUNT(*) c FROM recipients")[0]["c"]
        sent = self.db.fetchall("SELECT COUNT(*) c FROM recipients WHERE status='MESSAGE_SENT'")[0]["c"]
        pending = self.db.fetchall("SELECT COUNT(*) c FROM recipients WHERE status='PENDING'")[0]["c"]
        self.summary.setText(
            f"[ACCOUNTS]  {ac:>6} registered\n"
            f"[CUSTOMER]  {rc:>6} total\n"
            f"[PENDING ]  {pending:>6} ready\n"
            f"[SENT    ]  {sent:>6} complete\n"
            f"\nC:\\ANGELTOGGLE> system_status = READY"
        )

    def oneclick(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(self.title("원클릭 발송", "C:\\ANGELTOGGLE> send --preflight --auto"))

        info = QLabel(
            "하나의 버튼으로 API / 세션 / 고객 DB / PostBot 게시물을 검사하고\n"
            "발송 준비 상태를 자동으로 정리합니다."
        )
        info.setObjectName("StatusPanel")
        l.addWidget(info)

        self.precheck = QTextEdit()
        self.precheck.setReadOnly(True)
        self.precheck.setMaximumHeight(220)
        self.precheck.setPlainText(
            "$ waiting for command...\n"
            "[ ] Telegram API\n"
            "[ ] Telegram Sessions\n"
            "[ ] Customer DB\n"
            "[ ] PostBot"
        )
        l.addWidget(self.precheck)

        b = QPushButton(">> RUN ONE-CLICK PREFLIGHT")
        b.setMinimumHeight(64)
        b.clicked.connect(self.run_precheck)
        l.addWidget(b)
        l.addStretch()
        return w

    def run_precheck(self):
        api_ok = bool(self.db.get_setting("telegram_api_id")) and bool(self.db.get_setting("telegram_api_hash"))
        accounts = self.db.fetchall("SELECT COUNT(*) c FROM telegram_accounts")[0]["c"]
        recipients = self.db.fetchall("SELECT COUNT(*) c FROM recipients WHERE status='PENDING'")[0]["c"]
        post = bool(self.db.get_setting("postbot_link"))
        checks = [
            ("Telegram API", api_ok),
            ("Telegram Sessions", accounts > 0),
            ("Customer DB", recipients > 0),
            ("PostBot", post),
        ]
        lines = ["$ preflight --all"]
        lines += [f"[{'OK' if ok else 'FAIL'}] {name}" for name, ok in checks]
        lines.append("")
        lines.append("READY_TO_SEND=TRUE" if all(ok for _, ok in checks) else "READY_TO_SEND=FALSE")
        self.precheck.setPlainText("\n".join(lines))

        if all(ok for _, ok in checks):
            self.logs.write("SUCCESS", "SYSTEM", "원클릭 사전점검 완료")
            QMessageBox.information(self, "PRECHECK OK", "모든 필수 준비가 완료되었습니다.")
        else:
            self.logs.write("WARNING", "SYSTEM", "원클릭 사전점검 미완료")
            QMessageBox.warning(self, "PRECHECK FAILED", "누락된 필수 설정이 있습니다.")

    def dbpage(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(self.title("고객 DB", "C:\\ANGELTOGGLE> db import"))
        b = QPushButton("[ IMPORT EXCEL DATABASE ]")
        b.clicked.connect(self.import_db)
        l.addWidget(b)
        self.db_result = QLabel("No database imported.")
        self.db_result.setObjectName("StatusPanel")
        l.addWidget(self.db_result)
        l.addStretch()
        return w

    def import_db(self):
        path, _ = QFileDialog.getOpenFileName(self, "Excel DB 선택", "", "Excel (*.xlsx)")
        if not path:
            return
        try:
            s = self.db_import.import_xlsx(path)
            self.db_result.setText(
                f"[IMPORT COMPLETE]\nTOTAL={s['total']}\nVALID={s['valid']}\n"
                f"DUPLICATE={s['duplicate']}\nINVALID={s['invalid']}"
            )
            self.refresh_summary()
        except Exception as e:
            QMessageBox.critical(self, "DB IMPORT ERROR", str(e))

    def postbot(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(self.title("PostBot 게시물", "C:\\ANGELTOGGLE> postbot configure"))

        hint = QLabel("이미지 + 버튼이 구성된 PostBot 게시물 링크 또는 코드를 등록합니다.")
        hint.setObjectName("TerminalSub")
        l.addWidget(hint)

        self.postbot_input = QLineEdit(self.db.get_setting("postbot_link"))
        self.postbot_input.setPlaceholderText("paste PostBot link / code here...")
        l.addWidget(self.postbot_input)

        b = QPushButton("[ SAVE POSTBOT CONFIG ]")
        b.clicked.connect(self.save_postbot)
        l.addWidget(b)
        l.addStretch()
        return w

    def save_postbot(self):
        self.db.set_setting("postbot_link", self.postbot_input.text().strip())
        self.logs.write("INFO", "POSTBOT", "PostBot 게시물 설정 저장")
        QMessageBox.information(self, "POSTBOT", "게시물 설정을 저장했습니다.")

    def accounts(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(self.title("Telegram 계정", "C:\\ANGELTOGGLE> accounts --sessions"))

        b = QPushButton("[ IMPORT SESSION ZIP ]")
        b.clicked.connect(self.import_sessions)
        l.addWidget(b)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["ID", "ACCOUNT", "STATUS", "LAST ERROR", "SESSION"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(False)
        l.addWidget(self.table)

        r = QPushButton("[ REFRESH ACCOUNTS ]")
        r.clicked.connect(self.refresh_accounts)
        l.addWidget(r)
        self.refresh_accounts()
        return w

    def import_sessions(self):
        path, _ = QFileDialog.getOpenFileName(self, "Session ZIP 선택", "", "ZIP (*.zip)")
        if not path:
            return
        try:
            s = self.session_import.import_zip(path)
            QMessageBox.information(
                self,
                "SESSION IMPORT",
                f"IMPORTED={s['imported']}\nSKIPPED={s['skipped']}"
            )
            self.refresh_accounts()
            self.refresh_summary()
        except Exception as e:
            QMessageBox.critical(self, "SESSION IMPORT ERROR", str(e))

    def refresh_accounts(self):
        rows = self.db.fetchall(
            "SELECT id,name,status,last_error,session_file FROM telegram_accounts ORDER BY id"
        )
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            vals = [row["id"], row["name"], row["status"], row["last_error"] or "", row["session_file"]]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                if c == 2:
                    item.setText(f"[{v}]")
                self.table.setItem(r, c, item)

    def logpage(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(self.title("실시간 작업 로그", "C:\\ANGELTOGGLE> tail -f work.log"))
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas", 10))
        self.log_view.setPlainText("[LOG] waiting for worker events...")
        l.addWidget(self.log_view)
        return w

    def append_log(self, item):
        if hasattr(self, "log_view"):
            if self.log_view.toPlainText().startswith("[LOG] waiting"):
                self.log_view.clear()
            acc = f" account={item['account_id']}" if item.get("account_id") else ""
            self.log_view.append(
                f"[{item['time']}] [{item['level']}] [{item['category']}]{acc} :: {item['message']}"
            )

    def settings(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(self.title("설정", "C:\\ANGELTOGGLE> config telegram-api"))

        group = QGroupBox("TELEGRAM API CONFIG")
        form = QFormLayout(group)
        self.api_id = QLineEdit(self.db.get_setting("telegram_api_id"))
        self.api_id.setPlaceholderText("API ID")
        self.api_hash = QLineEdit(self.db.get_setting("telegram_api_hash"))
        self.api_hash.setEchoMode(QLineEdit.Password)
        self.api_hash.setPlaceholderText("API HASH")
        form.addRow("API ID", self.api_id)
        form.addRow("API HASH", self.api_hash)

        b = QPushButton("[ SAVE & LOCK CONFIG ]")
        b.clicked.connect(self.save_api)
        form.addRow(b)
        l.addWidget(group)
        l.addStretch()
        return w

    def save_api(self):
        self.db.set_setting("telegram_api_id", self.api_id.text().strip())
        self.db.set_setting("telegram_api_hash", self.api_hash.text().strip())
        self.logs.write("INFO", "SYSTEM", "Telegram API 설정 저장")
        QMessageBox.information(self, "CONFIG SAVED", "Telegram API 설정을 저장했습니다.")
