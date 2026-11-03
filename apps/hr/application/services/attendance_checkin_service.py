from __future__ import annotations

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.hr.application.services.attendance_service import AttendanceService
from apps.hr.domain.geo import nearest_location
from apps.hr.infrastructure.photo_storage import put_photo
from apps.hr.models import AttendanceEvent, AttendanceEventKind
from apps.hr.repositories import AttendanceEventRepository, CompanyLocationRepository


class OutsideGeofenceError(Exception):
    def __init__(self, distance_m: int, radius_m: int):
        self.distance_m = distance_m
        self.radius_m = radius_m
        super().__init__("Ngoài bán kính cho phép.")


class AlreadyCompleteError(Exception):
    pass


class AttendanceCheckinService:
    @staticmethod
    @transaction.atomic
    def check_in(*, employee_id: int, latitude: float, longitude: float,
                 photo_bytes: bytes, content_type: str = "image/jpeg") -> AttendanceEvent:
        now = timezone.now()
        work_date = timezone.localdate(now)
        events = list(AttendanceEventRepository.for_day(employee_id, work_date))
        has_in = any(e.kind == AttendanceEventKind.CHECK_IN for e in events)
        has_out = any(e.kind == AttendanceEventKind.CHECK_OUT for e in events)
        if has_in and has_out:
            raise AlreadyCompleteError("Bạn đã chấm công đủ hôm nay.")
        kind = AttendanceEventKind.CHECK_OUT if has_in else AttendanceEventKind.CHECK_IN

        locs = [
            {"id": cl.id, "latitude": cl.latitude, "longitude": cl.longitude, "radius_m": cl.radius_m}
            for cl in CompanyLocationRepository.active()
        ]
        loc, dist = nearest_location(latitude, longitude, locs)
        if loc is None or dist > loc["radius_m"]:
            raise OutsideGeofenceError(
                distance_m=int(dist) if dist is not None else -1,
                radius_m=loc["radius_m"] if loc else 0,
            )

        key = f"attendance/{employee_id}/{work_date.isoformat()}/{kind}-{int(now.timestamp())}.jpg"
        put_photo(key, photo_bytes, content_type)

        try:
            with transaction.atomic():
                event = AttendanceEvent.objects.create(
                    employee_id=employee_id, kind=kind, occurred_at=now, work_date=work_date,
                    photo_key=key, latitude=latitude, longitude=longitude,
                    distance_m=int(dist), location_id=loc["id"],
                )
        except IntegrityError as exc:
            # Race loser: a concurrent request inserted the same kind first.
            raise AlreadyCompleteError("Bạn vừa chấm công loại này rồi.") from exc
        local_time = timezone.localtime(now).time()
        if kind == AttendanceEventKind.CHECK_IN:
            AttendanceService.record(employee_id, work_date=work_date, check_in=local_time, source="self")
        else:
            AttendanceService.record(employee_id, work_date=work_date, check_out=local_time, source="self")
        return event
