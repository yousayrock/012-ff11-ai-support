#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
012号 FF11アシスタントくん — サーバー
mippi_main.py (003号) の STT/TTS/WS パターン +
011号 RoadTalk の VAD閾値・自動送信・UI収納設計を流用。

起動: python 012_server.py
OBSブラウザソース: http://localhost:8012/
WebSocket: ws://127.0.0.1:9012
"""

import sys, io
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)

# ── ログをファイルにも書き出す（コンソール + ファイル同時出力）──────────────
import pathlib as _pl, threading as _threading, traceback as _tb
from datetime import datetime as _dt

class _Tee:
    """stdout/stderr をコンソールとファイルに同時出力する。"""
    def __init__(self, stream, file_obj):
        self._stream = stream
        self._file   = file_obj
        self._lock   = _threading.Lock()

    def write(self, s):
        with self._lock:
            self._stream.write(s)
            self._file.write(s)
        return len(s)

    def flush(self):
        self._stream.flush()
        self._file.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)

_LOG_PATH = _pl.Path(__file__).parent / "012_log.txt"
_log_file = open(_LOG_PATH, "w", encoding="utf-8", buffering=1)

def _ts():
    return _dt.now().strftime("%H:%M:%S")

# ── 元の print に時刻プレフィックスを付ける ──
import builtins as _builtins
_orig_print = _builtins.print
def _print_ts(*args, **kwargs):
    # 空行・pygame のバナーはそのまま
    if args and str(args[0]).strip():
        _orig_print(f"[{_ts()}]", *args, **kwargs)
    else:
        _orig_print(*args, **kwargs)
_builtins.print = _print_ts

sys.stdout = _Tee(sys.stdout, _log_file)
sys.stderr = _Tee(sys.stderr, _log_file)

# ── 未ハンドル例外を全部ログに出す ────────────────────────────────────────

def _exc_hook(exc_type, exc_value, exc_tb):
    msg = "".join(_tb.format_exception(exc_type, exc_value, exc_tb))
    print(f"[{_ts()}][FATAL] 未ハンドル例外:\n{msg}")

sys.excepthook = _exc_hook

def _thread_exc_hook(args):
    msg = "".join(_tb.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
    print(f"[{_ts()}][FATAL] スレッド例外 ({args.thread.name if args.thread else '?'}):\n{msg}")

_threading.excepthook = _thread_exc_hook

import os
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), override=True)
except Exception:
    pass

# CUDA DLL パス追加（mippi_main.py 流用）
try:
    import site
    for _sp in site.getsitepackages():
        for _pkg in ("cublas", "cuda_nvrtc", "cuda_runtime", "cudnn"):
            _bin = os.path.join(_sp, "nvidia", _pkg, "bin")
            if os.path.isdir(_bin):
                os.environ["PATH"] = _bin + os.pathsep + os.environ.get("PATH", "")
                os.add_dll_directory(_bin)
except Exception:
    pass

import re, asyncio, threading, queue, time, json, tempfile, traceback
import numpy as np
from pathlib import Path
import http.server
import urllib.request

# ─── 外部ライブラリ ───────────────────────────────────────────────────────────

try:
    import pyaudio
    PYAUDIO_OK = True
except ImportError:
    pyaudio = None
    PYAUDIO_OK = False

try:
    import sounddevice as sd
    SOUNDDEVICE_OK = True
except ImportError:
    sd = None
    SOUNDDEVICE_OK = False
    print("[MIC] sounddevice なし → プッシュトゥトーク無効")

try:
    import anthropic
except ImportError:
    print("pip install anthropic"); exit(1)

try:
    import pygame
    pygame.mixer.pre_init(frequency=22050, size=-16, channels=2, buffer=1024)
    pygame.mixer.init()
    PYGAME_OK = True
except Exception:
    PYGAME_OK = False
    print("[TTS] pygame なし → 音声なしで動作")

try:
    from faster_whisper import WhisperModel
except ImportError:
    print("pip install faster-whisper"); exit(1)

try:
    import websockets
    import logging as _logging
    _logging.getLogger("websockets").setLevel(_logging.CRITICAL)
    WS_OK = True
except ImportError:
    print("pip install websockets"); exit(1)

try:
    import edge_tts
    EDGE_TTS_OK = True
except ImportError:
    EDGE_TTS_OK = False

import requests as _req
_VVX = _req.Session()

# ═══════════════════════════════════════════════════════════════════
#  定数
# ═══════════════════════════════════════════════════════════════════

ANTHROPIC_API_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL_HAIKU         = "claude-haiku-4-5-20251001"   # 通常Q&A
MODEL_SONNET        = "claude-sonnet-4-6"            # キャッシュ整理・複雑判断

HTTP_HOST           = os.environ.get("HTTP_HOST", "127.0.0.1")  # Docker: 0.0.0.0
HTTP_PORT           = 8012
WS_PORT             = 9012   # mippi_main.py の 9001 と衝突しない

# AivisSpeech（VOICEVOX互換API）
# 起動後に http://localhost:10101/speakers でスピーカーID一覧を確認できる
VOICEVOX_URL        = os.environ.get("TTS_URL",      "http://localhost:10101")
VOICEVOX_SPEAKER    = int(os.environ.get("TTS_SPEAKER", "888753760"))  # Anneli ノーマル
VOICEVOX_SPEED      = float(os.environ.get("TTS_SPEED",      "1.1"))
VOICEVOX_PITCH      = float(os.environ.get("TTS_PITCH",      "0.05"))
VOICEVOX_INTONATION = float(os.environ.get("TTS_INTONATION", "1.2"))

TTS_VOICE           = "ja-JP-NanamiNeural"
TTS_RATE            = "+25%"
TTS_PITCH           = "+45Hz"

WHISPER_MODEL       = "small"
# ─── マイク設定（mippi_main.py + 011号 RoadTalk 振幅知見融合）───────────────
# 011号 CP03pro 振幅実測：エンジン音≈141 / 発話500〜9389 / 無音11〜26
# FF11 ゲーム音はヘッドセット経由でも漏れるため mippi 700 より少し上げる
MIC_SEARCH_KEYWORD  = "airpods"
SAMPLE_RATE         = 16000
CHUNK_SIZE          = 1024
VOICE_THRESHOLD     = 99999  # 発話検知閾値（AirPods接続時のみ有効な値に下げる）
POST_SILENCE_SEC    = 1.2    # 無音1.2秒で送信（短い発話の誤送信防止）
MIN_VOICE_SEC       = 1.5    # 1.5秒未満はノイズとして捨てる
INTERRUPT_MIN_CHUNKS = 10   # 割り込み検知の連続チャンク数

MAX_HISTORY_TURNS   = 8
STATIC_DIR          = Path(__file__).parent
KB_FILE             = Path(__file__).parent / "012_knowledge.json"

# ═══════════════════════════════════════════════════════════════════
#  Brave Search（メイン）/ DuckDuckGo（フォールバック）
# ═══════════════════════════════════════════════════════════════════
BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")

def brave_search(query: str, max_results: int = 4) -> str:
    """Brave Search APIでFF11情報を検索。結果を文字列で返す。"""
    if not BRAVE_API_KEY:
        return ddg_search(query, max_results)  # キー未設定時はDDGフォールバック
    try:
        resp = _req.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": f"FF11 FFXI {query}", "count": max_results,
                    "country": "jp", "search_lang": "ja", "ui_lang": "ja-JP"},
            headers={"Accept": "application/json",
                     "Accept-Encoding": "gzip",
                     "X-Subscription-Token": BRAVE_API_KEY},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("web", {}).get("results", [])
        if not results:
            return ddg_search(query, max_results)
        parts = []
        for r in results:
            title = (r.get("title") or "").strip()
            desc  = (r.get("description") or "").strip()
            if desc:
                parts.append(f"【{title}】\n{desc}")
        result_text = "\n\n".join(parts)
        print(f"[SEARCH] Brave: {len(parts)}件取得")
        return result_text
    except Exception as e:
        print(f"[SEARCH] Brave エラー: {e} → DDGフォールバック")
        return ddg_search(query, max_results)

def ddg_search(query: str, max_results: int = 3) -> str:
    """DuckDuckGo フォールバック検索"""
    try:
        try:
            from ddgs import DDGS  # 新パッケージ名
        except ImportError:
            from duckduckgo_search import DDGS  # 旧名フォールバック
        with DDGS() as ddgs:
            # まず日本語FF11専門サイトで検索
            results = list(ddgs.text(
                f"FF11 {query} wiki",
                max_results=max_results, region="jp-jp",
                safesearch="off"))
            # 結果が薄い場合は英語wikiも追加
            if len(results) < 2:
                results += list(ddgs.text(
                    f"FFXI Final Fantasy XI {query}",
                    max_results=2, region="wt-wt"))
        parts = []
        for r in results:
            title = (r.get('title') or '').strip()
            body  = (r.get('body')  or '').strip()
            if body:
                parts.append(f"【{title}】\n{body}")
        result_text = "\n\n".join(parts)
        if result_text:
            print(f"[SEARCH] DDG: {len(parts)}件取得")
        return result_text
    except Exception as e:
        print(f"[SEARCH] DDG エラー: {e}")
        return ""

# ═══════════════════════════════════════════════════════════════════
#  サーバー側 知識ベース（KB）
# ═══════════════════════════════════════════════════════════════════

def _kb_load() -> dict:
    if KB_FILE.exists():
        try:
            return json.loads(KB_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"entries": {}}

def _kb_save(kb: dict):
    e = kb["entries"]
    if len(e) > 300:
        oldest = sorted(e.keys(), key=lambda k: e[k].get("ts", 0))
        for k in oldest[:len(e) - 300]:
            del e[k]
    KB_FILE.write_text(json.dumps(kb, ensure_ascii=False, indent=2), encoding="utf-8")

def _kb_tags(text: str) -> list:
    tags = set()
    for m in re.finditer(r'[ァ-ヶー]{3,}', text):
        tags.add(m.group())
    for j in ['BLM','WHM','MNK','WAR','PLD','THF','DRK','BST','BRD','RNG',
              'SAM','NIN','DRG','SMN','BLU','COR','PUP','DNC','SCH','GEO','RUN',
              'WS','NM','NQ','HQ','TP','PT','HP','MP']:
        if j in text.upper():
            tags.add(j)
    for m in re.finditer(r'[一-龯々]{2,4}', text):
        tags.add(m.group())
    return list(tags)[:12]

def kb_search(question: str) -> dict | None:
    kb    = _kb_load()
    words = _kb_tags(question)
    ql    = question.lower()
    best  = None
    best_score = 0.0
    for entry in kb["entries"].values():
        if entry.get("stale"):
            continue
        etags = entry.get("tags", [])
        hits  = sum(1 for w in words if any(w in t or t in w for t in etags))
        score = hits / max(len(words), len(etags), 1) if (words or etags) else 0
        if ql in entry.get("q", "").lower():
            score += 0.35
        if score >= 0.4 and score > best_score:
            best_score = score
            best = entry
    if best:
        best["hits"] = best.get("hits", 0) + 1
        _kb_save(kb)
    return best

def kb_add(q: str, a: str, verified: bool = False):
    kb   = _kb_load()
    tags = _kb_tags(q + " " + a)
    if verified:
        for entry in kb["entries"].values():
            etags = entry.get("tags", [])
            overlap = sum(1 for t in tags if t in etags)
            ratio = overlap / max(len(tags), len(etags), 1)
            if ratio >= 0.5 and not entry.get("verified"):
                entry["stale"] = True
    uid = f"{int(time.time()*1000)}_{len(kb['entries'])}"
    kb["entries"][uid] = {
        "q": q, "a": a, "tags": tags,
        "ts": int(time.time()), "hits": 0,
        "verified": verified, "stale": False,
    }
    _kb_save(kb)
    print(f"[KB] 保存 ({len(kb['entries'])}件) verified={verified}: {q[:30]}")

# ═══════════════════════════════════════════════════════════════════
#  キャラクター設定（.env でカスタマイズ可能）
# ═══════════════════════════════════════════════════════════════════

CHAR_NAME        = os.environ.get("CHAR_NAME",        "ミア")
CHAR_PROFILE     = os.environ.get("CHAR_PROFILE",
    "ミスラ族の冒険者。FF11を20年以上プレイした先輩プレイヤーAI。明るく好奇心旺盛。")
CHAR_TONE        = os.environ.get("CHAR_TONE",
    "明るくフレンドリー。「〜だよ！」「〜ね！」「いっしょに冒険しよ〜！」が口ぐせ。")
CHAR_TONE_EXAMPLES = os.environ.get("CHAR_TONE_EXAMPLES",
    "「サラマンダーのWSはインファーノだよ！前衛は必ず散開してね！」\n"
    "「ちょっと待って、ハルシオンの性能調べてくる！いっしょに冒険しよ〜！」\n"
    "「うーん、正確な数値わからないから調べてくるね〜」\n"
    "「めっちゃ危ない！速攻回避して！」")

# ═══════════════════════════════════════════════════════════════════
#  システムプロンプト（キャラ設定 + 固定ルール）
# ═══════════════════════════════════════════════════════════════════

def _build_system() -> str:
    return f"""あなたは「{CHAR_NAME}」というAIキャラクターです。
