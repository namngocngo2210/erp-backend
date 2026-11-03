# Chuẩn kiến trúc — Backend & Frontend

> Tài liệu quy ước bắt buộc cho mọi app backend và feature frontend mới.
> Code hiện có được đưa dần về chuẩn này theo các sprint trong
> `docs/refactor-clean-architecture.md`. App tham chiếu: **`apps.orders`**.

## 0. Nguyên tắc chung (SOLID)

- **S — Single Responsibility**: mỗi file/lớp một lý do để thay đổi. Trần mềm: file
  > 400 dòng (frontend) / > 500 dòng (backend) là tín hiệu phải tách.
- **O — Open/Closed**: thêm hành vi bằng cách mở rộng (strategy, mapping, DI), không sửa
  lõi. Tránh `switch`/`if` lặp lại cùng một phân loại ở nhiều nơi.
- **L — Liskov**: dùng interface/Protocol; mọi implementation thay thế được nhau.
- **I — Interface Segregation**: interface nhỏ, theo use-case; không "god interface".
- **D — Dependency Inversion**: business logic phụ thuộc abstraction, không phụ thuộc
  framework/ORM/HTTP cụ thể.

---

## 1. Backend — Clean Architecture cho 1 app

Cấu trúc chuẩn (lấy `apps/orders` làm mẫu):

```
apps/<feature>/
├── domain/              # Logic nghiệp vụ thuần — KHÔNG import Django models/ORM
│   └── ...              # dataclass, value object, pure function, business rule
├── application/         # Use-case / orchestration
│   └── services/        # *Service: điều phối domain + repository, không chứa HTTP
├── contracts/           # Interface (Protocol/ABC), event contract, DTO biên
├── infrastructure/      # I/O: external client, celery task, repository impl phức tạp
├── transport/           # Lớp HTTP (framework)
│   ├── views/           # DRF views — mỏng: parse → gọi service → trả response
│   └── serializers/     # DRF serializers
├── repositories.py      # Truy cập dữ liệu (kế thừa apps.common.BaseRepository)
├── models.py            # ORM models (chỉ định nghĩa schema + thuộc tính dữ liệu)
├── migrations/
└── tests/
```

### Quy tắc phân lớp (chiều phụ thuộc: ngoài → trong)

```
transport ──▶ application ──▶ domain
     │             │
     └────▶ infrastructure ◀─┘   (qua contracts/interface)
```

- **transport** (views): KHÔNG chứa business logic. Chỉ: xác thực input (serializer),
  parse pagination (`apps.common.pagination`), gọi service, map kết quả → `Response`.
- **application/services**: orchestrator **stateless**. Nhận repository qua tham số/DI,
  gọi domain. Không gọi `requests`/ORM trực tiếp khi có thể đẩy xuống repository/infra.
- **domain**: thuần Python. Không `import django`. Dễ test, không cần DB.
- **infrastructure**: nơi duy nhất được phép gọi mạng/file/celery. Dùng
  `apps.common.http_client.HttpClient` cho mọi outbound HTTP.
- **repositories**: kế thừa `apps.common.repository.BaseRepository[Model]`, soft-delete
  qua `deleted_at`. Tránh N+1 (`select_related`/`prefetch_related`), pagination qua
  `BaseRepository.paginate`.

### Shared (`apps/common/`)
Hạ tầng dùng chung, không chứa logic của app cụ thể:
- `pagination.py` — `parse_required_pagination(request, max_limit=100)`.
- `http_client.py` — `HttpClient` (timeout + retry/backoff).
- `repository.py` — `BaseRepository` (soft-delete-aware CRUD + paginate).

### Quy ước code
- Python ≥ 3.12, `ruff`, type hint đầy đủ, `from __future__ import annotations`.
- Service/Repository ưu tiên `@staticmethod`/`@classmethod` khi không có state.
- Mỗi app PHẢI có `tests/`; tối thiểu unit test cho service + repository.

### Luật ranh giới giữa các module (Modular Monolith)

