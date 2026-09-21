from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton,
    QMessageBox, QHBoxLayout
)

class LicenseDialog(QDialog):
    def __init__(self, license_service, parent=None):
        super().__init__(parent)
        self.license_service = license_service
        self.activated = False
        self.setWindowTitle("엔젤토글 라이선스 인증")
        self.setFixedWidth(520)

        self.setStyleSheet("""
        QDialog { background:#050706; color:#d9ffe7; font-family:Consolas,"D2Coding",monospace; }
        QLabel { color:#c8ffd8; }
        QLineEdit { background:#020403; color:#c8ffd8; border:1px solid #1d5a35; border-radius:6px; padding:11px; }
        QPushButton { background:#0b1b12; color:#79ff9e; border:1px solid #28d862; border-radius:7px; padding:11px; font-weight:700; }
        """)

        layout = QVBoxLayout(self)
        title = QLabel("엔젤토글 기간코드 인증")
        title.setStyleSheet("font-size:22px;font-weight:800;color:#67ff91;")
        desc = QLabel(
            "구매한 라이선스 코드를 입력하세요.\n"
            "처음 인증한 시점부터 이용기간이 시작되며 등록된 PC에서 사용할 수 있습니다."
        )
        desc.setWordWrap(True)

        self.code_input = QLineEdit()
        self.code_input.setPlaceholderText("ANGEL-XXXX-XXXX-XXXX-XXXX")
        self.code_input.returnPressed.connect(self.activate)

        self.status = QLabel("")
        self.status.setWordWrap(True)

        row = QHBoxLayout()
        self.activate_btn = QPushButton("[ 라이선스 인증 ]")
        self.activate_btn.clicked.connect(self.activate)
        row.addWidget(self.activate_btn)

        layout.addWidget(title)
        layout.addWidget(desc)
        layout.addWidget(self.code_input)
        layout.addWidget(self.status)
        layout.addLayout(row)

    def activate(self):
        code = self.code_input.text().strip()
        if not code:
            QMessageBox.information(self, "코드 확인", "라이선스 코드를 입력하세요.")
            return

        self.activate_btn.setEnabled(False)
        self.status.setText("라이선스 서버 확인 중...")
        try:
            result = self.license_service.activate(code)
        except Exception as e:
            self.activate_btn.setEnabled(True)
            self.status.setText(str(e))
            return

        if result.get("ok"):
            self.activated = True
            days = result.get("remaining_days")
            self.status.setText(f"인증 완료 · 남은기간 {days}일")
            QMessageBox.information(
                self, "인증 완료",
                f"라이선스가 정상 활성화되었습니다.\n남은기간: {days}일"
            )
            self.accept()
            return

        self.activate_btn.setEnabled(True)
        self.status.setText(result.get("message") or "라이선스 인증에 실패했습니다.")
