# Use an official Python runtime
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV APP_HOME=/app

WORKDIR $APP_HOME

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
  gcc \
  python3-dev \
  git \
  curl \
  && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && \
  pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY app/ ./app/
COPY frontend/ ./frontend/
COPY local_dbt_test/ ./local_dbt_test/

# Create logs and sessions directories
RUN mkdir -p logs app/sessions && chmod 777 logs app/sessions

# Expose port
EXPOSE 8000

# Health check for Azure Container Apps
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

# Start
CMD ["python", "-m", "app.main"]
