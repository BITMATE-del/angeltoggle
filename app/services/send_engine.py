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

        if not accounts:
            raise RuntimeError("사용 가능한 텔레그램 계정이 없습니다.")
        if not recipients:
            raise RuntimeError("사용 가능한 고객 DB가 없습니다.")

        account_map = {int(a["id"]): a for a in accounts}
        capacities = {int(a["id"]): max_per for a in accounts}
        assignments = []

        # 이미 연락처 추가가 성공했던 실패 DB는 기존 계정을 유지한다.
        for recipient in recipients:
            if recipient["contact_status"] != "ADDED":
                continue
            aid = int(recipient["assigned_account_id"] or 0)
            if aid in account_map and capacities.get(aid, 0) > 0:
                assignments.append((recipient, aid, "ADDED"))
                capacities[aid] -= 1

        assigned_ids = {int(item[0]["id"]) for item in assignments}

        # 연락처가 아직 없는 DB만 남은 계정 용량에 배정한다.
        for recipient in recipients:
            if int(recipient["id"]) in assigned_ids:
                continue
            target_aid = None
            for account in accounts:
                aid = int(account["id"])
                if capacities.get(aid, 0) > 0:
                    target_aid = aid
                    break
            if target_aid is None:
                break
            assignments.append((recipient, target_aid, "WAITING"))
            capacities[target_aid] -= 1

        if not assignments:
            raise RuntimeError("현재 계정 용량으로 처리할 고객 DB가 없습니다.")

        campaign_id = self.db.execute(
            "INSERT INTO campaigns(name,postbot_username,post_code,status,total_count) "
            "VALUES(?,?,?,'CONTACT_WAITING',?)",
            (name, bot_username, post_code, len(assignments)),
        )

        with self.db.connection() as conn:
            for recipient, account_id, contact_state in assignments:
                conn.execute(
                    "INSERT INTO campaign_recipients("
                    "campaign_id,recipient_id,assigned_account_id,status,contact_status,contact_added_at"
                    ") VALUES(?,?,?,'ASSIGNED',?,?)",
                    (
                        campaign_id,
                        recipient["id"],
                        account_id,
                        contact_state,
                        recipient["contact_added_at"] if contact_state == "ADDED" else None,
                    ),
                )
                conn.execute(
                    "UPDATE recipients SET assigned_account_id=?,status='ASSIGNED',"
                    "contact_status=?,error_code=NULL,error_message=NULL,"
                    "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (
                        account_id,
                        contact_state if contact_state == "ADDED" else "NOT_ADDED",
                        recipient["id"],
                    ),
                )

        self.logs.write(
            "INFO",
            "시스템",
            f"작업 #{campaign_id} 생성 / 대상 {len(assignments)}명 / 계정 {len(accounts)}개 / 계정당 최대 {max_per}명",
            campaign_id=campaign_id,
        )
        return campaign_id

    def reset_failed_recipients(self):
        rows = self.db.fetchall(
            "SELECT id,contact_status,assigned_account_id FROM recipients "
            "WHERE status='FAILED' ORDER BY id"
        )
        if not rows:
            return 0

        count = 0
        with self.db.connection() as conn:
            for row in rows:
                keep_contact = row["contact_status"] == "ADDED" and row["assigned_account_id"] is not None

                if keep_contact:
                    conn.execute(
                        "UPDATE recipients SET status='PENDING',"
                        "error_code=NULL,error_message=NULL,telegram_message_id=NULL,"
                        "processed_at=NULL,sent_at=NULL,updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=?",
                        (row["id"],),
                    )
                else:
                    conn.execute(
                        "UPDATE recipients SET status='PENDING',contact_status='NOT_ADDED',"
                        "assigned_account_id=NULL,telegram_uid=NULL,telegram_username=NULL,"
                        "contact_name=NULL,contact_added_at=NULL,error_code=NULL,error_message=NULL,"
                        "telegram_message_id=NULL,processed_at=NULL,sent_at=NULL,"
                        "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (row["id"],),
                    )
                count += 1

        self.logs.write(
            "INFO",
            "DB",
            f"실패 DB {count}개를 대기상태로 복구했습니다.",
        )
        return count

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
                        for recipient_id, payload in success.items():
                            user = payload["user"]
                            contact_name = payload.get("contact_name") or ""

                            conn.execute(
                                "UPDATE campaign_recipients SET contact_status='ADDED',"
                                "contact_added_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP "
                                "WHERE campaign_id=? AND recipient_id=?",
                                (campaign_id, recipient_id),
                            )
                            conn.execute(
                                "UPDATE recipients SET telegram_uid=?,telegram_username=?,contact_name=?,"
                                "contact_status='ADDED',contact_added_at=CURRENT_TIMESTAMP,"
                                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                                (
                                    str(getattr(user, "id", "")),
                                    getattr(user, "username", None),
                                    contact_name,
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
            # 메시지 전송 단계에서는 전화번호를 다시 ImportContacts 하지 않는다.
            # 실제 현재 연락처에 남아 있는 Telegram UID만 발송 대상으로 사용한다.
            contact_ids = await self.telegram.current_contact_ids(client)

            targets = self.db.fetchall(
                "SELECT cr.recipient_id,r.telegram_uid,r.contact_name "
                "FROM campaign_recipients cr JOIN recipients r ON r.id=cr.recipient_id "
                "WHERE cr.campaign_id=? AND cr.assigned_account_id=? "
                "AND cr.contact_status='ADDED' AND cr.status IN ('ASSIGNED','SEND_PAUSED') "
                "ORDER BY r.id",
                (campaign_id, account_id),
            )

            sendable = []
            for target in targets:
                try:
                    uid = int(target["telegram_uid"] or 0)
                except Exception:
                    uid = 0

                if not uid or uid not in contact_ids:
                    with self.db.connection() as conn:
                        conn.execute(
                            "UPDATE campaign_recipients SET status='FAILED',"
                            "error_code='CONTACT_NOT_PRESENT',"
                            "error_message='현재 텔레그램 연락처에 없는 대상',"
                            "updated_at=CURRENT_TIMESTAMP "
                            "WHERE campaign_id=? AND recipient_id=?",
                            (campaign_id, target["recipient_id"]),
                        )
                        conn.execute(
                            "UPDATE recipients SET status='FAILED',"
                            "error_code='CONTACT_NOT_PRESENT',"
                            "error_message='현재 텔레그램 연락처에 없는 대상',"
                            "processed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP "
                            "WHERE id=?",
                            (target["recipient_id"],),
                        )

                    self.logs.write(
                        "WARNING",
                        "발송",
                        "현재 연락처에 없는 대상이라 발송 제외",
                        account_id=account_id,
                        campaign_id=campaign_id,
                        recipient_id=target["recipient_id"],
                    )
                    continue

                sendable.append(target)

            if not sendable:
                self.logs.write(
                    "WARNING",
                    "발송",
                    "현재 연락처에 남아 있는 발송 대상이 없습니다.",
                    account_id=account_id,
                    campaign_id=campaign_id,
                )
                return

            # 계정별로 PostBot 게시물을 내 Saved Messages에 한 번 생성하고,
            # 이후 해당 원본을 실제 연락처에게 포워딩한다.
            try:
                source_message, postbot_code = await self.telegram.prepare_postbot_source(
                    client,
                    campaign["postbot_username"],
                    campaign["post_code"],
                )
            except RecipientError as e:
                for target in sendable:
                    self.db.execute(
                        "UPDATE campaign_recipients SET status='FAILED',error_code=?,error_message=?,"
                        "postbot_code=?,updated_at=CURRENT_TIMESTAMP "
                        "WHERE campaign_id=? AND recipient_id=?",
                        (
                            e.code,
                            str(e),
                            campaign["post_code"],
                            campaign_id,
                            target["recipient_id"],
                        ),
                    )
                self.logs.write(
                    "ERROR",
                    "발송",
                    f"PostBot 원본 준비 실패: {e.code} / {e}",
                    account_id=account_id,
                    campaign_id=campaign_id,
                )
                return

            for target in sendable:
                claimed = self.db.execute_rowcount(
                    "UPDATE campaign_recipients SET status='SENDING',postbot_code=?,"
                    "updated_at=CURRENT_TIMESTAMP "
                    "WHERE campaign_id=? AND recipient_id=? AND assigned_account_id=? "
                    "AND contact_status='ADDED' AND status IN ('ASSIGNED','SEND_PAUSED')",
                    (
                        postbot_code,
                        campaign_id,
                        target["recipient_id"],
                        account_id,
                    ),
                )
                if claimed != 1:
                    continue

                try:
                    peer = int(target["telegram_uid"])
                    message_id = await self.telegram.forward_postbot(
                        client,
                        peer,
                        source_message,
                    )

                    with self.db.connection() as conn:
                        conn.execute(
                            "UPDATE campaign_recipients SET status='MESSAGE_SENT',"
                            "telegram_message_id=?,sent_at=CURRENT_TIMESTAMP,"
                            "postbot_code=?,updated_at=CURRENT_TIMESTAMP "
                            "WHERE campaign_id=? AND recipient_id=?",
                            (
                                message_id,
                                postbot_code,
                                campaign_id,
                                target["recipient_id"],
                            ),
                        )
                        conn.execute(
                            "UPDATE recipients SET status='MESSAGE_SENT',telegram_message_id=?,"
                            "processed_at=CURRENT_TIMESTAMP,sent_at=CURRENT_TIMESTAMP,"
                            "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                            (message_id, target["recipient_id"]),
                        )

                    self.logs.write(
                        "SUCCESS",
                        "발송",
                        f"PostBot 게시물 포워딩 성공 / 메시지ID={message_id} / 코드={postbot_code}",
                        account_id=account_id,
                        campaign_id=campaign_id,
                        recipient_id=target["recipient_id"],
                    )

                except RecipientError as e:
                    with self.db.connection() as conn:
                        conn.execute(
                            "UPDATE campaign_recipients SET status='FAILED',error_code=?,error_message=?,"
                            "postbot_code=?,updated_at=CURRENT_TIMESTAMP "
                            "WHERE campaign_id=? AND recipient_id=?",
                            (
                                e.code,
                                str(e),
                                postbot_code,
                                campaign_id,
                                target["recipient_id"],
                            ),
                        )
                        conn.execute(
                            "UPDATE recipients SET status='FAILED',error_code=?,error_message=?,"
                            "processed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                            (e.code, str(e), target["recipient_id"]),
                        )

                    self.logs.write(
                        "ERROR",
                        "발송",
                        f"{e.code}: {e}",
                        account_id=account_id,
                        campaign_id=campaign_id,
                        recipient_id=target["recipient_id"],
                    )
                    continue

                except AccountWorkerError as e:
                    self.db.execute(
                        "UPDATE campaign_recipients SET status='UNCERTAIN',error_code=?,error_message=?,"
                        "postbot_code=?,updated_at=CURRENT_TIMESTAMP "
                        "WHERE campaign_id=? AND recipient_id=? AND status='SENDING'",
                        (
                            e.code,
                            str(e),
                            postbot_code,
                            campaign_id,
                            target["recipient_id"],
                        ),
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

    def cancel_active_assignments(self, account_ids=None, campaign_id=None):
        account_ids = [int(x) for x in (account_ids or [])]
        where = [
            "cr.status != 'MESSAGE_SENT'",
            "r.status != 'MESSAGE_SENT'",
        ]
        params = []

        if account_ids:
            marks = ",".join("?" for _ in account_ids)
            where.append(f"cr.assigned_account_id IN ({marks})")
            params.extend(account_ids)

        if campaign_id is not None:
            where.append("cr.campaign_id=?")
            params.append(int(campaign_id))

        sql_where = " AND ".join(where)

        rows = self.db.fetchall(
            "SELECT cr.campaign_id,cr.recipient_id,cr.assigned_account_id "
            "FROM campaign_recipients cr "
            "JOIN recipients r ON r.id=cr.recipient_id "
            f"WHERE {sql_where}",
            tuple(params),
        )

        if not rows:
            return 0

        recipient_ids = [int(row["recipient_id"]) for row in rows]
        campaign_ids = sorted({int(row["campaign_id"]) for row in rows})

        with self.db.connection() as conn:
            for row in rows:
                conn.execute(
                    "DELETE FROM campaign_recipients "
                    "WHERE campaign_id=? AND recipient_id=? AND status!='MESSAGE_SENT'",
                    (row["campaign_id"], row["recipient_id"]),
                )
                conn.execute(
                    "UPDATE recipients SET "
                    "assigned_account_id=NULL,status='PENDING',contact_status='NOT_ADDED',"
                    "telegram_uid=NULL,telegram_username=NULL,contact_name=NULL,"
                    "contact_added_at=NULL,error_code=NULL,error_message=NULL,"
                    "telegram_message_id=NULL,processed_at=NULL,sent_at=NULL,"
                    "updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=? AND status!='MESSAGE_SENT'",
                    (row["recipient_id"],),
                )

            for cid in campaign_ids:
                remaining = conn.execute(
                    "SELECT COUNT(*) c FROM campaign_recipients WHERE campaign_id=?",
                    (cid,),
                ).fetchone()["c"]
                sent = conn.execute(
                    "SELECT COUNT(*) c FROM campaign_recipients "
                    "WHERE campaign_id=? AND status='MESSAGE_SENT'",
                    (cid,),
                ).fetchone()["c"]

                if remaining == 0:
                    new_status = "CANCELLED"
                elif sent == remaining:
                    new_status = "COMPLETED"
                else:
                    new_status = "PARTIAL"

                conn.execute(
                    "UPDATE campaigns SET total_count=?,status=? "
                    "WHERE id=?",
                    (remaining, new_status, cid),
                )

        self.logs.write(
            "INFO",
            "배정",
            f"진행 중 배정 {len(rows)}건 취소 / 고객 DB를 대기상태로 복구",
        )
        return len(rows)

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
