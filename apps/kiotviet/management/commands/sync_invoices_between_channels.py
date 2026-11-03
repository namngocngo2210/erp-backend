"""
Đồng bộ hóa đơn giữa 2 KiotVietChannel khác retailer theo khoảng thời gian.

- Lấy danh sách hóa đơn từ kênh A (source) qua GET /invoices, lọc theo lastModified.
- Với mỗi hóa đơn, build payload và tạo hóa đơn mới ở kênh B (target) qua POST /orders
  (makeInvoice=True), giữ nguyên deliveryCode + partnerDelivery.name = "DVVC cho đơn DESI".
- Customer trên B: ưu tiên find_customer_by_phone, nếu có thì tái sử dụng mã KH cũ;
  nếu không có thì create_customer.

Ví dụ:
    python manage.py sync_invoices_between_channels \\
        --source-kiotviet-channel-id 1 --source-branch-id 231174 \\
        --target-kiotviet-channel-id 2 --target-branch-id 415922 \\
        --from "2026-04-01 00:00:00" --to "2026-04-09 23:59:59"

LỊCH SỬ HỢP NHẤT (BE-1 audit 2026-07): file này từng là monolith tự chứa ~2.800 dòng
(revert bc2dd1e5 về snapshot 12/05/2026 trong đợt golang) chạy SONG SONG với
``SyncInvoicesService`` — mọi bugfix tiền/hóa đơn phải sửa 2 nơi. Sau khi có
characterization tests ghim hành vi CLI (test_sync_between_channels_command.py),
command được rút về CLI adapter mỏng: parse args → ``SyncInvoicesService.run()``.

Các khác biệt của bản monolith và cách xử lý (đối chiếu từng mục khi review):
- Cancelled numeric ``status == 5``: là REGRESSION do revert nguyên snapshot —
  e59650dc (28/05) đã chốt 2 = "Đã hủy", 5 = "Không giao được" ở CẢ command lẫn
  service. Hợp nhất lấy ``== 2`` của service (check statusValue dạng chuỗi giữ nguyên).
- List qua web API ``POST /api/invoices/list``: engine chung dùng public
  ``GET /invoices`` (orderBy=id) — đã verify tương đương tập đơn và fix bug phân trang
  lặp/sót (xem docstring ``SyncInvoicesService._web_list_invoices``).
- ``--update-delivery-detail`` chỉ lệch GIÁ ship: engine chung PUT giá 1 lần
  (980e8602) thay vì delete+resync — delete+resync không sửa được giá và phá payments.
- ``--check-price``: chỉ so ``total`` (bỏ so discountAfterTax) — builder chung sau
  a782572d giữ giảm giá theo dòng nên A/B cố tình lệch các field discount dù total khớp.
- ``--update-status``: mode CLI-only, được port vào service (``_update_status``,
  từng bị gỡ ở c7ed08fb khi 2 stack tách đôi).
- ``exact_mirror``: CLI truyền False cứng — bản command lịch sử không đọc
  RECONCILE_DELIVERY_EXACT_MIRROR, giữ nguyên hành vi patch-status của CLI.

Cờ fallback ``--legacy`` + module ``_sync_invoices_legacy.py`` (2 835 dòng) đã XOÁ
2026-09-23 (audit C2): monolith sống hơn 1 tháng sau hạn "1 release" và vẫn bị vá
song song (68380b98). Lịch sử đối chiếu ở docs/runbooks/sync-invoices-unfork.md.
"""
from django.core.management.base import BaseCommand, CommandError

from apps.kiotviet.infrastructure.services._sync_service_shared import SyncError
from apps.kiotviet.infrastructure.services.sync_invoices_service import (
    SyncInvoicesService,
)


class _CliSyncInvoicesService(SyncInvoicesService):
    """Adapter output: log_callback → stdout/stderr của BaseCommand.

    Mất màu ``self.style`` của monolith cũ (cosmetic — text giữ nguyên);
    ``SyncError`` được dịch thành ``CommandError`` ở ``Command.handle``.
    """

    def __init__(self, *, stdout, stderr):
        self._cli_stdout = stdout
        self._cli_stderr = stderr
        super().__init__(log_callback=self._emit)

    def _emit(self, channel: str, msg: str) -> None:
        target = self._cli_stderr if channel == "stderr" else self._cli_stdout
        target.write(msg)


