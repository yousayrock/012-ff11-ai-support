# 012号 FF11アシスタントくん みっぴ

> 🐱 FF11プレイ中にデスクトップに常駐して、ゲームの質問にリアルタイムで答えてくれる AI ネコアシスタント

---

## 🎮 これは何？ / What is this?

**日本語**

ファイナルファンタジーXI（FF11）をプレイしながら、画面右下にふわふわ浮かぶ AI ネコキャラ「みっぴ」がゲームの知識を教えてくれるデスクトップアシスタントです。

- ジョブ構成・WS・クエスト・合成・装備など何でも聞ける
- Web 検索して最新情報を取得してから関西弁で回答
- 回答は吹き出しで表示、ゲームの邪魔をしない
- 透過対応でデスクトップに自然に溶け込む

**English**

A desktop AI assistant for Final Fantasy XI that floats over your game screen as an animated cat character ("Mippi"). Ask anything about FF11 — jobs, weapon skills, quests, crafting, gear — and get real-time answers via web search.

- Searches the web before answering to ensure accurate info
- Non-intrusive overlay with transparent background
- Answers in casual Japanese (Kansai dialect) as the "Mippi" character
- Text-based input; voice input support planned

---

## ✨ 機能 / Features

| 機能 | 詳細 |
|------|------|
| デスクトップキャラ | Canvas 描画の白ネコが右下に浮遊・口パク・表情変化 |
| Web 検索 | DDG / Brave Search で FF11 情報を毎回検索してから回答 |
| 知識 DB | 検索済み回答をローカル JSON に保存、次回は即答 |
| テロップ表示 | 短い回答は吹き出し、長い回答はパネルに表示 |
| 自己修繕 DB | 新しい検索結果が来たら古い未確認データを無効化 |
| デスクトップ透過 | PyWebView 5.3.2 で OBS 不要の透過オーバーレイ |
| AivisSpeech 対応 | Docker で起動すれば音声出力も可能（任意） |

---

## 🛠️ セットアップ / Setup

### 必要なもの / Requirements

- Python 3.10+
- CUDA 対応 GPU（推奨、CPU でも動作可）
- [Anthropic API キー](https://console.anthropic.com/)
- （任意）[Brave Search API キー](https://api.search.brave.com/)（無料 2000回/月）

### インストール / Installation

```bash
# 依存ライブラリ
pip install -r 012_requirements.txt

# 環境変数の設定
cp .env.example .env
# .env を編集して ANTHROPIC_API_KEY を記入
```

### 起動 / Launch

**デスクトップキャラとして起動（推奨）**
```
012_desktop.bat をダブルクリック
```

**サーバーのみ起動（OBS ブラウザソースや通常ブラウザで使う場合）**
```
012_start.bat をダブルクリック
→ ブラウザで http://localhost:8012/ を開く
```

---

## 🏗️ アーキテクチャ / Architecture

```
012_desktop.bat
  └─ 012_desktop.py (PyWebView)
       ├─ 012_server.py を子プロセスで起動
       │    ├─ HTTP :8012  → 012_ff11.html を配信
       │    ├─ WebSocket :9012  → ブラウザと双方向通信
       │    ├─ STT: faster-whisper（音声入力・任意）
       │    ├─ Search: DDG / Brave Search
       │    ├─ AI: Claude Haiku（回答生成）
       │    ├─ TTS: AivisSpeech → edge-tts フォールバック
       │    └─ KB: 012_knowledge.json（ローカル知識DB）
       └─ 012_ff11.html を透過ウィンドウで表示
```

### 回答フロー / Answer flow

```
質問
 → KB 検索（ヒットすれば即答）
 → DDG / Brave で Web 検索
 → 検索結果 + Claude Haiku で回答生成
 → みっぴの関西弁テロップ表示
 → KB に保存
```

---

## 🔊 音声設定 / Voice Setup (Optional)

AivisSpeech を Docker で起動すると音声出力が有効になります。

```bash
# WSL2 / Linux
docker run -d -p 10101:10101 \
  -v aivisspeech-data:/home/user/.local/share/AivisSpeech-Engine-Dev \
  ghcr.io/aivis-project/aivisspeech-engine:cpu-latest
```

`.env` に追加：
```
TTS_URL=http://localhost:10101
TTS_SPEAKER=888753760   # 起動後 http://localhost:10101/speakers で確認
```

---

## 🗺️ ロードマップ / Roadmap

- [x] **P1**: テキスト入力 + Web 検索 + デスクトップキャラ表示
- [ ] **P2**: 音声入力対応（専用マイク環境で再有効化）
- [ ] **P3**: Windower アドオン（Lua）でゲーム内チャットから直接呼び出し
- [ ] **P4**: ゆっぴの FF11 プレイ記録・装備・クエスト進捗をパーソナル KB に蓄積

---

## 📁 ファイル構成 / File Structure

```
012_server.py          メインサーバー（STT / AI / TTS / WS / KB）
012_ff11.html          フロントエンド UI（みっぴキャラ + テロップ）
012_desktop.py         PyWebView デスクトップランチャー
012_start.bat          サーバーのみ起動
012_desktop.bat        デスクトップキャラとして起動
.env.example           環境変数テンプレート
012_requirements.txt   Python 依存ライブラリ
docker-compose.aivisspeech.yml  AivisSpeech Docker 設定
```

---

## 🚢 プロジェクトについて / About

[寳家プロジェクト](https://github.com/yousayrock) の未来ガジェット研究所 012号。

003号「みっぴ配信AIシステム」のキャラクター・アーキテクチャを流用。  
011号「RoadTalk」の「ドライバーが手を離さず使える UI」設計思想を参考。

> *「神は細部に宿る」— ヘセド・エメト*

---

## ライセンス / License

MIT License

音声モデル（AivisSpeech）は各モデルのライセンスに従ってください。  
Voice models used with AivisSpeech are subject to their individual licenses.
