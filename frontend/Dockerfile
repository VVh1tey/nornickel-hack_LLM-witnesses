FROM  ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

COPY uv.lock pyproject.toml .

COPY . . 

RUN uv sync

EXPOSE 8000

CMD ["uv", "run", "main.py"]
