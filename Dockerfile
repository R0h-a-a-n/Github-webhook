# ---- Base image ----
    FROM python:3.11-slim

    # ---- Working directory ----
    WORKDIR /app
    
    # ---- Install system dependencies ----
    RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl && \
        rm -rf /var/lib/apt/lists/*
    
    # ---- Copy and install Python dependencies ----
    COPY requirements.txt .
    RUN pip install --no-cache-dir -r requirements.txt
    
    # ---- Copy application files ----
    COPY . .
    
    # ---- Expose the FastAPI port ----
    EXPOSE 8000
    
    # ---- Environment setup ----
    # Ensure UTF-8 encoding and disable Python buffering for logs
    ENV PYTHONUNBUFFERED=1 \
        PYTHONIOENCODING=UTF-8
    
    # ---- Run the FastAPI app ----
    # GITHUB_TOKEN should be passed as env variable at runtime:
    #   docker run -e GITHUB_TOKEN=<token> -p 8000:8000 github-event-monitor
    CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
    