#!/bin/sh
# ─────────────────────────────────────────────────────────────────────────────
# Hàng rào kiến trúc backend: contract phân tầng import + ruff + ratchet kích thước
# code/nợ ruff (scripts/arch_baseline.py, audit 2026-09-23 C3).
#
# VÌ SAO TỒN TẠI: `backend/.importlinter` khai hợp đồng phân tầng giữa các app từ
# 03/07 nhưng CHƯA TỪNG có gì chạy nó. Hệ quả đo được ngày 06/08:
#   • Commit 8766b9a1 trả nợ `orders → kiotviet` bằng Port/Adapter bị revert diện
#     rộng ở 26c1fa82 cùng ngày 30/07 — không ai phát hiện.
#   • Entry `orders.models -> kiotviet.models` được chú thích là "nợ lớn nhất"
#     thực chất là import chết từ lâu, không ai biết vì không có gì soi.
# Lộ trình trả nợ: docs/import-layering-debt-paydown-2026-08.md
#
# Chạy tay:  sh scripts/check-backend-arch.sh
# Tự động:   .githooks/pre-push (bật bằng `git config core.hooksPath .githooks`;
#            trước 2026-09-23 trỏ vào frontend/.husky/pre-push — thư mục đó không
#            tồn tại nên hook chưa từng chạy, audit N1).
#
# GIỚI HẠN — ĐỌC KỸ: đây là GỜ GIẢM TỐC, KHÔNG PHẢI CỔNG. Hook bypass được bằng
# `git push --no-verify`. Cổng thật phải là CI. Đừng nhầm script này với một sự bảo đảm.
# ─────────────────────────────────────────────────────────────────────────────
set -eu

# .importlinter chứa ký tự box-drawing UTF-8; trên Windows python mặc định đọc
# bằng codepage hệ thống → lint-imports crash 'charmap' codec. Ép UTF-8 mọi nhánh.
export PYTHONUTF8=1

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
BACKEND="$REPO_ROOT/backend"
LINT_IMAGE="desi-lint:local"

# Git Bash/MSYS bẻ đường dẫn kiểu unix khi truyền cho docker.exe. cygpath đổi sang
# dạng Windows; MSYS_NO_PATHCONV giữ nguyên đường dẫn phía trong container.
to_host_path() {
    if command -v cygpath >/dev/null 2>&1; then
        cygpath -w "$1"
    else
        printf '%s' "$1"
    fi
}

# ── Nhánh 1: công cụ có sẵn trên PATH (nhanh nhất) ──────────────────────────
if command -v lint-imports >/dev/null 2>&1 && command -v ruff >/dev/null 2>&1; then
    echo "→ Kiểm tra kiến trúc backend (công cụ local)..."
    cd "$BACKEND"
    lint-imports
    ruff check .
    python3 "$REPO_ROOT/scripts/arch_baseline.py" --check
    echo "✓ Kiến trúc backend OK"
    exit 0
fi

# ── Nhánh 2: chạy qua Docker ────────────────────────────────────────────────
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    echo "→ Kiểm tra kiến trúc backend (qua Docker)..."

    # Build lại mỗi lần, nhưng layer cache làm nó gần như tức thời khi
    # requirements-dev.txt không đổi. Đổi phiên bản công cụ là image tự làm mới —
    # không cần ai nhớ xoá image cũ bằng tay.
    MSYS_NO_PATHCONV=1 docker build -q -t "$LINT_IMAGE" -f - "$(to_host_path "$BACKEND")" >/dev/null <<'DOCKERFILE'
FROM public.ecr.aws/docker/library/python:3.13-slim
WORKDIR /src
COPY requirements-dev.txt /tmp/requirements-dev.txt
RUN pip install --no-cache-dir -r /tmp/requirements-dev.txt
DOCKERFILE

    # Mount read-only: hook không được phép sinh rác trong cây làm việc. Do đó
    # grimp chạy --no-cache và ruff ghi cache ra /tmp.
    # Mount cả repo (scripts/arch_baseline.py cần backend/ lẫn scripts/arch-baseline.json).
    MSYS_NO_PATHCONV=1 docker run --rm \
        -v "$(to_host_path "$REPO_ROOT"):/repo:ro" \
        -w /repo/backend \
        -e RUFF_CACHE_DIR=/tmp/ruff \
        -e PYTHONUTF8=1 \
        "$LINT_IMAGE" \
        sh -c 'lint-imports --no-cache && ruff check . && python /repo/scripts/arch_baseline.py --check'

    echo "✓ Kiến trúc backend OK"
    exit 0
fi

# ── Nhánh 3: không có gì để chạy ────────────────────────────────────────────
# Cố tình thoát 0. Người không dựng được môi trường backend (vd chỉ làm frontend)
# không đáng bị chặn đường push.
echo "⚠ Bỏ qua kiểm tra kiến trúc backend: không thấy lint-imports/ruff lẫn Docker."
echo "  Cài bằng: pip install -r backend/requirements-dev.txt"
exit 0
