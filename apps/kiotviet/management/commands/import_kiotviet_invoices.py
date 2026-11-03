"""CLI wrapper — business logic nằm ở
``apps.kiotviet.infrastructure.services.import_invoices_service.ImportInvoicesService``.
Command chỉ parse args rồi delegate (WP8.1 audit hardening)."""

from django.core.management.base import BaseCommand

from apps.kiotviet.infrastructure.services.import_invoices_service import ImportInvoicesService


class Command(BaseCommand):
    help = "Import full KiotViet invoices by pagination and upsert to orders."

    def add_arguments(self, parser):
        parser.add_argument("--channel-id", type=int, required=True, help="ID của KiotVietChannel")
        parser.add_argument(
            "--branch-ids",
            required=False,
            default="",
            help="Danh sách branch ids, phân tách bằng dấu phẩy (vd: 231174,231175). Bỏ qua khi dùng --invoice-code",
        )
        parser.add_argument(
            "--invoice-code",
            default="",
            help="Import 1 hoặc nhiều invoice theo code, phân tách bằng dấu phẩy (gọi /invoices/code/{code})",
        )
        parser.add_argument("--dry-run", action="store_true", help="Chỉ parse/map và log, không ghi DB")
        parser.add_argument(
            "--create-only",
            action="store_true",
            help="Chỉ tạo mới order, bỏ qua những invoice đã tồn tại",
        )
        parser.add_argument(
            "--list-missing",
            action="store_true",
            help="Chỉ liệt kê invoice có ở API nhưng chưa có trong DB, không ghi DB",
        )
        parser.add_argument(
            "--diff",
            action="store_true",
            help="So sánh 2 chiều: invoice có ở DB nhưng không ở KiotViet, và ngược lại",
        )
        parser.add_argument(
            "--update-status",
            action="store_true",
            help="Chỉ cập nhật invoice_status (từ invoice.status) và status (từ invoiceDelivery.status) cho order đã tồn tại",
        )
        parser.add_argument(
            "--update-phone",
            action="store_true",
            help="Lấy customerCode từ invoice, gọi API get customer theo code, cập nhật sdt vào order.phone",
        )
        parser.add_argument(
            "--resync-codes",
            action="store_true",
            help="Re-sync invoice_code, invoice_id, invoice_status, status cho order đã tồn tại; match theo order_code",
        )
        parser.add_argument(
            "--check-channel-match",
            action="store_true",
            help="Check & fix Order.channel theo saleChannelId của KV invoice; yêu cầu --branch-ids hoặc --invoice-code",
        )
        parser.add_argument(
            "--sale-channel-id",
            type=int,
            default=0,
            help="Channel.id (DB) để giới hạn scope: chỉ xử lý invoice có saleChannelId map về channel này",
        )
        parser.add_argument(
            "--only-current-channel-ids",
            dest="only_current_channel_ids",
            default="",
            help=(
                "Đi kèm --check-channel-match: chỉ sửa order đang thuộc các Channel.id này "
                "(vd: 22,23,29). Bỏ trống = sửa mọi order lệch kênh, kể cả order đã sửa tay."
            ),
        )
        parser.add_argument(
            "--empty-sale-channel-to",
            dest="empty_sale_channel_to",
            type=int,
            default=0,
            help=(
                "Đi kèm --check-channel-match: Channel.id nhận các order có saleChannelId RỖNG "
                "trên KiotViet (bán tại quầy). Order có saleChannelId nhưng thiếu mapping KHÔNG "
                "bị gán vào đây — chúng chỉ được liệt kê để khai báo mapping."
            ),
        )
        parser.add_argument(
            "--from",
            dest="from_dt",
            default="",
            help='Lọc theo purchaseDate từ, vd: "2026-04-01 00:00:00" hoặc "2026-04-01"',
        )
        parser.add_argument(
            "--to",
            dest="to_dt",
            default="",
            help='Lọc theo purchaseDate đến, vd: "2026-04-30 23:59:59" hoặc "2026-04-30"',
        )

    def handle(self, *args, **options):
        service = ImportInvoicesService(
            stdout=self.stdout,
            stderr=self.stderr,
            style=self.style,
        )
        service.run(**options)
