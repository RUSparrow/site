FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    docker.io \
    docker-compose-v2 \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY deploy-agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY deploy-agent/app/ ./app/
COPY deploy-agent/scripts/ ./scripts/

RUN chmod +x ./scripts/deploy.sh

EXPOSE 9000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9000"]
