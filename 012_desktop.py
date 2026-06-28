"""
012号 FF11アシスタントくん — デスクトップキャラクターランチャー
透過・フレームレス・常時最前面。SetWindowRgn でインタラクティブ領域を管理。
透明部分はクリックスルー（領域外）、UI部分は通常クリック可能（領域内）。
"""
import sys, os, time, subprocess, ctypes, threading, json
import ctypes.wintypes as wt
from pathlib import Path

try:
    import webview
except ImportError:
    print("pywebview が必要です: pip install pywebview")
    sys.exit(1)

SERVER = Path(__file__).parent / "012_server.py"

def _screen_size():
    u = ctypes.windll.user32
    return u.GetSystemMetrics(0), u.GetSystemMetrics(1)

SCREEN_W, SCREEN_H = _screen_size()

_u32 = ctypes.windll.user32

_hwnd          = None
_rect_lock     = threading.Lock()
_interactive_rects: list = []
_prev_region_key   = None
_window_ref    = None  # pywebview Window 参照（evaluate_js ポーリング用）

_RECTS_JS = (
    "(function(){"
    "  var wx=window.screenLeft||window.screenX||0;"
    "  var wy=window.screenTop||window.screenY||0;"
    "  var ids=['container','panel','telop-bar','voice-ui'];"
    "  var out=[];"
    "  for(var i=0;i<ids.length;i++){"
    "    var el=document.getElementById(ids[i]); if(!el)continue;"
    "    var st=window.getComputedStyle(el);"
    "    if(st.display==='none'||st.visibility==='hidden')continue;"
    "    if(parseFloat(st.opacity)<0.05)continue;"
    "    var r=el.getBoundingClientRect();"
    "    if(r.width<1||r.height<1)continue;"
    "    out.push({l:Math.floor(wx+r.left),t:Math.floor(wy+r.top),"
    "              r:Math.ceil(wx+r.right),b:Math.ceil(wy+r.bottom)});"
    "  }"
    "  return JSON.stringify(out);"
    "})()"
)

def _apply_rects_from_js():
    """evaluate_js で rects を直接取得してリージョンに反映"""
    if not _window_ref or not _hwnd:
        return
    try:
        rects_json = _window_ref.evaluate_js(_RECTS_JS)
        if not rects_json:
            return
        data = json.loads(rects_json)
        with _rect_lock:
            _interactive_rects.clear()
            _interactive_rects.extend(data)
        _update_window_region()
    except Exception as e:
        pass  # ページ遷移中など一時的なエラーは無視

def _start_region_poll():
    """1秒ごとに rects を確認してリージョンを更新するポーリングスレッド"""
    while True:
        time.sleep(1.0)
        _apply_rects_from_js()

# ── ウィンドウリージョン管理 ────────────────────────────────────────────────
def _update_window_region():
    """JS から渡されたUI矩形でウィンドウリージョンを更新する。
    領域外はクリックスルー＋非表示になる。"""
    global _prev_region_key
    if not _hwnd:
        return
    with _rect_lock:
        rects = list(_interactive_rects)

    key = tuple((r['l'], r['t'], r['r'], r['b']) for r in rects)
    if key == _prev_region_key:
        return
    _prev_region_key = key

    gdi32 = ctypes.windll.gdi32
    win_rect = wt.RECT()
    _u32.GetWindowRect(_hwnd, ctypes.byref(win_rect))
    ox, oy = win_rect.left, win_rect.top

    if not rects:
        # リージョン未指定 → ウィンドウ全体を表示（起動直後のフォールバック）
        _u32.SetWindowRgn(_hwnd, None, True)
        return

    r0 = rects[0]
    combined = gdi32.CreateRectRgn(
        r0['l'] - ox, r0['t'] - oy,
        r0['r'] - ox, r0['b'] - oy
    )
    for r in rects[1:]:
        rgn = gdi32.CreateRectRgn(
            r['l'] - ox, r['t'] - oy,
            r['r'] - ox, r['b'] - oy
        )
        gdi32.CombineRgn(combined, combined, rgn, 2)  # RGN_OR
        gdi32.DeleteObject(rgn)

    _u32.SetWindowRgn(_hwnd, combined, True)
    # SetWindowRgn がリージョンの所有権を持つので DeleteObject しない
    print(f"[WND] リージョン更新: {len(rects)} rects")

