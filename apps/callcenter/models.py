"""Bản ghi cuộc gọi Zenify — nguồn chân lý url ghi âm + transcript theo đơn.

Mỗi dòng = 1 cuộc gọi (callId Zenify, unique để dedupe cửa sổ cron chồng lấn).
1 đơn có N cuộc gọi; đơn khớp qua caller → Order.phone_normalized (loại đơn sàn
shopee/tiktok — sàn che SĐT thật). Xem docs/zenify-call-transcripts.md.
"""

from django.db import models
from django.db.models import Q


class TranscribeStatus(models.IntegerChoices):
    PENDING = 0, "Chờ transcribe"
    PROCESSING = 1, "Đang transcribe"
    DONE = 2, "Hoàn thành"
    FAILED = 3, "Thất bại"
    # Không có file ghi âm hoặc đàm thoại quá ngắn (gọi nhỡ/rớt) — lưu để không
    # kéo lại từ Zenify, nhưng không bao giờ vào hàng đợi transcribe.
    SKIPPED = 4, "Bỏ qua"


class ExtractStatus(models.IntegerChoices):
    """Trạng thái trích xuất chân dung khách từ transcript.

    Tách hẳn khỏi transcribe_status: 1 transcript có thể transcribe xong từ lâu
    mà chưa extract (bật tính năng sau), và ngược lại extract lỗi không được
    kéo transcript về pending làm mất công gọi Gemini lần nữa với audio.
    """

    PENDING = 0, "Chờ extract"
    PROCESSING = 1, "Đang extract"
    DONE = 2, "Hoàn thành"
    FAILED = 3, "Thất bại"
    # Transcript quá ngắn (không có nội dung tư vấn) — lưu để không quét lại mãi.
    SKIPPED = 4, "Bỏ qua"


class ScoreStatus(models.IntegerChoices):
    """Trạng thái chấm chất lượng cuộc gọi từ transcript.

    Tách khỏi extract_status: hai luồng đọc cùng transcript nhưng phục vụ hai
    mục đích khác nhau (điền chân dung khách vs chấm kỹ năng sale), bật/tắt và
    hỏng độc lập với nhau.
    """

    PENDING = 0, "Chờ chấm"
    PROCESSING = 1, "Đang chấm"
    DONE = 2, "Hoàn thành"
    FAILED = 3, "Thất bại"
    # Transcript quá ngắn hoặc cuộc gọi dưới ngưỡng thời lượng — không có nội
    # dung tư vấn để chấm, lưu lại để không quét đi quét lại mãi.
    SKIPPED = 4, "Bỏ qua"


