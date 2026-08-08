# P.E.T.E.R. — single-container cloud deployment
# Serves the mobile PWA + backend API AND runs the Peter voice worker
# (agent.py) in the same container, so one public URL is all you need.
FROM python:3.11-slim

WORKDIR /app

# System deps: build tools + audio/runtime libs the LiveKit/Gemini stack needs.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python deps first (better layer caching).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the app.
COPY . .

# The backend binds to 0.0.0.0 so the platform's port mapping works.
EXPOSE 8000

# Cloud platforms inject config via env vars (no .env needed).
# PUBLIC_URL / SPOTIFY_REDIRECT_URI are set by the host (see render.yaml).
CMD ["python", "mobile_backend.py"]
