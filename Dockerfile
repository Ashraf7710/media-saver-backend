FROM python:3.11-slim AS runtime

# Deno: يعتمد عليه yt-dlp لحل توقيعات/تحديات YouTube (بدونه قد تفشل الروابط بـ 403)
FROM denoland/deno:bin-2.1.4 AS deno

FROM runtime AS final

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY --from=deno /deno /usr/local/bin/deno

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir --upgrade yt-dlp

COPY . .

ENV PORT=5000
ENV PYTHONUNBUFFERED=1

CMD gunicorn --bind 0.0.0.0:$PORT --workers 2 --timeout 120 app:app