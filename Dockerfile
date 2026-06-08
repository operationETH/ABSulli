FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 absulli \
    && useradd --system --uid 10001 --gid absulli --home-dir /nonexistent --shell /usr/sbin/nologin absulli \
    && mkdir -p /config \
    && chown -R absulli:absulli /config

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=absulli:absulli . .

ARG ABSULLI_VERSION=0.0.0.dev0
ENV SETUPTOOLS_SCM_PRETEND_VERSION_FOR_ABSULLI=${ABSULLI_VERSION}
RUN pip install --no-cache-dir --no-deps . \
    && rm -rf /app/build /app/*.egg-info

USER absulli

EXPOSE 8272

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8272/healthz || exit 1

CMD ["uvicorn", "absulli.main:app", "--host", "0.0.0.0", "--port", "8272", "--no-server-header"]