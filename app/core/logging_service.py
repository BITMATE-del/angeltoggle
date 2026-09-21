from datetime import datetime

class LogService:
    def __init__(self, db):
        self.db = db
        self.listeners = []

    def subscribe(self, callback):
        self.listeners.append(callback)

    def write(self, level, category, message, account_id=None, campaign_id=None, recipient_id=None):
        self.db.execute(
            "INSERT INTO work_logs(level,category,account_id,campaign_id,recipient_id,message) VALUES(?,?,?,?,?,?)",
            (level, category, account_id, campaign_id, recipient_id, message)
        )
        item = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "category": category,
            "message": message,
            "account_id": account_id
        }
        for cb in self.listeners[:]:
            try:
                cb(item)
            except Exception:
                pass
