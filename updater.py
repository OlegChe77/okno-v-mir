"""Самообновление «Окна в мир»: сверка версии с последним релизом на GitHub, загрузка ZIP,
замена файлов программы и перезапуск. Только стандартная библиотека.

Адрес ZIP строится по номеру тега (v1.2 → releases/download/v1.2/okno-v-mir.zip), а не берётся из ответа API:
так скачать можно только файл релиза нашего репозитория.
"""
import io
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, "cache")
REPO = "OlegChe77/okno-v-mir"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
ZIP_URL = f"https://github.com/{REPO}/releases/download/{{tag}}/okno-v-mir.zip"
UA = "okno-v-mir-updater"
TAG_RE = re.compile(r"^v?(\d{1,4}(?:\.\d{1,4}){0,3})$")
# что при обновлении не трогаем (первый компонент пути внутри папки программы)
KEEP = {"cache", "python", "python.tmp", "keys.json", "extra_sources.json", ".git", "__pycache__"}
MAX_ZIP = 100 * 1024 * 1024
MAX_UNPACKED = 300 * 1024 * 1024
RESTART_FLAG = os.path.join(CACHE, "update_restart")  # «после обновления не открывать новую вкладку»
RECHECK = 12 * 3600

_ctx = ssl.create_default_context()
_lock = threading.Lock()
_state = {"checking": False, "latest": None, "error": None, "applying": False}


def _log(*a):
    try:
        print(time.strftime("%H:%M:%S"), "[обновление]", *a, flush=True)
    except Exception:
        pass


