import csv
from pathlib import Path

from openpyxl import load_workbook

from app.core.phone_utils import normalize_korean_phone


MIN_COUNT = 5000
MAX_COUNT = 1_000_000
UNIT_PRICE_TENTHS_KRW = 6


class TelegramCheckError(Exception):
    def __init__(self, code, message, data=None):
        super().__init__(message)
        self.code = code
        self.data = data or {}


def format_domestic_phone(value):
    digits = normalize_korean_phone(value)
    if not digits:
        return ""
    if digits.startswith("010") and len(digits) == 11:
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    if digits.startswith("02") and len(digits) in (9, 10):
        mid = 5 if len(digits) == 9 else 6
        return f"{digits[:2]}-{digits[2:mid]}-{digits[mid:]}"
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    if len(digits) == 11:
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    return digits


class TelegramCheckService:
    def __init__(self, db, logs=None):
        self.db = db
        self.logs = logs

    def _iter_values(self, path):
        ext = Path(path).suffix.lower()

        if ext in {".txt", ".csv"}:
            opened = None
            for encoding in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
                try:
                    opened = open(path, "r", encoding=encoding, newline="")
                    opened.read(2048)
                    opened.seek(0)
                    break
                except UnicodeDecodeError:
                    if opened:
                        opened.close()
                    opened = None

            if opened is None:
                raise TelegramCheckError("FILE_ENCODING", "파일 인코딩을 읽을 수 없습니다.")

            with opened:
                if ext == ".txt":
                    for line in opened:
                        value = line.strip()
                        if value:
                            yield value
                else:
                    for row in csv.reader(opened):
                        if row and str(row[0]).strip():
                            yield str(row[0]).strip()
            return

        if ext == ".xlsx":
            wb = load_workbook(path, read_only=True, data_only=True)
            try:
                ws = wb.active
                for row in ws.iter_rows(values_only=True):
                    if row and row[0] is not None and str(row[0]).strip():
                        yield str(row[0]).strip()
            finally:
                wb.close()
            return

        raise TelegramCheckError(
            "FILE_TYPE",
            "지원하지 않는 파일입니다. TXT / CSV / XLSX만 업로드할 수 있습니다.",
        )

    def prepare_file(self, path, filter_type="ALL"):
        filter_type = str(filter_type or "ALL").upper()
        if filter_type not in {"1D", "3D", "7D", "ALL"}:
            raise TelegramCheckError("INVALID_FILTER", "검수 조건이 올바르지 않습니다.")

        task_id = self.db.execute(
            "INSERT INTO telegram_check_tasks(source_file,filter_type,status) "
            "VALUES(?,?,'DRAFT')",
            (Path(path).name, filter_type),
        )

        total = valid = duplicate = invalid = 0
        seen = set()
        batch = []

        with self.db.connection() as conn:
            for raw in self._iter_values(path):
                total += 1
                if total > MAX_COUNT:
                    conn.execute(
                        "UPDATE telegram_check_tasks SET status='FAILED',"
                        "error_code='MAX_COUNT',error_message=?,updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=?",
                        ("1회 최대 검수 수량은 1,000,000건입니다.", task_id),
                    )
                    raise TelegramCheckError(
                        "MAX_COUNT",
                        "1회 최대 검수 수량은 1,000,000건입니다.",
                    )

                normalized = normalize_korean_phone(raw)
                if not normalized:
                    invalid += 1
                    batch.append((task_id, raw, None, None, "INVALID"))
                elif normalized in seen:
                    duplicate += 1
                    batch.append(
                        (task_id, raw, normalized, format_domestic_phone(normalized), "DUPLICATE")
                    )
                else:
                    seen.add(normalized)
                    valid += 1
                    batch.append(
                        (task_id, raw, normalized, format_domestic_phone(normalized), "VALID")
                    )

                if len(batch) >= 2000:
                    conn.executemany(
                        "INSERT INTO telegram_check_inputs("
                        "task_id,raw_phone,normalized_phone,display_phone,input_status"
                        ") VALUES(?,?,?,?,?)",
                        batch,
                    )
                    batch.clear()

            if batch:
                conn.executemany(
                    "INSERT INTO telegram_check_inputs("
                    "task_id,raw_phone,normalized_phone,display_phone,input_status"
                    ") VALUES(?,?,?,?,?)",
                    batch,
                )

            amount_tenths = valid * UNIT_PRICE_TENTHS_KRW
            conn.execute(
                "UPDATE telegram_check_tasks SET "
                "total_input_count=?,valid_count=?,duplicate_count=?,invalid_count=?,"
                "charged_count=?,amount_tenths_krw=?,updated_at=CURRENT_TIMESTAMP "
                "WHERE id=?",
                (total, valid, duplicate, invalid, valid, amount_tenths, task_id),
            )

        if self.logs:
            self.logs.write(
                "INFO",
                "가입자검수",
                f"파일 검증 완료 / 작업 #{task_id} / 입력 {total:,} / "
                f"정상 {valid:,} / 중복 {duplicate:,} / 오류 {invalid:,}",
            )

        return self.task_summary(task_id)

    def task_summary(self, task_id):
        row = self.db.fetchone("SELECT * FROM telegram_check_tasks WHERE id=?", (task_id,))
        if not row:
            raise TelegramCheckError("TASK_NOT_FOUND", "검수 작업을 찾을 수 없습니다.")
        data = dict(row)
        data["amount_krw"] = data["amount_tenths_krw"] / 10.0
        data["refund_krw"] = data["refund_tenths_krw"] / 10.0
        return data

    def list_tasks(self, limit=100):
        return self.db.fetchall(
            "SELECT * FROM telegram_check_tasks ORDER BY id DESC LIMIT ?",
            (int(limit),),
        )

    def can_start(self, task_id):
        row = self.task_summary(task_id)
        count = int(row["charged_count"] or 0)
        if count < MIN_COUNT:
            return False, "최소 검수 수량은 5,000건입니다."
        if count > MAX_COUNT:
            return False, "1회 최대 검수 수량은 1,000,000건입니다."
        return True, ""
