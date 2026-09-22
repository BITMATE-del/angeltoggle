import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QDialog, QLabel

from app.db.database import Database
from app.core.logging_service import LogService
from app.services.license_service import LicenseService, LicenseError
from app.ui.license_dialog import LicenseDialog
from app.ui.main_window import MainWindow


def _startup_log(message):
    try:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "AngelToggle"
        base.mkdir(parents=True, exist_ok=True)
        path = base / "startup.log"
        with path.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except Exception:
        pass


def main():
    _startup_log("START: process entered main()")

    app = QApplication(sys.argv)
    app.setApplicationName("엔젤토글")
    app.setQuitOnLastWindowClosed(True)
    _startup_log("OK: QApplication created")

    startup = QLabel("엔젤토글 시작 중...\n잠시만 기다려주세요.")
    startup.setWindowTitle("엔젤토글")
    startup.setMinimumSize(360, 120)
    startup.setStyleSheet(
        "background:#050706;color:#6CFF9B;"
        "font-family:Consolas;font-size:15px;font-weight:700;padding:24px;"
    )
    startup.show()
    startup.raise_()
    startup.activateWindow()
    app.processEvents()

    try:
        _startup_log("STEP: database initialize")
        db = Database()
        db.initialize()
        logs = LogService(db)
        _startup_log("OK: database initialized")

        startup.setText("엔젤토글 시작 중...\n라이선스를 확인하고 있습니다.")
        app.processEvents()

        license_service = LicenseService(logs)
        license_result = None

        try:
            _startup_log("STEP: license verify_saved")
            license_result = license_service.verify_saved()
            _startup_log("OK: license verify_saved returned")
        except LicenseError as e:
            _startup_log("WARN: license error: " + str(e))
            startup.hide()
            QMessageBox.warning(None, "라이선스 확인", str(e))
            startup.show()
            app.processEvents()

        if not license_result or not license_result.get("ok"):
            _startup_log("STEP: license dialog required")
            startup.hide()

            if license_result and license_result.get("code") not in ("NO_LICENSE", None):
                QMessageBox.warning(
                    None,
                    "라이선스 사용 불가",
                    license_result.get("message") or "라이선스를 확인할 수 없습니다."
                )

            dialog = LicenseDialog(license_service)
            if dialog.exec() != QDialog.Accepted:
                _startup_log("STOP: license dialog cancelled")
                return

            _startup_log("STEP: re-verify after activation")
            license_result = license_service.verify_saved()
            _startup_log("OK: license activation verified")

        startup.show()
        startup.setText("엔젤토글 시작 중...\n관리 화면을 준비하고 있습니다.")
        app.processEvents()

        _startup_log("STEP: MainWindow construct")
        win = MainWindow(db, logs, license_result)
        win.resize(1280, 820)
        _startup_log("OK: MainWindow constructed")

        startup.hide()
        win.show()
        win.raise_()
        win.activateWindow()
        app.processEvents()
        _startup_log("OK: MainWindow shown")
        sys.exit(app.exec())

    except Exception as e:
        _startup_log("FATAL: " + repr(e))
        _startup_log(traceback.format_exc())
        try:
            startup.hide()
        except Exception:
            pass
        QMessageBox.critical(
            None,
            "엔젤토글 시작 오류",
            str(e) + "\n\n시작 로그: %LOCALAPPDATA%\\AngelToggle\\startup.log"
        )
        raise


if __name__ == "__main__":
    main()
