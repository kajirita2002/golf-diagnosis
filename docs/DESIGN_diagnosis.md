# 診断（diagnosis）: 課題 → 原因の動き → 理想の動き → 直し方 → 確かめ方

- 版: 2026-09-30・`diagnosis/1`（知識ベースは `coaching_kb/1`）
- きっかけ（本人の声）: 「簡潔すぎて何をどう直せばいいのか分からない。現在はこんな課題があって、それはこの動きが原因で、理想の動きはこうだから、こう直しましょう、を構造化して」「見るだけでは結果は変わらない。アクションプランが要る」「原因の動きには動画が要る」。
- 置き場: `analysis/golf_analysis/diagnosis.py`（組み立て）・`analysis/golf_analysis/coaching_kb.json`（知識ベース）・`analysis/tests/test_diagnosis.py`。
- 出る所: 分析サービス `POST /v1/report` の `diagnosis`。Go の `GET /v1/sessions/{id}/report` の `report.diagnosis` にそのまま入る。

## 約束（DESIGN_v2 の R1〜R12 はそのまま）

- **最初の面に数字・角度・専門用語を出さないが、説明は省かない。** 課題ごとに5段を、開かなくても全部読める文で出す。数字は `evidence`（「なぜそう言える？」）だけ。回数・球数は漢数字（十球のうち八球）。検査は `diagnosis.texts()` に `gist.check_plain` を全部当てる（テスト）。
- **TrackMan が言えるのは当たる瞬間のクラブの様子まで。** `club_state`（面の向き・当たる場所）は確かな事実として書き、体の動きは原因の候補として並べる。
- **原因の根拠（`basis`）は3つ。** `measured`（動画で測れた）・`seen`（動画の見た目）・`likely`（球のデータとガイド／一般的な見方から見た可能性）。動画のチェックで範囲の外（`judge` の課題の候補＝多数・一本だけでない）なら上げて先頭に置き、範囲の中だった動きは `causes_ruled_out` に移す。動画の番手の種類が違う範囲には当てない。
- **ドリルを隠さない。** ガイドの練習法は `checked_by: "PGAガイド p.X"`（`checked: true`）、ガイドに無いものは `checked_by: "一般的な練習法"`（`checked: false`）の札で出す。安全の注意（`safety`）を必ず添える（立てた物を打ち出しの先に置かない等）。
- **動画が無くてもアクションプランを空にしない。** クラブの状態に効く練習（当たる場所・面の向き）を先に、候補の動きに効く練習を `tentative: true` で最後に置く。原因の段に `video_cta`（［動画を撮って原因を確かめる］・どの形を撮るか）を出す。
- **左打ち**: 知識ベースの `{lead}` / `{trail}` / `{dir:right}` / `{dir:left}` を利き手で差し込む（当たる場所のネック／先は入れ替えない）。

## 課題の選び方と順番

1. 本体の範囲を「まとめ → 球の多い順」に並べ、主の範囲のプランの `now` → `next` → ほかの範囲の `now` → `next` → 追加の症状（アイアンの入射が浅い S7）。同じ症状は1つにまとめ、`club_scope` に番手を足す。
2. 動画のチェックがあれば、`judge` が選んだ「まずここ」（ドミノの順）の動きを持つ課題を先頭へ。どの球の課題にも結びつかない動きは、動きの課題（`from: "video"`、`id: "motion:<原因>"`）として先頭に置く（DESIGN_v2 §4.4 の「動きが主役」）。
3. 最大3つ。1つ目が「いま取り組むこと」（`now_or_next: "now"`）。

## 形（担当どうしの約束から変えた所に ★）

```
diagnosis = {
  version: "diagnosis/1", ★kb_version,
  summary, strengths[≤2], video_needed, video_hint,
  issues: [{
    id, rank, ★now_or_next, ★from: "ball"|"video", club_scope（ラベル）, ★scope_ids,
    title, now, impact,
    ★club_state: 当たる瞬間のクラブの様子（確かな事実。動きの課題では null）,
    causes: [{ id, title, explain, basis, basis_text, ★source: "guide"|"general", ★source_label,
               checkpoint: {p, item_id, view, ★p_name} | null, guide: {pages, ★section, ★label} | null }],
    ★causes_ruled_out: [動画で範囲の中だった動き],
    ★video_cta: {button, view, text, p[]} | null,
    ideal: { text, p, item_id, ★p_name, figure },
    fix: {
      ★setup: [構えで直すこと],
      cue（動き＋理由の1文）, ★cue_move（動きだけ）, ★cue_why（理由だけ）,
      drills: [{ id, name, how, why, checked, ★checked_by, ★equipment, ★place, ★steps[], ★reps, ★reps_text,
                 ★safety, ★source: {kind, label, pages}, ★for: "symptom:<id>"|"cause:<id>", ★tentative, ★tentative_note?,
                 ★role: "drill"|"check", ★catalog_drill }],
      practice（今日の練習の組み方の文）, ★menu: [{kind: baseline|drill|main, title, balls, what, drill_id?}]
    },
    check, evidence: [{label, value_text, claim_id? | ★item_id?}], ★plan_candidate
  }]
}
```

