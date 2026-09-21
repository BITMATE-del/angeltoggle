"""
엔젤토글 Telegram 연동 계층.

원칙:
- 계정별 Client 독립
- 계정 간 병렬
- 동일 계정 내부 순차
- 한 계정 오류는 해당 Worker만 정지
- Telegram 제한 우회 금지
- 로컬 오류/정지 플래그는 관리자 재시작 가능
- 실제 Telegram 서버 제한은 재검사 후 정상 확인 시에만 복구
"""

class TelegramService:
    def __init__(self, db, logs):
        self.db = db
        self.logs = logs

    def api_configured(self):
        return bool(self.db.get_setting("telegram_api_id")) and bool(self.db.get_setting("telegram_api_hash"))
