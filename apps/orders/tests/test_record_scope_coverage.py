"""LƯỚI CHẶN CẤU TRÚC: view nhận `order_id`/`ids` từ client PHẢI đi qua record-scope.

VÌ SAO TEST NÀY TỒN TẠI
-----------------------
Gốc rễ của họ lỗ hổng audit 2026-07-26 N1 không nằm ở một view cụ thể mà ở
`UserService.can_edit_order` (apps/users/services.py): nó trả `can_edit=True` VÔ
ĐIỀU KIỆN khi scope là `OWN` hoặc `ALL`. Docstring nói "chặn theo owner ở tầng
view", nhưng tầng view thường quên. Mọi bản vá tới nay đều siết ở TỪNG view, nên
bất kỳ view MỚI nào quên gọi `scoped_order_or_error` là mở lại lỗ hổng — và không
có gì báo động.

Test này là cái báo động đó. Nó duyệt URLconf gốc, lấy mọi view của
`apps.orders` / `apps.warranty` / `apps.customers` nhận định danh đơn từ client
(path param `order_id`, hoặc đọc `ids`/`order_ids`/`order_id` từ request), rồi
khẳng định view đó gọi một helper record-scope. View không gọi mà cũng không nằm
trong danh sách trắng bên dưới → ĐỎ.

DANH SÁCH TRẮNG LÀ TÀI LIỆU SỐNG
--------------------------------
`_REVIEWED_NO_SCOPE_NEEDED` và `_KNOWN_GAPS` là bộ nhớ thể chế của đợt audit này:
mỗi entry ghi lại một quyết định đã cân nhắc. Người thêm view mới bị test bắt và
BUỘC phải quyết định (siết, hay khai vào danh sách kèm lý do) thay vì im lặng bỏ
qua. ĐỪNG xoá test hay nhét bừa entry vào danh sách để cho nhanh — nếu bạn không
viết nổi lý do, đó là dấu hiệu view của bạn cần record-scope.

CÁCH NHẬN DIỆN "ĐÃ SCOPE"
-------------------------
Phân tích tĩnh bằng `ast` trên source của hàm view, đi theo lời gọi tới các hàm
cùng module (độ sâu 2) để bắt cả trường hợp view ủy quyền cho helper nội bộ (vd
`download_order_invoice` → `_get_printable_order` → `_scoped_print_queryset` →
`_apply_user_channel_scope`).

Vì sao AST chứ không phải chạy thật/mock: một test hành vi chỉ chứng minh được các
view ĐÃ BIẾT là đúng — nó không thể báo động về view CHƯA TỒN TẠI. Lưới chặn phải
nói về *tập hợp mọi view*, nên nó phải đọc cấu trúc code. Đổi lại, nó không chứng
minh scope được áp ĐÚNG — đó là việc của các test hành vi
(`test_close_case_record_scope.py`, `test_order_record_scope.py`, ...). Hai lớp bổ
sung cho nhau: lớp này bắt "quên hoàn toàn", lớp kia bắt "áp sai".

GIỚI HẠN ĐÃ BIẾT
----------------
- Chỉ 3 app trong phạm vi audit này. App khác (hr, inventory, ...) chưa duyệt.
- (django-ninja `/api/v2/` từng là điểm mù của lưới — gỡ 2026-08-20 cùng ninja,
  audit 2026-08-19 mục 4.12. Nếu sau này mount framework ngoài DRF, nhớ rằng
  callback không thuộc `apps.*` sẽ lọt lưới, phải mở rộng `_APP_PREFIXES`.)
"""

import ast
import importlib
import inspect

from django.test import SimpleTestCase
from django.urls import URLPattern, URLResolver, get_resolver

# App nằm trong phạm vi lưới chặn. Lọc theo `callback.__module__` (không phải theo
# danh sách urlconf) để một `path(..., include(...))` MỚI trong các app này cũng tự
# động được duyệt — vd `apps.orders.urls_sale` đã lọt vào lưới mà không cần khai.
_APP_PREFIXES = ("apps.orders.", "apps.warranty.", "apps.customers.")

