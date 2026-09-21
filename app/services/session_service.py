import zipfile
from pathlib import Path
from app.core.paths import SESSIONS_DIR

class SessionService:
    def __init__(self, db, logs):
        self.db = db
        self.logs = logs

    def import_zip(self, zip_path):
        imported = skipped = 0
        with zipfile.ZipFile(zip_path, "r") as z:
            for member in z.namelist():
                if not member.lower().endswith(".session"):
                    continue
                name = Path(member).name
                target = SESSIONS_DIR / name
                if target.exists():
                    skipped += 1
                    continue
                with z.open(member) as src, open(target, "wb") as dst:
                    dst.write(src.read())
                try:
                    self.db.execute(
                        "INSERT INTO telegram_accounts(name,session_file,status) VALUES(?,?,?)",
                        (target.stem, str(target), "IMPORTED")
                    )
                    imported += 1
                except Exception:
                    skipped += 1
        self.logs.write("INFO", "ACCOUNT", f"세션 ZIP 등록 완료 / 등록 {imported} / 건너뜀 {skipped}")
        return {"imported": imported, "skipped": skipped}
