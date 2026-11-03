"""Xếp hàng + gọi Gemini + ghi nhãn cho hội thoại chat.

Khuôn bê từ apps/callcenter/extract.py: cooldown riêng khi 429, lỗi hạ tầng
nhất thời KHÔNG đốt lượt attempt, lỗi 1 dòng không chặn dòng khác, dòng kẹt
PROCESSING quá lâu được trả về PENDING. KHÔNG log nội dung hội thoại hay
message lỗi thô (PII) — chỉ log id, tên field, và lớp lỗi đã phân loại.

Hàng đợi nằm ở ai_stg.hoi_thoai_nhan; nguồn xếp hàng là ai.hoi_thoai (đã ETL),
nên vòng này KHÔNG chạm foreign table: FDW chết vẫn gán nhãn tiếp được phần
tồn đọng.

Nạp từ điển (need_labels/reason_labels) ĐÚNG MỘT LẦN mỗi lô, không phải mỗi
hội thoại: build_chat_prompt/build_chat_response_schema của Task 6 tự nạp lại
nếu không truyền vào, mà cả lô dùng chung một bộ mã hợp lệ tại một thời điểm —
truyền một lần rồi tái dùng cho toàn lô là đủ, không cần lo dữ liệu cũ vì mỗi
lần gọi run_batch() đều nạp mới.
"""

from __future__ import annotations

import json
import logging

from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.core.cache import cache
from django.db import connections, transaction

from apps.ai_mart.domain.chat_extract import (
    build_chat_prompt,
    build_chat_response_schema,
    load_need_labels,
    load_reason_labels,
    normalize_chat_extracted,
)
from apps.ai_mart.infrastructure.loaders import TARGET_ALIAS
from apps.sysconfig import sysconfig

logger = logging.getLogger(__name__)

# Cờ cooldown RIÊNG của luồng gán nhãn chat — không dùng chung với
# apps.callcenter.extract.EXTRACT_COOLDOWN_KEY để 429 của luồng này không khoá
# luôn luồng transcript cuộc gọi (và ngược lại).
COOLDOWN_KEY = "gemini:chat_extract:cooldown"

_TRANSIENT_MARKERS = ("429", "500", "503", "resource_exhausted", "unavailable",
                      "deadline", "timeout", "timed out", "connection")
_QUOTA_MARKERS = ("429", "resource_exhausted")

# Dòng kẹt PROCESSING quá lâu (worker bị kill giữa chừng, hoặc DB lỗi đúng lúc
# giữa _claim và _save_done/_save_error) được trả về PENDING — khuôn y hệt
# apps/callcenter/extract.py._requeue_stuck_processing. Không có cơ chế này,
# hội thoại có NỘI DUNG KHÔNG ĐỔI kẹt processing sẽ kẹt VĨNH VIỄN:
# enqueue_pending chỉ đánh thức lại khi hash đổi, _claim chỉ chọn 'pending'.
STUCK_PROCESSING_MINUTES = 30


class ChatExtractError(Exception):
    """Lỗi gán nhãn 1 hội thoại. Message giữ nguyên văn để lưu vào hàng đợi
    (cột extract_error nằm ở ai_stg, KHÔNG cấp quyền cho hermes_ro) — nhưng
    KHÔNG BAO GIỜ được log ra: SDK/DB có thể vọng lại một phần nội dung
    request (hội thoại khách). Log chỉ dùng ``origin``/``transient``/
    ``quota_exhausted``.

    ``transient=True`` = lỗi hạ tầng nhất thời (mạng, 429/5xx): KHÔNG đốt lượt
    attempt — cùng bài học audit 2026-07-18 của transcribe, Gemini chập chờn
    15 phút mà đốt attempt là hàng loạt record FAILED oan.

    ``quota_exhausted=True`` = Google trả 429: nghỉ trọn chu kỳ cooldown thay vì
    dội request vào tường.
    """

    def __init__(self, message: str, *, transient: bool = False, quota_exhausted: bool = False,
                 origin: str | None = None):
        super().__init__(message)
        self.transient = transient
        self.quota_exhausted = quota_exhausted
        # Lớp exception gốc (vd. "ConnectionError") — dùng để LOG an toàn thay
        # vì log message; mặc định là chính lớp này khi lỗi không bọc gì.
        self.origin = origin or type(self).__name__


