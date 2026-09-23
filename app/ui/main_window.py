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
from app.services.telegram_check_service import TelegramCheckService, TelegramCheckError
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
QTabWidget::pane {
    border: 1px solid #1C5A33;
    background-color: #050706;
    top: -1px;
}
QTabBar::tab {
    background-color: #0A160F;
    color: #8EF7AA;
    border: 1px solid #1C5A33;
    padding: 9px 14px;
    margin-right: 2px;
    min-width: 92px;
    font-weight: 800;
}
QTabBar::tab:selected {
    background-color: #12361F;
    color: #FFFFFF;
    border-color: #2CE66D;
}
QTabBar::tab:hover {
    background-color: #0F2A19;
    color: #FFFFFF;
}
QLabel#진행카드 {
    background-color: #07100B;
    border: 1px solid #1C5A33;
    border-radius: 8px;
    padding: 10px 12px;
    color: #BFFFD0;
    font-weight: 700;
}
QProgressBar {
    background-color: #020403;
    color: #D9FFE7;
    border: 1px solid #1C5A33;
    border-radius: 7px;
    text-align: center;
    min-height: 24px;
    font-weight: 800;
}
QProgressBar::chunk {
    background-color: #1BAF55;
    border-radius: 6px;
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
        self.telegram_check = TelegramCheckService(db, logs)
        self.license_info = license_info or {}

        self.bridge = 신호브리지()
        self.bridge.로그.connect(self.append_log)
        self.bridge.작업완료.connect(self.on_task_finished)
        self.logs.subscribe(lambda item: self.bridge.로그.emit(item))

        self.worker_thread = None
        self.current_campaign_id = None
        self.current_telegram_check_task_id = None
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
            "> 텔레그램 가입자 검수",
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
        self.pages.addWidget(self.telegram_check_page())
        self.pages.addWidget(self.log_page())
        self.pages.addWidget(self.settings_page())

        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)

        self.send_engine.rebalance_account_sectors()
        self.refresh_summary()
        self.refresh_accounts()
        self.refresh_work_status()
        self.refresh_point_balance_async()

        self.progress_timer = QTimer(self)
        self.progress_timer.timeout.connect(self.refresh_live_progress)
        self.progress_timer.start(800)
        QTimer.singleShot(0, self.refresh_progress_sectors)

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
        pending = self.db.fetchone(
            "SELECT COUNT(*) c FROM recipients WHERE status='PENDING'"
        )["c"]
        assigned = self.db.fetchone(
            "SELECT COUNT(*) c FROM recipients "
            "WHERE status IN ('ASSIGNED','SENDING','SEND_PAUSED')"
        )["c"]
        reassign_waiting = self.db.fetchone(
            "SELECT COUNT(*) c FROM recipients WHERE status='REASSIGN_WAITING'"
        )["c"]
        manual_check = self.db.fetchone(
            "SELECT COUNT(*) c FROM recipients "
            "WHERE status IN ('REASSIGN_BLOCKED','UNCERTAIN')"
        )["c"]
        final_failed = self.db.fetchone(
            "SELECT COUNT(*) c FROM recipients "
            "WHERE status IN ('FAILED','FAILED_FINAL')"
        )["c"]
        added = self.db.fetchone(
            "SELECT COUNT(*) c FROM recipients WHERE contact_status='ADDED'"
        )["c"]
        sent = self.db.fetchone(
            "SELECT COUNT(*) c FROM recipients WHERE status='MESSAGE_SENT'"
        )["c"]
        unsent = max(0, int(rc or 0) - int(sent or 0))
        remaining = self.license_info.get("remaining_days")
        expires_at = self._format_license_expiry(self.license_info.get("expires_at"))
        offline = "오프라인 유예" if self.license_info.get("offline") else "서버 인증"
        remaining_text = f"{remaining}일" if remaining is not None else "확인 필요"
        point_balance = int(self.license_info.get("point_balance_krw") or 0)
        unit_price = int(self.license_info.get("send_unit_price_krw") or 10)

        self.summary.setText(
            f"[등록 계정]       {ac:>7}개\n"
            f"[전체 고객 DB]    {rc:>7}명\n"
            f"[미발송 DB]       {unsent:>7}명\n"
            f"[즉시 대기 DB]    {pending:>7}명\n"
            f"[배정/진행 DB]    {assigned:>7}명\n"
            f"[재배정 대기]     {reassign_waiting:>7}명\n"
            f"[수동 확인]       {manual_check:>7}명\n"
            f"[실패/최종실패]   {final_failed:>7}명\n"
            f"[연락처 추가완료] {added:>7}명\n"
            f"[게시물 발송완료] {sent:>7}명\n"
            f"[발송포인트]      {point_balance:>7,}원 · 건당 {unit_price}원\n"
            f"[라이선스]        {remaining_text:>7} 남음\n"
            f"[만료일]          {expires_at}\n"
            f"[인증상태]        {offline}\n\n"
            f"C:\\엔젤토글> 시스템 상태 = 정상"
        )

    def work_page(self):
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        content = QWidget()
        l = QVBoxLayout(content)
        l.setContentsMargins(10, 8, 10, 12)
        l.setSpacing(10)

        l.addWidget(self.title("작업 실행", "C:\\엔젤토글> 연락처 추가 -> UID 확인 -> PostBot 발송"))

        info = QLabel(
            "발송 작업을 시작하면 아래 실시간 진행 로그에서 현재 처리 단계를 바로 확인할 수 있습니다.\n"
            "연락처 추가 → UID Resolve/중복확인 → PostBot Inline Query → 전송 → message_id 확인 순서로 표시됩니다."
        )
        info.setObjectName("상태패널")
        info.setMinimumHeight(68)
        info.setWordWrap(True)
        l.addWidget(info)

        progress_group = QGroupBox("현재 진행현황")
        progress_group.setMinimumHeight(185)
        progress_layout = QVBoxLayout(progress_group)
        progress_layout.setContentsMargins(10, 18, 10, 10)
        progress_layout.setSpacing(10)

        cards = QHBoxLayout()
        cards.setSpacing(7)

        self.progress_total = QLabel("전체 대상\n0")
        self.progress_contact = QLabel("연락처 완료\n0")
        self.progress_uid = QLabel("UID 확인\n0")
        self.progress_sent = QLabel("발송 성공\n0")
        self.progress_failed = QLabel("실패\n0")
        self.progress_remaining = QLabel("잔여/보류\n0")

        for card in [
            self.progress_total,
            self.progress_contact,
            self.progress_uid,
            self.progress_sent,
            self.progress_failed,
            self.progress_remaining,
        ]:
            card.setObjectName("진행카드")
            card.setAlignment(Qt.AlignCenter)
            card.setMinimumHeight(76)
            card.setMinimumWidth(110)
            card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            cards.addWidget(card)

        progress_layout.addLayout(cards)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("진행률 0.0%")
        self.progress_bar.setMinimumHeight(30)
        self.progress_bar.setMaximumHeight(30)
        progress_layout.addWidget(self.progress_bar)

        detail_row = QHBoxLayout()
        detail_row.setSpacing(14)

        self.progress_stage = QLabel("[현재 단계] 대기")
        self.progress_stage.setObjectName("보조")
        self.progress_stage.setMinimumHeight(22)

        self.progress_accounts = QLabel("[작업 계정] 0개")
        self.progress_accounts.setObjectName("보조")
        self.progress_accounts.setMinimumHeight(22)

        self.progress_points = QLabel("[포인트] 사용 0원 / 남은 예상 0원")
        self.progress_points.setObjectName("보조")
        self.progress_points.setMinimumHeight(22)

        detail_row.addWidget(self.progress_stage, 2)
        detail_row.addWidget(self.progress_accounts, 1)
        detail_row.addWidget(self.progress_points, 2)
        progress_layout.addLayout(detail_row)

        l.addWidget(progress_group)

        self.work_status = QTextEdit()
        self.work_status.setReadOnly(True)
        self.work_status.setMinimumHeight(92)
        self.work_status.setMaximumHeight(115)
        l.addWidget(self.work_status)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        self.contact_button = QPushButton("[ 1단계 · 연락처 추가 시작 ]")
        self.contact_button.setMinimumHeight(48)
        self.contact_button.clicked.connect(self.start_contact_stage)

        self.send_button = QPushButton("[ 2단계 · 게시물 발송 시작 ]")
        self.send_button.setMinimumHeight(48)
        self.send_button.clicked.connect(self.start_send_stage)

        self.auto_button = QPushButton("[ 전체 자동 진행 ]")
        self.auto_button.setMinimumHeight(48)
        self.auto_button.clicked.connect(self.start_full_auto)

        action_row.addWidget(self.contact_button)
        action_row.addWidget(self.send_button)
        action_row.addWidget(self.auto_button)
        l.addLayout(action_row)

        retry_row = QHBoxLayout()
        retry_row.setSpacing(8)

        self.retry_failed_button = QPushButton("[ 실패 0건만 다시 재시도 ]")
        self.retry_failed_button.setMinimumHeight(48)
        self.retry_failed_button.setEnabled(False)
        self.retry_failed_button.clicked.connect(self.start_failed_retry)

        self.retry_history_label = QLabel("[재시도 이력] 없음")
        self.retry_history_label.setObjectName("보조")
        self.retry_history_label.setMinimumHeight(28)
        self.retry_history_label.setWordWrap(True)

        retry_row.addWidget(self.retry_failed_button)
        retry_row.addWidget(self.retry_history_label, 1)
        l.addLayout(retry_row)

        sector_group = QGroupBox("병렬 진행창 관리")
        sector_group.setMinimumHeight(175)
        sector_layout = QVBoxLayout(sector_group)
        sector_layout.setContentsMargins(10, 18, 10, 10)
        sector_layout.setSpacing(8)

        sector_top = QHBoxLayout()
        self.sector_summary = QLabel("진행창 정보를 불러오는 중입니다.")
        self.sector_summary.setObjectName("보조")
        self.sector_summary.setMinimumHeight(32)

        add_sector = QPushButton("[ 진행창 추가 ]")
        add_sector.clicked.connect(self.add_progress_sector)

        remove_sector = QPushButton("[ 마지막 진행창 삭제 ]")
        remove_sector.clicked.connect(self.remove_progress_sector)

        sector_top.addWidget(self.sector_summary, 1)
        sector_top.addWidget(add_sector)
        sector_top.addWidget(remove_sector)
        sector_layout.addLayout(sector_top)

        self.sector_tabs = QTabWidget()
        self.sector_tabs.setMinimumHeight(100)
        self.sector_tabs.setMaximumHeight(135)
        sector_layout.addWidget(self.sector_tabs)

        l.addWidget(sector_group)

        live_group = QGroupBox("실시간 작업 진행 로그")
        live_group.setMinimumHeight(260)
        live_layout = QVBoxLayout(live_group)
        live_layout.setContentsMargins(10, 18, 10, 10)
        live_layout.setSpacing(8)

        live_top = QHBoxLayout()
        self.work_live_state = QLabel("[ 대기 ] 작업을 시작하면 진행 상황이 표시됩니다.")
        self.work_live_state.setObjectName("보조")
        self.work_live_state.setMinimumHeight(30)

        clear_live = QPushButton("[ 로그 지우기 ]")
        clear_live.clicked.connect(self.clear_work_live_log)

        live_top.addWidget(self.work_live_state, 1)
        live_top.addWidget(clear_live)
        live_layout.addLayout(live_top)

        self.work_live_log = QTextEdit()
        self.work_live_log.setReadOnly(True)
        self.work_live_log.setFont(QFont("Consolas", 10))
        self.work_live_log.setMinimumHeight(185)
        self.work_live_log.setPlainText(
            "[대기] 발송 시작 버튼을 누르면 이 화면에서 실시간 로그를 확인할 수 있습니다."
        )
        live_layout.addWidget(self.work_live_log)

        l.addWidget(live_group)
        l.addStretch(1)

        scroll.setWidget(content)
        outer.addWidget(scroll)
        return w

    def refresh_live_progress(self):
        if not hasattr(self, "progress_bar"):
            return

        campaign = None
        if self.current_campaign_id:
            campaign = self.db.fetchone(
                "SELECT * FROM campaigns WHERE id=?",
                (self.current_campaign_id,),
            )
        if not campaign:
            campaign = self.send_engine.latest_campaign()
        if not campaign:
            return

        campaign_id = int(campaign["id"])
        stats = self.db.fetchone(
            "SELECT "
            "COUNT(*) total, "
            "SUM(CASE WHEN cr.contact_status='ADDED' THEN 1 ELSE 0 END) contact_done, "
            "SUM(CASE WHEN r.telegram_uid IS NOT NULL AND r.telegram_uid!='' THEN 1 ELSE 0 END) uid_done, "
            "SUM(CASE WHEN cr.status='MESSAGE_SENT' THEN 1 ELSE 0 END) sent, "
            "SUM(CASE WHEN cr.status='FAILED' OR cr.contact_status='FAILED' THEN 1 ELSE 0 END) failed, "
            "SUM(CASE WHEN cr.status IN ('ASSIGNED','SENDING','SEND_PAUSED','UNCERTAIN') "
            "OR cr.contact_status IN ('WAITING','ADDING','CONTACT_PAUSED','CONTACT_UNCERTAIN') THEN 1 ELSE 0 END) remaining "
            "FROM campaign_recipients cr "
            "JOIN recipients r ON r.id=cr.recipient_id "
            "WHERE cr.campaign_id=?",
            (campaign_id,),
        )

        total = int(stats["total"] or 0)
        contact_done = int(stats["contact_done"] or 0)
        uid_done = int(stats["uid_done"] or 0)
        sent = int(stats["sent"] or 0)
        failed = int(stats["failed"] or 0)
        remaining = max(0, total - sent - failed)

        if total > 0:
            progress = min(100.0, ((sent + failed) / total) * 100.0)
        else:
            progress = 0.0

        self.progress_total.setText(f"전체 대상\n{total:,}")
        self.progress_contact.setText(f"연락처 완료\n{contact_done:,}")
        self.progress_uid.setText(f"UID 확인\n{uid_done:,}")
        self.progress_sent.setText(f"발송 성공\n{sent:,}")
        self.progress_failed.setText(f"실패\n{failed:,}")
        self.progress_remaining.setText(f"잔여/보류\n{remaining:,}")

        self.progress_bar.setValue(int(progress * 10))
        self.progress_bar.setFormat(f"진행률 {progress:.1f}% · {sent + failed:,}/{total:,} 처리")

        status = str(campaign["status"] or "")
        stage_map = {
            "CONTACT_WAITING": "연락처 추가 대기",
            "CONTACT_RUNNING": "연락처 추가 + UID 확인 중",
            "CONTACT_DONE": "연락처/UID 준비 완료",
            "SEND_RUNNING": "PostBot 게시물 발송 중",
            "COMPLETED": "작업 완료",
            "PARTIAL": "일부 완료 · 잔여 확인 필요",
            "CANCELLED": "작업 취소",
        }
        self.progress_stage.setText(
            f"[현재 단계] {stage_map.get(status, status or '대기')} · 작업 #{campaign_id}"
        )

        account_row = self.db.fetchone(
            "SELECT COUNT(DISTINCT assigned_account_id) c "
            "FROM campaign_recipients "
            "WHERE campaign_id=? "
            "AND (campaign_recipients.status IN ('ASSIGNED','SENDING','SEND_PAUSED') "
            "OR campaign_recipients.contact_status IN ('WAITING','ADDING','CONTACT_PAUSED'))",
            (campaign_id,),
        )
        active_accounts = int(account_row["c"] or 0) if account_row else 0
        self.progress_accounts.setText(f"[작업 계정] {active_accounts}개")

        unit_price = int(self.license_info.get("send_unit_price_krw") or 10)
        used_points = sent * unit_price
        remaining_send = max(0, total - sent - failed)
        expected_points = remaining_send * unit_price
        self.progress_points.setText(
            f"[포인트] 사용 {used_points:,}원 / 남은 예상 {expected_points:,}원"
        )

        self.refresh_progress_sectors()

    def refresh_progress_sectors(self):
        if not hasattr(self, "sector_tabs"):
            return

        sector_count = int(self.send_engine.sector_count() or 1)
        total_row = self.db.fetchone("SELECT COUNT(*) c FROM telegram_accounts")
        total_accounts = int(total_row["c"] or 0) if total_row else 0

        self.sector_summary.setText(
            f"진행창 {sector_count}개 · 등록 계정 {total_accounts}개 · 진행창당 최대 10개 계정"
        )

        current_index = self.sector_tabs.currentIndex()
        self.sector_tabs.clear()

        campaign = None
        if self.current_campaign_id:
            campaign = self.db.fetchone(
                "SELECT id FROM campaigns WHERE id=?",
                (self.current_campaign_id,),
            )
        if not campaign:
            campaign = self.send_engine.latest_campaign()
        campaign_id = int(campaign["id"]) if campaign else None

        for sector_id in range(1, sector_count + 1):
            page = QWidget()
            layout = QVBoxLayout(page)

            accounts = self.db.fetchall(
                "SELECT id,name,phone,status,last_error "
                "FROM telegram_accounts WHERE worker_sector=? ORDER BY id",
                (sector_id,),
            )

            working = 0
            sent = 0
            failed = 0
            if campaign_id and accounts:
                ids = [int(a["id"]) for a in accounts]
                marks = ",".join("?" for _ in ids)
                stats = self.db.fetchone(
                    "SELECT "
                    "SUM(CASE WHEN campaign_recipients.status IN ('ASSIGNED','SENDING','SEND_PAUSED') "
                    "OR campaign_recipients.contact_status IN ('WAITING','ADDING','CONTACT_PAUSED') THEN 1 ELSE 0 END) working,"
                    "SUM(CASE WHEN campaign_recipients.status='MESSAGE_SENT' THEN 1 ELSE 0 END) sent,"
                    "SUM(CASE WHEN campaign_recipients.status='FAILED' OR campaign_recipients.contact_status='FAILED' THEN 1 ELSE 0 END) failed "
                    f"FROM campaign_recipients WHERE campaign_id=? "
                    f"AND assigned_account_id IN ({marks})",
                    (campaign_id, *ids),
                )
                if stats:
                    working = int(stats["working"] or 0)
                    sent = int(stats["sent"] or 0)
                    failed = int(stats["failed"] or 0)

            account_names = []
            for account in accounts[:10]:
                label = account["phone"] or account["name"] or f"계정-{account['id']}"
                state = account["status"] or "UNKNOWN"
                account_names.append(f"{label}({state})")

            text = QLabel(
                f"[계정] {len(accounts)}/10개   [진행] {working}   "
                f"[성공] {sent}   [실패] {failed}\n"
                + (" · ".join(account_names) if account_names else "배정된 계정 없음")
            )
            text.setWordWrap(True)
            text.setObjectName("보조")
            layout.addWidget(text)

            self.sector_tabs.addTab(page, f"진행창 {sector_id} ({len(accounts)}/10)")

        if self.sector_tabs.count():
            self.sector_tabs.setCurrentIndex(
                min(max(current_index, 0), self.sector_tabs.count() - 1)
            )

    def add_progress_sector(self):
        if self.worker_thread and self.worker_thread.is_alive():
            QMessageBox.warning(
                self,
                "작업 실행 중",
                "작업 중에는 진행창 구성을 변경할 수 없습니다."
            )
            return

        try:
            count = self.send_engine.add_sector()
            self.refresh_progress_sectors()
            self.refresh_accounts()
            QMessageBox.information(
                self,
                "진행창 추가",
                f"진행창 {count}개로 변경했습니다. 계정은 자동으로 균등 재배치됩니다."
            )
        except Exception as e:
            QMessageBox.critical(self, "진행창 추가 오류", str(e))

    def remove_progress_sector(self):
        if self.worker_thread and self.worker_thread.is_alive():
            QMessageBox.warning(
                self,
                "작업 실행 중",
                "작업 중에는 진행창 구성을 변경할 수 없습니다."
            )
            return

        try:
            count = self.send_engine.remove_last_sector()
            self.refresh_progress_sectors()
            self.refresh_accounts()
            QMessageBox.information(
                self,
                "진행창 삭제",
                f"진행창 {count}개로 변경했습니다. 계정은 자동으로 다시 배치됩니다."
            )
        except Exception as e:
            QMessageBox.warning(self, "진행창 삭제 불가", str(e))

    def clear_work_live_log(self):
        if hasattr(self, "work_live_log"):
            self.work_live_log.clear()
            self.work_live_log.setPlainText("[대기] 작업 로그를 기다리고 있습니다.")
        if hasattr(self, "work_live_state"):
            self.work_live_state.setText("[ 대기 ]")

    def _prepare_work_live_log(self, title, campaign_id=None):
        if not hasattr(self, "work_live_log"):
            return

        self.work_live_log.clear()
        suffix = f" / 작업 #{campaign_id}" if campaign_id else ""
        self.work_live_log.append(
            f"[시작] {title}{suffix}\n"
            f"[안내] 실제 처리 결과가 아래에 실시간으로 표시됩니다."
        )
        self.work_live_state.setText(f"[ 실행중 ] {title}{suffix}")
        self.refresh_live_progress()
        bar = self.work_live_log.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _check_base(self, need_post=False):
        api_ok = bool(self.db.get_setting("telegram_api_id")) and bool(self.db.get_setting("telegram_api_hash"))
        accounts = self.db.fetchone(
            "SELECT COUNT(*) c FROM telegram_accounts WHERE enabled=1 "
            "AND status NOT IN ('SEND_RESTRICTED','PEER_FLOOD','FLOOD_WAIT','SESSION_ERROR','STOPPED')"
        )["c"]

        pending = self.db.fetchone(
            "SELECT COUNT(*) c FROM recipients WHERE status IN ('PENDING','REASSIGN_WAITING')"
        )["c"]

        latest = self.send_engine.latest_campaign()
        assigned_ready = 0
        if latest and latest["status"] in (
            "CONTACT_WAITING",
            "CONTACT_RUNNING",
            "CONTACT_DONE",
            "SEND_RUNNING",
            "PARTIAL",
        ):
            row = self.db.fetchone(
                "SELECT COUNT(*) c FROM campaign_recipients "
                "WHERE campaign_id=? "
                "AND status!='MESSAGE_SENT' "
                "AND contact_status IN ('WAITING','ADDING','ADDED','CONTACT_PAUSED')",
                (latest["id"],),
            )
            assigned_ready = int(row["c"] or 0) if row else 0

        post_ok = bool(self.db.get_setting("postbot_link"))
        customer_db_ok = int(pending or 0) > 0 or assigned_ready > 0

        checks = [
            ("텔레그램 API", api_ok),
            ("사용 가능한 계정", accounts > 0),
            ("고객 DB", customer_db_ok),
        ]
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
        if hasattr(self, "retry_failed_button"):
            if busy:
                self.retry_failed_button.setEnabled(False)
            else:
                self._refresh_retry_button()
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
        self._prepare_work_live_log("연락처 추가 + UID 확인", campaign_id)
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
        self._prepare_work_live_log("PostBot 게시물 발송", latest["id"])
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
        self._prepare_work_live_log("전체 자동 진행", campaign_id)
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

    def _latest_failed_count(self):
        latest = self.send_engine.latest_campaign()
        if not latest:
            return None, 0

        row = self.db.fetchone(
            "SELECT COUNT(*) c FROM campaign_recipients cr "
            "JOIN recipients r ON r.id=cr.recipient_id "
            "WHERE cr.campaign_id=? AND cr.status='FAILED' AND r.status='FAILED'",
            (latest["id"],),
        )
        return latest, int(row["c"] or 0) if row else 0

    def _refresh_retry_button(self):
        if not hasattr(self, "retry_failed_button"):
            return

        latest, failed_count = self._latest_failed_count()
        self.retry_failed_button.setText(
            f"[ 실패 {failed_count}건만 다시 재시도 ]"
        )
        busy = bool(self.worker_thread and self.worker_thread.is_alive())
        self.retry_failed_button.setEnabled(
            bool(latest and failed_count > 0 and not busy)
        )

        if hasattr(self, "retry_history_label"):
            if not latest:
                self.retry_history_label.setText("[재시도 이력] 없음")
                return

            if int(latest["retry_round"] or 0) > 0:
                history = self.db.fetchone(
                    "SELECT "
                    "COUNT(*) total,"
                    "SUM(CASE WHEN result_status='MESSAGE_SENT' THEN 1 ELSE 0 END) success,"
                    "SUM(CASE WHEN result_status='FAILED' THEN 1 ELSE 0 END) failed,"
                    "SUM(CASE WHEN result_status NOT IN ('MESSAGE_SENT','FAILED') THEN 1 ELSE 0 END) remaining "
                    "FROM retry_history WHERE retry_campaign_id=?",
                    (latest["id"],),
                )
                self.retry_history_label.setText(
                    f"[재시도 이력] {latest['retry_round']}차 · "
                    f"대상 {int(history['total'] or 0)} / "
                    f"성공 {int(history['success'] or 0)} / "
                    f"실패 {int(history['failed'] or 0)} / "
                    f"잔여 {int(history['remaining'] or 0)}"
                )
            else:
                self.retry_history_label.setText(
                    f"[재시도 이력] 작업 #{latest['id']} · 실패 {failed_count}건"
                )

    def start_failed_retry(self):
        if self.worker_thread and self.worker_thread.is_alive():
            QMessageBox.information(
                self,
                "작업 중",
                "현재 다른 작업이 실행 중입니다."
            )
            return

        latest, failed_count = self._latest_failed_count()
        if not latest or failed_count <= 0:
            QMessageBox.information(
                self,
                "실패건 재시도",
                "현재 작업에서 재시도할 실패 DB가 없습니다."
            )
            self._refresh_retry_button()
            return

        accounts = self.db.fetchone(
            "SELECT COUNT(*) c FROM telegram_accounts WHERE enabled=1 "
            "AND status NOT IN ('SEND_RESTRICTED','PEER_FLOOD','FLOOD_WAIT','SESSION_ERROR','STOPPED','WORKER_ERROR')"
        )
        if not accounts or int(accounts["c"] or 0) <= 0:
            QMessageBox.warning(
                self,
                "실패건 재시도",
                "재시도에 사용할 정상 텔레그램 계정이 없습니다."
            )
            return

        answer = QMessageBox.question(
            self,
            "실패건만 다시 재시도",
            f"실패 {failed_count}건만 다시 대기상태로 복구하고 "
            "정상 계정에 새로 재배정하여 전송하시겠습니까?\n\n"
            "기존 MESSAGE_SENT 성공건은 절대 다시 처리하지 않습니다.\n"
            "이미 연락처 추가/UID 확인이 끝난 DB는 기존 담당 계정을 그대로 사용하고 "
            "연락처 추가를 생략합니다.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        try:
            retry = self.send_engine.create_failed_retry_campaign(
                latest["id"],
                latest["postbot_username"],
                latest["post_code"],
            )
        except Exception as e:
            QMessageBox.critical(self, "재시도 작업 생성 오류", str(e))
            return

        retry_campaign_id = retry["campaign_id"]
        self.current_campaign_id = retry_campaign_id
        self._set_busy(True)
        self._prepare_work_live_log(
            f"실패 DB {retry['count']}건 · {retry['retry_round']}차 재시도",
            retry_campaign_id,
        )

        self.worker_thread = threading.Thread(
            target=self._background_failed_retry,
            args=(retry,),
            daemon=True,
        )
        self.worker_thread.start()

    def _background_failed_retry(self, retry):
        campaign_id = retry["campaign_id"]
        try:
            contact = self.send_engine.run_contact_stage(campaign_id)
            if contact["ready"] > 0:
                sent = self.send_engine.run_send_stage(campaign_id)
            else:
                sent = {
                    "success": 0,
                    "failed": contact["failed"],
                    "remaining": contact["paused"],
                }

            self.bridge.작업완료.emit(
                "재시도",
                {
                    "retry": retry,
                    "contact": contact,
                    "sent": sent,
                },
            )
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

        if kind == "포인트잔액":
            self.license_info["point_balance_krw"] = int(result.get("balance_krw") or 0)
            self.license_info["send_unit_price_krw"] = int(result.get("unit_price_krw") or 10)
            self.refresh_summary()
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
        if hasattr(self, "work_live_state"):
            if kind == "오류":
                self.work_live_state.setText("[ 오류 ] 작업이 중단되었습니다. 아래 로그를 확인하세요.")
            else:
                self.work_live_state.setText("[ 완료 ] 작업이 종료되었습니다.")
        self.refresh_summary()
        self.refresh_point_balance_async()
        self.refresh_work_status()
        self.refresh_accounts()
        self.refresh_completion_log()
        self.refresh_db_status_tabs()
        self.refresh_live_progress()

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
        elif kind == "재시도":
            retry = result["retry"]
            c = result["contact"]
            s = result["sent"]
            QMessageBox.information(
                self,
                "실패건 재시도 완료",
                f"원본 작업: #{retry['source_campaign_id']}\n"
                f"재시도 작업: #{retry['campaign_id']} / {retry['retry_round']}차\n"
                f"재시도 대상: {retry['count']}명\n"
                f"기존 연락처 재사용: {retry.get('contact_reused', 0)}명\n"
                f"연락처 재처리 필요: {retry.get('contact_readd', 0)}명\n"
                f"연락처/UID 준비: {c['ready']}명\n"
                f"재시도 발송 성공: {s['success']}명\n"
                f"재시도 실패: {s['failed']}명\n"
                f"잔여/보류: {s['remaining']}명"
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
            self._refresh_retry_button()
            return

        ready = self.db.fetchone(
            "SELECT COUNT(*) c FROM campaign_recipients WHERE campaign_id=? AND contact_status='ADDED'",
            (latest["id"],),
        )["c"]
        sent = self.db.fetchone(
            "SELECT COUNT(*) c FROM campaign_recipients WHERE campaign_id=? AND status='MESSAGE_SENT'",
            (latest["id"],),
        )["c"]
        failed_row = self.db.fetchone(
            "SELECT COUNT(*) c FROM campaign_recipients "
            "WHERE campaign_id=? AND status='FAILED'",
            (latest["id"],),
        )
        failed = int(failed_row["c"] or 0) if failed_row else 0

        retry_text = ""
        if int(latest["retry_round"] or 0) > 0:
            retry_text = (
                f"\n[재시도] 원본 작업 #{latest['retry_of_campaign_id']} / "
                f"{latest['retry_round']}차"
            )

        self.work_status.setPlainText(
            f"[작업번호] {latest['id']}\n"
            f"[작업상태] {latest['status']}\n"
            f"[전체대상] {latest['total_count']}명\n"
            f"[발송성공] {sent}명\n"
            f"[실패] {failed}명"
            f"{retry_text}"
        )
        handoff = self.send_engine.residual_handoff_summary()
        if handoff["waiting"] or handoff["blocked"] or handoff["final"]:
            self.work_status.append(
                f"[잔여승계] 대기 {handoff['waiting']} / "
                f"수동확인 {handoff['blocked']} / 최종실패 {handoff['final']}"
            )

        self._refresh_retry_button()

    def db_page(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(self.title("고객 DB", "C:\\엔젤토글> DB 상태별 관리"))

        db_buttons = QHBoxLayout()

        b = QPushButton("[ 고객 DB 업로드 · Excel / TXT ]")
        b.clicked.connect(self.import_db)

        retry_failed = QPushButton("[ 실패 DB 다시 대기상태로 ]")
        retry_failed.clicked.connect(self.reset_failed_db)

        unassign_db = QPushButton("[ 배정된 DB 다시 대기상태로 ]")
        unassign_db.clicked.connect(self.reset_assigned_db)

        classify_handoff = QPushButton("[ 잔여 작업 분류 ]")
        classify_handoff.clicked.connect(self.classify_residual_db)

        assign_handoff = QPushButton("[ 재배정 대기 자동 배정 ]")
        assign_handoff.clicked.connect(self.assign_residual_db)

        refresh = QPushButton("[ DB 상태 새로고침 ]")
        refresh.clicked.connect(self.refresh_db_status_tabs)

        db_buttons.addWidget(b)
        db_buttons.addWidget(retry_failed)
        db_buttons.addWidget(unassign_db)
        db_buttons.addWidget(classify_handoff)
        db_buttons.addWidget(assign_handoff)
        db_buttons.addWidget(refresh)
        l.addLayout(db_buttons)

        self.db_result = QLabel("DB 상태를 불러오는 중입니다.")
        self.db_result.setObjectName("상태패널")
        l.addWidget(self.db_result)

        self.db_tabs = QTabWidget()
        self.db_status_tables = {}

        tab_defs = [
            ("대기 DB", "pending"),
            ("배정된 DB", "assigned"),
            ("진행중 DB", "running"),
            ("진행완료 DB", "completed"),
            ("실패 DB", "failed"),
            ("재배정 대기", "reassign"),
            ("수동 확인", "blocked"),
            ("최종 실패", "final"),
            ("크리티컬 잔여 DB", "critical"),
            ("최근 업로드", "upload"),
        ]

        for label, key in tab_defs:
            page = QWidget()
            page_layout = QVBoxLayout(page)

            table = QTableWidget(0, 9 if key != "upload" else 4)

            if key == "upload":
                table.setHorizontalHeaderLabels([
                    "번호", "원본 번호", "변환 번호", "상태"
                ])
            else:
                table.setHorizontalHeaderLabels([
                    "DB",
                    "전화번호",
                    "담당 계정",
                    "DB 상태",
                    "연락처 상태",
                    "Telegram UID",
                    "오류 코드",
                    "실패 사유",
                    "최근 변경",
                ])

            table.horizontalHeader().setStretchLastSection(True)
            table.setSelectionBehavior(QAbstractItemView.SelectRows)
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)

            page_layout.addWidget(table)
            self.db_status_tables[key] = table
            self.db_tabs.addTab(page, label)

        self.db_table = self.db_status_tables["upload"]
        l.addWidget(self.db_tabs, 1)

        QTimer.singleShot(0, self.refresh_db_status_tabs)
        return w

    def _db_status_rows(self, kind):
        base_select = (
            "SELECT r.id,r.phone,r.normalized_phone,r.status,r.contact_status,"
            "r.telegram_uid,r.error_code,r.error_message,r.updated_at,"
            "r.handoff_status,r.handoff_reason,r.handoff_count,"
            "a.name account_name,a.phone account_phone "
            "FROM recipients r "
            "LEFT JOIN telegram_accounts a ON a.id=r.assigned_account_id "
        )

        if kind == "pending":
            sql = base_select + (
                "WHERE r.status='PENDING' "
                "AND r.assigned_account_id IS NULL "
                "ORDER BY r.id"
            )
            return self.db.fetchall(sql)

        if kind == "completed":
            return self.db.fetchall(
                base_select +
                "WHERE r.status='MESSAGE_SENT' ORDER BY r.id"
            )

        if kind == "failed":
            return self.db.fetchall(
                base_select +
                "WHERE r.status='FAILED' ORDER BY r.id"
            )

        if kind == "reassign":
            return self.db.fetchall(
                base_select +
                "WHERE r.status='REASSIGN_WAITING' ORDER BY r.id"
            )

        if kind == "blocked":
            return self.db.fetchall(
                base_select +
                "WHERE r.status='REASSIGN_BLOCKED' ORDER BY r.id"
            )

        if kind == "final":
            return self.db.fetchall(
                base_select +
                "WHERE r.status='FAILED_FINAL' ORDER BY r.id"
            )

        if kind == "critical":
            return self.db.fetchall(
                base_select +
                "WHERE r.status='UNCERTAIN' "
                "OR EXISTS ("
                "SELECT 1 FROM campaign_recipients cr "
                "WHERE cr.recipient_id=r.id "
                "AND (cr.status IN ('SEND_PAUSED','UNCERTAIN') "
                "OR cr.contact_status IN ('CONTACT_PAUSED','CONTACT_UNCERTAIN'))"
                ") "
                "ORDER BY r.id"
            )

        if kind == "running":
            return self.db.fetchall(
                base_select +
                "WHERE r.status='SENDING' "
                "OR EXISTS ("
                "SELECT 1 FROM campaign_recipients cr "
                "WHERE cr.recipient_id=r.id "
                "AND (cr.status='SENDING' OR cr.contact_status='ADDING')"
                ") "
                "ORDER BY r.id"
            )

        if kind == "assigned":
            return self.db.fetchall(
                base_select +
                "WHERE r.assigned_account_id IS NOT NULL "
                "AND r.status NOT IN ('MESSAGE_SENT','FAILED','UNCERTAIN','SENDING') "
                "AND NOT EXISTS ("
                "SELECT 1 FROM campaign_recipients cr "
                "WHERE cr.recipient_id=r.id "
                "AND (cr.status IN ('SENDING','SEND_PAUSED','UNCERTAIN') "
                "OR cr.contact_status IN ('ADDING','CONTACT_PAUSED','CONTACT_UNCERTAIN'))"
                ") "
                "ORDER BY r.id"
            )

        return []

    def refresh_db_status_tabs(self):
        if not hasattr(self, "db_status_tables"):
            return

        counts = {}

        for kind in ["pending", "assigned", "running", "completed", "failed", "reassign", "blocked", "final", "critical"]:
            rows = self._db_status_rows(kind)
            counts[kind] = len(rows)

            table = self.db_status_tables[kind]
            table.setRowCount(len(rows))

            for index, row in enumerate(rows):
                account_text = row["account_phone"] or row["account_name"] or ""
                error_code = row["error_code"] or ""
                failure_reason = row["handoff_reason"] or row["error_message"] or ""

                values = [
                    f"DB{row['id']}",
                    row["phone"] or row["normalized_phone"] or "",
                    account_text,
                    row["status"] or "",
                    row["contact_status"] or "",
                    row["telegram_uid"] or "",
                    error_code,
                    failure_reason,
                    row["updated_at"] or "",
                ]

                for col, value in enumerate(values):
                    item = QTableWidgetItem(str(value))
                    if kind in ("failed", "critical", "final"):
                        item.setForeground(QColor(255, 100, 100))
                    elif kind == "reassign":
                        item.setForeground(QColor(255, 214, 108))
                    elif kind == "blocked":
                        item.setForeground(QColor(255, 165, 100))
                    elif kind == "completed":
                        item.setForeground(QColor(110, 255, 150))
                    self.db_status_tables[kind].setItem(index, col, item)

            table.resizeColumnsToContents()

        labels = {
            "pending": "대기 DB",
            "assigned": "배정된 DB",
            "running": "진행중 DB",
            "completed": "진행완료 DB",
            "failed": "실패 DB",
            "reassign": "재배정 대기",
            "blocked": "수동 확인",
            "final": "최종 실패",
            "critical": "크리티컬 잔여 DB",
        }

        key_order = ["pending", "assigned", "running", "completed", "failed", "reassign", "blocked", "final", "critical", "upload"]
        for tab_index, key in enumerate(key_order):
            if key == "upload":
                self.db_tabs.setTabText(tab_index, "최근 업로드")
            else:
                self.db_tabs.setTabText(
                    tab_index,
                    f"{labels[key]} ({counts.get(key, 0)})"
                )

        self.db_result.setText(
            f"[대기] {counts.get('pending', 0)}명   |   "
            f"[배정] {counts.get('assigned', 0)}명   |   "
            f"[진행중] {counts.get('running', 0)}명   |   "
            f"[완료] {counts.get('completed', 0)}명   |   "
            f"[실패] {counts.get('failed', 0)}명   |   "
            f"[재배정대기] {counts.get('reassign', 0)}명   |   "
            f"[수동확인] {counts.get('blocked', 0)}명   |   "
            f"[최종실패] {counts.get('final', 0)}명   |   "
            f"[크리티컬 잔여] {counts.get('critical', 0)}명"
        )

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
        self.db_tabs.setCurrentIndex(9)

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
            self.refresh_db_status_tabs()
        except Exception as e:
            QMessageBox.critical(self, "DB 업로드 오류", str(e))

    def classify_residual_db(self):
        if self.worker_thread and self.worker_thread.is_alive():
            QMessageBox.warning(
                self,
                "작업 실행 중",
                "작업이 실행 중일 때는 잔여 DB를 분류할 수 없습니다."
            )
            return

        latest = self.send_engine.latest_campaign()
        if not latest:
            QMessageBox.information(self, "잔여 작업", "분류할 작업이 없습니다.")
            return

        try:
            result = self.send_engine.classify_residual_work(latest["id"])
            self.refresh_db_status_tabs()
            self.refresh_work_status()
            QMessageBox.information(
                self,
                "잔여 작업 분류 완료",
                f"재배정 대기: {result['queued']}건\n"
                f"수동 확인: {result['blocked']}건\n"
                f"최종 실패: {result['final']}건\n\n"
                "Telegram 제한/FloodWait/PeerFlood/전송결과 불확실 건은 "
                "자동 계정교체 대상에서 제외됩니다."
            )
        except Exception as e:
            QMessageBox.critical(self, "잔여 작업 분류 오류", str(e))

    def assign_residual_db(self):
        if self.worker_thread and self.worker_thread.is_alive():
            QMessageBox.warning(
                self,
                "작업 실행 중",
                "작업이 실행 중일 때는 재배정할 수 없습니다."
            )
            return

        try:
            result = self.send_engine.auto_assign_reassignment_waiting()
            if result["campaign_ids"]:
                self.current_campaign_id = result["campaign_ids"][-1]

            self.refresh_db_status_tabs()
            self.refresh_work_status()
            self.refresh_accounts()
            self.refresh_progress_sectors()

            QMessageBox.information(
                self,
                "잔여 DB 자동 배정",
                f"새 계정에 승계 배정: {result['assigned']}건\n"
                f"아직 재배정 대기: {result['waiting']}건\n"
                f"생성된 승계 작업: {len(result['campaign_ids'])}개\n\n"
                "승계된 DB는 새 담당 계정 기준으로 연락처 추가 후 발송을 이어갑니다."
            )
        except Exception as e:
            QMessageBox.critical(self, "잔여 DB 배정 오류", str(e))

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
            self.refresh_db_status_tabs()
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
            self.refresh_db_status_tabs()
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

        assign_db = QPushButton("[ 체크 계정에 대기 DB 배정 ]")
        assign_db.clicked.connect(self.assign_waiting_db_to_checked_accounts)

        unassign = QPushButton("[ 체크 계정 배정취소 ]")
        unassign.clicked.connect(self.unassign_checked_accounts)

        bulk_delete = QPushButton("[ 체크 계정 일괄 삭제 ]")
        bulk_delete.clicked.connect(self.delete_selected_account)

        top_buttons.addWidget(add)
        top_buttons.addWidget(assign_db)
        top_buttons.addWidget(unassign)
        top_buttons.addWidget(bulk_delete)
        l.addLayout(top_buttons)

        self.account_table = QTableWidget(0, 7)
        self.account_table.setHorizontalHeaderLabels(
            ["선택", "번호", "계정", "상태", "마지막 오류", "진행창", "세션 파일"]
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

        delete = QPushButton("[ 체크 계정 삭제 ]")
        delete.clicked.connect(self.delete_selected_account)

        row.addWidget(select_all)
        row.addWidget(clear_all)
        row.addWidget(refresh)
        row.addWidget(chats)
        row.addWidget(reset)
        row.addWidget(delete)
        l.addLayout(row)

        info = QLabel(
            "연락처 추가 전에 체크한 정상 계정에 대기 DB를 균등 배정할 수 있습니다.\n"
            "수동 배정 대상은 PENDING / 재배정 대기 DB이며, MESSAGE_SENT·최종실패·수동확인·Telegram 제한 건은 제외됩니다.\n"
            "배정취소는 아직 완료되지 않은 작업만 해제하며, 발송 완료 이력은 유지됩니다."
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

    def assign_waiting_db_to_checked_accounts(self):
        if self.worker_thread and self.worker_thread.is_alive():
            QMessageBox.warning(
                self,
                "작업 실행 중",
                "연락처 추가/발송 작업이 실행 중일 때는 DB를 새로 배정할 수 없습니다."
            )
            return

        account_ids = self.checked_account_ids()
        if not account_ids:
            QMessageBox.information(
                self,
                "계정 체크",
                "DB를 배정할 텔레그램 계정을 먼저 체크해주세요."
            )
            return

        pending = self.db.fetchone(
            "SELECT COUNT(*) c FROM recipients "
            "WHERE status IN ('PENDING','REASSIGN_WAITING')"
        )
        pending_count = int(pending["c"] or 0) if pending else 0

        if pending_count <= 0:
            QMessageBox.information(
                self,
                "대기 DB 없음",
                "현재 체크 계정에 배정할 PENDING / 재배정 대기 DB가 없습니다."
            )
            return

        max_per = int(self.db.get_setting("max_contacts_per_account", "40") or 40)

        answer = QMessageBox.question(
            self,
            "대기 DB 수동 배정",
            f"체크한 계정 {len(account_ids)}개에 대기 DB를 균등 배정하시겠습니까?\n\n"
            f"현재 배정 가능 DB: {pending_count}건\n"
            f"계정당 최대 처리량: {max_per}건\n\n"
            "배정 후 [1단계 · 연락처 추가 시작] 또는 [전체 자동 진행]으로 이어서 처리할 수 있습니다.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return

        try:
            bot_username = self.db.get_setting("postbot_username", "@PostBot") or "@PostBot"
            post_code = self.db.get_setting("postbot_link", "")
            campaign_name = "수동배정_" + datetime.now().strftime("%Y%m%d_%H%M%S")

            result = self.send_engine.create_manual_assignment_campaign(
                account_ids=account_ids,
                name=campaign_name,
                bot_username=bot_username,
                post_code=post_code,
            )

            self.current_campaign_id = result["campaign_id"]

            self.refresh_accounts()
            self.refresh_summary()
            self.refresh_work_status()
            self.refresh_db_status_tabs()
            self.refresh_progress_sectors()
            self.refresh_live_progress()

            per_account = result.get("per_account") or {}
            dist = ", ".join(
                f"계정 {aid}: {count}건"
                for aid, count in sorted(per_account.items())
            )

            msg = (
                f"작업 #{result['campaign_id']} 생성\n"
                f"체크 계정: {result['account_count']}개\n"
                f"DB 배정: {result['assigned']}건\n"
            )
            if result.get("excluded_accounts"):
                msg += f"사용 불가 계정 제외: {result['excluded_accounts']}개\n"
            if dist:
                msg += f"\n배정 내역\n{dist}"

            QMessageBox.information(
                self,
                "대기 DB 배정 완료",
                msg
            )
        except Exception as e:
            QMessageBox.critical(self, "DB 배정 오류", str(e))

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
        account_ids = self.checked_account_ids()

        if not account_ids:
            account_id = self._current_account_id()
            if account_id is not None:
                account_ids = [account_id]

        if not account_ids:
            QMessageBox.information(
                self,
                "계정 선택",
                "삭제할 계정을 체크해주세요."
            )
            return

        answer = QMessageBox.question(
            self,
            "계정 삭제",
            f"체크한 계정 {len(account_ids)}개를 삭제하시겠습니까?\n\n"
            "진행 중 배정이 있으면 배정취소 여부를 확인한 뒤 삭제합니다.\n"
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
            self.refresh_db_status_tabs()

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
            self.send_engine.rebalance_account_sectors()

            handoff_text = ""
            if not (self.worker_thread and self.worker_thread.is_alive()):
                try:
                    latest = self.send_engine.latest_campaign()
                    if latest:
                        classified = self.send_engine.classify_residual_work(latest["id"])
                        handoff = self.send_engine.auto_assign_reassignment_waiting()
                        if handoff["campaign_ids"]:
                            self.current_campaign_id = handoff["campaign_ids"][-1]
                        if classified["queued"] or handoff["assigned"]:
                            handoff_text = (
                                f"\n잔여작업 재배정대기: {classified['queued']}건"
                                f"\n새 계정 승계배정: {handoff['assigned']}건"
                                f"\n남은 대기: {handoff['waiting']}건"
                            )
                except Exception as handoff_error:
                    self.logs.write(
                        "WARNING",
                        "승계",
                        f"새 계정 등록 후 잔여작업 자동 승계 확인 실패: {handoff_error}",
                    )

            self.refresh_accounts()
            self.refresh_summary()
            self.refresh_db_status_tabs()
            self.refresh_work_status()

            if handoff_text:
                QMessageBox.information(
                    self,
                    "잔여작업 자동 승계",
                    "새 계정 등록 후 재배정 가능한 잔여 DB를 자동으로 배정했습니다."
                    + handoff_text
                    + "\n\n작업 실행 화면에서 전체 자동 진행을 눌러 이어서 처리할 수 있습니다."
                )

        except Exception as e:
            QMessageBox.critical(self, "세션 등록 오류", str(e))

    def refresh_accounts(self):
        if not hasattr(self, "account_table"):
            return

        checked_before = set(self.checked_account_ids()) if self.account_table.rowCount() else set()

        rows = self.db.fetchall(
            "SELECT id,name,status,last_error,worker_sector,session_file FROM telegram_accounts ORDER BY id"
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
                f"진행창 {int(row['worker_sector'] or 1)}",
                row["session_file"],
            ]
            for c, v in enumerate(vals, start=1):
                self.account_table.setItem(r, c, QTableWidgetItem(str(v)))

        self.account_table.resizeColumnsToContents()
        self.account_table.setColumnWidth(0, 58)
        if hasattr(self, "sector_tabs"):
            QTimer.singleShot(0, self.refresh_progress_sectors)

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
        account = f" / 계정 {item['account_id']}" if item.get("account_id") else ""
        campaign = f" / 작업 {item['campaign_id']}" if item.get("campaign_id") else ""
        recipient = f" / DB {item['recipient_id']}" if item.get("recipient_id") else ""
        level = {
            "INFO": "안내",
            "SUCCESS": "성공",
            "WARNING": "주의",
            "ERROR": "오류",
        }.get(item.get("level"), item.get("level"))

        line = (
            f"[{item['time']}] [{level}] [{item['category']}]"
            f"{campaign}{account}{recipient} :: {item['message']}"
        )

        if hasattr(self, "log_view"):
            if self.log_view.toPlainText().startswith("[대기]"):
                self.log_view.clear()
            self.log_view.append(line)
            bar = self.log_view.verticalScrollBar()
            bar.setValue(bar.maximum())

        if hasattr(self, "work_live_log"):
            if self.work_live_log.toPlainText().startswith("[대기]"):
                self.work_live_log.clear()

            current_campaign = self.current_campaign_id
            item_campaign = item.get("campaign_id")

            # 작업 실행 화면에는 현재 작업 관련 로그를 우선 표시한다.
            if (
                current_campaign is None
                or item_campaign is None
                or int(item_campaign) == int(current_campaign)
            ):
                self.work_live_log.append(line)
                bar = self.work_live_log.verticalScrollBar()
                bar.setValue(bar.maximum())

                if hasattr(self, "work_live_state"):
                    if item.get("level") == "ERROR":
                        self.work_live_state.setText("[ 진행중 · 오류 발생 ] 아래 로그 확인")
                    elif item.get("level") == "SUCCESS":
                        self.work_live_state.setText("[ 진행중 · 성공 응답 확인 ]")
                    else:
                        self.work_live_state.setText("[ 진행중 ] 실시간 처리 중...")

    def refresh_point_balance_async(self):
        def worker():
            try:
                point = self.send_engine.points.balance()
                self.bridge.작업완료.emit(
                    "포인트잔액",
                    {
                        "balance_krw": point["balance_krw"],
                        "unit_price_krw": point["unit_price_krw"],
                    },
                )
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

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
