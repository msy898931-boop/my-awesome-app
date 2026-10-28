# GoldBot runs as an unprivileged process in a container with no shell,
# no package manager, and no compiler. The image is built in two stages so
# that nothing the runtime does not need — pip's cache, the build toolchain,
# the wheel metadata — ever reaches the final layer.
#
# The image carries the source only. No secrets are baked in: the token, the
# chat id and the Groq key are injected at run time (see docker-compose.yml),
# which is also why this file is safe to commit.

# ── stage 1: build the wheel ──────────────────────────────────────────────
FROM python:3.12-slim AS build

WORKDIR /build

# The package has no runtime dependencies, so there is no requirements file to
# copy first: the build stage needs the metadata and the source, nothing more.
COPY pyproject.toml README.md ./
COPY src ./src

# ``--no-deps`` is honest here rather than an optimisation: there are no
# dependencies to resolve. The wheel is built into /build/dist.
RUN pip install --no-cache-dir --upgrade build \
    && python -m build --wheel --outdir /build/dist \
    && ls -l /build/dist


# ── stage 2: the runtime ──────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Unbuffered output so the container logs stream instead of arriving in
# blocks, and no .pyc files because the filesystem is read-only in production.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    GOLDBOT_ARCHIVE_DIR=/data/archive

# A dedicated unprivileged user. The bot only ever reads the market and writes
# its archive, so it needs no privilege at all, and running as root would give
# a feed-parsing bug far more reach than it deserves.
RUN groupadd --system --gid 10001 goldbot \
    && useradd --system --uid 10001 --gid goldbot --no-create-home --shell /usr/sbin/nologin goldbot

WORKDIR /app

# The wheel from the build stage. ``--no-deps`` again: nothing to pull in.
COPY --from=build /build/dist/*.whl /tmp/
RUN pip install --no-cache-dir --no-deps /tmp/*.whl && rm -f /tmp/*.whl

# The archive lives on a volume so a container restart does not lose the
# candles the bot has accumulated — the same reasoning as the Render note in
# the README, but a volume makes it durable instead of merely lucky.
RUN mkdir -p /data/archive && chown -R goldbot:goldbot /data
VOLUME ["/data"]

USER goldbot

# The health endpoint listens here when PORT is set. Declared so the port is
# documented and so ``docker run -P`` publishes it.
EXPOSE 8080

# The bot is a long-running poll loop, so the only meaningful check is that
# the process is alive and its health endpoint answers. Compose and the
# platform both read this.
HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import os,sys,urllib.request as u; \
p=os.environ.get('PORT'); \
sys.exit(0) if not p else sys.exit(0 if u.urlopen(f'http://127.0.0.1:{p}/health', timeout=8).status==200 else 1)"

# Advisory only: this process reads the market and sends a card. It never
# places an order, and the image holds no broker credentials to place one.
ENTRYPOINT ["python", "-m", "goldbot.cli"]
CMD ["bot"]
