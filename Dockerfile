# ── Stage 1: Build React frontend ──────────────────────────────────────
FROM node:20-alpine AS frontend-build

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm config set strict-ssl false \
 && NODE_ENV=development npm ci
COPY frontend/ ./
RUN npm run build
# Output: /static/react/

# ── Stage 2: Python application ───────────────────────────────────────
FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org --no-cache-dir -r requirements.txt

# Copy all application Python files
COPY *.py ./

# Copy built React from stage 1
COPY --from=frontend-build /static/react ./static/react/

ENV FLASK_ENV=production
ENV PYTHONUNBUFFERED=1
ENV DB_SCHEMA=svcmgr

EXPOSE 5000

# Run with gunicorn (gevent for SSE streaming support)
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--worker-class", "gevent", "--workers", "2", "--timeout", "120", "app:create_app()"]
