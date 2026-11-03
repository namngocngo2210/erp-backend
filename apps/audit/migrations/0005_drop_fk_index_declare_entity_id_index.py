"""Dọn index `audit_logs`: bỏ index FK thừa + khai báo index đang drift khỏi repo.

Đo trên prod 2026-08-17 (uptime 13,7 ngày — vượt ngưỡng ≥7 ngày mà runbook
`docs/runbooks/2026-07-26-drop-dead-indexes.md` yêu cầu để `idx_scan` có nghĩa):

    audit_logs — heap 3383MB, index 679MB, 2.181.930 dòng

    | index                            | idx_scan | size   |
    |----------------------------------|---------:|-------:|
    | audit_logs_source_5bf8b7_idx     |        0 | 123 MB |
    | audit_logs_entity__4be24a_idx    |        9 | 122 MB |
    | audit_logs_action__952b82_idx    |        0 | 105 MB |
    | audit_logs_user_id_e11c73_idx    |       12 |  92 MB |
    | audit_entity_id_ts_idx           |        0 |  83 MB |
    | audit_logs_timesta_e93820_idx    |      137 |  70 MB |
    | audit_auditlog_pkey              |  698.716 |  60 MB |
    | audit_auditlog_user_id_c1cca96c  |        0 |  24 MB |

Đợt này chỉ động vào hai cái có kết luận CHẮC CHẮN, mỗi cái vì một lý do khác nhau:

1. `audit_auditlog_user_id_c1cca96c` — DROP. Index FK một cột Django tự sinh, bị
   `(user, -timestamp)` phủ hoàn toàn. Đây là dư thừa cấu trúc chứng minh được
   bằng lý thuyết B-tree, không phải suy từ `idx_scan`.

2. `audit_entity_id_ts_idx` — GIỮ, chỉ khai báo lại. Index này tồn tại trên prod
   nhưng `git grep` toàn repo không ra một dòng nào định nghĩa nó → được tạo tay
   thẳng trên prod. Hệ quả: DB dựng lại từ migration (staging, test, DR) sẽ
   THIẾU nó, và `makemigrations --check` không phát hiện được vì Django cũng
   không biết nó tồn tại.

Các index `idx_scan = 0` còn lại (`source`, `action_type`) CỐ Ý chưa đụng tới:
runbook 26/07 đã ghi rõ "`idx_scan = 0` là điều kiện CẦN, không phải ĐỦ" — cả hai
đều là `filterset_fields` của `AuditLogViewSet`, tức có call site thật, chỉ là
tính năng ít người dùng. Muốn drop thì phải bỏ luôn bộ lọc ở tầng API trước.

Vì sao CONCURRENTLY + atomic=False + SeparateDatabaseAndState: xem đầu file
`orders/migrations/0109_drop_dead_indexes.py`. `audit_logs` nhận INSERT liên tục
từ middleware nên `DROP INDEX` thường (ACCESS EXCLUSIVE) sẽ phải xếp hàng chờ
khoá và chặn đường ghi audit trong lúc chờ.
"""
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    # CONCURRENTLY không chạy được bên trong transaction block.
    atomic = False

    dependencies = [
        ('audit', '0004_rename_audit_audit_timesta_901180_idx_audit_logs_timesta_e93820_idx_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # 1) Bỏ index FK một cột — `(user_id)` là tiền tố của
        #    `audit_logs_user_id_e11c73_idx (user_id, timestamp DESC)`, nên mọi
        #    truy vấn dùng được index nhỏ đều dùng được index lớn. Giữ lại chỉ
        #    tốn thêm một lần ghi B-tree trên mỗi INSERT audit.
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="DROP INDEX CONCURRENTLY IF EXISTS audit_auditlog_user_id_c1cca96c",
                    reverse_sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                        "audit_auditlog_user_id_c1cca96c ON audit_logs (user_id)"
                    ),
                ),
            ],
            state_operations=[
                migrations.AlterField(
                    model_name='auditlog',
                    name='user',
                    field=models.ForeignKey(
                        blank=True,
                        db_index=False,
                        null=True,
                        on_delete=models.SET_NULL,
                        related_name='audit_logs',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        # 2) Khai báo `audit_entity_id_ts_idx` cho khớp thực tế prod.
        #    `CREATE ... IF NOT EXISTS` nên trên prod là no-op (index đã có),
        #    còn DB dựng mới thì được tạo đúng — cùng một migration chạy được cả
        #    hai phía, không cần thao tác tay riêng cho từng môi trường.
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS audit_entity_id_ts_idx "
                        "ON audit_logs (entity_id, timestamp DESC)"
                    ),
                    reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS audit_entity_id_ts_idx",
                ),
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name='auditlog',
                    index=models.Index(
                        fields=['entity_id', '-timestamp'],
                        name='audit_entity_id_ts_idx',
                    ),
                ),
            ],
        ),
    ]
