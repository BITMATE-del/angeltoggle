import json
import os
import re
import shutil
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
    return tuple(int(x) for x in (nums + ["0","0","0"])[:3])

class UpdateService:
    def __init__(self, logs=None):
        self.logs = logs

    def check_latest(self):
        req = urllib.request.Request(
            RELEASE_API,
            headers={
                "User-Agent": "AngelToggle-Updater",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=12) as response:
            data = json.loads(response.read().decode("utf-8"))

        latest = (data.get("tag_name") or "").lstrip("v")
        asset = None
        asset_size = 0
        for item in data.get("assets", []):
            if item.get("name") == "AngelToggle-Windows.zip":
                asset = item.get("browser_download_url")
                asset_size = int(item.get("size") or 0)
                break

        return {
            "current": VERSION,
            "latest": latest,
            "available": bool(latest and asset and _version_tuple(latest) > _version_tuple(VERSION)),
            "download_url": asset,
            "asset_size": asset_size,
        }

    def download_and_replace(self, download_url):
        if not getattr(sys, "frozen", False):
            raise RuntimeError("개발 실행 상태에서는 자동 교체를 하지 않습니다.")

        current_exe = Path(sys.executable).resolve()
        install_dir = current_exe.parent
        local = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "AngelToggle"
        update_dir = local / "update"
        update_dir.mkdir(parents=True, exist_ok=True)

        zip_path = update_dir / "AngelToggle-Windows.zip"
        stage_dir = update_dir / "stage"
        log_file = update_dir / "update.log"
        script = update_dir / "apply_update.ps1"

        if zip_path.exists():
            zip_path.unlink()
        if stage_dir.exists():
            shutil.rmtree(stage_dir, ignore_errors=True)

        if self.logs:
            self.logs.write("INFO", "업데이트", "새 버전 ZIP 다운로드 시작")

        req = urllib.request.Request(
            download_url,
            headers={
                "User-Agent": "AngelToggle-Updater",
                "Accept": "application/octet-stream",
            },
        )
        downloaded = 0
        with urllib.request.urlopen(req, timeout=120) as response, open(zip_path, "wb") as f:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)

        if not zip_path.exists() or zip_path.stat().st_size < 5 * 1024 * 1024:
            raise RuntimeError("업데이트 ZIP 다운로드가 정상적으로 완료되지 않았습니다.")

        pid = os.getpid()
        target_exe = install_dir / "AngelToggle.exe"

        ps = f"""$ErrorActionPreference = 'Stop'
$PidToWait = {pid}
$Zip = '{str(zip_path).replace("'", "''")}'
$Stage = '{str(stage_dir).replace("'", "''")}'
$Install = '{str(install_dir).replace("'", "''")}'
$TargetExe = '{str(target_exe).replace("'", "''")}'
$Log = '{str(log_file).replace("'", "''")}'

function Log([string]$m) {{
  $t=Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
  Add-Content -LiteralPath $Log -Value "[$t] $m" -Encoding UTF8
}}

Log '업데이트 시작'
for($i=0;$i -lt 60;$i++){{
  if(-not (Get-Process -Id $PidToWait -ErrorAction SilentlyContinue)){{break}}
  Start-Sleep -Milliseconds 500
}}

if(Get-Process -Id $PidToWait -ErrorAction SilentlyContinue){{
  Log '기존 프로세스 강제 종료'
  Stop-Process -Id $PidToWait -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 1
}}

Remove-Item -LiteralPath $Stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $Stage | Out-Null
Expand-Archive -LiteralPath $Zip -DestinationPath $Stage -Force

$Source = Join-Path $Stage 'AngelToggle'
if(-not (Test-Path $Source)){{ throw '업데이트 압축 안에 AngelToggle 폴더가 없습니다.' }}

Get-ChildItem -LiteralPath $Install -Force | Where-Object {{
  $_.Name -ne 'update'
}} | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Copy-Item -Path (Join-Path $Source '*') -Destination $Install -Recurse -Force
if(-not (Test-Path $TargetExe)){{ throw '업데이트 후 AngelToggle.exe를 찾을 수 없습니다.' }}

Log '파일 교체 성공'
Start-Process -FilePath $TargetExe -WorkingDirectory $Install
Log '새 버전 재실행'
"""

        script.write_text(ps, encoding="utf-8-sig")

        creationflags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            creationflags=creationflags,
            close_fds=True,
        )

        if self.logs:
            self.logs.write(
                "INFO",
                "업데이트",
                f"업데이트 설치 준비 완료 / {downloaded // (1024*1024)}MB 다운로드",
            )
        return True
