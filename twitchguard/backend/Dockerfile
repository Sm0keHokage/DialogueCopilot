FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY rules_builtin ./rules_builtin
COPY alembic.ini ./
COPY migrations ./migrations
COPY config.example.yaml ./config.yaml

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn twitchguard.main:app --host 0.0.0.0 --port 8000"]
