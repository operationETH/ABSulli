FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1000 absulli \
    && useradd --system --uid 1000 --gid absulli --home-dir /nonexistent --shell /usr/sbin/nologin absulli \
    && mkdir -p /config \
    && chown -R absulli:absulli /config

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chmod=0755 . .
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

ARG ABSULLI_VERSION=0.0.0.dev0
ENV SETUPTOOLS_SCM_PRETEND_VERSION_FOR_ABSULLI=${ABSULLI_VERSION}
RUN pip install --no-cache-dir --no-deps . \
    && rm -rf /app/build /app/*.egg-info \
    && python -m compileall -q /app/absulli \
    && chmod +x /usr/local/bin/docker-entrypoint.sh

VOLUME /config

EXPOSE 8272

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c 'import os,urllib.request; urllib.request.urlopen("http://127.0.0.1:" + os.environ.get("ABSULLI_PORT", "8272") + "/healthz", timeout=4).close()'

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["uvicorn", "absulli.main:app", "--host", "0.0.0.0", "--port", "8272", "--no-server-header"]