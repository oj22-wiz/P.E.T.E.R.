# P.E.T.E.R. — single-container cloud deployment
# Serves the mobile PWA + backend API AND runs the Peter voice worker
# (agent.py) in the same container, so one public URL is all you need.
FROM python:3.11-slim

WORKDIR /app

# Numeric/audio libs (numpy's BLAS backend, etc.) auto-detect the HOST's
# total CPU count and spawn that many worker threads for "parallelism".
# On a small free-tier container with a throttled CPU allocation far
# below that count, those extra threads just thrash against each other
# for the same tiny CPU slice instead of helping — a well-known source of
# choppiness in real-time workloads on constrained containers. Pin them
# to 1 so nothing over-subscribes past what's actually available.
ENV OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1

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
