"""
012号 FF11アシスタントくん — デスクトップキャラクターランチャー
透過・フレームレス・常時最前面の小窓でみっぴを表示する。
透明部分はクリックスルー、キャラ・パネル部分は通常クリック可能。
"""
import sys, os, time, subprocess, ctypes, threading, json
import ctypes.wintypes as wt
from pathlib import Path

try:
    import webview
except ImportError:
    print("pywebview が必要です: pip install pywebview")
    sys.exit(1)

WIN_W  = 560
WIN_H  = 520
SERVER = Path(__file__).parent / "012_server.py"

# ── Win32 クリックスルー制御 ───────────────────────────────────────────────────
GWL_EXSTYLE       = -20
WS_EX_LAYERED     = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WH_MOUSE_LL       = 14

# 64-bit Windows 対応: LRESULT / LPARAM / WPARAM は pointer サイズ
_IS64 = ctypes.sizeof(ctypes.c_void_p) == 8
_LRESULT  = ctypes.c_longlong  if _IS64 else ctypes.c_long
_LPARAM   = ctypes.c_longlong  if _IS64 else ctypes.c_long
_WPARAM   = ctypes.c_ulonglong if _IS64 else ctypes.c_ulong
_ULONG_PTR = ctypes.c_uint64  if _IS64 else ctypes.c_uint32

_hwnd           = None
_clickthrough   = False
_rect_lock      = threading.Lock()
_interactive_rects: list = []   # [{l,t,r,b}, ...]
_hook_handle    = None

class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt",          _POINT),
        ("mouseData",   wt.DWORD),
        ("flags",       wt.DWORD),
        ("time",        wt.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]

# Win32 API に正しい型を設定
_u32 = ctypes.windll.user32
if _IS64:
    _u32.GetWindowLongPtrW.restype  = _LRESULT
    _u32.SetWindowLongPtrW.restype  = _LRESULT
    _GetWndLong = _u32.GetWindowLongPtrW
    _SetWndLong = _u32.SetWindowLongPtrW
else:
    _GetWndLong = _u32.GetWindowLongW
    _SetWndLong = _u32.SetWindowLongW
_u32.CallNextHookEx.restype  = _LRESULT
_u32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, _WPARAM, _LPARAM]

def _set_transparent(enable: bool):
    if not _hwnd:
        return
    style = _GetWndLong(_hwnd, GWL_EXSTYLE)
    style = (style | WS_EX_TRANSPARENT) if enable else (style & ~WS_EX_TRANSPARENT)
    _SetWndLong(_hwnd, GWL_EXSTYLE, style)

def _mouse_hook_proc(nCode, wParam, lParam):
    global _clickthrough
    if nCode >= 0 and _hwnd:
        pt = ctypes.cast(lParam, ctypes.POINTER(_MSLLHOOKSTRUCT)).contents.pt
        x, y = pt.x, pt.y
        with _rect_lock:
            rects = list(_interactive_rects)
        if rects:
            over = any(r['l'] <= x <= r['r'] and r['t'] <= y <= r['b'] for r in rects)
            if over and _clickthrough:
                _set_transparent(False)
                _clickthrough = False
            elif not over and not _clickthrough:
                _set_transparent(True)
                _clickthrough = True
    return _u32.CallNextHookEx(_hook_handle, nCode, wParam, lParam)

_HOOKPROC = ctypes.WINFUNCTYPE(_LRESULT, ctypes.c_int, _WPARAM, _LPARAM)
_hook_cb  = _HOOKPROC(_mouse_hook_proc)

def _run_hook():
    global _hook_handle
    _hook_handle = _u32.SetWindowsHookExW(WH_MOUSE_LL, _hook_cb, None, 0)
    if not _hook_handle:
        print("[WND] フック設定失敗")
        return
    print("[WND] マウスフック開始")
    msg = wt.MSG()
    while _u32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        _u32.TranslateMessage(ctypes.byref(msg))
        _u32.DispatchMessageW(ctypes.byref(msg))
    _u32.UnhookWindowsHookEx(_hook_handle)

