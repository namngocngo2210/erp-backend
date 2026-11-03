# Chấm chất lượng cuộc gọi sale bằng Gemini + trang QA trên CRM

Ngày: 2026-07-30 · Trạng thái: **Đã hiện thực** (chờ bật kill switch trên prod) ·
Liên quan:
`docs/zenify-call-transcripts.md`,
`docs/superpowers/specs/2026-07-24-call-transcript-assessment-autofill-design.md`

---

## 1. Bối cảnh

Đã có sẵn 3 luồng trong `apps/callcenter` (xem `docs/zenify-call-transcripts.md`):

| Luồng | Module | Việc |
|---|---|---|
| 1 | `sync_service.py` | Kéo lịch sử cuộc gọi Zenify → `callcenter_call_records`, khớp đơn theo SĐT |
| 2 | `transcribe.py` | Gemini nghe file ghi âm → `transcript` (đã phân vai `Nhân viên:` / `Khách:`) |
| 3 | `extract.py` | Gemini đọc transcript → điền chân dung khách vào `OrderAssessment` |

⇒ Việc mới là **luồng 4: `score`** — đọc transcript, chấm theo khung kịch bản
mẫu, và hiển thị trên một trang QA trong CRM.

**Quyết định: dữ liệu QA KHÔNG đẩy sang Lark Bitable.** Hạ tầng Lark của hệ
thống đã đủ (`lark_open_api.py`, `lark_bitable_client.py`, và mẫu đồng bộ cửa sổ
7 ngày ở `lark_ads_report_sync.py`), nhưng Bitable không có phân quyền theo
người, không có chỗ để trích dẫn bằng chứng mà không lộ ra cả phòng, và không có
đường phản hồi khi AI chấm sai. Ba thứ đó là điều kiện cần để QA dùng được, nên
chọn trang trong CRM (§6). `ops_alerts.py` vẫn dùng — nhưng chỉ để **thông báo**
cờ đỏ, không phải đồng bộ dữ liệu.

## 2. Phạm vi

**Trong phạm vi**

- Mã hoá khung kịch bản mẫu (ảnh khung 4 bước) thành rubric có version trong code.
- Chấm điểm text-only bằng Gemini structured output, mỗi tiêu chí bắt buộc kèm
  trích dẫn nguyên văn.
- Chỉ số đo bằng code (không tốn AI): thời lượng, tốc độ nói (WPM), tỷ lệ
  sale/khách nói, độ dài mở đầu.
- Cờ đỏ tuân thủ (chống chỉ định y tế, cam kết chữa bệnh, hứa sai chính sách).
- Chỉ chấm & hiển thị cuộc có ghi âm + có transcript; luôn hiện số cuộc
  bên cạnh mọi điểm trung bình.
- Trang "Chất lượng cuộc gọi" trong CRM: điểm chung cả team, bảng 14 tiêu chí
  sắp theo điểm thấp dần, chi tiết từng cuộc kèm trích dẫn.
- Cảnh báo Lark khi có cờ đỏ (chỉ là thông báo, dữ liệu vẫn ở CRM).

**Ngoài phạm vi (giai đoạn sau)**

- Chấm giọng nói/ngữ điệu từ audio (xem §4.4).
- **Chấm điểm theo từng nhân viên** — v1 chỉ đánh giá chung cả team (§6).
- Gắn điểm QA vào KPI/lương.
- Đẩy dữ liệu QA sang Lark Bitable — bỏ hẳn, xem §6.

## 3. Rubric — `apps/callcenter/qa_rubric.py`

Nguyên tắc kiến trúc: rubric là **hợp đồng dữ liệu**, tách khỏi phần gọi API —
đúng cách `apps/orders/ai_extract_contract.py` đang làm với extract. Từ rubric
sinh ra cả prompt lẫn `response_schema`, không có chỗ nào lệch nhau.

`RUBRIC_VERSION = "v1"` — **mọi so sánh điểm chỉ hợp lệ trong cùng version**.
Sửa tiêu chí ⇒ bump version, điểm cũ giữ nguyên, không backfill lẫn lộn.

### 3.1 Bốn khối, 14 tiêu chí

