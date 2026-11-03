# Danh bạ Digital / TKQC / fanpage / Sale cho agent Hermes

> Ảnh chụp dữ liệu prod ngày **15/09/2026**. Nguồn sống: bảng `channels`, `user_channels`,
> `channel_ad_accounts`, `facebook_ads` (ad → page), `teams`; qua HTTP là
> `/api/daily-report/definitions` → `ad_account_channels[].channels` (hoặc cột `channels`
> trên từng dòng `/ads`). Khi nhân sự, kênh hay TKQC đổi thì
> cập nhật file này — nó là thứ Hermes dùng để dịch tên người sang mã kênh.

**Từ vựng bắt buộc:**

- **Kênh (channel) = Digital.** Mỗi kênh ads là một nhân viên Ads phụ trách. Đơn và lead gắn
  với kênh, nên "doanh thu của Cường" = doanh thu kênh `facebook-cuong`.
- **TKQC** (tài khoản quảng cáo) gắn vào kênh. Một TKQC thuộc đúng một Digital.
- **Fanpage** không có bảng gán trực tiếp với Digital. Bảng dưới quy page về Digital theo
  **chi tiêu**: page nào có ad chạy trong TKQC của kênh đó trong 90 ngày gần nhất
  (17/06 → 15/09/2026). Chi tiêu ghi kèm là số gốc, chưa nhân hệ số.
- **Sale** không gắn với Digital hay fanpage. Lead chia cho sale qua nhân viên phụ trách
  trên đơn (`ai.don_hang.nhan_vien_ten`, HTTP: cột `staff` trên dòng `leads/stats`).

## 1. Digital theo tên

| Digital | Tài khoản CRM | Kênh phụ trách | Tên kênh |
|---|---|---|---|
| Cường | `cuongads` | `facebook-cuong` | Facebook Cường |
| Đức | `ducads` | `facebook-duc` | Facebook Đức |
| Khang | `khangads` | `google-ads-web` | Google Ads/Web |
| Khanh | `khanhads` | `facebook-khanh` | Facebook Khanh |
| Lợi | `loiads` | `facebook-loi` | Facebook Lợi |
| Tài | `taiads` | `facebook` | Facebook Tài |
| Thành | `thanhads` | `facebook-thanh` | Facebook Thành |

Kênh `facebook-lan` (Facebook Lan) **không có user phòng Ads** gắn; người đang gắn là sale
**Lan** (`lansale`). Có 2 TKQC nhưng không có chi tiêu trong 90 ngày. Hỏi "Digital Lan" thì
trả lời theo kênh này và nói rõ tình trạng đó.

Bảy kênh có Digital ở trên là đúng tập kênh có nhân viên phòng Ads phụ trách — màn Báo cáo
Ads của app; API `/ads` không lọc theo đây, để nguyên mọi TKQC. Các kênh khác (Shopee, TikTok, Bán trực tiếp, KOC,
Resale, Tặng hàng…) không thuộc Digital nào.

## 2. Digital cầm page nào, TKQC nào

### Tài — `facebook`

TKQC (9):

| ID | Tên TKQC |
|---|---|
| 1783282579756195 | Adsplus 5.2 - 01 - DELAXMASSAGERVN |
| 1748327042909330 | Adsplus 5.2 - 08 - DELAXMASSAGERVN |
| 1944372383180742 | INV_AKA_DESI_TÀI_01 |
| 1563738918791712 | INV_AKA_DESI_TÀI_02 |
| 1590079389144447 | INV_AKA_DESI_TÀI_03 |
| 4064075963852583 | TAI-TK10 |
| 2158268584938002 | TK8 |
| 4220512514862671 | TÀI - TK1 |
| 2132618337134900 | TÀI - TK3 |

Fanpage (3):

| Fanpage | Page ID | Chi tiêu 90 ngày |
|---|---|---|
| DELAX Massager VN | 451593898044414 | 225,4tr |
| DELAX Massager CHÍNH HÃNG | 407222339135523 | 112,2tr |
| DELAX Massager Việt Nam | 107941798775048 | 0,8tr |

### Cường — `facebook-cuong`

TKQC (12):

