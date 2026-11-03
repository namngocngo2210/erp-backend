# Async Export Jobs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chuyển 7 luồng xuất file từ chạy đồng bộ trong request cycle sang job nền chạy ở Celery worker riêng, có panel dock hiển thị tiến trình và tải xuống.

**Architecture:** App `apps/exports` là **engine thuần** (model job, registry, writer, storage, task, API) và **không import app nghiệp vụ nào**. Mỗi app nghiệp vụ tự khai báo `ExportDefinition` của mình trong `<app>/exports_definitions.py`, đăng ký qua `AppConfig.ready()`. Chiều phụ thuộc vì thế là nghiệp vụ → engine, được import-linter chặn cứng.

**Tech Stack:** Django 6 + DRF, Celery 5.5 + Redis, openpyxl `write_only`, boto3 → MinIO, React 18 + react-query + shadcn/ui, pytest + vitest.

## Global Constraints

- Python 3.12+, Django 6.0.7, DRF 3.17. **Không thêm dependency mới** — dùng openpyxl 3.1.5 + boto3 1.35.0 đã có trong `backend/requirements.txt`.
- Mọi comment và chuỗi hiển thị cho người dùng: **tiếng Việt**. Comment giải thích **vì sao**, không mô tả lại code.
- Trần dòng đơn hàng **giữ nguyên 50.000** (`EXPORT_MAX_ROWS` trong `apps/orders/repositories.py`) — không nới.
- Hai endpoint HR (`/api/hr/attendance/export`, `/api/hr/payroll/export`) **ngoài phạm vi**, giữ nguyên đồng bộ, không sửa, không xoá test của chúng.
- File xuất lưu MinIO **24h**, **chỉ chính chủ** (`job.user_id == request.user.id`) tải được.
- `CELERY_TASK_TIME_LIMIT` toàn cục là 30s → task export **bắt buộc** khai `soft_time_limit`/`time_limit` riêng.
- Env mới phải cập nhật đồng thời `docker-compose.prod.yml`, `docker-compose.staging.yml`, `docker-compose.local.yml`, `backend/.env.example`.
- Commit message tiếng Anh, ngắn, **không** kèm dòng `Co-Authored-By`.
- Chạy test backend bằng Docker (xem `backend/pytest.ini`, `testpaths = apps`). Lệnh trong plan này giả định chạy từ trong container backend ở thư mục `/app`.

---

## File Structure

**Engine (mới) — `backend/apps/exports/`**

| File | Trách nhiệm |
|---|---|
| `apps.py` | AppConfig |
| `models.py` | `ExportJob`, `ExportJobStatus` |
| `migrations/0001_initial.py` | bảng `export_jobs` |
| `application/registry.py` | `ExportDefinition` ABC, `register_export`, `get_definition`, `list_kinds` |
| `application/writers.py` | `StreamingXlsxWriter`, `CsvWriter`, `ExportCancelled` |
| `application/export_job_service.py` | tạo/dedupe/huỷ/kết thúc job |
| `infrastructure/export_storage.py` | MinIO: `upload_file`, `open_stream`, `delete_object` |
| `infrastructure/tasks.py` | `run_export`, `purge_expired_exports` |
| `infrastructure/event_publisher.py` | phát `export.updated` qua Channels |
| `transport/serializers.py` | `ExportJobSerializer`, `ExportCreateSerializer` |
| `transport/views.py` | 5 endpoint |
| `transport/throttling.py` | `ExportCreateThrottle` |
| `urls.py` | routing |
| `tests/` | test engine |

**Định nghĩa export (mới, nằm trong app nghiệp vụ)**

| File | Luồng |
|---|---|
| `apps/orders/exports_definitions.py` | `orders` |
| `apps/inventory/exports_definitions.py` | `stock`, `dispatch_history`, `return_history` |
| `apps/warranty/exports_definitions.py` | `warranty_claims` |
| `apps/warranty_codes/exports_definitions.py` | `warranty_code_batch` |
| `apps/audit/exports_definitions.py` | `audit_logs` |

**Frontend (mới) — `frontend/src/features/exports/`**

| File | Trách nhiệm |
|---|---|
| `types.ts` | `ExportJob`, `ExportJobStatus` |
| `api.ts` | gọi 5 endpoint |
| `ExportJobsProvider.tsx` | context + polling + WS listener + `startExport` |
| `ExportDock.tsx` | panel dock |
| `ExportDockRow.tsx` | một dòng job |
| `__tests__/` | test |

---

## Sprint 1 — Nền tảng + luồng đơn hàng

### Task 1: App `exports` + model `ExportJob`

**Files:**
- Create: `backend/apps/exports/__init__.py`, `apps.py`, `models.py`, `migrations/__init__.py`
- Modify: `backend/config/settings_parts/base.py` (INSTALLED_APPS), `backend/.importlinter`
- Test: `backend/apps/exports/tests/__init__.py`, `backend/apps/exports/tests/test_models.py`

**Interfaces:**
- Consumes: —
- Produces: `apps.exports.models.ExportJob`, `apps.exports.models.ExportJobStatus`

- [ ] **Step 1: Tạo AppConfig và đăng ký app**

`backend/apps/exports/__init__.py` — file rỗng.

`backend/apps/exports/apps.py`:

```python
from django.apps import AppConfig


class ExportsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.exports"
    verbose_name = "Xuất file nền"
```

Trong `backend/config/settings_parts/base.py`, thêm vào cuối `INSTALLED_APPS` (sau `"apps.warranty_codes",`):

```python
    "apps.exports",
```

- [ ] **Step 2: Viết test cho model (đỏ trước)**

`backend/apps/exports/tests/__init__.py` — file rỗng.

`backend/apps/exports/tests/test_models.py`:

```python
import pytest
from django.utils import timezone

from apps.exports.models import ExportJob, ExportJobStatus
from apps.users.test_helpers import make_user_with_sections

pytestmark = pytest.mark.django_db


def test_job_defaults_to_pending():
    user = make_user_with_sections("exp-1", ["orders"])
    job = ExportJob.objects.create(
        user=user,
        kind="orders",
        params={"section": "orders"},
        params_hash="a" * 64,
        file_name="orders.xlsx",
        expires_at=timezone.now(),
    )
    assert job.status == ExportJobStatus.PENDING
    assert job.progress_rows == 0
    assert job.id is not None


def test_is_downloadable_only_when_success_and_not_expired():
    user = make_user_with_sections("exp-2", ["orders"])
    job = ExportJob.objects.create(
        user=user,
        kind="orders",
        params={},
        params_hash="b" * 64,
        file_name="orders.xlsx",
        expires_at=timezone.now() + timezone.timedelta(hours=1),
        status=ExportJobStatus.SUCCESS,
        file_key="exports/1/x/orders.xlsx",
    )
    assert job.is_downloadable() is True

    job.expires_at = timezone.now() - timezone.timedelta(seconds=1)
    assert job.is_downloadable() is False
```

- [ ] **Step 3: Chạy test, xác nhận đỏ**

Run: `pytest apps/exports/tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.exports.models'`

- [ ] **Step 4: Viết model**

`backend/apps/exports/models.py`:

```python
import uuid

from django.db import models
from django.utils import timezone


class ExportJobStatus(models.TextChoices):
    PENDING = "pending", "Đang chờ"
    RUNNING = "running", "Đang xử lý"
    SUCCESS = "success", "Hoàn tất"
    FAILED = "failed", "Thất bại"
    CANCELLED = "cancelled", "Đã huỷ"
    EXPIRED = "expired", "Hết hạn"


# Trạng thái mà một job vẫn còn "sống" — dùng cho dedupe và cho panel FE (chỉ poll
# khi còn job sống). Gom thành hằng để hai nơi không lệch nhau khi thêm trạng thái.
ACTIVE_STATUSES = (ExportJobStatus.PENDING, ExportJobStatus.RUNNING)


class ExportJob(models.Model):
    """Một lượt xuất file chạy nền.

    Khóa chính là UUID chứ không phải số tuần tự: id này lộ ra URL cho FE, và số
    tuần tự cho phép đoán/đếm số lượt xuất của cả công ty.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="export_jobs")
    kind = models.CharField(max_length=50)
    params = models.JSONField(default=dict)
    # sha256 của (kind + params đã chuẩn hoá) — nền tảng của dedupe chống double-click.
    params_hash = models.CharField(max_length=64)
    status = models.CharField(
        max_length=16, choices=ExportJobStatus.choices, default=ExportJobStatus.PENDING
    )
    total_rows = models.IntegerField(null=True, blank=True)
    progress_rows = models.IntegerField(default=0)
    file_key = models.CharField(max_length=255, blank=True, default="")
    file_name = models.CharField(max_length=255)
    file_size = models.BigIntegerField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")
    celery_task_id = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "export_jobs"
        indexes = [
            models.Index(fields=["user", "-created_at"], name="export_user_created_idx"),
            models.Index(fields=["status", "expires_at"], name="export_status_expires_idx"),
            models.Index(
                fields=["user", "kind", "params_hash", "status"], name="export_dedupe_idx"
            ),
        ]

    def is_downloadable(self) -> bool:
        return (
            self.status == ExportJobStatus.SUCCESS
            and bool(self.file_key)
            and self.expires_at > timezone.now()
        )
```

- [ ] **Step 5: Sinh migration**

Run: `python manage.py makemigrations exports`
Expected: tạo `apps/exports/migrations/0001_initial.py`

- [ ] **Step 6: Chạy test, xác nhận xanh**

Run: `pytest apps/exports/tests/test_models.py -v`
Expected: PASS (2 passed)

- [ ] **Step 7: Thêm tầng cho import-linter**

Trong `backend/.importlinter`, mục `[importlinter:contract:layers]`, thêm `apps.exports` thành một tầng riêng **giữa** tầng nghiệp vụ và `apps.users`:

```
layers =
    apps.kiotviet : apps.facebook_ads : apps.google_ads : apps.best_express : apps.cnv_loyalty
    apps.customers : apps.orders : apps.inventory : apps.hr
    apps.exports
    apps.users : apps.channels_core
    apps.common
```

Vị trí này là chủ đích: `apps.exports` được phép import `apps.users` (FK `User`) nhưng **bị chặn cứng** khi import `apps.orders`/`apps.inventory`. Chiều đúng là nghiệp vụ → engine.

- [ ] **Step 8: Chạy import-linter, xác nhận không vi phạm**

Run: `lint-imports --config .importlinter`
Expected: `Contracts: 1 kept, 0 broken` (hoặc số contract hiện có, 0 broken)

- [ ] **Step 9: Commit**

```bash
git add backend/apps/exports backend/config/settings_parts/base.py backend/.importlinter
git commit -m "feat: add exports app with ExportJob model"
```

---

### Task 2: Registry `ExportDefinition`

**Files:**
- Create: `backend/apps/exports/application/__init__.py`, `backend/apps/exports/application/registry.py`
- Test: `backend/apps/exports/tests/test_registry.py`

**Interfaces:**
- Consumes: —
- Produces:
  - `ExportDefinition` ABC với thuộc tính `kind: str`, `file_ext: str`, và các method `validate(user, raw_params) -> dict`, `check_permission(user, params) -> None`, `estimate(params) -> int | None`, `file_name(params) -> str`, `write(params, writer, progress) -> None`
  - `register_export(definition: ExportDefinition) -> None`
  - `get_definition(kind: str) -> ExportDefinition` (raise `UnknownExportKind`)
  - `list_kinds() -> list[str]`
  - `UnknownExportKind(Exception)`

- [ ] **Step 1: Viết test (đỏ trước)**

`backend/apps/exports/tests/test_registry.py`:

```python
import pytest

from apps.exports.application.registry import (
    ExportDefinition,
    UnknownExportKind,
    get_definition,
    list_kinds,
    register_export,
)


class _Dummy(ExportDefinition):
    kind = "dummy_test_kind"

    def validate(self, user, raw_params):
        return {"ok": True}

    def check_permission(self, user, params):
        return None

    def estimate(self, params):
        return 1

    def file_name(self, params):
        return "dummy.xlsx"

    def write(self, params, writer, progress):
        writer.write_header(["A"])
        writer.write_row(["x"])


def test_register_and_get():
    register_export(_Dummy())
    assert get_definition("dummy_test_kind").kind == "dummy_test_kind"
    assert "dummy_test_kind" in list_kinds()


def test_get_unknown_kind_raises():
    with pytest.raises(UnknownExportKind):
        get_definition("khong-ton-tai")


def test_register_duplicate_kind_raises():
    register_export(_Dummy())
    with pytest.raises(ValueError):
        register_export(_Dummy())
```

- [ ] **Step 2: Chạy test, xác nhận đỏ**

