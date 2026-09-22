import json
import re
import webbrowser
import urllib.request

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

    def open_installer_download(self, installer_url):
        if not installer_url:
            raise RuntimeError("최신 설치마법사 주소를 찾을 수 없습니다.")

        opened = webbrowser.open(installer_url, new=2)
        if not opened:
            raise RuntimeError("브라우저를 열 수 없습니다.")

        if self.logs:
            self.logs.write(
                "INFO",
                "업데이트",
                "최신 설치마법사 다운로드 페이지를 열었습니다.",
            )
        return True
