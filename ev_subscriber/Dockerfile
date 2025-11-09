# Use official lightweight Python base
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies needed for psycopg3 + build tools
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

# Copy and install dependencies
COPY requirements.txt ./
RUN python -m pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Environment consistency (UrbanOS standard)
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app

# Expose FastAPI port
EXPOSE 8082

# Entry point
CMD ["python", "main.py"]
