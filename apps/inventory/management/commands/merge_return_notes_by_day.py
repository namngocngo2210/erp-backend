"""Gộp hồi tố phiếu trả hàng CŨ (1 phiếu ↔ 1 đơn) thành phiếu NGÀY (1 phiếu / ngày / kho).

Phạm vi (rất hẹp, cố ý): lệnh CHỈ đụng vào phiếu CŨ — mã không bắt đầu bằng `PTH-`
(migration 0022_return_entry để lại, dạng `THyyyymmdd-NNNN`). Phiếu ngày của hệ thống MỚI
(`PTH-...`, kể cả phiếu tách `-02`, `-03`) là chứng từ người dùng đã nhìn thấy và là
ranh giới kế toán do kho chủ động chốt → KHÔNG BAO GIỜ đổi mã, không gộp vào nhau.
Nhóm (ngày, kho) không có phiếu cũ nào → bỏ qua hoàn toàn.

An toàn theo thiết kế: lệnh chỉ đổi con trỏ `ReturnEntry.return_note_id`, đổi mã phiếu
CŨ được giữ lại và xoá phiếu CŨ đã RỖNG. `StockMovement` trỏ vào ENTRY (không trỏ
phiếu) nên tồn kho không thể đổi. Dù vậy vẫn đối soát tồn + movement trước/sau trong
cùng transaction (REPEATABLE READ) và rollback nếu lệch.

Ba cạm bẫy được xử lý tường minh:
  1. `ReturnEntry.return_note` là CASCADE → xoá nhầm phiếu còn entry sẽ xoá luôn entry
     đã ghi sổ (movement thành mồ côi). Luôn re-home hết entry rồi mới xoá, và assert
     phiếu sắp xoá có 0 entry.
  2. Hệ thống chạy LIVE → (ngày, kho) có thể ĐÃ có phiếu `PTH-` thật. Khi đó GIỮ phiếu
     đó (ưu tiên phiếu đang MỞ — đó là phiếu kho đang quét vào), entry của phiếu cũ
     dồn vào nó, và tuyệt đối không đổi mã/status của nó.
  3. Race với luồng quét: lấy advisory lock NGÀY (cùng lock class với
     `ReturnNoteService.resolve_open_note`) TRƯỚC khi mở transaction.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import Count, Sum

from apps.inventory.application.services.return_note_service import (
    ReturnNoteService,
    lock_day_session,
    unlock_all_day_locks,
)
from apps.inventory.models import (
    ReturnEntry,
    ReturnNote,
    ReturnNoteStatus,
    StockItem,
    StockMovement,
)

DAILY_PREFIX = "PTH-"
RETURN_ENTRY_REFERENCE = "return_entry"
REPEATABLE_READ = "repeatable read"

# Mỗi (ngày, kho) có phiếu cũ = 1 advisory lock SESSION giữ suốt lệnh. Bảng khoá dùng
# CHUNG toàn server và chỉ có ~ max_locks_per_transaction * (max_connections +
# max_prepared_transactions) slot. Vượt ngưỡng → "out of shared memory: increase
# max_locks_per_transaction" (an toàn: không ghi gì, nhưng chết lệnh). Chỉ dám xin một
# phần nhỏ ngân sách vì phần còn lại là của cả hệ thống đang chạy.
LOCK_BUDGET_FRACTION = 0.20


def _advisory_lock_budget() -> int:
    """Xấp xỉ số slot của bảng khoá chung: max_locks_per_transaction * max_connections."""
    with connection.cursor() as cursor:
        cursor.execute("SHOW max_locks_per_transaction;")
        per_tx = int(cursor.fetchone()[0])
        cursor.execute("SHOW max_connections;")
        max_conn = int(cursor.fetchone()[0])
    return per_tx * max_conn


def _month_slices(day_keys: list[tuple[date, int]]) -> list[date]:
    """Mốc `--date-from` gợi ý: mùng 1 của từng tháng có phiếu cũ, MỚI NHẤT trước.

    Chỉ có `--date-from` (không có `--date-to`) nên phải chạy từ tháng mới nhất lùi dần:
    sau mỗi lát, phiếu cũ của tháng đó đã thành `PTH-` nên lát sau không khoá lại chúng.
    """
    months = {date(day.year, day.month, 1) for day, _wh in day_keys}
    return sorted(months, reverse=True)


def _stock_snapshot() -> dict[tuple[int, int, str], int]:
    return {
        (row["warehouse_id"], row["product_id"], row["state"]): row["total"]
        for row in StockItem.objects.values("warehouse_id", "product_id", "state")
        .annotate(total=Sum("quantity"))
        .order_by()
    }


def _movement_snapshot() -> dict[str, int]:
    agg = StockMovement.objects.aggregate(count=Count("id"), total=Sum("qty_delta"))
    return {"count": agg["count"] or 0, "total": agg["total"] or 0}


def _orphan_movements() -> int:
    """Movement trỏ tới entry không còn tồn tại — phải luôn bằng 0."""
    return (
        StockMovement.objects.filter(reference_type=RETURN_ENTRY_REFERENCE)
        .exclude(reference_id__in=ReturnEntry.objects.values_list("id", flat=True))
        .count()
    )


def _isolation_level() -> str:
    with connection.cursor() as cursor:
        cursor.execute("SHOW transaction_isolation;")
        return cursor.fetchone()[0]


def _rehome_entries(drop_ids: list[int], keep_id: int) -> int:
    """Chuyển mọi entry của phiếu sắp xoá sang phiếu giữ lại. Trả về số dòng đã chuyển."""
    return ReturnEntry.objects.filter(return_note_id__in=drop_ids).update(
        return_note_id=keep_id
    )


def _is_legacy(note: ReturnNote) -> bool:
    return not note.code.startswith(DAILY_PREFIX)


def _pick_keep(notes: list[ReturnNote]) -> ReturnNote:
    """Phiếu được giữ lại cho (ngày, kho) — chỉ gọi khi nhóm CÓ phiếu cũ.

    Có phiếu `PTH-` trong nhóm → giữ nó, ưu tiên phiếu ĐANG MỞ vì đó là phiếu kho
    vẫn đang quét vào (dồn entry cũ vào phiếu đã chốt = mở lại một ranh giới kế toán
    đã đóng). Không có phiếu mở → lấy phiếu `PTH-` id nhỏ nhất (phiếu gốc trong ngày).
    Cả hai trường hợp: KHÔNG đổi mã, KHÔNG đổi status của phiếu `PTH-`.
    Nhóm toàn phiếu cũ → giữ phiếu cũ id nhỏ nhất rồi đổi tên thành mã ngày.
    """
    daily = [n for n in notes if not _is_legacy(n)]
    if daily:
        open_daily = [n for n in daily if n.status == ReturnNoteStatus.OPEN]
        return min(open_daily or daily, key=lambda n: n.id)
    return min(notes, key=lambda n: n.id)


def _target_code(doc_date: date, warehouse_id: int, keep_id: int) -> str:
    base = f"{DAILY_PREFIX}{doc_date:%Y%m%d}-K{warehouse_id}"
    taken = ReturnNote.objects.filter(code=base).exclude(id=keep_id).exists()
    if not taken:
        return base
    # Mã chuẩn đã bị phiếu khác (ngoài nhóm) chiếm — không đụng vào nó.
    return ReturnNoteService._next_code(doc_date, warehouse_id)


def _parse_date(raw: str) -> date:
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise CommandError(f"--date-from không hợp lệ: {raw!r} (cần YYYY-MM-DD)") from exc


class Command(BaseCommand):
    help = "Gộp phiếu trả hàng CŨ (mã TH...) theo (ngày, kho) thành 1 phiếu ngày PTH-."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--date-from", default="")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Bỏ qua guard số advisory lock (chỉ dùng khi đã nâng "
            "max_locks_per_transaction).",
        )
        parser.add_argument(
            "--allow-read-committed",
            action="store_true",
            help="Cho phép chạy bên trong transaction có sẵn ở mức READ COMMITTED "
            "(CHỈ dành cho test — đối soát tồn sẽ yếu đi).",
        )

    def handle(self, *args, **opts):
        dry = opts["dry_run"]
        date_from = _parse_date(opts["date_from"]) if opts["date_from"] else None
        allow_read_committed = opts["allow_read_committed"]

        # Gác TRƯỚC khi lấy khoá: chạy trong transaction có sẵn (Celery task,
        # ATOMIC_REQUESTS, shell đang mở tx) trông y hệt test bị pytest-django bọc, nhưng
        # hậu quả khác hẳn — mức cô lập tụt về READ COMMITTED và bất biến tồn trở lại bản
        # yếu/báo động giả. Chỉ test được phép mở cửa này, bằng cờ tường minh.
        if not transaction.get_autocommit() and not allow_read_committed:
            raise CommandError(
                "Lệnh đang chạy BÊN TRONG một transaction có sẵn → không thể đặt "
                "REPEATABLE READ, đối soát tồn trước/sau sẽ vô nghĩa. Chạy lệnh ở chế độ "
                "autocommit (manage.py trực tiếp), không gọi trong transaction.atomic() / "
                "Celery task / ATOMIC_REQUESTS. Chỉ test mới được dùng "
                "--allow-read-committed."
            )

        legacy_qs = ReturnNote.objects.exclude(code__startswith=DAILY_PREFIX)
        if date_from is not None:
            legacy_qs = legacy_qs.filter(doc_date__gte=date_from)
        day_keys = sorted(
            set(legacy_qs.values_list("doc_date", "warehouse_id").distinct())
        )
        if not day_keys:
            self.stdout.write(self.style.SUCCESS("Không còn phiếu cũ nào cần gộp."))
            return

        self.stdout.write(f"Số (ngày, kho) có phiếu cũ → số advisory lock: {len(day_keys)}")
        self._assert_lock_budget(day_keys, force=opts["force"])

        try:
            # Khoá NGÀY phải lấy TRƯỚC khi mở transaction: snapshot REPEATABLE READ được
            # chốt ngay ở câu lệnh ĐẦU TIÊN của transaction, nên nếu khoá muộn (bên trong
            # transaction) ta vẫn có thể không "thấy" phiếu mà người quét vừa commit trong
            # lúc ta chờ khoá → đổi mã phiếu là va unique(code). Giữ khoá session trước →
            # mọi luồng quét (resolve_open_note dùng cùng lock class) xếp hàng sau lệnh.
            # Vòng lấy khoá nằm TRONG try: hỏng giữa chừng (hoặc Ctrl-C) mà không có
            # finally thì trên connection POOLED (CONN_MAX_AGE, Celery/shell) các khoá đã
            # lấy sẽ rò rỉ theo session → mọi lượt quét hoàn của những ngày đó treo vĩnh viễn.
            for doc_date, warehouse_id in day_keys:
                lock_day_session(doc_date, warehouse_id)
            self._run(day_keys, dry=dry, allow_read_committed=allow_read_committed)
        finally:
            unlock_all_day_locks()

    def _assert_lock_budget(self, day_keys: list[tuple[date, int]], *, force: bool) -> None:
        """Chặn trước khi bảng khoá CHUNG của server hết chỗ (out of shared memory)."""
        budget = _advisory_lock_budget()
        limit = int(budget * LOCK_BUDGET_FRACTION)
        self.stdout.write(
            f"Ngân sách khoá xấp xỉ: {budget} slot → ngưỡng an toàn {limit}."
        )
        if len(day_keys) <= limit:
            return
        if force:
            self.stdout.write(
                self.style.WARNING(
                    f"--force: vẫn xin {len(day_keys)} khoá dù vượt ngưỡng {limit}."
                )
            )
            return
        slices = _month_slices(day_keys)
        suggested = "\n".join(
            f"  python manage.py merge_return_notes_by_day --date-from {day:%Y-%m-%d}"
            for day in slices
        )
        raise CommandError(
            f"{len(day_keys)} (ngày, kho) → {len(day_keys)} advisory lock session, vượt "
            f"ngưỡng an toàn {limit} của bảng khoá chung "
            f"(max_locks_per_transaction * max_connections = {budget}). Sẽ chết giữa chừng "
            "với 'out of shared memory'. Chạy CẮT LÁT theo --date-from, THEO ĐÚNG THỨ TỰ "
            "dưới đây (mới nhất trước — mỗi lát xong thì phiếu của lát đó đã thành PTH- nên "
            "lát sau không khoá lại):\n"
            f"{suggested}\n"
            "Hoặc --force nếu đã nâng max_locks_per_transaction và chấp nhận rủi ro."
        )

    def _run(
        self,
        day_keys: list[tuple[date, int]],
        *,
        dry: bool,
        allow_read_committed: bool = False,
    ) -> None:
        today = ReturnNoteService.today_vn()
        # Django chỉ thực sự BEGIN khi có câu lệnh đầu tiên → SET dưới đây là câu lệnh
        # đầu tiên của transaction (yêu cầu bắt buộc của Postgres). Chỉ SET được khi ta
        # tự mở transaction từ autocommit; trong test có --allow-read-committed,
        # pytest-django đã bọc sẵn 1 transaction nên bỏ qua (savepoint, không concurrency).
        was_autocommit = transaction.get_autocommit()

        merged = dropped = rehomed = skipped = 0
        with transaction.atomic():
            if was_autocommit:
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ;")
            isolation = _isolation_level()
            if was_autocommit and isolation != REPEATABLE_READ:
                raise CommandError(
                    f"Không đặt được REPEATABLE READ (đang là {isolation!r}) — dừng. "
                    "Đối soát tồn trước/sau chỉ có nghĩa dưới snapshot cố định."
                )
            self.stdout.write(f"Mức cô lập: {isolation}")
            if not was_autocommit and allow_read_committed:
                self.stdout.write(
                    self.style.WARNING(
                        "--allow-read-committed: chạy trong transaction có sẵn, giữ nguyên "
                        "mức cô lập của transaction ngoài. Đối soát tồn chỉ còn giá trị khi "
                        "không có ai ghi song song (test)."
                    )
                )

            # Đọc nhóm BÊN TRONG transaction (và sau khi đã giữ khoá ngày).
            groups: dict[tuple[date, int], list[ReturnNote]] = defaultdict(list)
            days = {key[0] for key in day_keys}
            warehouses = {key[1] for key in day_keys}
            key_set = set(day_keys)
            for note in ReturnNote.objects.filter(
                doc_date__in=days, warehouse_id__in=warehouses
            ).order_by("id"):
                key = (note.doc_date, note.warehouse_id)
                if key in key_set:
                    groups[key].append(note)

            before_stock = _stock_snapshot()
            before_moves = _movement_snapshot()
            before_orphans = _orphan_movements()

            for (doc_date, warehouse_id), notes in sorted(groups.items()):
                legacy = [n for n in notes if _is_legacy(n)]
                if not legacy:
                    # Nhóm toàn phiếu PTH- (kể cả -02 do kho chủ động tách) → không đụng.
                    skipped += 1
                    continue

                keep = _pick_keep(notes)
                # Chỉ phiếu CŨ mới bị xoá. Phiếu PTH- khác trong ngày giữ nguyên.
                drop_ids = [n.id for n in legacy if n.id != keep.id]

                moved = _rehome_entries(drop_ids, keep.id) if drop_ids else 0
                rehomed += moved

                # CASCADE guard: tuyệt đối không xoá phiếu còn entry.
                leftover = ReturnEntry.objects.filter(
                    return_note_id__in=drop_ids
                ).count()
                if leftover:
                    raise CommandError(
                        f"{doc_date} K{warehouse_id}: còn {leftover} nhóm đơn ở phiếu "
                        "sắp xoá — rollback (CASCADE sẽ xoá mất dữ liệu đã ghi sổ)."
                    )
                if drop_ids:
                    # Xoá TRƯỚC khi đổi mã/status của phiếu giữ lại: tránh đụng
                    # unique(code) và partial-unique 1-phiếu-mở-mỗi-ngày.
                    _, per_model = ReturnNote.objects.filter(id__in=drop_ids).delete()
                    dropped += per_model.get("inventory.ReturnNote", 0)

                if _is_legacy(keep):
                    self._promote_legacy_keep(keep, doc_date, warehouse_id, today)
                merged += 1
                self.stdout.write(
                    f"{keep.code}: {len(legacy)} phiếu cũ → 1 "
                    f"({moved} nhóm đơn chuyển, {keep.status})"
                )

            self._assert_no_stock_drift(
                before_stock, before_moves, before_orphans
            )

            if dry:
                self.stdout.write(self.style.WARNING("--dry-run: rollback, không ghi gì."))
                transaction.set_rollback(True)

        self.stdout.write(
            self.style.SUCCESS(
                f"{'[dry-run] ' if dry else ''}Gộp {merged} phiếu ngày, "
                f"chuyển {rehomed} nhóm đơn, xoá {dropped} phiếu rỗng, "
                f"bỏ qua {skipped} nhóm không có phiếu cũ. Tồn không đổi."
            )
        )

    def _promote_legacy_keep(
        self, keep: ReturnNote, doc_date: date, warehouse_id: int, today: date
    ) -> None:
        """Phiếu CŨ được giữ lại → đổi thành phiếu ngày chuẩn, status khớp closed_at."""
        target_status = (
            ReturnNoteStatus.OPEN if doc_date == today else ReturnNoteStatus.CLOSED
        )
        keep.code = _target_code(doc_date, warehouse_id, keep.id)
        keep.status = target_status
        if target_status == ReturnNoteStatus.OPEN:
            # Phiếu mở không được mang dấu vết "chốt lúc ..." kế thừa từ phiếu cũ.
            keep.closed_at = None
            keep.closed_by_id = None
        else:
            keep.closed_at = keep.closed_at or keep.created_at
        keep.save(update_fields=["code", "status", "closed_at", "closed_by"])

    def _assert_no_stock_drift(
        self,
        before_stock: dict[tuple[int, int, str], int],
        before_moves: dict[str, int],
        before_orphans: int,
    ) -> None:
        """So sánh trong CÙNG snapshot REPEATABLE READ → chênh lệch chỉ có thể do
        chính lệnh này gây ra (dưới READ COMMITTED thì bất kỳ giao dịch xuất/nhập nào
        của người khác commit xen vào cũng làm lệch → báo động giả)."""
        after_stock = _stock_snapshot()
        after_moves = _movement_snapshot()
        after_orphans = _orphan_movements()

        if before_stock != after_stock:
            diff = {
                key
                for key in set(before_stock) | set(after_stock)
                if before_stock.get(key) != after_stock.get(key)
            }
            raise CommandError(f"LỆCH TỒN sau khi gộp — rollback. Khoá lệch: {diff}")
        if before_moves != after_moves:
            raise CommandError(
                f"LỆCH SỔ MOVEMENT — rollback. before={before_moves} after={after_moves}"
            )
        if after_orphans != before_orphans:
            raise CommandError(
                f"MOVEMENT MỒ CÔI ({after_orphans} dòng, trước đó {before_orphans}) "
                "— rollback."
            )
