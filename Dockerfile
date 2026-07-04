FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# зависимости отдельным слоем — кэшируется, пока pyproject/uv.lock не меняются
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY backend backend
COPY frontend frontend
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
# без этого print() в скриптах (index_lightrag.py и т.п.) оседает в буфере
# stdout и не долетает до `docker compose logs`, пока буфер не заполнится
ENV PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "hypofactory.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