class Command(BaseCommand):
    help = (
        "Đồng bộ hóa đơn giữa 2 KiotVietChannel theo khoảng lastModified. "
        "Lấy hóa đơn từ source rồi tạo lại ở target."
    )

    def add_arguments(self, parser):
        parser.add_argument("--source-kiotviet-channel-id", type=int, required=True, help="ID của KiotVietChannel nguồn (A)")
        parser.add_argument("--source-branch-id", required=True, help="branchId trong A để fetch hóa đơn")
        parser.add_argument("--target-kiotviet-channel-id", type=int, required=True, help="ID của KiotVietChannel đích (B)")
        parser.add_argument("--target-branch-id", required=True, help="branchId trong B để tạo hóa đơn")
        parser.add_argument(
            "--from",
            dest="from_dt",
            required=True,
            help='purchaseDate from (client-side filter, khớp với cột "Thời gian" trên web), vd: "2026-04-01 00:00:00"',
        )
        parser.add_argument(
            "--to",
            dest="to_dt",
            required=False,
            help='purchaseDate to, vd: "2026-04-30 23:59:59"',
        )
        parser.add_argument(
            "--page-size",
            type=int,
            default=100,
            help="Số hóa đơn mỗi trang. Public API KiotViet chặn trần 100 — giá trị lớn hơn bị kẹp về 100.",
        )
        parser.add_argument("--limit", type=int, default=0, help="Giới hạn tổng số hóa đơn xử lý (0 = không giới hạn)")
        parser.add_argument("--sleep-between-pages", type=float, default=0.5)
        parser.add_argument("--sleep-between-pushes", type=float, default=0.3)
        parser.add_argument("--dry-run", action="store_true", help="Chỉ build payload và log, không tạo đơn bên B")
        parser.add_argument(
            "--invoice-codes",
            required=False,
            help="Chỉ sync các invoice cụ thể (phân tách bằng dấu phẩy, vd: HD003451.01,HD003452). Bỏ qua web list, fetch trực tiếp từ public API.",
        )
        parser.add_argument(
            "--target-invoice-codes",
            required=False,
            help=(
                "Filter theo TARGET (B) invoice code (phân tách dấu phẩy, vd: HD053672,HD053673). "
                "Hiện chỉ hỗ trợ kèm --update-status: scan B, lấy đơn match B code, "
                "suy ra source code từ description rồi update."
            ),
        )
        parser.add_argument(
            "--check-missing",
            action="store_true",
            help="So sánh danh sách A vs B, in ra các invoice code bên A chưa có bên B. Không tạo đơn.",
        )
        parser.add_argument(
            "--check-price",
            action="store_true",
            help="So sánh giá (total) giữa A và B, in ra các invoice bị lệch giá. Không tạo/sửa đơn.",
        )
        parser.add_argument(
            "--check-status",
            action="store_true",
            help="So sánh invoice status + delivery status giữa A và B, in ra các invoice bị lệch. Không tạo/sửa đơn.",
        )
        parser.add_argument(
            "--update-status",
            action="store_true",
            help=(
                "Update invoice/delivery status bên B cho khớp A. "
                "Dùng sau --check-status. Lưu ý: KiotViet không cho revert từ 'Hoàn thành' về 'Đang xử lý'."
            ),
        )
        parser.add_argument(
            "--compare-invoice",
            required=False,
            help="So sánh chi tiết 1 source invoice code giữa A và B (fetch public API cả 2 bên).",
        )
        parser.add_argument(
            "--purge-target",
            action="store_true",
            help="Trước khi sync, xóa tất cả hóa đơn ở target branch trong khoảng createdDate [from, to]",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Bỏ qua confirm khi --purge-target / --purge-cancelled (chạy thẳng, không hỏi)",
        )
        parser.add_argument(
            "--purge-cancelled",
            action="store_true",
            help="Scan đơn đã hủy bên A, tìm đơn tương ứng bên B qua description, xóa bên B.",
        )
        parser.add_argument(
            "--channel-ids",
            required=False,
            help=(
                "Danh sách id của channels (phân tách dấu phẩy), vd: 7,12. "
                "Chỉ xử lý invoice bên A có saleChannelId == Channel.channel_id. "
                "Khi dùng với --update-sale-channel: map sang Channel.old_channel_id để update bên B."
            ),
        )
        parser.add_argument(
            "--update-sale-channel",
            action="store_true",
            help=(
                "Chế độ update-only: KHÔNG tạo đơn mới bên B. Pre-scan B theo description, "
                "với invoice A match filter --channel-ids, PUT saleChannelId=old_channel_id cho đơn B."
            ),
        )
        parser.add_argument(
            "--include-empty-sale-channel",
            action="store_true",
            help=(
                "Đi kèm --update-sale-channel: đồng bộ thêm các invoice A không có saleChannelId, "
                "update đơn B tương ứng về saleChannelId=null."
            ),
        )
        parser.add_argument(
            "--purge-wrong-date",
            action="store_true",
            help=(
                "Xóa đơn bên B có description SYNC match source codes trong [from,to] bên A "
                "nhưng purchaseDate bên B nằm ngoài [from,to] (bị sai ngày)."
            ),
        )
        parser.add_argument(
            "--update-delivery-detail",
            action="store_true",
            help=(
                "Chế độ update-only: KHÔNG tạo đơn mới. Pre-scan B theo description, "
                "so sánh deliveryCode + delivery status giữa A và B, PUT update bên B nếu khác."
            ),
        )

    def handle(self, *args, **options):
        service = _CliSyncInvoicesService(stdout=self.stdout, stderr=self.stderr)
        # exact_mirror=False cứng: xem WHY trong docstring module (hành vi patch-status
        # của command lịch sử không phụ thuộc settings reconcile).
        options["exact_mirror"] = False
        try:
            service.run(**options)
        except SyncError as exc:
            raise CommandError(str(exc)) from exc
