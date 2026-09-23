import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from .telegram_service import TelegramService, RecipientError, AccountWorkerError
from .point_service import PointService, PointError

class SendEngine:
    def __init__(self, db, logs):
        self.db = db
        self.logs = logs
        self.telegram = TelegramService(db, logs)
        self.points = PointService(logs)
        self._uid_lock = threading.Lock()

    def sector_count(self):
        configured = int(self.db.get_setting("worker_sector_count", "1") or 1)
        return max(1, configured)

    def rebalance_account_sectors(self):
        accounts = self.db.fetchall(
            "SELECT id FROM telegram_accounts ORDER BY id"
        )
        if not accounts:
            return {"sectors": self.sector_count(), "accounts": 0}

        required = max(1, (len(accounts) + 9) // 10)
        configured = max(self.sector_count(), required)
        if configured != self.sector_count():
            self.db.set_setting("worker_sector_count", configured)

        with self.db.connection() as conn:
            for idx, account in enumerate(accounts):
                # 진행창을 추가하면 계정을 균등하게 다시 배치한다.
                # configured는 항상 ceil(account_count/10) 이상이므로 진행창당 최대 10개를 넘지 않는다.
                sector = (idx % configured) + 1
                conn.execute(
                    "UPDATE telegram_accounts SET worker_sector=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (sector, account["id"]),
                )

        return {"sectors": configured, "accounts": len(accounts)}

    def add_sector(self):
        current = self.sector_count()
        self.db.set_setting("worker_sector_count", current + 1)
        self.rebalance_account_sectors()
        return current + 1

    def remove_last_sector(self):
        current = self.sector_count()
        if current <= 1:
            raise RuntimeError("진행창은 최소 1개가 필요합니다.")

        target = current
        target_count = self.db.fetchone(
            "SELECT COUNT(*) c FROM telegram_accounts WHERE worker_sector=?",
            (target,),
        )["c"]

        remaining_capacity = (current - 1) * 10
        total_accounts = self.db.fetchone(
            "SELECT COUNT(*) c FROM telegram_accounts"
        )["c"]

        if total_accounts > remaining_capacity:
            raise RuntimeError(
                f"진행창 {target}을 삭제할 수 없습니다. "
                f"계정 {target_count}개를 옮길 빈 자리가 부족합니다."
            )

        self.db.set_setting("worker_sector_count", current - 1)
        self.rebalance_account_sectors()
        return current - 1

    def _group_accounts_by_sector(self, account_rows):
        groups = {}
        for account in account_rows:
            sector = int(account["worker_sector"] or 1)
            groups.setdefault(sector, []).append(account)
        return groups

    def _accounts(self):
        self.rebalance_account_sectors()
        return self.db.fetchall(
            "SELECT * FROM telegram_accounts WHERE enabled=1 "
            "AND status NOT IN ('SEND_RESTRICTED','PEER_FLOOD','FLOOD_WAIT','SESSION_ERROR','STOPPED') "
            "ORDER BY id"
        )

    def _pending(self):
        return self.db.fetchall("SELECT * FROM recipients WHERE status='PENDING' ORDER BY id")

    def create_manual_assignment_campaign(
        self,
        account_ids,
        name,
        bot_username,
        post_code,
    ):
        account_ids = sorted({int(x) for x in (account_ids or [])})
        if not account_ids:
            raise RuntimeError("DB를 배정할 텔레그램 계정을 체크해주세요.")

        marks = ",".join("?" for _ in account_ids)
        accounts = self.db.fetchall(
            f"SELECT * FROM telegram_accounts "
            f"WHERE id IN ({marks}) AND enabled=1 "
            f"AND status NOT IN ('SEND_RESTRICTED','PEER_FLOOD','FLOOD_WAIT',"
            f"'SESSION_ERROR','STOPPED','WORKER_ERROR') "
            f"ORDER BY id",
            tuple(account_ids),
        )

        if not accounts:
            raise RuntimeError("선택한 계정 중 DB를 배정할 수 있는 정상 계정이 없습니다.")

        valid_ids = {int(a["id"]) for a in accounts}
        excluded = len(account_ids) - len(valid_ids)

        max_per = int(self.db.get_setting("max_contacts_per_account", "40") or 40)

        capacities = {}
        for account in accounts:
            aid = int(account["id"])
            active = self.db.fetchone(
                "SELECT COUNT(*) c FROM campaign_recipients "
                "WHERE assigned_account_id=? "
                "AND status NOT IN ('MESSAGE_SENT','FAILED','FAILED_FINAL')",
                (aid,),
            )
            active_count = int(active["c"] or 0) if active else 0
            capacities[aid] = max(0, max_per - active_count)

        total_capacity = sum(capacities.values())
        if total_capacity <= 0:
            raise RuntimeError(
                "체크한 계정에 남은 DB 배정 용량이 없습니다. "
                "계정별 최대 처리량을 확인해주세요."
            )

        recipients = self.db.fetchall(
            "SELECT * FROM recipients "
            "WHERE status IN ('PENDING','REASSIGN_WAITING') "
            "AND status!='MESSAGE_SENT' "
            "ORDER BY "
            "CASE WHEN status='REASSIGN_WAITING' THEN 0 ELSE 1 END,"
            "id "
            "LIMIT ?",
            (total_capacity,),
        )

        if not recipients:
            raise RuntimeError(
                "현재 배정 가능한 대기 DB가 없습니다. "
                "PENDING 또는 재배정 대기 DB만 수동 배정할 수 있습니다."
            )

        assignments = []
        account_cycle = [int(a["id"]) for a in accounts]

        # 한 계정부터 꽉 채우지 않고 체크한 계정에 순환 배정한다.
        while recipients:
            progressed = False
            for aid in account_cycle:
                if not recipients:
                    break
                if capacities.get(aid, 0) <= 0:
                    continue

                recipient = recipients.pop(0)
                assignments.append((recipient, aid))
                capacities[aid] -= 1
                progressed = True

            if not progressed:
                break

        if not assignments:
            raise RuntimeError("배정 가능한 DB가 없습니다.")

        campaign_id = self.db.execute(
            "INSERT INTO campaigns(name,postbot_username,post_code,status,total_count) "
            "VALUES(?,?,?,'CONTACT_WAITING',?)",
            (name, bot_username, post_code, len(assignments)),
        )

        per_account = {}

        with self.db.connection() as conn:
            for recipient, account_id in assignments:
                recipient_id = int(recipient["id"])
                old_status = str(recipient["status"] or "")
                from_account_id = recipient["handoff_from_account_id"]

                conn.execute(
                    "INSERT INTO campaign_recipients("
                    "campaign_id,recipient_id,assigned_account_id,status,contact_status,"
                    "error_code,error_message,telegram_message_id"
                    ") VALUES(?,?,?,'ASSIGNED','WAITING',NULL,NULL,NULL)",
                    (campaign_id, recipient_id, account_id),
                )

                conn.execute(
                    "UPDATE recipients SET "
                    "assigned_account_id=?,status='ASSIGNED',contact_status='NOT_ADDED',"
                    "telegram_uid=NULL,telegram_username=NULL,contact_name=NULL,"
                    "contact_added_at=NULL,error_code=NULL,error_message=NULL,"
                    "telegram_message_id=NULL,processed_at=NULL,sent_at=NULL,"
                    "handoff_status=CASE WHEN ?='REASSIGN_WAITING' THEN 'ASSIGNED' ELSE handoff_status END,"
                    "handoff_count=CASE WHEN ?='REASSIGN_WAITING' THEN handoff_count+1 ELSE handoff_count END,"
                    "updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=? AND status IN ('PENDING','REASSIGN_WAITING')",
                    (
                        account_id,
                        old_status,
                        old_status,
                        recipient_id,
                    ),
                )

                conn.execute(
                    "INSERT INTO assignment_history("
                    "recipient_id,source_campaign_id,target_campaign_id,"
                    "from_account_id,to_account_id,event_type,reason_code,reason_message"
                    ") VALUES(?,?,?,?,?,'MANUAL_PREASSIGN',?,?)",
                    (
                        recipient_id,
                        recipient["handoff_source_campaign_id"],
                        campaign_id,
                        from_account_id,
                        account_id,
                        old_status,
                        "연락처 추가 전 체크 계정 수동 배정",
                    ),
                )

                per_account[account_id] = per_account.get(account_id, 0) + 1

        self.logs.write(
            "INFO",
            "배정",
            f"수동 DB 배정 작업 #{campaign_id} 생성 / "
            f"대상 {len(assignments)}명 / 체크 계정 {len(accounts)}개 / "
            f"사용불가 제외 {excluded}개",
            campaign_id=campaign_id,
        )

        return {
            "campaign_id": campaign_id,
            "assigned": len(assignments),
            "account_count": len(accounts),
            "excluded_accounts": excluded,
            "per_account": per_account,
        }

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

    def create_failed_retry_campaign(self, source_campaign_id, bot_username, post_code):
        source = self.db.fetchone(
            "SELECT * FROM campaigns WHERE id=?",
            (source_campaign_id,),
        )
        if not source:
            raise RuntimeError("재시도할 원본 작업을 찾을 수 없습니다.")

        accounts = self._accounts()
        if not accounts:
            raise RuntimeError("재시도에 사용할 정상 텔레그램 계정이 없습니다.")

        account_map = {int(a["id"]): a for a in accounts}

        failed_rows = self.db.fetchall(
            "SELECT "
            "cr.recipient_id,cr.assigned_account_id,cr.status,cr.contact_status,"
            "cr.error_code,cr.error_message,"
            "r.phone,r.telegram_uid,r.telegram_username,r.contact_name,r.contact_added_at "
            "FROM campaign_recipients cr "
            "JOIN recipients r ON r.id=cr.recipient_id "
            "WHERE cr.campaign_id=? "
            "AND cr.status='FAILED' "
            "AND r.status='FAILED' "
            "ORDER BY cr.recipient_id",
            (source_campaign_id,),
        )
        if not failed_rows:
            raise RuntimeError("재시도할 실패 DB가 없습니다.")

        max_per = int(self.db.get_setting("max_contacts_per_account", "40") or 40)
        capacities = {int(a["id"]): max_per for a in accounts}

        preserved = []
        needs_contact = []

        for row in failed_rows:
            old_account_id = int(row["assigned_account_id"] or 0)
            contact_ready = (
                row["contact_status"] == "ADDED"
                and bool(str(row["telegram_uid"] or "").strip())
                and old_account_id in account_map
            )

            if contact_ready:
                # 이미 이 Telegram 계정의 연락처에 추가된 DB는
                # 같은 계정을 그대로 사용하고 연락처 추가/UID Resolve를 다시 하지 않는다.
                preserved.append((row, old_account_id))
            else:
                needs_contact.append(row)

        assigned_new = []
        for row in needs_contact:
            target_id = None
            for account in accounts:
                aid = int(account["id"])
                if capacities.get(aid, 0) > 0:
                    target_id = aid
                    break
            if target_id is None:
                break
            capacities[target_id] -= 1
            assigned_new.append((row, target_id))

        chosen_count = len(preserved) + len(assigned_new)
        if chosen_count <= 0:
            raise RuntimeError("현재 계정 상태로 재시도할 실패 DB가 없습니다.")

        previous_round = int(source["retry_round"] or 0)
        retry_round = previous_round + 1

        campaign_id = self.db.execute(
            "INSERT INTO campaigns("
            "name,postbot_username,post_code,status,total_count,retry_of_campaign_id,retry_round"
            ") VALUES(?,?,?,'CONTACT_WAITING',?,?,?)",
            (
                f"재시도_{source_campaign_id}_{retry_round}",
                bot_username,
                post_code,
                chosen_count,
                source_campaign_id,
                retry_round,
            ),
        )

        with self.db.connection() as conn:
            for row, account_id in preserved:
                recipient_id = row["recipient_id"]

                conn.execute(
                    "UPDATE recipients SET "
                    "assigned_account_id=?,status='ASSIGNED',contact_status='ADDED',"
                    "error_code=NULL,error_message=NULL,telegram_message_id=NULL,"
                    "processed_at=NULL,sent_at=NULL,updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=? AND status='FAILED'",
                    (account_id, recipient_id),
                )

                conn.execute(
                    "INSERT INTO campaign_recipients("
                    "campaign_id,recipient_id,assigned_account_id,status,contact_status,"
                    "contact_added_at,error_code,error_message,telegram_message_id"
                    ") VALUES(?,?,?,'ASSIGNED','ADDED',?,NULL,NULL,NULL)",
                    (
                        campaign_id,
                        recipient_id,
                        account_id,
                        row["contact_added_at"],
                    ),
                )

                conn.execute(
                    "INSERT INTO retry_history("
                    "recipient_id,source_campaign_id,retry_campaign_id,retry_round,"
                    "previous_status,previous_error_code,previous_error_message,result_status"
                    ") VALUES(?,?,?,?,?,?,?,'RETRY_WAITING')",
                    (
                        recipient_id,
                        source_campaign_id,
                        campaign_id,
                        retry_round,
                        row["status"],
                        row["error_code"],
                        row["error_message"],
                    ),
                )

            for row, account_id in assigned_new:
                recipient_id = row["recipient_id"]

                conn.execute(
                    "UPDATE recipients SET "
                    "assigned_account_id=?,status='ASSIGNED',contact_status='NOT_ADDED',"
                    "telegram_uid=NULL,telegram_username=NULL,contact_name=NULL,"
                    "contact_added_at=NULL,error_code=NULL,error_message=NULL,"
                    "telegram_message_id=NULL,processed_at=NULL,sent_at=NULL,"
                    "updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=? AND status='FAILED'",
                    (account_id, recipient_id),
                )

                conn.execute(
                    "INSERT INTO campaign_recipients("
                    "campaign_id,recipient_id,assigned_account_id,status,contact_status,"
                    "error_code,error_message,telegram_message_id"
                    ") VALUES(?,?,?,'ASSIGNED','WAITING',NULL,NULL,NULL)",
                    (campaign_id, recipient_id, account_id),
                )

                conn.execute(
                    "INSERT INTO retry_history("
                    "recipient_id,source_campaign_id,retry_campaign_id,retry_round,"
                    "previous_status,previous_error_code,previous_error_message,result_status"
                    ") VALUES(?,?,?,?,?,?,?,'RETRY_WAITING')",
                    (
                        recipient_id,
                        source_campaign_id,
                        campaign_id,
                        retry_round,
                        row["status"],
                        row["error_code"],
                        row["error_message"],
                    ),
                )

        self.logs.write(
            "INFO",
            "재시도",
            f"실패 DB 전용 재시도 작업 #{campaign_id} 생성 / "
            f"원본 작업 #{source_campaign_id} / 대상 {chosen_count}명 / "
            f"연락처 재사용 {len(preserved)}명 / 연락처 재처리 {len(assigned_new)}명 / "
            f"재시도 {retry_round}차",
            campaign_id=campaign_id,
        )

        return {
            "campaign_id": campaign_id,
            "count": chosen_count,
            "contact_reused": len(preserved),
            "contact_readd": len(assigned_new),
            "retry_round": retry_round,
            "source_campaign_id": source_campaign_id,
        }

    def _sync_retry_history(self, campaign_id):
        campaign = self.db.fetchone(
            "SELECT retry_of_campaign_id,retry_round FROM campaigns WHERE id=?",
            (campaign_id,),
        )
        if not campaign or campaign["retry_of_campaign_id"] is None:
            return

        rows = self.db.fetchall(
            "SELECT recipient_id,status,error_code,error_message "
            "FROM campaign_recipients WHERE campaign_id=?",
            (campaign_id,),
        )

        with self.db.connection() as conn:
            for row in rows:
                conn.execute(
                    "UPDATE retry_history SET result_status=?,result_error_code=?,"
                    "result_error_message=?,updated_at=CURRENT_TIMESTAMP "
                    "WHERE retry_campaign_id=? AND recipient_id=?",
                    (
                        row["status"],
                        row["error_code"],
                        row["error_message"],
                        campaign_id,
                        row["recipient_id"],
                    ),
                )

    def _handoff_classification(self, status, contact_status, error_code, error_message):
        code = str(error_code or "").upper()
        message = str(error_message or "")
        message_upper = message.upper()

        final_codes = {
            "UID_DUPLICATE",
            "USER_NOT_FOUND",
            "PRIVACY_RESTRICTED",
            "POSTBOT_INVALID_CODE",
            "POSTBOT_RESULT_NOT_FOUND",
        }
        restricted_codes = {
            "FLOOD_WAIT",
            "PEER_FLOOD",
            "SEND_RESTRICTED",
            "ACCOUNT_RESTRICTED",
        }
        safe_reassign_codes = {
            "WORKER_ERROR",
            "SESSION_ERROR",
            "ACCOUNT_ERROR",
            "NETWORK_ERROR",
            "CONNECTION_ERROR",
            "TIMEOUT",
            "RPC_CALL_FAIL",
        }

        if status == "MESSAGE_SENT":
            return "DONE", "이미 발송 완료"

        if status == "UNCERTAIN" or contact_status == "CONTACT_UNCERTAIN":
            return "BLOCKED", "전송 결과가 불확실하여 중복발송 방지를 위해 자동 재배정하지 않음"

        if code in restricted_codes or "FROZENMETHODINVALIDERROR" in message_upper:
            return "BLOCKED", "Telegram 계정 제한/보호 응답으로 자동 계정교체 대상에서 제외"

        if code in final_codes:
            return "FINAL", "DB/수신자 자체 사유로 다른 계정 재배정 효과가 없음"

        if code in safe_reassign_codes:
            return "REASSIGN", "계정/세션/Worker 계열 오류로 다른 정상 계정에 승계 가능"

        if code in {"CONTACT_ADD_FAILED", "MESSAGE_SEND_FAILED"}:
            transient_words = (
                "TIMEOUT",
                "CONNECTION",
                "NETWORK",
                "DISCONNECTED",
                "CONNECTIONRESET",
                "SERVERERROR",
            )
            if any(word in message_upper for word in transient_words):
                return "REASSIGN", "네트워크/연결 계열 실패로 다른 정상 계정에 승계 가능"
            return "BLOCKED", "원인이 확정되지 않은 발송/연락처 실패라 자동 재배정하지 않음"

        if status in ("SEND_PAUSED",) or contact_status == "CONTACT_PAUSED":
            return "BLOCKED", "보류 원인을 확인한 뒤 수동 재배정 필요"

        return "BLOCKED", "자동 재배정 안전조건에 해당하지 않음"

    def classify_residual_work(self, campaign_id=None):
        if campaign_id is None:
            latest = self.latest_campaign()
            if not latest:
                return {"queued": 0, "final": 0, "blocked": 0, "done": 0}
            campaign_id = int(latest["id"])

        rows = self.db.fetchall(
            "SELECT "
            "cr.campaign_id,cr.recipient_id,cr.assigned_account_id,cr.status,cr.contact_status,"
            "cr.error_code,cr.error_message,r.handoff_count "
            "FROM campaign_recipients cr "
            "JOIN recipients r ON r.id=cr.recipient_id "
            "WHERE cr.campaign_id=? AND cr.status!='MESSAGE_SENT' "
            "ORDER BY cr.recipient_id",
            (campaign_id,),
        )

        result = {"queued": 0, "final": 0, "blocked": 0, "done": 0}

        with self.db.connection() as conn:
            for row in rows:
                kind, reason = self._handoff_classification(
                    row["status"],
                    row["contact_status"],
                    row["error_code"],
                    row["error_message"],
                )

                if kind == "DONE":
                    result["done"] += 1
                    continue

                handoff_count = int(row["handoff_count"] or 0)
                if kind == "REASSIGN" and handoff_count >= 3:
                    kind = "BLOCKED"
                    reason = "자동 승계 3회 초과 · 수동 확인 필요"

                if kind == "REASSIGN":
                    new_status = "REASSIGN_WAITING"
                    handoff_status = "WAITING"
                    result["queued"] += 1
                elif kind == "FINAL":
                    new_status = "FAILED_FINAL"
                    handoff_status = "FINAL"
                    result["final"] += 1
                else:
                    new_status = "REASSIGN_BLOCKED"
                    handoff_status = "BLOCKED"
                    result["blocked"] += 1

                conn.execute(
                    "UPDATE recipients SET "
                    "status=?,handoff_status=?,handoff_source_campaign_id=?,"
                    "handoff_from_account_id=?,handoff_reason=?,"
                    "error_code=?,error_message=?,updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=? AND status!='MESSAGE_SENT'",
                    (
                        new_status,
                        handoff_status,
                        campaign_id,
                        row["assigned_account_id"],
                        reason,
                        row["error_code"],
                        row["error_message"],
                        row["recipient_id"],
                    ),
                )

                conn.execute(
                    "INSERT INTO assignment_history("
                    "recipient_id,source_campaign_id,from_account_id,event_type,"
                    "reason_code,reason_message"
                    ") VALUES(?,?,?,?,?,?)",
                    (
                        row["recipient_id"],
                        campaign_id,
                        row["assigned_account_id"],
                        "QUEUE_REASSIGN" if kind == "REASSIGN" else (
                            "FINAL_FAILURE" if kind == "FINAL" else "REASSIGN_BLOCKED"
                        ),
                        row["error_code"],
                        reason,
                    ),
                )

        self.logs.write(
            "INFO",
            "승계",
            f"잔여 작업 분류 / 재배정대기 {result['queued']} / "
            f"최종실패 {result['final']} / 수동확인 {result['blocked']}",
            campaign_id=campaign_id,
        )
        return result

    def auto_assign_reassignment_waiting(self):
        rows = self.db.fetchall(
            "SELECT * FROM recipients WHERE status='REASSIGN_WAITING' "
            "AND handoff_status='WAITING' ORDER BY handoff_source_campaign_id,id"
        )
        if not rows:
            return {
                "assigned": 0,
                "campaign_ids": [],
                "waiting": 0,
            }

        accounts = self._accounts()
        if not accounts:
            return {
                "assigned": 0,
                "campaign_ids": [],
                "waiting": len(rows),
            }

        max_per = int(self.db.get_setting("max_contacts_per_account", "40") or 40)

        active_counts = {}
        for account in accounts:
            aid = int(account["id"])
            row = self.db.fetchone(
                "SELECT COUNT(*) c FROM campaign_recipients "
                "WHERE assigned_account_id=? "
                "AND status NOT IN ('MESSAGE_SENT','FAILED','FAILED_FINAL')",
                (aid,),
            )
            active_counts[aid] = int(row["c"] or 0) if row else 0

        capacities = {
            int(account["id"]): max(0, max_per - active_counts.get(int(account["id"]), 0))
            for account in accounts
        }

        source_groups = {}
        for row in rows:
            source_id = int(row["handoff_source_campaign_id"] or 0)
            if source_id:
                source_groups.setdefault(source_id, []).append(row)

        created_campaigns = []
        assigned_total = 0

        for source_campaign_id, group in sorted(source_groups.items()):
            source = self.db.fetchone(
                "SELECT * FROM campaigns WHERE id=?",
                (source_campaign_id,),
            )
            if not source:
                continue

            assignments = []

            for recipient in group:
                from_account_id = int(recipient["handoff_from_account_id"] or 0)
                target_id = None

                ordered_accounts = sorted(
                    accounts,
                    key=lambda a: (
                        -capacities.get(int(a["id"]), 0),
                        active_counts.get(int(a["id"]), 0),
                        int(a["id"]),
                    ),
                )

                for account in ordered_accounts:
                    aid = int(account["id"])
                    if aid == from_account_id:
                        continue
                    if capacities.get(aid, 0) <= 0:
                        continue
                    target_id = aid
                    break

                if target_id is None:
                    continue

                capacities[target_id] -= 1
                active_counts[target_id] = active_counts.get(target_id, 0) + 1
                assignments.append((recipient, target_id))

            if not assignments:
                continue

            campaign_id = self.db.execute(
                "INSERT INTO campaigns("
                "name,postbot_username,post_code,status,total_count,retry_of_campaign_id,retry_round"
                ") VALUES(?,?,?,'CONTACT_WAITING',?,?,?)",
                (
                    f"잔여승계_{source_campaign_id}",
                    source["postbot_username"],
                    source["post_code"],
                    len(assignments),
                    source_campaign_id,
                    int(source["retry_round"] or 0) + 1,
                ),
            )

            with self.db.connection() as conn:
                for recipient, target_id in assignments:
                    recipient_id = int(recipient["id"])
                    from_account_id = int(recipient["handoff_from_account_id"] or 0)

                    conn.execute(
                        "UPDATE recipients SET "
                        "assigned_account_id=?,status='ASSIGNED',contact_status='NOT_ADDED',"
                        "telegram_uid=NULL,telegram_username=NULL,contact_name=NULL,"
                        "contact_added_at=NULL,error_code=NULL,error_message=NULL,"
                        "telegram_message_id=NULL,processed_at=NULL,sent_at=NULL,"
                        "handoff_status='ASSIGNED',handoff_count=handoff_count+1,"
                        "updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=? AND status='REASSIGN_WAITING'",
                        (target_id, recipient_id),
                    )

                    conn.execute(
                        "INSERT INTO campaign_recipients("
                        "campaign_id,recipient_id,assigned_account_id,status,contact_status,"
                        "error_code,error_message,telegram_message_id"
                        ") VALUES(?,?,?,'ASSIGNED','WAITING',NULL,NULL,NULL)",
                        (campaign_id, recipient_id, target_id),
                    )

                    conn.execute(
                        "INSERT INTO assignment_history("
                        "recipient_id,source_campaign_id,target_campaign_id,"
                        "from_account_id,to_account_id,event_type,reason_code,reason_message"
                        ") VALUES(?,?,?,?,?,'HANDOFF_ASSIGNED',?,?)",
                        (
                            recipient_id,
                            source_campaign_id,
                            campaign_id,
                            from_account_id or None,
                            target_id,
                            "SAFE_REASSIGN",
                            recipient["handoff_reason"],
                        ),
                    )

            created_campaigns.append(campaign_id)
            assigned_total += len(assignments)

            self.logs.write(
                "INFO",
                "승계",
                f"잔여작업 승계 작업 #{campaign_id} 생성 / "
                f"원본 #{source_campaign_id} / {len(assignments)}건 새 계정 배정",
                campaign_id=campaign_id,
            )

        waiting = self.db.fetchone(
            "SELECT COUNT(*) c FROM recipients WHERE status='REASSIGN_WAITING'"
        )
        waiting_count = int(waiting["c"] or 0) if waiting else 0

        return {
            "assigned": assigned_total,
            "campaign_ids": created_campaigns,
            "waiting": waiting_count,
        }

    def residual_handoff_summary(self):
        rows = self.db.fetchall(
            "SELECT status,COUNT(*) c FROM recipients "
            "WHERE status IN ('REASSIGN_WAITING','REASSIGN_BLOCKED','FAILED_FINAL') "
            "GROUP BY status"
        )
        result = {
            "waiting": 0,
            "blocked": 0,
            "final": 0,
        }
        for row in rows:
            if row["status"] == "REASSIGN_WAITING":
                result["waiting"] = int(row["c"] or 0)
            elif row["status"] == "REASSIGN_BLOCKED":
                result["blocked"] = int(row["c"] or 0)
            elif row["status"] == "FAILED_FINAL":
                result["final"] = int(row["c"] or 0)
        return result

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

    def _claim_resolved_uid(
        self,
        campaign_id,
        account_id,
        recipient_id,
        uid,
        username,
        contact_name,
    ):
        uid_text = str(uid)

        with self._uid_lock:
            duplicate = self.db.fetchone(
                "SELECT id,phone,status FROM recipients "
                "WHERE telegram_uid=? AND id!=? "
                "AND contact_status='ADDED' "
                "ORDER BY id LIMIT 1",
                (uid_text, recipient_id),
            )

            if duplicate:
                with self.db.connection() as conn:
                    conn.execute(
                        "UPDATE campaign_recipients SET contact_status='FAILED',status='FAILED',"
                        "error_code='UID_DUPLICATE',"
                        "error_message=?,updated_at=CURRENT_TIMESTAMP "
                        "WHERE campaign_id=? AND recipient_id=?",
                        (
                            f"동일 Telegram UID가 DB #{duplicate['id']}에 이미 존재",
                            campaign_id,
                            recipient_id,
                        ),
                    )
                    conn.execute(
                        "UPDATE recipients SET status='FAILED',contact_status='FAILED',"
                        "telegram_uid=?,telegram_username=?,contact_name=?,"
                        "error_code='UID_DUPLICATE',error_message=?,"
                        "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (
                            uid_text,
                            username,
                            contact_name,
                            f"동일 Telegram UID가 DB #{duplicate['id']}에 이미 존재",
                            recipient_id,
                        ),
                    )

                self.logs.write(
                    "WARNING",
                    "UID",
                    f"UID 중복 제외 / UID={uid_text} / 기존 DB #{duplicate['id']}",
                    account_id=account_id,
                    campaign_id=campaign_id,
                    recipient_id=recipient_id,
                )
                return False

            with self.db.connection() as conn:
                conn.execute(
                    "UPDATE campaign_recipients SET contact_status='ADDED',"
                    "contact_added_at=CURRENT_TIMESTAMP,error_code=NULL,error_message=NULL,"
                    "updated_at=CURRENT_TIMESTAMP "
                    "WHERE campaign_id=? AND recipient_id=?",
                    (campaign_id, recipient_id),
                )
                conn.execute(
                    "UPDATE recipients SET telegram_uid=?,telegram_username=?,contact_name=?,"
                    "contact_status='ADDED',contact_added_at=CURRENT_TIMESTAMP,"
                    "error_code=NULL,error_message=NULL,updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=?",
                    (
                        uid_text,
                        username,
                        contact_name,
                        recipient_id,
                    ),
                )

            self.logs.write(
                "INFO",
                "UID",
                f"Telegram UID Resolve 성공 / UID={uid_text} / 중복 없음",
                account_id=account_id,
                campaign_id=campaign_id,
                recipient_id=recipient_id,
            )
            return True

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
            sector_groups = self._group_accounts_by_sector(account_rows)
            self.logs.write(
                "INFO",
                "병렬",
                f"연락처 작업 병렬 실행 / 진행창 {len(sector_groups)}개 / 진행창당 최대 계정 10개",
                campaign_id=campaign_id,
            )

            with ThreadPoolExecutor(
                max_workers=max(1, len(sector_groups)),
                thread_name_prefix="AngelToggleContactSector"
            ) as sector_pool:
                futures = [
                    sector_pool.submit(
                        self._run_contact_sector,
                        campaign_id,
                        sector_id,
                        accounts,
                    )
                    for sector_id, accounts in sorted(sector_groups.items())
                ]
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        self.logs.write(
                            "ERROR",
                            "연락처",
                            f"진행창 Worker 오류: {type(e).__name__}: {e}",
                            campaign_id=campaign_id,
                        )

        stats = self.db.fetchone(
            "SELECT "
            "SUM(CASE WHEN campaign_recipients.contact_status='ADDED' THEN 1 ELSE 0 END) ready, "
            "SUM(CASE WHEN campaign_recipients.contact_status='FAILED' THEN 1 ELSE 0 END) failed, "
            "SUM(CASE WHEN campaign_recipients.contact_status IN ('WAITING','ADDING','CONTACT_PAUSED','CONTACT_UNCERTAIN') THEN 1 ELSE 0 END) paused "
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
        self._sync_retry_history(campaign_id)
        return {"ready": ready, "failed": failed, "paused": paused}

    def _run_contact_sector(self, campaign_id, sector_id, accounts):
        limited = list(accounts)[:10]
        self.logs.write(
            "INFO",
            "진행창",
            f"진행창 {sector_id} 시작 / 계정 {len(limited)}개 병렬",
            campaign_id=campaign_id,
        )

        with ThreadPoolExecutor(
            max_workers=max(1, len(limited)),
            thread_name_prefix=f"AngelToggleContactS{sector_id}"
        ) as pool:
            futures = [
                pool.submit(self._contact_thread, campaign_id, account["id"])
                for account in limited
            ]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    self.logs.write(
                        "ERROR",
                        "진행창",
                        f"진행창 {sector_id} 계정 Worker 오류 격리: {type(e).__name__}: {e}",
                        campaign_id=campaign_id,
                    )

        self.logs.write(
            "INFO",
            "진행창",
            f"진행창 {sector_id} 연락처 단계 종료",
            campaign_id=campaign_id,
        )

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

                    resolved_ok = 0
                    duplicate_uid = 0
                    resolve_failed = 0

                    for recipient_id, payload in success.items():
                        user = payload["user"]
                        contact_name = payload.get("contact_name") or ""

                        try:
                            resolved = await self.telegram.resolve_imported_user(
                                client,
                                user,
                            )
                            claimed = self._claim_resolved_uid(
                                campaign_id,
                                account_id,
                                recipient_id,
                                resolved["uid"],
                                resolved.get("username"),
                                contact_name,
                            )
                            if claimed:
                                resolved_ok += 1
                            else:
                                duplicate_uid += 1

                        except AccountWorkerError:
                            raise
                        except RecipientError as e:
                            resolve_failed += 1
                            with self.db.connection() as conn:
                                conn.execute(
                                    "UPDATE campaign_recipients SET contact_status='FAILED',status='FAILED',"
                                    "error_code=?,error_message=?,updated_at=CURRENT_TIMESTAMP "
                                    "WHERE campaign_id=? AND recipient_id=?",
                                    (
                                        e.code,
                                        str(e),
                                        campaign_id,
                                        recipient_id,
                                    ),
                                )
                                conn.execute(
                                    "UPDATE recipients SET contact_status='FAILED',status='FAILED',"
                                    "error_code=?,error_message=?,updated_at=CURRENT_TIMESTAMP "
                                    "WHERE id=?",
                                    (
                                        e.code,
                                        str(e),
                                        recipient_id,
                                    ),
                                )

                    with self.db.connection() as conn:
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
                        f"10명 묶음 처리 / 요청 {len(batch)}명 / UID확정 {resolved_ok}명 / "
                        f"UID중복 {duplicate_uid}명 / Resolve실패 {resolve_failed}명 / 미확인 {len(missing)}명",
                        account_id=account_id, campaign_id=campaign_id,
                    )

                except AccountWorkerError as e:
                    if point_reserved:
                        try:
                            refund = self.points.cancel_send(message_key, str(e))
                            self.logs.write(
                                "INFO",
                                "포인트",
                                f"계정 오류 발송취소 환불 +10원 / 잔액 {int(refund.get('point_balance_krw') or 0):,}원",
                                account_id=account_id,
                                campaign_id=campaign_id,
                                recipient_id=recipient_id,
                            )
                        except Exception as refund_error:
                            self.logs.write(
                                "WARNING",
                                "포인트",
                                f"계정 오류 포인트 환불 확인 실패: {refund_error}",
                                account_id=account_id,
                                campaign_id=campaign_id,
                                recipient_id=recipient_id,
                            )

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

        try:
            point = self.points.balance()
        except PointError as e:
            raise RuntimeError(f"발송 포인트 확인 실패: {e}") from e

        unit_price = int(point["unit_price_krw"] or 10)
        required_points = int(ready) * unit_price
        current_points = int(point["balance_krw"] or 0)

        if current_points < required_points:
            raise RuntimeError(
                f"발송 포인트가 부족합니다. 현재 {current_points:,}원 / "
                f"필요 {required_points:,}원 ({ready}건 × {unit_price}원)"
            )

        self.logs.write(
            "INFO",
            "포인트",
            f"발송 포인트 확인 / 현재 {current_points:,}원 / "
            f"예상 사용 {required_points:,}원 / 건당 {unit_price}원",
            campaign_id=campaign_id,
        )

        self.db.execute(
            "UPDATE campaigns SET status='SEND_RUNNING',started_at=CURRENT_TIMESTAMP WHERE id=?",
            (campaign_id,),
        )
        self.logs.write(
            "INFO", "발송",
            f"게시물 발송 시작 / 연락처 추가 완료 {ready}명 대상",
            campaign_id=campaign_id,
        )

        sector_groups = self._group_accounts_by_sector(account_rows)
        self.logs.write(
            "INFO",
            "병렬",
            f"발송 병렬 실행 / 진행창 {len(sector_groups)}개 / 진행창당 최대 계정 10개",
            campaign_id=campaign_id,
        )

        with ThreadPoolExecutor(
            max_workers=max(1, len(sector_groups)),
            thread_name_prefix="AngelToggleSendSector"
        ) as sector_pool:
            futures = [
                sector_pool.submit(
                    self._run_send_sector,
                    campaign_id,
                    sector_id,
                    accounts,
                )
                for sector_id, accounts in sorted(sector_groups.items())
            ]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    self.logs.write(
                        "ERROR",
                        "발송",
                        f"진행창 Worker 오류: {type(e).__name__}: {e}",
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
        paused_accounts = self.db.fetchall(
            "SELECT a.id,a.phone,a.username,a.name,a.status,a.last_error,"
            "COUNT(cr.recipient_id) paused_count "
            "FROM campaign_recipients cr "
            "JOIN telegram_accounts a ON a.id=cr.assigned_account_id "
            "WHERE cr.campaign_id=? "
            "AND cr.status IN ('SEND_PAUSED','UNCERTAIN') "
            "GROUP BY a.id,a.phone,a.username,a.name,a.status,a.last_error "
            "ORDER BY a.id",
            (campaign_id,),
        )
        paused_summary = []
        for row in paused_accounts:
            paused_summary.append({
                "account_id": int(row["id"]),
                "label": row["phone"] or row["username"] or row["name"] or f"계정-{row['id']}",
                "status": row["status"] or "",
                "last_error": row["last_error"] or "",
                "paused_count": int(row["paused_count"] or 0),
            })

        self.logs.write(
            "SUCCESS" if success else "WARNING", "발송",
            f"게시물 발송 종료 / 성공 {success}명 / 실패 {failed}명 / 보류 {remaining}명",
            campaign_id=campaign_id,
        )
        for item in paused_summary:
            self.logs.write(
                "WARNING",
                "보류원인",
                f"{item['label']} / {item['status']} / "
                f"{item['last_error'] or '원인 메시지 없음'} / "
                f"잔여 {item['paused_count']}건",
                account_id=item["account_id"],
                campaign_id=campaign_id,
            )

        self._sync_retry_history(campaign_id)
        return {
            "success": success,
            "failed": failed,
            "remaining": remaining,
            "paused_accounts": paused_summary,
        }

    def _run_send_sector(self, campaign_id, sector_id, accounts):
        limited = list(accounts)[:10]
        self.logs.write(
            "INFO",
            "진행창",
            f"진행창 {sector_id} 발송 시작 / 계정 {len(limited)}개 병렬",
            campaign_id=campaign_id,
        )

        with ThreadPoolExecutor(
            max_workers=max(1, len(limited)),
            thread_name_prefix=f"AngelToggleSendS{sector_id}"
        ) as pool:
            futures = [
                pool.submit(self._send_thread, campaign_id, account["id"])
                for account in limited
            ]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    self.logs.write(
                        "ERROR",
                        "진행창",
                        f"진행창 {sector_id} 계정 Worker 오류 격리: {type(e).__name__}: {e}",
                        campaign_id=campaign_id,
                    )

        self.logs.write(
            "INFO",
            "진행창",
            f"진행창 {sector_id} 발송 단계 종료",
            campaign_id=campaign_id,
        )

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
                "SELECT cr.recipient_id,r.telegram_uid,r.contact_name "
                "FROM campaign_recipients cr "
                "JOIN recipients r ON r.id=cr.recipient_id "
                "WHERE cr.campaign_id=? AND cr.assigned_account_id=? "
                "AND cr.contact_status='ADDED' "
                "AND r.telegram_uid IS NOT NULL AND r.telegram_uid!='' "
                "AND cr.status IN ('ASSIGNED','SEND_PAUSED') "
                "ORDER BY r.id",
                (campaign_id, account_id),
            )

            if not targets:
                return

            try:
                prepared_post = await self.telegram.prepare_postbot_inline_result(
                    client,
                    campaign["postbot_username"],
                    campaign["post_code"],
                )
                self.logs.write(
                    "INFO",
                    "발송",
                    f"PostBot Inline Query 1회 준비 완료 / "
                    f"대상 {len(targets)}명 / PostBot={prepared_post['post_code']}",
                    account_id=account_id,
                    campaign_id=campaign_id,
                )
            except RecipientError as e:
                self._pause_send_account(
                    campaign_id,
                    account_id,
                    e.code,
                    str(e),
                )
                return
            except AccountWorkerError as e:
                self._pause_send_account(
                    campaign_id,
                    account_id,
                    e.code,
                    str(e),
                )
                return

            for target in targets:
                recipient_id = target["recipient_id"]
                uid = str(target["telegram_uid"] or "").strip()

                duplicate = self.db.fetchone(
                    "SELECT id FROM recipients "
                    "WHERE telegram_uid=? AND id!=? "
                    "AND contact_status='ADDED' "
                    "ORDER BY id LIMIT 1",
                    (uid, recipient_id),
                )
                if duplicate:
                    with self.db.connection() as conn:
                        conn.execute(
                            "UPDATE campaign_recipients SET status='FAILED',"
                            "error_code='UID_DUPLICATE',error_message=?,"
                            "updated_at=CURRENT_TIMESTAMP "
                            "WHERE campaign_id=? AND recipient_id=?",
                            (
                                f"동일 Telegram UID가 DB #{duplicate['id']}에 이미 존재",
                                campaign_id,
                                recipient_id,
                            ),
                        )
                        conn.execute(
                            "UPDATE recipients SET status='FAILED',contact_status='FAILED',"
                            "error_code='UID_DUPLICATE',error_message=?,"
                            "processed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP "
                            "WHERE id=?",
                            (
                                f"동일 Telegram UID가 DB #{duplicate['id']}에 이미 존재",
                                recipient_id,
                            ),
                        )
                    continue

                claimed = self.db.execute_rowcount(
                    "UPDATE campaign_recipients SET status='SENDING',"
                    "postbot_code=?,updated_at=CURRENT_TIMESTAMP "
                    "WHERE campaign_id=? AND recipient_id=? AND assigned_account_id=? "
                    "AND contact_status='ADDED' AND status IN ('ASSIGNED','SEND_PAUSED')",
                    (
                        campaign["post_code"],
                        campaign_id,
                        recipient_id,
                        account_id,
                    ),
                )
                if claimed != 1:
                    continue

                message_key = f"{campaign_id}:{recipient_id}"
                account_key = str(account["id"])
                account_label = (
                    account["phone"]
                    or account["username"]
                    or account["name"]
                    or f"계정-{account_id}"
                )
                point_reserved = False

                try:
                    reserve = self.points.reserve_send(
                        message_key=message_key,
                        telegram_account_key=account_key,
                        telegram_account_label=account_label,
                        campaign_id=campaign_id,
                        recipient_id=recipient_id,
                    )
                    point_reserved = True

                    self.logs.write(
                        "INFO",
                        "포인트",
                        f"발송 10원 예약 / 잔액 {int(reserve.get('point_balance_krw') or 0):,}원",
                        account_id=account_id,
                        campaign_id=campaign_id,
                        recipient_id=recipient_id,
                    )

                    peer = int(uid)

                    self.logs.write(
                        "INFO",
                        "발송",
                        f"준비된 PostBot 게시물 즉시 전송 / UID={uid}",
                        account_id=account_id,
                        campaign_id=campaign_id,
                        recipient_id=recipient_id,
                    )

                    sent = await self.telegram.send_prepared_postbot_inline(
                        peer,
                        prepared_post,
                    )
                    message_id = sent["message_id"]
                    postbot_code = sent["post_code"]

                    try:
                        confirmed = self.points.confirm_send(
                            message_key=message_key,
                            telegram_message_id=message_id,
                        )
                        self.logs.write(
                            "SUCCESS",
                            "포인트",
                            f"발송 확정 · 10원 사용 / 잔액 {int(confirmed.get('point_balance_krw') or 0):,}원",
                            account_id=account_id,
                            campaign_id=campaign_id,
                            recipient_id=recipient_id,
                        )
                    except PointError as e:
                        # Telegram message_id가 이미 확인된 뒤에는 중복발송 방지를 위해
                        # MESSAGE_SENT 처리를 유지한다. 예약된 10원은 이미 서버에서 차감되어 있다.
                        self.logs.write(
                            "WARNING",
                            "포인트",
                            f"발송 성공 후 포인트 확정 응답 확인 실패: {e}",
                            account_id=account_id,
                            campaign_id=campaign_id,
                            recipient_id=recipient_id,
                        )

                    with self.db.connection() as conn:
                        conn.execute(
                            "UPDATE campaign_recipients SET status='MESSAGE_SENT',"
                            "telegram_message_id=?,sent_at=CURRENT_TIMESTAMP,"
                            "postbot_code=?,error_code=NULL,error_message=NULL,"
                            "updated_at=CURRENT_TIMESTAMP "
                            "WHERE campaign_id=? AND recipient_id=? AND status='SENDING'",
                            (
                                message_id,
                                postbot_code,
                                campaign_id,
                                recipient_id,
                            ),
                        )
                        conn.execute(
                            "UPDATE recipients SET status='MESSAGE_SENT',"
                            "telegram_message_id=?,processed_at=CURRENT_TIMESTAMP,"
                            "sent_at=CURRENT_TIMESTAMP,error_code=NULL,error_message=NULL,"
                            "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                            (
                                message_id,
                                recipient_id,
                            ),
                        )

                    self.logs.write(
                        "SUCCESS",
                        "발송",
                        f"Inline Result 직접 전송 성공 / UID={uid} / "
                        f"Message ID={message_id} / PostBot={postbot_code}",
                        account_id=account_id,
                        campaign_id=campaign_id,
                        recipient_id=recipient_id,
                    )

                    try:
                        send_interval = float(
                            self.db.get_setting("telegram_send_interval_seconds", "1.0") or 1.0
                        )
                    except Exception:
                        send_interval = 1.0
                    send_interval = max(0.0, min(send_interval, 10.0))
                    if send_interval > 0:
                        await asyncio.sleep(send_interval)

                except PointError as e:
                    if point_reserved:
                        try:
                            self.points.cancel_send(message_key, str(e))
                        except Exception:
                            pass

                    code = e.code or "POINT_ERROR"
                    with self.db.connection() as conn:
                        conn.execute(
                            "UPDATE campaign_recipients SET status='SEND_PAUSED',"
                            "error_code=?,error_message=?,updated_at=CURRENT_TIMESTAMP "
                            "WHERE campaign_id=? AND recipient_id=?",
                            (code, str(e), campaign_id, recipient_id),
                        )
                        conn.execute(
                            "UPDATE recipients SET status='ASSIGNED',"
                            "error_code=?,error_message=?,updated_at=CURRENT_TIMESTAMP "
                            "WHERE id=?",
                            (code, str(e), recipient_id),
                        )

                    self.logs.write(
                        "ERROR",
                        "포인트",
                        f"{code}: {e}",
                        account_id=account_id,
                        campaign_id=campaign_id,
                        recipient_id=recipient_id,
                    )
                    break

                except RecipientError as e:
                    if point_reserved:
                        try:
                            refund = self.points.cancel_send(message_key, str(e))
                            self.logs.write(
                                "INFO",
                                "포인트",
                                f"발송 실패 환불 +10원 / 잔액 {int(refund.get('point_balance_krw') or 0):,}원",
                                account_id=account_id,
                                campaign_id=campaign_id,
                                recipient_id=recipient_id,
                            )
                        except Exception as refund_error:
                            self.logs.write(
                                "WARNING",
                                "포인트",
                                f"발송 실패 포인트 환불 확인 실패: {refund_error}",
                                account_id=account_id,
                                campaign_id=campaign_id,
                                recipient_id=recipient_id,
                            )

                    with self.db.connection() as conn:
                        conn.execute(
                            "UPDATE campaign_recipients SET status='FAILED',"
                            "error_code=?,error_message=?,updated_at=CURRENT_TIMESTAMP "
                            "WHERE campaign_id=? AND recipient_id=?",
                            (
                                e.code,
                                str(e),
                                campaign_id,
                                recipient_id,
                            ),
                        )
                        conn.execute(
                            "UPDATE recipients SET status='FAILED',"
                            "error_code=?,error_message=?,processed_at=CURRENT_TIMESTAMP,"
                            "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                            (
                                e.code,
                                str(e),
                                recipient_id,
                            ),
                        )

                    self.logs.write(
                        "ERROR",
                        "발송",
                        f"{e.code}: {e}",
                        account_id=account_id,
                        campaign_id=campaign_id,
                        recipient_id=recipient_id,
                    )
                    continue

                except AccountWorkerError as e:
                    self.db.execute(
                        "UPDATE campaign_recipients SET status='UNCERTAIN',"
                        "error_code=?,error_message=?,updated_at=CURRENT_TIMESTAMP "
                        "WHERE campaign_id=? AND recipient_id=? AND status='SENDING'",
                        (
                            e.code,
                            str(e),
                            campaign_id,
                            recipient_id,
                        ),
                    )
                    self.db.execute(
                        "UPDATE recipients SET status='UNCERTAIN',"
                        "error_code=?,error_message=?,updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=?",
                        (
                            e.code,
                            str(e),
                            recipient_id,
                        ),
                    )
                    self._pause_send_account(
                        campaign_id,
                        account_id,
                        e.code,
                        str(e),
                    )
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

    def cancel_active_assignments(self, account_ids=None, campaign_id=None, reassign_waiting=False):
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
                if reassign_waiting:
                    conn.execute(
                        "UPDATE recipients SET "
                        "assigned_account_id=NULL,status='REASSIGN_WAITING',contact_status='NOT_ADDED',"
                        "telegram_uid=NULL,telegram_username=NULL,contact_name=NULL,"
                        "contact_added_at=NULL,error_code='ACCOUNT_DELETED',"
                        "error_message='담당 계정 삭제로 정상 계정 재배정 대기',"
                        "telegram_message_id=NULL,processed_at=NULL,sent_at=NULL,"
                        "handoff_status='WAITING',handoff_source_campaign_id=?,"
                        "handoff_from_account_id=?,handoff_reason='담당 계정 삭제로 재배정',"
                        "updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=? AND status!='MESSAGE_SENT'",
                        (
                            row["campaign_id"],
                            row["assigned_account_id"],
                            row["recipient_id"],
                        ),
                    )
                else:
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
            (
                f"진행 중 배정 {len(rows)}건 취소 / "
                + ("삭제 계정 잔여 DB를 재배정 대기로 이동" if reassign_waiting
                   else "고객 DB를 대기상태로 복구")
            ),
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
