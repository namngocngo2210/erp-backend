import base64
from unittest.mock import patch
from django.test import TestCase
from rest_framework.test import APIClient
from apps.hr.models import CompanyLocation
from apps.hr.tests.factories import create_employee
from apps.users.tests.helpers import make_user_with_sections

PHOTO = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff" + b"fakejpeg").decode()


class CheckinApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        CompanyLocation.objects.create(name="HQ", latitude=10.0, longitude=106.0, radius_m=150)
        self.user = make_user_with_sections("nv-checkin", ["orders"])  # NO employees:* perms
        self.emp = create_employee(user=self.user, employee_code="NV0001", full_name="A")

    @patch("apps.hr.application.services.attendance_checkin_service.put_photo", return_value="k.jpg")
    def test_checkin_inside_radius(self, _put):
        self.client.force_authenticate(user=self.user)
        r = self.client.post("/api/hr/attendance/check-in",
            {"latitude": 10.0, "longitude": 106.0, "photo": PHOTO}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["kind"], "check_in")

    @patch("apps.hr.application.services.attendance_checkin_service.put_photo", return_value="k.jpg")
    def test_checkin_outside_radius_422(self, _put):
        self.client.force_authenticate(user=self.user)
        r = self.client.post("/api/hr/attendance/check-in",
            {"latitude": 20.0, "longitude": 106.0, "photo": PHOTO}, format="json")
        self.assertEqual(r.status_code, 422)
        self.assertIn("distance_m", r.data)

    def test_no_employee_404(self):
        other = make_user_with_sections("no-emp", ["orders"])
        self.client.force_authenticate(user=other)
        r = self.client.post("/api/hr/attendance/check-in",
            {"latitude": 10.0, "longitude": 106.0, "photo": PHOTO}, format="json")
        self.assertEqual(r.status_code, 404)

    def test_config_returns_locations(self):
        self.client.force_authenticate(user=self.user)
        r = self.client.get("/api/hr/attendance/config")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["locations"]), 1)

    @patch("apps.hr.application.services.attendance_checkin_service.put_photo", return_value="k.jpg")
    def test_non_image_photo_rejected(self, _put):
        self.client.force_authenticate(user=self.user)
        bad = "data:image/jpeg;base64," + base64.b64encode(b"<html>not an image").decode()
        r = self.client.post("/api/hr/attendance/check-in",
            {"latitude": 10.0, "longitude": 106.0, "photo": bad}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_invalid_latitude_400(self):
        self.client.force_authenticate(user=self.user)
        r = self.client.post("/api/hr/attendance/check-in",
            {"latitude": 9999, "longitude": 106.0, "photo": PHOTO}, format="json")
        self.assertEqual(r.status_code, 400)
