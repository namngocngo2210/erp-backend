# Chất lượng cuộc gọi: lọc theo trạng thái sale + xuất Excel

**Ngày:** 2026-08-29 · Bổ sung cho [2026-07-30-call-quality-scoring-design.md](2026-07-30-call-quality-scoring-design.md)

## 1. Vấn đề

Bộ lọc trạng thái sale đã có sẵn nhưng **chỉ lọc bảng danh sách** ở tab "Cuộc
gọi". Chọn "Tư vấn thêm" xong, điểm trung bình và bảng tiêu chí ở tab "Tổng
quan" vẫn là của toàn bộ — nên không trả lời được câu hỏi thật sự cần:
*cuộc chốt được đơn khác gì cuộc khách suy nghĩ thêm?*

Trang cũng chưa có đường mang số liệu ra ngoài để họp/đào tạo.

## 2. Quyết định

### 2.1 Bộ lọc áp cho cả trang

`sales_status` thành tham số chung của 4 endpoint: `summary`, `trend`, `agents`,
`filters` (endpoint `calls` đã có). Ô lọc chuyển từ toolbar của bảng lên
**header, cạnh bộ chọn ngày** — vị trí phản ánh đúng phạm vi ảnh hưởng.

Ba ô còn lại (sale / SKU / tiêu chí) **ở lại toolbar bảng**: chúng chỉ lọc danh
sách. Nút "Bỏ lọc" của bảng cũng chỉ xoá ba ô đó — bấm nó mà điểm TB ở tab khác
đổi theo là hành vi bất ngờ.

### 2.2 Trạng thái đọc LIVE, không phải bản chụp

Bộ lọc và cột trạng thái đọc `OrderAssessment.sales_status_code` — trạng thái
**hiện tại** của đơn — thay cho `CallQualityScore.sale_sales_status_code` (bản
chụp lúc chấm).

| | Bản chụp | Live |
|---|---|---|
| Số khớp trang Đơn hàng | không | **có** |
| Báo cáo kỳ cũ ổn định | **có** | không |
| Đo được lệch AI vs sale | **có** | không |

Chọn live vì mục đích chính của bộ lọc là *so sánh nhóm hội thoại*, mà nhóm phải
định nghĩa giống hệt trang Đơn hàng thì người dùng mới tin. Bản chụp **không bị
xoá**: nó vẫn nuôi khối "đối chiếu chéo" ở màn chi tiết — nơi mục đích ngược lại,
là ĐO ĐỘ LỆCH. Khi hai giá trị khác nhau, chi tiết bật cờ
`sales_status_changed_since` để người đọc biết vì sao thấy hai mã.

⚠️ **Đổi hành vi:** số cuộc theo từng trạng thái sẽ khác con số hiện tại, và báo
cáo kỳ cũ sẽ đổi theo thời gian khi sale sửa trạng thái. Phải báo trước.

### 2.3 Dòng "độ phủ" CỐ Ý không theo bộ lọc

`coverage` trả lời câu hỏi hạ tầng — *tổng đài có đang mất file ghi âm không* —
nên lọc nó theo trạng thái sale làm tỷ lệ "có ghi âm" mất ý nghĩa. Backend trả
thêm cờ `ignores_filters` để giao diện nói rõ điều đó, tránh việc người dùng thấy
số ở dòng này lớn hơn thẻ "số cuộc đã chấm" rồi tưởng một trong hai bị sai.

### 2.4 Ô lọc thu hẹp bất đối xứng

Ô **sale** và ô **SKU** thu hẹp theo trạng thái đang chọn (chọn một trạng thái
rồi thấy tên sale không có cuộc nào trong đó là bộ lọc nói dối). Danh sách
**trạng thái** thì không — thu hẹp theo chính nó sẽ khoá người dùng lại ở lựa
chọn hiện tại.

## 3. Xuất Excel

Chạy nền qua engine `apps.exports`, `kind = "call_quality"`, trần **20.000 cuộc**
mỗi lần. Ba sheet cho ba người đọc khác nhau:

| Sheet | Nội dung | Dành cho |
|---|---|---|
| Cuộc gọi | 1 dòng/cuộc: ngày, sale, khách, SĐT, SKU, đơn, trạng thái, thời lượng, điểm tổng + 4 khối, cờ đỏ, wpm, talk ratio, nhận xét | quản lý lọc/dò |
| Điểm tiêu chí | 1 dòng/(cuộc × tiêu chí): điểm 0–2, cờ `na`, trích dẫn | team lead lấy ví dụ đi dạy |
| Tổng hợp | thẻ số, điểm TB từng tiêu chí, đếm cờ đỏ, phân rã theo trạng thái sale | dán vào báo cáo tuần |

Cả ba sheet đi qua **cùng một bộ lọc**: xuất kèm lọc SKU mà ô "số cuộc đã chấm"
ở sheet 3 vẫn là con số toàn kỳ thì file tự mâu thuẫn với chính nó.

Ô điểm của tiêu chí `na` để **trống**, không ghi 0: 0 là "làm nhưng sai", `na` là
"không có cơ hội làm" — gộp lại là bóp méo số liệu.

### 3.1 Quyền mới `call-quality:export`

Tách khỏi `call-quality:view`: xem trên màn hình khác với gom SĐT khách + trích
dẫn transcript (có thông tin bệnh khách kể) của cả kỳ vào một tệp mang đi được.
Migration `users/0044` seed nhưng **cố ý không gán role nào** — admin cấp tay,
đúng cách `0038` làm với `contacts:export`.

## 4. Chống lệch màn hình ↔ file

Bộ lọc dựng ở **một chỗ duy nhất**: `quality_reports.filter_calls(period,
filters)`. Cả view danh sách lẫn job xuất đều gọi hàm này, và khoá của `filters`
trùng đúng tên query param nên frontend gửi thẳng bộ lọc đang áp thay vì dịch
sang bộ tên thứ hai. `TestCallQualityExportParity` khoá lại giao kèo đó bằng cách
so tập cuộc gọi của sheet 1 với tập endpoint danh sách trả về, trên 5 bộ lọc.

Đây là bài học trực tiếp từ trang Đơn hàng, nơi bộ lọc bị chép sang 5 endpoint và
file Excel âm thầm trôi khỏi màn hình.

## 5. Thay đổi ở engine export

`StreamingXlsxWriter` được thêm hai method, không đổi hành vi cũ:

- `set_sheet_title(title)` — đổi tên sheet đầu (engine đặt theo `job.kind`).
- `start_sheet(title)` — mở sheet mới, vẫn ghi streaming.

Định nghĩa export một sheet không cần gọi hàm nào trong hai hàm này.

## 6. Thứ tự deploy

1. `migrate` (users/0044 — chỉ seed một dòng Permission, an toàn chạy trước).
2. Roll code (`desi-web` + `desi-worker` — worker cần định nghĩa export mới).
3. Cấp quyền `call-quality:export` cho role quản lý trên UI Phân quyền. **Chưa
   cấp thì không ai thấy nút Xuất Excel** (trừ Administrator).
4. Báo trước cho người dùng về mục 2.2: số theo trạng thái sẽ đổi.
