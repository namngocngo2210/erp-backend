"""Quét danh sách tài khoản dưới MCC.

Bug được sửa: `WHERE customer_client.level <= 1` chỉ lấy con TRỰC TIẾP của MCC nên
mọi tài khoản nằm dưới một MCC con đều vô hình — báo cáo vẫn ra số, chỉ là thiếu tiền
của những tài khoản không ai biết là tồn tại.
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from apps.google_ads import services
from apps.google_ads.models import GoogleAdsAccount, GoogleAdsConfig


def _row(customer_id, name="Acc", *, manager=False, level=1, status="ENABLED"):
    """Giả một dòng GAQL. `status` truyền chuỗi cho gọn; test riêng phủ dạng enum."""
    return SimpleNamespace(
        customer_client=SimpleNamespace(
            id=customer_id,
            descriptive_name=name,
            currency_code="VND",
            status=status,
            manager=manager,
            level=level,
        )
    )


class SyncAccountsTreeTests(TestCase):
    def setUp(self):
        self.config = GoogleAdsConfig.objects.create(
            name="MCC", developer_token="d", client_id="c", client_secret="s",
            refresh_token="r", login_customer_id="1000000000",
        )

    def _sync(self, rows):
        with patch.object(services, "_build_client", return_value=object()), \
             patch.object(services, "_search", return_value=rows) as mock_search:
            result = services.sync_accounts_for_config(self.config)
        return result, mock_search

    def test_takes_accounts_at_every_depth(self):
        rows = [
            _row("1000000000", "MCC gốc", manager=True, level=0),
            _row("2000000000", "MCC con", manager=True, level=1),
            _row("3000000000", "TKQC dưới MCC con", level=2),
            _row("4000000000", "TKQC dưới MCC con con", level=3),
        ]
        result, mock_search = self._sync(rows)

        # Truy vấn KHÔNG được giới hạn độ sâu nữa.
        query = mock_search.call_args[0][2]
        self.assertNotIn("level", query.split("WHERE")[-1] if "WHERE" in query else "")

        # Chỉ 2 tài khoản chạy ads được lưu; 2 MCC bị loại.
        self.assertEqual(result["created"], 2)
        self.assertEqual(
            sorted(GoogleAdsAccount.objects.values_list("customer_id", flat=True)),
            ["3000000000", "4000000000"],
        )

    def test_same_customer_under_two_managers_stored_once(self):
        rows = [_row("3000000000", "TKQC", level=2), _row("3000000000", "TKQC", level=3)]
        self._sync(rows)
        self.assertEqual(GoogleAdsAccount.objects.filter(customer_id="3000000000").count(), 1)

    def test_cancelled_customer_marked_removed(self):
        rows = [_row("3000000000", "TKQC đã đóng", level=2, status="CANCELED")]
        self._sync(rows)
        account = GoogleAdsAccount.objects.get(customer_id="3000000000")
        self.assertEqual(account.status, GoogleAdsAccount.STATUS_REMOVED)

    def test_reads_proto_enum_status(self):
        # Thư viện google-ads trả enum proto-plus, không phải chuỗi.
        rows = [_row("3000000000", level=2, status=SimpleNamespace(name="ENABLED"))]
        self._sync(rows)
        account = GoogleAdsAccount.objects.get(customer_id="3000000000")
        self.assertEqual(account.status, GoogleAdsAccount.STATUS_ACTIVE)

    def test_account_missing_from_tree_marked_removed(self):
        GoogleAdsAccount.objects.create(
            config=self.config, name="Cũ", customer_id="9000000000",
            status=GoogleAdsAccount.STATUS_ACTIVE,
        )
        result, _ = self._sync([_row("3000000000", level=2)])
        self.assertEqual(result["removed"], 1)
        self.assertEqual(
            GoogleAdsAccount.objects.get(customer_id="9000000000").status,
            GoogleAdsAccount.STATUS_REMOVED,
        )

    def test_unrecognised_status_stays_active(self):
        # Giá trị lạ KHÔNG được làm tài khoản biến mất khỏi lượt đồng bộ chi phí.
        rows = [_row("3000000000", level=2, status="TRẠNG_THÁI_LẠ")]
        self._sync(rows)
        account = GoogleAdsAccount.objects.get(customer_id="3000000000")
        self.assertEqual(account.status, GoogleAdsAccount.STATUS_ACTIVE)

    def test_suspended_customer_marked_removed(self):
        rows = [_row("3000000000", level=2, status="SUSPENDED")]
        self._sync(rows)
        account = GoogleAdsAccount.objects.get(customer_id="3000000000")
        self.assertEqual(account.status, GoogleAdsAccount.STATUS_REMOVED)
