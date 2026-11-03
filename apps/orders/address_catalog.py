"""Facade module to preserve public imports while delegating to layered implementation."""

from .domain.address.address_catalog_impl import (  # noqa: F401
    AddressCatalog,
    DistrictNode,
    ProvinceNode,
    WardNode,
    build_catalog_payload,
    build_districts_payload,
    build_provinces_payload,
    build_wards_payload,
    get_address_catalog,
    get_catalog_etag,
    logger,
    map_to_catalog,
    normalize_codes,
)
