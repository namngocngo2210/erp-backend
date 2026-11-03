# Order Events Contract

This document describes the event contract used by order producer/consumer flows.

## Source Of Truth

- Code contract: `backend/apps/orders/contracts/order_events.py`
- Docs: this file

All producers and consumers must validate payloads with `OrderEventContract`.

## Event Types

### `order.created`

Required fields:
- `type` = `order.created`
- `order_id` (int > 0)
- `channel_code` (string)

Optional fields:
- `order_status` (`new`, `confirmed`, `cancelled`)
- `source`
- `occurred_at` (ISO datetime, auto-filled if missing)

### `order.updated`

Required fields:
- `type` = `order.updated`
- `order_id` (int > 0)

Optional fields:
- `channel_code` (string)
- `order_status` (`new`, `confirmed`, `cancelled`)
- `from_status`
- `to_status`
- `source`
- `occurred_at` (ISO datetime, auto-filled if missing)

`from_status` and `to_status` must be provided together.

## Status Transition Rules

Only allowed on `order.updated`:

- `new -> confirmed`
- `new -> cancelled`
- `confirmed -> cancelled`

Rejected transitions:
- same status (`new -> new`, etc.)
- unknown statuses
- reversed transitions not listed above (`confirmed -> new`, `cancelled -> new`, etc.)

## Producer/Consumer Alignment

Producer side:
- `OrderService.push_event(...)` validates every payload before publish.
- Builders:
  - `OrderEventContract.build_order_created(...)`
  - `OrderEventContract.build_order_updated(...)`

Consumer side:
- `CRMConsumer.crm_event(...)` validates payload again before pushing to WebSocket clients.
- Invalid payloads are dropped.

## Retry/Error Behavior

- WebSocket event delivery is best-effort.
- If payload violates contract:
  - producer raises `OrderEventContractError` (fail fast in backend flow).
  - consumer drops invalid payloads (defensive guard).
- Celery job processing (`process_kiotviet_job`) emits `order.updated` at:
  - job started
  - job finished
  - job failed