def _conn():
    return connections[TARGET_ALIAS]


def _classify(exc: Exception, prefix: str) -> ChatExtractError:
    """Bọc 1 exception bất kỳ (Gemini SDK, DB, ...) thành ChatExtractError,
    suy transient/quota_exhausted từ marker trong message. Dùng chung cho lỗi
    gọi Gemini lẫn lỗi hạ tầng khác xảy ra giữa lúc xử lý 1 dòng, để cả hai
    loại đều không đốt attempt oan khi thật sự chỉ là trục trặc nhất thời."""
    lowered = str(exc).lower()
    return ChatExtractError(
        f"{prefix}: {exc}",
        transient=any(m in lowered for m in _TRANSIENT_MARKERS),
        quota_exhausted=any(m in lowered for m in _QUOTA_MARKERS),
        origin=type(exc).__name__,
    )


def _log_error(hoi_thoai_id: int, exc: ChatExtractError) -> None:
    logger.warning(
        "gán nhãn chat lỗi hoi_thoai_id=%s lop=%s transient=%s quota_exhausted=%s",
        hoi_thoai_id, exc.origin, exc.transient, exc.quota_exhausted,
    )


def _log_quota_stopped(exc: ChatExtractError) -> None:
    logger.warning(
        "gán nhãn chat nghỉ %s phút vì Gemini hết quota (lop=%s)",
        settings.GEMINI_QUOTA_COOLDOWN_MINUTES, exc.origin,
    )


_ENQUEUE_SQL = """
INSERT INTO ai_stg.hoi_thoai_nhan (hoi_thoai_id, noi_dung_hash, extract_status)
SELECT h.hoi_thoai_id, md5(h.noi_dung), 'pending'
FROM ai.hoi_thoai h
WHERE h.so_tin_khach > 0
  AND h.noi_dung IS NOT NULL
  AND length(h.noi_dung) >= %(min_chars)s
ON CONFLICT (hoi_thoai_id) DO UPDATE
SET extract_status = 'pending',
    extract_attempts = 0,
    noi_dung_hash = EXCLUDED.noi_dung_hash,
    updated_at = now()
WHERE hoi_thoai_nhan.noi_dung_hash IS DISTINCT FROM EXCLUDED.noi_dung_hash
"""


def enqueue_pending() -> int:
    """Xếp hàng hội thoại đủ điều kiện. Nội dung không đổi thì KHÔNG gán nhãn lại.

    Có thể chạy song song với run_batch() của tick khác (ai_mart_chat_extract
    gọi 2 hàm không cùng 1 khoá) — nếu 1 dòng đang 'processing' mà hash đổi,
    câu lệnh này đánh thức nó về 'pending' NGAY LẬP TỨC bất kể nó đang dở dang.
    Đó là lý do _save_done/_save_error/_save_skipped đều tự khoá bằng "AND
    extract_status = 'processing'": worker đang xử lý nội dung CŨ phát hiện
    dòng đã bị đánh thức lại thì bỏ qua việc ghi, không đè kết quả cũ lên
    trạng thái mới.
    """
    with _conn().cursor() as cur:
        cur.execute(_ENQUEUE_SQL, {"min_chars": settings.AI_CHAT_EXTRACT_MIN_CHARS})
        return cur.rowcount


_REQUEUE_STUCK_SQL = f"""
UPDATE ai_stg.hoi_thoai_nhan
SET extract_status = 'pending', updated_at = now()
WHERE extract_status = 'processing'
  AND updated_at < now() - interval '{STUCK_PROCESSING_MINUTES} minutes'
"""


def _requeue_stuck_processing() -> int:
    with _conn().cursor() as cur:
        cur.execute(_REQUEUE_STUCK_SQL)
        return cur.rowcount


