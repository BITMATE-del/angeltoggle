import asyncio
import threading
from datetime import datetime

from PySide6.QtCore import Qt, QObject, Signal, QTimer
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import *

from app.services.db_import_service import DBImportService
from app.services.session_service import SessionService
from app.services.send_engine import SendEngine
from app.services.update_service import UpdateService
from app.services.completion_export_service import CompletionExportService
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
        self.completion_export = CompletionExportService(db, logs)
        self.license_info = license_info or {}

        self.bridge = 신호브리지()
        self.bridge.로그.connect(self.append_log)
        self.bridge.작업완료.connect(self.on_task_finished)
        self.logs.subscribe(lambda item: self.bridge.로그.emit(item))

        self.worker_thread = None
        self.current_campaign_id = None
        self.latest_installer_info = None
        self.update_installing = False

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
            "[안내] 프로그램 업데이트는 최신 설치마법사를 받아 기존 설치 위에 진행합니다."
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
            info = result.get("info") or {}
            self.latest_installer_info = info

            if hasattr(self, "update_button"):
                if info.get("available") and info.get("installer_url"):
                    self.update_button.setText(
                        f"[ 최신버전 {info.get('latest')} 다운로드 및 설치 ]"
                    )
                    self.update_button.setEnabled(True)
                else:
                    self.update_button.setText("[ 업데이트 확인 ]")
                    self.update_button.setEnabled(True)
            return

        if kind == "설치마법사다운로드":
            self.update_installing = False
            if result.get("ok"):
                self.update_label.setText(
                    "최신 설치마법사 다운로드 완료 / 설치마법사를 실행합니다."
                )
                try:
                    self.update_service.launch_installer(result["path"])
                    QMessageBox.information(
                        self,
                        "업데이트 설치",
                        "최신 설치마법사를 실행했습니다.\n"
                        "현재 엔젤토글을 종료하고 설치를 계속합니다."
                    )
                    QApplication.instance().quit()
                except Exception as e:
                    self.update_button.setEnabled(True)
                    self.update_label.setText(f"설치마법사 실행 실패: {e}")
                    QMessageBox.critical(self, "업데이트 오류", str(e))
            else:
                self.update_button.setEnabled(True)
                self.update_label.setText(
                    "설치마법사 다운로드 실패: " + result.get("error", "알 수 없는 오류")
                )
                QMessageBox.critical(
                    self,
                    "업데이트 다운로드 실패",
                    result.get("error", "알 수 없는 오류"),
                )
            return

        if kind == "포스트봇체크":
            if result.get("ok"):
                info = result.get("result") or {}
                text = (
                    f"[ 상태 ] 정상 · PostBot 게시물 조회 성공\n"
                    f"게시물 코드: {info.get('post_code', '')}\n"
                    f"조회 결과: {info.get('result_count', 0)}개\n"
                    f"게시물: {info.get('title', '')}"
                )
                self.postbot_status.setText(text)
                self.logs.write(
                    "SUCCESS",
                    "PostBot",
                    f"게시물 상태체크 성공 / 코드={info.get('post_code', '')} / 결과={info.get('result_count', 0)}개",
                )
            else:
                error = result.get("error", "알 수 없는 오류")
                self.postbot_status.setText(f"[ 상태 ] 오류 · {error}")
                self.logs.write("ERROR", "PostBot", f"게시물 상태체크 실패 / {error}")
            return

        self._set_busy(False)
        self.refresh_summary()
        self.refresh_work_status()
        self.refresh_accounts()
        self.refresh_completion_log()

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

        db_buttons = QHBoxLayout()

        b = QPushButton("[ 고객 DB 업로드 · Excel / TXT ]")
        b.clicked.connect(self.import_db)

        retry_failed = QPushButton("[ 실패 DB 다시 대기상태로 ]")
        retry_failed.clicked.connect(self.reset_failed_db)

        unassign_db = QPushButton("[ 배정된 DB 다시 대기상태로 ]")
        unassign_db.clicked.connect(self.reset_assigned_db)

        db_buttons.addWidget(b)
        db_buttons.addWidget(retry_failed)
        db_buttons.addWidget(unassign_db)
        l.addLayout(db_buttons)

        self.db_result = QLabel("업로드된 파일이 없습니다.")
        self.db_result.setObjectName("상태패널")
        l.addWidget(self.db_result)

        self.db_table = QTableWidget(0, 4)
        self.db_table.setHorizontalHeaderLabels(["번호", "원본 번호", "변환 번호", "상태"])
        self.db_table.horizontalHeader().setStretchLastSection(True)
        self.db_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.db_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        l.addWidget(self.db_table)
        return w

    def show_imported_db_rows(self, import_id):
        rows = self.db_import.get_import_rows(import_id)
        self.db_table.setRowCount(len(rows))

        duplicate_color = QColor(255, 90, 90)

        for idx, row in enumerate(rows):
            is_dup = row["type"] == "DUPLICATE"
            status_text = (
                f"중복 · {row['reason']}"
                if is_dup else
                "사용 가능"
            )

            values = [
                idx + 1,
                row["phone"] or "",
                row["normalized_phone"] or "",
                status_text,
            ]

            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if is_dup:
                    item.setForeground(duplicate_color)
                self.db_table.setItem(idx, col, item)

        self.db_table.resizeColumnsToContents()

    def import_db(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "고객 DB 파일 선택",
            "",
            "고객 DB (*.xlsx *.txt);;Excel (*.xlsx);;텍스트 (*.txt)"
        )
        if not path:
            return

        try:
            s = self.db_import.import_file(path)
            self.db_result.setText(
                f"[업로드 완료]\n"
                f"전체: {s['total']}명\n"
                f"즉시 사용 가능: {s['valid']}명\n"
                f"중복: {s['duplicate']}명\n"
                f"번호 오류: {s['invalid']}명"
            )

            self.show_imported_db_rows(s["import_id"])

            if s["duplicate"]:
                box = QMessageBox(self)
                box.setWindowTitle("중복번호 확인")
                box.setIcon(QMessageBox.Warning)
                box.setText(
                    f"중복 번호 {s['duplicate']}개가 있습니다.\n\n"
                    "빨간색으로 표시된 중복 번호를 제거하시겠습니까?"
                )
                remove_btn = box.addButton("중복 제거", QMessageBox.AcceptRole)
                keep_btn = box.addButton("중복 유지", QMessageBox.RejectRole)
                box.exec()

                if box.clickedButton() == remove_btn:
                    removed = self.db_import.reject_duplicates(s["import_id"])
                    self.logs.write(
                        "INFO",
                        "DB",
                        f"중복번호 {removed}개 제거",
                    )
                    QMessageBox.information(
                        self,
                        "중복 제거 완료",
                        f"중복번호 {removed}개를 제외했습니다."
                    )
                else:
                    added = self.db_import.approve_duplicates(s["import_id"])
                    self.logs.write(
                        "INFO",
                        "DB",
                        f"사용자 선택으로 중복번호 {added}개 유지",
                    )
                    QMessageBox.information(
                        self,
                        "중복 유지",
                        f"중복번호 {added}개를 고객 DB에 포함했습니다."
                    )

            self.refresh_summary()
        except Exception as e:
            QMessageBox.critical(self, "DB 업로드 오류", str(e))

    def reset_failed_db(self):
        failed = self.db.fetchone(
            "SELECT COUNT(*) c FROM recipients WHERE status='FAILED'"
        )
        count = int(failed["c"] or 0) if failed else 0

        if count <= 0:
            QMessageBox.information(
                self,
                "실패 DB 복구",
                "대기상태로 돌릴 실패 DB가 없습니다."
            )
            return

        answer = QMessageBox.question(
            self,
            "실패 DB 복구",
            f"실패한 DB {count}개를 다시 대기상태로 돌리시겠습니까?\n\n"
            "이미 연락처 추가가 성공한 DB는 연락처 상태와 기존 계정을 유지합니다.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        try:
            restored = self.send_engine.reset_failed_recipients()
            self.refresh_summary()
            self.refresh_work_status()
            QMessageBox.information(
                self,
                "복구 완료",
                f"실패 DB {restored}개를 다시 대기상태로 변경했습니다."
            )
        except Exception as e:
            QMessageBox.critical(self, "실패 DB 복구 오류", str(e))

    def reset_assigned_db(self):
        if self.worker_thread and self.worker_thread.is_alive():
            QMessageBox.warning(
                self,
                "작업 실행 중",
                "연락처 추가/발송 작업이 실행 중일 때는 배정 DB를 되돌릴 수 없습니다."
            )
            return

        active = self.db.fetchone(
            "SELECT COUNT(*) c FROM campaign_recipients cr "
            "JOIN recipients r ON r.id=cr.recipient_id "
            "WHERE cr.status!='MESSAGE_SENT' AND r.status!='MESSAGE_SENT'"
        )
        count = int(active["c"] or 0) if active else 0

        if count <= 0:
            QMessageBox.information(
                self,
                "배정 DB 복구",
                "다시 대기상태로 돌릴 배정 DB가 없습니다."
            )
            return

        answer = QMessageBox.question(
            self,
            "배정 DB 복구",
            f"현재 배정된 미완료 DB {count}건을 전부 배정취소하고 대기상태로 돌리시겠습니까?\n\n"
            "이미 MESSAGE_SENT 완료된 DB와 완료 이력은 유지됩니다.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        try:
            cancelled = self.send_engine.cancel_active_assignments()
            self.refresh_summary()
            self.refresh_work_status()
            self.refresh_completion_log()
            self.refresh_accounts()
            QMessageBox.information(
                self,
                "배정 DB 복구 완료",
                f"배정된 미완료 DB {cancelled}건을 다시 대기상태로 변경했습니다."
            )
        except Exception as e:
            QMessageBox.critical(self, "배정 DB 복구 오류", str(e))

    def postbot_page(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(self.title("PostBot 게시물", "C:\\엔젤토글> 게시물 설정 및 상태확인"))

        hint = QLabel(
            "PostBot에서 만든 이미지 + 버튼 게시물을 등록합니다.\n"
            "설정 저장 후 게시물을 실제 발송하지 않고 조회 상태만 확인할 수 있습니다."
        )
        hint.setObjectName("상태패널")
        l.addWidget(hint)

        form = QFormLayout()
        self.postbot_username = QLineEdit(
            self.db.get_setting("postbot_username", "@PostBot") or "@PostBot"
        )
        self.postbot_input = QLineEdit(self.db.get_setting("postbot_link"))
        form.addRow("PostBot 사용자명", self.postbot_username)
        form.addRow("게시물 코드 / 링크", self.postbot_input)
        l.addLayout(form)

        buttons = QHBoxLayout()

        save = QPushButton("[ 게시물 설정 저장 ]")
        save.clicked.connect(self.save_postbot)

        check = QPushButton("[ 저장 후 게시물 불러오기 · 상태체크 ]")
        check.clicked.connect(self.save_and_check_postbot)

        buttons.addWidget(save)
        buttons.addWidget(check)
        l.addLayout(buttons)

        self.postbot_status = QLabel("[ 상태 ] 아직 확인하지 않았습니다.")
        self.postbot_status.setObjectName("상태패널")
        l.addWidget(self.postbot_status)

        l.addStretch()
        return w

    def save_postbot(self, show_message=True):
        username = self.postbot_username.text().strip() or "@PostBot"
        self.db.set_setting("postbot_username", username)
        self.db.set_setting("postbot_link", self.postbot_input.text().strip())
        self.logs.write("INFO", "PostBot", "PostBot 게시물 설정 저장")
        if show_message:
            QMessageBox.information(self, "저장 완료", "PostBot 게시물 설정을 저장했습니다.")

    def save_and_check_postbot(self):
        self.save_postbot(show_message=False)

        username = self.postbot_username.text().strip() or "@PostBot"
        post_value = self.postbot_input.text().strip()

        if not post_value:
            self.postbot_status.setText("[ 상태 ] 오류 · 게시물 코드/링크가 비어 있습니다.")
            return

        account = self.db.fetchone(
            "SELECT * FROM telegram_accounts WHERE enabled=1 "
            "AND status NOT IN ('SEND_RESTRICTED','PEER_FLOOD','FLOOD_WAIT','SESSION_ERROR','STOPPED','WORKER_ERROR') "
            "ORDER BY id LIMIT 1"
        )
        if not account:
            self.postbot_status.setText("[ 상태 ] 확인 불가 · 사용 가능한 텔레그램 계정이 없습니다.")
            QMessageBox.warning(
                self,
                "PostBot 상태체크",
                "PostBot 조회에 사용할 정상 텔레그램 계정이 없습니다."
            )
            return

        self.postbot_status.setText("[ 상태 ] PostBot 게시물을 불러오는 중...")
        threading.Thread(
            target=self._postbot_check_worker,
            args=(dict(account), username, post_value),
            daemon=True,
        ).start()

    def _postbot_check_worker(self, account, username, post_value):
        async def runner():
            client = await self.send_engine.telegram.connect_account(account)
            try:
                return await self.send_engine.telegram.check_postbot(
                    client,
                    username,
                    post_value,
                )
            finally:
                await client.disconnect()

        try:
            result = asyncio.run(runner())
            self.bridge.작업완료.emit("포스트봇체크", {"ok": True, "result": result})
        except Exception as e:
            self.bridge.작업완료.emit(
                "포스트봇체크",
                {"ok": False, "error": f"{type(e).__name__}: {e}"}
            )

    def accounts_page(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(self.title("텔레그램 계정", "C:\\엔젤토글> 세션 관리"))

        top_buttons = QHBoxLayout()

        add = QPushButton("[ 세션 ZIP / SESSION 파일 등록 ]")
        add.clicked.connect(self.import_sessions)

        unassign = QPushButton("[ 체크 계정 배정취소 ]")
        unassign.clicked.connect(self.unassign_checked_accounts)

        bulk_delete = QPushButton("[ 체크 계정 일괄 삭제 ]")
        bulk_delete.clicked.connect(self.delete_checked_accounts)

        top_buttons.addWidget(add)
        top_buttons.addWidget(unassign)
        top_buttons.addWidget(bulk_delete)
        l.addLayout(top_buttons)

        self.account_table = QTableWidget(0, 6)
        self.account_table.setHorizontalHeaderLabels(
            ["선택", "번호", "계정", "상태", "마지막 오류", "세션 파일"]
        )
        self.account_table.horizontalHeader().setStretchLastSection(True)
        self.account_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.account_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.account_table.setColumnWidth(0, 58)
        l.addWidget(self.account_table)

        row = QHBoxLayout()

        select_all = QPushButton("[ 전체 체크 ]")
        select_all.clicked.connect(lambda: self.set_all_account_checks(True))

        clear_all = QPushButton("[ 전체 체크 해제 ]")
        clear_all.clicked.connect(lambda: self.set_all_account_checks(False))

        refresh = QPushButton("[ 계정 새로고침 ]")
        refresh.clicked.connect(self.refresh_accounts)

        chats = QPushButton("[ 선택 계정 대화창 보기 ]")
        chats.clicked.connect(self.open_selected_account_chats)

        reset = QPushButton("[ 선택 계정 오류 해제 ]")
        reset.clicked.connect(self.reset_selected_account)

        delete = QPushButton("[ 선택 계정 삭제 ]")
        delete.clicked.connect(self.delete_selected_account)

        row.addWidget(select_all)
        row.addWidget(clear_all)
        row.addWidget(refresh)
        row.addWidget(chats)
        row.addWidget(reset)
        row.addWidget(delete)
        l.addLayout(row)

        info = QLabel(
            "배정취소는 아직 완료되지 않은 작업만 해제하며, 발송 완료 이력은 유지됩니다.\n"
            "계정 삭제 시 진행 중 배정이 있으면 먼저 배정취소 후 삭제할 수 있습니다."
        )
        info.setObjectName("보조")
        l.addWidget(info)

        return w

    def _current_account_id(self):
        row = self.account_table.currentRow()
        if row < 0:
            return None
        item = self.account_table.item(row, 1)
        if not item:
            return None
        try:
            return int(item.text())
        except Exception:
            return None

    def checked_account_ids(self):
        ids = []
        for row in range(self.account_table.rowCount()):
            item = self.account_table.item(row, 0)
            id_item = self.account_table.item(row, 1)
            if (
                item
                and id_item
                and item.checkState() == Qt.Checked
            ):
                try:
                    ids.append(int(id_item.text()))
                except Exception:
                    pass
        return ids

    def set_all_account_checks(self, checked):
        state = Qt.Checked if checked else Qt.Unchecked
        for row in range(self.account_table.rowCount()):
            item = self.account_table.item(row, 0)
            if item:
                item.setCheckState(state)

    def open_selected_account_chats(self):
        account_id = self._current_account_id()
        if account_id is None:
            QMessageBox.information(self, "계정 선택", "대화창을 볼 계정을 먼저 선택하세요.")
            return

        dialog = ChatViewerDialog(self.db, self.logs, account_id, self)
        dialog.setStyleSheet(CMD_STYLE)
        dialog.exec()

    def unassign_checked_accounts(self):
        if self.worker_thread and self.worker_thread.is_alive():
            QMessageBox.warning(
                self,
                "작업 실행 중",
                "연락처 추가/발송 작업이 실행 중일 때는 배정취소할 수 없습니다."
            )
            return

        account_ids = self.checked_account_ids()
        if not account_ids:
            QMessageBox.information(self, "계정 체크", "배정취소할 계정을 체크해주세요.")
            return

        active = self.db.fetchone(
            "SELECT COUNT(*) c FROM campaign_recipients "
            "WHERE assigned_account_id IN (" + ",".join("?" for _ in account_ids) + ") "
            "AND status!='MESSAGE_SENT'",
            tuple(account_ids),
        )
        count = int(active["c"] or 0) if active else 0

        if count <= 0:
            QMessageBox.information(
                self,
                "배정취소",
                "체크한 계정에 취소할 진행 중 배정이 없습니다."
            )
            return

        answer = QMessageBox.question(
            self,
            "배정취소",
            f"체크한 계정의 진행 중 배정 {count}건을 취소하시겠습니까?\n\n"
            "해당 고객 DB는 다시 대기상태로 돌아가며, 이미 발송 완료된 이력은 유지됩니다.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        try:
            cancelled = self.send_engine.cancel_active_assignments(
                account_ids=account_ids
            )
            self.refresh_accounts()
            self.refresh_summary()
            self.refresh_work_status()
            self.refresh_completion_log()
            QMessageBox.information(
                self,
                "배정취소 완료",
                f"진행 중 배정 {cancelled}건을 취소하고 고객 DB를 대기상태로 돌렸습니다."
            )
        except Exception as e:
            QMessageBox.critical(self, "배정취소 오류", str(e))

    def _delete_accounts_with_optional_unassign(self, account_ids):
        if self.worker_thread and self.worker_thread.is_alive():
            raise RuntimeError("작업 실행 중에는 계정을 삭제할 수 없습니다.")

        account_ids = [int(x) for x in account_ids]
        if not account_ids:
            return {"deleted": 0, "session_failed": 0, "unassigned": 0}

        marks = ",".join("?" for _ in account_ids)
        active = self.db.fetchone(
            f"SELECT COUNT(*) c FROM campaign_recipients "
            f"WHERE assigned_account_id IN ({marks}) AND status!='MESSAGE_SENT'",
            tuple(account_ids),
        )
        active_count = int(active["c"] or 0) if active else 0

        unassigned = 0
        if active_count > 0:
            answer = QMessageBox.question(
                self,
                "배정된 계정",
                f"선택한 계정에 진행 중 배정 {active_count}건이 있습니다.\n\n"
                "배정을 취소하고 계정을 삭제하시겠습니까?\n"
                "고객 DB는 다시 대기상태로 돌아갑니다.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return None

            unassigned = self.send_engine.cancel_active_assignments(
                account_ids=account_ids
            )

        batch = self.session_import.delete_accounts_batch(account_ids)

        return {
            "deleted": int(batch.get("deleted", 0) or 0),
            "session_failed": int(batch.get("session_failed", 0) or 0),
            "missing": int(batch.get("missing", 0) or 0),
            "unassigned": unassigned,
        }

    def delete_selected_account(self):
        account_id = self._current_account_id()
        if account_id is None:
            QMessageBox.information(self, "계정 선택", "삭제할 계정을 먼저 선택하세요.")
            return

        row = self.account_table.currentRow()
        account_name_item = self.account_table.item(row, 2)
        account_name = account_name_item.text() if account_name_item else str(account_id)

        answer = QMessageBox.question(
            self,
            "계정 삭제",
            f"선택한 계정 [{account_name}]을 삭제하시겠습니까?\n\n"
            "등록된 세션파일도 함께 삭제됩니다.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        try:
            result = self._delete_accounts_with_optional_unassign([account_id])
            if result is None:
                return

            self.refresh_accounts()
            self.refresh_summary()
            self.refresh_work_status()
            self.refresh_completion_log()

            msg = f"계정 {result['deleted']}개를 삭제했습니다."
            if result["unassigned"]:
                msg += f"\n진행 중 배정 {result['unassigned']}건을 취소했습니다."
            if result["session_failed"]:
                msg += f"\n세션파일 {result['session_failed']}개는 삭제하지 못했습니다."
            if result.get("missing"):
                msg += f"\n이미 없던 계정 {result['missing']}개"

            QMessageBox.information(self, "계정 삭제 완료", msg)
        except Exception as e:
            QMessageBox.critical(self, "계정 삭제 오류", str(e))

    def delete_checked_accounts(self):
        account_ids = self.checked_account_ids()
        if not account_ids:
            QMessageBox.information(self, "계정 체크", "삭제할 계정을 체크해주세요.")
            return

        answer = QMessageBox.question(
            self,
            "계정 일괄 삭제",
            f"체크한 계정 {len(account_ids)}개를 일괄 삭제하시겠습니까?\n\n"
            "진행 중 배정이 있으면 배정취소 여부를 한 번 더 확인합니다.\n"
            "등록된 세션파일도 함께 삭제됩니다.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        try:
            result = self._delete_accounts_with_optional_unassign(account_ids)
            if result is None:
                return

            self.refresh_accounts()
            self.refresh_summary()
            self.refresh_work_status()
            self.refresh_completion_log()

            msg = f"계정 {result['deleted']}개를 삭제했습니다."
            if result["unassigned"]:
                msg += f"\n진행 중 배정 {result['unassigned']}건을 취소했습니다."
            if result["session_failed"]:
                msg += f"\n세션파일 {result['session_failed']}개는 삭제하지 못했습니다."
            if result.get("missing"):
                msg += f"\n이미 없던 계정 {result['missing']}개"

            QMessageBox.information(self, "일괄 삭제 완료", msg)
        except Exception as e:
            QMessageBox.critical(self, "일괄 삭제 오류", str(e))

    def import_sessions(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "텔레그램 세션 파일 여러 개 선택",
            "",
            "텔레그램 세션 (*.zip *.session);;ZIP 압축 (*.zip);;SESSION 파일 (*.session)"
        )
        if not paths:
            return

        total = {
            "found": 0,
            "imported": 0,
            "renamed": 0,
            "failed": 0,
            "skipped": 0,
        }

        try:
            for path in paths:
                try:
                    if path.lower().endswith(".session"):
                        s = self.session_import.import_session_file(path)
                    else:
                        s = self.session_import.import_zip(path)

                    for key in total:
                        total[key] += int(s.get(key, 0) or 0)

                except Exception as e:
                    total["failed"] += 1
                    self.logs.write(
                        "ERROR",
                        "ACCOUNT",
                        f"세션 파일 등록 실패: {path} / {type(e).__name__}: {e}",
                    )

            QMessageBox.information(
                self,
                "세션 일괄등록 완료",
                f"선택 파일: {len(paths)}개\n"
                f"발견: {total['found']}개\n"
                f"등록: {total['imported']}개\n"
                f"동일 이름 자동변경: {total['renamed']}개\n"
                f"실패: {total['failed']}개"
            )
            self.refresh_accounts()
            self.refresh_summary()

        except Exception as e:
            QMessageBox.critical(self, "세션 등록 오류", str(e))

    def refresh_accounts(self):
        if not hasattr(self, "account_table"):
            return

        checked_before = set(self.checked_account_ids()) if self.account_table.rowCount() else set()

        rows = self.db.fetchall(
            "SELECT id,name,status,last_error,session_file FROM telegram_accounts ORDER BY id"
        )
        self.account_table.setRowCount(len(rows))

        for r, row in enumerate(rows):
            check_item = QTableWidgetItem("")
            check_item.setFlags(
                Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
            )
            check_item.setCheckState(
                Qt.Checked if int(row["id"]) in checked_before else Qt.Unchecked
            )
            self.account_table.setItem(r, 0, check_item)

            vals = [
                row["id"],
                row["name"],
                row["status"],
                row["last_error"] or "",
                row["session_file"],
            ]
            for c, v in enumerate(vals, start=1):
                self.account_table.setItem(r, c, QTableWidgetItem(str(v)))

        self.account_table.resizeColumnsToContents()
        self.account_table.setColumnWidth(0, 58)

    def reset_selected_account(self):
        account_id = self._current_account_id()
        if account_id is None:
            QMessageBox.information(self, "계정 선택", "복구할 계정을 먼저 선택하세요.")
            return

        self.send_engine.clear_local_account_stop(account_id)
        self.refresh_accounts()
        QMessageBox.information(
            self,
            "상태 해제 완료",
            "프로그램 내부 정지/오류 상태를 해제했습니다.\n"
            "다음 작업에서 실제 텔레그램 계정 상태를 다시 확인합니다."
        )

    def log_page(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(self.title("작업 로그", "C:\\엔젤토글> 실시간 로그 + 작업완료 DB"))

        live_group = QGroupBox("실시간 작업 로그")
        live_layout = QVBoxLayout(live_group)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas", 10))
        self.log_view.setMaximumHeight(220)
        self.log_view.setPlainText("[대기] 작업 로그가 이곳에 표시됩니다.")
        live_layout.addWidget(self.log_view)
        l.addWidget(live_group)

        complete_group = QGroupBox("작업완료 로그")
        complete_layout = QVBoxLayout(complete_group)

        top = QHBoxLayout()
        self.completion_label = QLabel("완료된 작업이 없습니다.")
        self.completion_label.setObjectName("보조")

        refresh = QPushButton("[ 작업완료 로그 새로고침 ]")
        refresh.clicked.connect(self.refresh_completion_log)

        export = QPushButton("[ 작업완료 DB Excel 다운로드 ]")
        export.clicked.connect(self.export_completion_db)

        top.addWidget(self.completion_label, 1)
        top.addWidget(refresh)
        top.addWidget(export)
        complete_layout.addLayout(top)

        self.completion_table = QTableWidget(0, 7)
        self.completion_table.setHorizontalHeaderLabels([
            "DB",
            "전화번호",
            "연락처",
            "연락처 추가",
            "메시지 전송",
            "성공시간",
            "PostBot 고유번호",
        ])
        self.completion_table.horizontalHeader().setStretchLastSection(True)
        self.completion_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.completion_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        complete_layout.addWidget(self.completion_table)

        l.addWidget(complete_group, 1)

        QTimer.singleShot(0, self.refresh_completion_log)
        return w

    def refresh_completion_log(self):
        if not hasattr(self, "completion_table"):
            return

        campaign = self.send_engine.latest_campaign()
        if not campaign:
            self.completion_label.setText("완료된 작업이 없습니다.")
            self.completion_table.setRowCount(0)
            return

        rows = self.completion_export.completion_rows(campaign["id"])
        self.completion_label.setText(
            f"작업 #{campaign['id']} / 상태 {campaign['status']} / 대상 {campaign['total_count']}명"
        )
        self.completion_table.setRowCount(len(rows))

        for idx, row in enumerate(rows):
            values = [
                row["db_no"],
                row["phone"],
                row["contact_name"],
                row["contact_status"],
                row["send_status"],
                row["send_time"] or row["contact_time"],
                row["postbot_code"],
            ]

            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value or ""))
                self.completion_table.setItem(idx, col, item)

        self.completion_table.resizeColumnsToContents()

    def export_completion_db(self):
        campaign = self.send_engine.latest_campaign()
        if not campaign:
            QMessageBox.information(self, "작업완료 DB", "다운로드할 작업이 없습니다.")
            return

        default_name = f"AngelToggle_작업완료_{campaign['id']}.xlsx"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "작업완료 DB 저장",
            default_name,
            "Excel (*.xlsx)"
        )
        if not path:
            return

        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"

        try:
            saved = self.completion_export.export_xlsx(campaign["id"], path)
            QMessageBox.information(
                self,
                "다운로드 완료",
                f"작업완료 DB를 저장했습니다.\n\n{saved}"
            )
        except Exception as e:
            QMessageBox.critical(self, "작업완료 DB 오류", str(e))

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

        update_group = QGroupBox("프로그램 업데이트")
        ul = QVBoxLayout(update_group)
        self.update_label = QLabel("새 버전은 설치마법사로 업데이트합니다.")
        self.update_label.setObjectName("보조")
        self.update_button = QPushButton("[ 업데이트 확인 ]")
        self.update_button.clicked.connect(self.update_button_clicked)
        ul.addWidget(self.update_label)
        ul.addWidget(self.update_button)
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

    def update_button_clicked(self):
        info = self.latest_installer_info or {}
        if info.get("available") and info.get("installer_url"):
            self.start_installer_update()
        else:
            self.manual_update_check()

    def manual_update_check(self):
        if self.update_installing:
            return
        self.update_label.setText("최신 버전 확인 중...")
        if hasattr(self, "update_button"):
            self.update_button.setEnabled(False)
        threading.Thread(target=self._update_check_worker, daemon=True).start()

    def _update_check_worker(self):
        try:
            info = self.update_service.check_latest()
            if info.get("latest"):
                if info.get("available"):
                    text = (
                        f"현재 버전 {info['current']} / 최신 버전 {info['latest']} "
                        f"/ 업데이트 가능"
                    )
                else:
                    text = (
                        f"현재 버전 {info['current']} / 최신 버전 {info['latest']} "
                        f"/ 최신 버전입니다."
                    )
            else:
                text = f"현재 버전 {info['current']} / 최신 버전 확인 실패"
            self.bridge.작업완료.emit("업데이트확인", {"text": text, "info": info})
        except Exception as e:
            self.bridge.작업완료.emit(
                "업데이트확인",
                {"text": f"업데이트 확인 실패: {e}", "info": None},
            )

    def start_installer_update(self):
        if self.update_installing:
            return

        info = self.latest_installer_info or {}
        url = info.get("installer_url")
        latest = info.get("latest")
        if not url:
            QMessageBox.warning(
                self,
                "업데이트 확인",
                "최신 설치마법사 정보를 먼저 확인해주세요.",
            )
            self.manual_update_check()
            return

        self.update_installing = True
        self.update_button.setEnabled(False)
        self.update_label.setText(
            f"최신 버전 {latest} 설치마법사를 다운로드하고 있습니다..."
        )

        threading.Thread(
            target=self._download_installer_worker,
            args=(url, latest),
            daemon=True,
        ).start()

    def _download_installer_worker(self, url, latest):
        try:
            path = self.update_service.download_installer(url, latest)
            self.bridge.작업완료.emit(
                "설치마법사다운로드",
                {"ok": True, "path": path},
            )
        except Exception as e:
            self.bridge.작업완료.emit(
                "설치마법사다운로드",
                {"ok": False, "error": str(e)},
            )
