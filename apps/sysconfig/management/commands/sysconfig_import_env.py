"""Chuyển giá trị từ biến môi trường vào bảng system_settings (chạy MỘT lần khi deploy).

    manage.py sysconfig_import_env            # chỉ in diff
    manage.py sysconfig_import_env --apply    # ghi khóa DB chưa có
    manage.py sysconfig_import_env --apply --force   # ghi đè cả khóa DB đã có

Quy trình deploy: docs/system-settings.md. Lệnh chỉ đụng khóa có trong registry.
Giá trị env không ép kiểu được (vd bool "maybe") → dòng đó in `LỖI: …` và bị bỏ qua;
với --apply các khóa hợp lệ VẪN được ghi, rồi lệnh thoát mã 1 (CommandError) để
người deploy không bỏ sót.
"""

import os

from django.core.management.base import BaseCommand, CommandError

from apps.sysconfig import registry
from apps.sysconfig.models import SystemSetting
from apps.sysconfig.registry import coerce
from apps.sysconfig.service import canonical_raw, set_many


class Command(BaseCommand):
    help = "Import giá trị cài đặt hệ thống từ env vào DB (dry-run mặc định)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Ghi vào DB (mặc định chỉ in diff).")
        parser.add_argument("--force", action="store_true", help="Ghi đè khóa DB đã có giá trị khác.")

    def handle(self, *args, **options):
        existing = {
            r.key: (r.secret_value if registry.BY_KEY[r.key].secret else r.value)
            for r in SystemSetting.objects.filter(key__in=registry.BY_KEY)
        }
        to_write: dict[str, str] = {}
        invalid: dict[str, str] = {}
        self.stdout.write(f"{'KEY':45} {'ENV':<32} {'DB':<32}  HÀNH ĐỘNG")
        for spec in registry.SETTINGS:
            env_raw = (os.environ.get(spec.key) or "").strip()
            db_raw = existing.get(spec.key)
            shown_env = ("***" if spec.secret else env_raw) if env_raw else "-"
            shown_db = ("***" if spec.secret else db_raw) if db_raw else "-"
            if not env_raw:
                action = "bỏ qua (env trống)"
            else:
                try:
                    env_val = coerce(spec, env_raw)
                except ValueError as exc:
                    invalid[spec.key] = str(exc)
                    action = f"LỖI: {exc}"
                    self.stdout.write(f"{spec.key:45} {shown_env:<32} {str(shown_db):<32}  {action}")
                    continue
                env_store = canonical_raw(spec, env_val, env_raw)
                if db_raw is None:
                    action = "TẠO"
                    to_write[spec.key] = env_store
                elif db_raw == env_store or _same_value(spec, db_raw, env_val):
                    action = "giống nhau"
                elif options["force"]:
                    action = "GHI ĐÈ (--force)"
                    to_write[spec.key] = env_store
                else:
                    action = "KHÁC — giữ DB (thêm --force để ghi đè)"
            self.stdout.write(f"{spec.key:45} {shown_env:<32} {str(shown_db):<32}  {action}")

        if invalid:
            self.stdout.write(self.style.WARNING(
                f"\nCẢNH BÁO: {len(invalid)} khóa có giá trị env không hợp lệ, bị bỏ qua: "
                + ", ".join(sorted(invalid))
            ))
        if not options["apply"]:
            self.stdout.write(self.style.WARNING(f"\nDry-run: {len(to_write)} khóa sẽ ghi. Thêm --apply để ghi."))
            return
        if to_write:
            set_many(to_write, user=None)
        self.stdout.write(self.style.SUCCESS(f"\nĐã ghi {len(to_write)} khóa."))
        if invalid:
            raise CommandError(
                f"Đã ghi {len(to_write)} khóa hợp lệ nhưng {len(invalid)} khóa bị bỏ qua vì env "
                "không hợp lệ (xem LỖI ở trên) — sửa .env rồi chạy lại cho các khóa đó."
            )


def _same_value(spec: registry.Setting, db_raw: str, env_val) -> bool:
    """DB "1" vs env "true": cùng giá trị sau ép kiểu thì không phải KHÁC."""
    try:
        return coerce(spec, db_raw) == env_val
    except ValueError:
        return False
