import sys
from PySide6.QtWidgets import QApplication
from app.db.database import Database
from app.core.logging_service import LogService
from app.ui.main_window import MainWindow

def main():
    db = Database()
    db.initialize()
    logs = LogService(db)
    app = QApplication(sys.argv)
    app.setApplicationName("AngelToggle")
    win = MainWindow(db, logs)
    win.resize(1280, 820)
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
