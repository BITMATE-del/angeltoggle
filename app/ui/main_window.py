import threading
from datetime import datetime

from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import *

from app.services.db_import_service import DBImportService
from app.services.session_service import SessionService
from app.services.send_engine import SendEngine
from app.services.update_service import UpdateService
from app.ui.chat_viewer import ChatViewerDialog


class 신호브리지(QObject):
    로그 = Signal(dict)
    작업완료 = Signal(str, object)


CMD_STYLE = """
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
QPushButton:disabled {
    color: #456B50;
    border: 1px solid #24432F;
    background-color: #08100B;
}
QLineEdit, QTextEdit, QTableWidget, QSpinBox {
    background-color: #020403;
    color: #C8FFD8;
    border: 1px solid #1D5A35;
    border-radius: 6px;
    selection-background-color: #145A2E;
    selection-color: #E9FFF0;
}
QLineEdit, QSpinBox {
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
QLabel#제목 {
    color: #67FF91;
    font-size: 24px;
    font-weight: 800;
}
QLabel#보조 {
    color: #75A985;
}
QLabel#상태패널 {
    background-color: #07100B;
    border: 1px solid #1C5A33;
    border-radius: 8px;
    padding: 16px;
    color: #BFFFD0;
}
QFrame#콘솔패널 {
    background-color: #07100B;
    border: 1px solid #173A27;
    border-radius: 10px;
}
"""


