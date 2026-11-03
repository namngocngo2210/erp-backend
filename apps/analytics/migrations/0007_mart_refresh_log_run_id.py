"""Thêm `run_id` vào `dwh.mart_refresh_log` — bắt buộc một lượt refresh CỐ KẾT.

Lỗ hổng đã phát hiện trong review khối 2A: `last_refresh_boundary()` cũ lấy MIN
trên dòng log MỚI NHẤT của TỪNG mart, không đòi hỏi các dòng đó đến từ CÙNG một
lần chạy. Nếu job 04:25 chết 5 đêm liên tiếp, không có dòng log mới nào được
ghi — nhưng mỗi mart vẫn có "dòng mới nhất" của nó từ 5 đêm trước, MIN trên ba
dòng đó vẫn ra một mốc "đủ coverage", và `dwh_reconcile` báo khớp trên một kho
dữ liệu đã cũ 5 ngày. Coverage một phần từng bị coi là không có coverage (đúng
quy tắc), nhưng MỘT KHI mọi mart đã log ít nhất một lần, coverage vĩnh viễn đầy
đủ — lỗ hổng mở mãi mãi.

`run_id` đóng lỗ này: `refresh_all_marts()` sinh một `run_id` DUY NHẤT cho mỗi
lần gọi, đóng dấu lên MỌI dòng nó ghi. `last_refresh_boundary()` chỉ chấp nhận
mốc nếu `run_id` mới nhất có dòng cho ĐỦ mọi mart trong `MART_ORDER` — không
được trộn dòng của các lần chạy khác nhau nữa. Việc kho dữ liệu vẫn có thể CŨ
(lần chạy cố kết cuối cùng đã lâu) được chặn riêng ở tầng `dwh_reconcile`
(`DWH_RECONCILE_MAX_BOUNDARY_AGE_HOURS`) — hai lớp phòng thủ, hai lỗi khác nhau.

Backfill: mỗi dòng lịch sử hiện có nhận một `run_id` ngẫu nhiên RIÊNG (không
gộp chung một run_id cho cả lô cũ). Cố ý: các dòng cũ được ghi TRƯỚC khi
`run_id` tồn tại, không có cách nào biết chúng có thực sự cùng một lượt chạy
cố kết hay không — coi mỗi dòng là một run riêng là lựa chọn AN TOÀN (khiến
lịch sử cũ khó/không thể tình cờ tạo thành một "run đầy đủ" giả), đúng tinh
thần "coverage một phần = không coverage" của module này.
"""

from django.db import migrations

FORWARD_SQL = """
ALTER TABLE dwh.mart_refresh_log ADD COLUMN IF NOT EXISTS run_id uuid;

UPDATE dwh.mart_refresh_log SET run_id = gen_random_uuid() WHERE run_id IS NULL;

ALTER TABLE dwh.mart_refresh_log ALTER COLUMN run_id SET NOT NULL;

CREATE INDEX IF NOT EXISTS mart_refresh_log_run_id_idx
    ON dwh.mart_refresh_log (run_id);
"""

REVERSE_SQL = """
DROP INDEX IF EXISTS dwh.mart_refresh_log_run_id_idx;
ALTER TABLE dwh.mart_refresh_log DROP COLUMN IF EXISTS run_id;
"""


class Migration(migrations.Migration):
    dependencies = [("analytics", "0006_customer_360_behavior")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL)
            ]
        )
    ]
