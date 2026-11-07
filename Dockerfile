FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv

FROM python:3.13-slim-trixie@sha256:ae14d4ce8771606477a81446e7ef429edd6762305fc50c84507c1041fa8285d1 AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.13-slim-trixie@sha256:ae14d4ce8771606477a81446e7ef429edd6762305fc50c84507c1041fa8285d1
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 DATA_DIR=/data MALLOC_ARENA_MAX=2
RUN apt-get update && apt-get install -y --no-install-recommends libfribidi0 libjemalloc2 && rm -rf /var/lib/apt/lists/* \
  && ln -s /usr/lib/$(uname -m)-linux-gnu/libjemalloc.so.2 /usr/local/lib/libjemalloc.so.2
ENV LD_PRELOAD=/usr/local/lib/libjemalloc.so.2 MALLOC_CONF=narenas:2,background_thread:true
COPY --from=build /app/.venv /app/.venv
RUN python -c "from PIL import features; assert features.check('raqm'), 'Pillow cannot shape text without libfribidi'" \
  && python -c "assert 'jemalloc' in open('/proc/self/maps').read(), 'jemalloc is not loaded'" \
  && mkdir /data && chown 1000:1000 /data
USER 1000:1000
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --start-period=60s \
  CMD ["posteryard", "health"]
CMD ["posteryard", "serve"]