> Chuẩn ở mục 1 quy định kiến trúc **bên trong** một app. Mục này quy định ranh giới
> **giữa** các app/module — điều kiện để monolith không thành "big ball of mud" và dễ
> tách microservice sau này (xem `docs/erp-master-plan.md` §2). Một module nghiệp vụ
> (bounded context) gồm một hoặc vài app (vd `inventory`, `purchasing`, `accounting`).

1. **CẤM import ORM chéo module.** App `accounting` KHÔNG được
   `from apps.inventory.models import ...`. Mọi truy cập dữ liệu của module khác phải đi
   qua **application service** mà module sở hữu công bố ở `contracts/` (interface/Protocol).
2. **Mỗi module sở hữu bảng của nó.** Không module nào ghi thẳng vào bảng của module khác.
3. **Giao tiếp đồng bộ** giữa module = gọi application service (cùng process → vẫn ACID
   trong một `transaction.atomic()`). KHÔNG gọi xuyên qua HTTP nội bộ.
4. **Giao tiếp bất đồng bộ / side-effect** = phát **domain event** qua event bus dùng chung
   `apps/common/events.py` (`publish`/`subscribe`); handler có thể đẩy việc nặng sang Celery.
   Dùng cho phản ứng không cần đồng bộ (vd `OrderConfirmed` → gợi ý tạo phiếu xuất kho).
   Event contract đặt ở `contracts/` của module phát (mẫu: `apps/orders/contracts/order_events.py`).
5. **Kỷ luật dữ liệu tài chính/kho:** nghiệp vụ bọc trong `transaction.atomic()`; sổ cái &
   phiếu kho ghi **append-only/immutable** (sai thì ghi bút toán đảo, không sửa bản ghi cũ).

**Definition of Done bổ sung cho PR đụng nhiều module:**
- [ ] Không có `import` model/ORM của module khác (chỉ qua service ở `contracts/`).
- [ ] Side-effect chéo module đi qua event bus, không gọi trực tiếp internal của module kia.

---

## 2. Frontend — Feature-Slice

Mỗi domain là một "slice" tự chứa dưới `src/features/<domain>/`:

```
src/features/<domain>/
├── components/      # Component thuộc riêng domain (form, dialog, card...)
├── pages/           # Page container của domain (data + orchestration)
├── queries.ts       # TanStack Query hooks (useX, mutation) + query keys
├── types.ts         # Type của domain (Row, Payload, Option...)
├── hooks/           # Custom hook tái sử dụng trong domain
└── __tests__/
```

Tách bạch với hạ tầng dùng chung:
```
src/lib/
├── api/             # API client thuần (fetchJson) — không chứa React
├── format.ts        # formatCurrency / formatNumber / date  (DÙNG CHUNG, không tự định nghĩa lại)
├── utils.ts         # cn, formatAddress...
└── icons/
src/components/ui/   # shadcn/ui — KHÔNG sửa lõi, chỉ thêm mới
```

### Quy tắc
- **Không** xé một domain ra cả `features/`, `components/`, `pages/`. Page của domain nằm
  trong `features/<domain>/pages/`. `src/components/` chỉ giữ component **dùng chung** thật sự.
- **Data fetching** chỉ qua TanStack Query trong `queries.ts` của feature; component nhận
  data qua hook, không tự `fetch`.
- **Container vs presentational**: page = container (state + query); UI con là presentational
  nhận props. Tách khi component > 400 dòng.
- **Format**: luôn import từ `@/lib/format`. Cấm copy `formatCurrency`/`formatMoney` mới.
- **Type safety**: cấm `any` mới; lỗi dùng `catch (e: unknown)` + type guard. Type domain
  đặt trong `types.ts`, không inline rải rác.
- TypeScript, shadcn/ui, light theme xanh da trời, `lucide-react`.

---

## 3. Definition of Done (mỗi PR refactor)
- [ ] Không thêm file vượt trần dòng (400 FE / 500 BE) mà không có lý do ghi chú.
- [ ] Không thêm logic trùng — tái dùng `apps/common` / `src/lib`.
- [ ] Có unit test cho phần logic tách ra.
- [ ] `tsc --noEmit` xanh (FE); test suite xanh; không thêm eslint error.
- [ ] Hành vi không đổi (trừ khi chủ đích, có ghi chú).
