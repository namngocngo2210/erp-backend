"""Tên 5 bước chăm sóc sau bán được migration `contact_flow/0002` seed sẵn.

CỐ Ý không import ngược vào migration (migration phải tự chứa, không phụ thuộc code
app có thể đổi sau này) — bù lại `SeededCareStagesTests` so danh sách này với những gì
migration THỰC SỰ ghi vào DB, nên hai bên lệch nhau là test đỏ ngay.

Test nào đếm TUYỆT ĐỐI số bước (vd import workbook vào "DB cấu hình rỗng") phải xoá
đúng nhóm này trước, không thì tiền đề của phép đếm đã sai từ đầu.
"""

SEEDED_CARE_STAGE_NAMES = ("HDSD", "D3", "D7", "D15", "D30")
