"""Nhận dạng sản phẩm nhắc tới trong một chuỗi tự do (tên chiến dịch quảng cáo).

Đặt ở ``apps.catalog`` vì đây là nghiệp vụ của danh mục sản phẩm ("chuỗi này nhắc tới
SKU nào"), và vì cả ``apps.facebook_ads`` lẫn ``apps.google_ads`` đều cần dùng — hai app
đó nằm CÙNG tầng nên không được import lẫn nhau (.importlinter).

Xem docs/ads-campaign-product-mapping.md. Số liệu đo trên chi phí ads prod tháng 8/2026.

## Cách khớp

1. Tách tên theo mọi ký tự không phải chữ-số.
2. Chuẩn hoá token: **bỏ số 0 đứng đầu**, và **chữ `O` hoa gõ nhầm thành số 0** — quy
   ước đặt tên của phòng Ads viết SKU thành ``0DC30``/``0DG20``/``0BNC02``, và prod có
   cả ``ODC12`` (chữ O) lẫn ``0DC12`` (số 0) cho cùng một sản phẩm.
3. Token khớp **MÃ GỐC** (phần trước dấu ``-`` hoặc ``{``) chứ không phải SKU đầy đủ.
4. Một mã gốc có thể ứng nhiều biến thể → chọn biến thể **bán chạy nhất** theo bảng xếp
   hạng doanh thu 90 ngày mà caller truyền vào.

### Vì sao KHÔNG ưu tiên khớp SKU đầy đủ

Đây là bug đã suýt phát trên prod. Danh mục có SKU ``TMS01`` — tên đúng bằng mã, doanh
thu 90 ngày 1,7tr, tức một SKU rác — bên cạnh ``TMS01-PROMAX`` 47,5 tỉ. Khớp SKU đầy đủ
sẽ gán **657 chiến dịch** thảm massage về cái rác đó. Gom theo mã gốc rồi xếp theo doanh
thu cho ra đúng biến thể phòng Ads đang chạy (``SQA03`` → ``SQA03-1.7M``: 965 đơn so với
5 đơn của bản 1.5M).
"""

import re
from decimal import Decimal

from apps.catalog.models import Product

_TOKEN_SPLIT = re.compile(r"[^A-Za-z0-9]+")
_LEADING_ZEROS = re.compile(r"^0+")
# Chữ O hoa đứng đầu một token CÓ CHỨA SỐ: gần như chắc chắn là số 0 gõ nhầm
# (``ODC12``). Không áp cho token thuần chữ, kẻo nuốt mất chữ O của từ thật.
_LEADING_O = re.compile(r"^O(?=[A-Z]*\d)")

# Mã ngắn hơn ngưỡng này sinh khớp nhầm hàng loạt ("B1", "TK8" trong tên chiến dịch).
MIN_CODE_LENGTH = 3


def _base_code(sku: str) -> str:
    """``MSC02-CAOCAP{DEL}`` → ``MSC02``. Phần định danh sản phẩm, bỏ phần biến thể."""
    return sku.split("-", 1)[0].split("{", 1)[0].strip().upper()


class SkuIndex:
    """Bản đồ mã gốc → các sản phẩm mang mã đó, kèm luật chọn biến thể.

    Dựng một lần rồi tái dùng cho cả lô chiến dịch — mỗi lần dựng là một lượt quét
    ``products``.
    """

    def __init__(self, by_code: dict[str, list[int]], rank: dict[int, Decimal]):
        self._by_code = by_code
        self._rank = rank

    @property
    def codes(self) -> set[str]:
        return set(self._by_code)

    def candidates(self, code: str) -> list[int]:
        return self._by_code.get(code, [])

    def pick(self, code: str) -> tuple[int | None, bool]:
        """Sản phẩm cho một mã gốc, kèm cờ ``phải đoán``.

        Cờ đoán = True khi mã gốc có NHIỀU biến thể nên phải chọn theo doanh thu. Nhờ
        cờ này, người soát chỉ phải xem lại phần máy đoán chứ không phải xem lại tất cả.
        """
        products = self._by_code.get(code) or []
        if not products:
            return None, False
        if len(products) == 1:
            return products[0], False
        best = max(products, key=lambda pid: (self._rank.get(pid, Decimal("0")), -pid))
        return best, True