| Mã | Tiêu chí | Nguồn (ảnh) | Chấm được từ text? |
|---|---|---|---|
| **A. Mở đầu & xin phép — trọng số 15** ||||
| `a1_greeting` | Chào hỏi đúng chuẩn, xưng danh DELAX, xin phép tư vấn | Mở đầu & Xin phép | ✅ |
| `a2_purpose` | Nói rõ lý do gọi (khách để lại thông tin về sản phẩm X) | Mẫu cột 3 | ✅ |
| `a3_attitude` | Thái độ tích cực, tôn trọng, xưng hô phù hợp | Thái độ | ⚠️ một phần |
| **B. Khai thác nhu cầu — trọng số 30** ||||
| `b1_discovery` | Hỏi đủ 4 câu chốt: mua cho ai / đau vùng nào / đau lâu chưa / độ tuổi | Khai thác nhu cầu | ✅ (đếm được) |
| `b2_listening` | Không ngắt lời, có tóm tắt/xác nhận lại ý khách | Lắng nghe chủ động | ✅ |
| `b3_empathy` | Đồng cảm khi khách than phiền | Thấu cảm | ✅ |
| `b4_contraindication` | Nhận diện nhóm chống chỉ định (tim có thiết bị cấy, loãng xương, ung thư, tiểu đường nặng, mới phẫu thuật) | Lưu ý đối tượng | ✅ |
| **C. Tư vấn & xử lý từ chối — trọng số 35** ||||
| `c1_structure` | Đúng chuỗi: đau ở đâu → nguyên nhân đời thường → cơ chế sản phẩm → lợi ích | Cột Mẫu bước 3 | ✅ |
| `c2_value_prop` | Nói **lợi ích khách nhận được**, không thao thao bất tuyệt tính năng | Value Proposition | ✅ |
| `c3_knowledge` | Nắm đúng chính sách, giá, ưu đãi, chế độ bảo hành | Kiến thức sản phẩm | ✅ |
| `c4_objection` | Khách chê đắt / so sánh đối thủ / "để suy nghĩ thêm" — sale bình tĩnh xử lý, không bỏ cuộc sớm | Xử lý từ chối 1 | ✅ |
| `c5_real_reason` | Làm rõ **lý do từ chối thật** đằng sau | Xử lý từ chối 2 | ✅ |
| **D. Chốt đơn — trọng số 20** ||||
| `d1_proactive_close` | Chủ động đưa đề nghị chốt | Chốt đơn | ✅ |
| `d2_info_complete` | Xác nhận đủ: tên, SĐT, địa chỉ, giá + quà, thời gian giao 2–7 ngày, khung giờ nhận | Mẫu bước 4 | ✅ (checklist) |
| `d3_reassure` | Trấn an: được bóc kiểm tra rồi mới thanh toán, giao trong giờ hành chính | Mẫu bước 4 | ✅ |

### 3.2 Thang điểm

Mỗi tiêu chí: `0` (không làm) · `1` (làm nhưng thiếu/sai) · `2` (làm tốt) · `na`
(cuộc gọi không đi tới bước đó — ví dụ khách cúp máy ở phút 1 thì khối D là `na`).

```
điểm_khối = Σ điểm / (2 × số tiêu chí KHÔNG na) × trọng_số_khối
tổng_điểm = Σ điểm_khối          # thang 100
```

Loại `na` khỏi mẫu số thay vì cho 0 — nếu không, cuộc gọi khách bận hẹn gọi lại
sẽ bị chấm như sale kém, và bảng xếp hạng sẽ phạt đúng người xui chứ không phải
người dở. Khối bị `na` toàn bộ ⇒ trọng số chia lại cho các khối còn lại.

### 3.3 Cờ đỏ (tách khỏi điểm)

Không trừ điểm — báo riêng, vì đây là chuyện tuân thủ chứ không phải kỹ năng:

| Mã | Ý nghĩa |
|---|---|
| `rf_medical_claim` | Cam kết chữa khỏi bệnh / nói quá công dụng y tế |
| `rf_ignored_contraindication` | Khách khai bệnh nền chống chỉ định mà sale vẫn chốt, không cảnh báo |
| `rf_false_policy` | Hứa sai giá / bảo hành / thời gian giao |
| `rf_disrespect` | Thái độ tiêu cực, cãi hoặc gắt với khách |

Mỗi cờ bắt buộc kèm trích dẫn nguyên văn; không có trích dẫn ⇒ bỏ cờ.

### 3.4 Chống "điểm ảo"

Bài học từ `extract.py`: LLM sẽ điền cho đầy nếu không bị chặn.

- `temperature=0`, structured output theo `response_schema` sinh từ rubric.
- **Mỗi tiêu chí có điểm ≥ 1 phải kèm `evidence` là câu nói NGUYÊN VĂN** trong
  transcript. Post-process kiểm tra chuỗi evidence có thật sự nằm trong
  transcript không (so khớp sau khi bỏ dấu câu/khoảng trắng thừa); không khớp ⇒
  hạ điểm tiêu chí đó về 0 và ghi vào `rejected`.
- Transcript < `QA_MIN_TRANSCRIPT_CHARS` (mặc định 400) ⇒ `SKIPPED`, không chấm.

## 4. Kiến trúc luồng 4 — `score`

### 4.1 Hàng đợi

Bám nguyên pattern `extract.py` (đã chịu được 2 sự cố quota, không phát minh lại):

- Thêm `score_status` / `score_error` / `score_attempts` vào `CallRecord`,
  index `(score_status, id)`.
- `_claim_pending_batch` với `select_for_update(skip_locked=True)`, set
  `PROCESSING` trong transaction rồi gọi Gemini **ngoài** transaction.
- Cooldown key **riêng**: `gemini:score:cooldown` — 429 của QA không được khoá
  luôn transcribe/extract.
- Lỗi `transient` (429/5xx/timeout) **không đốt** `score_attempts`.
- Requeue record kẹt `PROCESSING` > 30 phút.

### 4.2 Chọn cuộc gọi để chấm