Run: `pytest apps/exports/tests/test_registry.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Viết registry**

`backend/apps/exports/application/__init__.py` — file rỗng.

`backend/apps/exports/application/registry.py`:

```python
"""Sổ đăng ký các loại export.

Engine không biết gì về nghiệp vụ: mỗi app tự khai một ``ExportDefinition`` trong
``<app>/exports_definitions.py`` và gọi ``register_export`` từ ``AppConfig.ready()``.
Nhờ vậy chiều phụ thuộc luôn là nghiệp vụ → engine, đúng contract của import-linter.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class UnknownExportKind(Exception):
    """Không có định nghĩa nào cho `kind` được yêu cầu."""


class ExportDefinition(ABC):
    """Hợp đồng của một loại export.

    Ba method đầu chạy ĐỒNG BỘ trong request (phải rẻ, dưới ~200ms) để lỗi quyền /
    tham số / vượt trần trả về ngay như đường đồng bộ cũ. Chỉ ``write`` chạy ở worker.
    """

    kind: str = ""
    file_ext: str = "xlsx"

    @abstractmethod
    def validate(self, user, raw_params: dict) -> dict:
        """Chuẩn hoá tham số client gửi lên thành `params` sẽ ghi vào job.

        Phải raise ``rest_framework.exceptions.ValidationError`` khi tham số sai.
        Kết quả phải JSON-serializable và ỔN ĐỊNH (cùng đầu vào → cùng đầu ra) vì
        nó là nguyên liệu của params_hash dùng để dedupe.
        """

    @abstractmethod
    def check_permission(self, user, params: dict) -> None:
        """Raise ``rest_framework.exceptions.PermissionDenied`` nếu không đủ quyền."""

    @abstractmethod
    def estimate(self, params: dict) -> int | None:
        """Số dòng dự kiến, hoặc None nếu không đếm được rẻ.

        Raise ``ValidationError`` khi vượt trần của luồng — đây là chỗ chặn sớm,
        trước khi tốn một slot worker.
        """

    @abstractmethod
    def file_name(self, params: dict) -> str:
        """Tên file người dùng nhìn thấy, đã kèm phần mở rộng."""

    @abstractmethod
    def write(self, params: dict, writer, progress) -> None:
        """Ghi toàn bộ nội dung vào `writer`. CHẠY Ở WORKER.

        Gọi ``progress(n)`` sau mỗi lô dòng; hàm đó vừa cập nhật tiến độ vừa là
        điểm kiểm tra huỷ (nó raise ``ExportCancelled`` khi job bị huỷ).
        """


_REGISTRY: dict[str, ExportDefinition] = {}


def register_export(definition: ExportDefinition) -> None:
    kind = definition.kind
    if not kind:
        raise ValueError("ExportDefinition phải khai `kind`.")
    existing = _REGISTRY.get(kind)
    # Cho phép đăng ký lại ĐÚNG cùng một class: AppConfig.ready() có thể chạy hai lần
    # trong test runner. Nhưng hai class KHÁC nhau cùng `kind` là lỗi lập trình thật.
    if existing is not None and type(existing) is not type(definition):
        raise ValueError(f"Trùng kind export: {kind}")
    if existing is not None:
        raise ValueError(f"Trùng kind export: {kind}")
    _REGISTRY[kind] = definition


def get_definition(kind: str) -> ExportDefinition:
    try:
        return _REGISTRY[kind]
    except KeyError as exc:
        raise UnknownExportKind(kind) from exc


def list_kinds() -> list[str]:
    return sorted(_REGISTRY)
```

- [ ] **Step 4: Chạy test, xác nhận xanh**

Run: `pytest apps/exports/tests/test_registry.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/apps/exports/application backend/apps/exports/tests/test_registry.py
git commit -m "feat: add export definition registry"
```

---

### Task 3: Writer streaming + helper chống formula injection

**Files:**
- Create: `backend/apps/exports/application/writers.py`
- Modify: `backend/apps/common/excel_utils.py` (thêm `write_only_cells`)
- Test: `backend/apps/exports/tests/test_writers.py`

**Interfaces:**
- Consumes: `apps.common.excel_utils.clean_excel_cell`
- Produces:
  - `StreamingXlsxWriter(path: str, sheet_title: str)` với `write_header(list)`, `write_row(list)`, `close()`
  - `CsvWriter(path: str)` với cùng ba method
  - `ExportCancelled(Exception)`
  - `apps.common.excel_utils.write_only_cells(ws, values) -> list`

- [ ] **Step 1: Viết test (đỏ trước)**

`backend/apps/exports/tests/test_writers.py`:

```python
import csv
import io

import openpyxl

from apps.exports.application.writers import CsvWriter, StreamingXlsxWriter


def test_xlsx_writer_writes_header_and_rows(tmp_path):
    path = str(tmp_path / "out.xlsx")
    writer = StreamingXlsxWriter(path, sheet_title="Đơn hàng")
    writer.write_header(["Tên", "SL"])
    writer.write_row(["Anh A", 3])
    writer.write_row(["Chị B", 5])
    writer.close()

    wb = openpyxl.load_workbook(path)
    ws = wb.active
    assert ws.title == "Đơn hàng"
    assert list(ws.iter_rows(values_only=True)) == [("Tên", "SL"), ("Anh A", 3), ("Chị B", 5)]


def test_xlsx_writer_neutralises_formula(tmp_path):
    path = str(tmp_path / "inj.xlsx")
    writer = StreamingXlsxWriter(path, sheet_title="S")
    writer.write_header(["A"])
    writer.write_row(['=HYPERLINK("http://evil","x")'])
    writer.close()

    wb = openpyxl.load_workbook(path)
    cell = wb.active["A2"]
    assert cell.data_type == "s"
    assert cell.value == '=HYPERLINK("http://evil","x")'


def test_xlsx_writer_strips_illegal_control_chars(tmp_path):
    path = str(tmp_path / "ctrl.xlsx")
    writer = StreamingXlsxWriter(path, sheet_title="S")
    writer.write_header(["A"])
    writer.write_row(["ma\x1dvach"])
    writer.close()

    wb = openpyxl.load_workbook(path)
    assert wb.active["A2"].value == "mavach"


def test_csv_writer_roundtrip(tmp_path):
    path = str(tmp_path / "out.csv")
    writer = CsvWriter(path)
    writer.write_header(["id", "tên"])
    writer.write_row([1, "Anh A"])
    writer.close()

    with open(path, encoding="utf-8-sig", newline="") as fh:
        assert list(csv.reader(fh)) == [["id", "tên"], ["1", "Anh A"]]
```

- [ ] **Step 2: Chạy test, xác nhận đỏ**

Run: `pytest apps/exports/tests/test_writers.py -v`
Expected: FAIL — `ModuleNotFoundError: apps.exports.application.writers`

- [ ] **Step 3: Thêm helper vào `excel_utils`**

Thêm vào cuối `backend/apps/common/excel_utils.py`:

```python
def write_only_cells(ws, values):
    """Dựng danh sách cell đã làm sạch cho sheet ``write_only``.

    Sheet write_only KHÔNG giữ cell sau khi ``ws.append()`` nên không thể mượn
    ``append_clean_row`` (hàm đó đọc lại ``ws[ws.max_row]`` sau khi append). Phải
    dựng ``WriteOnlyCell`` với data_type đã sửa TRƯỚC.

    KHÔNG được tái dùng một instance cell cho nhiều dòng: writer của openpyxl mutate
    cell theo vị trí lúc serialize, share instance sẽ làm giá trị lệch sang dòng kế.
    """
    from openpyxl.cell import WriteOnlyCell

    cells = []
    for value in values:
        cell = WriteOnlyCell(ws, value=clean_excel_cell(value))
        if cell.data_type == "f":
            cell.data_type = "s"
            cell.quotePrefix = True
        cells.append(cell)
    return cells
```

- [ ] **Step 4: Viết writers**

`backend/apps/exports/application/writers.py`:

```python
"""Writer ghi thẳng ra file trên đĩa — RAM phẳng theo số dòng.

Đây là điểm khác cốt lõi so với đường export đồng bộ cũ: cũ dựng cả workbook trong
``io.BytesIO`` nên 50.000 đơn × ~40 cột ≈ 2 triệu cell object nằm trong RAM của
``desi-web`` (mem_limit 1200m). Ở đây openpyxl ``write_only`` xả từng dòng xuống
file tạm, còn ``desi-web`` thì không dựng file nữa.
"""

from __future__ import annotations

import csv

import openpyxl

from apps.common.excel_utils import clean_excel_cell, write_only_cells


class ExportCancelled(Exception):
    """Người dùng đã huỷ job — worker dừng sớm, không phải lỗi."""


class StreamingXlsxWriter:
    def __init__(self, path: str, sheet_title: str = "Sheet1"):
        self._path = path
        self._wb = openpyxl.Workbook(write_only=True)
        # openpyxl chặn tên sheet >31 ký tự và một số ký tự đặc biệt; cắt cho an toàn
        # vì title có thể lấy từ dữ liệu (mã lô, tên kho).
        self._ws = self._wb.create_sheet(title=(sheet_title or "Sheet1")[:31])

    def write_header(self, headers) -> None:
        self._ws.append(write_only_cells(self._ws, headers))

    def write_row(self, values) -> None:
        self._ws.append(write_only_cells(self._ws, values))

    def close(self) -> None:
        self._wb.save(self._path)


class CsvWriter:
    def __init__(self, path: str):
        # utf-8-sig: Excel trên Windows đọc CSV utf-8 KHÔNG BOM ra tiếng Việt vỡ dấu.
        self._fh = open(path, "w", encoding="utf-8-sig", newline="")
        self._writer = csv.writer(self._fh)

    def write_header(self, headers) -> None:
        self._writer.writerow([clean_excel_cell(v) for v in headers])

    def write_row(self, values) -> None:
        self._writer.writerow([clean_excel_cell(v) for v in values])

    def close(self) -> None:
        self._fh.close()
```

- [ ] **Step 5: Chạy test, xác nhận xanh**

Run: `pytest apps/exports/tests/test_writers.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add backend/apps/exports/application/writers.py backend/apps/common/excel_utils.py backend/apps/exports/tests/test_writers.py
git commit -m "feat: add streaming xlsx and csv writers"
```

---

### Task 4: Storage MinIO cho file export

**Files:**
- Create: `backend/apps/exports/infrastructure/__init__.py`, `backend/apps/exports/infrastructure/export_storage.py`
- Modify: `backend/config/settings_parts/base.py` (thêm `MINIO_BUCKET_EXPORTS`, `EXPORT_*`)
- Test: `backend/apps/exports/tests/test_storage.py`

**Interfaces:**
- Consumes: `django.conf.settings.MINIO_*`
- Produces:
  - `upload_file(key: str, path: str, content_type: str) -> None`
  - `open_stream(key: str)` → object có `.read(size)` và `.close()`
  - `delete_object(key: str) -> None`
  - `settings.MINIO_BUCKET_EXPORTS`, `settings.EXPORT_FILE_TTL_HOURS`, `settings.EXPORT_SOFT_TIME_LIMIT`, `settings.EXPORT_MAX_ACTIVE_JOBS_PER_USER`

- [ ] **Step 1: Viết test (đỏ trước)**

`backend/apps/exports/tests/test_storage.py`:

```python
from unittest.mock import MagicMock, patch

from apps.exports.infrastructure import export_storage


def test_upload_file_creates_bucket_when_missing(tmp_path):
    path = tmp_path / "a.xlsx"
    path.write_bytes(b"xx")
    client = MagicMock()
    client.head_bucket.side_effect = Exception("missing")

    with patch.object(export_storage, "_client", return_value=client):
        with patch.object(export_storage, "_ClientError", Exception):
            export_storage.upload_file("exports/1/a.xlsx", str(path), "application/xlsx")

    client.create_bucket.assert_called_once()
    client.upload_file.assert_called_once()


def test_delete_object_calls_client():
    client = MagicMock()
    with patch.object(export_storage, "_client", return_value=client):
        export_storage.delete_object("exports/1/a.xlsx")
    client.delete_object.assert_called_once()
```

- [ ] **Step 2: Chạy test, xác nhận đỏ**

Run: `pytest apps/exports/tests/test_storage.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Thêm settings**

Trong `backend/config/settings_parts/base.py`, ngay sau dòng `MINIO_BUCKET_DOCUMENTS = ...`:

```python
MINIO_BUCKET_EXPORTS = os.getenv("MINIO_BUCKET_EXPORTS", "desi-exports").strip()
```

Và thêm một khối mới ở cuối file:

```python
# ── Xuất file chạy nền (apps.exports) ──
# TTL 24h: job export chạy nền nên xuất lại rất rẻ, không có lý do giữ file chứa
# thông tin cá nhân khách hàng (họ tên/điện thoại/địa chỉ) trên đĩa lâu hơn một ngày.
EXPORT_FILE_TTL_HOURS = int(os.getenv("EXPORT_FILE_TTL_HOURS", "24"))
# Phải override CELERY_TASK_SOFT_TIME_LIMIT toàn cục (30s) — xem apps/exports/tasks.
EXPORT_SOFT_TIME_LIMIT = int(os.getenv("EXPORT_SOFT_TIME_LIMIT", "600"))
# Trần số job đang chạy của MỘT người: chặn kiểu lạm dụng mà dedupe không bắt được
# (đổi một tham số nhỏ mỗi lần bấm thì params_hash khác nhau nên không dedupe).
EXPORT_MAX_ACTIVE_JOBS_PER_USER = int(os.getenv("EXPORT_MAX_ACTIVE_JOBS_PER_USER", "5"))
```

- [ ] **Step 4: Viết storage**

`backend/apps/exports/infrastructure/__init__.py` — file rỗng.

`backend/apps/exports/infrastructure/export_storage.py`:

```python
"""MinIO cho file export — cùng khuôn ``apps.inventory.infrastructure.document_storage``.

