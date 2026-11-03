"""Định nghĩa export của app warranty — đăng ký vào engine ở WarrantyConfig.ready().

`kind` = "warranty_claims", mirror `claim_views.export_warranty_claims_view` gốc
(view đã xoá ở Task 18 cùng endpoint đồng bộ `GET /api/warranty/claims/export`):
cùng 2 nhánh (ids đã chọn / filter q+status), cùng bộ cột `claim_excel._HEADERS`
+ `claim_excel._claim_row`.

Khác biệt DUY NHẤT so với view cũ: view cũ KHÔNG có trần nào (`list_all_claims`
quét sạch bảng theo filter, chỉ bị chặn ngầm bởi timeout HTTP của request đồng
bộ). Ở đường async không có timeout đó — job "sống" tới khi worker xử lý xong,
nên một filter rỗng sẽ dump cả bảng phiếu bảo hành vào RAM worker nếu không có
`_CLAIMS_MAX_ROWS`.
"""

from __future__ import annotations

from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.exports.application.registry import ExportDefinition, register_export
from apps.users.auth_core import user_has_any_permission
from apps.warranty.claim_excel import _HEADERS, _claim_row
from apps.warranty.claim_repository import WarrantyClaimRepository

# Trần mới (luồng đồng bộ cũ không có trần nào) — xem docstring module.
_CLAIMS_MAX_ROWS = 20000


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class WarrantyClaimsExport(ExportDefinition):
    kind = "warranty_claims"

    def validate(self, user, raw_params: dict) -> dict:
        # `ids` là JSON list (FE gửi từ lựa chọn bảng), không phải chuỗi phân
        # tách dấu phẩy như query param của view đồng bộ — cùng khác biệt
        # request body (async) vs query string (sync) như OrdersExport.
        #
        # `int(v)` ném ValueError trần nếu client gửi `ids: ["abc"]` — không
        # phải ValidationError nên lọt thành 500 thay vì 400 đúng hợp đồng
        # `validate()` (mirror OrdersExport.validate, apps/orders/exports_
        # definitions.py — cùng bug, cùng cách sửa).
        try:
            ids = [int(v) for v in (raw_params.get("ids") or [])]
        except (TypeError, ValueError):
            raise ValidationError({"detail": "Danh sách ids không hợp lệ."}) from None
        return {
            "ids": ids,
            "q": str(raw_params.get("q") or ""),
            "status": _int_or_none(raw_params.get("status")),
        }

    def check_permission(self, user, params: dict) -> None:
        # GIỮ NGUYÊN chuỗi generic của `require_permission` (apps/users/permissions.py)
        # — không bịa thông báo riêng cho luồng này; 2 definition anh em
        # (warranty_codes, audit) và `inventory/exports_definitions.py` đều dùng
        # đúng chuỗi này.
        if not user_has_any_permission(user, "warranty:export"):
            raise PermissionDenied("Bạn không có quyền thực hiện hành động này.")

    def estimate(self, params: dict) -> int | None:
        # Export theo lựa chọn (ids) không bị chặn trần — mirror OrdersExport:
        # người dùng đã tự giới hạn tập bằng thao tác chọn dòng trên UI.
        if params["ids"]:
            return len(params["ids"])
        matched = WarrantyClaimRepository.claims_queryset(params["q"], params["status"]).count()
        if matched > _CLAIMS_MAX_ROWS:
            raise ValidationError(
                {
                    "detail": (
                        f"Bộ lọc khớp {matched} phiếu, vượt giới hạn {_CLAIMS_MAX_ROWS} phiếu "
                        "mỗi lần xuất. Vui lòng thu hẹp bộ lọc rồi thử lại."
                    )
                }
            )
        return matched

    def file_name(self, params: dict) -> str:
        return "phieu-bao-hanh.xlsx"

    def write(self, params: dict, writer, progress) -> None:
        writer.write_header(_HEADERS)
        if params["ids"]:
            count = 0
            for claim in WarrantyClaimRepository.list_by_ids(params["ids"]):
                writer.write_row(_claim_row(claim))
                count += 1
                progress(count)
            progress(count)
            return

        qs = WarrantyClaimRepository.claims_queryset(params["q"], params["status"])
        # Lấy dư 1 dòng để phát hiện vượt trần mà không cần COUNT trước — cùng kỹ
        # thuật StockExport.write() (Task 15). BẮT BUỘC re-check ở ĐÂY, KHÔNG tin
        # estimate() đã chặn: estimate() chạy 1 lần, đồng bộ, lúc tạo job; write()
        # chạy sau đó ở worker, có thể trễ — giữa hai mốc đó số phiếu khớp filter
        # có thể phình vượt trần (phiếu mới liên tục được tạo) mà job đã tạo rồi
        # thì không huỷ được nữa.
        claims = list(qs[: _CLAIMS_MAX_ROWS + 1])
        truncated = len(claims) > _CLAIMS_MAX_ROWS
        total = None
        if truncated:
            # Vượt trần LÚC WRITE (dữ liệu phình sau khi job đã tạo): KHÔNG raise
            # — raise ở đây bị `tasks.run_export` nuốt thành thông báo chung
            # "Lỗi khi dựng file", mất hẳn con số cụ thể. Cắt bớt + cảnh báo ngay
            # trong file (đọc được) tốt hơn raise ở phút cuối, cùng tinh thần
            # StockExport.write().
            claims = claims[:_CLAIMS_MAX_ROWS]
            total = qs.count()
        count = 0
        for claim in claims:
            writer.write_row(_claim_row(claim))
            count += 1
            progress(count)
        # Gọi progress() không điều kiện sau vòng lặp — cùng khuôn nhánh `ids`
        # ở trên, `OrdersExport`, `AuditLogsExport`: đảm bảo progress() luôn
        # được gọi ít nhất 1 lần kể cả khi 0 phiếu khớp filter (không để job
        # đứng ở trạng thái "chưa có tiến độ nào" dù đã ghi xong).
        progress(count)
        if truncated:
            message = (
                f"⚠️ Bộ lọc khớp {total} phiếu — file chỉ chứa {_CLAIMS_MAX_ROWS} phiếu đầu "
                "(dữ liệu tăng lên sau khi lệnh xuất được tạo). Vui lòng xuất lại để lấy đủ."
            )
            writer.write_row([message] + [""] * (len(_HEADERS) - 1))
            count += 1
            progress(count)


register_export(WarrantyClaimsExport())
