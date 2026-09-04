# Sage FastAPI backend (text2sql). This image serves the API only; the Next.js frontend
# and the offline KB-build scripts (preprocessing/) are out of scope and shipped separately.
#
# Runtime config is injected via environment variables — NOT baked in. At minimum the read-only
# Postgres vars are required for startup (the connection pool is built eagerly in the app's
# lifespan); Bedrock/embedding vars are needed for the agent route but tolerated-absent at boot.
# See CLAUDE.md "Environment Variables". Build:  docker build -t sage-api .
# Run:  docker run -p 8000:8000 --env-file .env sage-api

FROM python:3.12-slim

# - PYTHONDONTWRITEBYTECODE: no .pyc files (smaller, read-only-friendly)
# - PYTHONUNBUFFERED: stream logs straight to the container's stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

# Dependencies first so this layer is cached across code changes. psycopg2-binary bundles libpq,
# so no system packages are needed.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code. prompt_loader reads prompts/prompts.md relative to the repo root, so the
# prompts/ directory must sit next to the text2sql/ package. bedrock_session.py is a top-level
# module (imported as `import bedrock_session`), so it lives at the repo root, not inside the package.
COPY text2sql/ ./text2sql/
COPY prompts/ ./prompts/
COPY bedrock_session.py ./

# Drop root — nothing here needs elevated privileges at runtime.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# The API exposes an unauthenticated liveness probe.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8000'), timeout=4).status==200 else sys.exit(1)"

# Bind the module-level `app` (create_app() output) on 0.0.0.0 so it's reachable outside the
# container — the CLI entrypoint defaults to 127.0.0.1, which would be unreachable. JSON/exec form
# keeps signals clean: `exec` makes uvicorn PID 1 (direct SIGTERM for graceful shutdown) while the
# `sh -c` wrapper still expands ${PORT}.
CMD ["sh", "-c", "exec uvicorn text2sql.api:app --host 0.0.0.0 --port ${PORT}"]
