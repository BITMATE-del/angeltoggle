from pathlib import Path
from openpyxl import load_workbook
from app.core.phone_utils import normalize_korean_phone, format_korean_international

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
        source_file = Path(path).name
        import_id = self.db.execute(
            "INSERT INTO import_runs(source_file) VALUES(?)",
            (source_file,)
        )

        stats = {
            "import_id": import_id,
            "total": 0,
            "valid": 0,
            "duplicate": 0,
            "invalid": 0,
            "duplicate_numbers": [],
        }
        seen_in_file = set()

        for row in ws.iter_rows(values_only=True):
            raw = row[0] if row else None
            if raw is None:
                continue

            stats["total"] += 1
            normalized = normalize_phone(raw)
            if not normalized:
                stats["invalid"] += 1
                continue

            reason = None
            if normalized in seen_in_file:
                reason = "업로드 파일 내부 중복"
            else:
                exists = self.db.fetchone(
                    "SELECT id FROM recipients WHERE normalized_phone=? LIMIT 1",
                    (normalized,)
                )
                if exists:
                    reason = "기존 데이터베이스 중복"

            if reason:
                stats["duplicate"] += 1
                stats["duplicate_numbers"].append(normalized)
                self.db.execute(
                    "INSERT INTO import_duplicates(import_id,raw_phone,normalized_phone,reason) VALUES(?,?,?,?)",
                    (import_id, str(raw), normalized, reason)
                )
                self.logs.write(
                    "WARNING", "DB",
                    f"중복번호 검수: {format_korean_international(normalized)} / {reason}"
                )
                continue

            seen_in_file.add(normalized)
            self.db.execute(
                "INSERT INTO recipients(source_file,import_id,phone,normalized_phone,status,contact_status) "
                "VALUES(?,?,?,?, 'PENDING','NOT_ADDED')",
                (source_file, import_id, str(raw), normalized)
            )
            stats["valid"] += 1

        self.db.execute(
            "UPDATE import_runs SET total_count=?,added_count=?,duplicate_count=?,invalid_count=? WHERE id=?",
            (stats["total"], stats["valid"], stats["duplicate"], stats["invalid"], import_id)
        )

        self.logs.write(
            "INFO", "DB",
            f"DB 업로드 완료 / 전체 {stats['total']} / 즉시사용 {stats['valid']} / "
            f"중복검수 {stats['duplicate']} / 오류 {stats['invalid']}"
        )
        return stats

    def approve_duplicates(self, import_id):
        rows = self.db.fetchall(
            "SELECT * FROM import_duplicates WHERE import_id=? AND processed=0 ORDER BY id",
            (import_id,)
        )
        added = 0
        with self.db.connection() as conn:
            source = conn.execute(
                "SELECT source_file FROM import_runs WHERE id=?", (import_id,)
            ).fetchone()
            source_file = source["source_file"] if source else "중복검수"
            for row in rows:
                conn.execute(
                    "INSERT INTO recipients(source_file,import_id,phone,normalized_phone,status,contact_status) "
                    "VALUES(?,?,?,?, 'PENDING','NOT_ADDED')",
                    (source_file, import_id, row["raw_phone"], row["normalized_phone"])
                )
                conn.execute(
                    "UPDATE import_duplicates SET approved=1,processed=1 WHERE id=?",
                    (row["id"],)
                )
                added += 1
            conn.execute(
                "UPDATE import_runs SET duplicate_decision='APPROVED',added_count=added_count+? WHERE id=?",
                (added, import_id)
            )

        self.logs.write("INFO", "DB", f"중복번호 {added}개를 사용 DB에 추가했습니다.")
        return added

    def reject_duplicates(self, import_id):
        count = self.db.execute_rowcount(
            "UPDATE import_duplicates SET approved=0,processed=1 WHERE import_id=? AND processed=0",
            (import_id,)
        )
        self.db.execute(
            "UPDATE import_runs SET duplicate_decision='REJECTED' WHERE id=?",
            (import_id,)
        )
        self.logs.write("INFO", "DB", f"중복번호 {count}개를 제외했습니다.")
        return count

    def get_duplicates(self, import_id):
        return self.db.fetchall(
            "SELECT normalized_phone,reason FROM import_duplicates WHERE import_id=? ORDER BY id",
            (import_id,)
        )
