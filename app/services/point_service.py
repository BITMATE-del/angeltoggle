import json
import urllib.error
import urllib.request

from app.services.license_service import (
    LICENSE_API,
    LICENSE_STATE,
    machine_hash,
    device_label,
)
from app.version import VERSION


class PointError(Exception):
    def __init__(self, code, message, balance_krw=None):
        super().__init__(message)
        self.code = code
        self.balance_krw = balance_krw


class PointService:
    UNIT_PRICE_KRW = 10

    def __init__(self, logs=None):
        self.logs = logs

    def _state(self):
        try:
            return json.loads(LICENSE_STATE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _code(self):
        code = str(self._state().get("license_code") or "").strip().upper()
        if not code:
            raise PointError("NO_LICENSE", "라이선스 코드가 없습니다.")
        return code

    def _request(self, action, **extra):
        payload = {
            "action": action,
            "license_code": self._code(),
            "device_hash": machine_hash(),
            "device_label": device_label(),
            "client_version": VERSION,
            **extra,
        }

        req = urllib.request.Request(
            LICENSE_API,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
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
                raise PointError("POINT_API_ERROR", f"포인트 서버 오류 ({e.code})") from e
        except Exception as e:
            raise PointError("POINT_API_OFFLINE", "포인트 서버에 연결할 수 없습니다.") from e

        if not data.get("ok"):
            raise PointError(
                str(data.get("code") or "POINT_ERROR"),
                str(data.get("message") or "포인트 처리에 실패했습니다."),
                data.get("point_balance_krw"),
            )
        return data

    def balance(self):
        data = self._request("point_balance")
        return {
            "balance_krw": int(data.get("point_balance_krw") or 0),
            "unit_price_krw": int(data.get("send_unit_price_krw") or self.UNIT_PRICE_KRW),
        }

    def reserve_send(
        self,
        message_key,
        telegram_account_key,
        telegram_account_label,
        campaign_id,
        recipient_id,
    ):
        return self._request(
            "reserve_send",
            message_key=str(message_key),
            telegram_account_key=str(telegram_account_key or ""),
            telegram_account_label=str(telegram_account_label or ""),
            campaign_id=str(campaign_id),
            recipient_id=str(recipient_id),
        )

    def confirm_send(self, message_key, telegram_message_id):
        return self._request(
            "confirm_send",
            message_key=str(message_key),
            telegram_message_id=str(telegram_message_id),
        )

    def cancel_send(self, message_key, reason=""):
        return self._request(
            "cancel_send",
            message_key=str(message_key),
            reason=str(reason or "")[:500],
        )
