from django.db import models
from django.conf import settings


class AuditLog(models.Model):
    ACTION_TYPES = [
        ('CREATE', 'Create'),
        ('UPDATE', 'Update'),
        ('DELETE', 'Delete'),
        ('READ', 'Read'),
        ('LOGIN', 'Login'),
        ('LOGOUT', 'Logout'),
        ('API_REQUEST', 'API Request'),
        ('KIOTVIET_REQUEST', 'KiotViet Request'),
        ('SYSTEM', 'System'),
    ]

    ENTITY_TYPES = [
        ('ORDER', 'Order'),
        ('CUSTOMER', 'Customer'),
        ('PRODUCT', 'Product'),
        ('USER', 'User'),
        ('CHANNEL', 'Channel'),
        ('KIOTVIET_JOB', 'KiotViet Job'),
        ('OTHER', 'Other'),
    ]

    # db_index=False: index FK một cột mà Django tự sinh
    # (`audit_auditlog_user_id_c1cca96c`, 24MB) đã được phủ hoàn toàn bởi
    # `(user, -timestamp)` bên dưới — B-tree composite phục vụ được mọi truy vấn
    # chỉ lọc theo cột dẫn đầu. Đo prod 2026-08-17: 0 lượt quét sau 13,7 ngày
    # uptime, trong khi index composite có 12. Cùng cách xử lý đã áp cho
    # `Customer.phone_normalized` ở audit 2026-07 mục 3.3.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_logs',
        db_index=False,
    )
    action_type = models.CharField(max_length=20, choices=ACTION_TYPES)
    entity_type = models.CharField(max_length=20, choices=ENTITY_TYPES)
    entity_id = models.CharField(max_length=100, null=True, blank=True)  # ID của entity bị tác động
    method = models.CharField(max_length=10, null=True, blank=True)  # HTTP method (GET, POST, PUT, DELETE, etc.)
    timestamp = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    response_data = models.JSONField(null=True, blank=True)  # Dữ liệu response
    duration_ms = models.PositiveIntegerField(null=True, blank=True)  # Thời gian xử lý (ms)
    metadata = models.JSONField(null=True, blank=True)  # Dữ liệu bổ sung
    source = models.CharField(max_length=20, choices=[('user_action', 'User Action'), ('api_request', 'API Request'), ('kiotviet', 'KiotViet')], default='user_action')
    curl_command = models.TextField(null=True, blank=True)  # Lưu curl command

    class Meta:
        db_table = "audit_logs"
        indexes = [
            models.Index(fields=['-timestamp']),
            models.Index(fields=['user', '-timestamp']),
            models.Index(fields=['action_type', '-timestamp']),
            models.Index(fields=['entity_type', 'entity_id', '-timestamp']),
            models.Index(fields=['source', '-timestamp']),
            # Tồn tại trên prod từ trước nhưng KHÔNG có trong repo (phát hiện
            # 2026-08-17: `git grep audit_entity_id_ts_idx` không ra kết quả nào)
            # — index tạo tay thẳng trên prod, nên DB dựng lại từ migration sẽ
            # thiếu nó. Khai báo ở đây để hết drift.
            #
            # Giữ chứ không drop dù 0 lượt quét: `entity_id` là `filterset_fields`
            # độc lập của `AuditLogViewSet` với `ordering = ['-timestamp']`, tức
            # `?entity_id=<id>` không kèm `entity_type` là đường tra hợp lệ
            # ("mọi thao tác lên đơn X, bất kể loại"). Index composite
            # `(entity_type, entity_id, -timestamp)` KHÔNG phục vụ được truy vấn
            # đó vì thiếu cột dẫn đầu.
            models.Index(fields=['entity_id', '-timestamp'], name='audit_entity_id_ts_idx'),
        ]
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.action_type} - {self.entity_type} - {self.timestamp}"
