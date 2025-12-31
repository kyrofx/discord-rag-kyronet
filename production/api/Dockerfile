FROM python:3.10-slim-bullseye

WORKDIR /app

# Force cache invalidation - change this value to rebuild
ARG CACHE_BUST=2025-12-31-v5
RUN echo "Cache bust: $CACHE_BUST"

# Install pydantic and pydantic-core first, explicitly
RUN pip install --no-cache-dir pydantic-core==2.27.2 pydantic==2.10.4 && \
    python -c "from pydantic import BaseModel; print('pydantic step 1 OK')"

# Install other core dependencies
RUN pip install --no-cache-dir \
    fastapi==0.115.6 \
    uvicorn==0.34.0 \
    python-multipart==0.0.20 \
    redis==5.2.1 \
    httpx==0.28.1 \
    motor==3.6.0 \
    tqdm && \
    python -c "from pydantic import BaseModel; print('pydantic step 2 OK')"

# Install langchain dependencies
RUN pip install --no-cache-dir \
    langchain-core==0.3.28 \
    langchain-community==0.3.13 \
    langchain-experimental==0.3.4 \
    langchain-redis==0.2.1 \
    google-generativeai==0.8.4 && \
    python -c "from pydantic import BaseModel; print('pydantic step 3 OK')"

# Copy application code
COPY packages/utils/src/utils /app/utils
COPY api/src/api /app/api

# Set Python path to include utils
ENV PYTHONPATH=/app

WORKDIR /app/api

# Verify pydantic works
RUN python -c "from pydantic import BaseModel; print('pydantic OK')"

# Run the API
CMD ["python", "main.py"]