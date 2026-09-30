# Swing Lab（golf-diagnosis）を1つのコンテナで動かす。
#   Go の API（画面も配る）… 外に開くのはこれだけ（$PORT）
#   Python の分析サービス … 127.0.0.1:8001。外からは届かない（API キーを使う入口を外に出さない）
# Render の無料プランはディスクを持たないので、保存先は PostgreSQL（DB_PATH に URL）。

FROM golang:1.26-bookworm AS build
WORKDIR /src/api
COPY api/go.mod api/go.sum ./
RUN go mod download
COPY api/ ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/server ./cmd/server

FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy
COPY analysis/pyproject.toml analysis/uv.lock analysis/
RUN pip install --no-cache-dir uv==0.8.17 \
 && cd analysis && uv sync --frozen --no-dev --no-install-project
COPY analysis/ analysis/
RUN cd analysis && uv sync --frozen --no-dev
COPY web/ web/
COPY --from=build /out/server /app/server
COPY deploy/start.sh /app/start.sh
# /app/data は DB_PATH が無いときの一時的な保存先（書けないと起動で落ちて再起動を繰り返した）
# 配るファイルは誰でも読めるようにする。取ってきた手元の権限（0640）のまま入ると、app の利用者が読めず
# MediaPipe の WASM が 403 になった（体の点が取れない。ローカルの docker build で見つけた）
RUN chmod +x /app/start.sh && chmod -R a+rX /app/web /app/analysis \
 && useradd -r -u 10001 app && mkdir -p /app/data && chown app /app/data
USER app
ENV WEB_DIR=/app/web ANALYSIS_URL=http://127.0.0.1:8001 PORT=8080 DEFAULT_DB_PATH=/app/data/golf.db
EXPOSE 8080
CMD ["/app/start.sh"]
