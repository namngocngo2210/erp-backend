# Phiếu kho: Chỉnh sửa & Đính kèm chứng từ

> Trạng thái: ĐANG TRIỂN KHAI — branch `feature/stock-document-edit-and-attachments`
> Ngày tạo: 2026-06-17

## 1. Bối cảnh & mục tiêu

Module phiếu kho (`apps/inventory`) hiện chỉ hỗ trợ **tạo mới** phiếu (DRAFT) rồi đi
theo state machine `draft → pending_approval → posted/cancelled`. Hai thiếu sót nghiệp vụ:

1. **Không sửa được phiếu**: khi nhập sai dòng/số lượng/ghi chú, người dùng buộc phải
   tạo phiếu mới. Cần cho **sửa phiếu khi còn ở trạng thái Nháp** (phiếu bị từ chối tự
   quay về Nháp nên cũng sửa được). Phiếu đã ghi sổ KHÔNG sửa vì đã phát sinh tồn kho.
2. **Không đính kèm được chứng từ**: kế toán/kho cần lưu ảnh hóa đơn, biên bản, phiếu
   giao nhận kèm phiếu kho. Cần upload **ảnh + PDF** lên MinIO, preview ngay trên dialog
   chi tiết phiếu, click để **zoom (ảnh) / mở (PDF)** và **tải về**.

## 2. Quyết định thiết kế (đã chốt với CTO)

| Quyết định | Lựa chọn |
|---|---|
| Sửa phiếu ở trạng thái nào | **Chỉ Nháp** (draft). Posted/pending/cancelled → khóa sửa |
| Upload chứng từ ở trạng thái nào | **Mọi trạng thái** (kể cả đã ghi sổ — chứng từ thường về sau) |
| Vị trí UI chứng từ | **Dialog chi tiết phiếu** (click dòng phiếu → xem header + dòng + chứng từ) |
| Loại file | **Ảnh (JPG/PNG/WebP) + PDF**, tối đa 10MB/file |
| Lưu trữ | MinIO (S3) qua boto3 — tái dùng pattern `apps/hr/infrastructure/photo_storage.py` |

## 3. Kiến trúc & tái sử dụng

- **Lưu object-key trong DB, không lưu URL** (giống `AttendanceEvent.photo_key`) — hạ tầng-agnostic.
- File phục vụ qua **endpoint proxy có JWT** (giống `attendance_event_photo`): set
  `X-Content-Type-Options: nosniff` + CSP để chặn stored-XSS; `?download=1` đổi sang
  `Content-Disposition: attachment`.
- Frontend tái dùng `AuthImage` (fetch blob kèm Bearer token) cho preview; lightbox tự
  dựng bằng shadcn Dialog (không thêm thư viện mới).

## 4. Mô hình dữ liệu

```
StockDocumentAttachment
  id              PK
  document_id     FK → StockDocument (CASCADE)
  file_key        CharField(300)   # object key trên MinIO
  content_type    CharField(100)
  original_name   CharField(255)
  size_bytes      PositiveInteger
  uploaded_by_id  FK → User (SET_NULL)
  uploaded_at     DateTime(auto_now_add)
  index: (document_id, uploaded_at)
```

Bucket MinIO mới: `MINIO_BUCKET_DOCUMENTS` (default `stock-documents`).
Key layout: `stock-documents/{document_id}/{uuid4}.{ext}`.

## 5. API

| Method | Path | Quyền | Mô tả |
|---|---|---|---|
| PATCH | `/api/inventory/documents/<id>` | section `stock-documents` | Sửa phiếu (chỉ DRAFT) |
| POST | `/api/inventory/documents/<id>/attachments` | section `stock-documents` | Upload 1..n file (multipart `files`) |
| GET | `/api/inventory/documents/attachments/<att_id>/file` | section `stock-documents` | Phục vụ file (proxy, `?download=1` để tải) |
| DELETE | `/api/inventory/documents/attachments/<att_id>` | section `stock-documents` | Xóa file (DB + MinIO) |

`attachments` được nhúng sẵn vào `StockDocumentSerializer` (kèm `file_url`) nên detail/list trả luôn.

## 6. Sprint

### Sprint 1 — Backend sửa phiếu ✅
- [x] `StockDocumentService.update_document` (guard DRAFT, replace lines atomically)
- [x] PATCH endpoint trên `document_detail`
- [x] Test `test_stock_document_edit.py`

### Sprint 2 — Backend chứng từ ✅
- [x] Setting `MINIO_BUCKET_DOCUMENTS` + module `infrastructure/document_storage.py`
- [x] Model `StockDocumentAttachment` + migration 0006
- [x] Service upload/list/delete; serializer + nhúng vào `StockDocumentSerializer`
- [x] Views upload/serve/delete + URLs
- [x] Test `test_stock_document_attachments.py`

### Sprint 3 — Frontend ✅
- [x] Types + queries (`useUpdateStockDocument`, `useUploadAttachments`, `useDeleteAttachment`)
- [x] `CreateDocumentDialog` chế độ sửa (prefill, đổi tiêu đề, gọi update)
- [x] `StockDocumentDetailDialog` (header + dòng + khu chứng từ: upload, thumbnail, lightbox zoom/tải)
- [x] `StockDocumentsPage`: click dòng → detail; nút Sửa cho phiếu Nháp

## 7. Ghi chú sau triển khai
- Cập nhật khi hoàn tất từng sprint.
</content>
</invoke>
