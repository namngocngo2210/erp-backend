from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Customer
from apps.common.phone_utils import normalize_phone, phone_lookup_candidates
from apps.orders.crm_catalog import CUSTOMER_CLASSIFICATION_LABELS
from apps.orders.repositories import OrderRepository
from apps.users.phone_visibility import PhoneVisibility, phone_visibility_for

__all__ = [
    "CustomerPhoneMasker",
    "CustomerService",
    "customer_phone_masker",
    "normalize_phone",
    "phone_lookup_candidates",
    "normalize_name",
]



def normalize_name(name: str) -> str:
    """Viết hoa chữ cái đầu mỗi từ trong tên."""
    return " ".join(word.capitalize() for word in (name or "").split())


@dataclass(frozen=True)
class CustomerPhoneMasker:
    """Che SĐT trên màn hình cấp KHÁCH HÀNG (không phải cấp đơn).

    Khác `PhoneVisibility` ở chỗ "của mình" không đọc được từ một dòng: một khách có
    thể có đơn của nhiều sale, nên quyền xem số thật = có ÍT NHẤT một đơn do mình phụ
    trách (tập `owned_customer_ids`, dựng sẵn 1 query cho cả trang). Trang KH thân
    thiết truyền thêm `owner_id` = sale được gán chăm sóc khách đó.
    """

    visibility: PhoneVisibility
    owned_customer_ids: frozenset

    def apply(self, phone, customer_id, owner_id=None) -> str:
        if customer_id is not None and int(customer_id) in self.owned_customer_ids:
            return str(phone or "")
        return self.visibility.apply(phone, owner_id)


def customer_phone_masker(user, customer_ids) -> CustomerPhoneMasker:
    """Dựng masker cho ĐÚNG trang hiện tại (1 query, bỏ qua khi user xem được hết)."""
    visibility = phone_visibility_for(user)
    if visibility.can_view_full:
        return CustomerPhoneMasker(visibility=visibility, owned_customer_ids=frozenset())
    return CustomerPhoneMasker(
        visibility=visibility,
        owned_customer_ids=frozenset(
            OrderRepository.customer_ids_assigned_to(visibility.user_id, customer_ids)
        ),
    )




