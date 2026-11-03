# ERP Master Plan — Tiến hóa CRM → ERP trên Modular Monolith

> Tài liệu nền (master plan) cho định hướng đưa hệ thống CRM hiện tại thành ERP
> (mô hình tham chiếu: Odoo). Đây là tài liệu **kiến trúc + lộ trình**, không phải
> spec triển khai chi tiết. Mỗi module ERP sẽ có spec riêng, dẫn chiếu về tài liệu này.
>
> - Ngày tạo: 2026-06-11
> - Chuẩn kiến trúc từng app: `docs/architecture-standard.md` (BẮT BUỘC tuân thủ)
> - App tham chiếu DDD: `apps.orders`
> - Nguồn lực giả định: 1–2 dev → roadmap tối ưu cho đội nhỏ.

---

## 1. Bối cảnh & Quyết định chiến lược

### 1.1 Hiện trạng
- **Backend:** Django 6 **monolith** + DRF + Celery + Channels (websocket) + Redis +
  PostgreSQL. App `orders` đã theo DDD-lite (`domain/application/infrastructure/transport/contracts`).
- **Frontend:** React 18 **SPA (Vite)** + TanStack Query + ag-grid + shadcn + react-router.
  Cấu trúc routing **phẳng**: ~21 `AppSection` ngang hàng trong một sidebar.
- **Nghiệp vụ đang có:** vận đơn (orders/dispatch), quản lý ads (google/facebook),
  kho (warehouses), bảo hành (warranty), khách hàng, tích hợp KiotViet, chat (smax/zalo).

### 1.2 Các quyết định đã chốt
| # | Quyết định | Lý do tóm tắt |
|---|---|---|
| D1 | **Build ERP trên hệ thống hiện tại**, không rewrite | Giữ tài sản nghiệp vụ VN đặc thù; rủi ro thấp. |
| D2 | **Modular Monolith**, KHÔNG microservices ở giai đoạn này | ERP cần ACID xuyên module; 1–2 dev không kham nổi chi phí vận hành microservices; có thể tách service sau (strangler). |
| D3 | **Giữ React SPA**, không rewrite NextJS | ERP nội bộ không cần SEO/SSR; chỉ chuẩn hóa app-shell. |
| D4 | **Build có chọn lọc** theo đòn bẩy (xem §5) | 1–2 dev không thể build đầy đủ Kho+HR+Kế toán chất lượng Odoo trong 12 tháng. |
| D5 | **App-shell kiểu Odoo**: App Launcher → module → section | Tránh sidebar phình khi số module tăng; UI co giãn theo module. |
| D6 | **URL nested** theo module (`/inventory/warehouses`) | Phản ánh ranh giới module ở tầng URL; gom code rõ ràng. |

> **Ghi chú về CLAUDE.md:** định hướng "microservices + NextJS + Golang" trong CLAUDE.md
> là **mục tiêu xa, có điều kiện**, không phải nguyên tắc của giai đoạn hiện tại.
> Cần cập nhật CLAUDE.md để phản ánh D2/D3 nhằm tránh mâu thuẫn định hướng.

### 1.3 Vì sao monolith là ĐÚNG cho ERP
- Odoo, ERPNext, SAP Business One đều là monolith — ERP là trường hợp kinh điển monolith thắng.
- Giao dịch ERP xuyên domain cần nhất quán tuyệt đối (bán → trừ tồn → công nợ → bút toán)
  trong **một transaction**. 1 Django + 1 PostgreSQL cho ACID miễn phí (`transaction.atomic()`).
  Microservices buộc phải Saga/outbox/eventual-consistency — ác mộng cho kế toán & kiểm toán.
- Scale bằng công cụ đã có: read replica (báo cáo), Celery (việc nặng/async),
  nhiều process app sau load balancer. Chưa cần xé nhỏ.

---

## 2. Nguyên tắc Modular Monolith (tầng còn thiếu so với architecture-standard.md)

`architecture-standard.md` quy định chuẩn **bên trong một app**. Master plan này bổ sung
chuẩn **giữa các app/module** — điều kiện để monolith không thành "big ball of mud".

### 2.1 Module = Bounded Context
Mỗi module ERP là một bounded context, sở hữu dữ liệu và logic của nó. Một hoặc vài Django
app gộp thành một module nghiệp vụ.