Khác một điểm quan trọng: KHÔNG có hàm đọc-hết-vào-RAM kiểu ``get_object_bytes``.
File export tính bằng chục MB, nạp trọn vào RAM của ``desi-web`` lúc tải là tái lập
đúng vấn đề mà cả thiết kế này sinh ra để dẹp. Chỉ có ``open_stream``.
"""

from __future__ import annotations

from django.conf import settings

try:  # botocore vắng mặt trong image unit-test; client luôn được mock ở đó
    from botocore.exceptions import ClientError as _ClientError
except ImportError:  # pragma: no cover
    _ClientError = Exception


def _bucket() -> str:
    return settings.MINIO_BUCKET_EXPORTS


def _client():
    import boto3  # lazy: test mock _client → không cần boto3 lúc import

    scheme = "https" if settings.MINIO_USE_SSL else "http"
    return boto3.client(
        "s3",
        endpoint_url=f"{scheme}://{settings.MINIO_ENDPOINT}",
        aws_access_key_id=settings.MINIO_ACCESS_KEY,
        aws_secret_access_key=settings.MINIO_SECRET_KEY,
        region_name="us-east-1",
    )


def upload_file(key: str, path: str, content_type: str) -> None:
    client = _client()
    try:
        client.head_bucket(Bucket=_bucket())
    except _ClientError:
        client.create_bucket(Bucket=_bucket())
    client.upload_file(
        Filename=path,
        Bucket=_bucket(),
        Key=key,
        ExtraArgs={"ContentType": content_type},
    )


def open_stream(key: str):
    """Trả về body dạng stream (có .read(size)) để FileResponse đọc theo chunk."""
    return _client().get_object(Bucket=_bucket(), Key=key)["Body"]


def delete_object(key: str) -> None:
    _client().delete_object(Bucket=_bucket(), Key=key)
```

- [ ] **Step 5: Chạy test, xác nhận xanh**

Run: `pytest apps/exports/tests/test_storage.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add backend/apps/exports/infrastructure backend/config/settings_parts/base.py backend/apps/exports/tests/test_storage.py
git commit -m "feat: add minio storage for export files"
```

---

### Task 5: `ExportJobService` — tạo, dedupe, huỷ

**Files:**
- Create: `backend/apps/exports/application/export_job_service.py`
- Test: `backend/apps/exports/tests/conftest.py`, `backend/apps/exports/tests/test_job_service.py`

**Interfaces:**
- Consumes: `ExportJob`, `ExportJobStatus`, `ACTIVE_STATUSES`, `get_definition`
- Produces:
  - `ExportJobService.create(user, kind, raw_params) -> tuple[ExportJob, bool]` (bool = `created`)
  - `ExportJobService.cancel(job) -> None`
  - `ExportJobService.mark_running(job_id) -> None`
  - `ExportJobService.mark_success(job_id, file_key, file_size, rows) -> None`
  - `ExportJobService.mark_failed(job_id, message) -> None`
  - `ExportJobService.params_hash(kind, params) -> str`
  - `TooManyActiveJobs(Exception)`

- [ ] **Step 1: Viết conftest cho app test**

`backend/apps/exports/tests/conftest.py`:

```python
import pytest
from rest_framework.test import APIClient

from apps.exports.application.registry import ExportDefinition, _REGISTRY, register_export
from apps.users.test_helpers import make_user_with_sections


class FakeExport(ExportDefinition):
    """Định nghĩa giả để test engine mà không phụ thuộc app nghiệp vụ nào."""

    kind = "fake"

    def validate(self, user, raw_params):
        return {"n": int(raw_params.get("n", 2))}

    def check_permission(self, user, params):
        return None

    def estimate(self, params):
        return params["n"]

    def file_name(self, params):
        return "fake.xlsx"

    def write(self, params, writer, progress):
        writer.write_header(["i"])
        for i in range(params["n"]):
            writer.write_row([i])
            progress(i + 1)


@pytest.fixture
def fake_export():
    """Đăng ký `fake` và gỡ sau test — registry là state toàn cục dùng chung."""
    if "fake" not in _REGISTRY:
        register_export(FakeExport())
    yield _REGISTRY["fake"]
    _REGISTRY.pop("fake", None)


@pytest.fixture
def export_user(db):
    return make_user_with_sections("export-user", ["orders"])


@pytest.fixture
def export_client(export_user):
    client = APIClient()
    client.force_authenticate(export_user)
    return client
```

- [ ] **Step 2: Viết test service (đỏ trước)**

`backend/apps/exports/tests/test_job_service.py`:

```python
import pytest

from apps.exports.application.export_job_service import ExportJobService, TooManyActiveJobs
from apps.exports.models import ExportJob, ExportJobStatus

pytestmark = pytest.mark.django_db


def test_create_returns_new_job(fake_export, export_user):
    job, created = ExportJobService.create(export_user, "fake", {"n": 3})
    assert created is True
    assert job.status == ExportJobStatus.PENDING
    assert job.total_rows == 3
    assert job.file_name == "fake.xlsx"
    assert job.params == {"n": 3}


def test_create_dedupes_identical_active_job(fake_export, export_user):
    first, _ = ExportJobService.create(export_user, "fake", {"n": 3})
    second, created = ExportJobService.create(export_user, "fake", {"n": 3})
    assert created is False
    assert second.id == first.id
    assert ExportJob.objects.count() == 1


def test_create_does_not_dedupe_across_users(fake_export, export_user):
    from apps.users.test_helpers import make_user_with_sections

    other = make_user_with_sections("export-other", ["orders"])
    ExportJobService.create(export_user, "fake", {"n": 3})
    _, created = ExportJobService.create(other, "fake", {"n": 3})
    assert created is True
    assert ExportJob.objects.count() == 2


def test_create_rejects_when_too_many_active(fake_export, export_user, settings):
    settings.EXPORT_MAX_ACTIVE_JOBS_PER_USER = 2
    ExportJobService.create(export_user, "fake", {"n": 1})
    ExportJobService.create(export_user, "fake", {"n": 2})
    with pytest.raises(TooManyActiveJobs):
        ExportJobService.create(export_user, "fake", {"n": 3})


def test_cancel_sets_status(fake_export, export_user):
    job, _ = ExportJobService.create(export_user, "fake", {"n": 3})
    ExportJobService.cancel(job)
    job.refresh_from_db()
    assert job.status == ExportJobStatus.CANCELLED
    assert job.finished_at is not None


def test_mark_success_records_file(fake_export, export_user):
    job, _ = ExportJobService.create(export_user, "fake", {"n": 3})
    ExportJobService.mark_success(job.id, "exports/1/f.xlsx", 120, 3)
    job.refresh_from_db()
    assert job.status == ExportJobStatus.SUCCESS
    assert job.file_key == "exports/1/f.xlsx"
    assert job.file_size == 120
    assert job.progress_rows == 3
```

- [ ] **Step 3: Chạy test, xác nhận đỏ**

Run: `pytest apps/exports/tests/test_job_service.py -v`
Expected: FAIL — `ModuleNotFoundError: apps.exports.application.export_job_service`

- [ ] **Step 4: Viết service**

`backend/apps/exports/application/export_job_service.py`:

```python
"""Vòng đời của một ExportJob. Không biết Celery — task gọi vào đây, không ngược lại."""

from __future__ import annotations

import hashlib
import json

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.exports.application.registry import get_definition
from apps.exports.models import ACTIVE_STATUSES, ExportJob, ExportJobStatus


class TooManyActiveJobs(Exception):
    """Người dùng đã có quá nhiều job đang chạy."""


class ExportJobService:
    @staticmethod
    def params_hash(kind: str, params: dict) -> str:
        # sort_keys: hai dict cùng nội dung khác thứ tự key phải ra CÙNG hash, nếu
        # không thì dedupe vô dụng ngay khi FE đổi thứ tự field.
        raw = json.dumps(
            {"kind": kind, "params": params}, sort_keys=True, ensure_ascii=False, default=str
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def create(user, kind: str, raw_params: dict) -> tuple[ExportJob, bool]:
        """Validate + dedupe + tạo job. Trả (job, created).

        Toàn bộ phần đắt (đếm dòng) đã nằm ở `estimate` của definition và chạy TRƯỚC
        khi ghi bản ghi — vượt trần thì không có job nào được tạo.
        """
        definition = get_definition(kind)
        params = definition.validate(user, raw_params)
        definition.check_permission(user, params)
        total_rows = definition.estimate(params)

        digest = ExportJobService.params_hash(kind, params)
        existing = (
            ExportJob.objects.filter(
                user=user, kind=kind, params_hash=digest, status__in=ACTIVE_STATUSES
            )
            .order_by("-created_at")
            .first()
        )
        if existing is not None:
            return existing, False

        active_count = ExportJob.objects.filter(user=user, status__in=ACTIVE_STATUSES).count()
        if active_count >= settings.EXPORT_MAX_ACTIVE_JOBS_PER_USER:
            raise TooManyActiveJobs()

        job = ExportJob.objects.create(
            user=user,
            kind=kind,
            params=params,
            params_hash=digest,
            total_rows=total_rows,
            file_name=definition.file_name(params),
            expires_at=timezone.now()
            + timezone.timedelta(hours=settings.EXPORT_FILE_TTL_HOURS),
        )
        return job, True

    @staticmethod
    def enqueue(job: ExportJob) -> None:
        """Đẩy job xuống Celery SAU khi transaction commit.

        ``on_commit`` là bắt buộc: worker có thể nhận task trước khi transaction của
        request commit xong và sẽ không tìm thấy bản ghi job.
        """
        from apps.exports.infrastructure.tasks import run_export

        def _send():
            result = run_export.delay(str(job.id))
            ExportJob.objects.filter(id=job.id).update(celery_task_id=result.id)

        transaction.on_commit(_send)

    @staticmethod
    def cancel(job: ExportJob) -> None:
        """Đánh dấu huỷ. Job RUNNING dừng ở mốc `progress` kế tiếp (xem writers)."""
        from celery import current_app

        if job.celery_task_id:
            current_app.control.revoke(job.celery_task_id)
        ExportJob.objects.filter(id=job.id, status__in=ACTIVE_STATUSES).update(
            status=ExportJobStatus.CANCELLED, finished_at=timezone.now()
        )

    @staticmethod
    def mark_running(job_id) -> None:
        ExportJob.objects.filter(id=job_id).update(
            status=ExportJobStatus.RUNNING, started_at=timezone.now()
        )

    @staticmethod
    def mark_success(job_id, file_key: str, file_size: int, rows: int) -> None:
        ExportJob.objects.filter(id=job_id).update(
            status=ExportJobStatus.SUCCESS,
            file_key=file_key,
            file_size=file_size,
            progress_rows=rows,
            finished_at=timezone.now(),
        )

    @staticmethod
    def mark_failed(job_id, message: str) -> None:
        ExportJob.objects.filter(id=job_id).update(
            status=ExportJobStatus.FAILED,
            error_message=message[:2000],
            finished_at=timezone.now(),
        )
```

- [ ] **Step 5: Chạy test, xác nhận xanh**

Run: `pytest apps/exports/tests/test_job_service.py -v`
Expected: PASS (6 passed)

- [ ] **Step 6: Commit**

```bash
git add backend/apps/exports/application/export_job_service.py backend/apps/exports/tests/
git commit -m "feat: add export job service with dedupe"
```

---

### Task 6: Task Celery `run_export` + `purge_expired_exports`

**Files:**
- Create: `backend/apps/exports/infrastructure/tasks.py`, `backend/apps/exports/infrastructure/event_publisher.py`
- Modify: `backend/config/settings_parts/celery.py` (queue + route + beat)
- Test: `backend/apps/exports/tests/test_tasks.py`

**Interfaces:**
- Consumes: `ExportJobService`, `get_definition`, `StreamingXlsxWriter`, `CsvWriter`, `ExportCancelled`, `export_storage`
- Produces:
  - `run_export(job_id: str) -> None` (Celery task, queue `exports`)
  - `purge_expired_exports() -> None` (Celery task)
  - `publish_export_event(job) -> None`
  - `settings.CELERY_EXPORTS_QUEUE`

- [ ] **Step 1: Viết test (đỏ trước)**

`backend/apps/exports/tests/test_tasks.py`:

```python
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.exports.application.export_job_service import ExportJobService
from apps.exports.infrastructure.tasks import purge_expired_exports, run_export
from apps.exports.models import ExportJob, ExportJobStatus

pytestmark = pytest.mark.django_db


def test_run_export_writes_file_and_marks_success(fake_export, export_user):
    job, _ = ExportJobService.create(export_user, "fake", {"n": 5})
    with patch("apps.exports.infrastructure.tasks.export_storage") as storage:
        run_export(str(job.id))
    job.refresh_from_db()
    assert job.status == ExportJobStatus.SUCCESS
    assert job.progress_rows == 5
    assert job.file_key.startswith(f"exports/{export_user.id}/{job.id}/")
    storage.upload_file.assert_called_once()


def test_run_export_marks_failed_on_error(fake_export, export_user):
    job, _ = ExportJobService.create(export_user, "fake", {"n": 5})
    with patch("apps.exports.infrastructure.tasks.export_storage") as storage:
        storage.upload_file.side_effect = RuntimeError("minio down")
        run_export(str(job.id))
    job.refresh_from_db()
    assert job.status == ExportJobStatus.FAILED
    assert "Không lưu được file" in job.error_message


def test_run_export_stops_when_cancelled(fake_export, export_user):
    job, _ = ExportJobService.create(export_user, "fake", {"n": 5000})
    ExportJob.objects.filter(id=job.id).update(status=ExportJobStatus.CANCELLED)
    with patch("apps.exports.infrastructure.tasks.export_storage") as storage:
        run_export(str(job.id))
    job.refresh_from_db()
    assert job.status == ExportJobStatus.CANCELLED
    storage.upload_file.assert_not_called()


def test_purge_deletes_expired_files(fake_export, export_user):
    job, _ = ExportJobService.create(export_user, "fake", {"n": 1})
    ExportJob.objects.filter(id=job.id).update(
        status=ExportJobStatus.SUCCESS,
        file_key="exports/x/y.xlsx",
        expires_at=timezone.now() - timezone.timedelta(hours=1),
    )
    with patch("apps.exports.infrastructure.tasks.export_storage") as storage:
        purge_expired_exports()
    job.refresh_from_db()
    assert job.status == ExportJobStatus.EXPIRED
    storage.delete_object.assert_called_once_with("exports/x/y.xlsx")


def test_purge_fails_stuck_running_jobs(fake_export, export_user):
    job, _ = ExportJobService.create(export_user, "fake", {"n": 1})
    ExportJob.objects.filter(id=job.id).update(
        status=ExportJobStatus.RUNNING,
        started_at=timezone.now() - timezone.timedelta(hours=3),
    )
    with patch("apps.exports.infrastructure.tasks.export_storage"):
        purge_expired_exports()
    job.refresh_from_db()
    assert job.status == ExportJobStatus.FAILED
```

- [ ] **Step 2: Chạy test, xác nhận đỏ**

Run: `pytest apps/exports/tests/test_tasks.py -v`
Expected: FAIL — `ModuleNotFoundError: apps.exports.infrastructure.tasks`

- [ ] **Step 3: Viết event publisher**

`backend/apps/exports/infrastructure/event_publisher.py`:

```python
"""Phát trạng thái job tới đúng MỘT người — chủ của job.

