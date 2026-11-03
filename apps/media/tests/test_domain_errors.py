from apps.media.domain.errors import upstream_detail


def test_known_codes_translated():
    assert upstream_detail("invalid_url") == "URL không hợp lệ (cần scheme + host)."
    assert upstream_detail("busy") == "Nguồn tải đang bận, thử lại sau vài giây."
    assert upstream_detail("photo_post") == "Bài dạng ảnh, không có video để tải."


def test_unknown_code_falls_back_to_code_itself():
    assert upstream_detail("weird_thing") == "weird_thing"