class CallRecord(models.Model):
    call_id = models.CharField(max_length=100, unique=True)
    call_type = models.CharField(max_length=5, blank=True, default="")
    agent_id = models.CharField(max_length=100, blank=True, default="")
    agent_name = models.CharField(max_length=255, blank=True, default="")
    caller = models.CharField(max_length=30, blank=True, default="")
    caller_normalized = models.CharField(max_length=20, blank=True, default="")
    called = models.CharField(max_length=30, blank=True, default="")
    recording_url = models.CharField(max_length=1024, blank=True, default="")
    start_time = models.DateTimeField(null=True, blank=True)
    end_time = models.DateTimeField(null=True, blank=True)
    acd_time = models.PositiveIntegerField(default=0)

    # SET_NULL: xoá đơn không được kéo theo lịch sử cuộc gọi (record quay về
    # trạng thái chưa khớp, không re-match vì match_attempts giữ nguyên).
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        related_name="call_records",
        null=True,
        blank=True,
    )
    matched_at = models.DateTimeField(null=True, blank=True)
    match_attempts = models.PositiveSmallIntegerField(default=0)

    transcribe_status = models.PositiveSmallIntegerField(
        choices=TranscribeStatus.choices, default=TranscribeStatus.PENDING
    )
    transcript = models.TextField(blank=True, default="")
    transcribe_error = models.TextField(blank=True, default="")
    transcribe_attempts = models.PositiveSmallIntegerField(default=0)

    extract_status = models.PositiveSmallIntegerField(
        choices=ExtractStatus.choices, default=ExtractStatus.PENDING
    )
    # Raw output Gemini + phần bị loại (key "rejected") — giữ để soi chất lượng
    # prompt theo thời gian mà không phải gọi lại API.
    extract_payload = models.JSONField(default=dict, blank=True)
    extract_error = models.TextField(blank=True, default="")
    extract_attempts = models.PositiveSmallIntegerField(default=0)

    score_status = models.PositiveSmallIntegerField(
        choices=ScoreStatus.choices, default=ScoreStatus.PENDING
    )
    score_error = models.TextField(blank=True, default="")
    score_attempts = models.PositiveSmallIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "callcenter_call_records"
        indexes = [
            models.Index(fields=["transcribe_status", "id"], name="callrec_status_id_idx"),
            models.Index(fields=["order", "start_time"], name="callrec_order_start_idx"),
            # Vòng re-match chỉ quét record chưa khớp — partial index giữ tập nhỏ.
            models.Index(
                fields=["caller_normalized"],
                condition=Q(order__isnull=True),
                name="callrec_unmatched_phone_idx",
            ),
            models.Index(fields=["extract_status", "id"], name="callrec_extract_status_idx"),
            models.Index(fields=["score_status", "id"], name="callrec_score_status_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - debug helper
        return f"CallRecord({self.call_id}, order={self.order_id})"


class CallQualityScore(models.Model):
    """Điểm chất lượng 1 cuộc gọi theo 1 phiên bản rubric.

    Bảng riêng chứ không thêm cột vào CallRecord: rubric sẽ đổi theo thời gian
    và ta cần giữ CẢ điểm cũ lẫn điểm mới để chứng minh chương trình đào tạo có
    tác dụng. unique(call_record, rubric_version) cho phép chấm lại bằng rubric
    mới mà không mất lịch sử.
    """

    call_record = models.ForeignKey(
        CallRecord, on_delete=models.CASCADE, related_name="quality_scores"
    )
    rubric_version = models.CharField(max_length=16)
    model_name = models.CharField(max_length=64, blank=True, default="")

    # Denormalize để báo cáo không phải join CallRecord/Order — bảng này bị quét
    # theo ngày liên tục. agent_* hiện chưa dùng để hiển thị (Zenify chưa trả
    # định danh nhân viên thật) nhưng vẫn lưu: ngày có dữ liệu đúng thì lịch sử
    # đã sẵn ở đây, chỉ thêm màn hình chứ không phải chấm lại.
    agent_id = models.CharField(max_length=100, blank=True, default="")
    agent_name = models.CharField(max_length=255, blank=True, default="")
    call_date = models.DateField()
    order_id_snapshot = models.BigIntegerField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(default=0)

    total_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    block_scores = models.JSONField(default=dict, blank=True)
    criteria = models.JSONField(default=dict, blank=True)
    red_flags = models.JSONField(default=list, blank=True)
    # Phần output bị loại vì trích dẫn không khớp transcript — giữ để soi chất
    # lượng prompt theo thời gian mà không phải gọi lại API.
    rejected = models.JSONField(default=dict, blank=True)
    summary = models.TextField(blank=True, default="")
    coaching = models.TextField(blank=True, default="")

    wpm = models.PositiveIntegerField(default=0)
    talk_ratio = models.DecimalField(max_digits=4, decimal_places=3, default=0)

    # Đối chiếu chéo với dữ liệu sale tự nhập. sale_* là BẢN CHỤP tại thời điểm
    # chấm — sale sửa status sau đó thì bản chụp giữ nguyên, nếu không thì báo
    # cáo "ghi nhận lệch" tự lành mỗi khi có người sửa lại cho đẹp.
    ai_not_closed_reason = models.TextField(blank=True, default="")
    sale_not_closed_reason_code = models.CharField(max_length=40, blank=True, default="")
    sale_sales_status_code = models.CharField(max_length=40, blank=True, default="")
    contraindications = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "callcenter_call_quality_scores"
        constraints = [
            models.UniqueConstraint(
                fields=["call_record", "rubric_version"], name="callqa_record_version_uniq"
            )
        ]
        indexes = [
            models.Index(fields=["rubric_version", "call_date"], name="callqa_version_date_idx"),
            models.Index(fields=["call_date"], name="callqa_date_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - debug helper
        return f"CallQualityScore(call={self.call_record_id}, {self.rubric_version})"


class CallQualityReview(models.Model):
    """Phản hồi của người về điểm AI chấm.

    KHÔNG ghi đè lên CallQualityScore: giữ nguyên điểm AI mới đo được "AI sai
    bao nhiêu %" theo thời gian, và đó là tập dữ liệu để sửa prompt phiên bản
    sau. Báo cáo hiển thị điểm người khi có, điểm AI khi không.
    """

    score = models.ForeignKey(
        CallQualityScore, on_delete=models.CASCADE, related_name="reviews"
    )
    reviewer = models.ForeignKey(
        "users.User", on_delete=models.PROTECT, related_name="call_quality_reviews"
    )
    # Rỗng = nhận xét cả cuộc gọi, không nhắm vào tiêu chí nào.
    criterion = models.CharField(max_length=32, blank=True, default="")
    ai_score = models.PositiveSmallIntegerField(null=True, blank=True)
    human_score = models.PositiveSmallIntegerField(null=True, blank=True)
    note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "callcenter_call_quality_reviews"
        indexes = [models.Index(fields=["score", "criterion"], name="callqarev_score_crit_idx")]

    def __str__(self) -> str:  # pragma: no cover - debug helper
        return f"CallQualityReview(score={self.score_id}, {self.criterion or 'all'})"
