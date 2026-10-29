FROM python:3.12.12-slim-bookworm AS build
ENV VIRTUAL_ENV=/opt/venv
RUN python -m venv /opt/venv && /usr/local/bin/pip install --no-cache-dir uv==0.13.6
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --active
COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable --active

FROM python:3.12.12-slim-bookworm
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PHOBOS_EXTENSION_ROOT=/opt/phobos-extensions
RUN groupadd --gid 10001 phobos && useradd --uid 10001 --gid phobos --create-home phobos \
    && mkdir -p /data /workspace && chown phobos:phobos /data /workspace
COPY --from=build /opt/venv /opt/venv
COPY hermes/plugins /opt/phobos-extensions/hermes/plugins
COPY skills /opt/phobos-extensions/skills
USER 10001:10001
WORKDIR /app
EXPOSE 8080
ENTRYPOINT ["phobos"]
CMD ["serve"]
