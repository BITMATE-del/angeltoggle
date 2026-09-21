import hashlib
import json
import os
import platform
import socket
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

from app.core.paths import BASE_DIR
from app.version import VERSION

LICENSE_API = "https://fgyiofykvpkxpeylcocn.supabase.co/functions/v1/angeltoggle-license"
LICENSE_STATE = BASE_DIR / "license.json"

class LicenseError(Exception):
    pass

def _utcnow():
    return datetime.now(timezone.utc)

def _parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None

def _machine_source():
    parts = [platform.system(), platform.machine(), socket.gethostname()]
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
                value, _ = winreg.QueryValueEx(key, "MachineGuid")
                parts.append(str(value))
        except Exception:
            pass
    return "|".join(parts)

def machine_hash():
    return hashlib.sha256(_machine_source().encode("utf-8", "ignore")).hexdigest()

def device_label():
    return socket.gethostname()[:120]

class LicenseService:
    def __init__(self, logs=None):
        self.logs = logs
        BASE_DIR.mkdir(parents=True, exist_ok=True)

    def load_state(self):
        try:
            return json.loads(LICENSE_STATE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save_state(self, state):
        temp = LICENSE_STATE.with_suffix(".tmp")
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(LICENSE_STATE)

    def clear_state(self):
        try:
            LICENSE_STATE.unlink(missing_ok=True)
        except Exception:
            pass

    def activate(self, code):
        return self._server_request("activate", code)

    def verify_saved(self):
        state = self.load_state()
        code = str(state.get("license_code") or "").strip()
        if not code:
            return {"ok": False, "code": "NO_LICENSE", "message": "라이선스 코드가 등록되지 않았습니다."}

        try:
            return self._server_request("verify", code)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if self._offline_allowed(state):
                if self.logs:
                    self.logs.write("WARNING", "라이선스", "라이선스 서버 연결 실패 / 오프라인 유예기간으로 실행")
                return {
                    "ok": True,
                    "offline": True,
                    "code": "OFFLINE_GRACE",
                    "message": "서버 연결 실패로 오프라인 유예기간을 사용합니다.",
                    "expires_at": state.get("expires_at"),
                    "remaining_days": self._remaining_days(state.get("expires_at")),
                }
            raise LicenseError("라이선스 서버에 연결할 수 없습니다. 인터넷 연결을 확인하세요.") from e

    def _offline_allowed(self, state):
        last = _parse_iso(state.get("last_verified_at"))
        expires = _parse_iso(state.get("expires_at"))
        grace_hours = int(state.get("grace_hours") or 72)
        if not last or not expires:
            return False
        now = _utcnow()
        return now <= expires and now <= (last + timedelta(hours=grace_hours))

    def _remaining_days(self, expires_at):
        expiry = _parse_iso(expires_at)
        if not expiry:
            return None
        seconds = (expiry - _utcnow()).total_seconds()
        return max(0, int((seconds + 86399) // 86400))

    def _server_request(self, action, code):
        payload = json.dumps({
            "action": action,
            "license_code": str(code).strip().upper(),
            "device_hash": machine_hash(),
            "device_label": device_label(),
            "client_version": VERSION,
        }).encode("utf-8")

        req = urllib.request.Request(
            LICENSE_API,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": f"AngelToggle/{VERSION}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                data = json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                data = json.loads(e.read().decode("utf-8"))
            except Exception:
                raise LicenseError(f"라이선스 확인 실패 ({e.code})") from e
            return data

        if data.get("ok"):
            state = {
                "license_code": str(code).strip().upper(),
                "license_id": data.get("license_id"),
                "expires_at": data.get("expires_at"),
                "remaining_days": data.get("remaining_days"),
                "grace_hours": data.get("grace_hours", 72),
                "last_verified_at": data.get("server_time") or _utcnow().isoformat(),
                "minimum_client_version": data.get("minimum_client_version"),
            }
            self.save_state(state)
            if self.logs:
                self.logs.write(
                    "INFO", "라이선스",
                    f"라이선스 정상 / 남은기간 {data.get('remaining_days')}일"
                )
        return data
