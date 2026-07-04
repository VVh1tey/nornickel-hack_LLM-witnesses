FROM  ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

COPY uv.lock pyproject.toml .

COPY . . 

RUN uv sync

EXPOSE 8501

CMD ["uv", "run", "streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
