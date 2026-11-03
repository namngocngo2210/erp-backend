import os
import sys
import django

# Setup Django environment
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

import pandas as pd
from django.db import transaction
from apps.customers.models import Customer
from apps.customers.services import normalize_phone, generate_customer_code

def import_customers_from_excel(excel_path: str):
    """
    Import customers từ file Excel vào database
    """
    # Đọc file Excel
    df = pd.read_excel(excel_path)

    print(f"Đã đọc {len(df)} dòng từ file Excel")
    print(f"Các cột: {df.columns.tolist()}")

    # Kiểm tra các cột cần thiết
    required_columns = ['code', 'name', 'phone_raw']
    missing_columns = [col for col in required_columns if col not in df.columns]
    if missing_columns:
        print(f"Lỗi: Thiếu các cột: {missing_columns}")
        return

    created_count = 0
    updated_count = 0
    skipped_count = 0

    with transaction.atomic():
        for idx, row in df.iterrows():
            code = str(row['code']).strip()
            full_name = str(row['name']).strip()
            phone_raw = str(row['phone_raw']).strip()

            if not phone_raw:
                print(f"Dòng {idx + 2}: Bỏ qua - thiếu phone_raw")
                skipped_count += 1
                continue

            # Chuẩn hóa số điện thoại
            phone_normalized = normalize_phone(phone_raw)

            # Kiểm tra customer đã tồn tại theo code
            existing_customer = Customer.objects.filter(code=code).first()

            if existing_customer:
                # Cập nhật thông tin nếu cần
                needs_update = False
                if existing_customer.full_name != full_name:
                    existing_customer.full_name = full_name
                    needs_update = True
                if existing_customer.phone_raw != phone_raw:
                    existing_customer.phone_raw = phone_raw
                    needs_update = True
                if existing_customer.phone_normalized != phone_normalized:
                    existing_customer.phone_normalized = phone_normalized
                    needs_update = True

                if needs_update:
                    existing_customer.save()
                    print(f"Cập nhật: {code} - {full_name}")
                    updated_count += 1
                else:
                    print(f"Bỏ qua (không thay đổi): {code} - {full_name}")
                    skipped_count += 1
            else:
                # Tạo customer mới
                customer = Customer.objects.create(
                    code=code,
                    full_name=full_name,
                    phone_raw=phone_raw,
                    phone_normalized=phone_normalized
                )
                print(f"Đã tạo: {customer.code} - {customer.full_name} - {customer.phone_normalized}")
                created_count += 1

    print("\n=== Tóm tắt ===")
    print(f"Tạo mới: {created_count}")
    print(f"Cập nhật: {updated_count}")
    print(f"Bỏ qua: {skipped_count}")
    print(f"Tổng cộng: {created_count + updated_count + skipped_count}")

if __name__ == '__main__':
    import_path = r'C:\Users\MYPC\Desktop\workspace\desi-order\customer.xlsx'
    import_customers_from_excel(import_path)
