from datetime import datetime
import threading

class LogService:
    def __init__(self, db):
        self.db = db
        self.listeners = []
        self._lock = threading.Lock()

    def subscribe(self, callback):
        with self._lock:
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
            "account_id": account_id,
            "campaign_id": campaign_id,
            "recipient_id": recipient_id,
        }
        with self._lock:
            listeners = self.listeners[:]
        for cb in listeners:
            try:
                cb(item)
            except Exception:
                pass