def build_sku_index(rank: dict[int, Decimal] | None = None) -> SkuIndex:
    """Bảng tra mã gốc → sản phẩm. ``rank`` = doanh thu gần đây, dùng để chọn biến thể.

    Không truyền ``rank`` thì biến thể được chọn theo id nhỏ nhất — chỉ đủ dùng cho
    test; caller thật phải truyền bảng xếp hạng, nếu không ``TMS01`` sẽ ra SKU rác.

    Bỏ sản phẩm đã xoá mềm: gán chi phí về SP đã xoá thì báo cáo không hiện nổi tên nó.
    """
    by_code: dict[str, list[int]] = {}
    rows = Product.objects.filter(deleted_at__isnull=True).values_list("id", "sku")
    for product_id, sku in rows:
        code = _base_code(sku or "")
        if len(code) < MIN_CODE_LENGTH:
            continue
        by_code.setdefault(code, []).append(product_id)
    return SkuIndex(by_code, rank or {})


def tokenize(text: str) -> list[str]:
    """Token đã chuẩn hoá, GIỮ THỨ TỰ xuất hiện trong chuỗi.

    Thứ tự quan trọng: khi tên chiến dịch nhắc nhiều sản phẩm, luật nới lỏng lấy mã
    xuất hiện ĐẦU TIÊN — đó thường là sản phẩm chính của chiến dịch.
    """
    tokens: list[str] = []
    for raw in _TOKEN_SPLIT.split(text or ""):
        if not raw:
            continue
        upper = raw.upper()
        for candidate in (upper, _LEADING_ZEROS.sub("", upper), _LEADING_O.sub("", upper)):
            if candidate and candidate not in tokens:
                tokens.append(candidate)
    return tokens


def match_codes(text: str, index: SkuIndex) -> list[str]:
    """Các mã gốc mà chuỗi nhắc tới, theo thứ tự xuất hiện, không trùng."""
    codes = index.codes
    return [token for token in tokenize(text) if token in codes]


def match_product(
    text: str, index: SkuIndex, *, loose: bool = False
) -> tuple[int | None, bool]:
    """``(product_id, phải đoán)`` cho một tên chiến dịch.

    Chế độ chặt: nhiều mã gốc khác nhau → không gán (để người quyết định).
    Chế độ nới: lấy mã xuất hiện đầu tiên và đánh dấu là đoán.
    """
    codes = match_codes(text, index)
    if not codes:
        return None, False
    if len(codes) > 1 and not loose:
        return None, False
    product_id, guessed = index.pick(codes[0])
    return product_id, guessed or len(codes) > 1


def learn_code_aliases(rows, index: SkuIndex, *, min_support: int = 3) -> dict[str, int]:
    """Học "token lạ → sản phẩm" từ những chiến dịch ĐÃ có sản phẩm.

    ``rows`` = iterable ``(name, product_id)`` của các chiến dịch đã gán. Token nào luôn
    đi cùng một sản phẩm và xuất hiện đủ ``min_support`` lần thì trở thành bí danh —
    nhờ đó ``Desiglobal_BonNgamChanGapGon_...`` (không có mã SKU nào) vẫn gán được vì
    token ``BONNGAMCHANGAPGON`` đã xuất hiện ở các chiến dịch có mã.

    Token vốn đã là mã gốc bị loại: chúng đã có đường khớp riêng, không cần học.
    """
    known_codes = index.codes
    votes: dict[str, dict[int, int]] = {}
    for name, product_id in rows:
        if product_id is None:
            continue
        for token in tokenize(name):
            if token in known_codes or len(token) < 4 or token.isdigit():
                continue
            votes.setdefault(token, {})
            votes[token][product_id] = votes[token].get(product_id, 0) + 1

    aliases: dict[str, int] = {}
    for token, by_product in votes.items():
        if len(by_product) != 1:
            continue
        product_id, support = next(iter(by_product.items()))
        if support >= min_support:
            aliases[token] = product_id
    return aliases