Đo thật trên prod 2026-07-30 (30 ngày): 2515 cuộc → 1545 có ghi âm → 1533
transcribe xong → **498 cuộc** dài ≥ 2 phút. Tức ~17 cuộc/ngày.

Với lưu lượng đó, **chấm hết** — không lấy mẫu, không xếp ưu tiên. Hai điều kiện
duy nhất:

| Env | Mặc định | Ý nghĩa |
|---|---|---|
| `QA_MIN_DURATION_SECONDS` | `120` | "Ưu tiên những cuộc gọi trên 2p" trong khung |
| `QA_MIN_TRANSCRIPT_CHARS` | `400` | Dưới ngưỡng là không có nội dung tư vấn |

Lưu ý cuối ảnh dặn ưu tiên soi cuộc chốt đơn thành công và cuộc khách suy nghĩ
thêm. Dữ liệu cho thấy **không cần cơ chế ưu tiên nào**: trong 498 cuộc đủ điều
kiện, trạng thái đơn khớp là `created_order` 373 · `need_more_consulting` 43 ·
`not_buying` 35 · `call_back_later` 9 — tức **84% đã nằm sẵn trong hai nhóm cần
soi**. Cứ chấm hết là bao trọn, mà còn được thêm nhóm `not_buying` (35 cuộc) —
nhóm quý nhất để đánh giá tiêu chí `c5_real_reason` (làm rõ lý do từ chối thật).

Cuộc chưa khớp đơn **vẫn chấm được**, khác extract — extract cần `order_id` vì
có chỗ để ghi, còn QA thì không ghi ngược vào đơn.

### 4.3 Đối chiếu chéo với dữ liệu sale tự nhập

Đây là thứ **không có trong khung giấy** nhưng hệ thống làm được, và nó đáng giá
hơn phần chấm điểm: transcript là bằng chứng khách quan để soi lại chính những
gì sale đã tự khai vào CRM.

**a. Lý do không chốt có thật không** — `NOT_CLOSED_REASON_OPTIONS`
(`crm_catalog.py:47`) đã có sẵn 14 mã: `too_expensive`, `competitor_cheaper`,
`family_disapproved`, `waiting_for_money`, `spouse_not_approved`… Đúng bằng câu
hỏi trong khung: *"Có làm rõ lý do từ chối thật sự đằng sau không?"*

Cho Gemini suy ra `not_closed_reason_code` **từ transcript** (kèm trích dẫn bắt
buộc, đúng luật §3.4), rồi so với mã sale đã chọn:

| Kết quả | Ý nghĩa |
|---|---|
| Trùng nhau | Sale ghi nhận đúng |
| Sale để trống, transcript có | Sale không ghi nhận — báo cáo nguyên nhân mất đơn đang thiếu dữ liệu |
| Lệch nhau | Cần xem lại: hoặc sale ghi ẩu, hoặc sale đọc được ý sâu hơn AI |
| Cả hai trống | Đúng ý *"chưa làm rõ lý do từ chối thật sự"* ⇒ trừ điểm `c5_real_reason` |

Đây là lần đầu tiên báo cáo nguyên nhân mất đơn có **đối chứng độc lập** — hiện
tại nó hoàn toàn phụ thuộc vào việc sale tự khai, mà chính sale là người có động
cơ khai cho đẹp.