| ID | Tên TKQC |
|---|---|
| 3692716264228019 | Adsplus 5.2 - 05 - DELAXMASSAGERVN |
| 661249253302923 | Cuong TK10 LT |
| 779819381833931 | Cuong_TK1_TKCT |
| 1738133480225659 | Cường TK1-LT |
| 25307714422198884 | Cường TK2-LT |
| 1163169098914182 | Cường TK3-LT |
| 738153308644179 | Cường TK7 LT |
| 1412029613445078 | Cường TK8 LT |
| 1770880347191131 | Cường TK9 LT |
| 3575109952789808 | Cường_TK2_TKCT |
| 685917665888651 | Cường_TK3_TKCT |
| 622929307066128 | Cường_TK4_TKCT |

Fanpage (4):

| Fanpage | Page ID | Chi tiêu 90 ngày |
|---|---|---|
| DELAX Massager | 337978729599920 | 313,2tr |
| *(chưa có tên trong CRM)* | 1141499389055321 | 8,7tr |
| Delax Massage Chính Hãng | 688049251060158 | 2,2tr |
| *(chưa có tên trong CRM)* | 1348247081700784 | 1,2tr |

### Đức — `facebook-duc`

TKQC (7):

| ID | Tên TKQC |
|---|---|
| 28557709770514191 | Adsplus 5.2 - 02 - DELAXMASSAGERVN |
| 1772819430699839 | Adsplus 5.2 - 07 - DELAXMASSAGERVN |
| 2414996245675068 | HT-TK1 |
| 994148749971569 | HT-TK2 |
| 960459886893833 | HT-TK3 |
| 1000772019399368 | HT-TK4 |
| 3804581706506110 | LACH-TAI-TK9 |

Fanpage (5):

| Fanpage | Page ID | Chi tiêu 90 ngày |
|---|---|---|
| Delax - Máy Massage Chân Cao Cấp | 1240309462490872 | 142,9tr |
| DELAX Massager - 282 Nguyễn Huy Tưởng | 1093778130489227 | 137,5tr |
| Delax - Máy Mát Xa Chườm Ấm Khớp Gối | 1197568930108828 | 62,9tr |
| Delax - Massage Cổ Vai Gáy Tại Nhà | 1196227593574062 | 26,6tr |
| Hoku - Tủ Sấy Khô Quần Áo Cao Cấp | 1249098931617542 | 4,2tr |

### Khanh — `facebook-khanh`

TKQC (4):

| ID | Tên TKQC |
|---|---|
| 1077106928123558 | Adsplus 5.2 - 03 - DELAXMASSAGERVN |
| 1114754040203890 | INV_AKA_DESI_KHANH_01 |
| 1528740074780069 | INV_AKA_DESI_KHANH_02 |
| 2036145810566705 | INV_AKA_DESI_KHANH_03 |

Fanpage (4):

| Fanpage | Page ID | Chi tiêu 90 ngày |
|---|---|---|
| DELAX - Thiết bị Massage Cao Cấp tại nhà | 1129263510280247 | 45,4tr |
| *(chưa có tên trong CRM)* | 547163498826636 | 18,4tr |
| *(chưa có tên trong CRM)* | 1447793842155929 | 12,8tr |
| *(chưa có tên trong CRM)* | 108127675328287 | 12,3tr |

### Lợi — `facebook-loi`

TKQC (11):

| ID | Tên TKQC |
|---|---|
| 1056210277037938 | Adsplus 5.2 - 04 - DELAXMASSAGERVN |
| 1333932535490354 | INV_AKA_DESI_Lợi_01 |
| 1097952775901589 | INV_AKA_DESI_Lợi_02 |
| 1024341963659208 | INV_AKA_DESI_Lợi_03 |
| 25322428374101900 | LỢI_TK7_HKD |
| 753582677009795 | Lợi TK3 LT |
| 2726867640835257 | Lợi TK4 LT |
| 1167014695491892 | Lợi TK5 LT |
| 2090916511406591 | Lợi_TK1_HKD |
| 1075959281093452 | Lợi_TK2_TKCT |
| 2007931173486188 | Lợi_TK6_HKD |

Fanpage (7):

