# Stage 1: Build React + Vite frontend
FROM node:22-alpine AS frontend-builder
WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# Stage 2: Python FastAPI Backend
FROM python:3.12-slim

WORKDIR /app

# Install system libraries needed by image processing / headless opencv
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install Python production dependencies
COPY requirements.txt requirements-prod.txt ./
RUN pip install --no-cache-dir -r requirements-prod.txt

# Copy backend application packages
COPY backend/ ./backend/
COPY core/ ./core/
COPY datasets/ ./datasets/
COPY storage/ ./storage/

# Copy built React frontend assets from builder stage
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# Default deployment environment
ENV PYTHONUNBUFFERED=1 \
    CVAT_REACT_DIST=/app/frontend/dist \
    CVAT_ENFORCE_LOCAL=0 \
    PORT=10000

# Create upload storage mount point
RUN mkdir -p /var/data/cvat_storage

EXPOSE 10000

# Bind to 0.0.0.0:$PORT
CMD ["sh", "-c", "uvicorn backend.integrated_api:create_integrated_from_env --factory --host 0.0.0.0 --port ${PORT:-10000}"]
