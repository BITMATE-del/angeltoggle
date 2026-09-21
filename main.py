import sys
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from app.db.database import Database
from app.core.logging_service import LogService
from app.services.license_service import LicenseService, LicenseError
from app.ui.license_dialog import LicenseDialog
from app.ui.main_window import MainWindow

def main():
    db = Database()
    db.initialize()
    logs = LogService(db)

    app = QApplication(sys.argv)
    app.setApplicationName("엔젤토글")

    license_service = LicenseService(logs)
    license_result = None

    try:
        license_result = license_service.verify_saved()
    except LicenseError as e:
        QMessageBox.warning(None, "라이선스 확인", str(e))

    if not license_result or not license_result.get("ok"):
        if license_result and license_result.get("code") not in ("NO_LICENSE", None):
            QMessageBox.warning(
                None,
                "라이선스 사용 불가",
                license_result.get("message") or "라이선스를 확인할 수 없습니다."
            )

        dialog = LicenseDialog(license_service)
        if dialog.exec() != QDialog.Accepted:
            return

    win = MainWindow(db, logs)
    win.resize(1280, 820)
    win.show()

    QTimer.singleShot(1800, win.manual_update_check)
    sys.exit(app.exec())

if __name__ == "__main__":
    from PySide6.QtWidgets import QDialog
    main()