### 2.2 Luật ranh giới (CỨNG)
1. **Cấm import ORM chéo module.** Module `accounting` KHÔNG được
   `from apps.inventory.models import ...`. Mọi truy cập dữ liệu của module khác phải qua
   **service interface** khai báo trong `contracts/` của module sở hữu.
2. **Mỗi module sở hữu bảng của nó.** Không module nào ghi thẳng vào bảng của module khác.
3. **Giao tiếp đồng bộ** qua application service (gọi hàm Python, cùng process — vẫn rẻ và ACID).
4. **Giao tiếp bất đồng bộ** qua **domain event** (in-process event bus / `docs/events`),
   dùng cho side-effect không cần đồng bộ (ví dụ: `OrderConfirmed` → gợi ý tạo phiếu xuất kho).
5. **Một schema Postgres cho mỗi module** (tùy chọn, khuyến nghị): `inventory.*`,
   `accounting.*`, `hr.*` trong **cùng một database** → vẫn ACID, nhưng ranh giới vật lý rõ,
   dễ tách service sau này. Bảng dùng chung ở schema `public`/`shared`.

### 2.3 Kỷ luật dữ liệu cho nghiệp vụ tài chính/kho
- Mọi nghiệp vụ tài chính/tồn kho bọc trong `transaction.atomic()`.
- **Sổ cái & phiếu kho ghi append-only / immutable** (không sửa bút toán đã ghi; sai thì
  ghi bút toán đảo). Chuẩn kế toán, tránh tranh chấp khóa, dễ audit.
- Index chuẩn, tránh N+1 (đã là yêu cầu trong CLAUDE.md / architecture-standard.md).

### 2.4 Lộ trình tách service (chỉ khi cần)
Tách một module thành service riêng CHỈ khi: (a) module có đặc tính tải hoàn toàn khác
(vd ingest realtime lớn), hoặc (b) tổ chức > 30–40 dev chia nhiều team dẫm chân nhau.
Vì đã làm modular đúng, việc tách là cơ học (strangler pattern), không phải đập đi xây lại.

---

## 3. Bản đồ Module (Backend Bounded Contexts)

| Module | Django apps (hiện/mới) | Trạng thái | Ghi chú |
|---|---|---|---|
| **sales** (Bán hàng/Vận đơn) | orders, dispatch | Có | Lõi DDD tốt nhất, dùng làm mẫu. |
| **crm** (Khách hàng) | customers | Có | Nâng dần lên pipeline/cơ hội sau. |
| **inventory** (Kho & Sản phẩm) | warehouses, products, kiotviet | Có (rời rạc) | **Ưu tiên 1** — gom & chuẩn hóa thành module xương sống. |
| **purchasing** (Mua hàng) | *mới* | Chưa | Đi cùng inventory (nhập kho ↔ đơn mua). |
| **marketing** (Ads/Kênh) | facebook_ads, google_ads, channels_core, smax, zalo_bridge | Có | Đặc tính tải khác → ứng viên tách worker sớm nhất. |
| **accounting** (Kế toán) | *mới* | Chưa | GL mỏng + xuất; KHÔNG tự làm phần mềm kế toán pháp định. |
| **hr** (Nhân sự) | *mới (mỏng)* | Chưa | Tích hợp Lark; chỉ build phần Lark thiếu (lương/định mức). |
| **warranty** (Bảo hành) | warranty | Có | Giữ nguyên, gắn vào sales/crm. |
| **platform/shared** | common, users, audit, cron_jobs, internal | Có | Hạ tầng dùng chung (auth, RBAC, audit, http_client). |

> Quy ước đặt tên app mới: gom theo module dưới một prefix rõ ràng khi hợp lý
> (ví dụ `apps/inventory/`, `apps/purchasing/`, `apps/accounting/`, `apps/hr/`),
> mỗi app theo đúng layout DDD trong `architecture-standard.md`.

---

## 4. Frontend — App-Shell kiểu Odoo

### 4.1 Mô hình 2 tầng (thay cho 1 tầng phẳng hiện tại)
```
Tầng 1: App Launcher (homepage /)   ← lưới icon module user có quyền
   │ click 1 app
   ▼
Tầng 2: Trong module                 ← sidebar CHỈ section của module đó
        + nút ⊞ (app switcher) ở topnav để nhảy module khác
```

