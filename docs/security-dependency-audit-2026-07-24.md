# Rà soát lỗ hổng phụ thuộc Python — 2026-07-24

> Phát hiện khi job `backend:pip-audit` chạy lần đầu (CI thật vừa được dựng ở MR !1069).
> `pip-audit` đã nằm trong workflow GitHub từ trước với `continue-on-error`, nhưng
> **chưa từng chạy** — repo host trên GitLab (xem `audit-2026-07-23-full-stack.md` §N0).

## Kết quả

| | Lỗ hổng | Package |
|---|---:|---:|
| Trước | **37** | 15 |
| Sau bump patch/minor | 3 | 2 |
| **Sau gỡ daphne+twisted** | **1** | **1** |

Hai bước: (1) bump patch/minor 14 package — không breaking change; (2) gỡ hẳn daphne +
twisted và 10 gói mồ côi kéo theo.

## Đã nâng

| Package | Từ → Đến | CVE | Vì sao ưu tiên |
|---|---|---:|---|
| `pyjwt` | 2.12.1 → 2.13.0 | 8 | Thư viện ký/verify JWT — đúng đường xác thực |
| `django` | 6.0.6 → 6.0.7 | 3 | Framework lõi, hứng internet |
| `cryptography` | 46.0.5 → 46.0.7 | 4 | Fernet mã hoá token KiotViet |
| `daphne` | 4.2.1 → 4.2.2 | 2 | (xem ghi chú bên dưới — không chạy ở runtime) |
| `urllib3` | 2.6.3 → 2.7.0 | 3 | Đường gọi KiotViet / Lark / BEST |
| `pyasn1` | 0.6.3 → 0.6.4 | 3 | — |
| `ujson` | 5.12.0 → 5.13.0 | 3 | — |
| `pygments`, `requests`, `idna`, `msgpack`, `cbor2`, `click`, `pytest` | patch | 8 | — |

## Đã gỡ daphne + twisted (thay vì hoãn)

2 CVE twisted ban đầu định hoãn, nhưng đo lại thấy **twisted không nằm trên đường đi của
bất kỳ request nào** nên gỡ hẳn là lựa chọn đúng hơn bump:

- Cả 3 môi trường (prod / staging / local) serve bằng
  `gunicorn config.asgi:application -k uvicorn.workers.UvicornWorker` — **không dùng daphne**.
- `daphne` **không có** trong `INSTALLED_APPS`; là extra tùy chọn của `channels`
  (`daphne>=4.0.0; extra == "daphne"`), không phải dependency bắt buộc.
- Không file `.py` nào trong `apps/`/`config/` import `daphne` hoặc `twisted`.

Đã gỡ 12 gói (daphne, twisted + 10 gói mồ côi kéo theo — tính bằng đồ thị phụ thuộc, không
đoán): `autobahn`, `automat`, `cbor2`, `constantly`, `hyperlink`, `incremental`, `py-ubjson`,
`txaio`, `ujson`, `zope-interface`. `requirements.txt`: **102 → 90 gói**.

Đã smoke test sau khi gỡ:
```
config.asgi.application dựng OK (ProtocolTypeRouter)
channels 4.3.2 + channels_redis import OK
uvicorn.workers.UvicornWorker import OK
```

### Còn lại 1 CVE — cố ý chưa vá

| Package | Cần lên | Vì sao hoãn |
|---|---|---|
| `cryptography` 46.0.7 | 48.0.1 | Nhảy **2 major** chỉ để vá 1 CVE (`GHSA-537c-gmf6-5ccf`); 4 CVE khác của cryptography đã vá xong ở 46.0.7. Major bump của thư viện mã hoá token KiotViet cần lượt kiểm tra riêng. |

## Frontend — dọn thêm

Ngoài phân tích bên dưới, đã **gỡ `shadcn` khỏi `devDependencies`** (nó kéo theo cả MCP SDK +
express, nguồn của 3/7 lỗ hổng high) và chạy `npm audit fix`. Kết quả: **14 → 2 high**, cả 2
còn lại là `js-yaml` từ eslint/openapi-typescript — công cụ dev lõi, không gỡ được, không tới
tay người dùng. `npm audit --omit=dev` vẫn **0 lỗ hổng**. Thêm component mới sau này dùng
`npx shadcn@latest add …`.

## Frontend — không có gì tới tay người dùng

`npm audit` báo 14 lỗ hổng (7 high), nhưng lọc đúng thì:

```
npm audit --omit=dev  →  found 0 vulnerabilities
```

**Toàn bộ 14 lỗ hổng nằm ở `devDependencies`**, không có gói nào đi vào bundle trình duyệt:

| Gói | Đến từ | Vai trò |
|---|---|---|
| `ws` | `jsdom` | môi trường test vitest |
| `js-yaml`, `brace-expansion` | `eslint` | lint |
| `picomatch` | `lint-staged` | git hook |
| `hono`, `path-to-regexp`, `fast-uri` | `shadcn` → `@modelcontextprotocol/sdk` → `express` | CLI sinh component |

Rủi ro thật: chỉ ảnh hưởng máy dev và CI runner, **không ảnh hưởng người dùng cuối**.

**Đề xuất nhỏ:** `shadcn` nằm trong `devDependencies` kéo theo cả một MCP SDK + express
server (3/7 lỗ hổng high đến từ nhánh này) trong khi nó chỉ được dùng lúc thêm component
mới. Gỡ khỏi `devDependencies` và dùng `npx shadcn@latest add …` khi cần là xong.

## Cách chạy lại

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app \
  ghcr.io/astral-sh/uv:python3.13-bookworm-slim \
  sh -c "uvx pip-audit -r requirements.txt"
```

Job CI `backend:pip-audit` đặt `allow_failure: true` có chủ đích: CVE mới được công bố hằng
ngày, để nó chặn merge thì CI sẽ đỏ vào những ngày không ai đụng code — dev học cách phớt lờ,
rồi phớt lờ luôn cái quan trọng. Nó cảnh báo, con người quyết.