| Fanpage | Page ID | Chi tiêu 90 ngày |
|---|---|---|
| DELAX Máy massage Chính Hãng | 518854254652984 | 227,7tr |
| DELAX Máy Massage Chính Hãng | 621054497755505 | 67,5tr |
| DELAX - Tiên phong massage tại nhà | 120540861140825 | 48,1tr |
| DELAX Máy Massage Thông Minh | 1134566529741302 | 27,5tr |
| *(chưa có tên trong CRM)* | 599029966637656 | 7,7tr |
| *(chưa có tên trong CRM)* | 641257155743525 | 1,0tr |
| DELAX Thiết Bị Massage Chính Hãng VN | 643674342170315 | 0,2tr |

Hai page "DELAX Máy massage Chính Hãng" (518854254652984) và "DELAX Máy Massage Chính Hãng"
(621054497755505) là **hai page khác nhau**, chỉ khác chữ hoa. Luôn phân biệt bằng Page ID.

### Thành — `facebook-thanh`

TKQC (8):

| ID | Tên TKQC |
|---|---|
| 2285169742277399 | Adsplus 5.2 - 06 - DELAXMASSAGERVN |
| 1078238278488936 | INV_AKA_DESI_Thành_01 |
| 1017936677672834 | INV_AKA_DESI_Thành_02 |
| 27750537157908031 | INV_AKA_DESI_Thành_03 |
| 803494525751788 | TAI-TK19 |
| 1881704629190263 | THANH - TK1 |
| 1098224918226425 | THÀNH - TK3 |
| 1249848769433218 | TÀI - TK4 |

Fanpage (6):

| Fanpage | Page ID | Chi tiêu 90 ngày |
|---|---|---|
| DELAX - Chăm Sóc Sức Khỏe Toàn Diện | 1069387002926498 | 198,8tr |
| Delax - Thiết Bị Chăm Sóc Sức Khỏe Thông Minh | 1153573734513703 | 40,7tr |
| DELAX - Chăm Sóc Sức Khỏe Gia Đình | 612219731972518 | 32,1tr |
| *(chưa có tên trong CRM)* | 1314344745092122 | 9,2tr |
| *(chưa có tên trong CRM)* | 1225921433929733 | 0,3tr |
| *(chưa có tên trong CRM)* | 1114940598374462 | 0,2tr |

### Khang — `google-ads-web`

TKQC (6): 2 Google + 4 Facebook.

| Nền tảng | ID | Tên TKQC |
|---|---|---|
| Google | 6097312844 | Delax.vn |
| Google | 1297488348 | Hoku.vn - Search |
| Facebook | 1131816018881765 | A Khang TK1- LT |
| Facebook | 1905102610409119 | A Khang TK2 - LT |
| Facebook | 1617623272719888 | A.Khang_TK1_HKD |
| Facebook | 881242794753144 | A.Khang_TK2_HKD |

Không có fanpage nào có ad chạy trong 90 ngày từ 4 TKQC Facebook của Khang.

### Lan — `facebook-lan` (không có Digital)

| ID | Tên TKQC |
|---|---|
| 1895687731066688 | ads 1 -Lan |
| 4180424835621393 | ads 2 -Lan |

Không có chi tiêu trong 90 ngày.

**TKQC tên "TÀI" nhưng gắn kênh khác:** `TÀI - TK4` (1249848769433218) và `TAI-TK19`
(803494525751788) thuộc kênh `facebook-thanh`; `LACH-TAI-TK9` thuộc `facebook-duc`. Tên TKQC
không nói lên Digital — chỉ bản đồ `channel_ad_accounts` mới đúng.

## 3. Page trong dữ liệu hội thoại (Pancake) → Digital

`page_ten` trong `ai.hoi_thoai` và trong API hội thoại là các tên dưới đây. ID Facebook của
Pancake trùng Page ID ở mục 2, nên quy về Digital được theo cùng luật chi tiêu.

