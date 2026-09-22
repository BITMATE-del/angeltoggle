import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

RELEASE_API = "https://api.github.com/repos/BITMATE-del/angeltoggle/releases/latest"

def version_tuple(value):
    nums = re.findall(r"\d+", value or "")
    return tuple(int(x) for x in (nums + ["0","0","0"])[:3])

def root_dir():
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    root = base / "AngelToggle"
    root.mkdir(parents=True, exist_ok=True)
    return root

def release_info():
    req = urllib.request.Request(
        RELEASE_API,
        headers={
            "User-Agent":"AngelToggle-Launcher",
            "Accept":"application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req,timeout=15) as res:
        data=json.loads(res.read().decode("utf-8"))

    version=(data.get("tag_name") or "").lstrip("v")
    zip_url=None
    for asset in data.get("assets",[]):
        if asset.get("name")=="AngelToggle-Windows.zip":
            zip_url=asset.get("browser_download_url")
            break
    if not version or not zip_url:
        raise RuntimeError("최신 엔젤토글 설치파일을 찾을 수 없습니다.")
    return version,zip_url

def installed_version(app_dir):
    try:
        return (app_dir/"version.txt").read_text(encoding="utf-8").strip()
    except Exception:
        return "0.0.0"

def download(url,target):
    req=urllib.request.Request(
        url,
        headers={"User-Agent":"AngelToggle-Launcher","Accept":"application/octet-stream"},
    )
    with urllib.request.urlopen(req,timeout=120) as res,open(target,"wb") as f:
        while True:
            chunk=res.read(1024*1024)
            if not chunk:
                break
            f.write(chunk)
    if not target.exists() or target.stat().st_size < 5*1024*1024:
        raise RuntimeError("업데이트 파일 다운로드가 정상적으로 완료되지 않았습니다.")

def install_zip(zip_path,root,version):
    app_dir=root/"app"
    stage=root/"launcher_stage"
    if stage.exists():
        shutil.rmtree(stage,ignore_errors=True)
    stage.mkdir(parents=True,exist_ok=True)

    with zipfile.ZipFile(zip_path,"r") as z:
        z.extractall(stage)

    source=stage/"AngelToggle"
    exe=source/"AngelToggle.exe"
    if not exe.exists():
        raise RuntimeError("업데이트 압축파일 내부에서 AngelToggle.exe를 찾을 수 없습니다.")

    old=root/"app_old"
    if old.exists():
        shutil.rmtree(old,ignore_errors=True)
    if app_dir.exists():
        try:
            app_dir.rename(old)
        except Exception:
            shutil.rmtree(app_dir,ignore_errors=True)

    shutil.copytree(source,app_dir,dirs_exist_ok=True)
    (app_dir/"version.txt").write_text(version,encoding="utf-8")

    shutil.rmtree(stage,ignore_errors=True)
    if old.exists():
        shutil.rmtree(old,ignore_errors=True)

    return app_dir/"AngelToggle.exe"

def main():
    root=root_dir()
    app_dir=root/"app"
    target=app_dir/"AngelToggle.exe"

    try:
        latest,url=release_info()
        current=installed_version(app_dir)
        if (not target.exists()) or version_tuple(latest)>version_tuple(current):
            update_dir=root/"update"
            update_dir.mkdir(parents=True,exist_ok=True)
            zip_path=update_dir/"AngelToggle-Windows.zip"
            if zip_path.exists():
                zip_path.unlink()
            download(url,zip_path)
            target=install_zip(zip_path,root,latest)
    except Exception:
        if not target.exists():
            raise

    subprocess.Popen([str(target)],cwd=str(target.parent))

if __name__=="__main__":
    try:
        main()
    except Exception as e:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0,
            str(e),
            "엔젤토글 업데이트 오류",
            0x10
        )