### 4.2 Bản đồ App (gom 21 section hiện có vào ~6 app + chỗ cho module ERP mới)
| App (icon) | Section gom vào | URL gốc |
|---|---|---|
| 🛒 Bán hàng/Vận đơn | orders, landing-orders, koc-orders | `/sales/...` |
| 👥 CRM/Khách hàng | customers, customer-report, loyal-customers, resale-tracking | `/crm/...` |
| 📦 Kho & Sản phẩm | warehouses, products, product-categories, kiotviet | `/inventory/...` |
| 📣 Marketing/Ads | facebook-ads, google-ads, ads-report, channels | `/marketing/...` |
| 📊 Báo cáo | dashboard | `/reports/...` |
| ⚙️ Quản trị | users, departments, audit, cron-jobs, sync | `/settings/...` |
| 🧾 Kế toán *(mới)* | (sau) | `/accounting/...` |
| 🛍️ Mua hàng *(mới)* | (sau) | `/purchasing/...` |
| 🧑‍💼 Nhân sự *(mới)* | (sau) | `/hr/...` |

Module ERP mới = thêm một icon app, **không đụng** app cũ.

### 4.3 Triển khai (incremental, không đập router)
1. **`app/modules.ts`** — registry: `{ key, label, icon, basePath, sections[] }`. Nguồn
   chân lý duy nhất cho mapping module ↔ section ↔ icon.
2. **Trang `/` → `AppLauncher`** (thay `DefaultRedirect`): render lưới icon các module mà
   user **có quyền** (module hiện nếu user có ≥1 section trong đó — tái dùng `allowed_sections`).
3. **`Sidebar.tsx` → contextual**: suy ra module từ section active, chỉ render section của
   module đó.
4. **`TopNav.tsx`**: thêm nút **⊞ app switcher** mở App Launcher / chuyển module.
5. **URL nested (D6):** đổi `SECTION_TO_PATH` sang dạng `/<module>/<section>`. Giữ
   **redirect 301 client-side** từ path phẳng cũ → path mới để không vỡ bookmark/link.
   Cập nhật `lib/routes.ts` (`SECTION_TO_PATH`, `sectionFromPath`, `pathFromSection`).
6. **Permission không đổi**: vẫn dựa trên `allowed_sections`; module chỉ là lớp trình bày.

> Frontend tuân thủ feature-slice trong `architecture-standard.md`:
> `src/features/<module>/...`. App-shell (launcher, sidebar contextual, switcher) đặt ở
> `src/app/` vì là hạ tầng khung dùng chung.

---

## 5. Chiến lược Build-vs-Buy theo Module (D4)

| Domain | Hướng | Lý do |
|---|---|---|
| **Kho/Mua hàng/Tồn** | **BUILD** (ưu tiên 1) | Đã có warehouses + kiotviet + orders; đòn bẩy cao nhất, gắn chặt data hiện có, là lợi thế riêng. |
| **Nhân sự/Chấm công** | **TÍCH HỢP Lark + build mỏng** | Lark đã làm HR/phòng ban; đồng bộ qua Lark API, chỉ build phần lương/định mức nếu Lark thiếu. |
| **Kế toán/Tài chính** | **GL mỏng + tích hợp/xuất** | Kế toán pháp định (thuế, BCTC) nặng & rủi ro pháp lý; build sổ cái + công nợ mức quản trị, xuất sang phần mềm kế toán VN (MISA…). KHÔNG tự làm phần mềm kế toán pháp định. |

Triết lý: **build lõi khác biệt, tích hợp phần chuẩn hóa** — cách đội nhỏ sống sót.

---

## 6. Roadmap theo Phase (tối ưu cho 1–2 dev)

> Mỗi phase chốt bằng một spec module riêng trước khi code. Cập nhật tiến độ ở §7.

