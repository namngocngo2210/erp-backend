from apps.common.repository import BaseRepository
from apps.warehouses.models import Warehouse


class WarehouseRepository(BaseRepository[Warehouse]):
    model = Warehouse
