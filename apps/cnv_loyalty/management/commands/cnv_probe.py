"""Probe chẩn đoán CNV chạy trên prod — tìm payload/tham số mà CNV thật chấp nhận.

Sinh ra từ sự cố 2026-08-06: create product bị "Data is invalid", create customer
trùng SĐT bị "existed" — tài liệu CNV không đủ chi tiết để biết trước. Hai chế độ:

  cnv_probe --product-id 1525   # thử lần lượt các biến thể payload product,
                                # biến thể nào CNV nhận thì báo tên rồi XÓA NGAY
                                # product test vừa tạo (trừ khi --keep)
  cnv_probe --phone 0912345678  # thử các tên tham số lọc customer theo SĐT,
                                # báo tên param nào CNV thực sự lọc được

Chạy được cả khi CNV_SYNC_ENABLED=0 (đây là công cụ chẩn đoán, không phải sync).
"""

from __future__ import annotations

import copy

from django.core.management.base import BaseCommand, CommandError

from apps.cnv_loyalty.domain import mappers
from apps.cnv_loyalty.exceptions import CnvError
from apps.cnv_loyalty.infrastructure.cnv_client import CnvLoyaltyClient
from apps.cnv_loyalty.models import CnvConnection

PROBE_TITLE_PREFIX = "ZZ-DESI-PROBE"


def _strip(payload: dict, *fields: str) -> dict:
    clone = copy.deepcopy(payload)
    for field in fields:
        clone["product"].pop(field, None)
    return clone


class Command(BaseCommand):
    help = "Thử payload/tham số với CNV thật để chẩn đoán lỗi 'Data is invalid'/'existed'."

    def add_arguments(self, parser):
        parser.add_argument("--product-id", type=int, default=0)
        parser.add_argument("--phone", default="")
        parser.add_argument("--keep", action="store_true", help="Giữ product test, không xóa")

    def handle(self, *args, **options):
        connection = CnvConnection.get_active()
        if connection is None:
            raise CommandError("Chưa có CnvConnection active.")
        client = CnvLoyaltyClient(connection)

        if options["product_id"]:
            self._probe_product(client, options["product_id"], keep=options["keep"])
        elif options["phone"]:
            self._probe_phone(client, options["phone"])
        else:
            raise CommandError("Cần --product-id hoặc --phone.")

    # -- product ------------------------------------------------------------

    def _probe_product(self, client, product_id: int, keep: bool):
        from apps.catalog.models import Product

        product = Product.objects.filter(pk=product_id).first()
        if product is None:
            raise CommandError(f"Không có product #{product_id}.")

        full = mappers.map_product(product)
        # Đánh dấu title để nhận diện + không đè lên sản phẩm thật cùng tên.
        full["product"]["title"] = f"{PROBE_TITLE_PREFIX} {product.name}"[:250]
        # SKU phải khác SKU thật — tránh CNV coi là trùng với bản ghi sẽ sync sau này.
        full["product"]["variants"][0]["sku"] = f"{PROBE_TITLE_PREFIX}-{product.sku}"[:100]

        variants = [
            ("V1 đầy đủ theo mẫu doc (mapper hiện tại)", full),
            ("V2 bỏ options", _strip(full, "options")),
            ("V3 bỏ options + metafields + images", _strip(full, "options", "metafields", "images")),
            ("V4 bỏ published_at", _strip(full, "published_at")),
            (
                "V5 tối giản (title + variant sku/price)",
                {
                    "product": {
                        "title": full["product"]["title"],
                        "product_type": "Khác",
                        "vendor": "Khác",
                        "variants": [
                            {
                                "title": "Default Title",
                                "sku": full["product"]["variants"][0]["sku"],
                                "price": full["product"]["variants"][0]["price"],
                            }
                        ],
                    }
                },
            ),
        ]

        for label, payload in variants:
            try:
                data = client.create_product(payload)
            except CnvError as exc:
                self.stdout.write(f"[FAIL] {label}: {exc}")
                continue
            cnv_id = (data.get("product") or {}).get("id")
            self.stdout.write(self.style.SUCCESS(f"[OK]   {label} -> product id={cnv_id}"))
            if cnv_id and not keep:
                try:
                    client.delete_product(int(cnv_id))
                    self.stdout.write(f"       đã xóa product test id={cnv_id}")
                except CnvError as exc:
                    self.stdout.write(f"       KHÔNG xóa được product test id={cnv_id}: {exc}")
            # Dừng ở biến thể đầu tiên thành công — đủ kết luận.
            return
        self.stdout.write(self.style.ERROR("Mọi biến thể đều bị từ chối — gửi output này để phân tích tiếp."))

    # -- phone --------------------------------------------------------------

    def _probe_phone(self, client, phone: str):
        for param in ("phone", "query", "search", "q"):
            try:
                data = client.list_customers({param: phone, "limit": 50})
            except CnvError as exc:
                self.stdout.write(f"[ERR ] param '{param}': {exc}")
                continue
            customers = data.get("customers") or []
            matched = [
                c for c in customers
                if phone[-9:] in "".join(ch for ch in str(c.get("phone") or "") if ch.isdigit())
            ]
            self.stdout.write(
                f"[INFO] param '{param}': trả {len(customers)} contact, khớp SĐT: "
                f"{[(c.get('id'), c.get('phone')) for c in matched] or 'không'}"
            )
