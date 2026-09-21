import re
from pathlib import Path
from openpyxl import load_workbook

def normalize_phone(value):
    if value is None:
        return None
    s = re.sub(r"[^0-9+]", "", str(value).strip())
    if s.startswith("+82"):
        s = "0" + s[3:]
    elif s.startswith("82"):
        s = "0" + s[2:]
    s = re.sub(r"\D", "", s)
    if len(s) < 9 or len(s) > 11:
        return None
    return s

class DBImportService:
    def __init__(self, db, logs):
        self.db = db
        self.logs = logs

    def import_xlsx(self, path):
        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        stats = {"total": 0, "valid": 0, "duplicate": 0, "invalid": 0}

        for row in ws.iter_rows(values_only=True):
            raw = row[0] if row else None
            if raw is None:
                continue
            stats["total"] += 1
            normalized = normalize_phone(raw)
            if not normalized:
                stats["invalid"] += 1
                continue
            try:
                self.db.execute(
                    "INSERT INTO recipients(source_file,phone,normalized_phone,status) VALUES(?,?,?,'PENDING')",
                    (Path(path).name, str(raw), normalized)
                )
                stats["valid"] += 1
            except Exception:
                stats["duplicate"] += 1

        self.logs.write(
            "INFO",
            "DB",
            f"DB 업로드 완료 / 전체 {stats['total']} / 정상 {stats['valid']} / 중복 {stats['duplicate']} / 오류 {stats['invalid']}"
        )
        return stats
