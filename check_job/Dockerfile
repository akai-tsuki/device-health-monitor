# ビルドはリポジトリルートをコンテキストとして実行する:
#   docker build -f check_job/Dockerfile -t check-job .
FROM python:3.12-slim

WORKDIR /app

COPY check_job/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY check_job/ ./check_job/
RUN rm -rf ./check_job/tests

# ログを即時に標準出力へ書き出す（Cloud Logging への取り込み遅延・欠落を避ける）
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "-m", "check_job.main"]
