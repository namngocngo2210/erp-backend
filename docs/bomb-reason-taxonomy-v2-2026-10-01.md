# Bộ lý do bom hàng v2 (2026-10-01)

## Bối cảnh
Bộ cũ 6 nhóm / 41 loại lẫn lộn trách nhiệm (vd "Lỗi, không hoạt động" nằm ở "Sản phẩm - kỳ vọng",
"Thiếu phụ kiện" ở "Đóng gói") nên báo cáo không trả lời được câu "bom do ai". Bộ mới tách theo
nguồn gây lỗi, 7 nhóm, kèm ô **Ghi chú chi tiết lý do** cho từng đơn.

| Nhóm (`group_code`) | Loại |
|---|---|
| Khách hàng (`customer`) | Không nghe máy · Khách đi công tác/về quê/đi viện/đi vắng · Khách không phối hợp nhận hàng · Khách đổi ý/không còn nhu cầu · Người nhà không đồng ý · Khách mua/nhận SP bên khác trước · Không đủ tiền khi nhận · Khách hủy đơn sau khi đã gửi hàng |
| DVVC / Giao hàng (`shipping_unit`) | Shipper giao theo tuyến, không hỗ trợ giờ khách muốn · Shipper không cho thử/xem hàng · Shipper không giao, tự ý tích hoàn · Bưu cục quá tải · Giao hàng chậm · Khách và shipper chửi nhau · Thất lạc hàng |
| Xử lý đơn – lỗi sale/vận đơn (`order_processing`) | Sai địa chỉ · Sai sản phẩm · Sai giá · Trùng đơn · Khách báo hủy trước khi gửi nhưng vẫn bị gửi · Chậm xử lý/gửi đơn · Khách có bệnh nền |
| Lỗi đóng gói (`packaging`) | Đóng sai sản phẩm · Thiếu quà tặng/Thẻ bảo hành · Đóng thiếu hàng · Đóng nhầm hàng · Hàng đã qua sử dụng (cũ) · Đóng gói không đảm bảo |
| Kỳ vọng KH về SP (`product_expectation`) | Không phù hợp nhu cầu · Kỳ vọng máy mạnh hơn · Kỳ vọng tính năng khác · Không ưng khi kiểm hàng · Không phù hợp kích thước · Không phù hợp người dùng · Kỳ vọng nguồn gốc/xuất xứ · Kỳ vọng về giá |
| Quảng cáo / Thông tin SP (`advertising_info`) | Nội dung QC không đúng thực tế · Hình ảnh/video khiến khách hiểu khác · Mô tả tính năng chưa rõ · Cách diễn đạt tạo kỳ vọng cao |
| Sản phẩm / Chất lượng (`product_quality`) | SP lỗi/không hoạt động · Thiếu/sai phụ kiện · Ngoại quan/kiện hàng có vấn đề · Máy hoạt động yếu bất thường · Trải nghiệm không đạt do vấn đề SP |

## Thiết kế
- Catalog vẫn là hằng số `BOMB_REASON_GROUP_SEED` / `BOMB_REASON_TYPE_SEED` (`orders/crm_catalog.py`),
  không thêm bảng — danh mục do nghiệp vụ chốt, đổi hiếm, đi kèm migration remap.
- Giữ nguyên mã cũ khi nghĩa tương đương (vd `no_answer`, `shipper_auto_returned`) để không phải
  đụng dữ liệu; chỉ đổi nhóm khi loại chuyển nhóm (`defective_not_working` → `product_quality`).
- `other_reason` (594 đơn, phần lớn có ghi chú tự do) không có loại tương đương → giữ làm mã legacy
  `is_active=False`: vẫn hiện nhãn "Lí do khác (cũ)", ẩn khỏi dropdown chọn mới.
- `bomb_reason_text` = ghi chú chi tiết do sale nhập. Bỏ cơ chế backend tự điền nhãn loại vào ghi chú
  và bỏ việc drawer xoá ghi chú khi đổi nhóm/loại.
- Data migration `orders/0149_remap_bomb_reason_taxonomy_v2` (mapping đóng băng trong file):

| Mã cũ | Mã mới |
|---|---|
| found_cheaper_elsewhere | price_expectation |
| trial_or_emotional_order, negative_feedback_after_order | changed_mind_not_buying |
| require_staff_installation | customer_uncooperative |
| mistook_other_product | expected_other_features |
| no_chair_not_accepting | product_not_suitable |
| waiting_for_salary_or_cash_flow | insufficient_cash_on_delivery |
| refuse_china_need_japan | origin_expectation |
| order_commitment_mismatch | overpromising_wording |
| natural_disaster | delivery_too_slow |
| misunderstood_product_utility | unclear_feature_description |
| massage_painful | not_suitable_for_user |

  Khi remap mà ghi chú trống → ghi nhãn cũ vào ghi chú để không mất thông tin. Nhóm cũ
  `finance`/`trust_information` không có loại → set NULL. Reverse là no-op.

## Sprint
### S1 — Catalog + migration + UI (xong)
- [x] Catalog 7 nhóm / 45 loại + legacy `other_reason`
- [x] Migration 0149 remap + regroup (đã đối chiếu dữ liệu prod 2026-10-01: ~2.400 đơn có lý do)
- [x] Bỏ auto-derive `bomb_reason_text`; drawer thêm ô "Ghi chú chi tiết lý do", đổi loại không xoá ghi chú
- [x] Bảng đơn: dropdown theo bộ mới, cột "Ghi chú lý do bom"
- [x] Test: toàn vẹn catalog, migration, không ghi đè ghi chú; cập nhật nhãn ở test báo cáo

### Chưa làm / đề xuất
- [ ] Báo cáo "Tỷ trọng lý do bom" đang group theo loại; nên thêm view theo **nhóm** (7 nhóm = 7 bộ phận chịu trách nhiệm).
- [ ] Phân loại lại 594 đơn `other_reason` dựa trên ghi chú (nhiều đơn ghi "kiểm hàng không ưng" → `not_satisfied_after_inspection`) — cần nghiệp vụ duyệt trước.
- [ ] Frontend `OrdersTable/constants.ts` vẫn nhân bản catalog cho ô bảng — nên chuyển sang đọc `crm-catalog` API như drawer.
