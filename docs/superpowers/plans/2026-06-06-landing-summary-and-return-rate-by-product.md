# Tab "Tổng hợp" (Landing) + "Nhóm 7 — Tỷ lệ hoàn theo SP" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thêm tab "Tổng hợp" làm tab đầu/mặc định trang Đơn hàng Landing (mọi đơn `order_source != ''` và `!= koc`, trừ đã hủy) và thêm "Nhóm 7 — Tỷ lệ hoàn theo sản phẩm" vào Báo cáo Sale.

**Architecture:** Tính năng 1 tái dùng endpoint `list_landing_orders` không-`sheet` (đã có sẵn logic `order_source_present` + loại `koc`), nên chỉ thêm 1 `FilterScope`/tab key + sửa ánh xạ tab ở frontend. Tính năng 2 thêm 1 aggregation backend gom theo `OrderItem.product` rồi render 1 card mới ở `DashboardPage`.

**Tech Stack:** Django + DRF (backend), React + TypeScript + Vite + Vitest + Recharts (frontend).

**Spec:** [docs/superpowers/specs/2026-06-06-landing-summary-tab-and-return-rate-by-product-design.md](../specs/2026-06-06-landing-summary-tab-and-return-rate-by-product-design.md)

---

## File Structure

**Tính năng 1 (frontend only):**
- Modify [frontend/src/features/orders/use-orders-data.ts](../../../frontend/src/features/orders/use-orders-data.ts#L359) — thêm `"landing-summary"` vào `FilterScope`.
- Modify [frontend/src/pages/OrdersPage.tsx](../../../frontend/src/pages/OrdersPage.tsx#L92) — thêm `"summary"` vào `LandingTabKey` + entry đầu `LANDING_SHEET_TABS`; sửa `activeTabKey` + điều kiện `SheetStatsRow`.
- Modify [frontend/src/app/orders-tab.ts](../../../frontend/src/app/orders-tab.ts#L18) — ánh xạ `summary`/mặc định → `landing-summary`.
- Create `frontend/src/app/__tests__/orders-tab.test.ts` — unit test ánh xạ scope.

**Tính năng 2 (backend + frontend):**
- Modify [backend/apps/orders/_repositories_reports.py](../../../backend/apps/orders/_repositories_reports.py#L319) — thêm `summary_return_rate_by_product`.
- Modify [backend/apps/orders/transport/views/order_views_impl/order_ingest_product_dashboard_view.py](../../../backend/apps/orders/transport/views/order_views_impl/order_ingest_product_dashboard_view.py#L573) — gọi + thêm key response.
- Modify [backend/apps/orders/tests/test_dashboard_summary_api.py](../../../backend/apps/orders/tests/test_dashboard_summary_api.py#L238) — test mới trong `DashboardSalePerformanceTests`.
- Modify [frontend/src/pages/DashboardPage.tsx](../../../frontend/src/pages/DashboardPage.tsx) — type + card Nhóm 7.

**Lệnh kiểm thử (lưu ý môi trường):**
- Frontend (chạy trong thư mục `frontend/`): `npx vitest run <file>` và `npx tsc --noEmit`.
- Backend: **pytest đang hỏng (BOM trong `pytest.ini`)** → dùng `python manage.py test ...` với Postgres/Redis tạm (chạy trong `backend/`).

---

## Tính năng 1 — Tab "Tổng hợp" (Landing)

### Task 1: Ánh xạ tab "Tổng hợp" → scope `landing-summary`

**Files:**
- Create: `frontend/src/app/__tests__/orders-tab.test.ts`
- Modify: `frontend/src/features/orders/use-orders-data.ts:359-371` (FilterScope)
- Modify: `frontend/src/pages/OrdersPage.tsx:92-117` (LandingTabKey + LANDING_SHEET_TABS)
- Modify: `frontend/src/app/orders-tab.ts:18-58` (getOrderTabScope + getOrderTabFallbackDefaults)

- [ ] **Step 1: Write the failing test**

Create `frontend/src/app/__tests__/orders-tab.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { getOrderTabScope, getOrderTabFallbackDefaults } from "@/app/orders-tab";

describe("getOrderTabScope — landing", () => {
  it("maps the default 'orders' tab to landing-summary (Tổng hợp)", () => {
    expect(getOrderTabScope("landing-orders", "orders")).toBe("landing-summary");
  });
  it("maps the explicit 'summary' tab to landing-summary", () => {
    expect(getOrderTabScope("landing-orders", "summary")).toBe("landing-summary");
  });
  it("keeps sheet1 mapping", () => {
    expect(getOrderTabScope("landing-orders", "sheet1")).toBe("landing-sheet1");
  });
  it("keeps sheet2/sheet3 mappings", () => {
    expect(getOrderTabScope("landing-orders", "sheet2")).toBe("landing-sheet2");
    expect(getOrderTabScope("landing-orders", "sheet3")).toBe("landing-sheet3");
  });
  it("keeps delivered/cancelled mappings", () => {
    expect(getOrderTabScope("landing-orders", "delivered")).toBe("landing-delivered");
    expect(getOrderTabScope("landing-orders", "cancelled")).toBe("landing-cancelled");
  });
});

describe("getOrderTabFallbackDefaults — summary", () => {
  it("returns empty status/invoice defaults for summary", () => {
    expect(getOrderTabFallbackDefaults("summary")).toEqual({ status: [], invoiceStatus: [] });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (trong `frontend/`): `npx vitest run src/app/__tests__/orders-tab.test.ts`
Expected: FAIL — TypeScript báo `"summary"`/`"landing-summary"` không hợp lệ, hoặc assertion `landing-summary` fail (hiện trả `landing-sheet1`).

- [ ] **Step 3a: Thêm `"landing-summary"` vào FilterScope**

Trong `frontend/src/features/orders/use-orders-data.ts`, sửa union `FilterScope` (dòng 359-371) thành:

```ts
export type FilterScope =
  | "orders"
  | "orders-delivered"
  | "orders-cancelled"
  | "landing-sale"
  | "landing-summary"
  | "landing-sheet1"
  | "landing-sheet2"
  | "landing-sheet3"
  | "landing-delivered"
  | "landing-cancelled"
  | "koc"
  | "koc-delivered"
  | "koc-cancelled";
```

> Lưu ý: `buildBody` (dòng ~1357) chỉ set `body.sheet` cho `landing-sheet1`/`landing-sale`/`landing-sheet2`. Scope `landing-summary` không khớp nhánh nào nên **không gửi `sheet`** → endpoint trả về hành vi mặc định (đúng "Tổng hợp"). **Không cần sửa `buildBody`.**

- [ ] **Step 3b: Thêm `"summary"` vào LandingTabKey + LANDING_SHEET_TABS**

Trong `frontend/src/pages/OrdersPage.tsx`, sửa type `LandingTabKey` (dòng 92-99) thành:

```ts
export type LandingTabKey =
  | "orders"
  | "summary"
  | "sheet1"
  | "sheet2"
  | "sheet3"
  | "delivered"
  | "cancelled"
  | "blocklist";
```

Và thêm entry "Tổng hợp" làm **phần tử đầu** của `LANDING_SHEET_TABS` (dòng 110-117):

```ts
export const LANDING_SHEET_TABS: Array<{ key: LandingTabKey; label: string }> = [
  { key: "summary", label: "Tổng hợp" },
  { key: "sheet1", label: "Landing xác nhận đơn" },
  { key: "sheet2", label: "Messenger xác nhận đơn" },
  { key: "sheet3", label: "Tư vấn" },
  { key: "delivered", label: "Giao thành công" },
  { key: "cancelled", label: "Đã hủy" },
  { key: "blocklist", label: "Danh sách blocklist" },
];
```

- [ ] **Step 3c: Ánh xạ scope + fallback defaults**

Trong `frontend/src/app/orders-tab.ts`, sửa khối `landing-orders` trong `getOrderTabScope` (dòng 22-29) thành:

```ts
  if (section === "landing-orders") {
    if (tab === "delivered") return "landing-delivered";
    if (tab === "cancelled") return "landing-cancelled";
    if (tab === "sheet1") return "landing-sheet1";
    if (tab === "sheet2") return "landing-sheet2";
    if (tab === "sheet3") return "landing-sheet3";
    // summary và mặc định ("orders") → Tổng hợp (không gửi sheet).
    return "landing-summary";
  }
```

Và bổ sung nhánh `summary` vào `getOrderTabFallbackDefaults` (trước dòng `return { status: [], invoiceStatus: [] };` cuối hàm). Vì giá trị trả về cho `summary` trùng default, chỉ cần đảm bảo default cuối hàm áp dụng — **không cần thêm code mới** nếu hàm đã kết thúc bằng `return { status: [], invoiceStatus: [] };`. Xác nhận hàm kết thúc đúng như vậy (dòng 57).

- [ ] **Step 4: Run test to verify it passes**

Run (trong `frontend/`): `npx vitest run src/app/__tests__/orders-tab.test.ts`
Expected: PASS (tất cả assertions).

- [ ] **Step 5: Verify type-check toàn FE**

Run (trong `frontend/`): `npx tsc --noEmit`
Expected: không có lỗi type mới.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/orders-tab.ts frontend/src/app/__tests__/orders-tab.test.ts frontend/src/features/orders/use-orders-data.ts frontend/src/pages/OrdersPage.tsx
git commit -m "feat(landing): map Tổng hợp tab to landing-summary scope (no sheet param)"
```

---

### Task 2: Tab mặc định + tab-bar active + ẩn SheetStatsRow cho "Tổng hợp"

**Files:**
- Modify: `frontend/src/pages/OrdersPage.tsx:1879-1880` (activeTabKey)
- Modify: `frontend/src/pages/OrdersPage.tsx:1900-1903` (điều kiện SheetStatsRow)

- [ ] **Step 1: Đổi activeTabKey mặc định sang "summary"**

Trong `frontend/src/pages/OrdersPage.tsx`, trong nhánh render tab-bar (dòng ~1877-1881), sửa dòng tính `activeTabKey`:

```ts
                        // Trang Landing: tab mặc định ("orders") tương ứng Tổng hợp.
                        const activeTabKey =
                          isLandingPage && landingTab === "orders" ? "summary" : landingTab;
```

- [ ] **Step 2: Ẩn SheetStatsRow ở tab Tổng hợp**

Cùng file, sửa điều kiện hiển thị `SheetStatsRow` (dòng ~1900-1903) — bỏ `landingTab === "orders"` (vì "orders" giờ = Tổng hợp, dùng endpoint không-`sheet`, không trả `sheet_stats`):

```tsx
                    {isLandingPage &&
                    (landingTab === "sheet1" || landingTab === "sheet2") ? (
```

> Cũng kiểm tra khối `Sheet3Section` (dòng ~1805) và các nhánh khác không giả định `"orders" === sheet1`. Tab "Tổng hợp" rơi vào nhánh `OrdersTable` thường (else), đúng ý muốn: bảng phẳng, không SLA, không stats row.

- [ ] **Step 3: Verify type-check**

Run (trong `frontend/`): `npx tsc --noEmit`
Expected: không lỗi.

- [ ] **Step 4: Verify trực quan**

Khởi động app (theo README: `docker compose up --build`, hoặc dev server FE đang chạy). Mở `/landing-orders`:
- Thanh tab hiển thị **Tổng hợp** ở đầu và **đang active** khi mới vào.
- Bảng hiển thị đơn nhiều nguồn (landing/zalo/messenger/hotline...) nhưng **không** có đơn `koc` và **không** có đơn đã hủy.
- **Không** có thanh "Tổng đơn / Mới chưa gọi / ..." (SheetStatsRow).
- Bấm sang **Landing xác nhận đơn** (Sheet 1): SheetStatsRow xuất hiện trở lại; bấm lại **Tổng hợp**: ẩn đi.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/OrdersPage.tsx
git commit -m "feat(landing): make Tổng hợp the default landing tab, hide sheet stats for it"
```

---

## Tính năng 2 — "Nhóm 7 — Tỷ lệ hoàn theo sản phẩm"

### Task 3: Backend aggregation `summary_return_rate_by_product`

**Files:**
- Modify: `backend/apps/orders/_repositories_reports.py` (thêm method sau `summary_bomb_reasons`, ~dòng 334)
- Modify: `backend/apps/orders/transport/views/order_views_impl/order_ingest_product_dashboard_view.py:573-613`
- Test: `backend/apps/orders/tests/test_dashboard_summary_api.py` (class `DashboardSalePerformanceTests`)

- [ ] **Step 1: Write the failing test**

Trong `backend/apps/orders/tests/test_dashboard_summary_api.py`:

1. Sửa dòng import (dòng 11) để thêm `OrderItem`, `Product`:

```python
from apps.orders.models import Order, OrderAssessment, OrderItem, OrderJourneyEvent, Product
```

2. Thêm method vào **`DashboardSalePerformanceTests`** (sau `test_delivery_quality_by_staff_classifies_orders`):

```python
    def test_return_rate_by_product_groups_by_product(self):
        # Mỗi đơn gắn 1 sản phẩm qua OrderItem. % hoàn theo SP = đơn hoàn / đơn đã gửi.
        product_a = Product.objects.create(name="Ghế massage A", sku="SKU-A")
        product_b = Product.objects.create(name="Ghế massage B", sku="SKU-B")

        def make(status, product):
            order = Order.objects.create(
                channel=self.landing_channel,
                customer=self.customer,
                staff=self.alice,
                name="X",
                phone="0909333444",
                status=status,
                order_source="landing",
            )
            if product is not None:
                OrderItem.objects.create(order=order, product=product)
            return order

        make(5, product_a)   # đã hoàn (status 5) + đã gửi — SP A
        make(3, product_a)   # giao TC (đã gửi) — SP A
        make(5, product_b)   # đã hoàn + đã gửi — SP B
        make(0, product_b)   # mới (chưa gửi) — SP B
        make(5, None)        # đã hoàn nhưng KHÔNG có item → loại khỏi báo cáo SP

        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/orders/dashboard/summary")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        rows = {r["items__product__sku"]: r for r in payload["return_rate_by_product"]}

        self.assertEqual(rows["SKU-A"]["total"], 2)
        self.assertEqual(rows["SKU-A"]["sent"], 2)
        self.assertEqual(rows["SKU-A"]["returned"], 1)

        self.assertEqual(rows["SKU-B"]["total"], 2)
        self.assertEqual(rows["SKU-B"]["sent"], 1)
        self.assertEqual(rows["SKU-B"]["returned"], 1)

        # Đơn không có item không tạo thêm nhóm SP nào.
        self.assertEqual(set(rows.keys()), {"SKU-A", "SKU-B"})
```

- [ ] **Step 2: Run test to verify it fails**

Run (trong `backend/`): `python manage.py test apps.orders.tests.test_dashboard_summary_api.DashboardSalePerformanceTests.test_return_rate_by_product_groups_by_product`
Expected: FAIL — `KeyError: 'return_rate_by_product'` (response chưa có key này).

- [ ] **Step 3a: Thêm aggregation vào repository**

Trong `backend/apps/orders/_repositories_reports.py`, thêm method ngay sau `summary_bomb_reasons` (kết thúc ~dòng 334):

```python
    @classmethod
    def summary_return_rate_by_product(
        cls, date_from: str | None = None, date_to: str | None = None
    ):
        """Tỷ lệ hoàn theo sản phẩm — % hoàn = đơn hoàn (status 5) / đơn đã gửi.

        Gom theo ``OrderItem.product``. Mỗi đơn được tính 1 lần cho mỗi sản phẩm
        nó chứa (đơn nhiều SP xuất hiện ở từng SP). Đơn KHÔNG có dòng sản phẩm
        khớp ``Product`` (SKU không khớp danh mục) bị loại khỏi báo cáo này.
        """
        queryset = cls._delivery_quality_base_queryset(date_from, date_to).filter(
            items__product__isnull=False
        )
        return list(
            queryset.values(
                "items__product",
                "items__product__sku",
                "items__product__name",
            )
            .annotate(
                total=Count("id", distinct=True),
                sent=Count(
                    "id", filter=Q(status__in=SENT_OUT_ORDER_STATUSES), distinct=True
                ),
                returned=Count(
                    "id", filter=Q(status=RETURNED_ORDER_STATUS), distinct=True
                ),
            )
            .order_by("-returned", "items__product__name")
        )
```

> `Count`, `Q`, `SENT_OUT_ORDER_STATUSES`, `RETURNED_ORDER_STATUS`, `_delivery_quality_base_queryset` đều đã có sẵn trong file. `distinct=True` đảm bảo đếm theo đơn (không nhân đôi do join `items`).

- [ ] **Step 3b: Wire vào dashboard_summary view**

Trong `backend/apps/orders/transport/views/order_views_impl/order_ingest_product_dashboard_view.py`, hàm `dashboard_summary`:

Thêm sau dòng tính `portrait_by_staff` (dòng ~597-599):

```python
    return_rate_by_product = OrderRepository.summary_return_rate_by_product(
        date_from=date_from, date_to=date_to
    )
```

Và thêm key vào dict `data` (sau `"portrait_completion_by_staff": portrait_by_staff,` dòng ~611):

```python
        "return_rate_by_product": return_rate_by_product,
```

- [ ] **Step 4: Run test to verify it passes**

Run (trong `backend/`): `python manage.py test apps.orders.tests.test_dashboard_summary_api.DashboardSalePerformanceTests.test_return_rate_by_product_groups_by_product`
Expected: PASS.

- [ ] **Step 5: Run cả test class để chắc không regression**

Run (trong `backend/`): `python manage.py test apps.orders.tests.test_dashboard_summary_api`
Expected: tất cả PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/orders/_repositories_reports.py backend/apps/orders/transport/views/order_views_impl/order_ingest_product_dashboard_view.py backend/apps/orders/tests/test_dashboard_summary_api.py
git commit -m "feat(dashboard): add return-rate-by-product aggregation (status 5 / sent)"
```

---

### Task 4: Frontend card "Nhóm 7 — Tỷ lệ hoàn theo sản phẩm"

**Files:**
- Modify: `frontend/src/pages/DashboardPage.tsx` (type + Props.summary + card)

- [ ] **Step 1: Thêm type + Props**

Trong `frontend/src/pages/DashboardPage.tsx`, thêm type (cạnh các type row khác, ví dụ sau `BombReasonRow` ~dòng 109):

```ts
type ReturnRateByProductRow = {
  items__product: number | null;
  items__product__sku: string | null;
  items__product__name: string | null;
  total: number;
  sent: number;
  returned: number;
};
```

Thêm vào `Props.summary` (trong object literal `summary: { ... } | null`, sau `portrait_completion_by_staff?: ...`):

```ts
    return_rate_by_product?: ReturnRateByProductRow[];
```

- [ ] **Step 2: Thêm hằng số ngưỡng + tính toán**

Cạnh `UNREACHED_WARNING_THRESHOLD = 30` (dòng 136), thêm:

```ts
const RETURN_RATE_WARNING_THRESHOLD = 10; // % hoàn vượt mức này → tô đỏ cảnh báo
```

Trong thân component `DashboardPage`, cạnh các `useMemo` totals khác (ví dụ sau `portraitByStaffTotals`), thêm:

```ts
  const returnByProductRows = summary?.return_rate_by_product ?? [];
```

- [ ] **Step 3: Thêm card Nhóm 7**

Trong JSX, thêm `<Card>` này **ngay sau** card "Nhóm 5b — Hiệu quả giao hàng theo Kênh" (sau thẻ `</Card>` đóng của nhóm 5b) và trước `</div>` bao ngoài cùng:

```tsx
      {/* Nhóm 7 — Tỷ lệ hoàn theo sản phẩm */}
      <Card className="rounded-2xl border-border bg-card">
        <CardHeader className="pb-3">
          <CardTitle className="text-base">
            {t("dashboard.returnByProductTitle", "Nhóm 7 — Tỷ lệ hoàn theo sản phẩm")}
          </CardTitle>
          <p className="text-xs text-muted-foreground">
            {t(
              "dashboard.returnByProductHint",
              "% hoàn = Đơn đã chuyển hoàn / Đơn đã gửi, gom theo sản phẩm. Chỉ tính các đơn có dòng sản phẩm khớp danh mục (đơn SKU lạ không vào báo cáo này).",
            )}
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          {returnByProductRows.length === 0 ? (
            <p className="py-2 text-sm text-muted-foreground">
              {t("dashboard.empty", "No data yet.")}
            </p>
          ) : (
            <>
              {(() => {
                const returnRatePct = safePercent(
                  deliveryStaffTotals.returned,
                  deliveryStaffTotals.sent,
                );
                return (
                  <KpiHero
                    bigValue={`${returnRatePct.toFixed(1)}%`}
                    bigLabel={t("dashboard.returnRateColumn", "% hoàn")}
                    bigTone={
                      returnRatePct <= RETURN_RATE_WARNING_THRESHOLD ? "success" : "danger"
                    }
                    breakdown={[
                      {
                        label: t("dashboard.sentColumn", "Đơn gửi"),
                        value: deliveryStaffTotals.sent.toLocaleString("vi-VN"),
                      },
                      {
                        label: t("dashboard.returnedColumn", "Đơn hoàn"),
                        value: deliveryStaffTotals.returned.toLocaleString("vi-VN"),
                        tone: "danger",
                      },
                    ]}
                  />
                );
              })()}
              <div className="rounded-xl border border-border bg-muted/20 p-4">
                <div className="mb-2 text-sm font-semibold text-foreground">
                  {t("dashboard.returnByProductChartTitle", "% hoàn theo sản phẩm")}
                </div>
                <p className="mb-3 text-xs text-muted-foreground">
                  {t(
                    "dashboard.returnByProductChartHint",
                    `Cảnh báo đỏ khi vượt ${RETURN_RATE_WARNING_THRESHOLD}%.`,
                  )}
                </p>
                <VerticalRateBar
                  data={returnByProductRows.map((row) => ({
                    name:
                      row.items__product__sku ||
                      row.items__product__name?.trim() ||
                      t("dashboard.unknownProduct", "Không xác định"),
                    value: safePercent(row.returned, row.sent),
                  }))}
                  warningThreshold={RETURN_RATE_WARNING_THRESHOLD}
                  domain={PERCENT_AXIS_DOMAIN}
                />
              </div>
              <CollapsibleTable label={t("dashboard.showDetailTable", "Xem bảng chi tiết")}>
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border text-left text-muted-foreground">
                      <th className="py-2 pr-4 font-medium">
                        {t("dashboard.productColumn", "Sản phẩm")}
                      </th>
                      <th className="py-2 pr-4 text-right font-medium">
                        {t("dashboard.sentColumn", "Đơn gửi")}
                      </th>
                      <th className="py-2 pr-4 text-right font-medium">
                        {t("dashboard.returnedColumn", "Đơn hoàn")}
                      </th>
                      <th className="py-2 text-right font-medium">
                        {t("dashboard.returnRateColumn", "% hoàn")}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {returnByProductRows.map((row) => {
                      const ratePct = percentValue(row.returned, row.sent);
                      const isWarning =
                        ratePct !== null && ratePct > RETURN_RATE_WARNING_THRESHOLD;
                      const productLabel =
                        row.items__product__name?.trim() ||
                        row.items__product__sku ||
                        t("dashboard.unknownProduct", "Không xác định");
                      return (
                        <tr
                          key={String(row.items__product ?? "unknown")}
                          className="border-b border-border/60"
                        >
                          <td className="py-2 pr-4 text-foreground">
                            <div className="font-medium text-foreground">{productLabel}</div>
                            {row.items__product__sku &&
                              row.items__product__sku !== productLabel && (
                                <div className="text-xs text-muted-foreground">
                                  {row.items__product__sku}
                                </div>
                              )}
                          </td>
                          <td className="py-2 pr-4 text-right text-foreground">{row.sent}</td>
                          <td className="py-2 pr-4 text-right text-foreground">{row.returned}</td>
                          <td
                            className={`py-2 text-right font-semibold ${
                              isWarning ? "text-destructive" : "text-foreground"
                            }`}
                          >
                            {formatPercent(row.returned, row.sent)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </CollapsibleTable>
            </>
          )}
        </CardContent>
      </Card>
```

> Tái dùng helpers/component có sẵn: `safePercent`, `percentValue`, `formatPercent`, `KpiHero`, `VerticalRateBar`, `CollapsibleTable`, `PERCENT_AXIS_DOMAIN`, `deliveryStaffTotals`. KPI tổng "% hoàn" lấy từ `deliveryStaffTotals` (đơn-level, chính xác) thay vì cộng dồn theo SP (tránh nhân đôi đơn nhiều SP).

- [ ] **Step 4: Verify type-check**

Run (trong `frontend/`): `npx tsc --noEmit`
Expected: không lỗi.

- [ ] **Step 5: Verify trực quan**

Mở trang **Báo cáo Sale** (`/dashboard`). Chọn khoảng ngày có đơn hoàn:
- Xuất hiện card **"Nhóm 7 — Tỷ lệ hoàn theo sản phẩm"** dưới Nhóm 5b.
- KPI tổng `% hoàn` hiển thị; biểu đồ thanh `% hoàn` theo SP, thanh > 10% tô **đỏ**.
- Mở "Xem bảng chi tiết": cột SP · Đơn gửi · Đơn hoàn · % hoàn khớp dữ liệu; % hoàn > 10% chữ đỏ.
- Khoảng ngày không có đơn hoàn / không có item khớp SP → hiển thị "No data yet."

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/DashboardPage.tsx
git commit -m "feat(dashboard): add Nhóm 7 — return rate by product card"
```

---

## Hoàn tất

- [ ] **Chạy lại toàn bộ test FE liên quan + tsc**

Run (trong `frontend/`): `npx vitest run` và `npx tsc --noEmit`
Expected: PASS / no errors.

- [ ] **Chạy lại test backend dashboard**

Run (trong `backend/`): `python manage.py test apps.orders.tests.test_dashboard_summary_api`
Expected: PASS.

- [ ] **Cập nhật tiến độ vào spec** (mục "Tiến độ triển khai" cuối spec) nếu cần, rồi quyết định merge theo skill `finishing-a-development-branch`.