def _ver(s):
    """'v1.2.3' → (1, 2, 3); None, если это не номер версии."""
    m = TAG_RE.match(str(s or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def current():
    try:
        with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as f:
            v = f.read().strip()
        return v if _ver(v) else "0"
    except OSError:
        return "0"


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout, context=_ctx)


def check():
    """Спрашивает GitHub про последний релиз. Результат — в состоянии (см. status)."""
    with _lock:
        if _state["checking"]:
            return
        _state["checking"] = True
    try:
        with _get(API_URL) as r:
            tag = str(json.loads(r.read(1_000_000).decode("utf-8")).get("tag_name", ""))
        if not _ver(tag):
            raise ValueError("непонятный номер версии: " + tag[:40])
        _state.update(latest=tag.lstrip("v"), tag=tag, error=None)
        _log("текущая", current(), "последняя", tag)
    except Exception as e:  # нет сети, лимит GitHub и т. п. — не страшно, проверим позже
        _state["error"] = str(e)[:200]
        _log("не удалось проверить:", e)
    finally:
        _state["checking"] = False


def status():
    cur, latest = current(), _state.get("latest")
    avail = bool(latest and (_ver(latest) or ()) > (_ver(cur) or ()))
    return {"current": cur, "latest": latest if avail else None, "available": avail,
            "checking": _state["checking"], "applying": _state["applying"]}


def start_background():
    """Проверка при запуске и дальше раз в 12 часов — в фоне, на работу программы не влияет."""
    def loop():
        while True:
            check()
            time.sleep(RECHECK)
    threading.Thread(target=loop, daemon=True, name="updater").start()


# ---------------------------------------------------------------- установка

def _download(tag):
    buf, total = io.BytesIO(), 0
    with _get(ZIP_URL.format(tag=tag), timeout=60) as r:
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_ZIP:
                raise ValueError("архив слишком большой")
            buf.write(chunk)
    return buf


def _plan(zf):
    """[(имя в архиве, относительный путь в папке программы)] без пропускаемого и без опасных путей."""
    names = [i for i in zf.infolist() if not i.is_dir()]
    if sum(i.file_size for i in names) > MAX_UNPACKED:
        raise ValueError("архив распаковывается в слишком большой объём")
    firsts = {i.filename.replace("\\", "/").split("/", 1)[0] for i in names}
    # в архиве релиза всё лежит в одной папке okno-v-mir/ — её снимаем
    strip = len(firsts) == 1 and all("/" in i.filename for i in names)
    out = []
    for i in names:
        rel = i.filename.replace("\\", "/")
        if strip:
            rel = rel.split("/", 1)[1]
        parts = [p for p in rel.split("/") if p]
        if (not parts or rel.startswith("/") or any(p in ("..", ".") or ":" in p for p in parts)):
            raise ValueError("опасный путь в архиве: " + i.filename[:80])
        if parts[0] in KEEP or rel.endswith(".pyc"):
            continue
        out.append((i, os.path.join(*parts)))
    have = {rel for _, rel in out}
    for need in ("server.py", "launcher.py", "index.html", "VERSION"):
        if need not in have:
            raise ValueError("в архиве нет " + need)
    return out


def install(tag):
    """Скачивает ZIP релиза и заменяет файлы. При сбое возвращает старые. Бросает исключение при ошибке."""
    zf = zipfile.ZipFile(_download(tag))
    plan = _plan(zf)
    stage = os.path.join(CACHE, "update_new")
    backup = os.path.join(CACHE, "update_old")
    for d in (stage, backup):
        shutil.rmtree(d, ignore_errors=True)
    # сначала всё распаковываем в сторону: битый архив не тронет программу
    for info, rel in plan:
        dst = os.path.join(stage, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with zf.open(info) as src, open(dst, "wb") as f:
            shutil.copyfileobj(src, f)
    done = []
    try:
        for _, rel in plan:
            dst, new = os.path.join(ROOT, rel), os.path.join(stage, rel)
            if os.path.exists(dst):
                bk = os.path.join(backup, rel)
                os.makedirs(os.path.dirname(bk), exist_ok=True)
                shutil.copy2(dst, bk)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            tmp = dst + ".upd"
            shutil.copy2(new, tmp)
            os.replace(tmp, dst)
            done.append(rel)
    except Exception:
        for rel in done:  # откат
            bk, dst = os.path.join(backup, rel), os.path.join(ROOT, rel)
            try:
                if os.path.exists(bk):
                    shutil.copy2(bk, dst)
                else:
                    os.remove(dst)
            except OSError:
                pass
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    shutil.rmtree(backup, ignore_errors=True)
    _log("установлена версия", tag, "файлов:", len(done))


def apply():
    """Проверяет релиз заново и устанавливает. Возвращает (версия, None) или (None, текст ошибки)."""
    with _lock:
        if _state["applying"]:
            return None, "обновление уже идёт"
        _state["applying"] = True
    try:
        _state["latest"] = None
        check()
        st = status()
        if not st["available"]:
            return None, _state["error"] or "новой версии нет"
        install(_state["tag"])
        return st["latest"], None
    except Exception as e:
        _log("ошибка установки:", e)
        return None, "не удалось обновить: " + str(e)[:200]
    finally:
        _state["applying"] = False


# ---------------------------------------------------------------- перезапуск

def restart_flag_fresh():
    """Был перезапуск после обновления (метка свежая)? Метка снимается. Тогда новую вкладку не открываем:
    открытая страница сама перезагрузится."""
    try:
        fresh = time.time() - os.path.getmtime(RESTART_FLAG) < 120
        os.remove(RESTART_FLAG)
        return fresh
    except OSError:
        return False


def spawn_relaunch(cmd, args=None, hidden=True):
    """Запускает помощника: он дожидается выхода этого процесса и запускает программу заново
    (cmd — start.bat или python, args — аргумент). Сам процесс после этого должен завершиться."""
    os.makedirs(CACHE, exist_ok=True)
    with open(RESTART_FLAG, "w") as f:
        f.write(str(time.time()))
    if sys.platform != "win32":
        return False
    env = dict(os.environ, R_PID=str(os.getpid()), R_EXE=cmd, R_ARGS=args or "", R_DIR=ROOT,
               R_STYLE="Hidden" if hidden else "Normal")
    ps = ("Wait-Process -Id ([int]$env:R_PID) -ErrorAction SilentlyContinue; Start-Sleep -Milliseconds 800; "
          "$a=@{FilePath=$env:R_EXE;WorkingDirectory=$env:R_DIR;WindowStyle=$env:R_STYLE}; "
          "if($env:R_ARGS){$a.ArgumentList=$env:R_ARGS}; Start-Process @a")
    subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], env=env,
                     creationflags=0x08000000 | 0x00000200,  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    return True
