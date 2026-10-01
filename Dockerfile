FROM ghcr.io/astral-sh/uv:0.12.5@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1 AS uv

FROM python:3.13-slim-trixie@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.13-slim-trixie@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 DATA_DIR=/data
COPY --from=build /app/.venv /app/.venv
RUN mkdir /data && chown 1000:1000 /data
USER 1000:1000
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --start-period=60s \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]
CMD ["posteryard", "serve"]
