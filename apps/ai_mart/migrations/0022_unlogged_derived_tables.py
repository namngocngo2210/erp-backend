"""Chuyển các bảng mart DẪN XUẤT trong schema ai sang UNLOGGED (audit
2026-09-23 C5/P1-4).

Vì sao: rebuild đêm TRUNCATE+INSERT ~1,2GB (đo prod 23/09: tin_nhan 296MB,
don_hang 284MB, khach_hang 242MB, dong_don_hang 165MB, hoi_thoai 140MB, ...)
và sync 30' DELETE+INSERT liên tục — mọi byte đều đi qua WAL (18GB WAL/20h,
buffers_backend > buffers_checkpoint, checkpoint bị ép). Dữ liệu này tính lại
được 100% từ bảng nguồn nên không cần WAL: UNLOGGED bỏ hẳn chi phí đó.

Hệ quả PHẢI biết:
- Sau crash/kill -9 Postgres (không phải restart sạch), bảng UNLOGGED bị
  TRUNCATE khi recovery → chạy `rebuild_all_marts` (celery task
  apps.ai_mart.tasks.rebuild_all_marts hoặc management command tương ứng) để
  nạp lại. Restart bình thường / `docker compose restart` KHÔNG mất dữ liệu.
- Bảng UNLOGGED không có trên streaming replica (chưa dùng) — pg_dump vẫn dump.
- `ALTER TABLE ... SET UNLOGGED` REWRITE toàn bộ bảng dưới ACCESS EXCLUSIVE:
  ~1,2GB → chạy giờ thấp điểm, ngay trước rebuild đêm (xem runbook
  2026-09-23-audit-ops.md). Reverse SET LOGGED cũng rewrite.

KHÔNG đổi: ai.tu_dien_ma (từ điển seed bằng migration/admin), ai_meta.*
(watermark/nhật ký — mất là ETL mất mốc), ai_stg.hoi_thoai_nhan (nhãn Gemini
trả tiền, không tính lại được rẻ).
"""

from django.db import migrations

# Thứ tự = thứ tự REBUILD_MARTS trong application/etl_service.py (chỉ để đọc).
DERIVED_TABLES = (
    "ai.don_hang",
    "ai.dong_don_hang",
    "ai.khach_hang",
    "ai.cuoc_goi",
    "ai.san_pham",
    "ai.ton_kho",
    "ai.lich_su_kho",
    "ai.chi_phi_quang_cao",
    "ai.tin_nhan",
    "ai.hoi_thoai",
)


class Migration(migrations.Migration):
    dependencies = [("ai_mart", "0021_don_hang_chien_dich_fb")]

    operations = [
        migrations.RunSQL(
            sql=f"ALTER TABLE {table} SET UNLOGGED",
            reverse_sql=f"ALTER TABLE {table} SET LOGGED",
        )
        for table in DERIVED_TABLES
    ]
