import csv
import io
import time
from pathlib import Path

import httpx

from app.core.paths import EXPORTS_DIR
from app.core.phone_utils import normalize_korean_phone
from app.services.secret_store import load_telegram_check_api_token
from app.services.telegram_check_service import (
    TelegramCheckError,
    UNIT_PRICE_TENTHS_KRW,
    format_domestic_phone,
    to_api_phone,
)


DEFAULT_BASE_URL = "https://api.ftm111.pw/api/tg"
DEFAULT_COUNTRY = "KR"
DEFAULT_FILTER_TYPE = "1"
BATCH_SIZE = 50_000
POLL_SECONDS = 5
REQUEST_TIMEOUT = 60
MAX_WAIT_SECONDS = 3600


class TelegramCheckApiService:
    def __init__(self, db, logs=None):
        self.db = db
        self.logs = logs

    def _base_url(self):
        return (
            self.db.get_setting("telegram_check_api_base_url", DEFAULT_BASE_URL)
            or DEFAULT_BASE_URL
        ).rstrip("/")

    def _token(self):
        token = load_telegram_check_api_token()
        if not token:
            raise TelegramCheckError(
                "API_TOKEN_REQUIRED",
                "가입자 검수 API 암호가 아직 고정 저장되지 않았습니다. 설정 메뉴에서 한 번 저장해주세요.",
            )
        return token

    def _headers(self):
        return {"token": self._token()}

    def test_connection(self):
        url = self._base_url()
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
                response = client.get(url, headers=self._headers())
            if response.status_code in (401, 403):
                raise TelegramCheckError(
                    "API_AUTH_FAILED",
                    "API 인증에 실패했습니다. API 암호를 확인해주세요.",
                )
            return {
                "ok": True,
                "status_code": response.status_code,
            }
        except TelegramCheckError:
            raise
        except Exception as exc:
            raise TelegramCheckError(
                "API_CONNECTION_FAILED",
                f"가입자 검수 API에 연결할 수 없습니다: {exc}",
            ) from exc

    def _task_phones(self, task_id):
        offset = 0
        chunk = 5000
        while True:
            rows = self.db.fetchall(
                "SELECT normalized_phone FROM telegram_check_inputs "
                "WHERE task_id=? AND input_status='VALID' "
                "ORDER BY id LIMIT ? OFFSET ?",
                (task_id, chunk, offset),
            )
            if not rows:
                break
            for row in rows:
                value = to_api_phone(row["normalized_phone"])
                if value:
                    yield value
            offset += len(rows)

    def _create_batch(self, task_id, batch_no, phones):
        payload = "\n".join(phones) + "\n"
        files = {"file": ("phones.txt", payload.encode("utf-8"), "text/plain")}
        data = {
            "filter_type": DEFAULT_FILTER_TYPE,
            "conuntry": DEFAULT_COUNTRY,
        }

        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
                response = client.post(
                    f"{self._base_url()}/addTask",
                    headers=self._headers(),
                    data=data,
                    files=files,
                )
            response.raise_for_status()
            result = response.json()
        except Exception as exc:
            raise TelegramCheckError(
                "API_ADD_TASK_FAILED",
                f"외부 검수 작업 등록에 실패했습니다: {exc}",
            ) from exc

        if int(result.get("code", 0) or 0) != 1 or not result.get("id"):
            raise TelegramCheckError(
                "API_ADD_TASK_FAILED",
                str(result.get("msg") or "외부 검수 작업 등록에 실패했습니다."),
                result,
            )

        api_task_id = str(result["id"])
        self.db.execute(
            "INSERT OR REPLACE INTO telegram_check_batches("
            "task_id,batch_no,api_task_id,item_count,status,updated_at"
            ") VALUES(?,?,?,?, 'PROCESSING', CURRENT_TIMESTAMP)",
            (task_id, batch_no, api_task_id, len(phones)),
        )
        return api_task_id

    def _check_batch(self, api_task_id):
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
                response = client.get(
                    f"{self._base_url()}/checkTask",
                    headers=self._headers(),
                    params={"id": api_task_id},
                )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            raise TelegramCheckError(
                "API_STATUS_FAILED",
                f"외부 검수 상태 조회에 실패했습니다: {exc}",
            ) from exc

    def _wait_batch(self, task_id, batch_no, api_task_id, progress=None):
        started = time.monotonic()
        while True:
            status = self._check_batch(api_task_id)
            total = int(status.get("total") or 0)
            checked = int(status.get("has_check") or 0)
            done = bool(status.get("result_file")) or (total > 0 and checked >= total)

            self.db.execute(
                "UPDATE telegram_check_batches SET total_count=?,checked_count=?,"
                "status=?,updated_at=CURRENT_TIMESTAMP WHERE task_id=? AND batch_no=?",
                (
                    total,
                    checked,
                    "COMPLETED" if done else "PROCESSING",
                    task_id,
                    batch_no,
                ),
            )

            if progress:
                progress({
                    "stage": "processing",
                    "task_id": task_id,
                    "batch_no": batch_no,
                    "api_task_id": api_task_id,
                    "total": total,
                    "checked": checked,
                })

            if done:
                return status

            if time.monotonic() - started > MAX_WAIT_SECONDS:
                raise TelegramCheckError(
                    "API_TIMEOUT",
                    f"외부 검수 작업 대기시간을 초과했습니다. 배치 #{batch_no}",
                )
            time.sleep(POLL_SECONDS)

    def _download_batch_result(self, task_id, batch_no, api_task_id):
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
                response = client.get(
                    f"{self._base_url()}/exportPhone",
                    headers=self._headers(),
                    params={"id": api_task_id},
                )
            response.raise_for_status()
            content = response.content
        except Exception as exc:
            raise TelegramCheckError(
                "API_EXPORT_FAILED",
                f"외부 검수 결과 다운로드에 실패했습니다: {exc}",
            ) from exc

        output_dir = EXPORTS_DIR / "telegram_check" / f"task_{task_id}"
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"batch_{batch_no:03d}.csv"
        path.write_bytes(content)

        self.db.execute(
            "UPDATE telegram_check_batches SET result_file_path=?,status='COMPLETED',"
            "completed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP "
            "WHERE task_id=? AND batch_no=?",
            (str(path), task_id, batch_no),
        )
        return path

    def _decode_csv(self, content):
        last_error = None
        for encoding in ("utf-8-sig", "utf-8", "gb18030", "cp949", "euc-kr"):
            try:
                return list(csv.reader(io.StringIO(content.decode(encoding))))
            except Exception as exc:
                last_error = exc
        raise TelegramCheckError(
            "RESULT_ENCODING",
            f"결과 CSV를 읽을 수 없습니다: {last_error}",
        )

    def _merge_batch_results(self, task_id, paths, failed_phones=None):
        failed_phones = {
            normalize_korean_phone(value)
            for value in (failed_phones or [])
            if normalize_korean_phone(value)
        }

        matched = {}
        for path in paths:
            rows = self._decode_csv(Path(path).read_bytes())
            first = True
            for row in rows:
                if not row:
                    continue

                if first:
                    first = False
                    first_cell = str(row[0] or "").strip().lower()
                    if any(k in first_cell for k in ("phone", "전화", "mobile")):
                        continue

                raw_phone = row[0] if len(row) > 0 else ""
                normalized = normalize_korean_phone(raw_phone)
                if not normalized:
                    continue

                telegram_id = str(row[1] or "").strip() if len(row) > 1 else ""
                telegram_username = str(row[2] or "").strip() if len(row) > 2 else ""
                telegram_active = str(row[-1] or "").strip() if len(row) > 3 else ""

                if telegram_id == "0":
                    telegram_id = ""
                if telegram_username == "0":
                    telegram_username = ""

                matched[normalized] = {
                    "telegram_id": telegram_id or None,
                    "telegram_username": telegram_username or None,
                    "telegram_active": telegram_active or None,
                }

        inputs = self.db.fetchall(
            "SELECT normalized_phone,display_phone FROM telegram_check_inputs "
            "WHERE task_id=? AND input_status='VALID' ORDER BY id",
            (task_id,),
        )

        inserts = []
        joined_count = 0
        not_joined_count = 0
        uncertain_count = 0

        for row in inputs:
            normalized = normalize_korean_phone(row["normalized_phone"])
            display = row["display_phone"] or format_domestic_phone(normalized)

            if normalized in failed_phones:
                status = "확인불가"
                telegram_id = telegram_username = telegram_active = None
                raw_status = "BATCH_FAILED"
                uncertain_count += 1
            elif normalized in matched:
                item = matched[normalized]
                status = "가입"
                telegram_id = item["telegram_id"]
                telegram_username = item["telegram_username"]
                telegram_active = item["telegram_active"]
                raw_status = "API_RESULT"
                joined_count += 1
            else:
                status = "미가입"
                telegram_id = telegram_username = telegram_active = None
                raw_status = "NOT_IN_EXPORT"
                not_joined_count += 1

            inserts.append((
                task_id,
                display,
                status,
                telegram_id,
                telegram_username,
                telegram_active,
                raw_status,
            ))

        with self.db.connection() as conn:
            conn.execute("DELETE FROM telegram_check_results WHERE task_id=?", (task_id,))
            for offset in range(0, len(inserts), 2000):
                conn.executemany(
                    "INSERT INTO telegram_check_results("
                    "task_id,phone_number,telegram_check_status,telegram_id,"
                    "telegram_username,telegram_active,raw_status"
                    ") VALUES(?,?,?,?,?,?,?)",
                    inserts[offset:offset + 2000],
                )

            requested = len(inserts)
            failed_count = uncertain_count
            processed_count = max(0, requested - failed_count)
            refund_tenths = failed_count * UNIT_PRICE_TENTHS_KRW
            status = "COMPLETED" if failed_count == 0 else "PARTIAL"

            conn.execute(
                "UPDATE telegram_check_tasks SET success_count=?,missing_count=?,"
                "refund_tenths_krw=?,status=?,completed_at=CURRENT_TIMESTAMP,"
                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (processed_count, failed_count, refund_tenths, status, task_id),
            )

        return {
            "requested_count": len(inserts),
            "processed_count": max(0, len(inserts) - uncertain_count),
            "joined_count": joined_count,
            "not_joined_count": not_joined_count,
            "uncertain_count": uncertain_count,
            "success_count": max(0, len(inserts) - uncertain_count),
            "missing_count": uncertain_count,
            "status": "COMPLETED" if uncertain_count == 0 else "PARTIAL",
        }

    def run_task(self, task_id, progress=None):
        task = self.db.fetchone(
            "SELECT * FROM telegram_check_tasks WHERE id=?",
            (task_id,),
        )
        if not task:
            raise TelegramCheckError("TASK_NOT_FOUND", "검수 작업을 찾을 수 없습니다.")

        count = int(task["charged_count"] or 0)
        if count < 100:
            raise TelegramCheckError("MIN_COUNT", "최소 검수 수량은 100건입니다.")
        if count > 1_000_000:
            raise TelegramCheckError("MAX_COUNT", "1회 최대 검수 수량은 1,000,000건입니다.")

        self._token()

        self.db.execute(
            "DELETE FROM telegram_check_batches WHERE task_id=?",
            (task_id,),
        )
        self.db.execute(
            "UPDATE telegram_check_tasks SET status='PROCESSING',"
            "started_at=CURRENT_TIMESTAMP,error_code=NULL,error_message=NULL,"
            "updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (task_id,),
        )

        if self.logs:
            self.logs.write(
                "INFO",
                "가입자검수",
                f"외부 API 검수 시작 / 작업 #{task_id} / {count:,}건",
            )

        result_paths = []
        failed_phones = set()
        batch_no = 0
        current = []

        def process_batch(phones, number):
            try:
                api_task_id = self._create_batch(task_id, number, phones)
                if progress:
                    progress({
                        "stage": "submitted",
                        "task_id": task_id,
                        "batch_no": number,
                        "api_task_id": api_task_id,
                        "count": len(phones),
                    })

                self._wait_batch(task_id, number, api_task_id, progress)
                result_paths.append(
                    self._download_batch_result(task_id, number, api_task_id)
                )
                return True
            except Exception as exc:
                self.db.execute(
                    "UPDATE telegram_check_batches SET status='FAILED',error_message=?,"
                    "updated_at=CURRENT_TIMESTAMP WHERE task_id=? AND batch_no=?",
                    (str(exc)[:1000], task_id, number),
                )
                for phone in phones:
                    normalized = normalize_korean_phone(phone)
                    if normalized:
                        failed_phones.add(normalized)
                if self.logs:
                    self.logs.write(
                        "ERROR",
                        "가입자검수",
                        f"외부 API 배치 실패 / 작업 #{task_id} / 배치 #{number} / "
                        f"{len(phones):,}건 / {exc}",
                    )
                if progress:
                    progress({
                        "stage": "batch_failed",
                        "task_id": task_id,
                        "batch_no": number,
                        "count": len(phones),
                        "error": str(exc),
                    })
                return False

        for phone in self._task_phones(task_id):
            current.append(phone)
            if len(current) >= BATCH_SIZE:
                batch_no += 1
                process_batch(list(current), batch_no)
                current = []

        if current:
            batch_no += 1
            process_batch(list(current), batch_no)

        if batch_no == 0:
            raise TelegramCheckError("NO_INPUT", "검수할 정상 전화번호가 없습니다.")

        result = self._merge_batch_results(
            task_id,
            result_paths,
            failed_phones=failed_phones,
        )
        result["task_id"] = task_id
        result["batch_count"] = batch_no

        if self.logs:
            level = "SUCCESS" if result["status"] == "COMPLETED" else "WARNING"
            self.logs.write(
                level,
                "가입자검수",
                f"외부 API 검수 완료 / 작업 #{task_id} / "
                f"가입 {result['joined_count']:,} / 미가입 {result['not_joined_count']:,} / "
                f"확인불가 {result['uncertain_count']:,}",
            )
        return result

