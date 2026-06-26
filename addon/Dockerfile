ARG BUILD_FROM
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/gateway \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    DEBIAN_FRONTEND=noninteractive

# Runtime: Python 3 + ffmpeg. Debian base = glibc, so sqlite-vec wheels work.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        ffmpeg \
        ca-certificates \
        tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && ln -sf /usr/bin/python3 /usr/local/bin/python \
 && ln -sf /usr/bin/pip3   /usr/local/bin/pip

WORKDIR /opt/gateway

# Stage gateway sources (populated by build-local.sh or CI before docker build).
COPY _build/core/                       /opt/gateway/core/
COPY _build/services/voice_gateway/     /opt/gateway/services/voice_gateway/
COPY _build/services/notify_gateway/    /opt/gateway/services/notify_gateway/
COPY _build/services/telegram_bot/      /opt/gateway/services/telegram_bot/

# Install Python deps. STT is always external in the addon — no faster-whisper.
RUN set -e \
 && pip install --no-cache-dir --upgrade pip wheel \
 && pip install --no-cache-dir \
        -r /opt/gateway/services/voice_gateway/requirements.txt \
        -r /opt/gateway/services/notify_gateway/requirements.txt \
        -r /opt/gateway/services/telegram_bot/requirements.txt

# s6-overlay service definitions + cont-init.
COPY rootfs/ /
RUN chmod +x \
        /etc/cont-init.d/*.sh \
        /etc/services.d/voice-gateway/run \
        /etc/services.d/notify-gateway/run \
        /etc/services.d/telegram-bot/run \
        /usr/lib/homeassistant-local-ai/export-env.sh

# /init from the HA base image starts s6-overlay; do not override.
