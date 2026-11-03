# In-Process Domain Event Bus

Cơ chế giao tiếp **bất đồng bộ / side-effect** giữa các module trong Modular Monolith.
Module phát domain event; module khác đăng ký handler — **không import nội bộ của nhau**.

- Code: `backend/apps/common/events.py`
- Luật ranh giới module: `docs/architecture-standard.md` (§ "Luật ranh giới giữa các module")
- Định hướng: `docs/erp-master-plan.md` §2

## Vì sao
Module `A` cần phản ứng khi `B` xảy ra việc gì đó, nhưng `A` **không được** import model/ORM
của `B` (luật ranh giới). Event bus là điểm nối: `B` publish event (chỉ id/giá trị, không
phải ORM object), `A` subscribe. Đổi consumer không đụng producer.

## API
```python
from dataclasses import dataclass
from typing import ClassVar
from apps.common.events import DomainEvent, event_bus, subscribe

# 1) Định nghĩa event ở contracts/ của module PHÁT (immutable, chỉ dữ liệu thuần)
@dataclass(frozen=True)
class OrderConfirmed(DomainEvent):
    event_type: ClassVar[str] = "order.confirmed"
    order_id: int

# 2) Producer (application service của module orders) phát event
event_bus.publish_on_commit(OrderConfirmed(order_id=order.id))

# 3) Consumer (module inventory) đăng ký handler — thường ở app ready()
@subscribe("order.confirmed")
def suggest_stock_issue(event: OrderConfirmed) -> None:
    ...
```

## Ngữ nghĩa
- **Đồng bộ, in-process.** Handler chạy tuần tự theo thứ tự đăng ký.
- **Cô lập lỗi.** Handler ném exception → log + bỏ qua, KHÔNG vỡ producer hay handler khác.
  (best-effort; không dùng cho ràng buộc nghiệp vụ bắt buộc — cái đó gọi service trực tiếp,
  đồng bộ, trong `transaction.atomic()`.)
- **`publish` vs `publish_on_commit`:**
  - `publish_on_commit(event)` — **mặc định cho domain event**: chỉ phát sau khi transaction
    hiện tại commit, nên consumer thấy state đã ghi và rollback sẽ huỷ event.
  - `publish(event)` — phát ngay; dùng khi không nằm trong transaction hoặc cần chạy liền.
- **Việc nặng:** handler nên đẩy sang Celery thay vì xử lý đồng bộ dài.

## Quan hệ với `order-events.md`
`order-events.md` mô tả **contract payload** cho luồng realtime WebSocket (producer/consumer
qua `OrderEventContract`). Event bus ở đây là **kênh in-process** giữa các module backend —
hai thứ khác mục đích, có thể dùng cùng nhau (handler của bus có thể đẩy payload realtime).
