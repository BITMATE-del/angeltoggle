import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from .telegram_service import TelegramService, RecipientError, AccountWorkerError

class SendEngine:
    def __init__(self, db, logs):
        self.db = db
        self.logs = logs
        self.telegram = TelegramService(db, logs)

    def _accounts(self):
        return self.db.fetchall(
            "SELECT * FROM telegram_accounts WHERE enabled=1 "
            "AND status NOT IN ('SEND_RESTRICTED','PEER_FLOOD','FLOOD_WAIT','SESSION_ERROR','STOPPED') "
            "ORDER BY id"
        )

    def _pending(self):
        return self.db.fetchall(
            "SELECT * FROM recipients WHERE status='PENDING' ORDER BY id"
        )

    def create_campaign(self, name, bot_username, post_code):
        accounts = self._accounts()
        recipients = self._pending()
        max_per = int(self.db.get_setting("max_per_account", "60") or 60)
        capacity = len(accounts) * max_per
        chosen = recipients[:capacity]

        if not accounts:
            raise RuntimeError("사용 가능한 Telegram 계정이 없습니다.")
        if not chosen:
            raise RuntimeError("발송 가능한 고객 DB가 없습니다.")

        campaign_id = self.db.execute(
            "INSERT INTO campaigns(name,postbot_username,post_code,status,total_count,started_at) "
            "VALUES(?,?,?,'RUNNING',?,CURRENT_TIMESTAMP)",
            (name, bot_username, post_code, len(chosen)),
        )

        idx = 0
        with self.db.connection() as conn:
            for account in accounts:
                for _ in range(max_per):
                    if idx >= len(chosen):
                        break
                    recipient = chosen[idx]
                    conn.execute(
                        "INSERT INTO campaign_recipients(campaign_id,recipient_id,assigned_account_id,status) "
                        "VALUES(?,?,?,'ASSIGNED')",
                        (campaign_id, recipient["id"], account["id"]),
                    )
                    conn.execute(
                        "UPDATE recipients SET assigned_account_id=?,status='ASSIGNED',updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=? AND status='PENDING'",
                        (account["id"], recipient["id"]),
                    )
                    idx += 1

        self.logs.write(
            "INFO", "SYSTEM",
            f"캠페인 #{campaign_id} 생성 / 대상 {len(chosen)} / 계정 {len(accounts)} / 계정당 최대 {max_per}",
            campaign_id=campaign_id,
        )
        return campaign_id

    def run_campaign(self, campaign_id):
        campaign = self.db.fetchone("SELECT * FROM campaigns WHERE id=?", (campaign_id,))
        if not campaign:
            raise RuntimeError("캠페인을 찾을 수 없습니다.")

        account_rows = self.db.fetchall(
            "SELECT DISTINCT a.* FROM telegram_accounts a "
            "JOIN campaign_recipients cr ON cr.assigned_account_id=a.id "
            "WHERE cr.campaign_id=? AND cr.status='ASSIGNED' ORDER BY a.id",
            (campaign_id,),
        )

        self.logs.write("INFO", "SYSTEM", f"캠페인 #{campaign_id} 병렬 Worker 시작", campaign_id=campaign_id)

        with ThreadPoolExecutor(max_workers=max(1, len(account_rows)), thread_name_prefix="AngelToggle") as pool:
            futures = [
                pool.submit(self._run_account_thread, campaign_id, account["id"])
                for account in account_rows
            ]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    self.logs.write("ERROR", "SYSTEM", f"Worker 종료 오류: {type(e).__name__}: {e}", campaign_id=campaign_id)

        stats = self.db.fetchone(
            "SELECT "
            "SUM(CASE WHEN status='MESSAGE_SENT' THEN 1 ELSE 0 END) success, "
            "SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END) failed, "
            "SUM(CASE WHEN status IN ('ASSIGNED','SENDING','UNCERTAIN','PAUSED') THEN 1 ELSE 0 END) remaining "
            "FROM campaign_recipients WHERE campaign_id=?",
            (campaign_id,),
        )
        success = int(stats["success"] or 0)
        failed = int(stats["failed"] or 0)
        remaining = int(stats["remaining"] or 0)
        final_status = "COMPLETED" if remaining == 0 else "PARTIAL"

        self.db.execute(
            "UPDATE campaigns SET status=?,success_count=?,failed_count=?,finished_at=CURRENT_TIMESTAMP WHERE id=?",
            (final_status, success, failed, campaign_id),
        )
        self.logs.write(
            "SUCCESS" if final_status == "COMPLETED" else "WARNING",
            "SYSTEM",
            f"캠페인 #{campaign_id} 종료 / 성공 {success} / 실패 {failed} / 보류 {remaining}",
            campaign_id=campaign_id,
        )
        return {"success": success, "failed": failed, "remaining": remaining}

    def _run_account_thread(self, campaign_id, account_id):
        asyncio.run(self._run_account(campaign_id, account_id))

    async def _run_account(self, campaign_id, account_id):
        account = self.db.fetchone("SELECT * FROM telegram_accounts WHERE id=?", (account_id,))
        campaign = self.db.fetchone("SELECT * FROM campaigns WHERE id=?", (campaign_id,))
        if not account or not campaign:
            return

        self.logs.write("INFO", "ACCOUNT", "Worker 시작", account_id=account_id, campaign_id=campaign_id)

        try:
            client = await self.telegram.connect_account(account)
        except AccountWorkerError as e:
            self._stop_account(campaign_id, account_id, e.code, str(e))
            return
        except Exception as e:
            self._stop_account(campaign_id, account_id, "SESSION_ERROR", type(e).__name__)
            return

        try:
            targets = self.db.fetchall(
                "SELECT cr.*,r.normalized_phone,r.id recipient_id "
                "FROM campaign_recipients cr JOIN recipients r ON r.id=cr.recipient_id "
                "WHERE cr.campaign_id=? AND cr.assigned_account_id=? AND cr.status='ASSIGNED' "
                "ORDER BY r.id",
                (campaign_id, account_id),
            )

            for target in targets:
                claimed = self.db.execute_rowcount(
                    "UPDATE campaign_recipients SET status='SENDING',updated_at=CURRENT_TIMESTAMP "
                    "WHERE campaign_id=? AND recipient_id=? AND assigned_account_id=? AND status='ASSIGNED'",
                    (campaign_id, target["recipient_id"], account_id),
                )
                if claimed != 1:
                    continue

                self.db.execute(
                    "UPDATE recipients SET status='SENDING',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (target["recipient_id"],),
                )

                try:
                    user = await self.telegram.import_contact(client, target["normalized_phone"])
                    self.db.execute(
                        "UPDATE recipients SET telegram_uid=?,telegram_username=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (str(getattr(user, "id", "")), getattr(user, "username", None), target["recipient_id"]),
                    )

                    message_id = await self.telegram.send_postbot(
                        client, user, campaign["postbot_username"], campaign["post_code"]
                    )

                    with self.db.connection() as conn:
                        conn.execute(
                            "UPDATE campaign_recipients SET status='MESSAGE_SENT',telegram_message_id=?,"
                            "updated_at=CURRENT_TIMESTAMP WHERE campaign_id=? AND recipient_id=?",
                            (message_id, campaign_id, target["recipient_id"]),
                        )
                        conn.execute(
                            "UPDATE recipients SET status='MESSAGE_SENT',telegram_message_id=?,processed_at=CURRENT_TIMESTAMP,"
                            "sent_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                            (message_id, target["recipient_id"]),
                        )
                    self.logs.write(
                        "SUCCESS", "SEND", f"발송 성공 message_id={message_id}",
                        account_id=account_id, campaign_id=campaign_id, recipient_id=target["recipient_id"],
                    )

                except RecipientError as e:
                    with self.db.connection() as conn:
                        conn.execute(
                            "UPDATE campaign_recipients SET status='FAILED',error_code=?,error_message=?,"
                            "updated_at=CURRENT_TIMESTAMP WHERE campaign_id=? AND recipient_id=?",
                            (e.code, str(e), campaign_id, target["recipient_id"]),
                        )
                        conn.execute(
                            "UPDATE recipients SET status='FAILED',error_code=?,error_message=?,processed_at=CURRENT_TIMESTAMP,"
                            "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                            (e.code, str(e), target["recipient_id"]),
                        )
                    self.logs.write(
                        "ERROR", "SEND", f"{e.code}: {e}",
                        account_id=account_id, campaign_id=campaign_id, recipient_id=target["recipient_id"],
                    )
                    continue

                except AccountWorkerError as e:
                    self.db.execute(
                        "UPDATE campaign_recipients SET status='UNCERTAIN',error_code=?,error_message=?,"
                        "updated_at=CURRENT_TIMESTAMP WHERE campaign_id=? AND recipient_id=? AND status='SENDING'",
                        (e.code, str(e), campaign_id, target["recipient_id"]),
                    )
                    self.db.execute(
                        "UPDATE recipients SET status='UNCERTAIN',error_code=?,error_message=?,"
                        "updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='SENDING'",
                        (e.code, str(e), target["recipient_id"]),
                    )
                    self._stop_account(campaign_id, account_id, e.code, str(e))
                    break

                except Exception as e:
                    code = "UNKNOWN_ERROR"
                    self.db.execute(
                        "UPDATE campaign_recipients SET status='UNCERTAIN',error_code=?,error_message=?,"
                        "updated_at=CURRENT_TIMESTAMP WHERE campaign_id=? AND recipient_id=? AND status='SENDING'",
                        (code, type(e).__name__, campaign_id, target["recipient_id"]),
                    )
                    self.db.execute(
                        "UPDATE recipients SET status='UNCERTAIN',error_code=?,error_message=?,updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=? AND status='SENDING'",
                        (code, type(e).__name__, target["recipient_id"]),
                    )
                    self._stop_account(campaign_id, account_id, code, type(e).__name__)
                    break
        finally:
            await client.disconnect()
            current = self.db.fetchone("SELECT status FROM telegram_accounts WHERE id=?", (account_id,))
            if current and current["status"] == "NORMAL":
                self.logs.write("INFO", "ACCOUNT", "Worker 정상 종료", account_id=account_id, campaign_id=campaign_id)

    def _stop_account(self, campaign_id, account_id, code, message):
        self.db.execute(
            "UPDATE telegram_accounts SET status=?,last_error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (code, message, account_id),
        )
        self.db.execute(
            "UPDATE campaign_recipients SET status='PAUSED',error_code=?,error_message=?,updated_at=CURRENT_TIMESTAMP "
            "WHERE campaign_id=? AND assigned_account_id=? AND status='ASSIGNED'",
            (code, message, campaign_id, account_id),
        )
        self.logs.write(
            "ERROR", "ACCOUNT", f"{code}: {message} / 해당 계정 Worker만 중지",
            account_id=account_id, campaign_id=campaign_id,
        )

    def clear_local_account_stop(self, account_id):
        self.db.execute(
            "UPDATE telegram_accounts SET status='IMPORTED',last_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (account_id,),
        )
        self.logs.write("INFO", "ACCOUNT", "로컬 정지 상태 해제 / 다음 실행 시 세션 재검사", account_id=account_id)