| Page (`page_ten`) | Page ID | Digital |
|---|---|---|
| DELAX Massager | 337978729599920 | Cường |
| Delax Massage Chính Hãng | 688049251060158 | Cường |
| Delax - Máy Massage Chân Cao Cấp | 1240309462490872 | Đức |
| DELAX Massager - 282 Nguyễn Huy Tưởng | 1093778130489227 | Đức |
| Delax - Máy Mát Xa Chườm Ấm Khớp Gối | 1197568930108828 | Đức |
| Delax - Massage Cổ Vai Gáy Tại Nhà | 1196227593574062 | Đức |
| Hoku - Tủ Sấy Khô Quần Áo Cao Cấp | 1249098931617542 | Đức |
| DELAX - Thiết bị Massage Cao Cấp tại nhà | 1129263510280247 | Khanh |
| DELAX Máy massage Chính Hãng | 518854254652984 | Lợi |
| DELAX Máy Massage Chính Hãng | 621054497755505 | Lợi |
| DELAX - Tiên phong massage tại nhà | 120540861140825 | Lợi |
| DELAX Máy Massage Thông Minh | 1134566529741302 | Lợi |
| DELAX Thiết Bị Massage Chính Hãng VN | 643674342170315 | Lợi |
| DELAX Massager VN | 451593898044414 | Tài |
| DELAX Massager CHÍNH HÃNG | 407222339135523 | Tài |
| DELAX Massager Việt Nam | 107941798775048 | Tài |
| DELAX - Chăm Sóc Sức Khỏe Toàn Diện | 1069387002926498 | Thành |
| Delax - Thiết Bị Chăm Sóc Sức Khỏe Thông Minh | 1153573734513703 | Thành |
| DELAX - Chăm Sóc Sức Khỏe Gia Đình | 612219731972518 | Thành |
| DELAX -Gia Dụng Thông Minh | 659743943883179 | — (không có ad 90 ngày) |

Page Zalo cá nhân trong dữ liệu hội thoại là tài khoản của **sale**, không phải Digital:
"Delax", "Delax Chính Hãng Vn", "Hồng Nhung Delax", "Huệ Delax Massage", "Hương Delax",
"Nga Trần Delax", "Nguyễn Tình Delax", "Phương Delax". Zalo OA "Delax Việt Nam", các gian
Shopee/TikTok và kênh YouTube cũng không thuộc Digital nào.

## 4. Sale theo tên

Phòng **Kinh doanh** (15):

| Sale | Tài khoản CRM | Team |
|---|---|---|
| Hiền | `hiensale` | Telesale |
| Hiền (Ca Tối) | `hiensale_catoi` | Telesale |
| Hiền Landing | `hien_landing` | Telesale |
| Phương | `phuongsale` | Telesale |
| Phương (Ca Tối) | `phuongsale_catoi` | Telesale |
| Thúy | `thuysale` | Telesale, CSKH |
| Hồng Nhung | `nhungsale` | CSKH (trưởng nhóm) |
| Hoa | `hoasale` | CSKH |
| Vân Hà | `vanha_cskh` | CSKH |
| Vân Hà Ca Tối | `vanha_catoi` | CSKH |
| Huyền CSKH | `huyen_cskh` | — |
| Miền CSKH | `mien_cskh` | — |
| Lan | `lansale` | — (gắn kênh `facebook-lan`) |
| Lương | `luongsale` | — |
| Nguyễn Thị Nhung | `nhungnt_sale` | — |

Phòng **Booking** (6): Hạnh (`hanhbk`), Hoài (`hoaibk`), Lành (`lanhketoan`), My (`mybk`),
Trâm (`trambk`), Vân Anh (`vananhbk`). Kênh `koc` (Hàng mẫu KOC) thuộc phòng này.

Team:

| Team | Trưởng nhóm | Thành viên |
|---|---|---|
| Telesale | — | Hiền, Hiền (Ca Tối), Hiền Landing, Phương, Phương (Ca Tối), Thúy |
| CSKH | Hồng Nhung | Hoa, Hồng Nhung, Thúy, Vân Hà, Vân Hà Ca Tối |

Tên sale trong `ai.don_hang.nhan_vien_ten`, `ai.cuoc_goi.agent_ten` và `staff.name` trên dòng `leads/stats`
là cột "Sale" ở trên. Các cặp "Hiền" / "Hiền (Ca Tối)", "Phương" / "Phương (Ca Tối)",
"Vân Hà" / "Vân Hà Ca Tối" là **hai tài khoản CRM riêng** — CRM không ghi đó có phải cùng một
người hay không. Khi hỏi theo tên, liệt kê tách từng tài khoản và hỏi lại nếu cần gộp.
