import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
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
        return self.db.fetchall("SELECT * FROM recipients WHERE status='PENDING' ORDER BY id")

    def create_campaign(self, name, bot_username, post_code):
        accounts = self._accounts()
        recipients = self._pending()
        max_per = int(self.db.get_setting("max_contacts_per_account", "40") or 40)
        capacity = len(accounts) * max_per
        chosen = recipients[:capacity]

        if not accounts:
            raise RuntimeError("사용 가능한 텔레그램 계정이 없습니다.")
        if not chosen:
            raise RuntimeError("사용 가능한 고객 DB가 없습니다.")

        campaign_id = self.db.execute(
            "INSERT INTO campaigns(name,postbot_username,post_code,status,total_count) "
            "VALUES(?,?,?,'CONTACT_WAITING',?)",
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
                        "INSERT INTO campaign_recipients("
                        "campaign_id,recipient_id,assigned_account_id,status,contact_status"
                        ") VALUES(?,?,?,'ASSIGNED','WAITING')",
                        (campaign_id, recipient["id"], account["id"]),
                    )
                    conn.execute(
                        "UPDATE recipients SET assigned_account_id=?,status='ASSIGNED',"
                        "contact_status='NOT_ADDED',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (account["id"], recipient["id"]),
                    )
                    idx += 1

        self.logs.write(
            "INFO", "시스템",
            f"작업 #{campaign_id} 생성 / 대상 {len(chosen)}명 / 계정 {len(accounts)}개 / 계정당 최대 {max_per}명",
            campaign_id=campaign_id,
        )
        return campaign_id

    def run_contact_stage(self, campaign_id):
        account_rows = self.db.fetchall(
            "SELECT DISTINCT a.* FROM telegram_accounts a "
            "JOIN campaign_recipients cr ON cr.assigned_account_id=a.id "
            "WHERE cr.campaign_id=? AND cr.contact_status IN ('WAITING','CONTACT_PAUSED') "
            "AND a.enabled=1 "
            "AND a.status NOT IN ('SEND_RESTRICTED','PEER_FLOOD','FLOOD_WAIT','SESSION_ERROR','STOPPED','WORKER_ERROR') "
            "ORDER BY a.id",
            (campaign_id,),
        )
        self.db.execute(
            "UPDATE campaigns SET status='CONTACT_RUNNING',contact_started_at=CURRENT_TIMESTAMP WHERE id=?",
            (campaign_id,),
        )
        self.logs.write(
            "INFO", "연락처",
            f"연락처 추가 시작 / 계정 {len(account_rows)}개 병렬 처리 / 10명씩 묶음 처리",
            campaign_id=campaign_id,
        )

        if account_rows:
            with ThreadPoolExecutor(
                max_workers=len(account_rows),
                thread_name_prefix="AngelToggleContact"
            ) as pool:
                futures = [
                    pool.submit(self._contact_thread, campaign_id, account["id"])
                    for account in account_rows
                ]
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        self.logs.write(
                            "ERROR", "연락처",
                            f"연락처 작업 오류: {type(e).__name__}: {e}",
                            campaign_id=campaign_id,
                        )

        stats = self.db.fetchone(
            "SELECT "
            "SUM(CASE WHEN contact_status='ADDED' THEN 1 ELSE 0 END) ready, "
            "SUM(CASE WHEN contact_status='FAILED' THEN 1 ELSE 0 END) failed, "
            "SUM(CASE WHEN contact_status IN ('WAITING','ADDING','CONTACT_PAUSED','CONTACT_UNCERTAIN') THEN 1 ELSE 0 END) paused "
            "FROM campaign_recipients WHERE campaign_id=?",
            (campaign_id,),
        )
        ready = int(stats["ready"] or 0)
        failed = int(stats["failed"] or 0)
        paused = int(stats["paused"] or 0)

        self.db.execute(
            "UPDATE campaigns SET status='CONTACT_DONE',contact_ready_count=?,contact_finished_at=CURRENT_TIMESTAMP WHERE id=?",
            (ready, campaign_id),
        )
        self.logs.write(
            "SUCCESS" if ready else "WARNING", "연락처",
            f"연락처 추가 완료 / 추가 {ready}명 / 실패 {failed}명 / 미처리 {paused}명. "
            f"추가 완료된 {ready}명은 바로 게시물 발송 가능합니다.",
            campaign_id=campaign_id,
        )
        return {"ready": ready, "failed": failed, "paused": paused}

    def _contact_thread(self, campaign_id, account_id):
        try:
            asyncio.run(self._run_contact_account(campaign_id, account_id))
        except Exception as e:
            self._pause_contact_account(
                campaign_id,
                account_id,
                "WORKER_ERROR",
                f"{type(e).__name__}: {e}",
            )
            self.logs.write(
                "ERROR",
                "연락처",
                f"계정 Worker 예외 격리 / 다른 계정은 계속 실행: {type(e).__name__}: {e}",
                account_id=account_id,
                campaign_id=campaign_id,
            )

    async def _run_contact_account(self, campaign_id, account_id):
        account = self.db.fetchone("SELECT * FROM telegram_accounts WHERE id=?", (account_id,))
        if not account:
            return

        try:
            client = await self.telegram.connect_account(account)
        except AccountWorkerError as e:
            self._pause_contact_account(campaign_id, account_id, e.code, str(e))
            return
        except Exception as e:
            self._pause_contact_account(campaign_id, account_id, "SESSION_ERROR", type(e).__name__)
            return

        try:
            targets = self.db.fetchall(
                "SELECT cr.recipient_id,r.normalized_phone "
                "FROM campaign_recipients cr JOIN recipients r ON r.id=cr.recipient_id "
                "WHERE cr.campaign_id=? AND cr.assigned_account_id=? "
                "AND cr.contact_status IN ('WAITING','CONTACT_PAUSED') ORDER BY r.id",
                (campaign_id, account_id),
            )

            for start in range(0, len(targets), 10):
                batch = targets[start:start + 10]
                ids = [row["recipient_id"] for row in batch]
                if not ids:
                    continue
                marks = ",".join("?" for _ in ids)

                self.db.execute(
                    f"UPDATE campaign_recipients SET contact_status='ADDING',updated_at=CURRENT_TIMESTAMP "
                    f"WHERE campaign_id=? AND recipient_id IN ({marks})",
                    (campaign_id, *ids),
                )

                try:
                    success, missing = await self.telegram.import_contacts_batch(
                        client,
                        [(row["recipient_id"], row["normalized_phone"]) for row in batch]
                    )

                    with self.db.connection() as conn:
                        for recipient_id, user in success.items():
                            conn.execute(
                                "UPDATE campaign_recipients SET contact_status='ADDED',updated_at=CURRENT_TIMESTAMP "
                                "WHERE campaign_id=? AND recipient_id=?",
                                (campaign_id, recipient_id),
                            )
                            conn.execute(
                                "UPDATE recipients SET telegram_uid=?,telegram_username=?,"
                                "contact_status='ADDED',contact_added_at=CURRENT_TIMESTAMP,"
                                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                                (
                                    str(getattr(user, "id", "")),
                                    getattr(user, "username", None),
                                    recipient_id,
                                ),
                            )

                        for recipient_id in missing:
                            conn.execute(
                                "UPDATE campaign_recipients SET contact_status='FAILED',status='FAILED',"
                                "error_code='USER_NOT_FOUND',error_message='텔레그램 사용자 확인 실패',"
                                "updated_at=CURRENT_TIMESTAMP WHERE campaign_id=? AND recipient_id=?",
                                (campaign_id, recipient_id),
                            )
                            conn.execute(
                                "UPDATE recipients SET contact_status='FAILED',status='FAILED',"
                                "error_code='USER_NOT_FOUND',error_message='텔레그램 사용자 확인 실패',"
                                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                                (recipient_id,),
                            )

                    self.logs.write(
                        "INFO", "연락처",
                        f"10명 묶음 처리 / 요청 {len(batch)}명 / 추가 {len(success)}명 / 실패 {len(missing)}명",
                        account_id=account_id, campaign_id=campaign_id,
                    )

                except AccountWorkerError as e:
                    self.db.execute(
                        f"UPDATE campaign_recipients SET contact_status='CONTACT_PAUSED',"
                        f"error_code=?,error_message=?,updated_at=CURRENT_TIMESTAMP "
                        f"WHERE campaign_id=? AND recipient_id IN ({marks}) AND contact_status='ADDING'",
                        (e.code, str(e), campaign_id, *ids),
                    )
                    self._pause_contact_account(campaign_id, account_id, e.code, str(e))
                    break

                except RecipientError as e:
                    self.db.execute(
                        f"UPDATE campaign_recipients SET contact_status='FAILED',status='FAILED',"
                        f"error_code=?,error_message=?,updated_at=CURRENT_TIMESTAMP "
                        f"WHERE campaign_id=? AND recipient_id IN ({marks})",
                        (e.code, str(e), campaign_id, *ids),
                    )
                    self.logs.write(
                        "ERROR", "연락처",
                        f"묶음 연락처 추가 실패: {e.code} / {e}",
                        account_id=account_id, campaign_id=campaign_id,
                    )
                    continue
        finally:
            await client.disconnect()

    def _pause_contact_account(self, campaign_id, account_id, code, message):
        self.db.execute(
            "UPDATE telegram_accounts SET status=?,last_error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (code, message, account_id),
        )
        self.db.execute(
            "UPDATE campaign_recipients SET contact_status='CONTACT_PAUSED',error_code=?,error_message=?,"
            "updated_at=CURRENT_TIMESTAMP WHERE campaign_id=? AND assigned_account_id=? "
            "AND contact_status IN ('WAITING','ADDING')",
            (code, message, campaign_id, account_id),
        )
        self.logs.write(
            "ERROR", "계정",
            f"{code}: {message} / 해당 계정의 연락처 추가만 중단",
            account_id=account_id, campaign_id=campaign_id,
        )

    def run_send_stage(self, campaign_id):
        campaign = self.db.fetchone("SELECT * FROM campaigns WHERE id=?", (campaign_id,))
        if not campaign:
            raise RuntimeError("발송 작업을 찾을 수 없습니다.")

        account_rows = self.db.fetchall(
            "SELECT DISTINCT a.* FROM telegram_accounts a "
            "JOIN campaign_recipients cr ON cr.assigned_account_id=a.id "
            "WHERE cr.campaign_id=? AND cr.contact_status='ADDED' "
            "AND cr.status IN ('ASSIGNED','SEND_PAUSED') "
            "AND a.enabled=1 "
            "AND a.status NOT IN ('SEND_RESTRICTED','PEER_FLOOD','FLOOD_WAIT','SESSION_ERROR','STOPPED','WORKER_ERROR') "
            "ORDER BY a.id",
            (campaign_id,),
        )
        ready = self.db.fetchone(
            "SELECT COUNT(*) c FROM campaign_recipients "
            "WHERE campaign_id=? AND contact_status='ADDED' "
            "AND status IN ('ASSIGNED','SEND_PAUSED')",
            (campaign_id,),
        )["c"]
        if not ready:
            raise RuntimeError("게시물을 발송할 연락처가 없습니다.")

        self.db.execute(
            "UPDATE campaigns SET status='SEND_RUNNING',started_at=CURRENT_TIMESTAMP WHERE id=?",
            (campaign_id,),
        )
        self.logs.write(
            "INFO", "발송",
            f"게시물 발송 시작 / 연락처 추가 완료 {ready}명 대상",
            campaign_id=campaign_id,
        )

        with ThreadPoolExecutor(
            max_workers=max(1, len(account_rows)),
            thread_name_prefix="AngelToggleSend"
        ) as pool:
            futures = [
                pool.submit(self._send_thread, campaign_id, account["id"])
                for account in account_rows
            ]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    self.logs.write(
                        "ERROR", "발송",
                        f"발송 Worker 오류: {type(e).__name__}: {e}",
                        campaign_id=campaign_id,
                    )

        stats = self.db.fetchone(
            "SELECT "
            "SUM(CASE WHEN status='MESSAGE_SENT' THEN 1 ELSE 0 END) success, "
            "SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END) failed, "
            "SUM(CASE WHEN status IN ('ASSIGNED','SENDING','SEND_PAUSED','UNCERTAIN') AND contact_status='ADDED' THEN 1 ELSE 0 END) remaining "
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
            "SUCCESS" if success else "WARNING", "발송",
            f"게시물 발송 종료 / 성공 {success}명 / 실패 {failed}명 / 보류 {remaining}명",
            campaign_id=campaign_id,
        )
        return {"success": success, "failed": failed, "remaining": remaining}

    def _send_thread(self, campaign_id, account_id):
        try:
            asyncio.run(self._run_send_account(campaign_id, account_id))
        except Exception as e:
            self._pause_send_account(
                campaign_id,
                account_id,
                "WORKER_ERROR",
                f"{type(e).__name__}: {e}",
            )
            self.logs.write(
                "ERROR",
                "발송",
                f"계정 Worker 예외 격리 / 다른 계정은 계속 실행: {type(e).__name__}: {e}",
                account_id=account_id,
                campaign_id=campaign_id,
            )

    async def _run_send_account(self, campaign_id, account_id):
        account = self.db.fetchone("SELECT * FROM telegram_accounts WHERE id=?", (account_id,))
        campaign = self.db.fetchone("SELECT * FROM campaigns WHERE id=?", (campaign_id,))
        if not account or not campaign:
            return

        try:
            client = await self.telegram.connect_account(account)
        except AccountWorkerError as e:
            self._pause_send_account(campaign_id, account_id, e.code, str(e))
            return
        except Exception as e:
            self._pause_send_account(campaign_id, account_id, "SESSION_ERROR", type(e).__name__)
            return

        try:
            targets = self.db.fetchall(
                "SELECT cr.recipient_id,r.telegram_uid "
                "FROM campaign_recipients cr JOIN recipients r ON r.id=cr.recipient_id "
                "WHERE cr.campaign_id=? AND cr.assigned_account_id=? "
                "AND cr.contact_status='ADDED' AND cr.status IN ('ASSIGNED','SEND_PAUSED') "
                "ORDER BY r.id",
                (campaign_id, account_id),
            )

            for target in targets:
                claimed = self.db.execute_rowcount(
                    "UPDATE campaign_recipients SET status='SENDING',updated_at=CURRENT_TIMESTAMP "
                    "WHERE campaign_id=? AND recipient_id=? AND assigned_account_id=? "
                    "AND contact_status='ADDED' AND status IN ('ASSIGNED','SEND_PAUSED')",
                    (campaign_id, target["recipient_id"], account_id),
                )
                if claimed != 1:
                    continue

                try:
                    peer = int(target["telegram_uid"])
                    message_id = await self.telegram.send_postbot(
                        client, peer, campaign["postbot_username"], campaign["post_code"]
                    )
                    with self.db.connection() as conn:
                        conn.execute(
                            "UPDATE campaign_recipients SET status='MESSAGE_SENT',telegram_message_id=?,"
                            "updated_at=CURRENT_TIMESTAMP WHERE campaign_id=? AND recipient_id=?",
                            (message_id, campaign_id, target["recipient_id"]),
                        )
                        conn.execute(
                            "UPDATE recipients SET status='MESSAGE_SENT',telegram_message_id=?,"
                            "processed_at=CURRENT_TIMESTAMP,sent_at=CURRENT_TIMESTAMP,"
                            "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                            (message_id, target["recipient_id"]),
                        )
                    self.logs.write(
                        "SUCCESS", "발송",
                        f"게시물 발송 성공 / 메시지ID={message_id}",
                        account_id=account_id, campaign_id=campaign_id,
                        recipient_id=target["recipient_id"],
                    )

                except RecipientError as e:
                    with self.db.connection() as conn:
                        conn.execute(
                            "UPDATE campaign_recipients SET status='FAILED',error_code=?,error_message=?,"
                            "updated_at=CURRENT_TIMESTAMP WHERE campaign_id=? AND recipient_id=?",
                            (e.code, str(e), campaign_id, target["recipient_id"]),
                        )
                        conn.execute(
                            "UPDATE recipients SET status='FAILED',error_code=?,error_message=?,"
                            "processed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                            (e.code, str(e), target["recipient_id"]),
                        )
                    self.logs.write(
                        "ERROR", "발송", f"{e.code}: {e}",
                        account_id=account_id, campaign_id=campaign_id,
                        recipient_id=target["recipient_id"],
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
                        "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (e.code, str(e), target["recipient_id"]),
                    )
                    self._pause_send_account(campaign_id, account_id, e.code, str(e))
                    break
        finally:
            await client.disconnect()

    def _pause_send_account(self, campaign_id, account_id, code, message):
        self.db.execute(
            "UPDATE telegram_accounts SET status=?,last_error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (code, message, account_id),
        )
        self.db.execute(
            "UPDATE campaign_recipients SET status='SEND_PAUSED',error_code=?,error_message=?,"
            "updated_at=CURRENT_TIMESTAMP WHERE campaign_id=? AND assigned_account_id=? "
            "AND contact_status='ADDED' AND status='ASSIGNED'",
            (code, message, campaign_id, account_id),
        )
        self.logs.write(
            "ERROR", "계정",
            f"{code}: {message} / 해당 계정의 게시물 발송만 중단",
            account_id=account_id, campaign_id=campaign_id,
        )

    def latest_campaign(self):
        return self.db.fetchone("SELECT * FROM campaigns ORDER BY id DESC LIMIT 1")

    def clear_local_account_stop(self, account_id):
        self.db.execute(
            "UPDATE telegram_accounts SET status='IMPORTED',last_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (account_id,),
        )
        self.logs.write(
            "INFO", "계정",
            "프로그램 내부 정지 상태를 해제했습니다. 다음 작업에서 실제 계정 상태를 다시 확인합니다.",
            account_id=account_id,
        )
