import re
import secrets

from app.core.phone_utils import to_telegram_e164, format_korean_international
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
    converted = to_telegram_e164(phone)
    if not converted:
        raise RecipientError("INVALID_PHONE", f"전화번호 형식 오류: {phone}")
    return converted


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


def make_contact_name(recipient_id):
    token = secrets.token_hex(3).upper()
    return f"Customer_{recipient_id}_{token}"


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
            raise RuntimeError("텔레그램 API ID가 올바르지 않습니다.")
        api_hash = self.db.get_setting("telegram_api_hash")
        if not api_hash:
            raise RuntimeError("텔레그램 API HASH가 없습니다.")
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

    async def import_contacts_batch(self, client, targets):
        contacts = []
        ids = {}
        aliases = {}

        for recipient_id, phone in targets:
            client_id = int(recipient_id)
            ids[client_id] = recipient_id
            converted_phone = phone_to_e164(phone)
            alias = make_contact_name(recipient_id)
            aliases[recipient_id] = alias

            contacts.append(
                types.InputPhoneContact(
                    client_id=client_id,
                    phone=converted_phone,
                    first_name=alias,
                    last_name="",
                )
            )

        try:
            result = await client(functions.contacts.ImportContactsRequest(contacts))
            users = {u.id: u for u in result.users}
            success = {}

            for item in result.imported:
                recipient_id = ids.get(int(item.client_id))
                user = users.get(item.user_id)
                if recipient_id is not None and user is not None:
                    success[recipient_id] = {
                        "user": user,
                        "contact_name": aliases.get(recipient_id),
                    }

            missing = [rid for rid, _ in targets if rid not in success]
            return success, missing

        except errors.FloodWaitError as e:
            raise AccountWorkerError("FLOOD_WAIT", f"FloodWait {e.seconds}초")
        except errors.PeerFloodError as e:
            raise AccountWorkerError("PEER_FLOOD", str(e))
        except (errors.AuthKeyUnregisteredError, errors.SessionRevokedError) as e:
            raise AccountWorkerError("SESSION_ERROR", type(e).__name__)
        except Exception as e:
            name = type(e).__name__
            if "Flood" in name or "AuthKey" in name or "Session" in name:
                raise AccountWorkerError("ACCOUNT_ERROR", name)
            raise RecipientError("CONTACT_ADD_FAILED", name)

    async def resolve_imported_user(self, client, user):
        try:
            peer = await client.get_input_entity(user)
            uid = int(getattr(peer, "user_id", 0) or getattr(user, "id", 0) or 0)
            if not uid:
                raise RecipientError("UID_RESOLVE_FAILED", "Telegram UID를 확인하지 못했습니다.")
            return {
                "uid": uid,
                "username": getattr(user, "username", None),
                "peer": peer,
            }
        except RecipientError:
            raise
        except errors.FloodWaitError as e:
            raise AccountWorkerError("FLOOD_WAIT", f"FloodWait {e.seconds}초")
        except errors.PeerFloodError as e:
            raise AccountWorkerError("PEER_FLOOD", str(e))
        except (errors.AuthKeyUnregisteredError, errors.SessionRevokedError) as e:
            raise AccountWorkerError("SESSION_ERROR", type(e).__name__)
        except Exception as e:
            raise RecipientError("UID_RESOLVE_FAILED", type(e).__name__)

    async def current_contact_ids(self, client):
        try:
            contacts = await client.get_contacts()
            return {int(getattr(user, "id", 0) or 0) for user in contacts}
        except errors.FloodWaitError as e:
            raise AccountWorkerError("FLOOD_WAIT", f"FloodWait {e.seconds}초")
        except errors.PeerFloodError as e:
            raise AccountWorkerError("PEER_FLOOD", str(e))
        except Exception as e:
            name = type(e).__name__
            if "Flood" in name or "AuthKey" in name or "Session" in name:
                raise AccountWorkerError("ACCOUNT_ERROR", name)
            raise RecipientError("CONTACT_LIST_FAILED", name)

    async def check_postbot(self, client, bot_username, post_value):
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

            first = results[0]
            title = getattr(first, "title", None) or getattr(first, "description", None) or "게시물 확인됨"
            return {
                "ok": True,
                "post_code": post_code,
                "result_count": len(results),
                "title": str(title),
            }

        except RecipientError:
            raise
        except errors.FloodWaitError as e:
            raise AccountWorkerError("FLOOD_WAIT", f"FloodWait {e.seconds}초")
        except errors.PeerFloodError as e:
            raise AccountWorkerError("PEER_FLOOD", str(e))
        except (errors.AuthKeyUnregisteredError, errors.SessionRevokedError) as e:
            raise AccountWorkerError("SESSION_ERROR", type(e).__name__)
        except Exception as e:
            name = type(e).__name__
            if "Flood" in name or "AuthKey" in name or "Session" in name:
                raise AccountWorkerError("ACCOUNT_ERROR", name)
            raise RecipientError("POSTBOT_CHECK_FAILED", name)

    async def send_postbot_inline(self, client, peer, bot_username, post_value):
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
                raise RecipientError(
                    "MESSAGE_SEND_FAILED",
                    "Telegram API에서 Message ID를 확인하지 못했습니다."
                )

            return {
                "message_id": str(message_id),
                "post_code": post_code,
                "result_count": len(results),
            }

        except RecipientError:
            raise
        except errors.FloodWaitError as e:
            raise AccountWorkerError("FLOOD_WAIT", f"FloodWait {e.seconds}초")
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

    async def prepare_postbot_source(self, client, bot_username, post_value):
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

            message = await results[0].click("me")
            message_id = getattr(message, "id", None)
            if not message_id:
                raise RecipientError("POSTBOT_SOURCE_FAILED", "포워딩 원본 Message ID를 확인하지 못했습니다.")

            return message, post_code

        except RecipientError:
            raise
        except errors.FloodWaitError as e:
            raise AccountWorkerError("FLOOD_WAIT", f"FloodWait {e.seconds}초")
        except errors.PeerFloodError as e:
            raise AccountWorkerError("PEER_FLOOD", str(e))
        except (errors.AuthKeyUnregisteredError, errors.SessionRevokedError) as e:
            raise AccountWorkerError("SESSION_ERROR", type(e).__name__)
        except Exception as e:
            name = type(e).__name__
            if "Flood" in name or "AuthKey" in name or "Session" in name:
                raise AccountWorkerError("ACCOUNT_ERROR", name)
            raise RecipientError("POSTBOT_SOURCE_FAILED", name)

    async def forward_postbot(self, client, peer, source_message):
        try:
            result = await client.forward_messages(peer, source_message)
            if isinstance(result, (list, tuple)):
                message = result[0] if result else None
            else:
                message = result

            message_id = getattr(message, "id", None)
            if not message_id:
                raise RecipientError("MESSAGE_FORWARD_FAILED", "포워딩 Message ID를 확인하지 못했습니다.")

            return str(message_id)

        except RecipientError:
            raise
        except errors.FloodWaitError as e:
            raise AccountWorkerError("FLOOD_WAIT", f"FloodWait {e.seconds}초")
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
            raise RecipientError("MESSAGE_FORWARD_FAILED", name)

    async def list_dialogs(self, account, limit=100):
        client = await self.connect_account(account)
        try:
            dialogs = await client.get_dialogs(limit=limit)
            result = []
            for dialog in dialogs:
                entity = dialog.entity
                title = getattr(dialog, "name", None) or getattr(entity, "title", None)
                if not title:
                    first = getattr(entity, "first_name", "") or ""
                    last = getattr(entity, "last_name", "") or ""
                    title = (first + " " + last).strip()
                if not title:
                    title = getattr(entity, "username", None) or str(dialog.id)

                result.append({
                    "id": int(dialog.id),
                    "title": title,
                    "username": getattr(entity, "username", None),
                    "unread_count": int(getattr(dialog, "unread_count", 0) or 0),
                    "is_user": bool(getattr(dialog, "is_user", False)),
                    "is_group": bool(getattr(dialog, "is_group", False)),
                    "is_channel": bool(getattr(dialog, "is_channel", False)),
                })
            return result
        finally:
            await client.disconnect()

    async def list_messages(self, account, dialog_id, limit=50):
        client = await self.connect_account(account)
        try:
            dialogs = await client.get_dialogs(limit=None)
            target = None
            for dialog in dialogs:
                if int(dialog.id) == int(dialog_id):
                    target = dialog.entity
                    break

            if target is None:
                raise RuntimeError("선택한 대화방을 찾을 수 없습니다.")

            messages = await client.get_messages(target, limit=limit)
            result = []
            me = await client.get_me()
            my_id = int(getattr(me, "id", 0) or 0)

            for msg in reversed(messages):
                sender_id = int(getattr(msg, "sender_id", 0) or 0)
                sender_name = "나" if sender_id == my_id else ""
                if not sender_name:
                    try:
                        sender = await msg.get_sender()
                        first = getattr(sender, "first_name", "") or ""
                        last = getattr(sender, "last_name", "") or ""
                        sender_name = (first + " " + last).strip()
                        if not sender_name:
                            sender_name = getattr(sender, "title", None) or getattr(sender, "username", None)
                    except Exception:
                        sender_name = None
                if not sender_name:
                    sender_name = str(sender_id) if sender_id else "시스템"

                text = getattr(msg, "message", None) or ""
                media = getattr(msg, "media", None)
                if media and not text:
                    text = "[미디어 메시지]"

                date = getattr(msg, "date", None)
                result.append({
                    "id": int(getattr(msg, "id", 0) or 0),
                    "sender": sender_name,
                    "text": text,
                    "date": date.astimezone().strftime("%Y-%m-%d %H:%M:%S") if date else "",
                    "out": bool(getattr(msg, "out", False)),
                })
            return result
        finally:
            await client.disconnect()