# Helper được công nhận là "đã record-scope".
#
# 3 hàm đầu là chuẩn hiện hành (`_view_helpers`). `_apply_user_channel_scope` là cơ
# chế CŨ nhưng tương đương về hiệu lực, còn dùng ở luồng in ấn/export
# (`order_export_view`): nó resolve scope theo section rồi caller lọc
# `staff_id`/`channel__code`. Không ép đổi trong đợt này vì đổi phải viết lại cả
# nhánh `scope_empty` của 4 endpoint in ấn — rủi ro cao hơn lợi ích.
_SCOPE_CALL_NAMES = frozenset(
    {
        "order_record_scope_q",
        "scoped_order_or_error",
        "scoped_orders_queryset",
        "_apply_user_channel_scope",
    }
)

# Tên tham số/khóa mà client dùng để chỉ định đơn. Xuất hiện trong THÂN hàm (hoặc
# helper cùng module) là dấu hiệu view nhận định danh đơn từ client.
_ORDER_IDENTIFIER_LITERALS = frozenset({"ids", "order_ids", "order_id"})

# ── Danh sách trắng 1: đã xét, KHÔNG cần record-scope ────────────────────────
_REVIEWED_NO_SCOPE_NEEDED: dict[str, str] = {
    "apps.orders.transport.views.order_views_impl.order_ingest_views.lark_ingest": (
        "Ingest máy-với-máy: auth bằng API token của hệ thống nguồn, KHÔNG có user "
        "để resolve scope. `order_id` ở đây là id đơn VỪA TẠO trả về cho Lark, "
        "không phải id do client chọn."
    ),
    "apps.orders.transport.views.order_views_impl.floating_pool_view.floating_pool_entries": (
        "`order_id` chỉ là khóa trong payload TRẢ VỀ; view không nhận id đơn từ "
        "client. Tập dòng là kho số nổi (FloatingPoolEntry) — theo thiết kế ai có "
        "`floating-pool:view` cũng thấy, và số điện thoại đã được che theo "
        "`config.hide_phone` + `floating-pool:edit`."
    ),
    "apps.orders.transport.views.order_views_impl.floating_pool_view.floating_pool_claim": (
        "Nhận `entry_id` (bản ghi kho số nổi), KHÔNG nhận `order_id`; `order_id` chỉ "
        "nằm trong response. Nhận số từ kho nổi là hành vi CỐ Ý cross-sale."
    ),
    "apps.orders.transport.views.order_views_impl.kship_sender_address_view."
    "list_create_kship_sender_addresses": (
        "`?ids=` chỉ dùng để suy ra `default_id` điểm gửi hàng theo kênh/chi nhánh "
        "của đơn đầu lô (`_default_sender_address_id`). Không trả bất kỳ trường nào "
        "của đơn, không ghi gì lên đơn — rò rỉ tối đa là 'đơn X mặc định gửi từ kho "
        "Y'. Siết ở đây chỉ làm hỏng UI chọn điểm gửi khi lô đơn trải nhiều kênh."
    ),
    "apps.orders.transport.views.shipping_lookup_view.lookup_by_shipping_code": (
        "Client gửi `code` (mã vận đơn/mã thu hồi) chứ không gửi id đơn; literal "
        "'order_id' chỉ là khóa trong payload trả về của `_serialize`. Đây là hot "
        "path QUÉT KHO: nhân viên cầm kiện hàng thật trên tay, gate `shipments:view` "
        "đúng bằng quyền vận hành. Siết theo owner sẽ chặn kho quét đơn của sale "
        "khác — tức là chặn toàn bộ nghiệp vụ xuất/nhập kho."
    ),
    "apps.warranty.claim_views.list_create_warranty_claims": (
        "QUYẾT ĐỊNH CÓ CHỦ ĐÍCH — xem apps/warranty/tests/"
        "test_claim_order_scope_decision.py. Khách mang máy tới bảo hành có thể đã "
        "mua từ BẤT KỲ sale nào, NV bảo hành không bao giờ là `Order.staff`; siết "
        "theo owner sẽ khiến họ không tạo được phiếu nào. `warranty` cũng đã được "
        "khai tường minh là TẬP CHA trong `_SECTION_ORDER_IDENTITY_Q`. Rào chắn "
        "thật là permission `warranty:view`, không phải record-scope."
    ),
    "apps.warranty.claim_views.detail_update_delete_warranty_claim": (
        "Khóa theo `claim_id`; literal 'order_id' chỉ là `data.pop('order_id')` để "
        "CẤM đổi đơn sau khi tạo phiếu. Cùng kết luận cross-sale như trên."
    ),
    "apps.warranty.claim_views.bulk_delete_warranty_claims": (
        "`ids` là id PHIẾU bảo hành, không phải id đơn; gate `warranty:delete`. "
        "Hàng đợi bảo hành là hàng đợi chung của tổ bảo hành (cùng kết luận)."
    ),
    # entry "export_warranty_claims_view" xoá ở Task 18 cùng endpoint đồng bộ
    # GET /api/warranty/claims/export (view không còn tồn tại).
    "apps.customers.transport.views.customers_views_impl.customer_crud_view."
    "bulk_delete_customers": (
        "`ids` là id KHÁCH HÀNG, không phải id đơn — record-scope theo đơn không áp "
        "được. Scope của section `customers` là việc riêng, ngoài phạm vi audit này."
    ),
}

