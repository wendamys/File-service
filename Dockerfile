FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

# zoneinfo нужна база часовых поясов: в slim-образе её нет, а без неё
# ZoneInfo("Asia/Novosibirsk") падает на импорте.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /srv

# Зависимости ставятся отдельным слоем — он переиспользуется, пока не
# изменились pyproject.toml/uv.lock.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY app ./app
RUN uv sync --locked --no-dev

RUN useradd --create-home --uid 1000 app \
    && mkdir -p /data \
    && chown -R app:app /data /srv
USER app

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
