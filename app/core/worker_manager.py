import asyncio
from dataclasses import dataclass

@dataclass
class WorkerState:
    account_id: int
    status: str = "STOPPED"
    last_error: str = ""
    stop_requested: bool = False

class WorkerManager:
    def __init__(self, db, logs):
        self.db = db
        self.logs = logs
        self.states = {}
        self.tasks = {}

    async def start_account(self, account_id):
        state = self.states.setdefault(account_id, WorkerState(account_id))
        if state.status == "RUNNING":
            return
        state.stop_requested = False
        state.status = "RUNNING"
        self.logs.write("INFO", "ACCOUNT", "Worker 시작", account_id=account_id)
        self.tasks[account_id] = asyncio.create_task(self._run(account_id))

    async def _run(self, account_id):
        state = self.states[account_id]
        try:
            while not state.stop_requested:
                await asyncio.sleep(0.5)
                break
            if state.status == "RUNNING":
                state.status = "IDLE"
        except Exception as e:
            state.status = "ERROR"
            state.last_error = str(e)
            self.logs.write("ERROR", "ACCOUNT", f"Worker 오류: {e}", account_id=account_id)

    def stop_account(self, account_id):
        state = self.states.setdefault(account_id, WorkerState(account_id))
        state.stop_requested = True
        state.status = "STOPPED"
        self.logs.write("WARNING", "ACCOUNT", "Worker 수동 정지", account_id=account_id)

    def clear_local_error(self, account_id):
        state = self.states.setdefault(account_id, WorkerState(account_id))
        state.last_error = ""
        state.status = "READY"
        self.logs.write("INFO", "ACCOUNT", "로컬 오류 상태 초기화", account_id=account_id)