### Phase 0 — Nền tảng (làm trước, bắt buộc)
- [ ] Cập nhật CLAUDE.md phản ánh D2/D3 (modular monolith, giữ SPA).
- [ ] Bổ sung mục "Luật ranh giới module" vào `architecture-standard.md` (tham chiếu §2).
- [ ] FE: app-shell kiểu Odoo (modules.ts + AppLauncher + Sidebar contextual + ⊞ switcher
      + nested URL + redirect tương thích). *(Spec riêng: `docs/erp-app-shell.md`)*
- [ ] BE: dựng in-process event bus chuẩn (xem `docs/events`) làm hạ tầng giao tiếp module.

### Phase 1 — Inventory & Purchasing (xương sống ERP)
- [ ] Gom warehouses/products/kiotviet thành module `inventory` chuẩn DDD.
- [ ] Tồn kho đa kho + sổ kho immutable (nhập/xuất/chuyển/điều chỉnh).
- [ ] Module `purchasing`: NCC, đơn mua, nhập kho ↔ công nợ phải trả.
- [ ] Event: `OrderConfirmed` → đề xuất phiếu xuất; `GoodsReceived` → cập nhật tồn + giá vốn.
- [ ] *(Spec riêng: `docs/erp-inventory.md`, `docs/erp-purchasing.md`)*

### Phase 2 — Accounting (GL mỏng)
- [ ] Sổ cái double-entry (immutable), chart of accounts cơ bản.
- [ ] Công nợ phải thu (từ sales) / phải trả (từ purchasing) tự sinh bút toán.
- [ ] Xuất dữ liệu sang phần mềm kế toán pháp định.
- [ ] *(Spec riêng: `docs/erp-accounting.md`)*

### Phase 3 — HR (tích hợp Lark)
- [ ] Đồng bộ nhân sự/phòng ban từ Lark.
- [ ] Chấm công/lương mức cơ bản (chỉ phần Lark thiếu).
- [ ] *(Spec riêng: `docs/erp-hr.md`)*

### Phase ∞ — Hoàn thiện
- [ ] CRM nâng cao (pipeline/cơ hội), báo cáo hợp nhất, BI.

---

## 7. Theo dõi tiến độ

> Cập nhật sau mỗi sprint: đã làm gì, chưa làm gì, vướng gì.

| Sprint | Phase | Phạm vi | Trạng thái | Ghi chú |
|---|---|---|---|---|
| 1 | 0 | App-shell kiểu Odoo (FE) | ✅ Xong (2026-06-11) | Branch `feat/erp-app-shell`; 7 commit; 169/169 test xanh; final review APPROVE. Chi tiết: `docs/erp-app-shell.md` §10. |
| 2 | 0 | Nền tảng BE: CLAUDE.md (D2/D3, local) + luật ranh giới module + event bus in-process | ✅ Xong (2026-06-11) | Branch `feat/erp-be-foundation`; `apps/common/events.py` (7/7 test, ruff sạch); `architecture-standard.md` + `docs/events/README.md`. → Phase 0 hoàn tất. |
| 3 | 1 | Inventory MVP đầy đủ (Sprint 1.1→1.4) — sổ kho immutable, tồn, phiếu nhập/xuất/điều chỉnh/chuyển kho, tự trừ kho khi confirm đơn | ✅ Xong (2026-06-11) | Branch `feat/erp-inventory`; app `apps/inventory` (53 BE test) + FE (section Tồn kho/Phiếu kho, chọn kho trên đơn + cảnh báo tồn âm). Event bus có consumer thật (`StockWentNegative`). Spec `docs/erp-inventory.md`. Migration chưa chạy prod. |

---

## 8. Rủi ro & Nguyên tắc vận hành
- **R1 — Monolith thành ball-of-mud:** kiểm soát bằng luật ranh giới §2 + review mỗi PR
  (cấm import ORM chéo module). *Đây là rủi ro số 1.*
- **R2 — Đội nhỏ ôm đồm:** tuân thủ build-vs-buy §5; ưu tiên tích hợp hơn tự build phần chuẩn.
- **R3 — Kế toán pháp lý:** không tự làm phần mềm kế toán pháp định; chỉ GL quản trị + xuất.
- **R4 — Migration dữ liệu:** mỗi module có migration + backup trước khi chạy prod
  (theo runbook hiện có).
- **Nguyên tắc:** mỗi module → spec .md riêng → chia sprint → cập nhật §7 sau mỗi sprint.
