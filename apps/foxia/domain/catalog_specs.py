"""Đặc tả các danh mục cần đồng bộ: model nội bộ ↔ tài nguyên Foxia ↔ field.

Gom vào một chỗ để thêm danh mục mới là thêm một dòng, không phải viết thêm một
hàm sync. Thứ tự trong ORDERED_SPECS là thứ tự phụ thuộc: danh mục được tham
chiếu phải đồng bộ trước danh mục tham chiếu nó, nếu không FK sẽ rỗng ở lượt đầu
và chỉ đầy lên ở lượt sau.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from apps.foxia.models import (
    FoxiaBranch,
    FoxiaBrand,
    FoxiaChannel,
    FoxiaMarketingTeam,
    FoxiaSaleTeam,
    FoxiaSource,
    FoxiaStage,
    FoxiaUser,
)


@dataclass(frozen=True)
class CatalogSpec:
    """Một danh mục Foxia.

    `relations`: tên field FK ở model nội bộ → (tên field bên Foxia, model đích).
    Service dùng nó để dịch id Foxia sang pk nội bộ khi upsert.
    `scalars`: tên field nội bộ → tên field bên Foxia, cho giá trị không phải quan hệ.
    """

    resource: str
    model: type
    fields: tuple[str, ...]
    scalars: dict = field(default_factory=dict)
    relations: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return self.resource


# Field `name` luôn được kéo; khai báo tường minh trong `fields` để request gửi
# đúng những gì tài liệu Foxia liệt kê, không dựa vào mặc định của Odoo.
ORDERED_SPECS: tuple[CatalogSpec, ...] = (
    CatalogSpec(resource="congtycon", model=FoxiaBranch, fields=("id", "name")),
    CatalogSpec(resource="doinhommarketing", model=FoxiaMarketingTeam, fields=("id", "name")),
    CatalogSpec(resource="kenhmarketing", model=FoxiaChannel, fields=("id", "name")),
    CatalogSpec(resource="thuonghieu", model=FoxiaBrand, fields=("id", "name")),
    CatalogSpec(
        resource="giaidoandonhang",
        model=FoxiaStage,
        fields=("id", "name", "phanloai"),
        scalars={"phanloai": "phanloai"},
    ),
    CatalogSpec(
        resource="crm.team",
        model=FoxiaSaleTeam,
        fields=("id", "name", "congtycon_id"),
        relations={"branch": ("congtycon_id", FoxiaBranch)},
    ),
    CatalogSpec(
        resource="res.users",
        model=FoxiaUser,
        fields=("id", "login", "name", "active", "doinhommarketing_id", "sale_team_id"),
        scalars={"login": "login", "is_active_foxia": "active"},
        relations={
            "marketing_team": ("doinhommarketing_id", FoxiaMarketingTeam),
            "sale_team": ("sale_team_id", FoxiaSaleTeam),
        },
    ),
    CatalogSpec(
        resource="utm.source",
        model=FoxiaSource,
        fields=(
            "id",
            "name",
            "thuonghieu_id",
            "kenhmarketing_id",
            "nhanvienmarketing_id",
            "doinhommarketing_id",
            "congtycon_id",
        ),
        relations={
            "brand": ("thuonghieu_id", FoxiaBrand),
            "channel": ("kenhmarketing_id", FoxiaChannel),
            "marketer": ("nhanvienmarketing_id", FoxiaUser),
            "marketing_team": ("doinhommarketing_id", FoxiaMarketingTeam),
            "branch": ("congtycon_id", FoxiaBranch),
        },
    ),
)
