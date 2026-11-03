"""Đồng bộ danh mục Foxia về bảng mirror.

Danh mục phải đồng bộ TRƯỚC dữ liệu giao dịch (checklist §11 của tài liệu
Foxia): đơn tham chiếu nhân viên/nguồn/chi nhánh bằng id, không có danh mục thì
mọi chiều phân tích đều rỗng.

Toàn bộ ghi đi qua `bulk_create(update_conflicts=True)`:
  - idempotent theo `foxia_id` unique — chạy lại bao nhiêu lần cũng không nhân bản;
  - không phát `post_save`, nên không kéo theo side effect của app khác;
  - một câu lệnh cho cả trang 500 bản ghi thay vì 500 lần round-trip.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.utils import timezone

from apps.foxia.contracts.dtos import many2one_id, many2one_name
from apps.foxia.domain.catalog_specs import ORDERED_SPECS, CatalogSpec
from apps.foxia.metrics import observe_run
from apps.foxia.models import FoxiaSyncRun, FoxiaSyncStatus

logger = logging.getLogger(__name__)

_PAGE_SIZE = 500


@dataclass
class CatalogSyncResult:
    resource: str
    fetched: int = 0
    written: int = 0
    skipped: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


class FoxiaCatalogSyncService:
    def __init__(self, client):
        self.client = client

    def sync_all(self) -> list[CatalogSyncResult]:
        """Đồng bộ mọi danh mục theo đúng thứ tự phụ thuộc.

        Một danh mục lỗi KHÔNG chặn các danh mục sau: chúng độc lập nhau về mặt
        dữ liệu, và chặn hết chỉ làm mất luôn phần lẽ ra đồng bộ được. Lỗi nằm
        lại ở FoxiaSyncRun tương ứng.
        """
        results: list[CatalogSyncResult] = []
        for spec in ORDERED_SPECS:
            try:
                results.append(self.sync_one(spec))
            except Exception as exc:  # noqa: BLE001 — lỗi một danh mục không giết cả lượt
                logger.exception("Foxia catalog sync %s thất bại", spec.resource)
                results.append(CatalogSyncResult(resource=spec.resource, error=str(exc)))
        return results

    def sync_one(self, spec: CatalogSpec) -> CatalogSyncResult:
        run = FoxiaSyncRun.objects.create(resource=f"catalog:{spec.resource}")
        result = CatalogSyncResult(resource=spec.resource)
        try:
            for rows in self.client.iter_all(
                spec.resource, fields=list(spec.fields), order="id asc", page_size=_PAGE_SIZE
            ):
                result.fetched += len(rows)
                written, skipped = self._upsert_page(spec, rows)
                result.written += written
                result.skipped += skipped
        except Exception as exc:  # noqa: BLE001 — ghi lại rồi ném lên cho sync_all
            result.error = str(exc)
            self._finish(run, result, FoxiaSyncStatus.FAILED)
            raise
        self._finish(run, result, FoxiaSyncStatus.SUCCESS)
        return result

    # ── nội bộ ───────────────────────────────────────────────────────────────

    def _upsert_page(self, spec: CatalogSpec, rows: list[dict]) -> tuple[int, int]:
        instances = []
        skipped = 0
        for row in rows:
            foxia_id = many2one_id(row.get("id"))
            if not foxia_id:
                # Bản ghi không có id thì không có khoá idempotent — bỏ qua và
                # ĐẾM, không im lặng.
                skipped += 1
                continue
            instances.append(self._build(spec, foxia_id, row))

        if not instances:
            return 0, skipped

        update_fields = ["name", "synced_at", *spec.scalars.keys(), *spec.relations.keys()]
        spec.model.objects.bulk_create(
            instances,
            update_conflicts=True,
            update_fields=update_fields,
            unique_fields=["foxia_id"],
        )
        return len(instances), skipped

    def _build(self, spec: CatalogSpec, foxia_id: int, row: dict):
        values = {
            "foxia_id": foxia_id,
            "name": many2one_name(row.get("name"))[:255],
            "synced_at": timezone.now(),
        }
        for local_field, foxia_field in spec.scalars.items():
            values[local_field] = self._scalar(spec, local_field, row.get(foxia_field))
        for local_field, (foxia_field, related_model) in spec.relations.items():
            values[f"{local_field}_id"] = self._resolve_fk(related_model, row.get(foxia_field))
        return spec.model(**values)

    @staticmethod
    def _scalar(spec: CatalogSpec, local_field: str, value):
        """Ép giá trị thô về đúng kiểu của field nội bộ.

        Odoo trả `False` cho chuỗi rỗng — gán thẳng vào CharField sẽ lưu ra
        chuỗi "False", một loại rác rất khó truy khi đã nằm trong DB.
        """
        model_field = spec.model._meta.get_field(local_field)
        if model_field.get_internal_type() == "BooleanField":
            return bool(value)
        if value is False or value is None:
            return ""
        return str(value).strip()[: getattr(model_field, "max_length", None) or 255]

    @staticmethod
    def _resolve_fk(related_model, raw_value) -> int | None:
        """Dịch id Foxia của quan hệ sang pk nội bộ.

        Trả None khi danh mục đích chưa đồng bộ — FK rỗng ở lượt đầu là chấp
        nhận được và tự đầy lên ở lượt sau, còn tạo bản ghi rỗng để lấp chỗ thì
        đẻ ra danh mục ma không có tên.
        """
        foxia_id = many2one_id(raw_value)
        if not foxia_id:
            return None
        return related_model.objects.filter(foxia_id=foxia_id).values_list("pk", flat=True).first()

    @staticmethod
    def _finish(run: FoxiaSyncRun, result: CatalogSyncResult, status: str) -> None:
        run.status = status
        run.fetched = result.fetched
        run.updated = result.written
        run.skipped = result.skipped
        run.error_message = result.error[:2000]
        run.finished_at = timezone.now()
        observe_run(
            run.resource,
            status,
            run.fetched,
            (run.finished_at - run.started_at).total_seconds(),
        )
        run.save(
            update_fields=[
                "status",
                "fetched",
                "updated",
                "skipped",
                "error_message",
                "finished_at",
            ]
        )