# ── Danh sách trắng 2: lỗ hổng ĐÃ BIẾT, CHƯA vá ──────────────────────────────
# Khác `_REVIEWED_NO_SCOPE_NEEDED` về bản chất: đây KHÔNG phải "không cần scope"
# mà là "cần scope, chưa vá trong đợt này". Tách riêng để không ai đọc nhầm thành
# đã an toàn. `test_known_gaps_are_still_unscoped` bắt buộc danh sách này chỉ được
# NGẮN ĐI: vá xong phải xoá entry.
#
# RỖNG kể từ 2026-07-26: 4 entry cuối (dispatch_batch + 3 endpoint ghi resale-tracking)
# đã được vá — xem `test_dispatch_batch_record_scope.py` và
# `apps/customers/tests/test_resale_tracking_record_scope.py`. Giữ dict lại (không xoá
# hằng) vì nó là nơi khai nợ cho đợt sau; `test_known_gaps_are_still_unscoped` vẫn canh
# chiều "vá xong phải xoá entry".
_KNOWN_GAPS: dict[str, str] = {}

# Sàn số view ứng viên. Chống TAUTOLOGY: nếu bộ duyệt URL/AST hỏng và trả về tập
# rỗng, mọi assert phía dưới sẽ xanh một cách vô nghĩa. Con số đo được lúc viết là
# 25; đặt sàn thấp hơn một chút để không vỡ khi xoá bớt endpoint hợp lệ.
_MIN_EXPECTED_CANDIDATES = 20


def _view_func_name(callback) -> str:
    """Tên hàm view gốc đứng sau `@api_view`.

    DRF bọc hàm trong `WrappedAPIView` rồi `as_view()`; Django 6 không còn copy
    `__name__` sang closure nên `callback.__name__` là "view". Tên thật nằm ở
    `callback.cls.__name__` (DRF gán `WrappedAPIView.__name__ = func.__name__`).
    """
    cls = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
    return getattr(cls, "__name__", None) or callback.__name__


def _iter_url_entries(patterns, prefix: str = ""):
    for entry in patterns:
        if isinstance(entry, URLResolver):
            yield from _iter_url_entries(entry.url_patterns, prefix + str(entry.pattern))
        elif isinstance(entry, URLPattern):
            yield prefix + str(entry.pattern), entry.callback


