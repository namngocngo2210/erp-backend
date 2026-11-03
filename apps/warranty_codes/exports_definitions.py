"""Định nghĩa export của app warranty_codes — đăng ký vào engine ở
WarrantyCodesConfig.ready().

`kind` = "warranty_code_batch", mirror `batch_views.export_batch` +
`export_service.export_batch` (cả hai đã xoá ở Task 18 cùng endpoint đồng bộ
`GET /api/warranty-codes/batches/<id>/export`): header gốc
`("SERIAL", "URL", "SKU")` — thêm cột `CODE` (mã 8 ký tự in rời) từ 18/09/2026
để xưởng in không phải bóc mã từ URL — cùng vòng `.iterator(chunk_size=5000)`, cùng việc
set `exported_at` SAU KHI ghi xong (không phải trước — nếu ghi lỗi giữa chừng
thì batch vẫn coi là "chưa xuất").

Không có trần dòng ở luồng này (từ 17/09/2026 lô không giới hạn số lượng):
writer đã stream theo `.iterator(chunk_size=5000)` nên bộ nhớ không phụ thuộc
cỡ lô; trần thời gian là EXPORT_SOFT_TIME_LIMIT chung của engine. Lô đang sinh
mã nền (`is_generating`) bị chặn ở validate — xuất lúc đó là file thiếu mã.
"""

from __future__ import annotations

from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.common.dates import parse_iso_date
from apps.exports.application.registry import ExportDefinition, register_export
from apps.sysconfig import sysconfig
from apps.users.auth_core import user_has_any_permission
from apps.warranty_codes import repository
from apps.warranty_codes.models import WarrantyCode, WarrantyCodeBatch

