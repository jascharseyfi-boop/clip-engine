FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 DATA_DIR=/data

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fontconfig ca-certificates libsndfile1 \
 && rm -rf /var/lib/apt/lists/*

# JavaScript-Laufzeit, die yt-dlp für YouTube braucht
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt && pip install -U "yt-dlp[default]"

COPY fonts /app/fonts
RUN mkdir -p /usr/share/fonts/truetype/clip && cp /app/fonts/*.ttf /usr/share/fonts/truetype/clip/ && fc-cache -f
COPY app /app/app

EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
