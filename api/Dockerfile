FROM python:3.12-slim

# Security: set labels for traceability
LABEL maintainer="routeweave" \
      description="RouteWeave API service"

WORKDIR /app

# Install curl for healthcheck (not present in slim)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Security: run as non-root user
USER 1000:1000

# Healthcheck for container orchestration
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
