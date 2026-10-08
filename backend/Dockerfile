# syntax=docker/dockerfile:1.7
# One image, two roles: the API (default CMD) and the worker (`python -m app.worker`).
# GDAL, GEOS and PROJ come bundled in the pyogrio / shapely / pyproj wheels - no system geo libraries needed.

ARG PYTHON_VERSION=3.13

FROM python:${PYTHON_VERSION}-slim AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
# Optional PyPI mirror for restricted networks (e.g. --build-arg PIP_INDEX_URL=https://mirror/simple).
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL}
WORKDIR /src
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
# Dependencies first (cached layer), pinned to the versions the test suite ran against.
COPY pyproject.toml constraints.txt README.md ./
RUN mkdir app && touch app/__init__.py \
    && pip install --no-compile -c constraints.txt . \
    && pip uninstall -y geomeasure
COPY app ./app
RUN pip install --no-deps --no-compile .

FROM python:${PYTHON_VERSION}-slim AS runtime
LABEL org.opencontainers.image.title="geomeasure-api" \
      org.opencontainers.image.description="CRS-aware geospatial file measurement API and worker" \
      org.opencontainers.image.licenses="MIT"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    LOG_FORMAT=json \
    STORAGE_LOCAL_ROOT=/data/storage
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /data/storage && chown -R app:app /data
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]
# --proxy-headers: trust X-Forwarded-* from the load balancer in front of the container.
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--no-access-log"]