FF11（Final Fantasy XI）の専属サポートAIです。

【キャラクター設定】
{CHAR_PROFILE}

【口調・話し方】
{CHAR_TONE}

【得意領域】
- ジョブ・サポジョブ構成、PT構成と役割分担
- 危険ウェポンスキル（WS）と対処法（散開・集合・カウンター方法）
- クエスト・ミッション・イベント進行
- 合成レシピ・素材・スキル上げルート
- エリア・NM・BCNM・ENM・アサルト情報
- 装備・魔装備・オーグメント・ジョブポイント振り
- アビセア・エンピリアン・アドゥリン・レルムエクセルシア・新コンテンツ

【絶対にやってはいけないこと】
- **絵文字禁止。🐱😊✨等は一切使うな。テキストのみ。**
- **「検索した」「調べた」「検索結果では」は実際に検索した時だけ言え。使ってないのに言うのは嘘。**
- 「確か〜だったと思う」で数値を言わない → 知らないなら「わからん、ちょっと聞いて」と言え
- FF11はアップデートで性能が変わる。訓練データを断言するな
- 質問がよくわからない → キャラクターらしい言葉で聞き返せ

【回答スタイル】
- 1〜2文が基本。立ち位置・図が必要な時だけASCII図を使う
- 確信あり → 即答
- 自信ない → 「調べてくるわ！」と言ってから検索して答える
- 複数案 → 推しを1個先に言ってから選択肢

