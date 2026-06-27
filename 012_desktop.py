"""
012号 FF11アシスタントくん — デスクトップキャラクターランチャー
透過・フレームレス・常時最前面の小窓でみっぴを表示する。
OBS不要。pip install pywebview で動く。

起動: python 012_desktop.py
"""
import sys, os, time, subprocess, ctypes, threading
from pathlib import Path

try:
    import webview
except ImportError:
    print("pywebview が必要です: pip install pywebview")
    sys.exit(1)

WIN_W  = 560
WIN_H  = 520
SERVER = Path(__file__).parent / "012_server.py"

# ── スクリーン右下に配置 ──────────────────────────────────────────────────────
def screen_bottom_right():
    try:
        u32 = ctypes.windll.user32
        sw  = u32.GetSystemMetrics(0)
        sh  = u32.GetSystemMetrics(1)
        # 作業領域（タスクバーを除いた高さ）を取得
        class RECT(ctypes.Structure):
            _fields_ = [("left",ctypes.c_long),("top",ctypes.c_long),
                        ("right",ctypes.c_long),("bottom",ctypes.c_long)]
        r = RECT()
        ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(r), 0)
        return r.right - WIN_W - 16, r.bottom - WIN_H - 8
    except Exception:
        return 1200, 400

# ── JS ↔ Python ブリッジ（ドラッグ移動に使用） ─────────────────────────────────
class API:
    def __init__(self, win, x, y):
        self._win = win
        self._x   = x
        self._y   = y

    def move_delta(self, dx, dy):
        """JS からドラッグ差分を受け取って窓を移動"""
        self._x = max(0, self._x + int(dx))
        self._y = max(0, self._y + int(dy))
        self._win.move(self._x, self._y)

    def resize(self, w, h):
        """JS から窓サイズ変更（パネル開閉に応じて）"""
        self._win.resize(int(w), int(h))

    def minimize(self):
        self._win.hide()

    def restore(self):
        self._win.show()

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
        title            = "",
        url              = "http://127.0.0.1:8012/",
        x                = x,
        y                = y,
        width            = WIN_W,
        height           = WIN_H,
        transparent      = True,
        frameless        = True,
        on_top           = True,
        min_size         = (160, 160),
        background_color = "#00000000",
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

    window.events.loaded += on_loaded
    window.expose(move_delta, set_size)

    webview.start(debug=False)

    # ウィンドウが閉じたらサーバーも停止
    if _server_proc:
        _server_proc.terminate()
        print("[012] サーバー停止")

if __name__ == "__main__":
    main()
