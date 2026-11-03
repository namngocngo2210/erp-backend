ARG PYTHON_BASE=public.ecr.aws/docker/library/python:3.13-slim

# ── Stage 1: builder — cài dependencies vào prefix riêng ──
FROM ${PYTHON_BASE} AS builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --prefix=/install -r /app/requirements.txt

# ── Stage 2: runtime — chỉ chứa deps đã cài + source, không pip cache/toolchain ──
FROM ${PYTHON_BASE}

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Tạo user không-đặc-quyền để container KHÔNG chạy bằng root.
RUN groupadd -r app && useradd -r -g app -d /app app

COPY --from=builder /install /usr/local

COPY . /app

# staticfiles sinh lúc runtime (collectstatic) → đảm bảo app sở hữu /app
RUN mkdir -p /app/staticfiles && chown -R app:app /app

USER app

# Migrate/collectstatic do compose command đảm nhiệm (docker-compose.local.yml /
# docker-compose.prod.yml là nguồn chân lý) — CMD chỉ là default tối thiểu.
CMD ["gunicorn", "config.asgi:application", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:8000"]