# Ưu tiên hội thoại khách nói nhiều nhất, mới nhất trước — backfill theo lô
# giảm dần (spec §6).
_CLAIM_SQL = """
WITH lo AS (
    SELECT n.hoi_thoai_id
    FROM ai_stg.hoi_thoai_nhan n
    JOIN ai.hoi_thoai h ON h.hoi_thoai_id = n.hoi_thoai_id
    WHERE n.extract_status = 'pending'
    -- Dòng vừa lỗi trong 1 giờ bị đẩy XUỐNG CUỐI. Không có mệnh đề này thì một
    -- hội thoại lớn đến mức không bao giờ xong sẽ chặn đầu hàng đợi vĩnh viễn:
    -- nó đứng đầu theo so_tin_khach, Gemini trả 504 DEADLINE_EXCEEDED, chuỗi
    -- "deadline" khớp _TRANSIENT_MARKERS nên KHÔNG đốt attempt, về pending rồi
    -- lượt sau lại claim nó đầu tiên. Đo trên prod 07/09/2026: đúng 1 hội thoại
    -- (631 tin khách, 82.891 ký tự) ăn 4/20 slot ở MỌI lượt beat — mất 22 phần trăm công
    -- suất. Lỗi nhất thời thật thì sau 1 giờ dòng đó trở lại ưu tiên bình thường.
    ORDER BY (n.extract_error <> '' AND n.updated_at > now() - interval '1 hour') ASC,
             h.so_tin_khach DESC, h.ket_thuc DESC NULLS LAST
    LIMIT %(limit)s
    FOR UPDATE OF n SKIP LOCKED
)
UPDATE ai_stg.hoi_thoai_nhan n
SET extract_status = 'processing', updated_at = now()
FROM lo
WHERE n.hoi_thoai_id = lo.hoi_thoai_id
RETURNING n.hoi_thoai_id
"""


def _claim(limit: int) -> list[int]:
    with transaction.atomic(using=TARGET_ALIAS):
        with _conn().cursor() as cur:
            cur.execute(_CLAIM_SQL, {"limit": limit})
            return [row[0] for row in cur.fetchall()]


def _generate_json(prompt: str, response_schema: dict) -> str:
    """Gọi Gemini với structured output. Hàm riêng để test mock được."""
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:  # pragma: no cover - thiếu dependency là lỗi deploy
        raise ChatExtractError(f"Thiếu thư viện google-genai: {exc}") from exc

    client = genai.Client(
        api_key=sysconfig.get("GEMINI_API_KEY"), http_options=types.HttpOptions(timeout=120_000)
    )
    response = client.models.generate_content(
        model=sysconfig.get("GEMINI_EXTRACT_MODEL"),
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=response_schema,
            temperature=0,   # đọc hiểu, không cần sáng tạo
        ),
    )
    return getattr(response, "text", None) or ""


def _extract_one(
    noi_dung: str,
    need_labels: dict[str, str],
    reason_labels: dict[str, str],
    response_schema: dict,
) -> tuple[dict, dict, dict]:
    try:
        prompt = build_chat_prompt(noi_dung, need_labels, reason_labels)
        text = _generate_json(prompt, response_schema)
    except ChatExtractError:
        raise
    except (SoftTimeLimitExceeded, KeyboardInterrupt, SystemExit):
        # PHẢI đứng trước `except Exception`: cả ba đều kế thừa Exception, để rơi
        # xuống dưới là chúng bị bọc thành ChatExtractError và guard ở run_batch
        # KHÔNG BAO GIỜ thấy — đo trên prod 07/09/2026: 8 dòng `failed` mang lỗi
        # "Gemini lỗi: SoftTimeLimitExceeded()", tức attempt bị đốt oan đúng cái
        # mà guard kia sinh ra để chặn. Guard đặt đúng tầng nhưng sai lớp.
        raise
    except Exception as exc:  # noqa: BLE001 — giữ nguyên văn lỗi SDK cho dòng hàng đợi
        raise _classify(exc, "Gemini lỗi") from exc

    text = (text or "").strip()
    if not text:
        raise ChatExtractError("Gemini trả về response rỗng.")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ChatExtractError(f"Gemini trả về JSON không hợp lệ: {exc}") from exc
    if not isinstance(raw, dict):
        raise ChatExtractError("Gemini trả về JSON không phải object.")
    clean, rejected = normalize_chat_extracted(raw, need_labels, reason_labels)
    return raw, clean, rejected


