import shutil
import zipfile
from pathlib import Path

from app.core.paths import SESSIONS_DIR


class SessionService:
    def __init__(self, db, logs):
        self.db = db
        self.logs = logs
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    def _unique_target(self, name):
        base = Path(name).stem or "telegram"
        suffix = ".session"
        target = SESSIONS_DIR / (base + suffix)
        if not target.exists():
            return target, False

        index = 2
        while True:
            candidate = SESSIONS_DIR / f"{base}_{index}{suffix}"
            if not candidate.exists():
                return candidate, True
            index += 1

    def _register_file(self, source_path, original_name=None):
        source = Path(source_path)
        if not source.exists() or not source.is_file():
            return False, False, "파일을 찾을 수 없음"

        if source.suffix.lower() != ".session":
            return False, False, "SESSION 확장자가 아님"

        try:
            size = source.stat().st_size
        except Exception:
            size = 0

        if size <= 0:
            return False, False, "빈 세션 파일"

        target, renamed = self._unique_target(original_name or source.name)

        try:
            shutil.copy2(source, target)
            self.db.execute(
                "INSERT INTO telegram_accounts(name,session_file,status) VALUES(?,?,?)",
                (target.stem, str(target), "IMPORTED"),
            )
            return True, renamed, None
        except Exception as e:
            try:
                if target.exists():
                    target.unlink()
            except Exception:
                pass
            return False, renamed, f"{type(e).__name__}: {e}"

    def import_session_file(self, session_path):
        ok, renamed, error = self._register_file(session_path)
        result = {
            "found": 1,
            "imported": 1 if ok else 0,
            "renamed": 1 if ok and renamed else 0,
            "failed": 0 if ok else 1,
            "skipped": 0,
        }

        if ok:
            self.logs.write(
                "INFO",
                "ACCOUNT",
                f"세션 파일 등록 완료 / 등록 1 / 이름변경 {result['renamed']}",
            )
        else:
            self.logs.write(
                "ERROR",
                "ACCOUNT",
                f"세션 파일 등록 실패 / {error}",
            )
        return result

    def import_zip(self, zip_path):
        found = imported = renamed = failed = skipped = 0
        temp_root = SESSIONS_DIR / "_import_temp"

        if temp_root.exists():
            shutil.rmtree(temp_root, ignore_errors=True)
        temp_root.mkdir(parents=True, exist_ok=True)

        try:
            with zipfile.ZipFile(zip_path, "r") as z:
                members = [
                    info for info in z.infolist()
                    if not info.is_dir()
                    and Path(info.filename).suffix.lower() == ".session"
                ]

                found = len(members)

                for index, info in enumerate(members, start=1):
                    original_name = Path(info.filename).name
                    temp_file = temp_root / f"{index}_{original_name}"

                    try:
                        with z.open(info, "r") as src, open(temp_file, "wb") as dst:
                            shutil.copyfileobj(src, dst)

                        ok, was_renamed, error = self._register_file(
                            temp_file,
                            original_name=original_name,
                        )

                        if ok:
                            imported += 1
                            if was_renamed:
                                renamed += 1
                            self.logs.write(
                                "INFO",
                                "ACCOUNT",
                                f"세션 인식: {info.filename} -> 등록 완료"
                                + (" / 동일 이름 자동변경" if was_renamed else ""),
                            )
                        else:
                            failed += 1
                            self.logs.write(
                                "ERROR",
                                "ACCOUNT",
                                f"세션 등록 실패: {info.filename} / {error}",
                            )
                    except Exception as e:
                        failed += 1
                        self.logs.write(
                            "ERROR",
                            "ACCOUNT",
                            f"세션 압축해제 실패: {info.filename} / {type(e).__name__}: {e}",
                        )

            if found == 0:
                self.logs.write(
                    "WARNING",
                    "ACCOUNT",
                    "ZIP 안에서 .session 파일을 찾지 못했습니다.",
                )

        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

        self.logs.write(
            "INFO",
            "ACCOUNT",
            f"세션 ZIP 등록 완료 / 발견 {found} / 등록 {imported} / "
            f"이름변경 {renamed} / 실패 {failed}",
        )

        return {
            "found": found,
            "imported": imported,
            "renamed": renamed,
            "failed": failed,
            "skipped": skipped,
        }


    def delete_account(self, account_id):
        account = self.db.fetchone(
            "SELECT * FROM telegram_accounts WHERE id=?",
            (account_id,),
        )
        if not account:
            raise RuntimeError("삭제할 계정을 찾을 수 없습니다.")

        active = self.db.fetchone(
            "SELECT COUNT(*) c FROM campaign_recipients "
            "WHERE assigned_account_id=? "
            "AND (contact_status IN ('WAITING','ADDING','CONTACT_PAUSED') "
            "OR status IN ('ASSIGNED','SENDING','SEND_PAUSED'))",
            (account_id,),
        )
        if active and int(active["c"] or 0) > 0:
            raise RuntimeError("현재 작업에 배정된 계정이라 삭제할 수 없습니다. 작업 종료 후 삭제해주세요.")

        session_path = Path(account["session_file"] or "")
        self.db.execute(
            "DELETE FROM telegram_accounts WHERE id=?",
            (account_id,),
        )

        if session_path and session_path.exists():
            try:
                session_path.unlink()
            except Exception as e:
                self.logs.write(
                    "WARNING",
                    "ACCOUNT",
                    f"계정 DB 삭제 완료 / 세션파일 삭제 실패: {type(e).__name__}: {e}",
                    account_id=account_id,
                )
                return {"deleted": True, "session_deleted": False}

        self.logs.write(
            "INFO",
            "ACCOUNT",
            "등록 계정과 세션파일을 삭제했습니다.",
            account_id=account_id,
        )
        return {"deleted": True, "session_deleted": True}