def _find_hwnd_and_setup():
    """HWND を取得して DWM 透過を設定する"""
    global _hwnd
    time.sleep(0.5)
    our_pid = os.getpid()
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    candidates = []

    def _enum_cb(hwnd, _):
        if not _u32.IsWindowVisible(hwnd):
            return True
        pid = wt.DWORD()
        _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == our_pid:
            r = wt.RECT()
            _u32.GetWindowRect(hwnd, ctypes.byref(r))
            area = max(0, r.right - r.left) * max(0, r.bottom - r.top)
            if area > 0:
                candidates.append((hwnd, area))
        return True

    cb = WNDENUMPROC(_enum_cb)
    _u32.EnumWindows(cb, 0)

    if not candidates:
        print("[WND] ウィンドウ未検出")
        return

    _hwnd = max(candidates, key=lambda x: x[1])[0]
    print(f"[WND] HWND=0x{_hwnd:08X}")

    # Python 側から直接 JS に問い合わせて初回リージョンを設定
    try:
        rects_json = _window_ref.evaluate_js(_RECTS_JS)
        print(f"[WND] 初回 rects: {rects_json}")
        if rects_json:
            with _rect_lock:
                _interactive_rects.clear()
                _interactive_rects.extend(json.loads(rects_json))
    except Exception as e:
        print(f"[WND] rects 直接取得失敗: {e}")
    _update_window_region()
    # 定期ポーリング開始（ドラッグ・パネル開閉に追従）
    threading.Thread(target=_start_region_poll, daemon=True).start()

# ── JS から呼ばれるAPI ────────────────────────────────────────────────────
def update_rects(rects_json: str):
    """インタラクティブ領域(スクリーン座標)の更新 → ウィンドウリージョンに反映"""
    print(f"[RECTS] JS→Python 受信: {rects_json[:120]}")
    with _rect_lock:
        try:
            _interactive_rects.clear()
            _interactive_rects.extend(json.loads(rects_json))
        except Exception:
            return
    _update_window_region()

# ── サーバー起動 ────────────────────────────────────────────────────────────
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

# ── メイン ──────────────────────────────────────────────────────────────────
def main():
    print("[012] サーバー起動中...")
    start_server()

    if not wait_server():
        print("[012] サーバーが起動しませんでした。012_server.py を確認してください。")
        if _server_proc:
            _server_proc.terminate()
        sys.exit(1)

    print("[012] サーバー準備完了。ウィンドウを表示します。")

    window = webview.create_window(
        title       = "",
        url         = "http://127.0.0.1:8012/",
        x           = 0,
        y           = 0,
        width       = SCREEN_W,
        height      = SCREEN_H,
        transparent = True,
        frameless   = True,
        on_top      = True,
        min_size    = (160, 160),
    )

    def on_loaded():
        global _window_ref
        _window_ref = window
        window.evaluate_js("window._desktopMode = true;")
        threading.Thread(target=_find_hwnd_and_setup, daemon=True).start()

        def _debug_and_trigger():
            time.sleep(0.3)
            try:
                api_keys = window.evaluate_js(
                    "JSON.stringify(Object.keys(window.pywebview?.api || {}))"
                )
                print(f"[JS] pywebview.api keys: {api_keys}")
                fn_type = window.evaluate_js(
                    "typeof window.pywebview?.api?.update_rects"
                )
                print(f"[JS] update_rects type: {fn_type}")
            except Exception as e:
                print(f"[JS] debug check error: {e}")
            # 0.3s 後に rects を強制送信（HWND setup 完了前にキャッシュしておく）
            window.evaluate_js(
                "if(typeof updateClickthroughRects==='function') updateClickthroughRects();"
            )

        threading.Thread(target=_debug_and_trigger, daemon=True).start()

    window.events.loaded += on_loaded
    window.expose(update_rects)

    webview.start(debug=False)

    if _server_proc:
        _server_proc.terminate()
        print("[012] サーバー停止")

if __name__ == "__main__":
    main()
