import hashlib
import json
import logging
import random
import time
from decimal import Decimal
from urllib.parse import urlparse

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.channels_core.models import Channel, ChannelApiToken, UserChannel
from apps.customers.models import Customer
from apps.customers.services import CustomerService, normalize_phone, normalize_name
from apps.users.services import UserService
from apps.audit.decorators import log_kiotviet_call

from apps.orders.contracts import OrderEventContract
from apps.orders.crm_catalog import BOMB_REASON_TYPE_LABELS
from apps.orders.address_catalog import map_to_catalog
from apps.orders.address_parser import format_standard_address, normalize_raw_address, parse_legacy_level3
from apps.orders.utils.kiotviet_address_mapping import resolve_kiotviet_address
from apps.orders.models import (
    NeedType,
    Order,
    OrderAssessment,
    OrderItem,
    OrderJourneyEvent,
    OrderNeed,
    OrderRecall,
    OrderRecallEntry,
    OrderSource,
    OrderStatus,
    OrderVat,
)
from apps.catalog.models import Product
from apps.orders.product_parser import derive_quantity, derive_sku, derive_total_amount
from apps.orders.sheet_rules import (
    ORDER_FIELD_CHANGED_EVENT,
    STAFF_ASSIGNED_EVENT,
    STAFF_CHANGED_EVENT,
    STAFF_INGEST_DESIGNATED_EVENT,
    TRACKED_ORDER_FIELDS,
)
from apps.users.models import User

logger = logging.getLogger(__name__)


