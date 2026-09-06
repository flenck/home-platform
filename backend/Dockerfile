FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY homeplatform/ ./homeplatform/
RUN pip install --no-cache-dir .

# Include frontend and config for self-contained deployment
COPY frontend/ ./frontend/
COPY config/ ./config/

EXPOSE 8000

ENV STATIC_DIR=/app/frontend
ENV HOME_CONFIG_DIR=/app/config

CMD ["python", "-m", "homeplatform"]
