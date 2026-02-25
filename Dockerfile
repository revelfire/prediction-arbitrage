# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies for asyncpg (libpq) and uv
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 && \
    rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install Python dependencies (cached layer)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source and config
COPY src/ src/
COPY config.example.yaml config.yaml

# Install the project itself
RUN uv sync --frozen --no-dev

EXPOSE 8000

# Default: start the dashboard. Override CMD for other commands.
CMD ["uv", "run", "arb-scanner", "serve", "--host", "0.0.0.0", "--port", "8000"]
