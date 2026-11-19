"""Значок в трее (у часов) на чистом ctypes — без сторонних библиотек (pystray — LGPL, нам нельзя).
run(icon, tip, items, on_click, on_quit(force)) крутит цикл сообщений в текущем потоке, пока не выберут «Выход».
items: [(подпись, функция), None — разделитель, (подпись, None) — выход]."""
import ctypes as C
import logging
from ctypes import wintypes as W
from pathlib import Path

u32 = C.WinDLL("user32", use_last_error=True)
sh32 = C.WinDLL("shell32")
k32 = C.WinDLL("kernel32")

LRESULT = C.c_ssize_t
WNDPROC = C.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)

WM_DESTROY, WM_CLOSE, WM_COMMAND = 0x0002, 0x0010, 0x0111
WM_QUERYENDSESSION, WM_ENDSESSION = 0x0011, 0x0016
WM_LBUTTONUP, WM_RBUTTONUP, WM_CONTEXTMENU = 0x0202, 0x0205, 0x007B
WM_TRAY = 0x8000 + 1                      # WM_APP + 1
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 1, 2, 4
MF_STRING, MF_SEPARATOR = 0, 0x800
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x2, 0x100, 0x80
IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x10, 0x40
IDI_APPLICATION = 32512


class WNDCLASSW(C.Structure):
    _fields_ = [("style", W.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", C.c_int), ("cbWndExtra", C.c_int),
                ("hInstance", W.HINSTANCE), ("hIcon", W.HICON), ("hCursor", W.HANDLE), ("hbrBackground", W.HBRUSH),
                ("lpszMenuName", W.LPCWSTR), ("lpszClassName", W.LPCWSTR)]


class GUID(C.Structure):
    _fields_ = [("a", C.c_ulong), ("b", C.c_ushort), ("c", C.c_ushort), ("d", C.c_ubyte * 8)]


class NOTIFYICONDATAW(C.Structure):
    _fields_ = [("cbSize", W.DWORD), ("hWnd", W.HWND), ("uID", W.UINT), ("uFlags", W.UINT),
                ("uCallbackMessage", W.UINT), ("hIcon", W.HICON), ("szTip", W.WCHAR * 128),
                ("dwState", W.DWORD), ("dwStateMask", W.DWORD), ("szInfo", W.WCHAR * 256),
                ("uVersion", W.UINT), ("szInfoTitle", W.WCHAR * 64), ("dwInfoFlags", W.DWORD),
                ("guidItem", GUID), ("hBalloonIcon", W.HICON)]


def _sig(f, res, *args):
    f.restype, f.argtypes = res, list(args)


_sig(u32.DefWindowProcW, LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
_sig(u32.RegisterClassW, W.ATOM, C.POINTER(WNDCLASSW))
_sig(u32.CreateWindowExW, W.HWND, W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, C.c_int, C.c_int, C.c_int, C.c_int,
     W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID)
_sig(u32.DestroyWindow, W.BOOL, W.HWND)
_sig(u32.GetMessageW, W.BOOL, C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT)
_sig(u32.TranslateMessage, W.BOOL, C.POINTER(W.MSG))
_sig(u32.DispatchMessageW, LRESULT, C.POINTER(W.MSG))
_sig(u32.PostQuitMessage, None, C.c_int)
_sig(u32.CreatePopupMenu, W.HMENU)
_sig(u32.AppendMenuW, W.BOOL, W.HMENU, W.UINT, C.c_size_t, W.LPCWSTR)
_sig(u32.SetMenuDefaultItem, W.BOOL, W.HMENU, W.UINT, W.UINT)
_sig(u32.TrackPopupMenu, C.c_int, W.HMENU, W.UINT, C.c_int, C.c_int, C.c_int, W.HWND, W.LPVOID)
_sig(u32.DestroyMenu, W.BOOL, W.HMENU)
_sig(u32.GetCursorPos, W.BOOL, C.POINTER(W.POINT))
_sig(u32.SetForegroundWindow, W.BOOL, W.HWND)
_sig(u32.PostMessageW, W.BOOL, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
_sig(u32.RegisterWindowMessageW, W.UINT, W.LPCWSTR)
_sig(u32.LoadImageW, W.HANDLE, W.HINSTANCE, W.LPCWSTR, W.UINT, C.c_int, C.c_int, W.UINT)
_sig(u32.LoadIconW, W.HICON, W.HINSTANCE, W.LPVOID)
_sig(k32.GetModuleHandleW, W.HMODULE, W.LPCWSTR)
_sig(sh32.Shell_NotifyIconW, W.BOOL, W.DWORD, C.POINTER(NOTIFYICONDATAW))

_keep = []   # ссылки на колбэк и структуры, чтобы их не собрал сборщик мусора


def run(icon: Path, tip: str, items, on_click=None, on_quit=None) -> None:
    hinst = k32.GetModuleHandleW(None)
    # размер «малого» значка (16 px при 100%, 20/24/32 при масштабе): иначе грузится 32 и ужимается — мыло
    cx, cy = u32.GetSystemMetrics(49) or 16, u32.GetSystemMetrics(50) or 16      # SM_CXSMICON, SM_CYSMICON
    hicon = u32.LoadImageW(None, str(icon), IMAGE_ICON, cx, cy, LR_LOADFROMFILE) if icon.exists() else None
    if not hicon:
        hicon = u32.LoadIconW(None, C.c_void_p(IDI_APPLICATION))
    taskbar_created = u32.RegisterWindowMessageW("TaskbarCreated")   # проводник перезапустился — вернуть значок
    nid = NOTIFYICONDATAW()
    state = {"quitting": False}

    def add_icon(hwnd):
        nid.cbSize = C.sizeof(NOTIFYICONDATAW)
        nid.hWnd, nid.uID = hwnd, 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage, nid.hIcon, nid.szTip = WM_TRAY, hicon, tip[:127]
        if not sh32.Shell_NotifyIconW(NIM_ADD, C.byref(nid)):
            logging.warning("значок в трее не добавился")

    def menu(hwnd):
        m = u32.CreatePopupMenu()
        for i, it in enumerate(items):
            if it is None:
                u32.AppendMenuW(m, MF_SEPARATOR, 0, None)
            else:
                u32.AppendMenuW(m, MF_STRING, i + 1, it[0])
        u32.SetMenuDefaultItem(m, 1, 0)        # первый пункт жирным: он же по щелчку
        pt = W.POINT()
        u32.GetCursorPos(C.byref(pt))
        u32.SetForegroundWindow(hwnd)          # иначе меню не закрывается щелчком мимо
        cmd = u32.TrackPopupMenu(m, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY, pt.x, pt.y, 0, hwnd, None)
        u32.DestroyMenu(m)
        u32.PostMessageW(hwnd, 0, 0, 0)
        if cmd:
            choose(hwnd, items[cmd - 1])

    def choose(hwnd, it):
        _, fn = it
        try:
            if fn is None:
                if state["quitting"]:
                    return
                if on_quit is None or on_quit(False):
                    state["quitting"] = True
                    u32.DestroyWindow(hwnd)
            else:
                fn()
        except Exception:  # noqa: BLE001
            logging.exception("пункт меню трея")

    def proc(hwnd, msg, wp, lp):
        if msg == WM_TRAY:
            ev = lp & 0xFFFF
            if ev == WM_LBUTTONUP and on_click:
                try:
                    on_click()
                except Exception:  # noqa: BLE001
                    logging.exception("щелчок по значку")
            elif ev in (WM_RBUTTONUP, WM_CONTEXTMENU):
                menu(hwnd)
            return 0
        if msg == WM_COMMAND and 1 <= (wp & 0xFFFF) <= len(items) and items[(wp & 0xFFFF) - 1]:
            choose(hwnd, items[(wp & 0xFFFF) - 1])     # пункт меню по номеру (так же его шлёт проверка)
            return 0
        if msg == taskbar_created:
            add_icon(hwnd)
            return 0
        if msg == WM_QUERYENDSESSION:
            return 1
        if msg == WM_ENDSESSION:
            if wp and on_quit and not state["quitting"]:
                state["quitting"] = True
                try:
                    on_quit(True)
                except Exception:  # noqa: BLE001
                    logging.exception("выход при завершении сеанса")
            return 0
        if msg == WM_CLOSE:
            return 0                            # закрывается только через «Выход»
        if msg == WM_DESTROY:
            sh32.Shell_NotifyIconW(NIM_DELETE, C.byref(nid))
            u32.PostQuitMessage(0)
            return 0
        return u32.DefWindowProcW(hwnd, msg, wp, lp)

    cb = WNDPROC(proc)
    wc = WNDCLASSW(lpfnWndProc=cb, hInstance=hinst, lpszClassName="OknoTrayWindow")
    _keep.extend([cb, wc, nid])
    u32.RegisterClassW(C.byref(wc))
    hwnd = u32.CreateWindowExW(0, "OknoTrayWindow", "Окно в мир", 0, 0, 0, 0, 0, None, None, hinst, None)
    add_icon(hwnd)
    msg = W.MSG()
    while u32.GetMessageW(C.byref(msg), None, 0, 0) > 0:
        u32.TranslateMessage(C.byref(msg))
        u32.DispatchMessageW(C.byref(msg))