def _save_done(hoi_thoai_id: int, raw, clean, rejected, *, cat_bot: bool = False) -> None:
    # AND extract_status = 'processing': nếu enqueue_pending() đã đánh thức lại
    # dòng này (nội dung đổi) trong lúc worker đang gọi Gemini, dòng đã về
    # 'pending' với hash MỚI — ghi đè 'done' ở đây sẽ khoá cứng kết quả CŨ vĩnh
    # viễn (hash đã đổi nên không lần enqueue nào sau còn đánh thức lại được).
    # Không khớp WHERE thì UPDATE này là no-op — đúng ý: dòng nằm lại 'pending'
    # với nội dung mới, chờ lượt claim kế tiếp.
    with _conn().cursor() as cur:
        cur.execute(
            "UPDATE ai_stg.hoi_thoai_nhan SET extract_payload = %s, extract_status = 'done',"
            " extract_error = '', model = %s, updated_at = now()"
            " WHERE hoi_thoai_id = %s AND extract_status = 'processing'",
            # cat_bot ghi vào payload để về sau truy được nhãn nào sinh từ hội
            # thoại đã bị cắt bớt — im lặng cắt rồi coi như bình thường là giấu
            # một suy giảm chất lượng.
            [json.dumps({"raw": raw, "clean": clean, "rejected": rejected,
                         "cat_bot": cat_bot}),
             sysconfig.get("GEMINI_EXTRACT_MODEL"), hoi_thoai_id],
        )


def _save_error(hoi_thoai_id: int, message: str, *, transient: bool) -> None:
    # Lỗi hạ tầng nhất thời KHÔNG đốt lượt attempt (bài học audit transcribe
    # 2026-07-18: Gemini chập chờn 15 phút mà đốt attempt là hàng loạt dòng
    # FAILED oan). Cùng lý do "AND extract_status = 'processing'" như _save_done.
    with _conn().cursor() as cur:
        cur.execute(
            "UPDATE ai_stg.hoi_thoai_nhan"
            " SET extract_attempts = extract_attempts + %s,"
            "     extract_error = %s,"
            "     extract_status = CASE WHEN extract_attempts + %s >= %s"
            "                           THEN 'failed' ELSE 'pending' END,"
            "     updated_at = now()"
            " WHERE hoi_thoai_id = %s AND extract_status = 'processing'",
            [0 if transient else 1, message[:2000], 0 if transient else 1,
             settings.AI_CHAT_EXTRACT_MAX_ATTEMPTS, hoi_thoai_id],
        )


def _save_skipped(hoi_thoai_id: int) -> None:
    with _conn().cursor() as cur:
        cur.execute(
            "UPDATE ai_stg.hoi_thoai_nhan SET extract_status = 'skipped', updated_at = now()"
            " WHERE hoi_thoai_id = %s AND extract_status = 'processing'",
            [hoi_thoai_id],
        )


def _requeue(ids: list[int]) -> None:
    if not ids:
        return
    with _conn().cursor() as cur:
        cur.execute(
            "UPDATE ai_stg.hoi_thoai_nhan SET extract_status = 'pending', updated_at = now()"
            " WHERE hoi_thoai_id = ANY(%s) AND extract_status = 'processing'",
            [ids],
        )


def _load_noi_dung(hoi_thoai_id: int) -> str:
    with _conn().cursor() as cur:
        cur.execute("SELECT noi_dung FROM ai.hoi_thoai WHERE hoi_thoai_id = %s", [hoi_thoai_id])
        row = cur.fetchone()
    return (row[0] if row else "") or ""


