# AgentCore Runtime container — ARM64 (Graviton) is MANDATORY; x86 images will not start.
# Base per POC spec: astral uv on python3.12 bookworm-slim.
FROM --platform=linux/arm64 ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

# Install deps first for layer caching.
COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt

# App code.
COPY agent/ ./agent/

# Run as non-root.
RUN useradd -r -u 1001 appuser && chown -R appuser /app
USER appuser

EXPOSE 8080
# BedrockAgentCoreApp serves POST /invocations and GET /ping on 0.0.0.0:8080.
# Binds 0.0.0.0 for AgentCore internal routing only — never exposed directly to the internet.
CMD ["python", "-m", "agent.main"]
