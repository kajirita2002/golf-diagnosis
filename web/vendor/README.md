# 同梱したもの（外部 CDN を使わない。docs/DESIGN_v2.md §6.3）

npm registry と storage.googleapis.com から取り、版入りのディレクトリにそのまま置いた（手を加えていない）。
サーバーは `/vendor/` に `Cache-Control: immutable` を付けて配る（`api/internal/httpapi/checkpoint.go` の `vendorCache`）。
Service Worker（`web/sw.js`）には入れない（大きいので、開いたときだけ読む）。

| ディレクトリ | 取った元 | 版 | ライセンス | 使う所 |
|---|---|---|---|---|
| `mediapipe-tasks-vision-1.0.1/` | npm `@mediapipe/tasks-vision`（`vision_bundle.mjs`・`wasm/vision_wasm_internal.{js,wasm}`） | 1.0.1 | Apache-2.0 | `web/video.js` の体の点（Pose Landmarker） |
| `mediapipe-models/pose_landmarker_lite-float16-v1.task` | `storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/` | lite・float16・v1 | Apache-2.0 | 同上のモデル |
| `mp4box-2.4.1/` | npm `mp4box`（`dist/mp4box.all.mjs` と、それが読む2つのファイル） | 2.4.1 | BSD-3-Clause | `web/video.js` の fps の読み取り（mp4 / mov の moov だけを読む） |

- WASM は SIMD 版だけを置いた（`vision_wasm_nosimd_internal.*` は置いていない。11MB 減らすため）。WebAssembly SIMD の無い古いブラウザ
  （iOS Safari 16.3 以前など）では体の点を取れず、姿勢の項目は「判断できない（体の点が見えない）」になる。
- 大きさ: 合わせて約 18MB（WASM 11.8MB・モデル 5.8MB・mp4box 0.3MB）。
- sha256:
  - `mediapipe-models/pose_landmarker_lite-float16-v1.task` 59929e1d1ee95287735ddd833b19cf4ac46d29bc7afddbbf6753c459690d574a
  - `mediapipe-tasks-vision-1.0.1/vision_bundle.mjs` d885630c297c0b20b1fe86096cb06291c4c8080876f27852e724f24ac603713f
  - `mediapipe-tasks-vision-1.0.1/wasm/vision_wasm_internal.wasm` 8da277a733926eacd0474b8704b36742d6ec3231c57a860c5b889dff8f1df886
  - `mediapipe-tasks-vision-1.0.1/wasm/vision_wasm_internal.js` e170ee67dd4e16c1a6fcd8840a206687e5a59b22c20e4a902bc445b095454d73
  - `mp4box-2.4.1/mp4box.all.mjs` 34fa8fd681e8b63998ca9e4c3b477830dd11310c8aaedc37ecb8f49c5452d259
- 版を上げるときは、新しい版のディレクトリを足し、`web/video.js` の先頭の定数を直す（古いディレクトリは消す）。
