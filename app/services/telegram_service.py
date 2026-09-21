import re
import secrets
from telethon import TelegramClient, errors, functions, types

class RecipientError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code

class AccountWorkerError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code

def phone_to_e164(phone):
    digits = re.sub(r"\D", "", str(phone))
    if digits.startswith("82"):
        return "+" + digits
    if digits.startswith("0"):
        return "+82" + digits[1:]
    return "+" + digits

def normalize_post_code(value):
    raw = (value or "").strip()
    if not raw:
        return ""
    if "?" in raw:
        query = raw.split("?", 1)[1]
        for part in query.split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                if k.lower() in {"start", "code", "q", "query"} and v:
                    return v
    if "/" in raw and raw.startswith(("http://", "https://", "t.me/", "telegram.me/")):
        tail = raw.rstrip("/").split("/")[-1]
        if tail and tail.lower() not in {"postbot", "@postbot"}:
            return tail
    return raw

class TelegramService:
    def __init__(self, db, logs):
        self.db = db
        self.logs = logs

    def api_configured(self):
        return bool(self.db.get_setting("telegram_api_id")) and bool(self.db.get_setting("telegram_api_hash"))

    def _api(self):
        try:
            api_id = int(self.db.get_setting("telegram_api_id"))
        except Exception:
            raise RuntimeError("Telegram API ID가 올바르지 않습니다.")
        api_hash = self.db.get_setting("telegram_api_hash")
        if not api_hash:
            raise RuntimeError("Telegram API HASH가 없습니다.")
        return api_id, api_hash

    def make_client(self, account):
        api_id, api_hash = self._api()
        return TelegramClient(account["session_file"], api_id, api_hash)

    async def connect_account(self, account):
        client = self.make_client(account)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise AccountWorkerError("SESSION_ERROR", "세션 인증이 만료되었거나 로그인되어 있지 않습니다.")
            me = await client.get_me()
            self.db.execute(
                "UPDATE telegram_accounts SET phone=?,telegram_uid=?,username=?,status='NORMAL',"
                "last_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (
                    getattr(me, "phone", None),
                    str(getattr(me, "id", "")),
                    getattr(me, "username", None),
                    account["id"],
                ),
            )
            return client
        except AccountWorkerError:
            await client.disconnect()
            raise
        except (errors.AuthKeyUnregisteredError, errors.SessionRevokedError, errors.UserDeactivatedError) as e:
            await client.disconnect()
            raise AccountWorkerError("SESSION_ERROR", type(e).__name__)
        except Exception:
            await client.disconnect()
            raise

    async def import_contact(self, client, phone):
        e164 = phone_to_e164(phone)
        contact = types.InputPhoneContact(
            client_id=secrets.randbits(63),
            phone=e164,
            first_name="Customer",
            last_name="",
        )
        try:
            result = await client(functions.contacts.ImportContactsRequest([contact]))
            if not result.users:
                raise RecipientError("USER_NOT_FOUND", "Telegram 사용자를 찾을 수 없습니다.")
            return result.users[0]
        except RecipientError:
            raise
        except errors.FloodWaitError as e:
            raise AccountWorkerError("FLOOD_WAIT", f"FloodWait {e.seconds}s")
        except errors.PeerFloodError as e:
            raise AccountWorkerError("PEER_FLOOD", str(e))
        except (errors.PhoneNumberInvalidError, errors.PhoneNumberBannedError) as e:
            raise RecipientError("INVALID_PHONE", type(e).__name__)
        except Exception as e:
            name = type(e).__name__
            if "Flood" in name or "AuthKey" in name or "Session" in name:
                raise AccountWorkerError("ACCOUNT_ERROR", name)
            raise RecipientError("CONTACT_ADD_FAILED", name)

    async def send_postbot(self, client, peer, bot_username, post_value):
        bot_username = (bot_username or "@PostBot").strip()
        if not bot_username.startswith("@"):
            bot_username = "@" + bot_username
        post_code = normalize_post_code(post_value)
        if not post_code:
            raise RecipientError("POSTBOT_INVALID_CODE", "PostBot 게시물 코드가 비어 있습니다.")

        try:
            results = await client.inline_query(bot_username, post_code)
            if not results:
                raise RecipientError("POSTBOT_RESULT_NOT_FOUND", "PostBot 인라인 결과가 없습니다.")
            message = await results[0].click(peer)
            message_id = getattr(message, "id", None)
            if not message_id:
                raise RecipientError("MESSAGE_SEND_FAILED", "Telegram Message ID를 확인하지 못했습니다.")
            return str(message_id)
        except RecipientError:
            raise
        except errors.FloodWaitError as e:
            raise AccountWorkerError("FLOOD_WAIT", f"FloodWait {e.seconds}s")
        except errors.PeerFloodError as e:
            raise AccountWorkerError("PEER_FLOOD", str(e))
        except (errors.AuthKeyUnregisteredError, errors.SessionRevokedError) as e:
            raise AccountWorkerError("SESSION_ERROR", type(e).__name__)
        except errors.UserPrivacyRestrictedError as e:
            raise RecipientError("PRIVACY_RESTRICTED", type(e).__name__)
        except Exception as e:
            name = type(e).__name__
            if "Flood" in name or "AuthKey" in name or "Session" in name:
                raise AccountWorkerError("ACCOUNT_ERROR", name)
            raise RecipientError("MESSAGE_SEND_FAILED", name)
