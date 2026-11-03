import hashlib
import os
import random
from datetime import datetime, timedelta, UTC

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.audit.models import AuditLog
from apps.channels_core.models import Channel, ChannelApiToken, UserChannel
from apps.kiotviet.models import KiotVietChannel
from apps.customers.models import Customer
from apps.orders.models import Order, OrderItem, OrderKiotVietJob
from apps.catalog.models import Product
from apps.orders.models import OrderStatus, OrderType, OrderKiotVietJobStatus


class Command(BaseCommand):
    help = "Seed dữ liệu ban đầu cho CRM"

    def handle(self, *args, **options):
        # Seed data (kèm mật khẩu mặc định) là dev-only. Chạy nhầm trên prod
        # từng reset admin về mật khẩu yếu — chặn cứng tại đây (audit 2026-07).
        if settings.IS_PRODUCTION:
            raise CommandError(
                "seed_initial_data is dev-only; refusing to run with APP_ENV=production"
            )

        User = get_user_model()

        # Mật khẩu seed đọc từ env; fallback giá trị cũ CHỈ cho dev/local
        # (production đã bị chặn ở trên).
        admin_password = os.environ.get("SEED_ADMIN_PASSWORD") or "admin123"
        staff_password = os.environ.get("SEED_STAFF_PASSWORD") or "staff123"

        admin, admin_created = User.objects.get_or_create(
            username="admin",
            defaults={"is_superuser": True, "is_staff": True},
        )
        # Chỉ set password khi TẠO MỚI — không bao giờ reset password user đã có.
        if admin_created:
            admin.set_password(admin_password)
            admin.save(update_fields=["password"])

        staff_usernames = ["staff1", "staff2", "staff3"]
        staff_users = []
        for username in staff_usernames:
            staff, staff_created = User.objects.get_or_create(
                username=username,
                defaults={"is_superuser": False, "is_staff": False},
            )
            if staff_created:
                staff.set_password(staff_password)
                staff.save(update_fields=["password"])
            staff_users.append(staff)

        channels = []
        for code in ["lp-a", "lp-b", "lp-c"]:
            channel, created = Channel.objects.get_or_create(
                code=code,
                defaults={
                    "name": code.upper(),
                    "channel_id": f"{code}-channel-id"
                }
            )
            # Update channel_id if not set
            if created and not channel.channel_id:
                channel.channel_id = f"{code}-channel-id"
                channel.save(update_fields=["channel_id"])
            channels.append(channel)

        UserChannel.objects.get_or_create(user=staff_users[0], channel=channels[0])
        UserChannel.objects.get_or_create(user=staff_users[0], channel=channels[1])
        UserChannel.objects.get_or_create(user=staff_users[1], channel=channels[1])
        UserChannel.objects.get_or_create(user=staff_users[2], channel=channels[2])

        token_hash = hashlib.sha256(b"landing-secret").hexdigest()
        ChannelApiToken.objects.get_or_create(channel=channels[0], token_hash=token_hash)

        # Seed KiotVietChannel data
        kiotviet_channels = []
        kiotviet_seed = [
            ("KiotViet Store 1", "KV001", "KV_RETAIL_1", "kv_client_1", "kv_secret_1"),
            ("KiotViet Store 2", "KV002", "KV_RETAIL_2", "kv_client_2", "kv_secret_2"),
            ("Sapo Store 1", "SP001", "SP_RETAIL_1", "sp_client_1", "sp_secret_1"),
        ]
        for name, branch_id, retailer, client_id, client_secret in kiotviet_seed:
            kiotviet_channel, created = KiotVietChannel.objects.get_or_create(
                branch_id=branch_id,
                defaults={
                    "name": name,
                    "retailer": retailer,
                    "client_id": client_id,
                    "client_secret": client_secret,
                }
            )
            # Update branch_ids if not set
            if created or not kiotviet_channel.branch_ids:
                kiotviet_channel.branch_ids = [branch_id]
                kiotviet_channel.save(update_fields=["branch_ids"])
            kiotviet_channels.append(kiotviet_channel)

        # Seed Customer data
        customers = []
        customer_seed = [
            ("Nguyễn Văn A", "0901234567", "0901234567", 1),
            ("Trần Thị B", "0902345678", "0902345678", 0),
            ("Lê Văn C", "0903456789", "0903456789", 1),
            ("Phạm Thị D", "0904567890", "0904567890", 0),
            ("Hoàng Văn E", "0905678901", "0905678901", 1),
            ("Ngô Thị F", "0906789012", "0906789012", 0),
            ("Vũ Văn G", "0907890123", "0907890123", 1),
            ("Đỗ Thị H", "0908901234", "0908901234", 0),
            ("Bùi Văn I", "0909012345", "0909012345", 1),
            ("Lý Thị K", "0900123456", "0900123456", 0),
        ]
        for full_name, phone_raw, phone_normalized, gender in customer_seed:
            customer, _ = Customer.objects.get_or_create(
                phone_normalized=phone_normalized,
                defaults={
                    "full_name": full_name,
                    "phone_raw": phone_raw,
                    "gender": gender,
                }
            )
            if customer.gender != gender:
                customer.gender = gender
                customer.save(update_fields=["gender"])
            customers.append(customer)

        # Seed Product data
        products = []
        product_seed = [
            ("Sản phẩm A", "SP-A-001", 100000, True),
            ("Sản phẩm B", "SP-B-002", 150000, True),
            ("Sản phẩm C", "SP-C-003", 200000, True),
            ("Sản phẩm D", "SP-D-004", 250000, False),
            ("Sản phẩm E", "SP-E-005", 300000, True),
            ("Sản phẩm F", "SP-F-006", 350000, False),
            ("Sản phẩm G", "SP-G-007", 400000, True),
            ("Sản phẩm H", "SP-H-008", 450000, True),
        ]
        for name, sku, price, is_active in product_seed:
            product, _ = Product.objects.get_or_create(
                sku=sku,
                defaults={
                    "name": name,
                    "price": price,
                    "is_active": is_active,
                }
            )
            products.append(product)

        # Seed Order data
        orders = []
        order_types = [OrderType.NORMAL, OrderType.NO_RETURN, OrderType.HAS_RETURN]

        base_time = datetime.now(UTC) - timedelta(days=7)

        order_seed_data = [
            # (channel, kiotviet_channel, customer, staff, status, order_type, total_amount, shipping_fee)
            (channels[0], kiotviet_channels[0], customers[0], staff_users[0], OrderStatus.NEW, OrderType.NORMAL, 500000, 30000),
            (channels[0], kiotviet_channels[0], customers[1], staff_users[0], OrderStatus.PENDING_PROCESSING, OrderType.NORMAL, 750000, 35000),
            (channels[1], kiotviet_channels[1], customers[2], staff_users[1], OrderStatus.NEW, OrderType.HAS_RETURN, 300000, 25000),
            (channels[1], kiotviet_channels[1], customers[3], staff_users[1], OrderStatus.CANCELLED, OrderType.NO_RETURN, 1200000, 45000),
            (channels[2], kiotviet_channels[2], customers[4], staff_users[2], OrderStatus.NEW, OrderType.NORMAL, 450000, 30000),
            (channels[0], kiotviet_channels[0], customers[5], None, OrderStatus.PENDING_PROCESSING, OrderType.NORMAL, 680000, 35000),
            (channels[1], None, customers[6], staff_users[1], OrderStatus.NEW, OrderType.HAS_RETURN, 920000, 40000),
            (channels[2], kiotviet_channels[2], customers[7], staff_users[2], OrderStatus.PENDING_PROCESSING, OrderType.NORMAL, 550000, 30000),
            (channels[0], kiotviet_channels[1], customers[8], staff_users[0], OrderStatus.NEW, OrderType.NORMAL, 380000, 25000),
            (channels[1], kiotviet_channels[0], customers[9], staff_users[1], OrderStatus.CANCELLED, OrderType.NO_RETURN, 1500000, 50000),
        ]

        for i, (channel, kiotviet_channel, customer, staff, status, order_type, total_amount, shipping_fee) in enumerate(order_seed_data):
            order, _ = Order.objects.get_or_create(
                external_order_id=f"EXT-ORDER-{i+1:04d}",
                defaults={
                    "channel": channel,
                    "kiotviet_channel": kiotviet_channel,
                    "customer": customer,
                    "staff": staff,
                    "status": status,
                    "order_type": order_type,
                    "total_amount": total_amount,
                    "shipping_fee": shipping_fee,
                    "name": customer.full_name,
                    "phone": customer.phone_raw,
                    "raw_address": "",  # Customer không có address
                    "quantity": random.randint(1, 5),
                    "channel_owner": channel.name,
                }
            )
            orders.append(order)

        # Seed OrderItem data
        order_items = []
        order_item_seed = [
            # (order, product, quantity, unit_price)
            (orders[0], products[0], 2, 100000),
            (orders[0], products[1], 1, 150000),
            (orders[1], products[2], 3, 200000),
            (orders[1], products[4], 1, 300000),
            (orders[2], products[0], 1, 100000),
            (orders[2], products[3], 1, 250000),
            (orders[3], products[5], 2, 350000),
            (orders[3], products[6], 1, 400000),
            (orders[4], products[1], 2, 150000),
            (orders[4], products[7], 1, 450000),
            (orders[5], products[2], 2, 200000),
            (orders[5], products[4], 1, 300000),
            (orders[6], products[0], 3, 100000),
            (orders[6], products[6], 1, 400000),
            (orders[7], products[1], 2, 150000),
            (orders[7], products[3], 1, 250000),
            (orders[8], products[4], 1, 300000),
            (orders[8], products[7], 1, 450000),
            (orders[9], products[2], 3, 200000),
            (orders[9], products[5], 1, 350000),
        ]

        for order, product, quantity, unit_price in order_item_seed:
            line_total = quantity * unit_price
            order_item, _ = OrderItem.objects.get_or_create(
                order=order,
                product=product,
                defaults={
                    "product_name": product.name,
                    "sku": product.sku,
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "line_total": line_total,
                }
            )
            order_items.append(order_item)

        # Seed OrderKiotVietJob data
        order_kiotviet_jobs = []
        base_time = datetime.now(UTC) - timedelta(days=7)

        job_seed = [
            # (order, action, status, requested_by, error_message, created_at)
            (orders[0], OrderKiotVietJob.ACTION_PUSH, OrderKiotVietJobStatus.SUCCESS, staff_users[0], "", base_time + timedelta(hours=1)),
            (orders[1], OrderKiotVietJob.ACTION_PUSH, OrderKiotVietJobStatus.SUCCESS, staff_users[0], "", base_time + timedelta(hours=2)),
            (orders[2], OrderKiotVietJob.ACTION_PUSH, OrderKiotVietJobStatus.FAILED, staff_users[1], "Connection timeout", base_time + timedelta(hours=3)),
            (orders[3], OrderKiotVietJob.ACTION_PUSH, OrderKiotVietJobStatus.SUCCESS, staff_users[1], "", base_time + timedelta(hours=4)),
            (orders[4], OrderKiotVietJob.ACTION_PUSH, OrderKiotVietJobStatus.PENDING, staff_users[2], "", base_time + timedelta(hours=5)),
            (orders[5], OrderKiotVietJob.ACTION_SYNC_SHIPPING, OrderKiotVietJobStatus.SUCCESS, None, "", base_time + timedelta(hours=6)),
            (orders[6], OrderKiotVietJob.ACTION_PUSH, OrderKiotVietJobStatus.FAILED, staff_users[1], "No kiotviet channel", base_time + timedelta(hours=7)),
            (orders[7], OrderKiotVietJob.ACTION_SYNC_STATUS, OrderKiotVietJobStatus.SUCCESS, staff_users[2], "", base_time + timedelta(hours=8)),
            (orders[8], OrderKiotVietJob.ACTION_PUSH, OrderKiotVietJobStatus.SUCCESS, staff_users[0], "", base_time + timedelta(hours=9)),
            (orders[9], OrderKiotVietJob.ACTION_SYNC_STATUS, OrderKiotVietJobStatus.PENDING, staff_users[1], "", base_time + timedelta(hours=10)),
        ]

        for order, action, status, requested_by, error_message, created_at in job_seed:
            order_kiotviet_job, _ = OrderKiotVietJob.objects.get_or_create(
                order=order,
                action=action,
                defaults={
                    "status": status,
                    "requested_by": requested_by,
                    "error_message": error_message,
                }
            )
            order_kiotviet_jobs.append(order_kiotviet_job)

        # Seed AuditLog data
        audit_logs = []
        audit_seed = [
            (staff_users[0], "CREATE", "USER", "admin", "POST", "127.0.0.1", base_time + timedelta(hours=1)),
            (staff_users[0], "CREATE", "USER", "staff1", "POST", "127.0.0.1", base_time + timedelta(hours=2)),
            (staff_users[0], "CREATE", "USER", "staff2", "POST", "127.0.0.1", base_time + timedelta(hours=3)),
            (staff_users[0], "CREATE", "USER", "staff3", "POST", "127.0.0.1", base_time + timedelta(hours=4)),
            (staff_users[0], "CREATE", "CHANNEL", "lp-a", "POST", "127.0.0.1", base_time + timedelta(hours=5)),
            (staff_users[0], "CREATE", "CHANNEL", "lp-b", "POST", "127.0.0.1", base_time + timedelta(hours=6)),
            (staff_users[0], "CREATE", "CHANNEL", "lp-c", "POST", "127.0.0.1", base_time + timedelta(hours=7)),
            (staff_users[0], "CREATE", "KIOTVIET_JOB", "KV001", "POST", "127.0.0.1", base_time + timedelta(hours=8)),
            (staff_users[0], "CREATE", "KIOTVIET_JOB", "KV002", "POST", "127.0.0.1", base_time + timedelta(hours=9)),
            (staff_users[0], "CREATE", "KIOTVIET_JOB", "SP001", "POST", "127.0.0.1", base_time + timedelta(hours=10)),
            (staff_users[0], "CREATE", "CUSTOMER", "Nguyễn Văn A", "POST", "127.0.0.1", base_time + timedelta(hours=11)),
            (staff_users[0], "CREATE", "PRODUCT", "SP-A-001", "POST", "127.0.0.1", base_time + timedelta(hours=12)),
            (staff_users[0], "CREATE", "ORDER", "EXT-ORDER-0001", "POST", "127.0.0.1", base_time + timedelta(hours=13)),
            (staff_users[1], "UPDATE", "ORDER", "EXT-ORDER-0002", "PUT", "127.0.0.1", base_time + timedelta(hours=14)),
            (staff_users[2], "DELETE", "ORDER", "EXT-ORDER-0004", "DELETE", "127.0.0.1", base_time + timedelta(hours=15)),
        ]
        for user, action_type, entity_type, entity_id, method, ip_address, timestamp in audit_seed:
            audit_log, _ = AuditLog.objects.get_or_create(
                user=user,
                action_type=action_type,
                entity_type=entity_type,
                entity_id=entity_id,
                timestamp=timestamp,
                defaults={
                    "method": method,
                    "ip_address": ip_address,
                }
            )
            audit_logs.append(audit_log)

        self.stdout.write(
            self.style.SUCCESS(
                "Seed thành công: admin + staff1|staff2|staff3 "
                "(password từ SEED_ADMIN_PASSWORD/SEED_STAFF_PASSWORD, chỉ set khi tạo mới)"
            )
        )