_HEADERS = ("SERIAL", "CODE", "URL", "SKU")


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class WarrantyCodeBatchExport(ExportDefinition):
    """Xuất mã của MỘT hoặc NHIỀU lô vào một file.

    Tham số (17/09/2026): `batch_ids` (chọn tay) HOẶC `filters` (không chọn gì →
    xuất toàn bộ lô khớp bộ lọc của bảng, mọi trang). `batch_id` đơn lẻ giữ
    để tương thích. Tập lô được giải ra NGAY ở validate thành `batch_ids` sort
    ổn định — vừa để params_hash dedupe, vừa để file ghi ở worker đúng tập
    người dùng nhìn lúc bấm (lô tạo sau đó không lọt vào).
    """

    kind = "warranty_code_batch"

    def validate(self, user, raw_params: dict) -> dict:
        if raw_params.get("batch_ids") is not None:
            qs = WarrantyCodeBatch.objects.filter(id__in=_parse_ids(raw_params["batch_ids"]))
        elif raw_params.get("batch_id") is not None:
            qs = WarrantyCodeBatch.objects.filter(id__in=_parse_ids([raw_params["batch_id"]]))
        elif raw_params.get("filters") is not None:
            filters = raw_params["filters"]
            if not isinstance(filters, dict):
                raise ValidationError({"detail": "Bộ lọc không hợp lệ."})
            qs = repository.filter_batches(
                WarrantyCodeBatch.objects.all(),
                q=str(filters.get("q") or ""),
                product_id=_int_or_none(filters.get("product_id")),
                status=str(filters.get("status") or ""),
                date_from=_parse_date(filters.get("date_from")),
                date_to=_parse_date(filters.get("date_to")),
            )
        else:
            raise ValidationError({"detail": "Thiếu tham số batch_ids hoặc filters."})

        batches = list(qs.order_by("id").only("id", "batch_code", "start_seq", "end_seq", "generated_seq"))
        if not batches:
            raise ValidationError({"detail": "Không có lô nào để xuất."})
        if raw_params.get("batch_ids") is not None and len(batches) != len(set(_parse_ids(raw_params["batch_ids"]))):
            raise ValidationError({"detail": "Không tìm thấy lô mã."})
        # Lô đang sinh mã nền: file sẽ thiếu mã → từ chối cả lượt, nêu tên lô
        # (quyết định chủ 17/09: không âm thầm bỏ qua).
        busy = [b.batch_code for b in batches if b.is_generating]
        if busy:
            raise ValidationError(
                {"detail": f"Lô đang sinh mã, chờ xong rồi xuất: {', '.join(busy)}."}
            )
        return {
            "batch_ids": [b.id for b in batches],
            # Cache batch_code ngay ở validate: file_name cần nó nhưng không nhận
            # user/request để tự tra lại — cùng lý do StockExport cache warehouse_code.
            "batch_code": batches[0].batch_code if len(batches) == 1 else "",
            "created_on": timezone.localdate().strftime("%Y%m%d"),
        }

    def check_permission(self, user, params: dict) -> None:
        if not user_has_any_permission(user, "warranty-codes:export"):
            raise PermissionDenied("Bạn không có quyền thực hiện hành động này.")

    def estimate(self, params: dict) -> int | None:
        return WarrantyCode.objects.filter(batch_id__in=params["batch_ids"]).count()

    def file_name(self, params: dict) -> str:
        if params.get("batch_code"):
            return f"{params['batch_code']}.xlsx"
        return f"ma-bao-hanh-{len(params['batch_ids'])}-lo-{params['created_on']}.xlsx"

    def write(self, params: dict, writer, progress) -> None:
        batches = list(
            WarrantyCodeBatch.objects.select_related("product").filter(id__in=params["batch_ids"])
        )
        if len(batches) != len(params["batch_ids"]):
            # Lô bị xoá giữa lúc tạo job và lúc worker chạy — kịch bản cực hiếm
            # (không có endpoint xoá lô), để tasks.run_export bắt và mark_failed
            # bằng thông báo lỗi chung.
            raise ValueError("Không tìm thấy một số lô mã trong job.")

        writer.write_header(_HEADERS)
        # Mỗi lô cùng 1 sản phẩm (hoặc chưa gắn — reserved) nên tra SKU theo
        # batch_id từ dict, không join per-row. SKU là dữ liệu người dùng nhập
        # nhưng KHÔNG cần tự chống formula-injection ở đây: writer của engine
        # (StreamingXlsxWriter → apps.common.excel_utils.write_only_cells) đã áp
        # cơ chế ép cell 'f' về text cho MỌI cột, xem
        # apps/warranty_codes/tests/test_batch_export_definition.py.
        sku_by_batch = {b.id: (b.product.sku if b.product_id else "") for b in batches}
        base_url = sysconfig.get("WARRANTY_QR_BASE_URL")
        base_url = base_url if base_url.endswith("/") else base_url + "/"
        codes = (
            WarrantyCode.objects.filter(batch_id__in=params["batch_ids"])
            .order_by("seq")
            .values_list("batch_id", "serial", "code")
            .iterator(chunk_size=5000)
        )
        count = 0
        for batch_id, serial, code in codes:
            writer.write_row([serial, code, f"{base_url}{code}", sku_by_batch[batch_id]])
            count += 1
            progress(count)
        progress(count)
        # exported_at set SAU KHI ghi xong — giữ nguyên hành vi view đồng bộ
        # (export_service.export_batch): ghi lỗi giữa chừng thì batch vẫn coi là
        # chưa xuất, người dùng xuất lại được mà không cần can thiệp thủ công.
        WarrantyCodeBatch.objects.filter(id__in=params["batch_ids"]).update(exported_at=timezone.now())


def _parse_ids(raw) -> list[int]:
    if not isinstance(raw, list):
        raise ValidationError({"detail": "Danh sách lô không hợp lệ."})
    try:
        ids = [int(v) for v in raw]
    except (TypeError, ValueError):
        raise ValidationError({"detail": "Danh sách lô không hợp lệ."}) from None
    if not ids:
        raise ValidationError({"detail": "Chưa chọn lô nào."})
    return ids


def _parse_date(value):
    try:
        return parse_iso_date(value, strict=True)
    except ValueError:
        raise ValidationError({"detail": "Ngày lọc không hợp lệ."}) from None


register_export(WarrantyCodeBatchExport())
