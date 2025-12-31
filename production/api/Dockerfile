FROM python:3.10-slim-bullseye

WORKDIR /app

# Install poetry
RUN pip install --no-cache-dir poetry

# Copy dependency files first for caching
COPY packages/utils/pyproject.toml packages/utils/poetry.lock /app/packages/utils/
COPY api/pyproject.toml api/poetry.lock /app/api/

# Build utils package
COPY packages/utils/ /app/packages/utils/
WORKDIR /app/packages/utils
RUN poetry build

# Install API dependencies
COPY api/ /app/api/
WORKDIR /app/api
RUN poetry config virtualenvs.in-project true && \
    poetry install --no-interaction

# Run the API
CMD ["poetry", "run", "python", "src/api/main.py"]