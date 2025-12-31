FROM python:3.10-slim-bullseye

WORKDIR /app

RUN pip install poetry && \
    poetry config virtualenvs.create false

COPY packages/utils/ /app/packages/utils
COPY api/ /app/api

WORKDIR /app/packages/utils
RUN poetry install --no-interaction --no-root && \
    poetry build

WORKDIR /app/api
RUN poetry install --no-interaction

# Run the API
CMD ["python", "src/api/main.py"]