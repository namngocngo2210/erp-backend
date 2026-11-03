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
    # Trùng `kind` luôn là lỗi lập trình — kể cả đăng ký lại cùng một class — nên
    # báo lỗi ngay từ lần đăng ký thứ hai thay vì âm thầm ghi đè định nghĩa cũ.
    #
    # An toàn dù `AppConfig.ready()` có thể chạy hai lần trong test runner: mỗi
    # `ExportDefinition` được khai ở cấp module của `<app>/exports_definitions.py`,
    # và lần `ready()` thứ hai chỉ `import` lại module đó — Python trả về bản đã
    # cache trong `sys.modules`, thân module KHÔNG chạy lại, nên dòng gọi
    # `register_export` bên trong cũng không chạy lại. Vì vậy KHÔNG được nới lỏng
    # thành cho phép đăng ký lại cùng class — nếu `ready()` thật sự chạy lại thân
    # module (tức có bug ở chỗ khác), ta MUỐN Django sập ngay lúc load app.
    if kind in _REGISTRY:
        raise ValueError(f"Trùng kind export: {kind}")
    _REGISTRY[kind] = definition


def get_definition(kind: str) -> ExportDefinition:
    try:
        return _REGISTRY[kind]
    except KeyError as exc:
        raise UnknownExportKind(kind) from exc


def list_kinds() -> list[str]:
    return sorted(_REGISTRY)
