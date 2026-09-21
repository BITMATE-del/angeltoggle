from PySide6.QtWidgets import *
from app.services.db_import_service import DBImportService
from app.services.session_service import SessionService

class MainWindow(QMainWindow):
    def __init__(self, db, logs):
        super().__init__()
        self.db = db
        self.logs = logs
        self.db_import = DBImportService(db, logs)
        self.session_import = SessionService(db, logs)
        self.setWindowTitle("엔젤토글")

        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)

        self.nav = QListWidget()
        self.nav.setFixedWidth(190)
        self.nav.addItems(["대시보드","원클릭 발송","고객 DB","PostBot 게시물","Telegram 계정","작업 로그","설정"])
        self.pages = QStackedWidget()

        layout.addWidget(self.nav)
        layout.addWidget(self.pages, 1)

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

    def title(self, text):
        x = QLabel(text)
        x.setStyleSheet("font-size:24px;font-weight:700;")
        return x

    def dashboard(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(self.title("엔젤토글"))
        self.summary = QLabel()
        self.summary.setStyleSheet("font-size:16px;padding:16px;")
        l.addWidget(self.summary)
        b = QPushButton("새로고침")
        b.clicked.connect(self.refresh_summary)
        l.addWidget(b)
        l.addStretch()
        self.refresh_summary()
        return w

    def refresh_summary(self):
        ac = self.db.fetchall("SELECT COUNT(*) c FROM telegram_accounts")[0]["c"]
        rc = self.db.fetchall("SELECT COUNT(*) c FROM recipients")[0]["c"]
        sent = self.db.fetchall("SELECT COUNT(*) c FROM recipients WHERE status='MESSAGE_SENT'")[0]["c"]
        self.summary.setText(f"등록 계정: {ac}개\n고객 DB: {rc}명\n발송 성공: {sent}명")

    def oneclick(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(self.title("원클릭 발송"))
        self.precheck = QLabel("API / 세션 / 고객 DB / PostBot 설정을 자동 점검합니다.")
        l.addWidget(self.precheck)
        b = QPushButton("사전점검 및 원클릭 발송 준비")
        b.setMinimumHeight(58)
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
            ("Telegram 세션", accounts > 0),
            ("고객 DB", recipients > 0),
            ("PostBot 게시물", post),
        ]
        self.precheck.setText("\n".join([f"{'✓' if ok else '✕'} {name}" for name, ok in checks]))
        if all(ok for _, ok in checks):
            self.logs.write("SUCCESS", "SYSTEM", "원클릭 사전점검 완료")
            QMessageBox.information(self, "완료", "모든 사전점검이 완료되었습니다.")
        else:
            self.logs.write("WARNING", "SYSTEM", "원클릭 사전점검 미완료")
            QMessageBox.warning(self, "확인 필요", "누락된 필수 설정이 있습니다.")

    def dbpage(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(self.title("고객 DB"))
        b = QPushButton("Excel DB 업로드")
        b.clicked.connect(self.import_db)
        l.addWidget(b)
        self.db_result = QLabel()
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
                f"전체 {s['total']} / 정상 {s['valid']} / 중복 {s['duplicate']} / 오류 {s['invalid']}"
            )
            self.refresh_summary()
        except Exception as e:
            QMessageBox.critical(self, "DB 업로드 오류", str(e))

    def postbot(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(self.title("PostBot 게시물"))
        self.postbot_input = QLineEdit(self.db.get_setting("postbot_link"))
        self.postbot_input.setPlaceholderText("PostBot 게시물 링크 또는 코드")
        l.addWidget(self.postbot_input)
        b = QPushButton("게시물 설정 저장")
        b.clicked.connect(self.save_postbot)
        l.addWidget(b)
        l.addStretch()
        return w

    def save_postbot(self):
        self.db.set_setting("postbot_link", self.postbot_input.text().strip())
        self.logs.write("INFO", "POSTBOT", "PostBot 게시물 설정 저장")
        QMessageBox.information(self, "저장", "저장되었습니다.")

    def accounts(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(self.title("Telegram 계정"))
        b = QPushButton("세션 ZIP 일괄등록")
        b.clicked.connect(self.import_sessions)
        l.addWidget(b)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["ID", "계정", "상태", "마지막 오류", "세션"])
        l.addWidget(self.table)
        r = QPushButton("새로고침")
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
            QMessageBox.information(self, "세션 등록", f"등록 {s['imported']}개 / 건너뜀 {s['skipped']}개")
            self.refresh_accounts()
            self.refresh_summary()
        except Exception as e:
            QMessageBox.critical(self, "세션 등록 오류", str(e))

    def refresh_accounts(self):
        rows = self.db.fetchall(
            "SELECT id,name,status,last_error,session_file FROM telegram_accounts ORDER BY id"
        )
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            vals = [row["id"], row["name"], row["status"], row["last_error"] or "", row["session_file"]]
            for c, v in enumerate(vals):
                self.table.setItem(r, c, QTableWidgetItem(str(v)))

    def logpage(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(self.title("실시간 작업 로그"))
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        l.addWidget(self.log_view)
        return w

    def append_log(self, item):
        if hasattr(self, "log_view"):
            acc = f" Account:{item['account_id']}" if item.get("account_id") else ""
            self.log_view.append(
                f"[{item['time']}] {item['level']} / {item['category']}{acc} - {item['message']}"
            )

    def settings(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(self.title("설정"))
        form = QFormLayout()
        self.api_id = QLineEdit(self.db.get_setting("telegram_api_id"))
        self.api_hash = QLineEdit(self.db.get_setting("telegram_api_hash"))
        self.api_hash.setEchoMode(QLineEdit.Password)
        form.addRow("API ID", self.api_id)
        form.addRow("API HASH", self.api_hash)
        b = QPushButton("저장 및 고정")
        b.clicked.connect(self.save_api)
        form.addRow(b)
        l.addLayout(form)
        l.addStretch()
        return w

    def save_api(self):
        self.db.set_setting("telegram_api_id", self.api_id.text().strip())
        self.db.set_setting("telegram_api_hash", self.api_hash.text().strip())
        self.logs.write("INFO", "SYSTEM", "Telegram API 설정 저장")
        QMessageBox.information(self, "저장", "Telegram API 설정이 저장되었습니다.")
