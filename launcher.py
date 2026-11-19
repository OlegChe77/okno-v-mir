"""Запуск без консоли (как в «Нарезке рилсов»): заставка «Загрузка…» → сервер в фоне → браузер → значок в трее.
Запускается из start.bat: pythonw launcher.py. Ошибки — окном (MessageBox), лог — cache/app.log."""
import base64
import ctypes
import hashlib
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
from splash import Splash  # noqa: E402
LOG = ROOT / "cache" / "app.log"
ICON = ROOT / "app.ico"
TITLE = "Окно в мир"
PORT = int(os.environ.get("PORT", "8765"))
HOST = "127.0.0.1"
URL = f"http://{HOST}:{PORT}/"


def setup_log() -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        if LOG.exists() and LOG.stat().st_size > 2_000_000:
            os.replace(LOG, LOG.with_suffix(".log.1"))
    except OSError:
        pass
    f = open(LOG, "a", encoding="utf-8", buffering=1, errors="replace")  # noqa: SIM115
    sys.stdout = sys.stderr = f          # у pythonw их нет: print/traceback иначе падают
    logging.basicConfig(stream=f, level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    logging.info("---- запуск (pid %s) ----", os.getpid())


def message(text: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, text, TITLE, 0x10 | 0x10000 | 0x40000)   # ICONERROR | SETFOREGROUND | TOPMOST


def ours_running() -> bool:
    """На порту уже наша программа? (/api/me отдаёт JSON с ключом user)"""
    try:
        with urllib.request.urlopen(URL + "api/me", timeout=1.5) as r:  # noqa: S310 (только 127.0.0.1)
            return "user" in json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return False


def bind_exclusive() -> socket.socket:
    """Порт только нам: на Windows SO_REUSEADDR (его ставит http.server) позволяет второму серверу
    «молча» сесть на занятый порт — поэтому сокет открываем сами с SO_EXCLUSIVEADDRUSE."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    s.bind((HOST, PORT))
    s.listen(128)
    return s


class App:
    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.srv = None
        self.error = None
        self._opened = 0.0

    def open_browser(self) -> None:
        if time.time() - self._opened < 1.0:   # двойной щелчок по значку = два щелчка
            return
        self._opened = time.time()
        webbrowser.open(URL)

    def boot(self, splash) -> None:
        """В фоне, пока крутится заставка."""
        try:
            import server
            from http.server import ThreadingHTTPServer
            srv = ThreadingHTTPServer((HOST, PORT), server.Handler, bind_and_activate=False)
            srv.socket.close()
            srv.socket = self.sock                    # наш сокет с SO_EXCLUSIVEADDRUSE
            srv.server_address = self.sock.getsockname()
            srv.daemon_threads = True
            self.srv = srv
            threading.Thread(target=server.startup, daemon=True).start()
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            for _ in range(100):
                if ours_running():
                    break
                time.sleep(0.1)
            else:
                self.error = "Сервер программы не ответил."
                return
            logging.info("сервер работает: %s", URL)
            import updater
            server.RESTART["fn"] = self.restart_after_update
            if updater.restart_flag_fresh():
                logging.info("перезапуск после обновления: страница обновится сама")
            else:
                self.open_browser()
            time.sleep(0.8)
        except Exception:  # noqa: BLE001
            logging.exception("запуск")
            self.error = "Программа не запустилась:\n\n" + traceback.format_exc(limit=3)[-600:]
        finally:
            splash.done.set()

    def restart_after_update(self) -> None:
        """Файлы уже заменены: помощник дождётся нашего выхода и снова запустит start.bat."""
        import tray
        import updater
        logging.info("перезапуск после обновления")
        if not updater.spawn_relaunch(str(ROOT / "start.bat")):
            return
        tray.request_exit()               # значок уходит из трея, цикл main() завершается → os._exit(0)
        time.sleep(5)
        os._exit(0)                       # на случай, если меню трея не отозвалось

    def quit(self, force: bool = False) -> bool:
        logging.info("выход")
        if self.srv:
            threading.Thread(target=self.srv.shutdown, daemon=True).start()
        return True


def make_shortcut() -> None:
    """Ярлык «Окно в мир» на рабочем столе (pythonw launcher.py, без консоли): создаётся один раз, удалил
    пользователь — не возвращаем. Если ярлык есть — только обновляем значок (новый рисунок = новый путь
    cache/icons/app_<хэш>.ico, иначе кэш значков Windows покажет старый). Состояние — cache/shortcuts.json."""
    try:
        h = hashlib.sha1(ICON.read_bytes()).hexdigest()[:10]
        state_f = ROOT / "cache" / "shortcuts.json"
        try:
            state = json.loads(state_f.read_text("utf-8"))
        except (OSError, ValueError):
            state = {}
        if state.get("placed") and state.get("icon") == h:
            return
        ico = ROOT / "cache" / "icons" / f"app_{h}.ico"
        ico.parent.mkdir(parents=True, exist_ok=True)
        if not ico.exists():
            shutil.copyfile(ICON, ico)
        env = dict(os.environ, R_NAME=f"{TITLE}.lnk", R_FIRST="" if state.get("placed") else "1",
                   R_EXE=str(Path(sys.executable).with_name("pythonw.exe")), R_ARG=f'"{ROOT / "launcher.py"}"',
                   R_DIR=str(ROOT), R_ICO=f"{ico},0", R_DESC=TITLE)
        ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;$w=New-Object -ComObject WScript.Shell;"
              "$p=Join-Path ([Environment]::GetFolderPath('Desktop')) $env:R_NAME;"
              "if($env:R_FIRST -or (Test-Path -LiteralPath $p)){$s=$w.CreateShortcut($p);$s.TargetPath=$env:R_EXE;"
              "$s.Arguments=$env:R_ARG;$s.WorkingDirectory=$env:R_DIR;$s.IconLocation=$env:R_ICO;"
              "$s.Description=$env:R_DESC;$s.Save();"
              "[Console]::Out.WriteLine([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($p)))}")
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], env=env,
                           capture_output=True, timeout=30, creationflags=0x08000000,   # CREATE_NO_WINDOW
                           encoding="utf-8", errors="replace")
        if r.returncode:
            logging.warning("ярлык: %s", (r.stderr or "")[-500:])
            return
        made = [base64.b64decode(x.strip()).decode("utf-8") for x in r.stdout.splitlines() if x.strip()]
        try:                                   # проводнику: значок файла поменялся
            sh = ctypes.windll.shell32
            for x in made:
                sh.SHChangeNotify(0x2000, 0x0005, ctypes.c_wchar_p(x), None)   # SHCNE_UPDATEITEM, SHCNF_PATHW
            sh.SHChangeNotify(0x08000000, 0, None, None)                         # SHCNE_ASSOCCHANGED
        except Exception:  # noqa: BLE001
            pass
        for old in ico.parent.glob("app_*.ico"):
            if old != ico:
                try:
                    old.unlink()
                except OSError:
                    pass
        state_f.write_text(json.dumps({"icon": h, "placed": True, "links": made}, ensure_ascii=False), "utf-8")
        logging.info("ярлык (значок %s): %s", h, made)
    except Exception:  # noqa: BLE001
        logging.exception("ярлык")


def main() -> None:
    setup_log()
    threading.Thread(target=make_shortcut, daemon=True).start()
    try:
        sock = bind_exclusive()
    except OSError:
        if ours_running():                # уже запущена — просто открыть страницу
            logging.info("уже запущена — открываю браузер")
            webbrowser.open(URL)
            return
        message(f"Порт {PORT} занят другой программой.\n\nВозможно, это старая копия «Окна в мир», запущенная из консоли, "
                "— закройте её. Или задайте другой порт: переменная PORT.")
        return
    a = App(sock)
    splash = Splash(TITLE)
    threading.Thread(target=a.boot, args=(splash,), daemon=True).start()
    splash.run()
    if a.error:
        logging.error(a.error)
        message(a.error + "\n\nЛог: cache\\app.log")
        os._exit(1)
    import tray
    tray.run(ICON, f"{TITLE} — работает", [
        ("Открыть в браузере", a.open_browser),
        None,
        ("Выход", None),
    ], on_click=a.open_browser, on_quit=a.quit)
    logging.info("завершено")
    os._exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        try:
            logging.exception("сбой лаунчера")
        finally:
            message("Программа не запустилась:\n\n" + traceback.format_exc(limit=3)[-600:] + "\n\nЛог: cache\\app.log")
        os._exit(1)
