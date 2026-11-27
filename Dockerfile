FROM ghcr.io/astral-sh/uv:0.13.10@sha256:a3ea1357342c1a4bf9a0c53a3deb1b55379aefa037338d8113d5771efa01774f AS uv

FROM python:3.13-slim-trixie@sha256:7249d5587b78209a006dc4a3b5340678f84ea729239193aa559962cd8c1fce50 AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.13-slim-trixie@sha256:7249d5587b78209a006dc4a3b5340678f84ea729239193aa559962cd8c1fce50
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 DATA_DIR=/data MALLOC_ARENA_MAX=2
RUN apt-get update && apt-get install -y --no-install-recommends libfribidi0 libjemalloc2 && rm -rf /var/lib/apt/lists/* \
  && ln -s /usr/lib/$(uname -m)-linux-gnu/libjemalloc.so.2 /usr/local/lib/libjemalloc.so.2
ENV LD_PRELOAD=/usr/local/lib/libjemalloc.so.2 MALLOC_CONF=narenas:2,background_thread:true
COPY --from=build /app/.venv /app/.venv
RUN python -c "from PIL import features; assert features.check('raqm'), 'Pillow cannot shape text without libfribidi'" \
  && python -c "assert 'jemalloc' in open('/proc/self/maps').read(), 'jemalloc is not loaded'" \
  && mkdir /data && chown 1000:1000 /data
LABEL org.opencontainers.image.title=Posteryard \
  org.opencontainers.image.description="Clean, consistent posters for Plex, Jellyfin and Emby, picked, rendered and kept current automatically" \
  org.opencontainers.image.url=https://posteryard.sandobserver.com \
  org.opencontainers.image.source=https://github.com/SandObserver/Posteryard \
  org.opencontainers.image.licenses=Apache-2.0
USER 1000:1000
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --start-period=60s \
  CMD ["posteryard", "health"]
CMD ["posteryard", "serve"]
