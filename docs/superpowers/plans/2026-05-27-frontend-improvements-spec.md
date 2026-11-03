# Frontend Improvements — Spec tổng & lộ trình

> Nguồn: `docs/frontend_review_report.md` (rà soát 2026-05-27). Tài liệu này là spec tổng, gom toàn bộ P1/P2/P3 và chia thành 5 plan độc lập theo area. Mỗi plan chạy & test được riêng.

## Quyết định kiến trúc (đã chốt với CTO)

| Vấn đề | Quyết định |
|---|---|
| Routing | Giữ SPA Vite, thêm **react-router-dom** (không migrate Next.js lúc này) |
| Data-fetching | Thêm **TanStack Query** (@tanstack/react-query), migrate dần từng resource |
| Tổ chức plan | **Nhiều plan theo area**, độc lập, giao việc song song được |

## Bản đồ P1/P2/P3 → plan

| Severity | Vấn đề | Plan |
|---|---|---|
| P1 | Không có ESLint/Prettier + CI | `fe-01-tooling-quality` |
| P3 | BOM trong tsconfig | `fe-01-tooling-quality` |
| P3 | 67 `console.*` sót lại | `fe-01-tooling-quality` |
| P3 | 39 chỗ `any` | `fe-01-tooling-quality` |
| P1 | Không có tầng caching/data-fetching | `fe-02-data-layer` |
| P2 | Dead code `lib/fetch-json.ts` | `fe-02-data-layer` |
| P2 | Fetch inline rải rác 11 file, thiếu API layer | `fe-02-data-layer` |
| P1 | Routing tự chế bằng `window.location` | `fe-03-routing` |
| P1 | God components/files >1800 dòng | `fe-04-refactor-god-components` |
| P2 | Re-render rộng, thiếu memo | `fe-04-refactor-god-components` |
| P2 | Polling chưa kiểm soát | `fe-05-perf-cleanup` |
| P2 | Bundle vendor nặng (`@base-ui` thừa) | `fe-05-perf-cleanup` |
| P3 | i18n `messages.ts` 1322 dòng | `fe-05-perf-cleanup` |
| P3 | 261 arrow inline trong OrdersPage | `fe-04` / `fe-05` |

## Thứ tự thực hiện (dependency)

```
fe-01 (tooling)  ──┐  nền tảng chất lượng, làm TRƯỚC để mọi PR sau được lint
                   │
fe-02 (data)     ──┼──> fe-04 (refactor) ──> fe-05 (perf/cleanup)
fe-03 (routing)  ──┘
```

- **fe-01** làm đầu tiên: dựng hàng rào lint/format/CI để các plan sau không tạo nợ mới.
- **fe-02** và **fe-03** độc lập, có thể chạy song song sau fe-01.
- **fe-04** (tách god component) nên làm sau fe-02 + fe-03 vì `useOrdersData`/`OrdersPage`/`App.tsx` sẽ gọn hơn nhiều khi data đã chuyển sang Query và routing đã tách ra.
- **fe-05** dọn dẹp & tối ưu cuối cùng, đo lại bundle.

## Sprint đề xuất

| Sprint | Nội dung | Plan |
|---|---|---|
| Sprint 1 | Tooling + CI | fe-01 |
| Sprint 2 | Data layer + routing (song song) | fe-02, fe-03 |
| Sprint 3 | Tách god components | fe-04 |
| Sprint 4 | Perf & cleanup, đo bundle | fe-05 |

## Nguyên tắc chung mọi plan
- Mỗi task theo TDD khi có hành vi kiểm chứng được; commit nhỏ, thường xuyên.
- Commit message tiếng Anh, ngắn gọn (theo CLAUDE.md), **không** kèm dòng Co-Authored-By.
- Mỗi session một branch riêng; nếu đang không ở `master` thì checkout master + pull rồi tạo branch mới.
- Sau mỗi sprint, note lại đã làm / chưa làm trong chính file plan.

## Definition of Done toàn cục
- [ ] `npm run lint` và `npm run format:check` pass, chạy trong CI.
- [ ] `npm run test` xanh; `npm run build` thành công.
- [ ] Không còn `lib/fetch-json.ts`; mọi gọi API qua module trong `lib/api/`.
- [ ] Điều hướng qua react-router; không còn `history.pushState` thủ công ngoài router.
- [ ] Không file nào >800 dòng trong `pages/` và `features/` (mục tiêu, không tuyệt đối).
- [ ] Chạy `/audit` lại, điểm cấu trúc & hiệu năng tăng.