【口調例】
{CHAR_TONE_EXAMPLES}
"""

FF11_SYSTEM = _build_system()

# ═══════════════════════════════════════════════════════════════════
#  FF11System
# ═══════════════════════════════════════════════════════════════════

class FF11System:
    def __init__(self):
        self.claude          = None
        self.whisper         = None
        self.audio_q         = queue.Queue()
        self._browser_clients: set = set()
        self._text_input_q   = queue.Queue()
        self.conversation    = []
        self.is_speaking     = False
        self.interrupt_req   = False
        self._use_voicevox   = False
        self.running         = True
        # sounddevice 録音
        self._sd_frames: list = []
        self._sd_stream       = None
        self._sd_lock         = threading.Lock()
        self._sd_rate: int    = SAMPLE_RATE
        self._sd_vad_stop     = threading.Event()  # VADセッション停止フラグ

    # ──────────────────────────────────────────────────────────────
    #  初期化
    # ──────────────────────────────────────────────────────────────

    def initialize(self):
        print("=" * 55)
        print("  012号 FF11アシスタントくん ミア 起動中...")
        print("=" * 55)
        if not ANTHROPIC_API_KEY:
            print("ERROR: .env に ANTHROPIC_API_KEY を設定してください"); exit(1)
        self.claude = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        print("[OK] Claude API クライアント初期化")

        print(f"[...] faster-whisper ({WHISPER_MODEL}) ロード中...")
        try:
            self.whisper = WhisperModel(WHISPER_MODEL, device="cuda", compute_type="int8")
            print("[OK] faster-whisper (CUDA)")
        except Exception:
            self.whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
            print("[OK] faster-whisper (CPU)")

        if self._vvx_available():
            self._use_voicevox = True
            print(f"[OK] VOICEVOX (speaker={VOICEVOX_SPEAKER})")
        elif EDGE_TTS_OK:
            print("[OK] edge-tts フォールバック")
        else:
            print("[!!] TTS なし — テキストのみで動作")

        print("=" * 55)
        print(f"  OBSブラウザソース: http://localhost:{HTTP_PORT}/")
        print(f"  WebSocket: ws://127.0.0.1:{WS_PORT}")
        print("  停止: Ctrl+C")
        print("=" * 55 + "\n")

    # ──────────────────────────────────────────────────────────────
    #  VOICEVOX
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _vvx_available() -> bool:
        try:
            _VVX.get(f"{VOICEVOX_URL}/version", timeout=2)
            return True
        except Exception:
            return False

    @staticmethod
    def _vvx_synth(text: str, out_path: str) -> bool:
        try:
            import urllib.parse
            enc = urllib.parse.quote(text)
            r1  = _VVX.post(
                f"{VOICEVOX_URL}/audio_query?text={enc}&speaker={VOICEVOX_SPEAKER}",
                timeout=10)
            r1.raise_for_status()
            q   = r1.json()
            q["speedScale"]     = VOICEVOX_SPEED
            q["pitchScale"]     = VOICEVOX_PITCH
            q["intonationScale"]= VOICEVOX_INTONATION
            r2  = _VVX.post(
                f"{VOICEVOX_URL}/synthesis?speaker={VOICEVOX_SPEAKER}",
                json=q, headers={"Content-Type": "application/json"}, timeout=30)
            r2.raise_for_status()
            with open(out_path, "wb") as f:
                f.write(r2.content)
            return True
        except Exception as e:
            print(f"[VVX] エラー: {e}")
            return False

    # ──────────────────────────────────────────────────────────────
    #  TTS + 再生
    # ──────────────────────────────────────────────────────────────

    async def _speak_sentence(self, text: str):
        """1文をTTS合成して再生。ブラウザに口パク通知。"""
        tts_text = re.sub(r'[*_`#>\[\]|]', '', text).strip()
        if not tts_text:
            return

        loop = asyncio.get_event_loop()
        fd, path = tempfile.mkstemp(suffix=".wav" if self._use_voicevox else ".mp3")
        os.close(fd)

        try:
            if self._use_voicevox:
                ok = await loop.run_in_executor(None, self._vvx_synth, tts_text, path)
                if not ok:
                    return
            elif EDGE_TTS_OK:
                try:
                    comm = edge_tts.Communicate(tts_text, TTS_VOICE, rate=TTS_RATE, pitch=TTS_PITCH)
                    await comm.save(path)
                except Exception as e:
                    print(f"[TTS] edge-tts 失敗: {e}")
                    return
            else:
                return

            await self.browser_send({"cmd": "talkStart"})
            self.is_speaking = True
            if PYGAME_OK:
                await loop.run_in_executor(None, self._pygame_play, path)
            await self.browser_send({"cmd": "talkStop"})
        finally:
            self.is_speaking = False
            try:
                os.unlink(path)
            except Exception:
                pass

    @staticmethod
    def _pygame_play(path: str):
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=22050, size=-16, channels=2, buffer=512)
            pygame.mixer.music.load(path)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                time.sleep(0.05)
        except Exception as e:
            print(f"[PLAY] エラー: {e}")

    # ──────────────────────────────────────────────────────────────
    #  Claude API
    # ──────────────────────────────────────────────────────────────

    async def ask(self, user_text: str, model: str = MODEL_HAIKU) -> str:
        """Claudeに問い合わせ。KB→web_search の順で回答。"""
        self.conversation.append({"role": "user", "content": user_text})
        if len(self.conversation) > MAX_HISTORY_TURNS * 2:
            self.conversation = self.conversation[-(MAX_HISTORY_TURNS * 2):]

        await self.browser_send({"cmd": "user_msg", "text": user_text})

        # ① KBから検索（ユーザーには見えない裏処理）
        cached = kb_search(user_text)
        if cached:
            reply = cached["a"]
            print(f"[KB] ヒット (hits={cached['hits']}): {user_text[:30]}")
            self.conversation.append({"role": "assistant", "content": reply})
            await self.browser_send({"cmd": "reply", "text": reply})
            print(f"[ミア] {reply[:120]}{'...' if len(reply)>120 else ''}")
            try:
                await self._speak_sentence(reply)
            except Exception as e:
                print(f"[TTS] KB回答の読み上げ失敗: {e}")
            self.interrupt_req = False
            return reply

        await self.browser_send({"cmd": "setStamp", "key": "nani", "duration": 8000})

        SPLIT_RE   = re.compile(r"[。！？!?\n]")
        full_reply = ""
        search_msgs = list(self.conversation)
        announced   = False  # 「調べてくるわ」は1回だけ

        try:
            # DDGで検索してから Claude に渡す（tool_choice は使わない）
            loop = asyncio.get_event_loop()
            search_results = await loop.run_in_executor(None, brave_search, user_text)

            if search_results:
                announced = True
                print(f"[SEARCH] DDG ヒット: {len(search_results)}文字")
                await self.browser_send({"cmd": "setStamp", "key": "nani", "duration": 8000})
                # 検索結果を「参考情報（内部知識）」として注入
                # 「検索結果」という言葉を出さず、みっぴ自身の知識として答えさせる
                search_msgs[-1] = {
                    "role": "user",
                    "content": (
                        f"[参考情報（これをもとに答えること。「検索結果」「調べた」という言葉は使うな）]\n"
                        f"{search_results}\n\n---\n"
                        f"質問: {user_text}"
                    )
                }
            else:
                print(f"[SEARCH] DDG 結果なし → 訓練データで回答")

            response = await self.claude.messages.create(
                model=model,
                max_tokens=800 if model == MODEL_HAIKU else 1500,
                system=FF11_SYSTEM,
                messages=search_msgs,
            )
            print(f"[Claude] stop={response.stop_reason}")

            for block in response.content:
                t = getattr(block, "text", None)
                if t:
                    full_reply += t

        except Exception as e:
            print(f"[Claude] エラー: {e}")
            full_reply = "うまく繋がらんかったわ〜！もう一回試してや！"

        full_reply = full_reply.strip()
        # Claudeが出力するXML検索タグを除去（ツールなしモードでの誤生成）
        full_reply = re.sub(r'<web_search>.*?</web_search>', '', full_reply, flags=re.DOTALL)
        full_reply = re.sub(r'<search_query>.*?</search_query>', '', full_reply, flags=re.DOTALL)
        full_reply = re.sub(r'\*\*.*?\*\*', lambda m: m.group().replace('**',''), full_reply)  # **太字**を平文に
        full_reply = full_reply.strip()
        if not full_reply:
            full_reply = "うまく調べられんかったわ〜"

        # ① ブラウザに返答を先に送る（TTSが失敗してもUIには表示される）
        self.conversation.append({"role": "assistant", "content": full_reply})
        await self.browser_send({"cmd": "reply", "text": full_reply})
        print(f"[ミア] {full_reply[:120]}{'...' if len(full_reply) > 120 else ''}")

        # KBには検索済みデータのみ保存（verified=Trueのみ）
        # announced=True = 実際にweb_searchが使われた
        if announced:
            kb_add(user_text, full_reply, verified=True)

        # ② TTS再生（失敗してもUIには影響しない）
        try:
            buf = full_reply
            while SPLIT_RE.search(buf) and not self.interrupt_req:
                m    = SPLIT_RE.search(buf)
                sent = buf[:m.end()].strip()
                buf  = buf[m.end():]
                sent = re.sub(r'^[-=*#\s]+$', '', sent).strip()
                if len(sent) >= 4:
                    await self.browser_send({"cmd": "autoFace", "text": sent})
                    await self._speak_sentence(sent)
            if buf.strip() and not self.interrupt_req:
                await self.browser_send({"cmd": "autoFace", "text": buf.strip()})
                await self._speak_sentence(buf.strip())
        except Exception as e:
            print(f"[TTS] エラー（UIには影響なし）: {e}")

        self.interrupt_req = False
        return full_reply

    async def consolidate_cache(self, cache_json: str) -> str:
        """キャッシュ整理（Sonnet を一時使用）。ブラウザから cache_consolidate コマンドで呼ぶ。"""
        prompt = (
            "以下はFF11に関するQ&Aキャッシュです。重複を統合し、"
            "最も有用な形に整理してJSON配列で返してください（同じキーで）:\n\n"
            + cache_json
        )
        resp = await self.claude.messages.create(
            model=MODEL_SONNET,
            max_tokens=2000,
            system="FF11 Q&Aキャッシュを整理するアシスタントです。JSON形式で返してください。",
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text

    # ──────────────────────────────────────────────────────────────
    #  WebSocket ブラウザ通信
    # ──────────────────────────────────────────────────────────────

    async def browser_send(self, data: dict):
        if not self._browser_clients:
            return
        msg = json.dumps(data, ensure_ascii=False)
        dead = set()
        for ws in list(self._browser_clients):
            try:
                await ws.send(msg)
            except Exception:
                dead.add(ws)
        self._browser_clients -= dead

    async def _ws_server(self):
        async def handler(ws):
            self._browser_clients.add(ws)
            print(f"[WS] ブラウザ接続 ({len(self._browser_clients)}台)")
            try:
                async for raw in ws:
                    try:
                        d = json.loads(raw)
                        cmd = d.get("cmd", "")
                        if cmd == "user_text":
                            text = d.get("text", "").strip()
                            if text:
                                print(f"[USER] テキスト: 「{text}」")
                                self._text_input_q.put(text)
                        elif cmd == "cache_consolidate":
                            print("[CACHE] Sonnet でキャッシュ整理中...")
                            result = await self.consolidate_cache(d.get("data", "[]"))
                            await ws.send(json.dumps(
                                {"cmd": "cache_consolidated", "data": result},
                                ensure_ascii=False))
                        elif cmd == "interrupt":
                            self.interrupt_req = True
                        elif cmd == "mic_start":
                            self._sd_vad_stop.clear()
                            await self.browser_send({"cmd": "mic_state", "state": "recording"})
                            async def _vad_task():
                                loop = asyncio.get_event_loop()
                                audio = await loop.run_in_executor(None, self._sd_mic_vad_session)
                                if audio is None:
                                    await self.browser_send({"cmd": "mic_state", "state": "idle"})
                                    return
                                await self.browser_send({"cmd": "mic_state", "state": "processing"})
                                text = await loop.run_in_executor(None, self.transcribe, audio)
                                if text:
                                    print(f"[MIC] 音声認識: 「{text}」")
                                    await self.browser_send({"cmd": "mic_transcribed", "text": text})
                                    self._text_input_q.put(text)
                                else:
                                    print("[MIC] 音声認識: テキストなし")
                                    await self.browser_send({"cmd": "mic_state", "state": "idle"})
                            asyncio.ensure_future(_vad_task())
                        elif cmd == "mic_stop":
                            # 録音中キャンセル
                            self._sd_vad_stop.set()
                            await self.browser_send({"cmd": "mic_state", "state": "idle"})
                    except Exception as e:
                        print(f"[WS] コマンド処理エラー: {e}")
            except Exception as e:
                if "ConnectionClosed" not in type(e).__name__:
                    print(f"[WS] 接続エラー: {e}")
            finally:
                self._browser_clients.discard(ws)
                print(f"[WS] 切断 (残{len(self._browser_clients)}台)")

        async with websockets.serve(handler, HTTP_HOST, WS_PORT,
                                    reuse_address=True, logger=None):
            print(f"[OK] WebSocket ws://127.0.0.1:{WS_PORT}")
            await asyncio.Future()

    # ──────────────────────────────────────────────────────────────
    #  HTTP サーバー（012_ff11.html を配信）
    # ──────────────────────────────────────────────────────────────

    def _start_http(self):
        class H(http.server.BaseHTTPRequestHandler):
            def log_message(_, *a): pass
            def do_GET(_):
                req = _.path.lstrip('/')
                if req.startswith('mia/') and req.endswith('.png'):
                    img_path = STATIC_DIR / req
                    if img_path.exists():
                        data = img_path.read_bytes()
                        _.send_response(200)
                        _.send_header("Content-Type", "image/png")
                        _.send_header("Content-Length", str(len(data)))
                        _.end_headers()
                        _.wfile.write(data)
                        return
                    _.send_error(404); return
                path = STATIC_DIR / "012_ff11.html"
                if not path.exists():
                    _.send_error(404); return
                data = path.read_bytes()
                _.send_response(200)
                _.send_header("Content-Type", "text/html; charset=utf-8")
                _.send_header("Content-Length", str(len(data)))
                _.end_headers()
                _.wfile.write(data)

        srv = http.server.HTTPServer((HTTP_HOST, HTTP_PORT), H)
        print(f"[OK] HTTP http://{HTTP_HOST}:{HTTP_PORT}/")
        threading.Thread(target=srv.serve_forever, daemon=True).start()

    # ──────────────────────────────────────────────────────────────
    #  sounddevice VAD
    # ──────────────────────────────────────────────────────────────

    def _sd_mic_vad_session(self) -> "np.ndarray | None":
        """
        1クリックで録音開始→自動VAD停止→音声データを返す。
        ・録音直後0.4秒でノイズフロアをキャリブレーション
        ・ノイズフロア×1.8 を動的閾値とする
        ・発話開始後、POST_SILENCE_SEC 無音が続いたら終了
        ・最大 MAX_RECORD_SEC 秒で強制終了
        """
        if not SOUNDDEVICE_OK:
            return None

        MAX_RECORD_SEC  = 30
        CAL_SEC         = 0.4

        chunk_q: "queue.Queue[np.ndarray]" = queue.Queue()
        self._sd_vad_stop.clear()

        try:
            dev_info    = sd.query_devices(sd.default.device[0])
            native_rate = int(dev_info['default_samplerate'])
        except Exception:
            native_rate = SAMPLE_RATE

        def _cb(indata, frames_count, time_info, status):
            chunk_q.put(indata.copy())

        stream = sd.InputStream(
            samplerate=native_rate, channels=1, dtype='float32',
            callback=_cb, blocksize=CHUNK_SIZE
        )
        stream.start()
        print(f"[MIC] VAD録音開始 ({native_rate}Hz) — 話し終わると自動送信")

        # キャリブレーション
        cal_chunks = int(CAL_SEC * native_rate / CHUNK_SIZE)
        cal_data   = []
        for _ in range(cal_chunks):
            try:
                cal_data.append(chunk_q.get(timeout=1.0))
            except queue.Empty:
                break
        if cal_data:
            raw = float(np.sqrt(np.mean(np.concatenate(cal_data) ** 2))) * 32768 * 1.8
            noise_floor = max(raw, 2000)  # 下限2000（スピーカー回り込み・低すぎる閾値を防ぐ）
        else:
            noise_floor = 2000
        print(f"[MIC] 動的閾値={noise_floor:.0f}")

        # VADループ
        all_frames: list  = []  # キャリブ音声は含めない（クリーンな発話のみ）
        silence_limit     = int(POST_SILENCE_SEC * native_rate / CHUNK_SIZE)
        voice_started     = False
        silent_chunks     = 0
        deadline          = time.time() + MAX_RECORD_SEC

        try:
            while time.time() < deadline and not self._sd_vad_stop.is_set():
                try:
                    chunk = chunk_q.get(timeout=0.5)
                except queue.Empty:
                    continue

                # TTS再生中はフレームを破棄（スピーカー音の回り込み防止）
                if self.is_speaking:
                    voice_started = False
                    silent_chunks = 0
                    all_frames.clear()
                    continue

                rms = float(np.sqrt(np.mean(chunk ** 2))) * 32768

                if rms > noise_floor:
                    if not voice_started:
                        print(f"[MIC] 発話検出 RMS={rms:.0f} (閾値={noise_floor:.0f})")
                    voice_started = True
                    silent_chunks = 0
                    all_frames.append(chunk)
                elif voice_started:
                    all_frames.append(chunk)
                    silent_chunks += 1
                    if silent_chunks >= silence_limit:
                        print(f"[MIC] 無音検出 → 録音終了")
                        break  # 発話終了
        finally:
            stream.stop()
            stream.close()

        if self._sd_vad_stop.is_set():
            # PTTモードでmouseup → 発話があれば音声を返す、なければキャンセル
            if not voice_started or not all_frames:
                print("[MIC] キャンセル（発話なし）")
                return None
            print("[MIC] PTT終了 → 録音データを使用")
        elif not voice_started:
            print("[MIC] 発話なし")
            return None

        audio = np.concatenate(all_frames, axis=0).flatten()
        dur   = len(audio) / native_rate
        print(f"[MIC] VAD録音完了 {dur:.1f}s")
        if dur < MIN_VOICE_SEC:
            print("[MIC] 短すぎ → スキップ")
            return None

        if native_rate != SAMPLE_RATE:
            pcm16 = (audio * 32768).astype(np.int16)
            pcm16 = self._resample(pcm16, native_rate, SAMPLE_RATE)
            audio = pcm16.astype(np.float32) / 32768.0
        return audio

    def _sd_mic_stop(self) -> "np.ndarray | None":
        if not SOUNDDEVICE_OK or self._sd_stream is None:
            return None
        try:
            self._sd_stream.stop()
            self._sd_stream.close()
            self._sd_stream = None
            with self._sd_lock:
                frames = list(self._sd_frames)
                self._sd_frames = []
            if not frames:
                return None
            audio = np.concatenate(frames, axis=0).flatten()
            if self._sd_rate != SAMPLE_RATE:
                audio = self._resample(
                    (audio * 32768).astype(np.int16),
                    self._sd_rate, SAMPLE_RATE
                ).astype(np.float32) / 32768.0
            dur = len(audio) / SAMPLE_RATE
            print(f"[MIC] 録音停止 ({dur:.1f}s)")
            return audio
        except Exception as e:
            print(f"[MIC] 録音停止エラー: {e}")
            return None

    @staticmethod
    def _resample(pcm: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
        if from_rate == to_rate or len(pcm) == 0:
            return pcm
        new_len = int(round(len(pcm) * to_rate / from_rate))
        x_old   = np.arange(len(pcm), dtype=np.float64)
        x_new   = np.linspace(0.0, len(pcm) - 1, new_len)
        return np.interp(x_new, x_old, pcm.astype(np.float64)).astype(np.int16)

    def _mic_loop(self):
        if not SOUNDDEVICE_OK:
            print("[MIC] sounddevice なし → VAD スキップ")
            return
        print(f"[MIC] VAD 開始 (threshold={VOICE_THRESHOLD}, silence={POST_SILENCE_SEC}s)")
        while self.running:
            try:
                self._mic_inner_sd()
            except Exception as e:
                print(f"[MIC] エラー: {e} → 5秒後に再起動")
                for _ in range(5):
                    if not self.running: return
                    time.sleep(1)

    def _mic_inner_sd(self):
        """sounddevice による VAD ループ（pyaudio 不要）"""
        # デバイス選択：キーワードで探してなければデフォルト
        dev_idx  = None
        dev_rate = SAMPLE_RATE
        try:
            devices = sd.query_devices()
            for i, d in enumerate(devices):
                if d["max_input_channels"] >= 1 and MIC_SEARCH_KEYWORD in d["name"].lower():
                    dev_idx  = i
                    dev_rate = int(d["default_samplerate"])
                    print(f"[MIC] 検出: [{i}] {d['name']} ({dev_rate}Hz)")
                    break
            if dev_idx is None:
                print(f"[MIC] '{MIC_SEARCH_KEYWORD}' 未検出 → デフォルトデバイスで続行")
        except Exception as e:
            print(f"[MIC] デバイス検索エラー: {e}")

        silence_limit = int(POST_SILENCE_SEC * dev_rate / CHUNK_SIZE)
        frames: list  = []
        voice_chunks  = 0
        silent_chunks = 0
        recording     = False
        chunk_buf     = queue.Queue()

        def _cb(indata, frames_count, time_info, status):
            chunk_buf.put(indata.copy())

        with sd.InputStream(device=dev_idx, samplerate=dev_rate, channels=1,
                            dtype='float32', blocksize=CHUNK_SIZE, callback=_cb):
            while self.running:
                try:
                    chunk = chunk_buf.get(timeout=0.5)
                except queue.Empty:
                    continue

                rms = float(np.sqrt(np.mean(chunk ** 2))) * 32768  # float→int16スケールに換算

                if rms > VOICE_THRESHOLD:
                    if not recording:
                        recording     = True
                        frames        = []
                        voice_chunks  = 0
                        silent_chunks = 0
                    frames.append(chunk.copy())
                    voice_chunks  += 1
                    silent_chunks  = 0
                    if self.is_speaking:
                        self.interrupt_req = True
                else:
                    if recording:
                        frames.append(chunk.copy())
                        silent_chunks += 1
                        if silent_chunks >= silence_limit:
                            recording = False
                            audio_f32 = np.concatenate(frames, axis=0).flatten()
                            dur = len(audio_f32) / dev_rate
                            if dur >= MIN_VOICE_SEC:
                                if dev_rate != SAMPLE_RATE:
                                    pcm16 = (audio_f32 * 32768).astype(np.int16)
                                    pcm16 = self._resample(pcm16, dev_rate, SAMPLE_RATE)
                                    audio_f32 = pcm16.astype(np.float32) / 32768.0
                                self.audio_q.put(audio_f32)
                                print(f"[MIC] VAD 録音完了 {dur:.1f}s → STT キューに追加")
                            frames = []

    def transcribe(self, audio_f32: np.ndarray) -> str:
        if not self.whisper:
            return ""
        try:
            segs, _ = self.whisper.transcribe(
                audio_f32, language="ja", beam_size=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                initial_prompt=(
                    "FF11 FFXI ヴァナ・ディール ジョブ ウェポンスキル アビリティ "
                    "忍者 黒魔 白魔 モンク 戦士 シーフ ナイト 侍 吟遊詩人 "
                    "レンジャー 竜騎士 召喚士 暗黒騎士 青魔 コルセア 踊り子 学者 "
                    "イオニック エンピリアン ミシック レリック リトレース "
                    "エスナ ケアル デス テレポ リトレース "
                    "NM BCNM ENM アビセア アドゥリン レルムエクセルシア "
                    "ゆっぴ"
                ))
            return "".join(s.text for s in segs).strip()
        except Exception as e:
            print(f"[STT] エラー: {e}")
            return ""

    # ──────────────────────────────────────────────────────────────
    #  メインループ
    # ──────────────────────────────────────────────────────────────

    async def _main_loop(self):
        """音声 & テキスト入力を統合して Claude に投げるループ"""
        loop = asyncio.get_event_loop()
        while self.running:
            # テキスト入力チェック（ブラウザから）
            user_text = None
            try:
                user_text = self._text_input_q.get_nowait()
            except queue.Empty:
                pass

            # 音声入力チェック（マイクから）
            if user_text is None:
                try:
                    audio_f32 = self.audio_q.get_nowait()
                    print("[STT] 文字起こし中...")
                    user_text = await loop.run_in_executor(None, self.transcribe, audio_f32)
                    if user_text:
                        print(f"[USER] 音声: 「{user_text}」")
                    else:
                        await asyncio.sleep(0.05)
                        continue
                except queue.Empty:
                    await asyncio.sleep(0.05)
                    continue

            if not user_text.strip():
                continue

            # Claude Haiku に投げる
            await self.ask(user_text)

    async def run(self):
        self.initialize()
        self._start_http()

        # asyncio 内の未ハンドル例外をログに出す
        def _async_exc_handler(loop, context):
            exc = context.get("exception")
            msg = context.get("message", "不明なエラー")
            if exc:
                tb = "".join(_tb.format_exception(type(exc), exc, exc.__traceback__))
                print(f"[ASYNC][ERROR] {msg}\n{tb}")
            else:
                print(f"[ASYNC][ERROR] {msg}")
        asyncio.get_event_loop().set_exception_handler(_async_exc_handler)

        threading.Thread(target=self._mic_loop, daemon=True).start()
        print("[OK] マイク VAD スレッド起動")

        await asyncio.gather(
            self._ws_server(),
            self._main_loop(),
        )


# ─── エントリーポイント ───────────────────────────────────────────────────────

if __name__ == "__main__":
    sys = FF11System()
    try:
        asyncio.run(sys.run())
    except KeyboardInterrupt:
        print("\n[012] 停止")
        sys.running = False