- `figure` は描く中身の指定だけ（`{kind: "strike"|"face"|"path"|"low_point"|"attack", now, ideal}`、動画で確かめた原因なら `{kind: "checkpoint", p, view, item_id, overlay: true}`）。絵は画面の担当が描く。
- 「checked_by の無いドリルは出さない」を「札（checked_by）の無いドリルは出さない」に変えた（ガイドの練習法と一般的な練習法の両方に札がある）。既存のドリル集（`drills/catalog.json`）の `checked_by` は触っていない（人が確かめる欄のまま）。

## 知識ベースの持ち方

- `symptoms`（球の症状 12）: 題・`club_state`・響き方・原因の候補（原因 id ＋ この症状を生む理由 `explain` ＋ `source`）・理想・直し方（構え・意識する動きと理由・ドリル）・確かめ方・動画の向き・根拠に出す定型文。
- `causes`（原因の動き 31）: 動きの名前・動きの課題としての題・チェックポイントの項目と外れの向き（動画の判定と突き合わせる）・確かめる P・ガイドのページ（ノートの節の範囲）・理想・直し方・確かめ方。
- `drills`（28。うちガイドの練習法 12）: 名前・道具・置き方・手順（一〜三）・球数・何に効くか・安全・出典（guide ならページ）。
- 検査（`diagnosis.validate`・pytest）: 右・左の直書き・知らない差し込み口・最初の面に出せない語（両方の利き手で）・カタログに無い項目と外れの向き・束ねた項目（`same_as`）の相手・ノートの範囲外のページ・ドリルの無い症状／原因。
- Go は保存済みの判定を渡すだけで、解説を開くたびに測り直さない（測るのはチェックの画面を開いたとき）。

## 画面（web/diagnosis.js・home.js・practice.js・record.js）

- **診断レポート**（`#/session/{id}`。ホームの［くわしいレポートを見る］・記録の各日の［診断レポートを見る］から1押し）: 今日のまとめと良かったところ → 課題の一覧 → 課題ごとのカード。1つ目の課題は ①いま起きていること（文と絵）→ ②原因の動き（当たる瞬間のクラブ＝確かな事実、体の動き＝根拠の札つき・可能性の高い順、動画が無ければ［動画を撮って原因を確かめる］）→ ③理想の動き（動画で確かめた原因なら本人のコマに範囲と線、無ければ P の見本の線画＋いまと理想の絵）→ ④直し方（構えで直すこと・本番で意識する体の動きと理由・ドリル（道具・置き方・手順・球数・何に効くか・安全・出どころの札）・今日の練習メニュー）→ ⑤確かめ方、を開かなくても全部読める。2つ目からは見出しと①だけで、残りは開いて読む。数字は各段の「なぜそう言える？」の中だけ。主ボタンは［この課題で練習を組む］。`diagnosis` が無い古い分析サービスのときだけ、前の要点（4つの塊）を出す。
- **ホーム**: 課題のカードを「課題 → 原因（いちばん可能性の高い動き。根拠の札）→ 理想 → 今日やること（本番で意識する体の動き＋練習メニュー）」の4行で出す。Go の `GET /v1/players/{id}/home` の `focus.diag`（`{summary, video_needed, video_hint, issue, next, n_issues, rank}`。プランがあればその候補の課題）。プラン中は今日やることがプランの練習になり、レポートへの副ボタンを置く。主ボタンは画面の下に留める。
- **練習**: プランのきっかけの診断の課題（`plan_candidate` と範囲で突き合わせる）の「構えで直すこと」と「ドリル」を出し、組み方のドリルのブロックに診断のドリルを当てる（練習中の画面にも名前・置き方・手順・安全）。写しを `localStorage` に持つので圏外でも出る。
- **診断のドリルのブロック**: 診断レポートからプランを作るときは `diag_drills: true` を送り、分析サービスは確かめ済みのドリル集のドリルが無くても型にドリルのブロックを残す（ドリルの球を「いつも通り」「本番」に混ぜない）。ドリル集の `checked_by` は触らない。
- **記録**: ①球のデータ（TrackMan）と②スイングの動画（後ろから・できれば正面からも）をセットで入れる形。球だけの日は「体の動きは可能性のまま」と出す。
- **出どころの札**の形は `config.PLAIN_LABEL_PATTERNS["source"]`（「PGAガイド p.X」「一般的な練習法」「一般的な見方」）。
