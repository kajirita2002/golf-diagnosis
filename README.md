# golf-diagnosis

TrackMan × 動画 × 個人履歴から「なぜその球になったか」を特定し、**実験で確かめる**ゴルフ診断エンジン。
設計は [`docs/DESIGN.md`](docs/DESIGN.md)。

いまは **Phase 0**（TrackMan の CSV だけ）。動画の解析はまだ入っていない。

## 構成

```
api/        Go の API（取り込み・保存・1球ごとの物理分解）
analysis/   Python の分析サービス（Good 判定・原因の群・ばらつき・実験の評価）
testdata/   ダミーの TrackMan 風 CSV（実物が届いたら差し替える）
scripts/    e2e.py（2つのサービスを立てて通しで確かめる）
docs/       設計
```

## 動かす

```sh
# 分析サービス（:8001）
cd analysis && uv sync && uv run uvicorn golf_analysis.app:app --port 8001

# API（:8080）
cd api && go run ./cmd/server
```

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

## テスト

```sh
cd api && go vet ./... && go test ./...
cd analysis && uv run pytest -q
python3 scripts/e2e.py   # 2つのサービスを本当に立てて通しで確かめる
```

## 約束

- 保存は **SI 単位・右打ちの座標**。単位が分からない値は推測せずにエラーにする。
- 物理の式は Go の `physics` に1か所だけ。分析サービスには計算済みの分解を渡す。
- 判定を確率のふりにしない。件数・区間・`insufficient` で言う。
- 閾値は全部「初期値・要較正」。変えたら `EngineVersion` / `ENGINE_VERSION` を上げる。
