# 012号 FF11 AIサポートシステム — ミア・リノス

> 🐾 FF11プレイヤー全員の専属AIサポートキャラクター「ミア・リノス」がデスクトップに常駐し、ゲームの質問にリアルタイムで答えてくれるシステム

---

## 🎮 これは何？ / What is this?

**日本語**

ファイナルファンタジーXI（FF11）プレイヤーのためのデスクトップAIサポートシステムです。
ミスラ族の冒険者キャラクター「ミア・リノス」が画面右下に常駐し、ジョブ・装備・クエスト・合成など何でも答えます。

- Web検索で最新情報を取得してから回答
- 16種類のスタンプ画像で感情を表現
- 透過・フレームレスウィンドウでゲームの邪魔をしない
- チャット欄・字幕テロップが独立して自由移動・拡大縮小可能

**English**

A desktop AI support system for Final Fantasy XI players.
"Mia Rinos," a Mithra adventurer character, lives on your desktop and answers questions about jobs, gear, quests, and crafting in real time.

- Web search for up-to-date FF11 information
- 16 expressive stamp images with emotion detection
- Transparent, frameless overlay — stays out of your way
- Chat panel and subtitle bar are independently movable and zoomable

---

## ✨ 機能 / Features

| 機能 | 詳細 |
|------|------|
| デスクトップキャラ | ミア・リノス（ミスラ）の16種スタンプが感情に合わせて切り替わる |
| 字幕テロップ | 返答を１文ずつ映画字幕風に表示。ドラッグ・ズーム対応 |
| チャットパネル | 会話ログ＋メモタブ。独立ドラッグ・ズーム・20秒自動クローズ |
| Web検索 | DuckDuckGo でFF11情報を毎回検索して回答 |
| 透過オーバーレイ | PyWebView + transparent=True でOBS不要 |
| TTS音声出力 | edge-tts（標準）／AivisSpeech Docker（高品質、任意） |
| 音声入力 | 計画中（sounddevice + faster-whisper） |

---

## 🛠️ セットアップ / Setup

### 必要なもの / Requirements

- Python 3.10+
- [Anthropic API キー](https://console.anthropic.com/)
- （任意）GPU：faster-whisper の高速化に使用

### インストール / Installation

```bash
pip install -r 012_requirements.txt
```

`.env` を作成して API キーを設定：

```
ANTHROPIC_API_KEY=sk-ant-...
```

### 起動 / Launch

**デスクトップキャラとして起動（推奨）**
```
012_desktop.bat をダブルクリック
```

**ブラウザで使う場合**
```
012_start.bat をダブルクリック
→ ブラウザで http://localhost:8012/ を開く
```

---

## 🎮 操作方法 / Controls

| 操作 | 内容 |
|------|------|
| ミアをクリック | チャットパネルを開く／閉じる |
| ミアをドラッグ | ウィンドウを移動 |
| ミア上でスクロール | キャラクターを拡大縮小 |
| パネルヘッダーをドラッグ | チャットパネルを独立移動 |
| テロップ上部グリップをドラッグ | 字幕バーを独立移動 |
| 各要素上でスクロール | 各要素を独立ズーム |
| 左端をドラッグ | パネル幅を変更 |

---

## 🏗️ アーキテクチャ / Architecture

```
012_desktop.bat
  └─ 012_desktop.py (PyWebView 560×520 透過ウィンドウ)
       ├─ 012_server.py を子プロセスで起動
       │    ├─ HTTP :8012  → 012_ff11.html + mia/ スタンプ配信
       │    ├─ WebSocket :9012  → ブラウザと双方向通信
       │    ├─ Search: DuckDuckGo
       │    ├─ AI: Claude Sonnet / Haiku（回答生成 + emotion タグ）
       │    └─ TTS: edge-tts → AivisSpeech フォールバック
       └─ 012_ff11.html を透過ウィンドウで表示
```

### 回答フロー / Answer flow

```
テキスト入力
 → DuckDuckGo でFF11情報を検索
 → 検索結果 + Claude API で回答生成（emotion キー付き）
 → ミアのスタンプ切り替え + 字幕テロップ表示（１文ずつ）
 → チャットログに追記
```

---

## 📁 ファイル構成 / File Structure

```
012_server.py          メインサーバー（AI / TTS / WS / HTTP）
012_ff11.html          フロントエンド UI（ミアキャラ + テロップ + チャット）
012_desktop.py         PyWebView デスクトップランチャー
012_start.bat          サーバーのみ起動
012_desktop.bat        デスクトップキャラとして起動
012_requirements.txt   Python 依存ライブラリ
mia/                   ミア・リノス スタンプ画像 16枚（透過PNG 313×313px）
.env.example           環境変数テンプレート
```

---

## 🗺️ ロードマップ / Roadmap

- [x] **P1** テキスト入力 + Web検索 + デスクトップキャラ表示
- [x] **P1.5** ミア・リノス スタンプ16種 + 感情連動 + 字幕テロップ
- [x] **P1.6** チャット・テロップの独立ドラッグ＆ズーム + タブ
- [ ] **P2** 音声入力（sounddevice + faster-whisper）
- [ ] **P3** FF11全プレイヤー向け情報強化（ジョブ・装備・クエストDB）
- [ ] **P4** Web / モバイル展開

---

## 🔊 音声出力設定 / Voice Setup (Optional)

AivisSpeech を Docker で起動すると高品質な音声になります。

```bash
docker run -d -p 10101:10101 \
  ghcr.io/aivis-project/aivisspeech-engine:cpu-latest
```

`.env` に追加：
```
TTS_URL=http://localhost:10101
TTS_SPEAKER=888753760
```

---

## 🚢 プロジェクトについて / About

[寳家プロジェクト](https://github.com/yousayrock) の未来ガジェット研究所 012号。

FF11プレイヤー全員の専属AIサポートシステムを目指して開発中。

> *「いっしょに冒険しよ〜！」— ミア・リノス*

---

## 🙏 スペシャルサンクス / Special Thanks

- **ニケちゃん**（AITuber）— キャラクターシステムのインスピレーション源

---

## ライセンス / License

MIT License

音声モデル（AivisSpeech）は各モデルのライセンスに従ってください。