def run_batch(limit: int | None = None) -> dict:
    summary = {"claimed": 0, "done": 0, "errors": 0, "skipped": 0, "truncated": 0,
               "quota_stopped": False,
               "requeued_stuck": 0}
    if not sysconfig.get("AI_CHAT_EXTRACT_ENABLED") or not sysconfig.get("GEMINI_API_KEY"):
        return summary

    summary["requeued_stuck"] = _requeue_stuck_processing()
    # Chặn TRƯỚC khi claim: hết quota mà vẫn claim thì mỗi tick ghi 2 lượt
    # UPDATE cả lô để rồi vứt (sự cố transcribe 2026-07-22).
    if cache.get(COOLDOWN_KEY) is not None:
        summary["quota_stopped"] = True
        return summary

    batch = _claim(settings.AI_CHAT_EXTRACT_BATCH if limit is None else limit)
    summary["claimed"] = len(batch)
    if not batch:
        return summary

    # Nạp từ điển + dựng response_schema ĐÚNG MỘT LẦN cho cả lô — build_chat_prompt/
    # build_chat_response_schema của Task 6 tự truy vấn need_types/crm_catalog
    # nếu không được truyền sẵn; không tái dùng thì mỗi hội thoại trong lô lại
    # đập DB 4 lần chỉ để lắp cùng một request.
    need_labels = load_need_labels()
    reason_labels = load_reason_labels()
    response_schema = build_chat_response_schema(need_labels, reason_labels)

    for index, hoi_thoai_id in enumerate(batch):
        try:
            noi_dung = _load_noi_dung(hoi_thoai_id)
            if len(noi_dung) < settings.AI_CHAT_EXTRACT_MIN_CHARS:
                # _ENQUEUE_SQL đã lọc theo CÙNG ngưỡng này lúc xếp hàng, nên
                # nhánh này gần như không bao giờ chạy trong thực tế — chỉ khi
                # nội dung hội thoại NGẮN LẠI (tin bị xoá ở nguồn) sau khi đã
                # xếp hàng, hoặc AI_CHAT_EXTRACT_MIN_CHARS được nâng lên giữa
                # lúc xếp hàng và lúc chạy batch. Giữ lại như lớp phòng vệ.
                _save_skipped(hoi_thoai_id)
                summary["skipped"] += 1
                continue
            # Cắt bớt hội thoại quá dài. Giữ ĐUÔI chứ không giữ đầu: nhu cầu chốt
            # lại và lý do không mua nằm ở đoạn hội thoại GẦN NHẤT, phần mở đầu
            # thường là chào hỏi. Đo trên prod 07/09/2026: hội thoại dài nhất từng
            # gán nhãn THÀNH CÔNG là 16.831 ký tự, trung vị 2.029; cái 82.891 ký tự
            # thì Gemini trả 504, còn hai cái khác trả JSON bị cắt giữa chừng
            # (output vượt giới hạn token). Ngưỡng 30.000 chỉ đụng 5/60.564 hội thoại.
            cat_bot = len(noi_dung) > settings.AI_CHAT_EXTRACT_MAX_CHARS
            if cat_bot:
                noi_dung = noi_dung[-settings.AI_CHAT_EXTRACT_MAX_CHARS:]
                summary["truncated"] += 1
                logger.info(
                    "cắt bớt hội thoại quá dài hoi_thoai_id=%s còn %s ký tự cuối",
                    hoi_thoai_id, settings.AI_CHAT_EXTRACT_MAX_CHARS,
                )
            raw, clean, rejected = _extract_one(
                noi_dung, need_labels, reason_labels, response_schema
            )
            _save_done(hoi_thoai_id, raw, clean, rejected, cat_bot=cat_bot)
        except (SoftTimeLimitExceeded, KeyboardInterrupt, SystemExit):
            # Phải đứng TRƯỚC `except ChatExtractError`/`except Exception`:
            # SoftTimeLimitExceeded kế thừa Exception, để nó rơi vào nhánh lỗi
            # bên dưới sẽ đốt oan 1 attempt của dòng đang xử lý dở, rồi task vẫn
            # chạy tiếp tới khi bị hard-kill mà không kịp dọn. Trả dòng NÀY và
            # các dòng còn lại trong lô về pending rồi thoát ngay.
            _requeue(batch[index:])
            raise
        except ChatExtractError as exc:
            if exc.quota_exhausted:
                cache.set(COOLDOWN_KEY, 1,
                          timeout=settings.GEMINI_QUOTA_COOLDOWN_MINUTES * 60)
                _requeue(batch[index:])
                summary["quota_stopped"] = True
                _log_quota_stopped(exc)
                break
            _save_error(hoi_thoai_id, str(exc), transient=exc.transient)
            summary["errors"] += 1
            _log_error(hoi_thoai_id, exc)
            continue
        except Exception as exc:  # noqa: BLE001 — lỗi hạ tầng (DB...) không được chặn cả lô còn lại
            wrapped = _classify(exc, "Lỗi hạ tầng")
            _save_error(hoi_thoai_id, str(wrapped), transient=wrapped.transient)
            summary["errors"] += 1
            _log_error(hoi_thoai_id, wrapped)
            continue
        summary["done"] += 1
        # KHÔNG log nội dung hội thoại/giá trị nhãn (PII) — chỉ id và tên field.
        logger.info("gán nhãn chat xong hoi_thoai_id=%s fields=%s",
                    hoi_thoai_id, sorted(clean))
    return summary
