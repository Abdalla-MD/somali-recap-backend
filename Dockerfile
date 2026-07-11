# Deployable on Render.com (Docker web service) — see README.md.
FROM python:3.11-slim

# ffmpeg is needed to extract audio from uploaded videos.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render injects a PORT env var and expects the app to listen on it
# (defaults to 8000 for local/other platforms that don't set PORT).
EXPOSE 8000
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
