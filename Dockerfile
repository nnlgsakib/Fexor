# Fexor server image. Ships compiled Python only: no Fexor source code is in the final image.

# --- admin web app ---
# Built once on the build machine: the output is plain files, the same for every CPU type.
FROM --platform=$BUILDPLATFORM node:24-alpine AS web
WORKDIR /web
COPY web/admin/package.json web/admin/package-lock.json ./
RUN npm ci
COPY web/admin/ ./
RUN npm run build

# --- build: install, compile, strip source ---
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv FEXOR_MODEL_DIR=/opt/models
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY core/pyproject.toml core/pyproject.toml
COPY server/pyproject.toml server/pyproject.toml
COPY kiosk/pyproject.toml kiosk/pyproject.toml
RUN mkdir -p core/src/fexor_core server/src/fexor_server kiosk/src/fexor_kiosk && \
    uv sync --frozen --no-dev --package fexor-server --no-install-workspace
COPY core core
COPY server server
# --no-editable installs Fexor as normal packages (not links back to /src). Then each module is
# replaced by its compiled .pyc next to it, and the .py files are removed.
RUN uv sync --frozen --no-dev --no-editable --package fexor-server && \
    SP=$(/opt/venv/bin/python -c "import sysconfig; print(sysconfig.get_paths()['purelib'])") && \
    /opt/venv/bin/python -m compileall -q -b "$SP/fexor_core" "$SP/fexor_server" && \
    find "$SP/fexor_core" "$SP/fexor_server" -name "*.py" -delete && \
    find "$SP/fexor_core" "$SP/fexor_server" -name "__pycache__" -type d -prune -exec rm -rf {} + && \
    (/opt/venv/bin/python -m compileall -q "$SP" > /dev/null || true) && \
    /opt/venv/bin/fexor-server models && chmod -R a+rX /opt/models

# --- final image ---
FROM python:3.12-slim AS app
ENV PATH=/opt/venv/bin:$PATH FEXOR_MODEL_DIR=/opt/models FEXOR_WEB_DIR=/opt/web PYTHONUNBUFFERED=1
COPY --from=build /opt/venv /opt/venv
COPY --from=build /opt/models /opt/models
COPY --from=web /web/dist /opt/web
RUN useradd --system --uid 10001 fexor
USER fexor
WORKDIR /opt
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
CMD ["sh", "-c", "fexor-server migrate && fexor-server serve --workers ${FEXOR_WORKERS:-2}"]