def _find_hwnd_and_start_hook():
    """プロセスの最大可視ウィンドウを HWND として取得し、フックを開始する"""
    global _hwnd
    time.sleep(0.3)  # ウィンドウが確実に表示されるまで待つ
    our_pid = os.getpid()
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    candidates = []

    def _enum_cb(hwnd, _):
        if not ctypes.windll.user32.IsWindowVisible(hwnd):
            return True
        pid = wt.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == our_pid:
            r = wt.RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
            area = max(0, r.right - r.left) * max(0, r.bottom - r.top)
            if area > 0:
                candidates.append((hwnd, area))
        return True

    cb = WNDENUMPROC(_enum_cb)
    ctypes.windll.user32.EnumWindows(cb, 0)

    if candidates:
        _hwnd = max(candidates, key=lambda x: x[1])[0]
        print(f"[WND] HWND=0x{_hwnd:08X}")
        # WS_EX_LAYERED が確実に設定されていることを確認
        style = ctypes.windll.user32.GetWindowLongW(_hwnd, GWL_EXSTYLE)
        style |= WS_EX_LAYERED
        ctypes.windll.user32.SetWindowLongW(_hwnd, GWL_EXSTYLE, style)
        _run_hook()  # ブロッキング（メッセージループ）
    else:
        print("[WND] ウィンドウ未検出、クリックスルー無効")

def update_rects(rects_json: str):
    """JS から呼ばれる: インタラクティブ領域(スクリーン座標)の更新"""
    with _rect_lock:
        try:
            _interactive_rects.clear()
            _interactive_rects.extend(json.loads(rects_json))
        except Exception:
            pass

# ── スクリーン右下に配置 ──────────────────────────────────────────────────────
def screen_bottom_right():
    try:
        class RECT(ctypes.Structure):
            _fields_ = [("left",ctypes.c_long),("top",ctypes.c_long),
                        ("right",ctypes.c_long),("bottom",ctypes.c_long)]
        r = RECT()
        ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(r), 0)
        return r.right - WIN_W - 16, r.bottom - WIN_H - 8
    except Exception:
        return 1200, 400

# ── サーバー起動 ────────────────────────────────────────────────────────────────
_server_proc = None

def start_server():
    global _server_proc
    _server_proc = subprocess.Popen(
        [sys.executable, str(SERVER)],
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    )
    print(f"[012] サーバー起動 PID={_server_proc.pid}")

def wait_server(url="http://127.0.0.1:8012/", timeout=15):
    import urllib.request
    for _ in range(timeout * 5):
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.2)
    return False

# ── メイン ──────────────────────────────────────────────────────────────────────
def main():
    print("[012] サーバー起動中...")
    start_server()

    if not wait_server():
        print("[012] サーバーが起動しませんでした。012_server.py を確認してください。")
        if _server_proc:
            _server_proc.terminate()
        sys.exit(1)

    print("[012] サーバー準備完了。ウィンドウを表示します。")

    x, y   = screen_bottom_right()
    window = webview.create_window(
        title       = "",
        url         = "http://127.0.0.1:8012/",
        x           = x,
        y           = y,
        width       = WIN_W,
        height      = WIN_H,
        transparent = True,
        frameless   = True,
        on_top      = True,
        min_size    = (160, 160),
    )

    win_pos  = [x, y]
    win_size = [WIN_W, WIN_H]

    def move_delta(dx, dy):
        win_pos[0] = max(0, win_pos[0] + int(dx))
        win_pos[1] = max(0, win_pos[1] + int(dy))
        window.move(win_pos[0], win_pos[1])

    def set_size(w, h):
        win_size[0] = max(160, int(w))
        win_size[1] = max(160, int(h))
        window.resize(win_size[0], win_size[1])

    def on_loaded():
        window.evaluate_js(
            "window._desktopMode = true;"
            "document.documentElement.style.background='transparent';"
            "document.body.style.background='transparent';"
        )
        threading.Thread(target=_find_hwnd_and_start_hook, daemon=True).start()

    window.events.loaded += on_loaded
    window.expose(move_delta, set_size, update_rects)

    webview.start(debug=False)

    if _server_proc:
        _server_proc.terminate()
        print("[012] サーバー停止")

if __name__ == "__main__":
    main()
