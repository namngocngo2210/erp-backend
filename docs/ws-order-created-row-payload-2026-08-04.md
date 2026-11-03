# WS `order.created` payload tự mang row — diệt gốc REST fanout

## Bài toán

Sau MR `perf/order-list-fanout-hardening`, mỗi đơn ingest vẫn kéo theo:
- N tab (section orders) × 1 GET `/api/orders/{id}` (event đơn lẻ), hoặc
- N tab × 1 POST list (lô event) — đã có cooldown 10s/tab nhưng vẫn là request thật.

Log 14:01 04/08: ~10 cú `GET /api/orders/550947` cho MỘT đơn ingest. Endgame ghi
từ sự cố 30/07: payload tự chứa row thì tab upsert thẳng, **zero REST**.

## Thiết kế

```
create/ingest/duplicate ──build_order_created_row(order)──┐  (serialize 1 lần,
                                                          │   OrderListSerializer,
                OrderEventContract (row: dict|None) ◄─────┘   pop can_edit)
                        │ validate (lenient: row hỏng → bỏ row, event sống)
                        ▼
                publish_order_event → group_send
                        ▼
        CRMConsumer.crm_event: validate → _is_authorized
                        │ strip row theo field_policy CỦA TỪNG USER
                        │ (drop_hidden_fields — cùng hàm với REST; policy nạp
                        │  MỘT lần lúc handshake, không chạm DB per event)
                        ▼
        FE use-crm-websocket: payload.row hợp lệ (id khớp order_id)
                        │ gom jitter 0.5–2s (chỉ để gộp setState lô bulk)
                        ▼
        upsertOrderFromBroadcast(rows) → applyBroadcastRowsInMemory
                        │ can_edit suy từ row cùng kênh đang có trên bảng
                        ▼
        bảng cập nhật — KHÔNG request nào
```

Quyết định thiết kế chính:

1. **`can_edit` không đi trong broadcast** (per-user). Quyền sửa là
   per-user-per-KÊNH → FE suy từ một row cùng `channel_code` có `can_edit=true`
   trên bảng; không có thì để `false` an toàn (mở chi tiết fetch bản đầy đủ có
   giá trị thật). Đơn hủy luôn `can_edit=false` nên chỉ tin row `true`.
2. **Field ẩn theo role không rò qua WS**: consumer strip row bằng đúng
   `drop_hidden_fields` của REST, policy section "orders" nạp 1 lần lúc
   handshake (resolver RBAC có cache) — tôn trọng nguyên tắc "không chạm DB per
   event" của sự cố 2026-07-29.
3. **Lenient + tương thích deploy lệch**: row hỏng (không phải dict / id lệch)
   bị bỏ ở validate, event vẫn phát; FE gặp event không có row thì rơi về đường
   fetch coalesce + cooldown cũ. Backend cũ + FE mới, hoặc ngược lại, đều chạy.
4. **Đường row không cần gate tab ẩn / cooldown / backoff** — không tốn request;
   tab ẩn cũng upsert luôn (quay lại là thấy, không còn cửa sổ stale).
5. KiotViet badge: đơn mới chưa có job → giá trị trung tính (false/null) đúng
   với mọi user, không cần gate per-user cho event created.

Chi phí thêm ở điểm phát: 1 lần serialize OrderListSerializer (~5-15ms + vài
query PK nhỏ) cho MỖI đơn tạo — đổi lấy việc xoá N request × (page+ctx+serialize)
phía đọc.

## Phạm vi

- Contract: `OrderEvent.row` + `build_order_created(row=)` (`order_events.py`).
- Publisher: `build_order_created_row` (best-effort, lỗi → None).
- 3 call site: create manual / ingest landing / duplicate-restore.
- Consumer: `_load_scope` trả thêm policy; `crm_event` strip row.
- FE: `upsertOrderFromBroadcast` (App → use-orders-data → use-orders-table),
  đường row trong `use-crm-websocket`.
- KHÔNG đổi: `order.updated` (transfer vẫn đi đường fetch), restore.completed.

## Thứ tự merge (MR stack)

1. `perf/order-list-fanout-hardening` → master (cache + serializer gọn + defer).
2. Nhánh này (`feat/ws-order-created-row`) → merge sau, phụ thuộc
   OrderListSerializer của nhánh trên. Deploy FE+BE cùng nhau (event có row mà
   FE cũ thì bỏ qua row — vẫn chạy, chỉ chưa được lợi).

## Tiến độ

- [x] Contract + validate lenient + test (row sống 2 vòng validate, row hỏng bị bỏ)
- [x] Publisher builder + test (row dạng list, không can_edit, lỗi → None)
- [x] Consumer strip per-user (81 test channels_core + publisher pass)
- [x] FE row path + 4 test mới (16/16 hook test pass), tsc + eslint sạch
- [ ] Sau deploy: xác nhận log không còn cụm `GET /api/orders/{id}` sau ingest;
      cân nhắc áp cùng pattern cho `order.updated` (transfer) nếu số liệu cần