def _module_function_nodes(module_name: str) -> dict[str, ast.AST]:
    module = importlib.import_module(module_name)
    tree = ast.parse(inspect.getsource(module))
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _collect(node, functions: dict[str, ast.AST], depth: int = 2):
    """Trả (literal chuỗi, tên hàm được gọi) trong THÂN hàm, đi theo helper cùng module.

    Chỉ duyệt `node.body` — bỏ qua `decorator_list` — để literal trong khai báo
    `@extend_schema(inline_serializer(fields={"ids": ...}))` không bị tính là "view
    đọc ids từ client".

    Đi theo lời gọi tới hàm cùng module (độ sâu 2) vì nhiều view ủy quyền cho helper
    private: nếu chỉ soi thân view thì `download_order_invoice` (gọi
    `_get_printable_order`) sẽ bị báo nhầm là thiếu scope.
    """
    literals: set[str] = set()
    calls: set[str] = set()
    for statement in node.body:
        for child in ast.walk(statement):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                literals.add(child.value)
            elif isinstance(child, ast.Call):
                func = child.func
                if isinstance(func, ast.Name):
                    calls.add(func.id)
                elif isinstance(func, ast.Attribute):
                    calls.add(func.attr)
    if depth > 0:
        for name in sorted(calls):
            helper = functions.get(name)
            if helper is not None and helper is not node:
                sub_literals, sub_calls = _collect(helper, functions, depth - 1)
                literals |= sub_literals
                calls |= sub_calls
    return literals, calls


def collect_candidate_views() -> dict[str, dict]:
    """{view_id: {"route", "scoped", "reason"}} cho mọi view nhận định danh đơn."""
    candidates: dict[str, dict] = {}
    node_cache: dict[str, dict[str, ast.AST]] = {}
    for route, callback in _iter_url_entries(get_resolver().url_patterns):
        module_name = getattr(callback, "__module__", "") or ""
        if not module_name.startswith(_APP_PREFIXES):
            continue
        name = _view_func_name(callback)
        if module_name not in node_cache:
            node_cache[module_name] = _module_function_nodes(module_name)
        node = node_cache[module_name].get(name)
        if node is None:
            continue
        literals, calls = _collect(node, node_cache[module_name])
        takes_order_id = "order_id" in {arg.arg for arg in node.args.args}
        if not takes_order_id and not (literals & _ORDER_IDENTIFIER_LITERALS):
            continue
        view_id = f"{module_name}.{name}"
        candidates[view_id] = {
            "route": route,
            "scoped": bool(calls & _SCOPE_CALL_NAMES),
        }
    return candidates


