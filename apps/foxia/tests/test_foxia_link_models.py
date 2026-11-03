"""Bảng liên kết Foxia (kênh × loại nguồn) — ràng buộc và hành vi xoá theo tầng.

Hàng kênh là bảng riêng: hàng phải tồn tại được TRƯỚC khi có ô nào điền, và
"bỏ một hàng thì id trong hàng đó biến mất" phải là ràng buộc DB chứ không phải
một vòng dọn dẹp trong service có thể quên.
"""

import pytest
from django.db import IntegrityError

from apps.channels_core.models import Channel
from apps.foxia.models import FoxiaLinkCell, FoxiaLinkChannel, FoxiaLinkKind

pytestmark = pytest.mark.django_db


def _cell(code: str, kind: str = "landing", foxia_source_id: int = 1234) -> FoxiaLinkCell:
    channel = Channel.objects.create(code=code, name=f"Kênh {code}")
    return FoxiaLinkCell.objects.create(
        link_channel=FoxiaLinkChannel.objects.create(channel=channel),
        kind=kind,
        foxia_source_id=foxia_source_id,
    )


def test_bo_hang_xoa_luon_o_cua_hang_do():
    cell = _cell("kenh-a")
    other = _cell("kenh-b")

    cell.link_channel.delete()

    assert not FoxiaLinkCell.objects.filter(id=cell.id).exists()
    assert FoxiaLinkCell.objects.filter(id=other.id).exists()


def test_mot_cap_kenh_loai_nguon_chi_co_mot_o():
    cell = _cell("kenh-a")

    with pytest.raises(IntegrityError):
        FoxiaLinkCell.objects.create(
            link_channel=cell.link_channel, kind=cell.kind, foxia_source_id=9999
        )


def test_mot_kenh_dien_duoc_du_cac_cot():
    cell = _cell("kenh-a")
    for kind in FoxiaLinkKind:
        if kind != cell.kind:
            FoxiaLinkCell.objects.create(link_channel=cell.link_channel, kind=kind, foxia_source_id=1)

    assert FoxiaLinkCell.objects.filter(link_channel=cell.link_channel).count() == len(FoxiaLinkKind)


def test_mot_kenh_chi_la_mot_hang():
    channel = Channel.objects.create(code="kenh-a", name="Kênh A")
    FoxiaLinkChannel.objects.create(channel=channel)

    with pytest.raises(IntegrityError):
        FoxiaLinkChannel.objects.create(channel=channel)
