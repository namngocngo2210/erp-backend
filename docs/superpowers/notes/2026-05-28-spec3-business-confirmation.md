# Spec 3 — Phiếu xác nhận business rule

> Spec 3 (schema/DB) có 4 mục cần ngữ nghĩa nghiệp vụ trước khi sửa. Vui lòng điền (chọn 1 phương án mỗi câu, có thể thêm ghi chú). Sau khi điền, báo lại để mở khóa các task tương ứng trong plan.

**Tham chiếu:**
- Spec: [docs/superpowers/specs/2026-05-28-backend-spec3-schema-db-design.md](../specs/2026-05-28-backend-spec3-schema-db-design.md)
- Plan: [docs/superpowers/plans/2026-05-28-backend-spec3-schema-db.md](../plans/2026-05-28-backend-spec3-schema-db.md)

---

## Q1. `Order.order_code` — quy tắc duy nhất?

Hiện trạng: `order_code = CharField(max_length=100, blank=True, default="")`. Có index nhưng KHÔNG unique constraint.

- [ ] **(a) Unique toàn cục** (mọi channel) — sẽ thêm `UniqueConstraint(fields=["order_code"], condition=Q(order_code__gt="", deleted_at__isnull=True))`.
- [ ] **(b) Unique trong từng channel** — `UniqueConstraint(fields=["channel", "order_code"], condition=...)`.
- [ ] **(c) Có thể trùng hợp lệ** — bỏ qua, không thêm constraint.

Ghi chú thêm (nếu có):

---

## Q2. `Order.external_order_id` — quy tắc duy nhất?

Hiện trạng: `external_order_id = CharField(max_length=100, blank=True, default="")`. Là ID từ platform (Shopee/TikTok/Facebook…) khi ingest.

- [ ] **(a) Unique theo (channel, external_order_id)** cho bản ghi active — thêm `UniqueConstraint(fields=["channel","external_order_id"], condition=Q(external_order_id__gt="", deleted_at__isnull=True))`.
- [ ] **(b) Có thể trùng hợp lệ** (vd platform retry sinh cùng id; bản chất là idempotency key) — không thêm constraint, có thể chỉ thêm log/dedup ở tầng ingest.

Ghi chú:

---

## Q3. Hard-delete `Order` — có xảy ra trên production không?

Hiện trạng: `OrderAssessment.order` và `OrderNeed.order_assessment` đều CASCADE. Nếu Order bị **hard-delete**, toàn bộ assessment + needs (dữ liệu đánh giá) bị xóa theo chain.

- [ ] **(a) KHÔNG bao giờ hard-delete** — chỉ soft-delete qua `deleted_at`. Giữ CASCADE, không cần đổi.
- [ ] **(b) Có hard-delete trong một số trường hợp** (vd cleanup script, admin) — đổi sang `SET_NULL` để giữ assessment lại như audit trail.
- [ ] **(c) Có hard-delete nhưng muốn xóa cùng** — giữ CASCADE (status quo).

Ghi chú (kịch bản hard-delete nếu có):

---

## Q4. JSONField nào cần tách thành cột để query/report?

Hiện trạng các JSONField đáng chú ý:
- `CustomerResaleFollowUp.contact_logs` (list các lần liên hệ resale)
- `CustomerResaleFollowUp.resale_orders` (list order resale)
- `AuditLog.response_data`, `AuditLog.metadata`
- `OrderJourneyEvent.payload_json`
- `LandingIngestTrace.payload`, `LandingIngestTrace.validation_errors`

Anh tick những field đang/sẽ cần aggregate/filter (nếu chỉ đọc raw thì để nguyên — YAGNI):

- [ ] `CustomerResaleFollowUp.contact_logs` → thêm cột `last_contact_at` (index), `contact_count`
- [ ] `CustomerResaleFollowUp.resale_orders` → thêm cột `resale_count`, `last_resale_at`
- [ ] `AuditLog.response_data` → trích `response_status`?
- [ ] `AuditLog.metadata` → trích key nào?
- [ ] Khác (ghi rõ):

Ghi chú:

---

## Sau khi điền

Báo lại trong chat. Tôi sẽ chạy Spec 3 theo các phương án anh chọn, kết hợp:
- Phase A (đo + thêm index có bằng chứng) — không phụ thuộc phiếu này.
- Phase B (Job TextField → CharField) — không phụ thuộc.
- Phase C2/C3 (unique) — phụ thuộc Q1, Q2.
- Phase C4 (on_delete) — phụ thuộc Q3.
- Phase C5 (JSON tách cột) — phụ thuộc Q4.
