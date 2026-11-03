#!/bin/sh
# ─────────────────────────────────────────────────────────────────────────────
# Kiểm tra compose render ra ĐÚNG giá trị cho container, không cần daemon.
#
# VÌ SAO TỒN TẠI: 2026-07-30 23:33 prod chết toàn bộ vì anchor `x-backend-env`
# passthrough `${POSTGRES_HOST:-postgres}`. Biến đó trên host mang góc nhìn HOST
# (`localhost`, cho psql/backup) nên rò vào container → mọi service boot fail với
# `connection to server at "127.0.0.1", port 5432 failed: Connection refused`.
# CI lúc đó xanh 8/8 vì KHÔNG job nào render compose — lớp lỗi này trượt thẳng
# xuống prod. Xem docs/runbooks/2026-07-24-pgbouncer-cutover.md.
#
# Chạy local trước khi sửa compose:  sh scripts/check-compose-render.sh
# ─────────────────────────────────────────────────────────────────────────────
set -eu

FILES="docker-compose.prod.yml docker-compose.staging.yml"
FAILED=0

# Gán giá trị giả cho mọi biến BẮT BUỘC `${VAR:?...}` để config render được.
# Quét động chứ không liệt kê tay — thêm biến bắt buộc mới không làm script mục.
stub_required_vars() {
  for v in $(grep -oE '\$\{[A-Z_0-9]+:\?' "$1" | sed 's/\${//; s/:?//' | sort -u); do
    export "$v=ci-dummy"
  done
}

# Đọc giá trị một biến env của service `web` trong output đã render.
rendered_env() {
  docker compose -f "$1" --env-file /dev/null config 2>/dev/null |
    grep -E "^      $2:" | head -1 | sed "s/^      $2: *//; s/^\"//; s/\"$//"
}

expect() {
  if [ "$2" = "$3" ]; then
    echo "  ok   $1 = $2"
  else
    echo "  FAIL $1 = '$2' (mong đợi '$3')"
    FAILED=1
  fi
}

for f in $FILES; do
  echo "== $f"
  stub_required_vars "$f"

  # 1) Render được (bắt lỗi cú pháp / thiếu biến bắt buộc).
  if ! docker compose -f "$f" --env-file /dev/null config >/dev/null 2>/tmp/compose-err; then
    echo "  FAIL không render được:"
    sed 's/^/       /' /tmp/compose-err
    FAILED=1
    continue
  fi

  # 2) Giá trị "góc nhìn host" KHÔNG được rò vào container. Cố tình đặt
  #    POSTGRES_HOST/PORT sai như trên máy thật rồi khẳng định container vẫn
  #    trỏ service `postgres:5432`.
  POSTGRES_HOST=localhost POSTGRES_PORT=15432
  export POSTGRES_HOST POSTGRES_PORT
  expect "POSTGRES_HOST (khi host đặt localhost)" "$(rendered_env "$f" POSTGRES_HOST)" "postgres"
  expect "POSTGRES_PORT (khi host đặt 15432)" "$(rendered_env "$f" POSTGRES_PORT)" "5432"

  # 3) Cutover PgBouncer vẫn phải đổi được — nếu không thì runbook thành vô dụng
  #    (đúng lỗi mà d4762497 sinh ra khi hai giá trị này bị hardcode).
  DB_TARGET_HOST=pgbouncer DB_TARGET_PORT=6432
  export DB_TARGET_HOST DB_TARGET_PORT
  expect "POSTGRES_HOST (cutover)" "$(rendered_env "$f" POSTGRES_HOST)" "pgbouncer"
  expect "POSTGRES_PORT (cutover)" "$(rendered_env "$f" POSTGRES_PORT)" "6432"
  unset DB_TARGET_HOST DB_TARGET_PORT
done

if [ "$FAILED" -ne 0 ]; then
  echo
  echo "Compose render sai — ĐỪNG merge. Biến nào host và container hiểu khác nhau"
  echo "thì phải tách tên (vd DB_TARGET_HOST), đừng passthrough \${POSTGRES_HOST}."
  exit 1
fi

echo
echo "Compose render đúng."
