#!/bin/bash
# 分析サービスと API を立てる。どちらかが落ちたら全体を落とす（デプロイ先が再起動する）。
# 片方だけ死んだまま動き続けると、画面は出るのに診断だけ失敗する、が見えにくい。
set -u
cd /app/analysis
.venv/bin/uvicorn golf_analysis.app:app --host 127.0.0.1 --port 8001 --log-level warning &
PY=$!
/app/server &
GO=$!
trap 'kill -TERM $PY $GO 2>/dev/null' TERM INT
wait -n $PY $GO
code=$?
kill -TERM $PY $GO 2>/dev/null
wait
exit $code
