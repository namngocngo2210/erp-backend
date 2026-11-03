#!/usr/bin/env python3
"""Cầu nối Alertmanager → Lark custom bot.

VÌ SAO CẦN: Alertmanager gửi payload theo schema của chính nó
(``{"version":"4","status":"firing","alerts":[...]}``), còn Lark custom bot chỉ
nhận ``{"msg_type":"text","content":{"text":...}}``. Trỏ webhook_configs thẳng
vào URL bot Lark thì Lark trả lỗi và **không ai nhận được alert** — hỏng im lặng,
tệ hơn là biết mình không có alerting. Alertmanager v0.27 không template được
body webhook nên bắt buộc phải có lớp chuyển đổi.

Chỉ dùng thư viện chuẩn: chạy thẳng trên image python-slim, không cần build.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

LARK_URL = os.getenv("LARK_ALERT_WEBHOOK_URL", "").strip()
PORT = int(os.getenv("ADAPTER_PORT", "9095"))
TIMEOUT = float(os.getenv("LARK_TIMEOUT_SECONDS", "10"))

_ICON = {"firing": "🔴", "resolved": "✅"}


def _log(msg: str) -> None:
    # stdout → docker log → Loki. Đây là đường duy nhất để biết adapter có sống.
    print(msg, flush=True)


def _format(payload: dict) -> str:
    status = payload.get("status", "unknown")
    alerts = payload.get("alerts") or []
    lines = [f"{_ICON.get(status, '⚠️')} [{status.upper()}] {len(alerts)} alert"]

    for alert in alerts:
        labels = alert.get("labels") or {}
        annotations = alert.get("annotations") or {}
        name = labels.get("alertname", "?")
        severity = labels.get("severity", "?")
        lines.append(f"\n• {name} ({severity})")
        summary = annotations.get("summary")
        if summary:
            lines.append(f"  {summary}")
        description = annotations.get("description")
        if description:
            lines.append(f"  {description}")
        # Nhãn định danh nguồn — thiếu nó thì alert chỉ nói "có gì đó hỏng".
        for key in ("instance", "job", "route", "queue_name", "name"):
            if key in labels:
                lines.append(f"  {key}={labels[key]}")

    return "\n".join(lines)


def _send_to_lark(text: str) -> None:
    body = json.dumps({"msg_type": "text", "content": {"text": text}}).encode("utf-8")
    req = urllib.request.Request(
        LARK_URL, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        raw = resp.read().decode("utf-8", "replace")
    # Lark trả HTTP 200 KỂ CẢ khi từ chối message (lỗi nằm trong body: code != 0).
    # Không kiểm tra chỗ này thì mọi lỗi cấu hình đều trông như thành công.
    try:
        data = json.loads(raw)
    except ValueError:
        _log(f"[WARN] Lark trả về body không phải JSON: {raw[:200]}")
        return
    if data.get("code", 0) != 0:
        _log(f"[ERROR] Lark từ chối message: {raw[:300]}")
    else:
        _log("[OK] đã gửi Lark")


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - tên do BaseHTTPRequestHandler quy định
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"

        # Luôn trả 200 cho Alertmanager rồi mới xử lý: nếu trả lỗi, Alertmanager
        # sẽ retry liên tục và có thể spam Lark khi sự cố nằm ở phía Lark.
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

        try:
            payload = json.loads(raw.decode("utf-8"))
        except ValueError:
            _log(f"[ERROR] payload không phải JSON: {raw[:200]!r}")
            return

        text = _format(payload)
        if not LARK_URL:
            _log(f"[NOOP] chưa cấu hình LARK_ALERT_WEBHOOK_URL. Alert:\n{text}")
            return
        try:
            _send_to_lark(text)
        except urllib.error.URLError as exc:
            _log(f"[ERROR] không gọi được Lark: {exc}")

    def do_GET(self):  # noqa: N802
        # Healthcheck.
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args):
        # Tắt access log mặc định (mỗi alert đã có dòng log riêng ở trên).
        return


if __name__ == "__main__":
    if not LARK_URL:
        _log("[WARN] LARK_ALERT_WEBHOOK_URL trống — chạy chế độ NOOP, chỉ in ra log.")
    _log(f"lark-alert-adapter nghe ở :{PORT}")
    try:
        HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)
