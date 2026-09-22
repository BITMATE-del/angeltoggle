import json
import os
import re
import subprocess
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
    return tuple(int(x) for x in (nums + ["0", "0", "0"])[:3])


class UpdateService:
    def __init__(self, logs=None):
        self.logs = logs

    def check_latest(self):
        req = urllib.request.Request(
            RELEASE_API,
            headers={
                "User-Agent": "AngelToggle-Installer-Checker",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=12) as response:
            data = json.loads(response.read().decode("utf-8"))

        latest = (data.get("tag_name") or "").lstrip("v")
        installer_url = None
        installer_size = 0

        for item in data.get("assets", []):
            if item.get("name") == "AngelToggle-Setup.exe":
                installer_url = item.get("browser_download_url")
                installer_size = int(item.get("size") or 0)
                break

        return {
            "current": VERSION,
            "latest": latest,
            "available": bool(
                latest
                and installer_url
                and _version_tuple(latest) > _version_tuple(VERSION)
            ),
            "installer_url": installer_url,
            "installer_size": installer_size,
        }

    def download_installer(self, installer_url, latest_version=None):
        if not installer_url:
            raise RuntimeError("최신 설치마법사 주소를 찾을 수 없습니다.")

        base = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "AngelToggle" / "update"
        base.mkdir(parents=True, exist_ok=True)

        version = (latest_version or "latest").strip()
        target = base / f"AngelToggle-Setup-{version}.exe"
        temp = base / f"AngelToggle-Setup-{version}.download"

        try:
            temp.unlink(missing_ok=True)
        except Exception:
            pass

        req = urllib.request.Request(
            installer_url,
            headers={
                "User-Agent": "AngelToggle-Installer-Downloader",
                "Accept": "application/octet-stream",
            },
        )

        downloaded = 0
        with urllib.request.urlopen(req, timeout=180) as response, open(temp, "wb") as f:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)

        if not temp.exists() or temp.stat().st_size < 5 * 1024 * 1024:
            raise RuntimeError("설치마법사 다운로드가 정상적으로 완료되지 않았습니다.")

        try:
            target.unlink(missing_ok=True)
        except Exception:
            pass
        temp.replace(target)

        if self.logs:
            self.logs.write(
                "INFO",
                "업데이트",
                f"설치마법사 다운로드 완료 / {downloaded // (1024 * 1024)}MB / {target.name}",
            )

        return str(target)

    def launch_installer(self, installer_path):
        path = Path(installer_path)
        if not path.exists():
            raise RuntimeError("다운로드한 설치마법사를 찾을 수 없습니다.")

        subprocess.Popen(
            [str(path)],
            cwd=str(path.parent),
            close_fds=True,
        )

        if self.logs:
            self.logs.write(
                "INFO",
                "업데이트",
                "최신 설치마법사를 실행했습니다.",
            )
        return True
