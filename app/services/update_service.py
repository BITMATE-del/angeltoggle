import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

try:
    from app.version import VERSION
except Exception:
    VERSION = "0.0.0"

RELEASE_API = "https://api.github.com/repos/BITMATE-del/angeltoggle/releases/latest"

def _version_tuple(value):
    nums = re.findall(r"\d+", value or "")
    return tuple(int(x) for x in nums[:3] + ["0"] * max(0, 3 - len(nums)))

class UpdateService:
    def __init__(self, logs=None):
        self.logs = logs

    def check_latest(self):
        req = urllib.request.Request(
            RELEASE_API,
            headers={"User-Agent": "AngelToggle-Updater"}
        )
        with urllib.request.urlopen(req, timeout=8) as response:
            data = json.loads(response.read().decode("utf-8"))

        latest = (data.get("tag_name") or "").lstrip("v")
        asset = None
        for item in data.get("assets", []):
            if item.get("name") == "AngelToggle.exe":
                asset = item.get("browser_download_url")
                break

        return {
            "current": VERSION,
            "latest": latest,
            "available": bool(latest and asset and _version_tuple(latest) > _version_tuple(VERSION)),
            "download_url": asset,
        }

    def download_and_replace(self, download_url):
        if not getattr(sys, "frozen", False):
            raise RuntimeError("개발 실행 상태에서는 자동 교체를 하지 않습니다.")

        current_exe = Path(sys.executable).resolve()
        temp_dir = Path(tempfile.gettempdir()) / "AngelToggleUpdate"
        temp_dir.mkdir(parents=True, exist_ok=True)
        new_exe = temp_dir / "AngelToggle_new.exe"

        req = urllib.request.Request(
            download_url,
            headers={"User-Agent": "AngelToggle-Updater"}
        )
        with urllib.request.urlopen(req, timeout=60) as response, open(new_exe, "wb") as f:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)

        if new_exe.stat().st_size < 1024 * 1024:
            raise RuntimeError("업데이트 파일 크기가 비정상입니다.")

        script = temp_dir / "angeltoggle_update.cmd"
        pid = os.getpid()
        script.write_text(
            "@echo off\r\n"
            "chcp 65001 >nul\r\n"
            f":wait\r\n"
            f"tasklist /FI \"PID eq {pid}\" | find \"{pid}\" >nul\r\n"
            f"if not errorlevel 1 (timeout /t 1 /nobreak >nul & goto wait)\r\n"
            f"move /Y \"{new_exe}\" \"{current_exe}\" >nul\r\n"
            f"start \"\" \"{current_exe}\"\r\n"
            "del \"%~f0\"\r\n",
            encoding="utf-8",
        )

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            ["cmd.exe", "/c", str(script)],
            creationflags=creationflags,
            close_fds=True,
        )
        return True
