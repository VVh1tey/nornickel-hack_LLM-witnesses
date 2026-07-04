FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# DejaVu Sans — Unicode-шрифт (кириллица) для PDF-экспорта (fpdf2 не умеет
# кириллицу через встроенные core-шрифты); стандартный debian-пакет, не тащим
# бинарник шрифта в git (см. export/pdf_report.py: путь ниже фиксированный).
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

# зависимости отдельным слоем — кэшируется, пока backend/pyproject.toml и
# backend/uv.lock не меняются (pyproject.toml/uv.lock живут под backend/ —
# у бэка и фронта отдельные uv-проекты)
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

WORKDIR /app
COPY backend backend
COPY frontend frontend

WORKDIR /app/backend
RUN uv sync --frozen --no-dev

ENV PATH="/app/backend/.venv/bin:$PATH"
# без этого print() в скриптах (index_lightrag.py и т.п.) оседает в буфере
# stdout и не долетает до `docker compose logs`, пока буфер не заполнится
ENV PYTHONUNBUFFERED=1

WORKDIR /app
EXPOSE 8000
CMD ["uvicorn", "hypofactory.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
