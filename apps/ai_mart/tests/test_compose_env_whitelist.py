"""Biến settings của ai_mart PHẢI có mặt trong anchor `x-backend-env` của compose.

Anchor đó là WHITELIST: biến không khai ở đấy KHÔNG BAO GIỜ vào container. Đặt
`AI_CHAT_EXTRACT_ENABLED=1` trong `.env` mà thiếu dòng khai thì settings lấy
default 0, `run_batch()` thoát ngay, hàng đợi đứng im — trong khi task vẫn báo
thành công và `AiChatExtractSilent` (chỉ canh beat/worker còn sống) KHÔNG kêu.

Đo thật khi deploy prod 05/09/2026: 60.435 dòng `pending`, 0 dòng `done`, không
alert nào nổ. Cùng vết đã xảy ra trước đó với `GEMINI_EXTRACT_*` — comment cảnh
báo trong chính compose file. Comment không chặn được lỗi tái diễn, test thì có.
"""

from pathlib import Path

import pytest
from django.test import SimpleTestCase

# Biến mà ai_mart cần ở phía container (worker-slow chạy task, web chạy lệnh tay).
REQUIRED_ENV = (
    "CHAT_CRAWLER_DB_HOST",
    "CHAT_CRAWLER_DB_PORT",
    "CHAT_CRAWLER_DB_NAME",
    "CHAT_CRAWLER_DB_USER",
    "CHAT_CRAWLER_DB_PASSWORD",
    "AI_CHAT_EXTRACT_ENABLED",
    "AI_CHAT_EXTRACT_BATCH",
    "AI_CHAT_EXTRACT_MAX_ATTEMPTS",
    "AI_CHAT_EXTRACT_MIN_CHARS",
    "AI_CHAT_EXTRACT_MAX_CHARS",
)

# Mật khẩu DB crawler nằm trong STAGING_FORBIDDEN_OUTBOUND_ENVS — staging cầm nó
# là đọc được hội thoại khách thật, nên CỐ Ý không khai ở file staging.
FORBIDDEN_IN_STAGING = ("CHAT_CRAWLER_DB_PASSWORD",)

_ROOT = Path(__file__).resolve().parents[4]


class ComposeEnvWhitelistTests(SimpleTestCase):
    def _text(self, name: str) -> str:
        path = _ROOT / name
        if not path.is_file():
            # Chỉ mount backend/ (chạy test trong container theo docs/superpowers/plans)
            # thì không có compose file ở thư mục cha — bỏ qua thay vì đỏ sai chỗ.
            # Trong CI (repo đầy đủ) test vẫn chạy thật.
            pytest.skip(f"không thấy {path} — cần mount cả repo (audit 2026-09-23 C9)")
        return path.read_text(encoding="utf-8")

    def test_prod_compose_declares_every_required_env(self):
        text = self._text("docker-compose.prod.yml")
        missing = [v for v in REQUIRED_ENV if f"{v}:" not in text]
        self.assertEqual(missing, [], f"docker-compose.prod.yml thiếu: {missing}")

    def test_local_compose_declares_every_required_env(self):
        text = self._text("docker-compose.local.yml")
        missing = [v for v in REQUIRED_ENV if f"{v}:" not in text]
        self.assertEqual(missing, [], f"docker-compose.local.yml thiếu: {missing}")

    def test_staging_compose_never_holds_the_crawler_password(self):
        text = self._text("docker-compose.staging.yml")
        leaked = [v for v in FORBIDDEN_IN_STAGING if v in text]
        self.assertEqual(leaked, [], f"staging KHÔNG được cầm: {leaked}")
