# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base

# Unbuffered logs reach `docker logs` immediately; no .pyc files in the image.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    GEMINI_WEB2API_HOST=0.0.0.0 \
    GEMINI_WEB2API_PORT=8081

WORKDIR /app

# Installed first so the dependency layer is cached across code-only changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY gemini_web2api/ ./gemini_web2api/
COPY README.md LICENSE ./

# Run as an unprivileged user. The server needs no write access to /app.
RUN groupadd --gid 10001 gemini \
    && useradd --uid 10001 --gid gemini --shell /usr/sbin/nologin --create-home gemini \
    && mkdir -p /data \
    && chown -R gemini:gemini /data
USER gemini

# Config comes from a mount or the environment. Nothing is baked in: copying
# config.example.json here used to ship the image with the public key
# "sk-gemini", which looked like authentication but was not.
VOLUME ["/data"]

EXPOSE 8081

# Liveness probe. Uses the interpreter rather than curl/wget — neither is
# present in python:slim, and adding them would only grow the image.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "/app/gemini_web2api/_healthcheck.py"]

# dumb-init is not available in slim; Python handles SIGTERM directly.
ENTRYPOINT ["python", "-m", "gemini_web2api"]
CMD ["--host", "0.0.0.0", "--port", "8081"]
