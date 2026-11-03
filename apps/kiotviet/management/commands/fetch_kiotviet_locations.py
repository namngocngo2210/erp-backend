"""Fetch all locations from KiotViet API and save to kiotviet_address_final.json."""

import json
import os

from django.core.management.base import BaseCommand

from apps.kiotviet.models import KiotVietChannel
from apps.kiotviet.infrastructure.services.common_auth_customer import (
    http_read,
    wait_for_kiotviet_slot,
    get_access_token,
)


PAGE_SIZE = 100
OUTPUT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "orders", "data", "kiotviet_address_final.json"
)


class Command(BaseCommand):
    help = "Fetch all locations from KiotViet Public API and save to JSON."

    def add_arguments(self, parser):
        parser.add_argument(
            "--channel-id",
            type=int,
            default=4,
            help="KiotVietChannel ID to use for auth (default: 4)",
        )
        parser.add_argument(
            "--output",
            type=str,
            default=OUTPUT_PATH,
            help="Output file path",
        )

    def handle(self, *args, **options):
        channel_id = options["channel_id"]
        output_path = options["output"]

        channel = KiotVietChannel.objects.get(id=channel_id)
        self.stdout.write(f"Using channel: id={channel.id} retailer={channel.retailer}")

        token = get_access_token(order=None, channel=channel)
        base_url = "https://public.kiotapi.com"
        headers = {
            "Authorization": f"Bearer {token}",
            "Retailer": str(channel.retailer or "").strip(),
            "Content-Type": "application/json",
        }

        all_locations = []
        current_item = 0
        total = None

        retailer = str(channel.retailer or "").strip()

        while True:
            params = {
                "currentItem": current_item,
                "pageSize": PAGE_SIZE,
            }
            # Command này gọi thẳng requests (không qua _kiotviet_request) nên phải
            # tự xin slot, nếu không nó sẽ ăn hạn mức của các job đang chạy song song.
            wait_for_kiotviet_slot(retailer)
            # GET thuần đọc → http_read (session pooling + retry 429/5xx).
            resp = http_read.get(
                f"{base_url}/locations",
                headers=headers,
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
            body = resp.json()

            if total is None:
                total = body.get("total", 0)
                self.stdout.write(f"Total locations: {total}")

            data = body.get("data", [])
            if not data:
                break

            all_locations.extend(data)
            current_item += len(data)
            self.stdout.write(f"  Fetched {current_item}/{total}")

            if current_item >= total:
                break

        self.stdout.write(f"\nTotal fetched: {len(all_locations)}")

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(all_locations, f, ensure_ascii=False, indent=2)

        self.stdout.write(self.style.SUCCESS(f"Saved to {output_path}"))
