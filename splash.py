"""Заставка «Загрузка…» на чистом ctypes (без tkinter: в портативном Python его нет).
Splash(title).run() крутит цикл сообщений в текущем потоке, пока другой поток не вызовет done.set()."""
import ctypes as C
import threading
from ctypes import wintypes as W

u32 = C.WinDLL("user32", use_last_error=True)
g32 = C.WinDLL("gdi32")
k32 = C.WinDLL("kernel32")

LRESULT = C.c_ssize_t
WNDPROC = C.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
WM_DESTROY, WM_PAINT, WM_TIMER, WM_ERASEBKGND = 0x0002, 0x000F, 0x0113, 0x0014
WS_POPUP, WS_VISIBLE = 0x80000000, 0x10000000
WS_EX_TOPMOST, WS_EX_TOOLWINDOW = 0x8, 0x80
SRCCOPY, TRANSPARENT = 0x00CC0020, 1
BG, AMBER, TROUGH, WHITE = 0x22150F, 0x47B5FF, 0x40271B, 0xFFFFFF        # COLORREF = 0x00BBGGRR
WIDTH, HEIGHT, PAD = 360, 120, 34


class WNDCLASSW(C.Structure):
    _fields_ = [("style", W.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", C.c_int), ("cbWndExtra", C.c_int),
                ("hInstance", W.HINSTANCE), ("hIcon", W.HICON), ("hCursor", W.HANDLE), ("hbrBackground", W.HBRUSH),
                ("lpszMenuName", W.LPCWSTR), ("lpszClassName", W.LPCWSTR)]


class PAINTSTRUCT(C.Structure):
    _fields_ = [("hdc", W.HDC), ("fErase", W.BOOL), ("rcPaint", W.RECT), ("fRestore", W.BOOL),
                ("fIncUpdate", W.BOOL), ("rgbReserved", C.c_byte * 32)]


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
_sig(u32.SetTimer, C.c_size_t, W.HWND, C.c_size_t, W.UINT, W.LPVOID)
_sig(u32.KillTimer, W.BOOL, W.HWND, C.c_size_t)
_sig(u32.BeginPaint, W.HDC, W.HWND, C.POINTER(PAINTSTRUCT))
_sig(u32.EndPaint, W.BOOL, W.HWND, C.POINTER(PAINTSTRUCT))
_sig(u32.InvalidateRect, W.BOOL, W.HWND, W.LPVOID, W.BOOL)
_sig(u32.FillRect, C.c_int, W.HDC, C.POINTER(W.RECT), W.HBRUSH)
_sig(u32.GetSystemMetrics, C.c_int, C.c_int)
_sig(u32.SetProcessDPIAware, W.BOOL)
_sig(u32.DrawTextW, C.c_int, W.HDC, W.LPCWSTR, C.c_int, C.POINTER(W.RECT), W.UINT)
_sig(g32.CreateSolidBrush, W.HBRUSH, W.COLORREF)
_sig(g32.DeleteObject, W.BOOL, W.HANDLE)
_sig(g32.CreateCompatibleDC, W.HDC, W.HDC)
_sig(g32.CreateCompatibleBitmap, W.HBITMAP, W.HDC, C.c_int, C.c_int)
_sig(g32.SelectObject, W.HANDLE, W.HDC, W.HANDLE)
_sig(g32.DeleteDC, W.BOOL, W.HDC)
_sig(g32.BitBlt, W.BOOL, W.HDC, C.c_int, C.c_int, C.c_int, C.c_int, W.HDC, C.c_int, C.c_int, W.DWORD)
_sig(g32.SetBkMode, C.c_int, W.HDC, C.c_int)
_sig(g32.SetTextColor, W.COLORREF, W.HDC, W.COLORREF)
_sig(g32.CreateFontW, W.HANDLE, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int, W.DWORD, W.DWORD, W.DWORD, W.DWORD,
     W.DWORD, W.DWORD, W.DWORD, W.DWORD, W.LPCWSTR)
_sig(k32.GetModuleHandleW, W.HMODULE, W.LPCWSTR)


class Splash:
    def __init__(self, title: str, text: str = "Загрузка…"):
        self.title, self.text = title, text
        self.done = threading.Event()
        self._tick = 0

    def _fill(self, dc, l, t, r, b, color):
        br = g32.CreateSolidBrush(color)
        u32.FillRect(dc, C.byref(W.RECT(l, t, r, b)), br)
        g32.DeleteObject(br)

    def _text(self, dc, s, size, weight, color, top):
        f = g32.CreateFontW(-size, 0, 0, 0, weight, 0, 0, 0, 1, 0, 0, 5, 0, "Segoe UI")   # RUSSIAN/DEFAULT charset, ClearType
        old = g32.SelectObject(dc, f)
        g32.SetTextColor(dc, color)
        u32.DrawTextW(dc, s, -1, C.byref(W.RECT(PAD, top, WIDTH - PAD, top + size * 2)), 0x20 | 0x100)   # DT_SINGLELINE | DT_NOCLIP
        g32.SelectObject(dc, old)
        g32.DeleteObject(f)

    def _paint(self, hwnd):
        ps = PAINTSTRUCT()
        hdc = u32.BeginPaint(hwnd, C.byref(ps))
        mem = g32.CreateCompatibleDC(hdc)                       # рисуем в памяти, затем одним куском — без мерцания
        bmp = g32.CreateCompatibleBitmap(hdc, WIDTH, HEIGHT)
        old = g32.SelectObject(mem, bmp)
        self._fill(mem, 0, 0, WIDTH, HEIGHT, AMBER)             # рамка 2 px
        self._fill(mem, 2, 2, WIDTH - 2, HEIGHT - 2, BG)
        g32.SetBkMode(mem, TRANSPARENT)
        self._text(mem, self.title, 22, 600, WHITE, 16)
        self._text(mem, self.text, 17, 400, AMBER, 48)
        bx, by, bw, bh = PAD, HEIGHT - 32, WIDTH - 2 * PAD, 6
        self._fill(mem, bx, by, bx + bw, by + bh, TROUGH)
        seg = bw // 3
        pos = (self._tick * 7) % (bw + seg) - seg                # бегущий отрезок слева направо
        self._fill(mem, bx + max(pos, 0), by, bx + min(pos + seg, bw), by + bh, AMBER)
        g32.BitBlt(hdc, 0, 0, WIDTH, HEIGHT, mem, 0, 0, SRCCOPY)
        g32.SelectObject(mem, old)
        g32.DeleteObject(bmp)
        g32.DeleteDC(mem)
        u32.EndPaint(hwnd, C.byref(ps))

    def run(self) -> None:
        try:
            u32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass

        def proc(hwnd, msg, wp, lp):
            if msg == WM_PAINT:
                self._paint(hwnd)
                return 0
            if msg == WM_ERASEBKGND:
                return 1
            if msg == WM_TIMER:
                if self.done.is_set():
                    u32.KillTimer(hwnd, 1)
                    u32.DestroyWindow(hwnd)
                else:
                    self._tick += 1
                    u32.InvalidateRect(hwnd, None, False)
                return 0
            if msg == WM_DESTROY:
                u32.PostQuitMessage(0)
                return 0
            return u32.DefWindowProcW(hwnd, msg, wp, lp)

        cb = WNDPROC(proc)
        hinst = k32.GetModuleHandleW(None)
        wc = WNDCLASSW(lpfnWndProc=cb, hInstance=hinst, lpszClassName="OknoSplashWindow")
        u32.RegisterClassW(C.byref(wc))
        x = (u32.GetSystemMetrics(0) - WIDTH) // 2
        y = (u32.GetSystemMetrics(1) - HEIGHT) // 2
        hwnd = u32.CreateWindowExW(WS_EX_TOPMOST | WS_EX_TOOLWINDOW, "OknoSplashWindow", self.title,
                                   WS_POPUP | WS_VISIBLE, x, y, WIDTH, HEIGHT, None, None, hinst, None)
        if not hwnd:                                            # заставка не обязательна: просто ждём запуска
            self.done.wait()
            return
        u32.SetTimer(hwnd, 1, 30, None)
        msg = W.MSG()
        while u32.GetMessageW(C.byref(msg), None, 0, 0) > 0:
            u32.TranslateMessage(C.byref(msg))
            u32.DispatchMessageW(C.byref(msg))
