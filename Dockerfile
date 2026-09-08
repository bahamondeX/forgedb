FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY forgedb ./forgedb
RUN pip install --no-cache-dir -e ".[dev]"

COPY tests ./tests

# Same checks CI runs, so `docker run forgedb` reproduces the pipeline locally.
CMD ["sh", "-c", "ruff check . && pytest -q && mypy"]