class MainWindow(QMainWindow):
    def __init__(self, db, logs, license_info=None):
        super().__init__()
        self.db = db
        self.logs = logs
        self.db_import = DBImportService(db, logs)
        self.session_import = SessionService(db, logs)
        self.send_engine = SendEngine(db, logs)
        self.update_service = UpdateService(logs)
        self.license_info = license_info or {}

        self.bridge = 신호브리지()
        self.bridge.로그.connect(self.append_log)
        self.bridge.작업완료.connect(self.on_task_finished)
        self.logs.subscribe(lambda item: self.bridge.로그.emit(item))

        self.worker_thread = None
        self.current_campaign_id = None

        self.setWindowTitle("엔젤토글")
        self.setMinimumSize(1160, 740)
        self.setStyleSheet(CMD_STYLE)

        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(10)

        top = QHBoxLayout()
        brand = QLabel("엔젤토글.exe")
        brand.setObjectName("제목")
        self.license_state = QLabel(self._license_badge_text())
        self.license_state.setStyleSheet("color:#FFD66C;font-weight:800;")
        self.top_state = QLabel("[ 시스템 정상 ]")
        self.top_state.setStyleSheet("color:#6CFF9B;font-weight:800;")
        top.addWidget(brand)
        top.addStretch()
        top.addWidget(self.license_state)
        top.addSpacing(16)
        top.addWidget(self.top_state)
        outer.addLayout(top)

        shell = QFrame()
        shell.setObjectName("콘솔패널")
        layout = QHBoxLayout(shell)
        layout.setContentsMargins(10, 10, 10, 10)

        self.nav = QListWidget()
        self.nav.setFixedWidth(220)
        self.nav.addItems([
            "> 대시보드",
            "> 작업 실행",
            "> 고객 DB",
            "> PostBot 게시물",
            "> 텔레그램 계정",
            "> 작업 로그",
            "> 설정",
        ])

        self.pages = QStackedWidget()
        layout.addWidget(self.nav)
        layout.addWidget(self.pages, 1)
        outer.addWidget(shell, 1)

        footer = QLabel("C:\\엔젤토글> 준비완료_")
        footer.setObjectName("보조")
        outer.addWidget(footer)

        self.pages.addWidget(self.dashboard())
        self.pages.addWidget(self.work_page())
        self.pages.addWidget(self.db_page())
        self.pages.addWidget(self.postbot_page())
        self.pages.addWidget(self.accounts_page())
        self.pages.addWidget(self.log_page())
        self.pages.addWidget(self.settings_page())

        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)

        self.refresh_summary()
        self.refresh_accounts()
        self.refresh_work_status()

    def _license_badge_text(self):
        remaining = self.license_info.get("remaining_days")
        if remaining is None:
            return "[ 라이선스 확인 필요 ]"
        return f"[ 라이선스 {remaining}일 남음 ]"

    def _format_license_expiry(self, value):
        if not value:
            return "확인 필요"
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return dt.astimezone().strftime("%Y-%m-%d %H:%M")
        except Exception:
            return str(value)

    def title(self, text, command):
        box = QWidget()
        l = QVBoxLayout(box)
        l.setContentsMargins(0, 0, 0, 8)
        cmd = QLabel(command)
        cmd.setObjectName("보조")
        h = QLabel(text)
        h.setObjectName("제목")
        l.addWidget(cmd)
        l.addWidget(h)
        return box

    def dashboard(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(self.title("엔젤토글 관리센터", "C:\\엔젤토글> 전체 상태 확인"))

        self.summary = QLabel()
        self.summary.setObjectName("상태패널")
        self.summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        l.addWidget(self.summary)

        refresh = QPushButton("[ 상태 새로고침 ]")
        refresh.clicked.connect(self.refresh_summary)
        l.addWidget(refresh)

        console = QTextEdit()
        console.setReadOnly(True)
        console.setMaximumHeight(170)
        console.setPlainText(
            "[시작] 엔젤토글 실행 완료\n"
            "[안내] 고객 DB → 중복검수 → 연락처 추가 → 게시물 발송 순서로 진행합니다.\n"
            "[안내] 계정 오류 발생 시 해당 계정만 중단됩니다.\n"
            "[안내] 프로그램 업데이트는 실행 시 자동 확인됩니다."
        )
        l.addWidget(console)
        l.addStretch()
        return w

    def refresh_summary(self):
        ac = self.db.fetchone("SELECT COUNT(*) c FROM telegram_accounts")["c"]
        rc = self.db.fetchone("SELECT COUNT(*) c FROM recipients")["c"]
        pending = self.db.fetchone("SELECT COUNT(*) c FROM recipients WHERE status='PENDING'")["c"]
        added = self.db.fetchone("SELECT COUNT(*) c FROM recipients WHERE contact_status='ADDED'")["c"]
        sent = self.db.fetchone("SELECT COUNT(*) c FROM recipients WHERE status='MESSAGE_SENT'")["c"]
        remaining = self.license_info.get("remaining_days")
        expires_at = self._format_license_expiry(self.license_info.get("expires_at"))
        offline = "오프라인 유예" if self.license_info.get("offline") else "서버 인증"
        remaining_text = f"{remaining}일" if remaining is not None else "확인 필요"

        self.summary.setText(
            f"[등록 계정]       {ac:>7}개\n"
            f"[전체 고객 DB]    {rc:>7}명\n"
            f"[사용 가능 DB]    {pending:>7}명\n"
            f"[연락처 추가완료] {added:>7}명\n"
            f"[게시물 발송완료] {sent:>7}명\n"
            f"[라이선스]        {remaining_text:>7} 남음\n"
            f"[만료일]          {expires_at}\n"
            f"[인증상태]        {offline}\n\n"
            f"C:\\엔젤토글> 시스템 상태 = 정상"
        )

    def work_page(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(self.title("작업 실행", "C:\\엔젤토글> 연락처 추가 -> 게시물 발송"))

        info = QLabel(
            "작업은 두 단계로 분리됩니다.\n"
            "1단계에서 연락처를 먼저 추가하고, 실제로 추가된 인원만 2단계 게시물 발송 대상으로 사용합니다.\n"
            "예: 최대 40명 설정 후 33명만 추가되면 33명으로 연락처 단계가 종료되며, 그 33명은 즉시 게시물 발송 가능합니다."
        )
        info.setObjectName("상태패널")
        l.addWidget(info)

        self.work_status = QTextEdit()
        self.work_status.setReadOnly(True)
        self.work_status.setMaximumHeight(215)
        l.addWidget(self.work_status)

        step1 = QGroupBox("1단계 · 연락처 추가")
        step1_layout = QVBoxLayout(step1)
        self.contact_button = QPushButton("[ 연락처 추가 시작 · 10명씩 빠른 처리 ]")
        self.contact_button.setMinimumHeight(56)
        self.contact_button.clicked.connect(self.start_contact_stage)
        step1_layout.addWidget(self.contact_button)
        l.addWidget(step1)

        step2 = QGroupBox("2단계 · 게시물 발송")
        step2_layout = QVBoxLayout(step2)
        self.send_button = QPushButton("[ 추가 완료 연락처에 게시물 발송 ]")
        self.send_button.setMinimumHeight(56)
        self.send_button.clicked.connect(self.start_send_stage)
        step2_layout.addWidget(self.send_button)
        l.addWidget(step2)

        self.auto_button = QPushButton("[ 전체 자동 진행 · 연락처 추가 후 게시물 발송 ]")
        self.auto_button.setMinimumHeight(62)
        self.auto_button.clicked.connect(self.start_full_auto)
        l.addWidget(self.auto_button)
        l.addStretch()
        return w

    def _check_base(self, need_post=False):
        api_ok = bool(self.db.get_setting("telegram_api_id")) and bool(self.db.get_setting("telegram_api_hash"))
        accounts = self.db.fetchone(
            "SELECT COUNT(*) c FROM telegram_accounts WHERE enabled=1 "
            "AND status NOT IN ('SEND_RESTRICTED','PEER_FLOOD','FLOOD_WAIT','SESSION_ERROR','STOPPED')"
        )["c"]
        pending = self.db.fetchone("SELECT COUNT(*) c FROM recipients WHERE status='PENDING'")["c"]
        post_ok = bool(self.db.get_setting("postbot_link"))
        checks = [("텔레그램 API", api_ok), ("사용 가능한 계정", accounts > 0), ("고객 DB", pending > 0)]
        if need_post:
            checks.append(("PostBot 게시물", post_ok))
        return checks

    def _create_campaign_if_needed(self):
        latest = self.send_engine.latest_campaign()
        if latest and latest["status"] in ("CONTACT_WAITING", "CONTACT_RUNNING", "CONTACT_DONE"):
            self.current_campaign_id = latest["id"]
            return latest["id"]

        bot_username = self.db.get_setting("postbot_username", "@PostBot") or "@PostBot"
        post_code = self.db.get_setting("postbot_link", "")
        campaign_name = "작업_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        self.current_campaign_id = self.send_engine.create_campaign(campaign_name, bot_username, post_code)
        return self.current_campaign_id

    def _set_busy(self, busy):
        self.contact_button.setEnabled(not busy)
        self.send_button.setEnabled(not busy)
        self.auto_button.setEnabled(not busy)
        self.top_state.setText("[ 작업 실행중 ]" if busy else "[ 시스템 정상 ]")

    def start_contact_stage(self):
        if self.worker_thread and self.worker_thread.is_alive():
            QMessageBox.information(self, "작업 중", "현재 다른 작업이 실행 중입니다.")
            return

        checks = self._check_base(False)
        failed = [name for name, ok in checks if not ok]
        if failed:
            QMessageBox.warning(self, "준비 확인", "다음 항목을 먼저 준비하세요.\n- " + "\n- ".join(failed))
            return

        try:
            campaign_id = self._create_campaign_if_needed()
        except Exception as e:
            QMessageBox.critical(self, "작업 생성 오류", str(e))
            return

        self._set_busy(True)
        self.worker_thread = threading.Thread(
            target=self._background_contact,
            args=(campaign_id,),
            daemon=True,
        )
        self.worker_thread.start()

    def _background_contact(self, campaign_id):
        try:
            result = self.send_engine.run_contact_stage(campaign_id)
            self.bridge.작업완료.emit("연락처", result)
        except Exception as e:
            self.bridge.작업완료.emit("오류", str(e))

    def start_send_stage(self):
        if self.worker_thread and self.worker_thread.is_alive():
            QMessageBox.information(self, "작업 중", "현재 다른 작업이 실행 중입니다.")
            return

        latest = self.send_engine.latest_campaign()
        if not latest:
            QMessageBox.warning(self, "발송 불가", "먼저 연락처 추가 작업을 진행하세요.")
            return

        ready = self.db.fetchone(
            "SELECT COUNT(*) c FROM campaign_recipients "
            "WHERE campaign_id=? AND contact_status='ADDED' "
            "AND status IN ('ASSIGNED','SEND_PAUSED')",
            (latest["id"],),
        )["c"]
        if ready <= 0:
            QMessageBox.warning(self, "발송 불가", "게시물을 발송할 연락처가 없습니다.")
            return

        if not self.db.get_setting("postbot_link"):
            QMessageBox.warning(self, "게시물 확인", "PostBot 게시물을 먼저 등록하세요.")
            return

        self.current_campaign_id = latest["id"]
        self._set_busy(True)
        self.worker_thread = threading.Thread(
            target=self._background_send,
            args=(latest["id"],),
            daemon=True,
        )
        self.worker_thread.start()

    def _background_send(self, campaign_id):
        try:
            result = self.send_engine.run_send_stage(campaign_id)
            self.bridge.작업완료.emit("발송", result)
        except Exception as e:
            self.bridge.작업완료.emit("오류", str(e))

    def start_full_auto(self):
        if self.worker_thread and self.worker_thread.is_alive():
            QMessageBox.information(self, "작업 중", "현재 다른 작업이 실행 중입니다.")
            return

        checks = self._check_base(True)
        failed = [name for name, ok in checks if not ok]
        if failed:
            QMessageBox.warning(self, "준비 확인", "다음 항목을 먼저 준비하세요.\n- " + "\n- ".join(failed))
            return

        try:
            campaign_id = self._create_campaign_if_needed()
        except Exception as e:
            QMessageBox.critical(self, "작업 생성 오류", str(e))
            return

        self._set_busy(True)
        self.worker_thread = threading.Thread(
            target=self._background_full_auto,
            args=(campaign_id,),
            daemon=True,
        )
        self.worker_thread.start()

    def _background_full_auto(self, campaign_id):
        try:
            contact = self.send_engine.run_contact_stage(campaign_id)
            if contact["ready"] > 0:
                sent = self.send_engine.run_send_stage(campaign_id)
            else:
                sent = {"success": 0, "failed": 0, "remaining": 0}
            self.bridge.작업완료.emit("전체", {"contact": contact, "sent": sent})
        except Exception as e:
            self.bridge.작업완료.emit("오류", str(e))

    def on_task_finished(self, kind, result):
        if kind == "업데이트확인":
            self.update_label.setText(result["text"])
            info = result.get("info")
            if info and info.get("available"):
                if self.worker_thread and self.worker_thread.is_alive():
                    self.update_label.setText(
                        result["text"] + " / 현재 작업 종료 후 다음 실행에서 자동 업데이트됩니다."
                    )
                    return
                self.top_state.setText("[ 업데이트 설치중 ]")
                self.update_label.setText(result["text"] + " / 새 버전을 자동 설치합니다.")
                threading.Thread(
                    target=self._apply_update_worker,
                    args=(info["download_url"],),
                    daemon=True,
                ).start()
            return

        if kind == "업데이트설치":
            if result.get("ok"):
                QApplication.instance().quit()
            else:
                self.top_state.setText("[ 시스템 정상 ]")
                self.update_label.setText("자동 업데이트 실패: " + result.get("error", "알 수 없는 오류"))
            return

        self._set_busy(False)
        self.refresh_summary()
        self.refresh_work_status()
        self.refresh_accounts()

        if kind == "연락처":
            QMessageBox.information(
                self, "연락처 추가 완료",
                f"추가 완료: {result['ready']}명\n실패: {result['failed']}명\n미처리/보류: {result['paused']}명\n\n"
                f"추가 완료된 {result['ready']}명은 바로 게시물 발송 가능합니다."
            )
        elif kind == "발송":
            QMessageBox.information(
                self, "게시물 발송 완료",
                f"발송 성공: {result['success']}명\n실패: {result['failed']}명\n보류: {result['remaining']}명"
            )
        elif kind == "전체":
            c = result["contact"]
            s = result["sent"]
            QMessageBox.information(
                self, "전체 작업 완료",
                f"연락처 추가: {c['ready']}명\n연락처 실패: {c['failed']}명\n"
                f"게시물 발송 성공: {s['success']}명\n게시물 발송 실패: {s['failed']}명"
            )
        else:
            QMessageBox.critical(self, "작업 오류", str(result))

    def refresh_work_status(self):
        if not hasattr(self, "work_status"):
            return
        latest = self.send_engine.latest_campaign()
        if not latest:
            self.work_status.setPlainText(
                "[대기] 생성된 작업이 없습니다.\n"
                "[순서] 고객 DB 업로드 → 연락처 추가 → 게시물 발송"
            )
            return

        ready = self.db.fetchone(
            "SELECT COUNT(*) c FROM campaign_recipients WHERE campaign_id=? AND contact_status='ADDED'",
            (latest["id"],),
        )["c"]
        sent = self.db.fetchone(
            "SELECT COUNT(*) c FROM campaign_recipients WHERE campaign_id=? AND status='MESSAGE_SENT'",
            (latest["id"],),
        )["c"]
        self.work_status.setPlainText(
            f"[작업번호] {latest['id']}\n"
            f"[작업상태] {latest['status']}\n"
            f"[전체대상] {latest['total_count']}명\n"
            f"[연락처 추가완료] {ready}명\n"
            f"[게시물 발송완료] {sent}명"
        )

    def db_page(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(self.title("고객 DB", "C:\\엔젤토글> DB 업로드 및 중복 검수"))

        b = QPushButton("[ Excel 고객 DB 업로드 ]")
        b.clicked.connect(self.import_db)
        l.addWidget(b)

        self.db_result = QLabel("업로드된 파일이 없습니다.")
        self.db_result.setObjectName("상태패널")
        l.addWidget(self.db_result)

        self.duplicate_view = QTextEdit()
        self.duplicate_view.setReadOnly(True)
        self.duplicate_view.setPlaceholderText("중복번호가 발견되면 이곳에 표시됩니다.")
        l.addWidget(self.duplicate_view)
        return w

    def import_db(self):
        path, _ = QFileDialog.getOpenFileName(self, "Excel 고객 DB 선택", "", "Excel (*.xlsx)")
        if not path:
            return

        try:
            s = self.db_import.import_xlsx(path)
            self.db_result.setText(
                f"[업로드 완료]\n"
                f"전체: {s['total']}명\n"
                f"즉시 사용 가능: {s['valid']}명\n"
                f"중복 검수: {s['duplicate']}명\n"
                f"번호 오류: {s['invalid']}명"
            )

            if s["duplicate"]:
                duplicates = self.db_import.get_duplicates(s["import_id"])
                lines = [
                    f"{idx+1}. {row['normalized_phone']} · {row['reason']}"
                    for idx, row in enumerate(duplicates)
                ]
                self.duplicate_view.setPlainText("\n".join(lines))

                box = QMessageBox(self)
                box.setWindowTitle("중복번호 검수")
                box.setIcon(QMessageBox.Question)
                box.setText(
                    f"중복된 번호 {s['duplicate']}개를 발견했습니다.\n\n"
                    f"중복번호를 제외한 {s['valid']}개 번호는 이미 바로 사용할 수 있습니다.\n"
                    "중복된 번호도 사용 DB에 추가하시겠습니까?"
                )
                add_btn = box.addButton("중복번호 추가", QMessageBox.AcceptRole)
                exclude_btn = box.addButton("중복번호 제외", QMessageBox.RejectRole)
                box.exec()

                if box.clickedButton() == add_btn:
                    added = self.db_import.approve_duplicates(s["import_id"])
                    self.logs.write("INFO", "DB", f"사용자 선택으로 중복번호 {added}개 추가")
                else:
                    self.db_import.reject_duplicates(s["import_id"])
            else:
                self.duplicate_view.setPlainText("중복번호 없음")

            self.refresh_summary()
        except Exception as e:
            QMessageBox.critical(self, "DB 업로드 오류", str(e))

    def postbot_page(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(self.title("PostBot 게시물", "C:\\엔젤토글> 게시물 설정"))

        hint = QLabel(
            "PostBot에서 만든 이미지 + 버튼 게시물을 등록합니다.\n"
            "텍스트 메시지는 사용하지 않습니다."
        )
        hint.setObjectName("상태패널")
        l.addWidget(hint)

        form = QFormLayout()
        self.postbot_username = QLineEdit(self.db.get_setting("postbot_username", "@PostBot") or "@PostBot")
        self.postbot_input = QLineEdit(self.db.get_setting("postbot_link"))
        form.addRow("PostBot 사용자명", self.postbot_username)
        form.addRow("게시물 코드 / 링크", self.postbot_input)
        l.addLayout(form)

        b = QPushButton("[ 게시물 설정 저장 ]")
        b.clicked.connect(self.save_postbot)
        l.addWidget(b)
        l.addStretch()
        return w

    def save_postbot(self):
        username = self.postbot_username.text().strip() or "@PostBot"
        self.db.set_setting("postbot_username", username)
        self.db.set_setting("postbot_link", self.postbot_input.text().strip())
        self.logs.write("INFO", "PostBot", "PostBot 게시물 설정 저장")
        QMessageBox.information(self, "저장 완료", "PostBot 게시물 설정을 저장했습니다.")

    def accounts_page(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(self.title("텔레그램 계정", "C:\\엔젤토글> 세션 관리"))

        b = QPushButton("[ 세션 ZIP 일괄등록 ]")
        b.clicked.connect(self.import_sessions)
        l.addWidget(b)

        self.account_table = QTableWidget(0, 5)
        self.account_table.setHorizontalHeaderLabels(["번호", "계정", "상태", "마지막 오류", "세션 파일"])
        self.account_table.horizontalHeader().setStretchLastSection(True)
        self.account_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        l.addWidget(self.account_table)

        row = QHBoxLayout()
        refresh = QPushButton("[ 계정 새로고침 ]")
        refresh.clicked.connect(self.refresh_accounts)
        chats = QPushButton("[ 선택 계정 대화창 보기 ]")
        chats.clicked.connect(self.open_selected_account_chats)
        reset = QPushButton("[ 선택 계정 프로그램 정지/오류 해제 ]")
        reset.clicked.connect(self.reset_selected_account)
        row.addWidget(refresh)
        row.addWidget(chats)
        row.addWidget(reset)
        l.addLayout(row)
        return w

    def open_selected_account_chats(self):
        row = self.account_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "계정 선택", "대화창을 볼 계정을 먼저 선택하세요.")
            return

        item = self.account_table.item(row, 0)
        if not item:
            return

        account_id = int(item.text())
        dialog = ChatViewerDialog(self.db, self.logs, account_id, self)
        dialog.setStyleSheet(CMD_STYLE)
        dialog.exec()

    def import_sessions(self):
        path, _ = QFileDialog.getOpenFileName(self, "세션 ZIP 선택", "", "ZIP (*.zip)")
        if not path:
            return
        try:
            s = self.session_import.import_zip(path)
            QMessageBox.information(
                self, "세션 등록 완료",
                f"등록: {s['imported']}개\n건너뜀: {s['skipped']}개"
            )
            self.refresh_accounts()
            self.refresh_summary()
        except Exception as e:
            QMessageBox.critical(self, "세션 등록 오류", str(e))

    def refresh_accounts(self):
        if not hasattr(self, "account_table"):
            return
        rows = self.db.fetchall(
            "SELECT id,name,status,last_error,session_file FROM telegram_accounts ORDER BY id"
        )
        self.account_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            vals = [row["id"], row["name"], row["status"], row["last_error"] or "", row["session_file"]]
            for c, v in enumerate(vals):
                self.account_table.setItem(r, c, QTableWidgetItem(str(v)))

    def reset_selected_account(self):
        row = self.account_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "계정 선택", "복구할 계정을 먼저 선택하세요.")
            return
        account_id = int(self.account_table.item(row, 0).text())
        self.send_engine.clear_local_account_stop(account_id)
        self.refresh_accounts()
        QMessageBox.information(
            self, "상태 해제 완료",
            "프로그램 내부 정지/오류 상태를 해제했습니다.\n"
            "다음 작업에서 실제 텔레그램 계정 상태를 다시 확인합니다."
        )

    def log_page(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(self.title("실시간 작업 로그", "C:\\엔젤토글> 작업 로그 실시간 표시"))

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas", 10))
        self.log_view.setPlainText("[대기] 작업 로그가 이곳에 표시됩니다.")
        l.addWidget(self.log_view)
        return w

    def append_log(self, item):
        if not hasattr(self, "log_view"):
            return
        if self.log_view.toPlainText().startswith("[대기]"):
            self.log_view.clear()

        account = f" / 계정 {item['account_id']}" if item.get("account_id") else ""
        campaign = f" / 작업 {item['campaign_id']}" if item.get("campaign_id") else ""
        level = {
            "INFO": "안내",
            "SUCCESS": "성공",
            "WARNING": "주의",
            "ERROR": "오류",
        }.get(item.get("level"), item.get("level"))

        self.log_view.append(
            f"[{item['time']}] [{level}] [{item['category']}]{campaign}{account} :: {item['message']}"
        )

    def settings_page(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(self.title("설정", "C:\\엔젤토글> 프로그램 설정"))

        api_group = QGroupBox("텔레그램 API 설정")
        form = QFormLayout(api_group)
        self.api_id = QLineEdit(self.db.get_setting("telegram_api_id"))
        self.api_hash = QLineEdit(self.db.get_setting("telegram_api_hash"))
        self.api_hash.setEchoMode(QLineEdit.Password)
        form.addRow("API ID", self.api_id)
        form.addRow("API HASH", self.api_hash)

        api_save = QPushButton("[ API 저장 및 고정 ]")
        api_save.clicked.connect(self.save_api)
        form.addRow(api_save)
        l.addWidget(api_group)

        work_group = QGroupBox("연락처 작업 설정")
        wf = QFormLayout(work_group)
        self.max_contacts = QSpinBox()
        self.max_contacts.setRange(1, 200)
        self.max_contacts.setValue(int(self.db.get_setting("max_contacts_per_account", "40") or 40))
        wf.addRow("계정당 최대 연락처", self.max_contacts)
        work_save = QPushButton("[ 작업 설정 저장 ]")
        work_save.clicked.connect(self.save_work_settings)
        wf.addRow(work_save)
        l.addWidget(work_group)

        update_group = QGroupBox("자동 업데이트")
        ul = QVBoxLayout(update_group)
        self.update_label = QLabel("프로그램 실행 시 새 버전을 자동 확인합니다.")
        self.update_label.setObjectName("보조")
        check = QPushButton("[ 지금 업데이트 확인 ]")
        check.clicked.connect(self.manual_update_check)
        ul.addWidget(self.update_label)
        ul.addWidget(check)
        l.addWidget(update_group)
        l.addStretch()
        return w

    def save_api(self):
        self.db.set_setting("telegram_api_id", self.api_id.text().strip())
        self.db.set_setting("telegram_api_hash", self.api_hash.text().strip())
        self.logs.write("INFO", "설정", "텔레그램 API 설정 저장")
        QMessageBox.information(self, "저장 완료", "텔레그램 API 설정을 저장했습니다.")

    def save_work_settings(self):
        self.db.set_setting("max_contacts_per_account", self.max_contacts.value())
        self.logs.write("INFO", "설정", f"계정당 최대 연락처 {self.max_contacts.value()}명으로 저장")
        QMessageBox.information(self, "저장 완료", "연락처 작업 설정을 저장했습니다.")

    def manual_update_check(self):
        self.update_label.setText("업데이트 확인 중...")
        threading.Thread(target=self._update_check_worker, daemon=True).start()

    def _update_check_worker(self):
        try:
            info = self.update_service.check_latest()
            text = (
                f"현재 버전 {info['current']} / 최신 버전 {info['latest']}"
                if info["latest"] else
                f"현재 버전 {info['current']} / 최신 버전 확인 실패"
            )
            self.bridge.작업완료.emit("업데이트확인", {"text": text, "info": info})
        except Exception as e:
            self.bridge.작업완료.emit("업데이트확인", {"text": f"업데이트 확인 실패: {e}", "info": None})

    def _apply_update_worker(self, download_url):
        try:
            self.update_service.download_and_replace(download_url)
            self.bridge.작업완료.emit("업데이트설치", {"ok": True})
        except Exception as e:
            self.bridge.작업완료.emit("업데이트설치", {"ok": False, "error": str(e)})