def assign_products_to_campaigns(
    queryset,
    *,
    rank: dict[int, Decimal] | None = None,
    loose: bool = False,
    dry_run: bool = False,
) -> dict:
    """Điền ``product`` cho các chiến dịch chưa ai gán tay.

    Nhận queryset thay vì import model: ``apps.facebook_ads`` và ``apps.google_ads`` nằm
    cùng tầng nên không app nào được import app kia, mà catalog thì nằm dưới cả hai.
    Queryset chỉ cần có ``name``, ``product_id``, ``product_source``.

    LUẬT SẮT: không bao giờ chạm dòng ``product_source='manual'``. Máy sai một lần,
    người sửa một lần — sửa xong mà lượt sau ghi đè thì không ai còn tin số.

    ``loose=True`` bật thêm hai luật đoán: tên nhắc nhiều sản phẩm thì lấy cái đầu, và
    học bí danh token từ các chiến dịch đã gán. Mọi dòng phải đoán được đánh dấu
    ``product_source='auto_loose'`` để người soát lọc riêng ra xem.

    ``dry_run=True`` chỉ đếm, không ghi.
    """
    model = queryset.model
    pending = list(
        queryset.exclude(product_source=model.SOURCE_MANUAL)
        .filter(product__isnull=True)
        .values("id", "name")
    )
    if not pending:
        return {"scanned": 0, "assigned": 0, "guessed": 0, "samples": []}

    index = build_sku_index(rank)
    aliases: dict[str, int] = {}
    if loose:
        assigned_rows = queryset.filter(product__isnull=False).values_list("name", "product_id")
        aliases = learn_code_aliases(assigned_rows, index)

    updates = []
    samples: list[tuple[str, int, bool]] = []
    for row in pending:
        product_id, guessed = match_product(row["name"], index, loose=loose)
        if product_id is None and aliases:
            for token in tokenize(row["name"]):
                if token in aliases:
                    product_id, guessed = aliases[token], True
                    break
        if product_id is None:
            continue
        source = model.SOURCE_AUTO_LOOSE if guessed else model.SOURCE_AUTO
        updates.append(model(id=row["id"], product_id=product_id, product_source=source))
        if len(samples) < 20:
            samples.append((row["name"], product_id, guessed))

    if not dry_run:
        for start in range(0, len(updates), 500):
            model.objects.bulk_update(
                updates[start : start + 500], ["product", "product_source"],
            )
    return {
        "scanned": len(pending),
        "assigned": len(updates),
        "guessed": sum(1 for u in updates if u.product_source == model.SOURCE_AUTO_LOOSE),
        "samples": samples,
    }


def format_backfill_report(result: dict, *, label: str, loose: bool, applied: bool) -> str:
    """Bản in dùng chung cho 2 management command backfill (Facebook + Google).

    Hai app quảng cáo nằm cùng tầng nên không app nào import app kia được; phần dùng
    chung phải nằm ở đây.
    """
    product_names = dict(
        Product.objects.filter(id__in=[pid for _, pid, _ in result["samples"]]).values_list(
            "id", "sku"
        )
    )
    lines = [
        f"{label} — {'GHI THẬT' if applied else 'XEM THỬ (chưa ghi gì)'}, "
        f"luật {'nới' if loose else 'chặt'}",
        f"  chiến dịch chưa gán : {result['scanned']}",
        f"  sẽ gán được         : {result['assigned']} (máy ĐOÁN: {result['guessed']})",
        f"  còn lại gán tay     : {result['scanned'] - result['assigned']}",
    ]
    if result["samples"]:
        lines.append("")
        lines.append("  Mẫu (tối đa 20 dòng):")
        for name, product_id, guessed in result["samples"]:
            flag = "ĐOÁN" if guessed else "chắc"
            sku = product_names.get(product_id, product_id)
            lines.append(f"    [{flag}] {name[:66]:<66} → {sku}")
    return "\n".join(lines)
