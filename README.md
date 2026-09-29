# golf-diagnosis

TrackMan × 動画 × 個人履歴から「なぜその球になったか」を特定し、**実験で確かめる**ゴルフ診断エンジン。
設計は [`docs/DESIGN.md`](docs/DESIGN.md)。

いまは **Phase 0**（TrackMan の CSV だけ）。動画の解析はまだ入っていない。

## 構成

```
api/        Go の API（取り込み・保存・1球ごとの物理分解）
analysis/   Python の分析サービス（Good 判定・原因の群・ばらつき・前回との比較・実験の評価）
web/        画面（素の HTML/JS 1ファイル。API が / で配る）
testdata/   ダミーの CSV と、実データ（real/。本人の練習・数値だけ）
scripts/    e2e.py（2つのサービスを立てて通しで確かめる）・ui_check.py（画面をブラウザで操作）
docs/       設計
```

## 動かす

```sh
# 分析サービス（:8001）
cd analysis && uv sync && uv run uvicorn golf_analysis.app:app --port 8001

# API（:8080）
cd api && go run ./cmd/server
```

ブラウザで http://localhost:8080/ を開くと画面が出ます（CSV の取り込み・診断・1球ずつ・前回と比べる・実験）。

### スクショから取り込む（Claude の API を使う）

分析サービスを立てる前に、Claude の API キーを入れておきます。

```sh
export ANTHROPIC_API_KEY=sk-ant-...   # 分析サービスを立てる端末で
```

1. TrackMan のレポートの URL を貼って「表で開く」→ 10項目・Club data の表で開く
2. クラブごとの表を **Average の行まで入れて**スクショする
3. 画面に貼る（Ctrl+V）・ドロップ・選ぶ → 読み取り → 検算 → 確かめて「この表を取り込む」

- 読み取りは `claude-opus-5-5`。1枚 数円〜十数円（画面に実際の利用料を出す）。
- **検算**: 読み取った各球の平均を、画面の Average の行と列ごとに比べる。合わない列は ★ で出る。
  1桁の読み違いや R/L の読み違いはここで見つかる。直して「もう一度検算」できる。
- 取り込みは自動ではしない（人が確かめてから）。
API だけ使うなら:

```sh
# 選手とセッションを作る
curl -s -XPOST localhost:8080/v1/players -d '{"name":"Rita","handedness":"R"}'
curl -s -XPOST localhost:8080/v1/sessions -d '{"player_id":1,"date":"2026-09-27"}'

# CSV を取り込む（単位が書いていない列は ?units=imperial|metric）
curl -s -XPOST localhost:8080/v1/sessions/1/import -F file=@testdata/trackman_dummy_session.csv

# 1球ごとの分解（速い診断）／セッションの分析（深い診断）
curl -s localhost:8080/v1/sessions/1/shots
curl -s localhost:8080/v1/sessions/1/analysis

# 実験：仮説 → ブロック → 評価
curl -s -XPOST localhost:8080/v1/sessions/1/experiments \
  -d '{"hypothesis":"フェースが開いてプッシュフェード","intervention":"左手甲を目標に向ける","target_metric":"face_to_path","goal":"reduce_abs"}'
curl -s -XPOST localhost:8080/v1/experiments/1/blocks -d '{"kind":"baseline","seq_from":1,"seq_to":10}'
curl -s -XPOST localhost:8080/v1/experiments/1/blocks -d '{"kind":"intervention","seq_from":11,"seq_to":20}'
curl -s localhost:8080/v1/experiments/1/evaluation
```

## 公開（Render）

english-tts の Render のブループリント（english-tts の `render.yaml`）に、2つ目のサービスとして載せている。

- 1つのコンテナ（`Dockerfile`）で Go の API と Python の分析サービスを動かす。外に開くのは Go だけ。
- **保存先は PostgreSQL**（無料プランはディスクを持たないので SQLite だと再起動で消える）。
  english-tts と同じデータベースの **`golf` スキーマ**に作る（english-tts のテーブルとぶつけない）。
- **パスワード（`APP_PASSWORD`）を必ず掛ける。** 掛けないと URL を知った人があなたの API キーで読み取りを動かせる。
- 環境変数: `DB_PATH`（PostgreSQL の URL）/ `APP_PASSWORD` / `ANTHROPIC_API_KEY`

手元で本番と同じ形を試す:

```sh
docker build -t golf-diagnosis .
docker run -p 8080:8080 -e APP_PASSWORD=... -e DB_PATH=postgres://... -e ANTHROPIC_API_KEY=... golf-diagnosis
```

## API

| メソッド | パス | 中身 |
|---|---|---|
| POST | `/v1/players` | 選手（`handedness`: R / L） |
| GET | `/v1/players/{id}/sessions` | 選手のセッション |
| POST | `/v1/sessions` | セッション |
| POST | `/v1/sessions/{id}/import` | CSV の取り込み（multipart の `file` か本文そのまま） |
| GET | `/v1/sessions/{id}/shots` | 球と1球ごとの分解 |
| PATCH | `/v1/shots/{id}` | `club` / `excluded` / `good_override`（null で自動） / `impact_offset_mm` / `impact_height_mm` |
| GET | `/v1/sessions/{id}/analysis` | セッションの分析（分析サービスへ） |
| GET | `/v1/sessions/{id}/compare?with={id}` | 前のセッションと何が違ったか（L0 の変化を L1 で説明） |
| POST | `/v1/sessions/{id}/experiments` | 実験（`goal`: reduce_abs / reduce_sd / increase / decrease） |
| POST | `/v1/experiments/{id}/blocks` | ブロック（baseline / intervention / retention、打った順の範囲） |
| GET | `/v1/experiments/{id}/evaluation` | 実験の評価 |
| GET | `/v1/trackman/report-link?url=` | TrackMan のレポートを10項目・Club data で開くリンク |
| POST | `/v1/screenshot` | スクショ（multipart の `image`）の表を読んで検算する（取り込みはしない） |
| POST | `/v1/screenshot/verify` | 直した表（TSV）をもう一度検算する |

## テスト

```sh
cd api && go vet ./... && go test ./...
TEST_DATABASE_URL=postgres://... go test ./internal/httpapi/   # PostgreSQL でも同じテストを通す（golf スキーマを作り直す）
cd analysis && uv run pytest -q
python3 scripts/e2e.py        # 2つのサービスを本当に立てて通しで確かめる
python3 scripts/ui_check.py   # 画面をブラウザで操作（Playwright。PC幅とスマホ幅・横溢れ・JSエラー）
```

## 約束

- 保存は **SI 単位・右打ちの座標**。単位が分からない値は推測せずにエラーにする。
- 物理の式は Go の `physics` に1か所だけ。分析サービスには計算済みの分解を渡す。
- 判定を確率のふりにしない。件数・区間・`insufficient` で言う。
- 閾値は全部「初期値・要較正」。変えたら `EngineVersion` / `ENGINE_VERSION` を上げる。
