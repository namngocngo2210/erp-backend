"""Smoke test đấu nối Best Express (chạy TAY trên tài khoản env test).

    python manage.py best_smoke_test --account-id 1            # login + estimate
    python manage.py best_smoke_test --account-id 1 --create   # + tạo/trace/hủy đơn test

KHÔNG chạy trên account production.
Nếu lệnh gãy giữa chừng sau bước create, vận đơn test có thể còn sống phía Best —
hủy tay trên trang V9.
"""

import uuid

from django.core.management.base import BaseCommand, CommandError

from apps.best_express.application.services import shipment_service, tracking_service
from apps.best_express.infrastructure.best_client import ensure_token
from apps.best_express.models import BestExpressAccount, BestExpressEnvironment


class Command(BaseCommand):
    help = "Smoke test Best Express API (login → estimate → [create → trace → cancel])"

    def add_arguments(self, parser):
        parser.add_argument("--account-id", type=int, required=True)
        parser.add_argument(
            "--create",
            action="store_true",
            help="Tạo đơn test thật trên env test rồi hủy ngay",
        )

    def handle(self, *args, **options):
        account = BestExpressAccount.objects.filter(pk=options["account_id"]).first()
        if account is None:
            raise CommandError(f"Không tìm thấy account id={options['account_id']}")
        if account.environment != BestExpressEnvironment.TEST and options["create"]:
            raise CommandError("--create chỉ cho phép trên account environment=test")

        token = ensure_token(account, force_refresh=True)
        self.stdout.write(self.style.SUCCESS(f"[1/4] Login OK (token {token[:16]}…)"))

        fee = tracking_service.estimate_fee(
            account,
            {
                "ServiceId": 12491,
                "SourceCity": "Hà Nội",
                "SourceDistrict": "Quận Thanh Xuân",
                "SourceWard": "Phường Thượng Đình",
                "DestCity": "Hồ Chí Minh",
                "DestDistrict": "Tân Bình",
                "DestWard": "Phường 13",
                "Cod": 100000,
                "ProductPrice": 100000,
                "Weight": 500,
            },
        )
        self.stdout.write(
            self.style.SUCCESS(f"[2/4] EstimateFee OK (TotalFee={fee.get('TotalFee')})")
        )

        if not options["create"]:
            self.stdout.write("[3/4][4/4] Bỏ qua create/cancel (thêm --create để chạy)")
            return

        code = f"DESITEST{uuid.uuid4().hex[:12].upper()}"
        shipment = shipment_service.create_shipment(
            account,
            {
                "Code": code,
                "ProductName": "Đơn test DESI - không xử lý",
                "CollectAmount": 0,
                "ProductPrice": 0,
                "JourneyType": 1,
                "ServiceId": 12491,
                "PickupType": 2,
                "Weight": 500,
                "SourceCity": "Hà Nội",
                "SourceDistrict": "Quận Thanh Xuân",
                "SourceWard": "Phường Thượng Đình",
                "SourceAddress": "Đường Nguyễn Trãi",
                "SourceName": "DESI Test",
                "SourcePhoneNumber": "0900000000",
                "DestCity": "Hồ Chí Minh",
                "DestDistrict": "Tân Bình",
                "DestWard": "Phường 13",
                "DestAddress": "Cộng Hòa",
                "DestName": "DESI Test",
                "DestPhoneNumber": "0900000000",
                "ReturnCity": "Hà Nội",
                "ReturnDistrict": "Quận Thanh Xuân",
                "ReturnWard": "Phường Thượng Đình",
                "ReturnAddress": "Đường Nguyễn Trãi",
                "ReturnName": "DESI Test",
                "ReturnPhoneNumber": "0900000000",
                "Note": "Đơn test API. Không cần thực hiện hành động nào.",
            },
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"[3/4] CreateOrder OK (code_best={shipment.code_best}, route={shipment.route_code})"
            )
        )

        tracking_service.trace(account, [shipment.code_best])
        shipment_service.cancel_shipment(shipment)
        self.stdout.write(self.style.SUCCESS("[4/4] Trace + Cancel OK"))