Dùng group riêng theo user (``exports_user_<id>``) chứ không phải group chung
``crm_updates``: payload mang tên file và số dòng của một người, không có lý do
fan-out tới mọi tab đang mở của cả công ty.
"""

from __future__ import annotations

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


def export_group(user_id: int) -> str:
    return f"exports_user_{user_id}"


def publish_export_event(job) -> None:
    payload = {
        "type": "export.updated",
        "id": str(job.id),
        "kind": job.kind,
        "status": job.status,
        "progress_rows": job.progress_rows,
        "total_rows": job.total_rows,
        "file_name": job.file_name,
        "error_message": job.error_message,
    }
    try:
        layer = get_channel_layer()
        if layer is None:
            return
        async_to_sync(layer.group_send)(
            export_group(job.user_id), {"type": "export_event", "payload": payload}
        )
    except Exception:
        # Best-effort: mất event realtime thì FE vẫn có polling 3s làm lưới an toàn,
        # KHÔNG được để lỗi phát event làm hỏng job đã chạy xong.
        logger.exception("Không phát được export event job_id=%s", job.id)
```

- [ ] **Step 4: Viết tasks**

`backend/apps/exports/infrastructure/tasks.py`:

```python
from __future__ import annotations

import logging
import os
import tempfile

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.utils import timezone

from apps.exports.application.export_job_service import ExportJobService
from apps.exports.application.registry import UnknownExportKind, get_definition
from apps.exports.application.writers import CsvWriter, ExportCancelled, StreamingXlsxWriter
from apps.exports.infrastructure import export_storage
from apps.exports.infrastructure.event_publisher import publish_export_event
from apps.exports.models import ACTIVE_STATUSES, ExportJob, ExportJobStatus

logger = logging.getLogger(__name__)

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_CONTENT_TYPES = {"xlsx": _XLSX, "csv": "text/csv; charset=utf-8"}

# Cập nhật tiến độ mỗi N dòng: mỗi lần là 1 UPDATE + 1 SELECT kiểm tra huỷ, đặt quá
# dày sẽ biến job 50k dòng thành 50k round-trip DB.
_PROGRESS_EVERY = 1000


def _make_writer(job: ExportJob, path: str):
    if job.file_name.endswith(".csv"):
        return CsvWriter(path)
    return StreamingXlsxWriter(path, sheet_title=job.kind[:31])


@shared_task(
    bind=True,
    # BẮT BUỘC override: CELERY_TASK_SOFT_TIME_LIMIT toàn cục là 30s, không đủ cho
    # một file 50.000 dòng.
    soft_time_limit=settings.EXPORT_SOFT_TIME_LIMIT,
    time_limit=settings.EXPORT_SOFT_TIME_LIMIT + 60,
    # KHÔNG retry tự động: một job export chạy lại nguyên vẹn là nhân đôi tải mà
    # người dùng không biết. Panel có nút "Thử lại" để họ tự quyết.
    max_retries=0,
)
def run_export(self, job_id: str) -> None:
    job = ExportJob.objects.filter(id=job_id).first()
    if job is None:
        logger.warning("run_export: không thấy job %s", job_id)
        return
    if job.status not in ACTIVE_STATUSES:
        # Đã bị huỷ trước khi worker nhận task.
        return

    ExportJobService.mark_running(job.id)
    job.refresh_from_db()
    publish_export_event(job)

    written = 0

    def progress(rows_done: int) -> None:
        nonlocal written
        written = rows_done
        if rows_done % _PROGRESS_EVERY:
            return
        ExportJob.objects.filter(id=job.id).update(progress_rows=rows_done)
        current = ExportJob.objects.filter(id=job.id).values_list("status", flat=True).first()
        if current == ExportJobStatus.CANCELLED:
            raise ExportCancelled()
        job.progress_rows = rows_done
        publish_export_event(job)

    suffix = os.path.splitext(job.file_name)[1] or ".xlsx"
    tmp_path = ""
    try:
        definition = get_definition(job.kind)
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name
        writer = _make_writer(job, tmp_path)
        try:
            definition.write(job.params, writer, progress)
        finally:
            writer.close()

        key = f"exports/{job.user_id}/{job.id}/{job.file_name}"
        content_type = _CONTENT_TYPES.get(suffix.lstrip("."), "application/octet-stream")
        try:
            export_storage.upload_file(key, tmp_path, content_type)
        except Exception as exc:
            raise _StorageFailed() from exc

        ExportJobService.mark_success(job.id, key, os.path.getsize(tmp_path), written)
    except ExportCancelled:
        logger.info("Job export %s bị huỷ giữa chừng", job.id)
    except _StorageFailed:
        logger.exception("Không upload được file export %s", job.id)
        ExportJobService.mark_failed(job.id, "Không lưu được file lên kho lưu trữ.")
    except SoftTimeLimitExceeded:
        logger.warning("Job export %s vượt thời gian cho phép", job.id)
        ExportJobService.mark_failed(
            job.id, "Quá thời gian cho phép. Vui lòng thu hẹp bộ lọc rồi xuất lại."
        )
    except UnknownExportKind:
        ExportJobService.mark_failed(job.id, "Loại xuất file không còn được hỗ trợ.")
    except Exception:
        logger.exception("Job export %s thất bại", job.id)
        ExportJobService.mark_failed(job.id, "Lỗi khi dựng file. Vui lòng thử lại.")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        job.refresh_from_db()
        publish_export_event(job)


class _StorageFailed(Exception):
    """Bọc lỗi upload để phân biệt với lỗi khi dựng file — hai thông báo khác nhau."""


@shared_task
def purge_expired_exports() -> None:
    """Dọn file hết hạn và cứu job kẹt RUNNING.

    Giữ lại bản ghi (chỉ đổi status) vì nó là vết audit "ai đã xuất cái gì".
    """
    now = timezone.now()
    expired = ExportJob.objects.filter(
        status=ExportJobStatus.SUCCESS, expires_at__lt=now
    ).only("id", "file_key")
    for job in expired.iterator(chunk_size=200):
        if job.file_key:
            try:
                export_storage.delete_object(job.file_key)
            except Exception:
                logger.exception("Không xoá được object export %s", job.file_key)
                continue
        ExportJob.objects.filter(id=job.id).update(
            status=ExportJobStatus.EXPIRED, file_key=""
        )

    # Worker chết giữa chừng để lại job kẹt RUNNING mãi mãi — panel sẽ quay vô tận.
    # Ngưỡng gấp đôi soft_time_limit để không cắt nhầm job đang chạy thật.
    stuck_before = now - timezone.timedelta(seconds=settings.EXPORT_SOFT_TIME_LIMIT * 2)
    ExportJob.objects.filter(
        status=ExportJobStatus.RUNNING, started_at__lt=stuck_before
    ).update(
        status=ExportJobStatus.FAILED,
        error_message="Tiến trình xử lý bị gián đoạn. Vui lòng xuất lại.",
        finished_at=now,
    )
```

- [ ] **Step 5: Đăng ký queue, route và beat**

Trong `backend/config/settings_parts/celery.py`:

Sau dòng `CELERY_SLOW_QUEUE = ...`, thêm:

```python
# Queue RIÊNG cho export, không dùng chung `slow`: queue đó đang ôm job Gemini
# (soft_time_limit 300–480s) và precompute báo cáo (time_limit 900s) với concurrency 2
# — dùng chung là đổi timeout lấy độ trễ xếp hàng sau một job LLM 8 phút.
CELERY_EXPORTS_QUEUE = os.getenv("CELERY_EXPORTS_QUEUE", "exports")
```

Trong `CELERY_TASK_ROUTES`, thêm hai dòng cuối:

```python
    "apps.exports.infrastructure.tasks.run_export": {"queue": CELERY_EXPORTS_QUEUE},
    "apps.exports.infrastructure.tasks.purge_expired_exports": {"queue": CELERY_EXPORTS_QUEUE},
```

Trong `CELERY_BEAT_SCHEDULE`, thêm một entry (giữ nguyên khuôn của các entry sẵn có):

```python
    "purge-expired-exports": {
        "task": "apps.exports.infrastructure.tasks.purge_expired_exports",
        "schedule": crontab(minute=15),  # mỗi giờ, phút 15
    },
```

- [ ] **Step 6: Chạy test, xác nhận xanh**

Run: `pytest apps/exports/tests/test_tasks.py -v`
Expected: PASS (5 passed)

- [ ] **Step 7: Commit**

```bash
git add backend/apps/exports/infrastructure backend/config/settings_parts/celery.py backend/apps/exports/tests/test_tasks.py
git commit -m "feat: add export celery tasks and purge job"
```

---

### Task 7: API `/api/exports`

**Files:**
- Create: `backend/apps/exports/transport/__init__.py`, `serializers.py`, `throttling.py`, `views.py`, `backend/apps/exports/urls.py`
- Modify: `backend/config/urls.py`, `backend/config/settings_parts/base.py` (throttle rate)
- Test: `backend/apps/exports/tests/test_api.py`

**Interfaces:**
- Consumes: `ExportJobService`, `ExportJob`, `export_storage.open_stream`
- Produces: 5 endpoint dưới prefix `/api/exports`

- [ ] **Step 1: Viết test (đỏ trước)**

`backend/apps/exports/tests/test_api.py`:

```python
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.exports.application.export_job_service import ExportJobService
from apps.exports.models import ExportJob, ExportJobStatus
from apps.users.test_helpers import make_user_with_sections

pytestmark = pytest.mark.django_db


def test_create_returns_202_and_job(fake_export, export_client):
    with patch("apps.exports.application.export_job_service.transaction.on_commit"):
        r = export_client.post("/api/exports", {"kind": "fake", "params": {"n": 3}}, format="json")
    assert r.status_code == 202
    assert r.data["kind"] == "fake"
    assert r.data["status"] == "pending"
    assert r.data["total_rows"] == 3


def test_create_unknown_kind_returns_400(export_client):
    r = export_client.post("/api/exports", {"kind": "khong-co", "params": {}}, format="json")
    assert r.status_code == 400


def test_create_dedupe_returns_200_with_same_id(fake_export, export_client):
    with patch("apps.exports.application.export_job_service.transaction.on_commit"):
        first = export_client.post(
            "/api/exports", {"kind": "fake", "params": {"n": 3}}, format="json"
        )
        second = export_client.post(
            "/api/exports", {"kind": "fake", "params": {"n": 3}}, format="json"
        )
    assert second.status_code == 200
    assert second.data["id"] == first.data["id"]


def test_list_returns_only_own_jobs(fake_export, export_user, export_client):
    other = make_user_with_sections("export-other-2", ["orders"])
    ExportJobService.create(export_user, "fake", {"n": 1})
    ExportJobService.create(other, "fake", {"n": 1})
    r = export_client.get("/api/exports")
    assert r.status_code == 200
    assert len(r.data["items"]) == 1


def test_download_forbidden_for_other_user(fake_export, export_user):
    from rest_framework.test import APIClient

    job, _ = ExportJobService.create(export_user, "fake", {"n": 1})
    ExportJob.objects.filter(id=job.id).update(
        status=ExportJobStatus.SUCCESS, file_key="exports/x/y.xlsx"
    )
    other = make_user_with_sections("export-other-3", ["orders"])
    client = APIClient()
    client.force_authenticate(other)
    r = client.get(f"/api/exports/{job.id}/download")
    assert r.status_code == 404


def test_download_expired_returns_410(fake_export, export_user, export_client):
    job, _ = ExportJobService.create(export_user, "fake", {"n": 1})
    ExportJob.objects.filter(id=job.id).update(
        status=ExportJobStatus.SUCCESS,
        file_key="exports/x/y.xlsx",
        expires_at=timezone.now() - timezone.timedelta(seconds=1),
    )
    r = export_client.get(f"/api/exports/{job.id}/download")
    assert r.status_code == 410


def test_download_streams_file(fake_export, export_user, export_client):
    job, _ = ExportJobService.create(export_user, "fake", {"n": 1})
    ExportJob.objects.filter(id=job.id).update(
        status=ExportJobStatus.SUCCESS, file_key="exports/x/y.xlsx", file_size=2
    )
    with patch("apps.exports.transport.views.export_storage") as storage:
        storage.open_stream.return_value = __import__("io").BytesIO(b"ab")
        r = export_client.get(f"/api/exports/{job.id}/download")
    assert r.status_code == 200
    assert "attachment" in r["Content-Disposition"]


def test_cancel_sets_cancelled(fake_export, export_user, export_client):
    job, _ = ExportJobService.create(export_user, "fake", {"n": 1})
    r = export_client.delete(f"/api/exports/{job.id}")
    assert r.status_code == 204
    job.refresh_from_db()
    assert job.status == ExportJobStatus.CANCELLED
```

- [ ] **Step 2: Chạy test, xác nhận đỏ**

Run: `pytest apps/exports/tests/test_api.py -v`
Expected: FAIL — 404 vì chưa có route

- [ ] **Step 3: Viết serializers**

`backend/apps/exports/transport/__init__.py` — file rỗng.

`backend/apps/exports/transport/serializers.py`:

```python
from rest_framework import serializers

from apps.exports.models import ExportJob


class ExportCreateSerializer(serializers.Serializer):
    kind = serializers.CharField(max_length=50)
    # Kiểu dict tự do: mỗi definition tự validate phần của mình (registry.validate).
    params = serializers.DictField(required=False, default=dict)


class ExportJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExportJob
        fields = (
            "id", "kind", "status", "total_rows", "progress_rows",
            "file_name", "file_size", "error_message", "created_at", "finished_at",
        )
        read_only_fields = fields
```

- [ ] **Step 4: Viết throttle**

`backend/apps/exports/transport/throttling.py`:

```python
from rest_framework.throttling import SimpleRateThrottle

from apps.common.throttling import ClientIpMixin, ThrottleGuardMixin


class ExportCreateThrottle(ThrottleGuardMixin, ClientIpMixin, SimpleRateThrottle):
    """Trần tần suất TẠO job export.

    Cùng lý do với OrderPrintRateThrottle: dedupe chỉ chặn bấm lại y hệt, còn một
    script đổi tham số mỗi lượt vẫn tạo được job vô hạn và lấp kín queue `exports`.
    Key theo user (không phải IP) vì cả văn phòng ra internet chung một IP NAT.
    """

    scope = "export_create"

    def get_cache_key(self, request, view):
        user = getattr(request, "user", None)
        ident = (
            f"user-{user.pk}"
            if getattr(user, "is_authenticated", False)
            else self.get_ident(request)
        )
        return self.cache_format % {"scope": self.scope, "ident": ident}
```

Thêm rate vào `DEFAULT_THROTTLE_RATES` trong `backend/config/settings_parts/base.py` (ngay sau dòng `"order_print": ...`):

```python
        # Tạo job export (apps.exports). Mỗi job chiếm một slot worker hàng chục giây
        # nên đây là hành vi đắt; 30/giờ đủ rộng cho thao tác tay, đủ chặt để một
        # script không lấp kín queue.
        "export_create": os.getenv("EXPORT_CREATE_THROTTLE_RATE", "30/hour"),
```

- [ ] **Step 5: Viết views**

`backend/apps/exports/transport/views.py`:

```python
import logging

from django.http import FileResponse
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.exports.application.export_job_service import ExportJobService, TooManyActiveJobs
from apps.exports.application.registry import UnknownExportKind
from apps.exports.infrastructure import export_storage
from apps.exports.models import ACTIVE_STATUSES, ExportJob
from apps.exports.transport.serializers import ExportCreateSerializer, ExportJobSerializer
from apps.exports.transport.throttling import ExportCreateThrottle

logger = logging.getLogger(__name__)

_LIST_LIMIT = 20
# 64KB: đủ lớn để không tạo hàng nghìn lượt read cho file vài chục MB, đủ nhỏ để
# không dựng lại nguyên file trong RAM — chính là thứ thiết kế này sinh ra để tránh.
_CHUNK = 64 * 1024


def _create_job(request):
    """Thân xử lý POST. Là hàm THƯỜNG (không decorator) vì `GET /api/exports` và
    `POST /api/exports` dùng chung một path nên chỉ có MỘT view DRF ở đó."""
    serializer = ExportCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    kind = serializer.validated_data["kind"]
    params = serializer.validated_data.get("params") or {}

    try:
        job, created = ExportJobService.create(request.user, kind, params)
    except UnknownExportKind:
        return Response(
            {"detail": "Loại xuất file không hợp lệ."}, status=status.HTTP_400_BAD_REQUEST
        )
    except TooManyActiveJobs:
        return Response(
            {"detail": "Bạn đang có quá nhiều file chờ xử lý. Đợi xong rồi xuất tiếp."},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    except PermissionDenied as exc:
        return Response({"detail": str(exc.detail)}, status=status.HTTP_403_FORBIDDEN)
    except ValidationError:
        raise

    if created:
        ExportJobService.enqueue(job)
    return Response(
        ExportJobSerializer(job).data,
        # Dedupe trả 200 (không tạo gì mới) để FE phân biệt được với lượt tạo thật.
        status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
    )


@extend_schema(exclude=True)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
@throttle_classes([ExportCreateThrottle])
def exports_collection(request):
    """GET = danh sách job của chính mình; POST = tạo job mới."""
    if request.method == "GET":
        queryset = ExportJob.objects.filter(user=request.user)
        if request.query_params.get("active") == "1":
            queryset = queryset.filter(status__in=ACTIVE_STATUSES)
        jobs = queryset.order_by("-created_at")[:_LIST_LIMIT]
        return Response({"items": ExportJobSerializer(jobs, many=True).data})
    return _create_job(request)


@extend_schema(exclude=True)
@api_view(["GET", "DELETE"])
@permission_classes([IsAuthenticated])
def export_detail(request, job_id):
    job = ExportJob.objects.filter(id=job_id, user=request.user).first()
    if job is None:
        return Response({"detail": "Không tìm thấy."}, status=status.HTTP_404_NOT_FOUND)
    if request.method == "DELETE":
        ExportJobService.cancel(job)
        return Response(status=status.HTTP_204_NO_CONTENT)
    return Response(ExportJobSerializer(job).data)


@extend_schema(exclude=True)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def download_export(request, job_id):
    # Lọc luôn theo user trong query: file của người khác phải là 404 chứ không phải
    # 403 — 403 xác nhận job đó tồn tại, và id là UUID nên 404 không mơ hồ với ai cả.
    job = ExportJob.objects.filter(id=job_id, user=request.user).first()
    if job is None:
        return Response({"detail": "Không tìm thấy file."}, status=status.HTTP_404_NOT_FOUND)
    if not job.is_downloadable():
        return Response(
            {"detail": "File đã hết hạn hoặc chưa sẵn sàng."}, status=status.HTTP_410_GONE
        )
    try:
        body = export_storage.open_stream(job.file_key)
    except Exception:
        logger.exception("Không mở được file export %s", job.id)
        return Response(
            {"detail": "Không đọc được file."}, status=status.HTTP_502_BAD_GATEWAY
        )
    response = FileResponse(body, as_attachment=True, filename=job.file_name)
    response.block_size = _CHUNK
    return response
```

Và bổ sung vào `ExportCreateThrottle` (file `throttling.py`):

```python
    def allow_request(self, request, view):
        # Throttle chỉ tính cho POST. Panel poll GET mỗi 3s — tính chung thì người
        # dùng bị chặn oan trong vài phút mà chẳng tạo job nào.
        if request.method != "POST":
            return True
        return super().allow_request(request, view)
```

- [ ] **Step 6: Viết urls và gắn vào root**

`backend/apps/exports/urls.py`:

```python
from django.urls import path

from apps.exports.transport import views

urlpatterns = [
    path("", views.exports_collection, name="export-collection"),
    path("<uuid:job_id>", views.export_detail, name="export-detail"),
    path("<uuid:job_id>/download", views.download_export, name="export-download"),
]
```

Ba route, không phải năm: `GET`/`POST /api/exports` dùng chung path rỗng nên gộp vào
`exports_collection`; `GET`/`DELETE /api/exports/<id>` gộp vào `export_detail`.

Trong `backend/config/urls.py`, thêm **trước** dòng catch-all `re_path(r"^api/", include("apps.audit.urls"))`:

```python
    re_path(r"^api/exports/?", include("apps.exports.urls")),
```

- [ ] **Step 7: Chạy test, xác nhận xanh**

Run: `pytest apps/exports/tests/test_api.py -v`
Expected: PASS (8 passed)

- [ ] **Step 8: Chạy toàn bộ test app exports**

Run: `pytest apps/exports -v`
Expected: PASS (tất cả)

- [ ] **Step 9: Commit**

```bash
git add backend/apps/exports backend/config/urls.py backend/config/settings_parts/base.py
git commit -m "feat: add exports REST API"
```

---

### Task 8: WebSocket consumer cho export

**Files:**
- Modify: `backend/apps/channels_core/consumers.py`, `backend/config/asgi.py` hoặc file routing WS tương ứng
- Test: `backend/apps/exports/tests/test_ws.py`

**Interfaces:**
- Consumes: `apps.exports.infrastructure.event_publisher.export_group`
- Produces: consumer nhận `{"type": "export_event", "payload": {...}}` và gửi `payload` xuống client

- [ ] **Step 1: Đọc consumer hiện có để bám khuôn**

Run: `grep -n "class .*Consumer" -A 40 backend/apps/channels_core/consumers.py | head -80`

Ghi lại: tên consumer CRM, cách nó join group trong `connect()`, cách nó gửi event xuống client.

- [ ] **Step 2: Viết test (đỏ trước)**

`backend/apps/exports/tests/test_ws.py`:

```python
import pytest
from channels.layers import get_channel_layer

from apps.exports.infrastructure.event_publisher import export_group, publish_export_event
from apps.exports.application.export_job_service import ExportJobService

pytestmark = pytest.mark.django_db


def test_publish_sends_to_user_group(fake_export, export_user, settings):
    settings.CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
    }
    job, _ = ExportJobService.create(export_user, "fake", {"n": 1})
    publish_export_event(job)

    from asgiref.sync import async_to_sync

    layer = get_channel_layer()
    # Group rỗng thì group_send không lỗi; test này khoá đúng tên group đang dùng.
    assert export_group(export_user.id) == f"exports_user_{export_user.id}"
    async_to_sync(layer.group_send)(
        export_group(export_user.id), {"type": "export_event", "payload": {}}
    )
```

- [ ] **Step 3: Thêm group vào consumer**

Trong `apps/channels_core/consumers.py`, ở consumer CRM, thêm vào `connect()` sau khi đã xác thực user (bám đúng chỗ nó join group `crm_updates`):

```python
        # Group riêng theo user cho tiến trình xuất file — payload chỉ liên quan
        # một người, không fan-out ra cả công ty như crm_updates.
        if getattr(self.scope.get("user"), "is_authenticated", False):
            self._export_group = f"exports_user_{self.scope['user'].id}"
            await self.channel_layer.group_add(self._export_group, self.channel_name)
```

Trong `disconnect()`:

```python
        group = getattr(self, "_export_group", "")
        if group:
            await self.channel_layer.group_discard(group, self.channel_name)
```

Và thêm handler:

```python
    async def export_event(self, event):
        await self.send_json(event["payload"])
```

(Nếu consumer dùng `send()` với `json.dumps` thay vì `send_json`, dùng đúng cách của consumer đó.)

- [ ] **Step 4: Chạy test, xác nhận xanh**

Run: `pytest apps/exports/tests/test_ws.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/apps/channels_core/consumers.py backend/apps/exports/tests/test_ws.py
git commit -m "feat: broadcast export job status over websocket"
```

---

### Task 9: `OrdersExport` — chuyển luồng đơn hàng

**Files:**
- Create: `backend/apps/orders/exports_definitions.py`
- Modify: `backend/apps/orders/apps.py` (ready), `backend/apps/orders/transport/views/order_views_impl/order_export_view.py` (tách helper dùng chung)
- Test: `backend/apps/orders/tests/test_orders_export_definition.py`

**Interfaces:**
- Consumes: `ExportDefinition`, `register_export`, `OrderRepository.export_orders_queryset`, `EXPORT_MAX_ROWS`, `_section_filter_overrides`, `_apply_user_channel_scope`, `build_orders_filters`
- Produces: `kind = "orders"` trong registry

- [ ] **Step 1: Viết test (đỏ trước)**

`backend/apps/orders/tests/test_orders_export_definition.py`:

```python
import pytest
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.exports.application.registry import get_definition
from apps.users.test_helpers import make_user_with_sections

pytestmark = pytest.mark.django_db


def test_orders_definition_is_registered():
    assert get_definition("orders").kind == "orders"


def test_validate_rejects_unknown_section():
    definition = get_definition("orders")
    user = make_user_with_sections("ord-exp-1", ["orders"])
    with pytest.raises(ValidationError):
        definition.validate(user, {"section": "khong-co", "columns": [{"key": "name"}]})


def test_validate_rejects_empty_columns():
    definition = get_definition("orders")
    user = make_user_with_sections("ord-exp-2", ["orders"])
    with pytest.raises(ValidationError):
        definition.validate(user, {"section": "orders", "columns": []})


def test_check_permission_denies_without_export_right():
    definition = get_definition("orders")
    user = make_user_with_sections("ord-exp-3", [])
    params = {"section": "orders", "columns": [{"key": "name", "header": "Tên"}], "ids": [], "filters": {}}
    with pytest.raises(PermissionDenied):
        definition.check_permission(user, params)


def test_write_emits_header_and_rows(order_factory):
    definition = get_definition("orders")
    user = make_user_with_sections("ord-exp-4", ["orders"])
    order = order_factory(name="Anh A")
    params = definition.validate(
        user,
        {"section": "orders", "columns": [{"key": "name", "header": "Tên"}], "ids": [order.id]},
    )

    rows = []

    class _W:
        def write_header(self, headers):
            rows.append(list(headers))

        def write_row(self, values):
            rows.append(list(values))

    definition.write(params, _W(), lambda n: None)
    assert rows[0] == ["Tên"]
    assert ["Anh A"] in rows
```

Nếu chưa có fixture `order_factory` trong `apps/orders/tests/conftest.py`, thêm:

```python
@pytest.fixture
def order_factory(db):
    from apps.orders.models import Order

    def _make(**kwargs):
        data = {"name": "Khách test", "phone": "0900000000", "quantity": 1}
        data.update(kwargs)
        return Order.objects.create(**data)

    return _make
```

(Kiểm tra các trường bắt buộc thực tế của `Order` trước khi viết fixture; bổ sung trường nào model yêu cầu.)

- [ ] **Step 2: Chạy test, xác nhận đỏ**

Run: `pytest apps/orders/tests/test_orders_export_definition.py -v`
Expected: FAIL — `UnknownExportKind: orders`

- [ ] **Step 3: Tách logic chọn queryset ra khỏi view**

Trong `order_export_view.py`, tách phần dựng queryset của `bulk_export_excel` thành hàm dùng chung, đặt ngay dưới `_section_filter_overrides`:

```python
def build_export_orders_queryset(user, section: str, ids, filters_source):
    """Queryset đơn cho một lượt xuất — dùng chung giữa view cũ và ExportDefinition.

    `filters_source` là dict bộ lọc đã parse (không phải request) để hàm này gọi
    được từ worker, nơi không có request object.
    """
    scope_filters, scope_empty = _apply_user_channel_scope({}, user, section)
    if scope_empty:
        return Order.objects.none()
    if ids:
        orders = (
            Order.objects.select_related("channel", "kiotviet_channel", "customer", "staff")
            .prefetch_related(
                "assessment",
                Prefetch(
                    "assessment__needs",
                    queryset=OrderNeed.objects.select_related("need_type"),
                ),
            )
            .filter(id__in=ids, deleted_at__isnull=True)
            .order_by("-created_at", "-id")
        )
        if scope_filters.get("owner_user_id"):
            orders = orders.filter(staff_id=scope_filters["owner_user_id"])
        elif scope_filters.get("channel"):
            orders = orders.filter(channel__code__in=scope_filters["channel"])
        return orders

    filters, scope_empty = _apply_user_channel_scope(dict(filters_source), user, section)
    if scope_empty:
        return Order.objects.none()
    return OrderRepository.export_orders_queryset(filters)
```

- [ ] **Step 4: Viết definition**

`backend/apps/orders/exports_definitions.py`:

```python
"""Định nghĩa export của app orders — đăng ký vào engine ở OrdersConfig.ready()."""

from __future__ import annotations

from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.exports.application.registry import ExportDefinition, register_export
from apps.orders.export_excel import order_row_values
from apps.orders.models import Order
from apps.orders.repositories import EXPORT_MAX_ROWS
from apps.orders.transport.views.order_views_impl.order_export_view import (
    build_export_orders_queryset,
)
from apps.users.auth_core import user_has_any_permission
from apps.users.permissions import ORDER_SECTIONS

# Số dòng nạp mỗi lô từ DB. 2000 là cân bằng giữa số round-trip và bộ nhớ giữ tạm
# cho prefetch assessment/needs của lô đó.
_CHUNK = 2000


def _load_user(params: dict):
    """Nạp lại user từ `params`.

    `estimate` và `write` KHÔNG nhận `user` trong chữ ký (write chạy ở worker, nơi
    không có request), nên `validate` ghi `user_id` vào params và hai method kia nạp
    lại. Đây là hợp đồng chung cho MỌI definition cần resolve record-scope.
    """
    from apps.users.models import User

    return User.objects.filter(id=params["user_id"]).first()


class OrdersExport(ExportDefinition):
    kind = "orders"

    def validate(self, user, raw_params: dict) -> dict:
        section = str(raw_params.get("section") or "orders")
        if section not in ORDER_SECTIONS:
            raise ValidationError({"detail": "Section không hợp lệ."})
        columns = raw_params.get("columns") or []
        if not columns:
            raise ValidationError({"detail": "Chưa chọn cột xuất."})
        ids = [int(v) for v in (raw_params.get("ids") or [])]
        filters = raw_params.get("filters") or {}
        return {
            "user_id": user.id,
            "section": section,
            "columns": [
                {"key": str(c.get("key")), "header": str(c.get("header") or c.get("key"))}
                for c in columns
            ],
            "ids": ids,
            "filters": filters,
        }

    def check_permission(self, user, params: dict) -> None:
        if not user_has_any_permission(user, f"{params['section']}:export"):
            raise PermissionDenied("Bạn không có quyền xuất Excel ở trang này.")

    def estimate(self, params: dict) -> int | None:
        if params["ids"]:
            return len(params["ids"])
        queryset = build_export_orders_queryset(
            _load_user(params), params["section"], [], params["filters"]
        )
        matched = queryset.count()
        if matched > EXPORT_MAX_ROWS:
            raise ValidationError(
                {
                    "detail": (
                        f"Bộ lọc khớp {matched} đơn, vượt giới hạn {EXPORT_MAX_ROWS} đơn "
                        "mỗi lần xuất. Vui lòng thu hẹp bộ lọc (theo ngày/nhân viên/kênh) "
                        "rồi thử lại."
                    )
                }
            )
        return matched

    def file_name(self, params: dict) -> str:
        return f"don-hang-{timezone.localtime(timezone.now()):%Y%m%d-%H%M%S}.xlsx"

    def write(self, params: dict, writer, progress) -> None:
        writer.write_header([c["header"] for c in params["columns"]])
        queryset = build_export_orders_queryset(
            _load_user(params), params["section"], params["ids"], params["filters"]
        )
        count = 0
        for order in queryset.iterator(chunk_size=_CHUNK):
            writer.write_row(order_row_values(order, params["columns"]))
            count += 1
            progress(count)
        progress(count)


register_export(OrdersExport())
```

Ghi chú hợp đồng (áp cho **mọi** definition, kể cả Task 15/16): `estimate` và `write`
không nhận `user` trong chữ ký vì `write` chạy ở worker, nơi không có request. Luồng
nào cần user để resolve record-scope thì `validate` ghi `user_id` vào `params` và hai
method kia nạp lại bằng `_load_user(params)` như trên.

- [ ] **Step 5: Tách hàm dựng dòng từ `export_excel.py`**

Trong `backend/apps/orders/export_excel.py`, tách phần thân vòng lặp thành hàm dùng lại được, giữ `export_orders_to_excel` gọi vào nó (view cũ vẫn chạy cho tới khi bị xoá ở sprint 2):

```python
def order_row_values(order, columns) -> list:
    """Giá trị một dòng export theo `columns`. Tách khỏi export_orders_to_excel để
    ExportDefinition dùng lại mà không kéo theo openpyxl."""
    status_dict = dict(OrderStatus.choices)
    row = []
    for col in columns:
        key = col.get("key")
        # ... (chuyển nguyên khối if/elif hiện tại vào đây, không đổi logic)
        row.append(val)
    return row
```

Rồi `export_orders_to_excel` rút gọn phần vòng lặp thành:

```python
    for order in orders:
        append_clean_row(ws, order_row_values(order, columns))
```

- [ ] **Step 6: Đăng ký trong AppConfig**

Trong `backend/apps/orders/apps.py`, bổ sung vào `ready()`:

```python
        # Đăng ký định nghĩa export vào engine. Import trong ready() vì module đụng
        # model và queryset — nạp ở top-level sẽ chạy trước khi app registry sẵn sàng.
        from apps.orders import exports_definitions  # noqa: F401
```

- [ ] **Step 7: Chạy test, xác nhận xanh**

Run: `pytest apps/orders/tests/test_orders_export_definition.py -v`
Expected: PASS (5 passed)

- [ ] **Step 8: Chạy test cũ của luồng đơn, xác nhận không vỡ**

Run: `pytest apps/orders/tests/test_order_export.py -v`
Expected: PASS (đường đồng bộ cũ vẫn nguyên, chưa xoá)

- [ ] **Step 9: Chạy import-linter**

Run: `lint-imports --config .importlinter`
Expected: 0 broken — `apps.orders` → `apps.exports` là chiều hợp lệ

- [ ] **Step 10: Commit**

```bash
git add backend/apps/orders backend/apps/exports
git commit -m "feat: add orders export definition"
```

---

### Task 10: Compose — service `worker-export` và env

**Files:**
- Modify: `docker-compose.prod.yml`, `docker-compose.staging.yml`, `docker-compose.local.yml`, `backend/.env.example`
- Test: `backend/apps/common/tests/test_settings_guards.py` (bổ sung assert env mới)

**Interfaces:**
- Consumes: `CELERY_EXPORTS_QUEUE`
- Produces: container `desi-worker-export`

- [ ] **Step 1: Thêm service vào `docker-compose.prod.yml`**

Ngay sau service `worker-slow`, thêm:

```yaml
  # Worker thứ tư cho queue `exports`. KHÔNG dùng chung `slow`: queue đó đang ôm job
  # Gemini (soft_time_limit 300–480s) và precompute báo cáo (time_limit 900s) với
  # concurrency 2 — file Excel của người dùng xếp sau một job LLM 8 phút thì việc
  # chuyển sang chạy nền mất hết ý nghĩa.
  worker-export:
    build: *backend-build
    container_name: desi-worker-export
    environment:
      <<: *backend-env
      DB_APPLICATION_NAME: desi-worker-export
      # Công thức giống worker-slow: (mem_limit 768MB - parent ~180MB) / concurrency 2
      # * 0.85 ≈ 250MB mỗi child. Đổi EXPORT_WORKER_CONCURRENCY thì PHẢI đổi giá trị
      # này theo, nếu không child bị cgroup OOM killer SIGKILL trước khi Celery kịp
      # recycle → connection Postgres mồ côi chiếm slot.
      CELERY_WORKER_MAX_MEMORY_PER_CHILD: ${EXPORT_WORKER_MAX_MEMORY_PER_CHILD:-250000}
    depends_on:
      web: { condition: service_started }
    command:
      - sh
      - -c
      - |
        mkdir -p /tmp/prom_multiproc
        exec celery -A config worker -Q exports -l ${DJANGO_LOG_LEVEL:-INFO} --concurrency=${EXPORT_WORKER_CONCURRENCY:-2}
    restart: unless-stopped
    networks: [desi-net]
    healthcheck:
      test: ["CMD-SHELL", "celery -A config inspect ping -d celery@$$(hostname) --timeout=10 | grep -q pong"]
      interval: 60s
      timeout: 15s
      retries: 3
      start_period: 60s
    mem_limit: 768m
    cpus: 1.0
```

- [ ] **Step 2: Thêm env vào anchor `backend-env` của compose**

Trong `docker-compose.prod.yml`, khối `x-backend-env` (nơi có `WEB_HTTP_CONCURRENCY`), thêm:

```yaml
  CELERY_EXPORTS_QUEUE: ${CELERY_EXPORTS_QUEUE:-exports}
  MINIO_BUCKET_EXPORTS: ${MINIO_BUCKET_EXPORTS:-desi-exports}
  EXPORT_FILE_TTL_HOURS: ${EXPORT_FILE_TTL_HOURS:-24}
  EXPORT_SOFT_TIME_LIMIT: ${EXPORT_SOFT_TIME_LIMIT:-600}
  EXPORT_MAX_ACTIVE_JOBS_PER_USER: ${EXPORT_MAX_ACTIVE_JOBS_PER_USER:-5}
  EXPORT_CREATE_THROTTLE_RATE: ${EXPORT_CREATE_THROTTLE_RATE:-30/hour}
```

- [ ] **Step 3: Làm tương tự cho staging và local**

Lặp lại Step 1 và Step 2 cho `docker-compose.staging.yml` và `docker-compose.local.yml`, giữ đúng cấu trúc anchor của từng file. Với `docker-compose.local.yml`, đặt `mem_limit`/`cpus` theo mức các worker khác trong chính file đó (máy dev chỉ có 2 CPU).

- [ ] **Step 4: Thêm vào `.env.example`**

```bash
# ── Xuất file chạy nền (apps.exports) ──
CELERY_EXPORTS_QUEUE=exports
EXPORT_WORKER_CONCURRENCY=2
EXPORT_WORKER_MAX_MEMORY_PER_CHILD=250000
MINIO_BUCKET_EXPORTS=desi-exports
EXPORT_FILE_TTL_HOURS=24
EXPORT_SOFT_TIME_LIMIT=600
EXPORT_MAX_ACTIVE_JOBS_PER_USER=5
EXPORT_CREATE_THROTTLE_RATE=30/hour
```

- [ ] **Step 5: Kiểm tra compose parse được**

Run: `docker compose -f docker-compose.prod.yml config > /dev/null && echo OK`
Expected: `OK`

Lặp lại cho `staging` và `local`.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.prod.yml docker-compose.staging.yml docker-compose.local.yml backend/.env.example
git commit -m "chore: add worker-export service and env"
```

---

### Task 11: FE — API client và types

**Files:**
- Create: `frontend/src/features/exports/types.ts`, `frontend/src/features/exports/api.ts`
- Test: `frontend/src/features/exports/__tests__/api.test.ts`

**Interfaces:**
- Consumes: `fetchJson`, `getValidAccessToken` từ `@/services/http`
- Produces:
  - `type ExportJob = { id, kind, status, total_rows, progress_rows, file_name, file_size, error_message, created_at, finished_at }`
  - `type ExportJobStatus = "pending" | "running" | "success" | "failed" | "cancelled" | "expired"`
  - `createExportJob(kind, params): Promise<ExportJob>`
  - `listExportJobs(activeOnly?): Promise<ExportJob[]>`
  - `cancelExportJob(id): Promise<void>`
  - `downloadExportJob(job): Promise<void>`

- [ ] **Step 1: Viết test (đỏ trước)**

`frontend/src/features/exports/__tests__/api.test.ts`:

```ts
import { describe, expect, it, vi, beforeEach } from "vitest";

import { createExportJob, listExportJobs } from "@/features/exports/api";

vi.mock("@/services/http", () => ({
  fetchJson: vi.fn(),
  getValidAccessToken: vi.fn().mockResolvedValue("tok"),
}));

const { fetchJson } = await import("@/services/http");

describe("exports api", () => {
  beforeEach(() => vi.mocked(fetchJson).mockReset());

  it("gửi kind và params khi tạo job", async () => {
    vi.mocked(fetchJson).mockResolvedValue({ id: "1", kind: "orders", status: "pending" });
    await createExportJob("orders", { section: "orders" });
    expect(vi.mocked(fetchJson)).toHaveBeenCalledWith(
      "/api/exports",
      expect.objectContaining({ method: "POST" }),
    );
    const body = JSON.parse(vi.mocked(fetchJson).mock.calls[0][1]!.body as string);
    expect(body).toEqual({ kind: "orders", params: { section: "orders" } });
  });

  it("lọc active khi được yêu cầu", async () => {
    vi.mocked(fetchJson).mockResolvedValue({ items: [] });
    await listExportJobs(true);
    expect(vi.mocked(fetchJson)).toHaveBeenCalledWith("/api/exports?active=1");
  });
});
```

- [ ] **Step 2: Chạy test, xác nhận đỏ**

Run: `npm run test -- src/features/exports/__tests__/api.test.ts`
Expected: FAIL — không resolve được module `@/features/exports/api`

- [ ] **Step 3: Viết types**

`frontend/src/features/exports/types.ts`:

```ts
export type ExportJobStatus =
  | "pending"
  | "running"
  | "success"
  | "failed"
  | "cancelled"
  | "expired";

export type ExportJob = {
  id: string;
  kind: string;
  status: ExportJobStatus;
  total_rows: number | null;
  progress_rows: number;
  file_name: string;
  file_size: number | null;
  error_message: string;
  created_at: string;
  finished_at: string | null;
};

export const ACTIVE_STATUSES: ExportJobStatus[] = ["pending", "running"];

export function isActive(job: ExportJob): boolean {
  return ACTIVE_STATUSES.includes(job.status);
}
```

- [ ] **Step 4: Viết api client**

`frontend/src/features/exports/api.ts`:

```ts
import { fetchJson, getValidAccessToken } from "@/services/http";
import type { ExportJob } from "@/features/exports/types";

export async function createExportJob(kind: string, params: unknown): Promise<ExportJob> {
  return (await fetchJson("/api/exports", {
    method: "POST",
    body: JSON.stringify({ kind, params }),
  })) as ExportJob;
}

export async function listExportJobs(activeOnly = false): Promise<ExportJob[]> {
  const url = activeOnly ? "/api/exports?active=1" : "/api/exports";
  const data = (await fetchJson(url)) as { items: ExportJob[] };
  return data.items ?? [];
}

export async function cancelExportJob(id: string): Promise<void> {
  await fetchJson(`/api/exports/${id}`, { method: "DELETE" });
}

/** Tải file đã sẵn sàng. Dùng fetch trần vì đây là blob nhị phân, không phải JSON. */
export async function downloadExportJob(job: ExportJob): Promise<void> {
  let token: string | null = null;
  try {
    token = await getValidAccessToken();
  } catch {
    token = null;
  }
  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(`/api/exports/${job.id}/download`, {
    method: "GET",
    credentials: "include",
    headers,
  });
  if (!res.ok) throw new Error("Không tải được file.");
  const blob = await res.blob();
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = job.file_name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}
```

- [ ] **Step 5: Chạy test, xác nhận xanh**

Run: `npm run test -- src/features/exports/__tests__/api.test.ts`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/exports
git commit -m "feat: add exports api client"
```

---

### Task 12: FE — `ExportJobsProvider`

**Files:**
- Create: `frontend/src/features/exports/ExportJobsProvider.tsx`
- Test: `frontend/src/features/exports/__tests__/ExportJobsProvider.test.tsx`

**Interfaces:**
- Consumes: `createExportJob`, `listExportJobs`, `getCRMWebSocket`
- Produces:
  - `<ExportJobsProvider>` component
  - `useExportJobs(): { jobs: ExportJob[], startExport(kind, params): Promise<void>, cancel(id), dismiss(id), isOpen, setOpen }`

- [ ] **Step 1: Viết test (đỏ trước)**

`frontend/src/features/exports/__tests__/ExportJobsProvider.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ExportJobsProvider, useExportJobs } from "@/features/exports/ExportJobsProvider";

vi.mock("@/features/exports/api", () => ({
  createExportJob: vi.fn().mockResolvedValue({
    id: "j1", kind: "orders", status: "pending", total_rows: 10, progress_rows: 0,
    file_name: "don-hang.xlsx", file_size: null, error_message: "",
    created_at: "", finished_at: null,
  }),
  listExportJobs: vi.fn().mockResolvedValue([]),
  cancelExportJob: vi.fn().mockResolvedValue(undefined),
  downloadExportJob: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("@/services/websocket", () => ({
  getCRMWebSocket: () => ({ addEventListener: vi.fn(), removeEventListener: vi.fn() }),
}));

function Probe() {
  const { jobs, startExport } = useExportJobs();
  return (
    <div>
      <button onClick={() => startExport("orders", { section: "orders" })}>go</button>
      <span data-testid="count">{jobs.length}</span>
    </div>
  );
}

function renderProbe() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ExportJobsProvider>
        <Probe />
      </ExportJobsProvider>
    </QueryClientProvider>,
  );
}

describe("ExportJobsProvider", () => {
  it("thêm job vào danh sách sau khi startExport", async () => {
    renderProbe();
    await userEvent.click(screen.getByText("go"));
    await waitFor(() => expect(screen.getByTestId("count").textContent).toBe("1"));
  });
});
```

- [ ] **Step 2: Chạy test, xác nhận đỏ**

Run: `npm run test -- src/features/exports/__tests__/ExportJobsProvider.test.tsx`
Expected: FAIL — không resolve được module

- [ ] **Step 3: Viết provider**

`frontend/src/features/exports/ExportJobsProvider.tsx`:

```tsx
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { cancelExportJob, createExportJob, listExportJobs } from "@/features/exports/api";
import { isActive, type ExportJob } from "@/features/exports/types";
import { getCRMWebSocket } from "@/services/websocket";

type ExportJobsContextValue = {
  jobs: ExportJob[];
  activeCount: number;
  isOpen: boolean;
  setOpen: (open: boolean) => void;
  startExport: (kind: string, params: unknown) => Promise<void>;
  cancel: (id: string) => Promise<void>;
  dismiss: (id: string) => void;
};

const ExportJobsContext = createContext<ExportJobsContextValue | null>(null);

export const exportKeys = {
  list: ["exports", "list"] as const,
};

/** Nhịp poll. Đường chính là WebSocket; poll chỉ là lưới an toàn khi WS rớt hoặc
 * người dùng vừa F5 — nên chỉ bật khi CÒN job đang chạy. */
const POLL_MS = 3000;

export function ExportJobsProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [isOpen, setOpen] = useState(false);
  const [dismissed, setDismissed] = useState<string[]>([]);

  const { data } = useQuery({
    queryKey: exportKeys.list,
    queryFn: () => listExportJobs(false),
    refetchInterval: (query) => {
      const items = (query.state.data ?? []) as ExportJob[];
      return items.some(isActive) ? POLL_MS : false;
    },
  });

  const jobs = useMemo(
    () => (data ?? []).filter((job) => !dismissed.includes(job.id)),
    [data, dismissed],
  );

  useEffect(() => {
    const ws = getCRMWebSocket();
    const handler = () => {
      queryClient.invalidateQueries({ queryKey: exportKeys.list });
    };
    ws.addEventListener("export.updated", handler);
    return () => ws.removeEventListener("export.updated", handler);
  }, [queryClient]);

  const startExport = useCallback(
    async (kind: string, params: unknown) => {
      try {
        const job = await createExportJob(kind, params);
        // Ghi thẳng vào cache thay vì chờ vòng poll kế: panel phải hiện NGAY khi bấm,
        // đó là toàn bộ mục đích của nó.
        queryClient.setQueryData<ExportJob[]>(exportKeys.list, (prev) => {
          const rest = (prev ?? []).filter((item) => item.id !== job.id);
          return [job, ...rest];
        });
        setDismissed((prev) => prev.filter((id) => id !== job.id));
        setOpen(true);
      } catch (error) {
        const message =
          error instanceof Error && error.message ? error.message : "Không thể xuất file.";
        toast.error(message);
      }
    },
    [queryClient],
  );

  const cancel = useCallback(
    async (id: string) => {
      await cancelExportJob(id);
      queryClient.invalidateQueries({ queryKey: exportKeys.list });
    },
    [queryClient],
  );

  const dismiss = useCallback((id: string) => {
    setDismissed((prev) => (prev.includes(id) ? prev : [...prev, id]));
  }, []);

  const value = useMemo<ExportJobsContextValue>(
    () => ({
      jobs,
      activeCount: jobs.filter(isActive).length,
      isOpen,
      setOpen,
      startExport,
      cancel,
      dismiss,
    }),
    [jobs, isOpen, startExport, cancel, dismiss],
  );

  return <ExportJobsContext.Provider value={value}>{children}</ExportJobsContext.Provider>;
}

export function useExportJobs(): ExportJobsContextValue {
  const value = useContext(ExportJobsContext);
  if (value === null) {
    throw new Error("useExportJobs phải nằm trong ExportJobsProvider.");
  }
  return value;
}
```

- [ ] **Step 4: Chạy test, xác nhận xanh**

Run: `npm run test -- src/features/exports/__tests__/ExportJobsProvider.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/exports
git commit -m "feat: add export jobs provider"
```

---

### Task 13: FE — `ExportDock`

**Files:**
- Create: `frontend/src/features/exports/ExportDockRow.tsx`, `frontend/src/features/exports/ExportDock.tsx`
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/features/exports/__tests__/ExportDock.test.tsx`

**Interfaces:**
- Consumes: `useExportJobs`, `downloadExportJob`
- Produces: `<ExportDock />`

- [ ] **Step 1: Viết test (đỏ trước)**

`frontend/src/features/exports/__tests__/ExportDock.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ExportDock } from "@/features/exports/ExportDock";
import type { ExportJob } from "@/features/exports/types";

const downloadExportJob = vi.fn().mockResolvedValue(undefined);
vi.mock("@/features/exports/api", () => ({
  downloadExportJob: (job: ExportJob) => downloadExportJob(job),
}));

const state = {
  jobs: [] as ExportJob[],
  activeCount: 0,
  isOpen: true,
  setOpen: vi.fn(),
  startExport: vi.fn(),
  cancel: vi.fn(),
  dismiss: vi.fn(),
};

vi.mock("@/features/exports/ExportJobsProvider", () => ({
  useExportJobs: () => state,
}));

function job(overrides: Partial<ExportJob>): ExportJob {
  return {
    id: "j1", kind: "orders", status: "running", total_rows: 100, progress_rows: 40,
    file_name: "don-hang.xlsx", file_size: null, error_message: "",
    created_at: "", finished_at: null, ...overrides,
  };
}

describe("ExportDock", () => {
  it("hiện tiến độ khi đang chạy", () => {
    state.jobs = [job({ status: "running" })];
    render(<ExportDock />);
    expect(screen.getByText(/Đang xử lý xuất file/)).toBeInTheDocument();
    expect(screen.getByText(/40\/100/)).toBeInTheDocument();
  });

  it("hiện link tải khi thành công và gọi download khi bấm", async () => {
    state.jobs = [job({ status: "success", progress_rows: 100 })];
    render(<ExportDock />);
    await userEvent.click(screen.getByText("Nhấn vào đây để tải xuống"));
    expect(downloadExportJob).toHaveBeenCalled();
  });

  it("hiện lý do thất bại", () => {
    state.jobs = [job({ status: "failed", error_message: "Quá thời gian cho phép." })];
    render(<ExportDock />);
    expect(screen.getByText(/Quá thời gian cho phép/)).toBeInTheDocument();
  });

  it("không render gì khi không có job", () => {
    state.jobs = [];
    const { container } = render(<ExportDock />);
    expect(container.firstChild).toBeNull();
  });
});
```

- [ ] **Step 2: Chạy test, xác nhận đỏ**

Run: `npm run test -- src/features/exports/__tests__/ExportDock.test.tsx`
Expected: FAIL — không resolve được module

- [ ] **Step 3: Viết `ExportDockRow`**

`frontend/src/features/exports/ExportDockRow.tsx`:

```tsx
import { useState } from "react";
import { Check, FileSpreadsheet, Loader2, RotateCcw, X } from "lucide-react";
import { toast } from "sonner";

import { downloadExportJob } from "@/features/exports/api";
import type { ExportJob } from "@/features/exports/types";

type Props = {
  job: ExportJob;
  onCancel: (id: string) => void;
  onDismiss: (id: string) => void;
};

function progressLabel(job: ExportJob): string {
  if (!job.total_rows) return "Đang xử lý xuất file…";
  return `Đang xử lý xuất file… (${job.progress_rows}/${job.total_rows} dòng)`;
}

export function ExportDockRow({ job, onCancel, onDismiss }: Props) {
  const [downloading, setDownloading] = useState(false);

  const handleDownload = async () => {
    setDownloading(true);
    try {
      await downloadExportJob(job);
    } catch {
      toast.error("Không tải được file. Vui lòng thử lại.");
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="flex items-start gap-3 px-4 py-3">
      <FileSpreadsheet className="mt-0.5 h-5 w-5 shrink-0 text-slate-400" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm text-slate-800">{job.file_name}</p>
        {(job.status === "pending" || job.status === "running") && (
          <p className="mt-1 flex items-center gap-1.5 text-xs text-slate-400">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            {progressLabel(job)}
          </p>
        )}
        {job.status === "success" && (
          <button
            type="button"
            onClick={handleDownload}
            disabled={downloading}
            className="mt-1 text-xs text-sky-600 hover:underline disabled:opacity-60"
          >
            {downloading ? "Đang tải…" : "Nhấn vào đây để tải xuống"}
          </button>
        )}
        {job.status === "failed" && (
          <p className="mt-1 text-xs text-red-600">
            Thất bại: {job.error_message || "Lỗi không xác định."}
          </p>
        )}
        {job.status === "cancelled" && <p className="mt-1 text-xs text-slate-400">Đã huỷ.</p>}
        {job.status === "expired" && (
          <p className="mt-1 text-xs text-slate-400">File đã hết hạn, vui lòng xuất lại.</p>
        )}
      </div>
      {job.status === "success" && <Check className="mt-0.5 h-4 w-4 shrink-0 text-sky-600" />}
      {(job.status === "pending" || job.status === "running") && (
        <button
          type="button"
          aria-label="Huỷ"
          onClick={() => onCancel(job.id)}
          className="mt-0.5 text-slate-400 hover:text-slate-600"
        >
          <X className="h-4 w-4" />
        </button>
      )}
      {job.status === "failed" && (
        <button
          type="button"
          aria-label="Bỏ qua"
          onClick={() => onDismiss(job.id)}
          className="mt-0.5 text-slate-400 hover:text-slate-600"
        >
          <RotateCcw className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Viết `ExportDock`**

`frontend/src/features/exports/ExportDock.tsx`:

```tsx
import { ChevronDown, ChevronUp, X } from "lucide-react";

import { ExportDockRow } from "@/features/exports/ExportDockRow";
import { useExportJobs } from "@/features/exports/ExportJobsProvider";
import { useState } from "react";

export function ExportDock() {
  const { jobs, isOpen, setOpen, cancel, dismiss } = useExportJobs();
  const [collapsed, setCollapsed] = useState(false);

  if (jobs.length === 0 || !isOpen) return null;

  return (
    <div className="fixed bottom-0 right-6 z-50 w-[360px] max-w-[calc(100vw-2rem)] overflow-hidden rounded-t-lg border border-slate-200 bg-white shadow-lg">
      <div className="flex items-center justify-between bg-sky-600 px-4 py-2.5 text-white">
        <span className="text-sm font-medium">Xử lý xuất file</span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            aria-label={collapsed ? "Mở rộng" : "Thu gọn"}
            onClick={() => setCollapsed((prev) => !prev)}
            className="rounded p-1 hover:bg-sky-500"
          >
            {collapsed ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </button>
          <button
            type="button"
            aria-label="Đóng"
            onClick={() => setOpen(false)}
            className="rounded p-1 hover:bg-sky-500"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>
      {!collapsed && (
        <div className="max-h-80 divide-y divide-slate-100 overflow-y-auto">
          {jobs.map((job) => (
            <ExportDockRow key={job.id} job={job} onCancel={cancel} onDismiss={dismiss} />
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 5: Gắn vào `App.tsx`**

Thêm import:

```tsx
import { ExportDock } from "@/features/exports/ExportDock";
import { ExportJobsProvider } from "@/features/exports/ExportJobsProvider";
```

Bọc cây render (đặt cạnh `<Toaster />`, bên trong provider của react-query):

```tsx
      <ExportJobsProvider>
        <RouterProvider router={router} />
        <ExportDock />
      </ExportJobsProvider>
```

(Giữ nguyên mọi provider/context đang bao quanh; chỉ chèn thêm một lớp.)

- [ ] **Step 6: Chạy test, xác nhận xanh**

Run: `npm run test -- src/features/exports/__tests__/ExportDock.test.tsx`
Expected: PASS (4 passed)

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/exports frontend/src/App.tsx
git commit -m "feat: add export dock panel"
```

---

### Task 14: FE — nối nút Xuất Excel của 4 trang đơn

**Files:**
- Modify: `frontend/src/features/orders/OrdersTable/useOrdersTableData.ts`
- Test: `frontend/src/features/orders/__tests__/useOrdersTableData.export-gate.test.tsx` (mở rộng)

**Interfaces:**
- Consumes: `useExportJobs().startExport`
- Produces: nút Xuất Excel tạo job `kind="orders"`

- [ ] **Step 1: Đọc test hiện có để không phá hợp đồng**

Run: `cat frontend/src/features/orders/__tests__/useOrdersTableData.export-gate.test.tsx`

Ghi lại các assert đang khoá hành vi "chưa chọn dòng thì báo lỗi" — hành vi đó phải giữ nguyên.

- [ ] **Step 2: Viết test mới (đỏ trước)**

Thêm vào file test đó:

```tsx
it("tạo job export thay vì tải blob", async () => {
  const startExport = vi.fn().mockResolvedValue(undefined);
  // ... render hook với ExportJobsProvider mock trả startExport
  // rồi gọi handleExportConfirm
  expect(startExport).toHaveBeenCalledWith(
    "orders",
    expect.objectContaining({ section: "orders", columns: expect.any(Array) }),
  );
});
```

(Bám đúng cách file test đó đang render hook — dùng lại wrapper của nó, chỉ thêm mock cho `@/features/exports/ExportJobsProvider`.)

- [ ] **Step 3: Chạy test, xác nhận đỏ**

Run: `npm run test -- src/features/orders/__tests__/useOrdersTableData.export-gate.test.tsx`
Expected: FAIL — `startExport` chưa được gọi

- [ ] **Step 4: Sửa hook**

Trong `useOrdersTableData.ts`, thêm import và thay thân `handleExportConfirm`:

```tsx
import { useExportJobs } from "@/features/exports/ExportJobsProvider";
```

```tsx
  const { startExport } = useExportJobs();

  const handleExportConfirm = useCallback(() => {
    if (selectedIds.length === 0 && exportAllFilters === null) {
      toast.error("Vui lòng chọn ít nhất một đơn hàng để xuất Excel.");
      setOpenExportModal(false);
      return;
    }
    localStorage.setItem("desi_export_columns", JSON.stringify(exportColumns));

    const columnsPayload = exportColumns.map((key) => {
      const option = ORDER_COLUMN_OPTIONS.find((o) => o.key === key);
      const labelMsg = option ? t(option.label) : key;
      return { key, header: labelMsg };
    });

    // Chọn đơn → xuất đúng các đơn đó. Không chọn → xuất mọi đơn khớp bộ lọc hiện tại
    // (server tự quét mọi trang). Bộ lọc đi trong `filters` thay vì trải phẳng ở gốc
    // body: server ghi nguyên khối này vào job và worker đọc lại, nên nó phải là một
    // nhánh riêng, không lẫn với `section`/`columns`/`ids`.
    void startExport("orders", {
      section: exportSection,
      columns: columnsPayload,
      ids: selectedIds.length > 0 ? selectedIds : [],
      filters: selectedIds.length > 0 ? {} : (exportAllFilters ?? {}),
    });
    setOpenExportModal(false);
  }, [exportColumns, selectedIds, exportAllFilters, exportSection, t, startExport]);
```

Xoá `downloadFileFromApi` nếu sau thay đổi này nó không còn call site nào (kiểm bằng grep trước khi xoá).

- [ ] **Step 5: Chạy test, xác nhận xanh**

Run: `npm run test -- src/features/orders`
Expected: PASS

- [ ] **Step 6: Chạy toàn bộ test FE**

Run: `npm run test`
Expected: PASS ngoại trừ các test đã đỏ sẵn trên master (`RequireAuth.test.tsx`, 6 test `report_trends`, 5 test `realtime-upsert-filters`) — ghi lại danh sách đỏ trước và sau để đối chiếu, **không** được thêm test đỏ mới.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/orders
git commit -m "feat: route orders export through job dock"
```

---

## Sprint 2 — Migrate 6 luồng còn lại, xoá đường cũ

### Task 15: Ba định nghĩa export của kho

**Files:**
- Create: `backend/apps/inventory/exports_definitions.py`
- Modify: `backend/apps/inventory/apps.py`
- Test: `backend/apps/inventory/tests/test_inventory_export_definitions.py`

**Interfaces:**
- Consumes: `StockQueryService`, `MergedIssueHistory`, `MergedReturnHistory`, `PendingScanService`, `_EXPORT_MAX_ROWS`, `_EXPORT_LIMIT`
- Produces: `kind` = `"stock"`, `"dispatch_history"`, `"return_history"`

- [ ] **Step 1: Viết test (đỏ trước)**

`backend/apps/inventory/tests/test_inventory_export_definitions.py`:

```python
import pytest
from rest_framework.exceptions import ValidationError

from apps.exports.application.registry import get_definition

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("kind", ["stock", "dispatch_history", "return_history"])
def test_registered(kind):
    assert get_definition(kind).kind == kind


def test_stock_requires_warehouse_id():
    definition = get_definition("stock")
    with pytest.raises(ValidationError):
        definition.validate(None, {})


def test_stock_rejects_over_cap(monkeypatch):
    from apps.inventory import exports_definitions

    definition = get_definition("stock")
    params = definition.validate(None, {"warehouse_id": 1})
    monkeypatch.setattr(exports_definitions, "_STOCK_MAX_ROWS", 0)
    with pytest.raises(ValidationError):
        definition.estimate(params)
```

- [ ] **Step 2: Chạy test, xác nhận đỏ**

Run: `pytest apps/inventory/tests/test_inventory_export_definitions.py -v`
Expected: FAIL — `UnknownExportKind`

- [ ] **Step 3: Viết definitions**

`backend/apps/inventory/exports_definitions.py` — ba class, mỗi class chuyển nguyên logic của view tương ứng:

- `StockExport`: `validate` đòi `warehouse_id` (thiếu → `ValidationError("Thiếu tham số warehouse_id.")`), kiểm tra kho tồn tại; `estimate` chạy `StockQueryService.list_stock(...)` đếm và chặn ở `_STOCK_MAX_ROWS = 20000` với đúng thông báo cũ; `file_name` giữ công thức `ton-kho-<mã kho>-<yyyymmdd>.xlsx`; `write` gọi `pivot_stock` rồi ghi header `["Kho","SKU","Tên sản phẩm","Bán được","Bảo hành","Lỗi","Tổng","RR3","RR7"]` và từng dòng.
- `DispatchHistoryExport` và `ReturnHistoryExport`: `validate` chuyển nguyên `_common_filters` (đổi nguồn từ `request.query_params` sang dict thô, giữ nguyên quy tắc kiểm `YYYY-MM-DD` và parse `product_ids`), thêm khoá `group` (`"sku"` hay rỗng); `write` gọi đúng `MergedIssueHistory.skus_page` / `orders_page` / `MergedReturnHistory.skus_page` / `list_page` với `_EXPORT_LIMIT = 5000`, giữ nguyên khối gộp ghi chú `PendingScanService.scan_notes_by_code`, khối `pending` loại trùng mã vận đơn, dòng cảnh báo cắt bớt và các dòng footer tổng.

Mỗi class kết thúc bằng `register_export(...)`.

- [ ] **Step 4: Đăng ký trong AppConfig**

Trong `backend/apps/inventory/apps.py`, thêm vào `ready()` (tạo `ready()` nếu chưa có):

```python
    def ready(self):
        from apps.inventory import exports_definitions  # noqa: F401
```

- [ ] **Step 5: Chạy test, xác nhận xanh**

Run: `pytest apps/inventory/tests/test_inventory_export_definitions.py -v`
Expected: PASS

- [ ] **Step 6: Chạy test kho hiện có, xác nhận không vỡ**

Run: `pytest apps/inventory -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/apps/inventory
git commit -m "feat: add inventory export definitions"
```

---

### Task 16: Định nghĩa export bảo hành + mã bảo hành + nhật ký

**Files:**
- Create: `backend/apps/warranty/exports_definitions.py`, `backend/apps/warranty_codes/exports_definitions.py`, `backend/apps/audit/exports_definitions.py`
- Modify: `apps.py` của ba app
- Test: `backend/apps/warranty/tests/test_warranty_export_definition.py`, `backend/apps/warranty_codes/tests/test_batch_export_definition.py`, `backend/apps/audit/tests/test_audit_export_definition.py`

**Interfaces:**
- Consumes: `WarrantyClaimRepository`, `WarrantyCodeBatch`, `AuditLog` queryset + filter hiện có
- Produces: `kind` = `"warranty_claims"`, `"warranty_code_batch"`, `"audit_logs"`

- [ ] **Step 1: Viết test cho `warranty_claims` (đỏ trước)**

`backend/apps/warranty/tests/test_warranty_export_definition.py`:

```python
import pytest
from rest_framework.exceptions import ValidationError

from apps.exports.application.registry import get_definition

pytestmark = pytest.mark.django_db


def test_registered():
    assert get_definition("warranty_claims").kind == "warranty_claims"


def test_estimate_rejects_over_cap(monkeypatch):
    from apps.warranty import exports_definitions

    definition = get_definition("warranty_claims")
    params = definition.validate(None, {})
    monkeypatch.setattr(exports_definitions, "_CLAIMS_MAX_ROWS", 0)
    with pytest.raises(ValidationError):
        definition.estimate(params)
```

- [ ] **Step 2: Chạy test, xác nhận đỏ**

Run: `pytest apps/warranty/tests/test_warranty_export_definition.py -v`
Expected: FAIL — `UnknownExportKind`

- [ ] **Step 3: Viết ba definitions**

- `WarrantyClaimsExport` (`apps/warranty/exports_definitions.py`): `validate` nhận `ids` (list int) hoặc `q`/`status`; **thêm trần mới** `_CLAIMS_MAX_ROWS = 20000` với thông báo `"Bộ lọc khớp {n} phiếu, vượt giới hạn {cap} phiếu mỗi lần xuất. Vui lòng thu hẹp bộ lọc rồi thử lại."` (luồng này hiện **không có trần nào**); `write` giữ nguyên bộ cột của `claim_excel.py`.
- `WarrantyCodeBatchExport` (`apps/warranty_codes/exports_definitions.py`): `validate` đòi `batch_id`, kiểm tra lô tồn tại; `file_name` = `f"{batch.batch_code}.xlsx"`; `write` giữ nguyên header `("SERIAL","URL","SKU")` và vòng `.iterator(chunk_size=5000)`; **giữ nguyên** việc set `exported_at` sau khi ghi xong (đặt ở cuối `write`).
- `AuditLogsExport` (`apps/audit/exports_definitions.py`): `file_ext = "csv"`, `file_name` kết thúc `.csv`; `validate` chuyển các query param filter hiện có thành dict; `write` giữ nguyên 10 cột `_EXPORT_COLUMNS` và trần `10_000`.

Mỗi file kết thúc bằng `register_export(...)`; mỗi `AppConfig.ready()` import module tương ứng.

- [ ] **Step 4: Viết test cho hai definition còn lại**

`backend/apps/warranty_codes/tests/test_batch_export_definition.py`:

```python
import pytest

from apps.exports.application.registry import get_definition
from apps.warranty_codes.application.services.batch_service import WarrantyCodeBatchService
from apps.warranty_codes.models import WarrantyCodeBatch

pytestmark = pytest.mark.django_db


def test_write_emits_all_codes_and_marks_exported():
    batch = WarrantyCodeBatchService.create_batch(quantity=3, product_id=None, created_by=None)
    definition = get_definition("warranty_code_batch")
    params = definition.validate(None, {"batch_id": batch.id})

    rows = []

    class _W:
        def write_header(self, headers):
            rows.append(tuple(headers))

        def write_row(self, values):
            rows.append(tuple(values))

    definition.write(params, _W(), lambda n: None)
    assert rows[0] == ("SERIAL", "URL", "SKU")
    assert len(rows) == 4
    batch.refresh_from_db()
    assert batch.exported_at is not None
```

`backend/apps/audit/tests/test_audit_export_definition.py`:

```python
import pytest

from apps.exports.application.registry import get_definition

pytestmark = pytest.mark.django_db


def test_audit_export_is_csv():
    definition = get_definition("audit_logs")
    assert definition.file_ext == "csv"
    assert definition.file_name({}).endswith(".csv")
```

- [ ] **Step 5: Chạy test, xác nhận xanh**

Run: `pytest apps/warranty/tests/test_warranty_export_definition.py apps/warranty_codes/tests/test_batch_export_definition.py apps/audit/tests/test_audit_export_definition.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/apps/warranty backend/apps/warranty_codes backend/apps/audit
git commit -m "feat: add warranty, warranty codes and audit export definitions"
```

---

### Task 17: FE — nối 6 nút xuất còn lại

**Files:**
- Modify: `frontend/src/features/inventory/queries.ts`, `issue-history-queries.ts`, `return-history-queries.ts`, `frontend/src/lib/api/warrantyClaims.ts`, `warrantyCodes.ts`, `audit-logs.ts` và các component gọi chúng
- Test: `frontend/src/features/inventory/__tests__/stock-export.test.tsx` (sửa lại)

**Interfaces:**
- Consumes: `useExportJobs().startExport`
- Produces: 6 nút xuất tạo job

- [ ] **Step 1: Tìm mọi call site**

Run: `grep -rn "downloadXlsxExport\|downloadStockExport" frontend/src --include=*.ts --include=*.tsx`

Ghi lại danh sách file và component gọi.

- [ ] **Step 2: Sửa test `stock-export.test.tsx` (đỏ trước)**

Đổi assert từ "gọi `downloadXlsxExport` với URL" sang "gọi `startExport('stock', { warehouse_id, search })`".

- [ ] **Step 3: Chạy test, xác nhận đỏ**

Run: `npm run test -- src/features/inventory/__tests__/stock-export.test.tsx`
Expected: FAIL

- [ ] **Step 4: Sửa từng call site**

Với mỗi nút: thay lời gọi hàm tải blob bằng `startExport(kind, params)` với `kind`/`params` khớp `validate` của definition tương ứng:

| Nút | kind | params |
|---|---|---|
| Tồn kho | `stock` | `{ warehouse_id, search }` |
| Lịch sử xuất | `dispatch_history` | `{ date_from, date_to, warehouse_id, product, product_ids, order, carrier, group }` |
| Lịch sử trả | `return_history` | như trên |
| Phiếu bảo hành | `warranty_claims` | `{ ids, q, status }` |
| Lô mã bảo hành | `warranty_code_batch` | `{ batch_id }` |
| Nhật ký hệ thống | `audit_logs` | `{ ...filter hiện có }` |

- [ ] **Step 5: Chạy test, xác nhận xanh**

Run: `npm run test -- src/features/inventory src/lib/api`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "feat: route remaining exports through job dock"
```

---

### Task 18: Xoá đường export đồng bộ

**Files:**
- Delete: `frontend/src/lib/api/xlsx.ts`
- Modify: `backend/apps/orders/transport/views/order_views_impl/order_export_view.py`, `backend/apps/orders/urls.py`, `backend/apps/inventory/transport/views/stock_views.py`, `flow_history_views.py`, `backend/apps/inventory/urls.py`, `backend/apps/warranty/claim_views.py`, `backend/apps/warranty/urls.py`, `backend/apps/warranty_codes/transport/batch_views.py`, `backend/apps/warranty_codes/urls.py`, `backend/apps/audit/transport/views/audit_views_impl.py`
- Delete tests: `backend/apps/orders/tests/test_order_export.py`, `backend/apps/warranty/tests/test_claim_bulk_export.py`, `backend/apps/warranty_codes/tests/test_export.py`, `backend/apps/inventory/tests/test_flow_history_excel.py` (phần test endpoint), `frontend/src/features/inventory/__tests__/stock-export.test.tsx` (phần test URL cũ)

**Interfaces:**
- Consumes: —
- Produces: chỉ còn 2 endpoint export đồng bộ (HR)

- [ ] **Step 1: Kiểm tra nội dung nghiệp vụ đã được phủ ở test mới**

Run: `pytest apps/exports apps/orders/tests/test_orders_export_definition.py apps/inventory/tests/test_inventory_export_definitions.py apps/warranty/tests/test_warranty_export_definition.py apps/warranty_codes/tests/test_batch_export_definition.py apps/audit/tests/test_audit_export_definition.py -v`
Expected: PASS toàn bộ

Đọc lại từng test sắp xoá; assert nào chưa có bản tương đương ở test mới thì **bổ sung vào test mới trước**, rồi mới sang bước sau. Đặc biệt: `BulkExportExcelSectionFilterTests` (parity giữa tập xuất và tập list theo section) phải có bản tương đương trong `test_orders_export_definition.py`.

- [ ] **Step 2: Xoá 7 view + route**

Xoá `bulk_export_excel` và route `orders-bulk-export-excel`; `export_stock` và route; `issue_export`/`return_export` và route; `export_warranty_claims_view` và route; `export_batch` và route; action `export` của audit viewset. Giữ nguyên hai endpoint HR.

Giữ lại `export_excel.py` / `stock_excel.py` / `flow_history_excel.py` / `claim_excel.py` **chỉ khi** còn hàm được definition dùng lại; hàm nào không còn call site thì xoá luôn (kiểm bằng grep).

- [ ] **Step 3: Xoá `xlsx.ts` và mọi import còn sót**

Run: `grep -rn "downloadXlsxExport\|lib/api/xlsx" frontend/src`
Expected: không còn kết quả nào sau khi xoá.

- [ ] **Step 4: Xoá test của endpoint cũ**

- [ ] **Step 5: Chạy toàn bộ suite backend**

Run: `pytest apps -q`
Expected: PASS, ngoại trừ đúng các test đã đỏ sẵn trên master (6 test `report_trends`, 5 test `realtime-upsert-filters`). Ghi lại số lượng đỏ trước/sau để chứng minh không thêm mới.

- [ ] **Step 6: Chạy toàn bộ suite FE**

Run: `npm run test`
Expected: PASS ngoại trừ `RequireAuth.test.tsx` đỏ sẵn trên master.

- [ ] **Step 7: Cập nhật OpenAPI schema**

Run: `python manage.py spectacular --file schema.yaml`
Expected: `schema.yaml` cập nhật, không còn 7 endpoint đã xoá.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor: remove synchronous export endpoints"
```

---

### Task 19: nginx `proxy_read_timeout` cho `/api/`

**Files:**
- Modify: `nginx/customer.delax.vn.conf.prod-template`, `nginx/customer.delax.vn.host.conf.example`, `nginx/default.conf`, `nginx/conf.d/default.conf`

**Interfaces:**
- Consumes: —
- Produces: `/api/` không còn cắt ở 60s

- [ ] **Step 1: Thêm timeout vào location `/api/` của từng file**

```nginx
    # Mặc định của nginx là 60s. Hai endpoint HR (bảng lương / chấm công tháng) vẫn
    # chạy đồng bộ theo chủ đích (docs/superpowers/specs/2026-08-06-async-export-jobs-design.md
    # mục 2), và các báo cáo nặng khác cũng vượt 60s khi kỳ lọc rộng — 60s là quả mìn
    # hẹn giờ, không phải hàng rào bảo vệ.
    proxy_read_timeout 300s;
    proxy_send_timeout 300s;
```

- [ ] **Step 2: Kiểm tra cú pháp**

Run: `docker run --rm -v "$PWD/nginx:/etc/nginx/conf.d:ro" nginx:alpine nginx -t 2>&1 | tail -5`
Expected: `syntax is ok` / `test is successful` (nếu template có biến không resolve được, kiểm bằng cách đọc kỹ diff thay vì chạy `nginx -t`).

- [ ] **Step 3: Commit**

```bash
git add nginx
git commit -m "chore: raise api proxy read timeout to 300s"
```

---

## Sprint 3 — Quan sát và làm cứng

### Task 20: Metric Prometheus cho export

**Files:**
- Create: `backend/apps/exports/infrastructure/metrics.py`
- Modify: `backend/apps/exports/infrastructure/tasks.py`, `monitoring/prometheus/alerts/*.yml`
- Test: `backend/apps/exports/tests/test_metrics.py`

**Interfaces:**
- Consumes: `prometheus_client`
- Produces: `export_jobs_total{kind,status}` (Counter), `export_job_duration_seconds{kind}` (Histogram), `export_job_rows{kind}` (Histogram)

- [ ] **Step 1: Đọc khuôn metric hiện có**

Run: `cat backend/apps/common/http_metrics.py`

Bám đúng cách file đó khai báo collector (multiprocess mode đang bật qua `/tmp/prom_multiproc`).

- [ ] **Step 2: Viết test (đỏ trước)**

`backend/apps/exports/tests/test_metrics.py`:

```python
from apps.exports.infrastructure.metrics import (
    export_job_duration_seconds,
    export_jobs_total,
)


def test_counter_accepts_labels():
    export_jobs_total.labels(kind="orders", status="success").inc()
    export_job_duration_seconds.labels(kind="orders").observe(1.5)
```

- [ ] **Step 3: Viết metrics và gắn vào task**

`backend/apps/exports/infrastructure/metrics.py`:

```python
from prometheus_client import Counter, Histogram

export_jobs_total = Counter(
    "export_jobs_total", "Số job export theo loại và kết quả", ["kind", "status"]
)
export_job_duration_seconds = Histogram(
    "export_job_duration_seconds",
    "Thời gian dựng file export",
    ["kind"],
    # Bucket bám thực tế: dưới 5s là file nhỏ, trên 300s là sắp chạm soft_time_limit.
    buckets=(1, 5, 15, 30, 60, 120, 300, 600),
)
export_job_rows = Histogram(
    "export_job_rows",
    "Số dòng mỗi file export",
    ["kind"],
    buckets=(100, 1000, 5000, 10000, 20000, 50000),
)
```

Trong `tasks.py`, ghi nhận ở nhánh thành công và các nhánh lỗi (dùng `time.monotonic()` đo từ lúc `mark_running`).

- [ ] **Step 4: Thêm alert rule**

Trong file alert của `monitoring/prometheus/alerts/`, thêm:

```yaml
      - alert: ExportJobsFailing
        expr: increase(export_jobs_total{status="failed"}[15m]) > 3
        for: 5m
        labels: { severity: warning }
        annotations:
          summary: "Nhiều job xuất file thất bại"
          description: "Hơn 3 job export failed trong 15 phút — kiểm tra desi-worker-export và MinIO."

      - alert: ExportQueueBacklog
        expr: celery_queue_length{queue_name="exports"} > 20
        for: 10m
        labels: { severity: warning }
        annotations:
          summary: "Hàng đợi xuất file tồn đọng"
          description: "Queue `exports` >20 job trong 10 phút — cân nhắc tăng EXPORT_WORKER_CONCURRENCY."
```

(Kiểm tên metric queue length thật mà `celery-exporter` đang phát trước khi ghi — đọc `monitoring/` hoặc query Prometheus.)

- [ ] **Step 5: Chạy test và kiểm tra rule**

Run: `pytest apps/exports/tests/test_metrics.py -v`
Expected: PASS

Run: `promtool check rules monitoring/prometheus/alerts/*.yml`
Expected: `SUCCESS`

- [ ] **Step 6: Commit**

```bash
git add backend/apps/exports monitoring
git commit -m "feat: add export job metrics and alerts"
```

---

### Task 21: Audit log cho lượt tạo và lượt tải

**Files:**
- Modify: `backend/apps/exports/transport/views.py`
- Test: `backend/apps/exports/tests/test_audit_trail.py`

**Interfaces:**
- Consumes: task ghi audit của `apps.audit`
- Produces: bản ghi audit cho `export.create` và `export.download`

- [ ] **Step 1: Đọc cách ghi audit hiện có**

Run: `grep -rn "create_audit_log_task\|create_audit_log_batch_task" backend/apps --include=*.py | grep -v tests | head`

Ghi lại chữ ký và các trường bắt buộc.

- [ ] **Step 2: Viết test (đỏ trước)**

`backend/apps/exports/tests/test_audit_trail.py`:

```python
from unittest.mock import patch

import pytest

from apps.exports.application.export_job_service import ExportJobService
from apps.exports.models import ExportJob, ExportJobStatus

pytestmark = pytest.mark.django_db


def test_download_writes_audit_log(fake_export, export_user, export_client):
    job, _ = ExportJobService.create(export_user, "fake", {"n": 1})
    ExportJob.objects.filter(id=job.id).update(
        status=ExportJobStatus.SUCCESS, file_key="exports/x/y.xlsx", file_size=2
    )
    with patch("apps.exports.transport.views.export_storage") as storage, patch(
        "apps.exports.transport.views.record_export_audit"
    ) as audit:
        storage.open_stream.return_value = __import__("io").BytesIO(b"ab")
        export_client.get(f"/api/exports/{job.id}/download")
    audit.assert_called_once()
```

- [ ] **Step 3: Viết hàm ghi audit và gắn vào view**

Thêm vào `views.py` một hàm `record_export_audit(request, job, action: str)` gọi task audit hiện có (best-effort, nuốt lỗi — ghi audit hỏng không được làm hỏng lượt tải), gọi ở `_create_job` (`action="export.create"`) và `download_export` (`action="export.download"`).

- [ ] **Step 4: Chạy test, xác nhận xanh**

Run: `pytest apps/exports/tests/test_audit_trail.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/apps/exports
git commit -m "feat: audit export create and download"
```

---

### Task 22: Runbook vận hành

**Files:**
- Create: `docs/runbooks/export-jobs.md`
- Modify: `docs/superpowers/specs/2026-08-06-async-export-jobs-design.md` (mục 10 — ghi chú sau sprint)

**Interfaces:**
- Consumes: —
- Produces: tài liệu vận hành

- [ ] **Step 1: Viết runbook**

Nội dung bắt buộc, mỗi mục kèm lệnh chạy được:

1. Xem job đang chạy: query `export_jobs` theo `status`.
2. Job kẹt RUNNING: cách nhận biết, `purge_expired_exports` xử lý sau bao lâu, cách chạy tay.
3. Queue tồn đọng: xem độ dài queue `exports` trong Redis, tăng `EXPORT_WORKER_CONCURRENCY` (nhắc **phải** đổi `EXPORT_WORKER_MAX_MEMORY_PER_CHILD` theo công thức).
4. MinIO đầy: kiểm dung lượng bucket `desi-exports`, xác nhận job dọn đang chạy.
5. Người dùng báo "không tải được file": bảng phân nhánh 404 / 410 / 502 và ý nghĩa từng mã.
6. Rollback: đổi FE về nút cũ **không khả thi** sau sprint 2 (endpoint đã xoá) — đường lùi duy nhất là revert cả hai phía; ghi rõ commit ranh giới.

- [ ] **Step 2: Cập nhật mục 10 của spec**

Ghi cho từng sprint: đã làm gì, chưa làm gì, phát sinh gì.

- [ ] **Step 3: Commit**

```bash
git add docs
git commit -m "docs: add export jobs runbook"
```

---

## Self-Review

**Spec coverage** — đối chiếu từng mục của spec:

| Mục spec | Task |
|---|---|
| 4.1 Model `ExportJob` | Task 1 |
| 4.2 Registry + writers | Task 2, 3 |
| 4.3 API 5 endpoint + dedupe + throttle + download stream | Task 7 |
| 4.4 Task + worker riêng + soft_time_limit + purge | Task 6, 10 |
| 4.5 WS + polling | Task 8, 12 |
| 4.6 Bảo mật (chính chủ, audit, RBAC cũ, key khó đoán) | Task 7, 21 |
| 5.1 Provider | Task 12 |
| 5.2 Dock 4 trạng thái | Task 13 |
| 5.3 Chuyển nút + xoá `xlsx.ts` | Task 14, 17, 18 |
| 6 Env + compose + nginx + monitoring | Task 10, 19, 20 |
| 7 Bảng xử lý lỗi | Task 6 (task), Task 7 (API) |
| 8 Test strategy | Task 1–8 (engine), 9/15/16 (parity), 18 (chuyển test trước khi xoá) |
| 9 Sprint | Task 1–14 / 15–19 / 20–22 |

**Điểm cần người thực thi lưu ý** (đã ghi trong task tương ứng, nhắc lại để không bỏ sót):

- Task 7 chỉ có **ba** route: `exports_collection` (GET+POST), `export_detail` (GET+DELETE), `download_export`. `_create_job` là hàm thường, không decorator — hai method chung một path nên chỉ được có một view DRF ở đó.
- `ExportDefinition` không nhận `user` ở `estimate`/`write` (worker không có request), nên `validate` phải nhét `user_id` vào `params` và hai method kia nạp lại bằng `_load_user`. Hợp đồng này áp cho **mọi** definition ở Task 9, 15, 16.
- Task 15/16 mô tả nội dung định nghĩa bằng lời thay vì code đầy đủ vì chúng là **chuyển nguyên logic** từ view hiện có. Người thực thi phải đọc view cũ và bê nguyên, không viết lại theo trí nhớ — mọi khác biệt là một lỗi dữ liệu thầm lặng.
- Task 18 chỉ được xoá test cũ **sau khi** đã có assert tương đương ở test mới. `BulkExportExcelSectionFilterTests` là quan trọng nhất.
