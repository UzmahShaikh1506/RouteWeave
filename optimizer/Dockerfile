FROM python:3.12-slim

# Security: set labels for traceability
LABEL maintainer="routeweave" \
      description="RouteWeave optimizer microservice"

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
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8001/health || exit 1

EXPOSE 8001

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001"]
