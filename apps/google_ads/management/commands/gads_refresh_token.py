"""Lấy Google Ads OAuth refresh token (chạy 1 lần, tương tác qua terminal).

Dùng cho OAuth client loại **Desktop app**: luồng copy-paste authorization code
với redirect_uri = http://localhost (không cần chạy local server).

Cách dùng:
    python manage.py gads_refresh_token \
        --client-id="xxx.apps.googleusercontent.com" \
        --client-secret="yyy"

Lệnh in ra một URL. Mở URL bằng trình duyệt, đăng nhập đúng email đã tạo MCC,
bỏ qua cảnh báo "unverified" (Advanced -> Go to app), đồng ý quyền. Trình duyệt
sẽ chuyển tới http://localhost/?code=... (trang báo lỗi không tải được là bình
thường) -- copy giá trị `code` trên thanh địa chỉ, dán lại vào terminal.
"""

from django.core.management.base import BaseCommand, CommandError

ADWORDS_SCOPE = "https://www.googleapis.com/auth/adwords"


class Command(BaseCommand):
    help = "Sinh Google Ads OAuth refresh token (Desktop app, luồng copy-paste code)."

    def add_arguments(self, parser):
        parser.add_argument("--client-id", required=True, help="OAuth Client ID (Desktop app).")
        parser.add_argument("--client-secret", required=True, help="OAuth Client Secret.")

    def handle(self, *args, **options):
        try:
            from google_auth_oauthlib.flow import Flow
        except ImportError as exc:  # pragma: no cover
            raise CommandError(
                "Thiếu thư viện google-auth-oauthlib (đi kèm google-ads)."
            ) from exc

        client_config = {
            "installed": {
                "client_id": options["client_id"],
                "client_secret": options["client_secret"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        }
        flow = Flow.from_client_config(
            client_config,
            scopes=[ADWORDS_SCOPE],
            redirect_uri="http://localhost",
        )
        # access_type=offline + prompt=consent => luôn trả refresh_token.
        auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")

        self.stdout.write("")
        self.stdout.write("1) Mở URL sau bằng trình duyệt (đăng nhập email đã tạo MCC):")
        self.stdout.write("")
        self.stdout.write(auth_url)
        self.stdout.write("")
        self.stdout.write(
            "2) Sau khi đồng ý, trình duyệt nhảy tới http://localhost/?code=... "
            "(trang lỗi là bình thường)."
        )
        self.stdout.write("   Copy giá trị `code` trên thanh địa chỉ rồi dán vào đây.")
        self.stdout.write("")

        raw = input("Dán authorization code (hoặc cả URL localhost): ").strip()
        code = self._extract_code(raw)
        if not code:
            raise CommandError("Không đọc được authorization code.")

        try:
            flow.fetch_token(code=code)
        except Exception as exc:
            raise CommandError(f"Đổi code lấy token thất bại: {exc}") from exc

        refresh_token = flow.credentials.refresh_token
        if not refresh_token:
            raise CommandError(
                "Không nhận được refresh_token. Thử lại và đảm bảo prompt=consent "
                "(thu hồi quyền cũ tại https://myaccount.google.com/permissions rồi chạy lại)."
            )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("REFRESH TOKEN ="))
        self.stdout.write(refresh_token)
        self.stdout.write("")
        self.stdout.write("Dán giá trị này vào trường 'Refresh Token' khi thêm TK Google Ads.")

    @staticmethod
    def _extract_code(raw: str) -> str:
        """Chấp nhận cả khi người dùng dán nguyên URL localhost lẫn chỉ mã code."""
        if raw.startswith("http"):
            from urllib.parse import parse_qs, urlparse

            qs = parse_qs(urlparse(raw).query)
            return (qs.get("code") or [""])[0]
        return raw
