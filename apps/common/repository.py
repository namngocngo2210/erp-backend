"""Generic, soft-delete-aware repository base.

Most app repositories repeat the same primitives: filter out soft-deleted rows,
fetch-by-id-or-none, slice for pagination, soft-delete a row. ``BaseRepository``
centralises those so concrete repositories only add domain-specific queries.

Soft delete is detected automatically: if the model has a ``deleted_at`` field,
``active()`` excludes rows where it is set and ``soft_delete()`` stamps it.

Example::

    class CustomerRepository(BaseRepository[Customer]):
        model = Customer

        @classmethod
        def search(cls, q: str):
            return cls.active().filter(full_name__icontains=q)
"""

from __future__ import annotations

from typing import Generic, TypeVar

from django.db import models
from django.utils import timezone

TModel = TypeVar("TModel", bound=models.Model)


class BaseRepository(Generic[TModel]):
    #: Concrete subclasses must set this to their Django model.
    model: type[TModel]

    @classmethod
    def _has_soft_delete(cls) -> bool:
        return any(f.name == "deleted_at" for f in cls.model._meta.get_fields())

    @classmethod
    def all_with_deleted(cls) -> models.QuerySet[TModel]:
        return cls.model._default_manager.all()

    @classmethod
    def active(cls) -> models.QuerySet[TModel]:
        """Queryset excluding soft-deleted rows (if the model supports it)."""
        qs = cls.model._default_manager.all()
        if cls._has_soft_delete():
            qs = qs.filter(deleted_at__isnull=True)
        return qs

    @classmethod
    def get_or_none(cls, pk) -> TModel | None:
        return cls.active().filter(pk=pk).first()

    @classmethod
    def exists(cls, **filters) -> bool:
        return cls.active().filter(**filters).exists()

    @staticmethod
    def paginate(queryset: models.QuerySet[TModel], *, limit: int, offset: int):
        """Slice a queryset for offset pagination (caller validates the bounds)."""
        return queryset[offset : offset + limit]

    @classmethod
    def soft_delete(cls, obj: TModel) -> TModel:
        if not cls._has_soft_delete():
            raise NotImplementedError(
                f"{cls.model.__name__} has no 'deleted_at' field; cannot soft-delete."
            )
        # _has_soft_delete() ở trên đã bảo đảm field tồn tại; TModel bound theo
        # models.Model nên mypy không thấy được field động này.
        obj.deleted_at = timezone.now()  # type: ignore[attr-defined]
        obj.save(update_fields=["deleted_at"])
        return obj