**b. Bệnh nền chống chỉ định** — vừa có mã `underlying_condition` ("Khách bị bệnh
nền", thêm ngày 2026-07-29). Cờ đỏ `rf_ignored_contraindication` do đó
**không cần AI phán** ở bước cuối, mà kiểm chứng được bằng dữ liệu:

```
transcript có bệnh thuộc danh sách chống chỉ định
  AND sales_status_code ∈ {closed, closed_express, closed_scheduled_date, created_order}
  AND sales_status_code ≠ underlying_condition
⇒ cờ đỏ
```

AI chỉ làm đúng một việc nó giỏi: nhận diện khách có nhắc tới bệnh nền nào trong
danh sách hay không. Phần suy luận "có vi phạm hay không" là code, deterministic,
giải thích được cho sale khi họ khiếu nại.

**c. Chỉ số phái sinh miễn phí**: tỷ lệ "nói đúng kịch bản" theo từng sale, so
với tỷ lệ chốt thật của chính sale đó. Nếu hai đường không tương quan thì khung
kịch bản cần sửa, chứ không phải sale sai — dữ liệu này chỉ có khi chấm 100%
cuộc gọi (§4.6).

### 4.4 Phần transcript KHÔNG chấm được

Cột "Kỹ năng đánh giá" có 2 tiêu chí thuộc về **âm thanh**: *Tông giọng & Tốc
độ* (giọng rõ ràng, tự tin, âm lượng vừa đủ) và một phần *Thái độ / năng lượng*.
Text không mang thông tin này — bắt Gemini chấm từ transcript là bịa.

Cách xử lý:

1. **Đo bằng code, không tốn AI** — transcript đã phân vai `Nhân viên:` /
   `Khách:` nên tính được:
   - `wpm = số từ của Nhân viên / (acd_time/60)` → tốc độ nói. Ngưỡng tham khảo
     tiếng Việt: < 110 quá chậm, > 190 quá nhanh (chốt lại sau khi calibrate).
   - `talk_ratio = từ của Nhân viên / tổng số từ` → sale nói > 75% là dấu hiệu
     độc thoại, đúng lỗi "thao thao bất tuyệt" trong khung.
   - `time_to_first_customer_turn` → mở đầu có dài dòng không.
   Ba số này khách quan, ổn định, và **không bao giờ ảo** — dùng làm đối chứng
   cho điểm AI.
2. Tiêu chí thuần âm thanh để `na` ở v1, trang QA hiện rõ "chưa đánh giá" chứ
   không lấy điểm khác bù vào.
3. Giai đoạn sau: chấm prosody ngay tại **luồng 2**, khi file ghi âm còn trong
   tay — URL Zenify hết hạn theo retention nên không có đường quay lại. Đã bật
   billing nên rào cản không còn là chi phí (audio đắt hơn text nhiều lần nhưng
   vẫn nhỏ so với giá trị), mà là **thiết kế**: gộp chấm prosody vào cùng lần
   gọi transcribe để không phải tải file 2 lần, và phải làm sao lỗi chấm điểm
   không kéo theo hỏng transcript. Tách thành spec riêng sau khi v1 chạy ổn.

### 4.5 Lưu kết quả — bảng mới

```python
class CallQualityScore(models.Model):
    call_record = models.ForeignKey(CallRecord, on_delete=models.CASCADE,
                                    related_name="quality_scores")
    rubric_version = models.CharField(max_length=16)
    model_name     = models.CharField(max_length=64)

    # Denormalize để report không phải join CallRecord/Order (bảng này bị quét
    # theo agent × ngày liên tục cho grain tổng hợp).
    agent_id   = models.CharField(max_length=100, db_index=True)
    agent_name = models.CharField(max_length=255, blank=True, default="")
    call_date  = models.DateField()               # theo Asia/Bangkok
    order_id   = models.BigIntegerField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(default=0)

    total_score  = models.DecimalField(max_digits=5, decimal_places=2)
    block_scores = models.JSONField(default=dict)   # {"A": 12.5, "B": 24.0, ...}
    criteria     = models.JSONField(default=dict)   # {code: {score, na, evidence}}
    red_flags    = models.JSONField(default=list)
    rejected     = models.JSONField(default=dict)   # evidence không khớp transcript
    summary      = models.TextField(blank=True, default="")   # 2–3 câu
    coaching     = models.TextField(blank=True, default="")   # 1 điểm cần sửa

    wpm        = models.PositiveIntegerField(default=0)
    talk_ratio = models.DecimalField(max_digits=4, decimal_places=3, default=0)

    # Đối chiếu chéo (§4.3). `sale_*` chụp lại giá trị sale khai TẠI THỜI ĐIỂM
    # chấm — sale sửa status sau đó thì bản chụp vẫn giữ nguyên, nếu không thì
    # báo cáo "sale ghi ẩu" sẽ tự lành mỗi khi có người sửa lại cho đẹp.
    ai_not_closed_reason_code   = models.CharField(max_length=40, blank=True, default="")
    sale_not_closed_reason_code = models.CharField(max_length=40, blank=True, default="")
    sale_sales_status_code      = models.CharField(max_length=40, blank=True, default="")
    contraindications           = models.JSONField(default=list)  # bệnh nền AI nghe thấy

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "callcenter_call_quality_scores"
        constraints = [UniqueConstraint(fields=["call_record", "rubric_version"],
                                        name="callqa_record_version_uniq")]
        indexes = [Index(fields=["agent_id", "call_date"]),
                   Index(fields=["call_date"])]
```

Bảng riêng chứ không nhét thêm cột vào `CallRecord`: rubric sẽ thay đổi theo
thời gian, và ta cần giữ **cả điểm cũ lẫn điểm mới** để chứng minh chương trình
coaching có tác dụng. `unique(call_record, rubric_version)` cho phép chấm lại
bằng rubric mới mà không mất lịch sử.

### 4.6 Quota & chi phí

**Không còn là vấn đề.** ~17 cuộc/ngày ⇒ 17 request Gemini/ngày. Đo trên prod
2026-07-30: `transcribe_status` chỉ có DONE (1533) và SKIPPED (982), **không một
dòng FAILED, không một dòng PENDING** — hàng đợi transcribe hiện tại chưa bao giờ
chạm trần, kể cả với `GEMINI_MAX_RPM=8` / `GEMINI_MAX_RPD=200`.

Vì vậy hai việc từng nêu đều **hạ ưu tiên**:

- Nới `GEMINI_MAX_RPM` / `GEMINI_MAX_RPD` trong `.env` prod: nên làm cho đúng
  (billing đã bật, hai số này là counter Redis tự đặt từ thời free tier ở
  `docker-compose.prod.yml:122-123`, dùng chung toàn cụm) nhưng **không gấp** —
  lưu lượng thực tế còn xa trần.
- Van khoá chi phí theo số cuộc/ngày: bỏ hẳn. Ở mức 17 request/ngày với Flash,
  chi phí là tiếng ồn trong hoá đơn.

Vẫn đo token thật ở S1 để có con số thay vì ước lượng, và đối chiếu hoá đơn sau
tuần đầu.

**Vẫn giữ nguyên toàn bộ cơ chế phòng thủ**: cooldown key riêng
`gemini:score:cooldown`, `transient` không đốt attempt, requeue stuck. Trả phí
không làm 429/503 biến mất — chỉ làm chúng hiếm hơn. Và lưu lượng sẽ tăng nếu
39% cuộc đang mất ghi âm được khôi phục.

## 5. Phạm vi dữ liệu & mẫu số

### 5.1 Chỉ lấy cuộc có ghi âm → có transcript → đã chấm

Toàn bộ trang QA chỉ làm việc trên tập này, không đụng tới cuộc gọi khác:

```sql
FROM callcenter_call_records r
JOIN callcenter_call_quality_scores s ON s.call_record_id = r.id
WHERE r.recording_url <> ''
  AND r.transcribe_status = 2          -- DONE
  AND r.transcript <> ''
  AND s.rubric_version = <hiện hành>
```

Cuộc không có ghi âm, hoặc có ghi âm mà transcribe lỗi, **không xuất hiện ở đâu
trên trang** — không phải dòng trống, không phải điểm 0. Chúng chỉ được đếm ở
một chỗ duy nhất: ô "mẫu số" nói dưới đây.

### 5.2 Luôn hiện số cuộc bên cạnh điểm

Mọi con số trung bình trên trang đi kèm số cuộc tạo ra nó:

> Tiêu chí `c5_real_reason` — **0,7/2** *(412 cuộc)*

Điểm trung bình của một tiêu chí trên 12 cuộc và trên 400 cuộc là hai loại thông
tin khác nhau. Khi lọc theo tuần hoặc theo nhóm trạng thái đơn, mẫu tụt xuống
rất nhanh — số cuộc phải luôn ở đó để người đọc biết mình đang nhìn cái gì.

### 5.3 Một ô đếm cho vận hành

Ở góc trang, một dòng nhỏ cho team lead biết pipeline có đang khoẻ không:

> Kỳ này: **412** cuộc gọi · **380** có ghi âm · **366** có transcript · **352** đã chấm

Con số này không dùng để đánh giá sale. Nó chỉ trả lời một câu hỏi vận hành: nếu
"có ghi âm" tụt xa so với "cuộc gọi" thì tổng đài đang mất file — mà ghi âm mất
là **không lấy lại được**, phát hiện muộn thì mất trắng dữ liệu QA của ngày hôm
đó.

Ghi chú kỹ thuật: `TranscribeStatus.SKIPPED` (`models.py:19`) đang gộp *không có
file ghi âm* với *đàm thoại quá ngắn*. Muốn tách được hai cột đầu của dòng trên
thì đọc `recording_url` rỗng hay không, hoặc tách thành 2 mã status (S2).

## 6. Trang "Chất lượng cuộc gọi" trên CRM

**Phạm vi v1: đánh giá CHUNG cả team, không chấm theo từng nhân viên.** Câu hỏi
trang này trả lời là *"cả team đang yếu bước nào trong kịch bản"* — để sửa kịch
bản và mở buổi đào tạo đúng chỗ, không phải để xếp hạng người.

Quyết định này giải luôn vướng mắc dữ liệu: `agent_name` rỗng 100% và chỉ có 4
`agent_id` cho 2515 cuộc (extension dùng chung, không phải từng nhân viên). Chấm
theo người sẽ phải đi vòng qua `Order.staff` với một giả định chưa kiểm chứng
được — bỏ chiều đó đi thì không cần giả định nào cả.

Vẫn **lưu** `agent_id` / `agent_name` vào `CallQualityScore` (§4.5). Chúng gần
như miễn phí, và ngày tổng đài trả đúng định danh nhân viên thì dữ liệu lịch sử
đã sẵn ở đó — chỉ thêm màn hình, không phải chấm lại.

### 6.1 API — `apps/callcenter/transport/views`

Bám `transport/views/callcenter_views_impl.py` đang có:

| Method | Path | Việc |
|---|---|---|
| `GET` | `/api/callcenter/quality/summary` | Thẻ tổng + **điểm TB từng tiêu chí** theo khoảng ngày, kèm số cuộc làm mẫu số |
| `GET` | `/api/callcenter/quality/trend` | Điểm TB theo tuần (tổng + từng khối) để đo hiệu quả đào tạo |
| `GET` | `/api/callcenter/quality/calls` | Danh sách cuộc đã chấm (filter: ngày, khoảng điểm, có cờ đỏ, trạng thái đơn) |
| `GET` | `/api/callcenter/quality/calls/{id}` | Chi tiết 1 cuộc: điểm từng tiêu chí + **trích dẫn**, transcript, đối chiếu chéo |
| `POST` | `/api/callcenter/quality/calls/{id}/review` | Đánh dấu "AI chấm sai" + điểm đúng theo người |

**Phân quyền** — section mới `call-quality` trong RBAC hiện có, chỉ một mức
`call-quality:view` dành cho quản lý. Không có chuyện sale xem điểm của nhau vì
trang không hiển thị theo người. Đơn giản hơn hẳn phương án cũ: không phải lọc
queryset theo `agent_id`, không phải test rò rỉ chéo giữa các sale.

Transcript đầy đủ nằm sau cùng quyền đó — nó chứa SĐT và thông tin bệnh của
khách, không phải thứ mở cho toàn công ty.

**Hiệu năng**: truy vấn nặng nhất là điểm TB từng tiêu chí trên toàn kỳ, tức
quét `criteria` JSONB của vài trăm dòng — nhẹ ở quy mô 498 cuộc/30 ngày. Index
`(call_date)` của `CallQualityScore` là đủ. **Chưa cần** bảng rollup, chưa cần
index `(agent_id, start_time)` — cả hai chỉ sinh ra khi có màn hình theo người.

### 6.2 Frontend — `src/features/call-quality`

Theo đúng cấu trúc feature hiện có (xem `features/overview-report`):

```
src/features/call-quality/
  pages/CallQualityPage.tsx        # 2 tab
  components/
    QualitySummaryCards.tsx        # thẻ số tổng
    CriteriaBreakdown.tsx          # 14 tiêu chí sắp theo điểm thấp dần
    QualityTrendChart.tsx          # điểm TB theo tuần
    CallQualityTable.tsx           # danh sách cuộc gọi (data-table-shell)
    CallScoreDetailSheet.tsx       # sheet chi tiết 1 cuộc
  queries.ts                       # react-query hooks
src/lib/api/call-quality.ts        # tầng gọi API
```

Đăng ký route: thêm section `call-quality` vào nhóm **Báo cáo** trong
`src/app/modules.ts:167`, lazy import ở `routed-pages.tsx`, route
`reports/call-quality` bọc `<Guard section="call-quality">` trong
`routes.tsx:302`. Dùng lại `data-table-shell`, `date-filter`, `sheet`, `badge`,
`tabs` trong `components/ui` — không dựng component mới.

**Tab 1 — Tổng quan.** Thứ tự trên trang phản ánh thứ tự ra quyết định:

1. Thẻ số: số cuộc đã chấm · điểm TB · tỷ lệ đạt ≥ `QA_PASS_SCORE` · số cờ đỏ.
2. **Bảng 14 tiêu chí sắp theo điểm thấp dần** — đây là màn hình chính, không
   phải phụ. Dòng đầu bảng chính là nội dung buổi đào tạo tuần tới. Mỗi dòng có
   nút mở thẳng danh sách cuộc gọi bị điểm 0 ở tiêu chí đó, kèm trích dẫn — team
   lead có ngay ví dụ thật để dạy, không phải nói lý thuyết.
3. Biểu đồ điểm TB theo tuần, tách theo 4 khối. Sau buổi đào tạo về khối B thì
   đường khối B phải nhích lên — nếu không thì cách đào tạo sai, chứ không phải
   sale không tiếp thu.
4. Dòng đếm vận hành (§5.3) ở góc, cỡ chữ nhỏ.

**Tab 2 — Cuộc gọi**: bảng lọc được, mỗi dòng = 1 cuộc (ngày, mã đơn, trạng thái
đơn, thời lượng, tổng điểm, 4 điểm khối dạng chip màu, cờ đỏ). Click mở sheet.

**Sheet chi tiết** — màn hình quan trọng thứ hai, vì đây là thứ dùng để dạy:

- Điểm từng tiêu chí, mỗi dòng kèm **trích dẫn nguyên văn** làm căn cứ. Không có
  trích dẫn thì không hiện điểm — đúng luật §3.4.
- Ba chỉ số khách quan (tốc độ nói, tỷ lệ sale nói, thời lượng) đặt cạnh điểm AI.
- Khối đối chiếu chéo (§4.3): lý do không chốt AI nghe được vs sale đã khai;
  bệnh nền phát hiện được.
- Transcript đầy đủ, **highlight** đúng các đoạn được trích làm evidence.
- Nút "AI chấm sai" → ghi `CallQualityReview`, và là dữ liệu để sửa prompt v2.

### 6.3 Bảng phản hồi

```python
class CallQualityReview(models.Model):
    score       = models.ForeignKey(CallQualityScore, on_delete=models.CASCADE,
                                    related_name="reviews")
    reviewer    = models.ForeignKey("users.User", on_delete=models.PROTECT)
    criterion   = models.CharField(max_length=32, blank=True, default="")  # rỗng = cả cuộc
    ai_score    = models.PositiveSmallIntegerField(null=True, blank=True)
    human_score = models.PositiveSmallIntegerField(null=True, blank=True)
    note        = models.TextField(blank=True, default="")
    created_at  = models.DateTimeField(auto_now_add=True)
```

Không sửa đè lên `CallQualityScore`: điểm AI giữ nguyên để đo **AI sai bao nhiêu
%** theo thời gian. Báo cáo hiển thị điểm người khi có, điểm AI khi không. Đây
cũng là tập dữ liệu để calibrate prompt v2 — không có nó thì mãi phải tin AI mà
không chứng minh được.

### 6.4 Env mới

```
QA_SCORING_ENABLED=0                  # kill switch, bật sau smoke test prod
QA_SCORING_MODEL=gemini-flash-latest
QA_SCORING_BATCH=10
QA_SCORING_MAX_ATTEMPTS=3
QA_MIN_TRANSCRIPT_CHARS=400
QA_MIN_DURATION_SECONDS=120
QA_PASS_SCORE=80                      # ngưỡng "đạt"
```

Không còn env `LARK_*` nào cho luồng dữ liệu. Tiện thể: `base.py` cấm mọi env
prefix `LARK_` ở staging, nên bỏ Lark cũng làm staging test được đầy đủ luồng
thay vì phải mock.

## 7. Celery & vận hành

| Task | Nhịp | Queue |
|---|---|---|
| `apps.callcenter.tasks.score_pending_call_quality` | 02:00 hằng ngày | `slow` |

Chỉ còn **một** task — bỏ Lark là bỏ luôn task sync 30 phút, bảng mapping
`record_key → lark_record_id` và lớp zero-out. Trang đọc thẳng DB nên không có
độ trễ đồng bộ, không có trạng thái lệch giữa hai nơi.

Nhịp 1 lần/ngày chứ không phải mỗi 5' như transcribe: báo cáo QA đọc theo
tuần/tháng nên chấm sớm vài giờ không đổi kết luận, mà chạy đêm thì không tranh
worker `slow` với sweep ban ngày.

**Chấm cuốn chiếu, không phải một lô cố định.** `drain_pending_calls` lặp từng
lô `QA_SCORING_BATCH` (20) cho tới khi hết hàng đợi. Ba điều kiện dừng: hết
record PENDING, Gemini hết quota, hoặc chạm trần `QA_SCORING_MAX_MINUTES` (60').
Chạm trần thì phần chưa chấm nằm nguyên PENDING và đêm sau chạy tiếp — không mất
gì. Trần cố định mỗi ngày là sai: tồn đọng 500 cuộc sẽ mất hàng chục đêm mới hết.

**Cuộc đã chấm không bao giờ chấm lại**: `_claim_pending_batch` chỉ lấy
`score_status=PENDING`, chấm xong chuyển DONE. Chấm lại phải cố ý bằng
`--re-score`.

`soft_time_limit=4200` / `time_limit=4500` và lock Redis 4500s chỉ là lưới an
toàn phía Celery — đặt rộng hơn trần 60' để phần dừng CÓ KIỂM SOÁT luôn xảy ra
trước khi Celery giết task giữa chừng. Lock ngắn hơn time limit thì nó tự hết
hạn giữa lúc tick còn chạy và chốt chặn chống chạy chồng thành vô nghĩa.

Task gọi Gemini + I/O dài ⇒ vào `slow` cùng 3 task callcenter hiện có, **không**
để rơi vào `default` (bài học audit 2026-07-26: Gemini chiếm slot làm trễ các
sweep điều phối lead). Lock Redis riêng `callcenter:score:lock`, tick sau SKIP
chứ không xếp hàng.

Cảnh báo & command:

- **Cờ đỏ** ⇒ thẻ Lark tới nhóm quản lý sale ngay trong tick (dedupe theo
  `call_id`), kèm mã đơn + loại cờ + link tới trang chi tiết, **không kèm
  trích dẫn**. Giữ đường này dù bỏ Bitable: đây là *thông báo* để người ta mở
  CRM, không phải *đồng bộ dữ liệu*, và `ops_alerts.py` đã có sẵn. Tắt bằng cách
  để `LARK_OPS_ALERT_WEBHOOK_URL` rỗng. Dùng chung kênh ops sẵn có thay vì thêm
  webhook riêng: cờ đỏ hiếm (dedupe 1h/cuộc gọi) nên không làm nhiễu kênh, và
  thêm một biến env nữa chỉ để cùng đích đến là nợ cấu hình không cần thiết.
- **Số cuộc có ghi âm trong ngày tụt bất thường** (so với trung bình 7 ngày)
  ⇒ cảnh báo ops. Tổng đài mất file là sự cố hạ tầng, phát hiện muộn thì mất
  trắng dữ liệu QA của ngày hôm đó — ghi âm không có đường lấy lại.
- `score_call_quality --limit N --dry-run` để chấm thử; `--record-id` chấm lại 1
  cuộc sau khi sửa prompt.
- `drain_call_quality` theo mẫu `drain_transcribe.py` để backfill.

## 8. Sprint

| Sprint | Nội dung | DoD |
|---|---|---|
| **S1 — Rubric & chấm offline** | `qa_rubric.py`, `scoring.py`, model + migration, command `--dry-run`, unit test (mock Gemini) | Chấm 50 cuộc thật; team lead nghe tay 10 cuộc, lệch điểm TB < 10/100 thì prompt đạt |
| ~~S1.5 — Đếm mẫu trước~~ | ✅ Đã chạy 2026-07-30 | 498 cuộc/30 ngày đủ điều kiện; 84% thuộc `created_order` + `need_more_consulting`. Mẫu đủ để làm |
| **S2 — Pipeline tự động** | `score_status` trên `CallRecord`, celery task + beat, cooldown riêng, requeue stuck, tách `SKIPPED` thành 2 mã, drain command | Chạy 3 ngày prod, `FAILED` < 5%, không ảnh hưởng nhịp transcribe/extract |
| **S3 — API** | 5 endpoint §6.1, section RBAC `call-quality` (một mức view) | Điểm TB từng tiêu chí khớp với tính tay trên 20 cuộc mẫu |
| **S4 — Trang CRM** | Feature `call-quality`, 2 tab, bảng 14 tiêu chí, sheet chi tiết có highlight evidence, `CallQualityReview` | Team lead mở 1 buổi đào tạo lấy nội dung từ dòng đầu bảng tiêu chí, có ví dụ thật kèm theo |

### 8.1 Đã hiện thực (2026-07-31)

| Phần | File |
|---|---|
| Rubric (14 tiêu chí, 4 cờ đỏ, verify trích dẫn, chia lại trọng số khi `na`) | `backend/apps/callcenter/qa_rubric.py` |
| Gọi Gemini + hàng đợi + đo WPM/talk-ratio + đối chiếu chéo | `backend/apps/callcenter/scoring.py` |
| Model `CallQualityScore`, `CallQualityReview`, `ScoreStatus` | `backend/apps/callcenter/models.py` + migration `0003` |
| Celery task 02:00 hằng ngày, chấm cuốn chiếu tới khi hết hàng đợi, queue `slow`, lock riêng | `backend/apps/callcenter/tasks.py`, `config/settings_parts/celery.py` |
| Tổng hợp báo cáo (summary / criteria / trend / coverage) | `backend/apps/callcenter/quality_reports.py` |
| 6 endpoint + RBAC section `call-quality` | `backend/apps/callcenter/transport/views/quality_views_impl.py` |
| Command `score_call_quality --dry-run / --re-score / --call-record` | `backend/apps/callcenter/management/commands/` |
| Trang CRM 2 tab + sheet chi tiết có highlight evidence | `frontend/src/features/call-quality/` |
| Env passthrough | `docker-compose.prod.yml`, `.env.example` |

Test: 29 test rubric + scoring (mock Gemini, không gọi mạng), 21 test API
(phân quyền, lọc, tổng hợp). `ruff` sạch, `tsc --noEmit` sạch, `schema.yaml` +
`schema.d.ts` đã regenerate.

### 8.2 Việc còn lại trước khi bật trên prod

1. **Duyệt rubric với team sale** — thang điểm/trọng số/cờ đỏ là do thiết kế đặt
   ra, khung giấy gốc không có. Sale phải biết mình bị chấm theo cái gì trước
   khi bị chấm.
2. `python manage.py migrate` + cấp section `call-quality` cho role quản lý.
3. `score_call_quality --dry-run --limit 50` → team lead nghe tay 10 cuộc, đối
   chiếu. Lệch điểm TB < 10/100 thì prompt đạt; nhiều dòng `loại:` nghĩa là model
   đang bịa trích dẫn, phải sửa prompt trước.
4. Chỉ khi (3) đạt mới đặt `QA_SCORING_ENABLED=1`.
5. Tách `TranscribeStatus.SKIPPED` thành 2 mã (*không có file* vs *quá ngắn*) —
   chưa làm, hiện phân biệt bằng `recording_url` rỗng hay không.

## 9. Rủi ro

| Rủi ro | Cách chặn |
|---|---|
| Điểm AI không nhất quán giữa các lần | `temperature=0`, rubric versioned, calibrate S1, chỉ so sánh trong cùng version |
| Transcript sai (nhiễu, giọng vùng miền) ⇒ điểm sai | Bắt buộc evidence khớp transcript; điểm thấp bất thường thì đối chiếu `wpm`/`talk_ratio` trước khi kết luận |
| Sale phản ứng "AI chấm oan" | v1 không chấm theo người nên không có ai để oan; điểm dùng để sửa kịch bản và chọn nội dung đào tạo. Trước khi bật chiều theo người phải thông báo trước với team |
| Chi phí Gemini vượt dự kiến | ~17 request/ngày ở lưu lượng hiện tại (§4.6); đo token thật ở S1, đối chiếu hoá đơn sau tuần đầu |
| 429/503 vẫn xảy ra dù trả phí | Cooldown key riêng `gemini:score:cooldown`, `transient` không đốt attempt — giữ nguyên phòng thủ của free tier |
| Đọc điểm TB trên mẫu quá nhỏ khi lọc hẹp | Số cuộc luôn hiện cạnh mọi điểm TB (§5.2) |
| Đánh giá chung che mất người yếu thật sự | Chấp nhận ở v1 — đổi lại có kết quả dùng được ngay mà không cần chờ tổng đài trả đúng định danh nhân viên |
