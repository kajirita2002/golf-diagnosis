#!/bin/bash
# 分析サービス（Python）と API（Go）を立てる。
#
# **待ち受け（Go）を道連れにしない。** 前はどちらかが落ちたら全体を落としていたので、
# 分析サービスが起動で転ぶと Render は「起動中」のまま再起動を繰り返し、原因が外から見えなかった。
# いまは分析サービスだけ落ちても繰り返し立て直し、Go は動き続けて /healthz で "analysis":"down" と返す。
set -u
(
  cd /app/analysis
  while true; do
    .venv/bin/uvicorn golf_analysis.app:app --host 127.0.0.1 --port 8001 --log-level warning
    echo "analysis exited with $?; restarting in 3s" >&2
    sleep 3
  done
) &
exec /app/server