class RecordScopeCoverageTests(SimpleTestCase):
    """Lưới chặn cấu trúc — xem docstring module để biết ý đồ trước khi sửa."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.candidates = collect_candidate_views()

    def test_candidate_set_is_not_empty(self):
        """Chống tautology: bộ duyệt hỏng → tập rỗng → mọi assert khác xanh vô nghĩa."""
        self.assertGreaterEqual(
            len(self.candidates),
            _MIN_EXPECTED_CANDIDATES,
            "Bộ duyệt URLconf/AST có vẻ đã hỏng (hoặc route bị xóa hàng loạt): "
            f"chỉ tìm thấy {len(self.candidates)} view ứng viên, kỳ vọng >= "
            f"{_MIN_EXPECTED_CANDIDATES}. Sửa bộ duyệt, ĐỪNG hạ ngưỡng.",
        )

    def test_every_order_id_view_goes_through_record_scope(self):
        """Mọi view nhận `order_id`/`ids` phải gọi helper record-scope, hoặc được khai."""
        unscoped = sorted(
            view_id
            for view_id, info in self.candidates.items()
            if not info["scoped"]
            and view_id not in _REVIEWED_NO_SCOPE_NEEDED
            and view_id not in _KNOWN_GAPS
        )
        self.assertEqual(
            unscoped,
            [],
            "View nhận định danh đơn từ client nhưng KHÔNG đi qua record-scope:\n"
            + "\n".join(f"  - {v} ({self.candidates[v]['route']})" for v in unscoped)
            + "\n\nUser chỉ cần quyền vào SECTION là chạm được đơn của bất kỳ ai "
            "(`UserService.can_edit_order` fail-open cho scope OWN/ALL).\n"
            "Cách xử lý, theo thứ tự ưu tiên:\n"
            "  1. Gọi `scoped_order_or_error` / `scoped_orders_queryset` "
            "(apps/orders/transport/views/order_views_impl/_view_helpers.py).\n"
            "  2. Nếu THẬT SỰ không cần: khai vào `_REVIEWED_NO_SCOPE_NEEDED` trong "
            "file này kèm lý do NGHIỆP VỤ cụ thể.\n"
            "  3. Nếu cần nhưng chưa vá được: khai vào `_KNOWN_GAPS` kèm lý do hoãn.",
        )

    def test_whitelist_entries_still_point_at_real_views(self):
        """Chống mục rữa: entry trỏ tới view đã đổi tên/bị xóa phải bị dọn."""
        stale = sorted(
            view_id
            for view_id in (*_REVIEWED_NO_SCOPE_NEEDED, *_KNOWN_GAPS)
            if view_id not in self.candidates
        )
        self.assertEqual(
            stale,
            [],
            "Entry danh sách trắng không còn khớp view nào (đổi tên/xóa/không còn "
            f"nhận order_id): {stale}. Xóa entry hoặc cập nhật đường dẫn.",
        )

    def test_known_gaps_are_still_unscoped(self):
        """`_KNOWN_GAPS` chỉ được NGẮN ĐI: vá xong thì xóa entry, đừng để nợ giả."""
        fixed = sorted(
            view_id for view_id in _KNOWN_GAPS if self.candidates.get(view_id, {}).get("scoped")
        )
        self.assertEqual(
            fixed,
            [],
            f"Các view sau đã được vá record-scope: {fixed}. Xóa chúng khỏi "
            "`_KNOWN_GAPS` để danh sách nợ bảo mật phản ánh đúng thực tế.",
        )

    def test_every_whitelist_entry_has_a_reason(self):
        """Entry không có lý do là entry nhét bừa cho test xanh."""
        for view_id, reason in (*_REVIEWED_NO_SCOPE_NEEDED.items(), *_KNOWN_GAPS.items()):
            self.assertGreater(
                len(reason.strip()),
                30,
                f"{view_id}: lý do quá sơ sài. Viết rõ VÌ SAO — đây là tài liệu cho "
                "người audit sau, không phải thủ tục.",
            )


class ScopeDetectorSelfTests(SimpleTestCase):
    """Tự kiểm bộ dò AST.

    Nếu `_collect` im lặng trả về tập rỗng (vd đổi cấu trúc ast, bắt nhầm exception)
    thì lưới chặn sẽ báo "mọi view đều đã scope" — xanh mà vô dụng. Hai test dưới
    khoá cả hai chiều của bộ dò trên source tổng hợp.
    """

    @staticmethod
    def _collect_from_source(source: str, name: str):
        functions = {
            node.name: node
            for node in ast.parse(source).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        return _collect(functions[name], functions)

    def test_detector_flags_view_without_scope_call(self):
        source = (
            "def bad_view(request, order_id):\n"
            "    order = Order.objects.filter(id=order_id).first()\n"
            "    return Response(OrderSerializer(order).data)\n"
        )
        literals, calls = self._collect_from_source(source, "bad_view")
        self.assertFalse(calls & _SCOPE_CALL_NAMES)

    def test_detector_follows_module_local_helper(self):
        """Ca dễ báo nhầm nhất: view ủy quyền scope cho helper private cùng module."""
        source = (
            "def _get_it(request, order_id):\n"
            "    return scoped_order_or_error(request, order_id, SECTIONS)\n"
            "\n"
            "def good_view(request, order_id):\n"
            "    order, error = _get_it(request, order_id)\n"
            "    return Response({})\n"
        )
        literals, calls = self._collect_from_source(source, "good_view")
        self.assertIn("scoped_order_or_error", calls)

    def test_detector_ignores_literals_in_decorators(self):
        """Literal 'ids' trong `@extend_schema` KHÔNG được tính là đọc ids từ client."""
        source = (
            "@extend_schema(request=inline_serializer('X', fields={'ids': f()}))\n"
            "def schema_only_view(request):\n"
            "    return Response({})\n"
        )
        literals, calls = self._collect_from_source(source, "schema_only_view")
        self.assertNotIn("ids", literals)
