"""Facade package: re-export tường minh API công khai của catalog core + queries."""

from .catalog_core import (  # noqa: F401
    AddressCatalog,
    DistrictNode,
    ProvinceNode,
    WardNode,
    logger,
)
from .catalog_queries import (  # noqa: F401
    build_catalog_payload,
    build_districts_payload,
    build_provinces_payload,
    build_wards_payload,
    get_address_catalog,
    get_catalog_etag,
    map_to_catalog,
    normalize_codes,
)
