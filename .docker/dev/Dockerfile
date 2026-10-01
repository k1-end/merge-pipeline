FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=config.settings.development

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    default-libmysqlclient-dev \
    pkg-config \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/ /app/requirements/
RUN pip install --upgrade pip \
    && pip install -r requirements/development.txt

ARG OPENCODE_VERSION=2.0.21
RUN curl -fsSL https://opencode.ai/v2/install | bash -s -- --version "${OPENCODE_VERSION}" --no-modify-path \
    && mv /root/.opencode/bin/opencode /usr/local/bin/opencode \
    && rm -rf /root/.opencode

COPY . /app/

EXPOSE 8000

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