class CustomerService:
    @staticmethod
    def get_or_create_by_phone(phone: str, full_name: str = "", code_prefix: str = "KH") -> Customer:
        """
        Tìm customer theo số điện thoại đã chuẩn hóa.
        Nếu không tìm thấy, tạo mới customer với code mới.

        Args:
            phone: Số điện thoại thô từ landing page
            full_name: Tên khách hàng (nếu có)
            code_prefix: Tiền tố mã cho khách MỚI (vd "KH" hoặc "KOC"); khách cũ giữ nguyên mã

        Returns:
            Customer instance
        """
        phone_normalized = normalize_phone(phone)
        if not phone_normalized:
            raise ValueError("Invalid phone payload")
        lookup_candidates = phone_lookup_candidates(phone)

        # Tìm customer theo phone_normalized
        customer = Customer.objects.filter(phone_normalized__in=lookup_candidates, deleted_at__isnull=True).first()

        full_name = normalize_name(full_name)

        if customer:
            # Cập nhật full_name nếu có
            if full_name and not customer.full_name:
                customer.full_name = full_name
                customer.save(update_fields=['full_name', 'updated_at'])
            if customer.phone_normalized != phone_normalized:
                customer.phone_normalized = phone_normalized
                customer.save(update_fields=["phone_normalized", "updated_at"])
            return customer

        # Tạo customer mới; code sẽ được model chuẩn hóa thành <code_prefix><id>.
        new_customer = Customer(
            full_name=full_name,
            phone_raw=phone,
            phone_normalized=phone_normalized,
        )
        new_customer._code_prefix = code_prefix
        try:
            with transaction.atomic():
                new_customer.save()
        except IntegrityError:
            # Race: một request song song vừa tạo customer cùng phone → unique index
            # cust_phone_norm_active_uniq chặn INSERT. Re-fetch bản thắng cuộc.
            existing = Customer.objects.filter(
                phone_normalized__in=lookup_candidates, deleted_at__isnull=True
            ).first()
            if existing is not None:
                return existing
            raise
        return new_customer

    @staticmethod
    def get_by_code(code: str) -> Customer | None:
        """Tìm customer theo code"""
        return Customer.objects.filter(code=code, deleted_at__isnull=True).first()

    @staticmethod
    def create_customer(*, full_name: str = "", phone_raw: str = "", gender: int | None = None) -> Customer:
        return Customer.objects.create(
            full_name=normalize_name(full_name),
            phone_raw=(phone_raw or "").strip(),
            phone_normalized=normalize_phone(phone_raw or ""),
            gender=gender,
        )

    @staticmethod
    def update_customer(
        customer: Customer,
        *,
        full_name: str | None = None,
        phone_raw: str | None = None,
        gender: int | None = None,
        has_gender: bool = False,
    ) -> Customer:
        update_fields: list[str] = []

        if full_name is not None:
            customer.full_name = normalize_name(full_name)
            update_fields.append("full_name")

        if phone_raw is not None:
            phone_value = phone_raw.strip()
            customer.phone_raw = phone_value
            customer.phone_normalized = normalize_phone(phone_value)
            update_fields.extend(["phone_raw", "phone_normalized"])

        if has_gender:
            customer.gender = gender
            update_fields.append("gender")

        if update_fields:
            customer.save(update_fields=list(dict.fromkeys(update_fields + ["updated_at"])))

        return customer

    @staticmethod
    def delete_customer(customer: Customer):
        if customer.deleted_at is not None:
            return
        customer.deleted_at = timezone.now()
        customer.save(update_fields=["deleted_at", "updated_at"])

    @staticmethod
    def bulk_delete_customers(ids: list[int]) -> dict:
        customers = list(Customer.objects.filter(id__in=ids, deleted_at__isnull=True))
        if not customers:
            return {"deleted_count": 0, "protected_ids": []}
        now = timezone.now()
        deleted_count = Customer.objects.filter(id__in=[customer.id for customer in customers], deleted_at__isnull=True).update(
            deleted_at=now,
            updated_at=now,
        )
        return {"deleted_count": deleted_count, "protected_ids": []}

    @staticmethod
    def build_customer_order_aggregate(customer: Customer) -> dict:
        orders = list(OrderRepository.list_customer_orders(customer.id))
        current_classification_code = None
        for order in orders:
            assessment = getattr(order, "assessment", None)
            if assessment and assessment.customer_classification_code:
                current_classification_code = assessment.customer_classification_code
                break

        bombed_count = 0
        unreachable_count = 0
        delivered_count = 0
        in_delivery_count = 0
        for order in orders:
            assessment = getattr(order, "assessment", None)
            delivery_business_status_code = getattr(assessment, "delivery_business_status_code", None)
            sales_status_code = getattr(assessment, "sales_status_code", None)
            if delivery_business_status_code == "delivered_success":
                delivered_count += 1
            if delivery_business_status_code == "delivering":
                in_delivery_count += 1
            if delivery_business_status_code == "bombed":
                bombed_count += 1
            if sales_status_code in {
                "no_answer_1",
                "no_answer_2",
                "no_answer_3",
                "no_answer_4",
                "no_answer_5",
                "no_answer_6",
                "unreachable",
            }:
                unreachable_count += 1

        return {
            "total_orders": len(orders),
            "delivered_count": delivered_count,
            "in_delivery_count": in_delivery_count,
            "bombed_count": bombed_count,
            "unreachable_count": unreachable_count,
            "current_customer_classification_code": current_classification_code,
            "current_customer_classification_label": CUSTOMER_CLASSIFICATION_LABELS.get(current_classification_code),
        }

