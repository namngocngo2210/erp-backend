"""Luật chấm điểm — test thuần, không cần DB."""

from apps.callcenter.qa_rubric import (
    BLOCKS,
    CRITERIA,
    RED_FLAGS,
    compute_scores,
    normalize_scored,
)

TRANSCRIPT = (
    "Nhân viên: Dạ em chào anh chị ạ, em gọi từ nhà DELAX ạ.\n"
    "Khách: Ừ em nói đi.\n"
    "Nhân viên: Không biết anh đang bị mỏi nhiều ở vùng nào ạ?\n"
    "Khách: Anh đau vai gáy mấy tháng nay rồi.\n"
    "Nhân viên: Dạ em hiểu, đau vai gáy lâu ngày rất khó chịu ạ.\n"
)


def _criteria(**overrides):
    """Mọi tiêu chí na, trừ những mã được truyền vào."""
    data = {c.code: {"score": "na"} for c in CRITERIA}
    data.update(overrides)
    return data


def test_block_weights_sum_to_100():
    assert sum(block.weight for block in BLOCKS) == 100


def test_evidence_khong_co_trong_transcript_thi_ha_ve_0():
    raw = {
        "criteria": _criteria(
            a1_greeting={"score": "2", "evidence": "Em xin phép tư vấn cho mình ạ"}
        ),
        "summary": "ok",
    }
    clean, rejected = normalize_scored(raw, TRANSCRIPT)

    assert clean["criteria"]["a1_greeting"]["score"] == 0
    assert clean["criteria"]["a1_greeting"]["evidence"] == ""
    assert "a1_greeting" in rejected


def test_evidence_co_that_thi_giu_diem():
    raw = {
        "criteria": _criteria(
            a1_greeting={
                "score": "2",
                "evidence": "Dạ em chào anh chị ạ, em gọi từ nhà DELAX ạ.",
            }
        ),
        "summary": "ok",
    }
    clean, rejected = normalize_scored(raw, TRANSCRIPT)

    assert clean["criteria"]["a1_greeting"]["score"] == 2
    assert "a1_greeting" not in rejected


def test_evidence_khac_dau_cau_van_khop():
    """Model hay trả lại câu thiếu dấu phẩy/chấm — đó là khác định dạng, không
    phải bịa. Chỉ chuẩn hoá dấu câu, KHÔNG bỏ dấu tiếng Việt."""
    raw = {
        "criteria": _criteria(
            b3_empathy={"score": "2", "evidence": "Dạ em hiểu đau vai gáy lâu ngày rất khó chịu"}
        ),
        "summary": "ok",
    }
    clean, _ = normalize_scored(raw, TRANSCRIPT)

    assert clean["criteria"]["b3_empathy"]["score"] == 2


def test_evidence_qua_ngan_bi_loai():
    raw = {"criteria": _criteria(a3_attitude={"score": "2", "evidence": "Dạ"}), "summary": "ok"}
    clean, rejected = normalize_scored(raw, TRANSCRIPT)

    assert clean["criteria"]["a3_attitude"]["score"] == 0
    assert rejected["a3_attitude"] == "trích dẫn quá ngắn để kiểm chứng"


def test_diem_0_khong_can_evidence():
    raw = {"criteria": _criteria(a2_purpose={"score": "0", "evidence": ""}), "summary": "ok"}
    clean, rejected = normalize_scored(raw, TRANSCRIPT)

    assert clean["criteria"]["a2_purpose"]["score"] == 0
    assert "a2_purpose" not in rejected


def test_co_do_bia_bi_loai():
    raw = {
        "criteria": _criteria(),
        "red_flags": [
            {"code": "rf_medical_claim", "evidence": "Dùng cái này là khỏi hẳn ung thư luôn ạ"}
        ],
        "summary": "ok",
    }
    clean, rejected = normalize_scored(raw, TRANSCRIPT)

    assert clean["red_flags"] == []
    assert "red_flag:rf_medical_claim" in rejected


def test_co_do_khong_hop_le_bi_bo():
    raw = {
        "criteria": _criteria(),
        "red_flags": [{"code": "rf_khong_ton_tai", "evidence": "Dạ em chào anh chị ạ"}],
        "summary": "ok",
    }
    clean, _ = normalize_scored(raw, TRANSCRIPT)

    assert clean["red_flags"] == []
    assert set(RED_FLAGS) == {
        "rf_medical_claim",
        "rf_ignored_contraindication",
        "rf_false_policy",
        "rf_disrespect",
    }


def test_diem_khong_hop_le_thanh_na():
    raw = {"criteria": _criteria(c3_knowledge={"score": "5"}), "summary": "ok"}
    clean, rejected = normalize_scored(raw, TRANSCRIPT)

    assert clean["criteria"]["c3_knowledge"]["na"] is True
    assert "c3_knowledge" in rejected


def test_tieu_chi_thieu_han_thanh_na():
    raw = {"criteria": {}, "summary": "ok"}
    clean, rejected = normalize_scored(raw, TRANSCRIPT)

    assert all(item["na"] for item in clean["criteria"].values())
    assert rejected["a1_greeting"] == "thiếu trong output"


def test_na_bi_loai_khoi_mau_so():
    """Khối chỉ có 1 tiêu chí được chấm thì mẫu số là 1 tiêu chí đó, không phải
    toàn khối — nếu không, cuộc gọi ngắn bị chấm như sale kém."""
    clean_criteria = {c.code: {"score": None, "na": True} for c in CRITERIA}
    clean_criteria["a1_greeting"] = {"score": 2, "na": False}

    total, blocks = compute_scores(clean_criteria)

    # Chỉ khối A sống ⇒ trọng số chia lại thành 100, đạt điểm tuyệt đối.
    assert blocks == {"A": 100.0}
    assert total == 100.0


def test_khoi_na_toan_bo_thi_chia_lai_trong_so():
    clean_criteria = {c.code: {"score": None, "na": True} for c in CRITERIA}
    clean_criteria["a1_greeting"] = {"score": 2, "na": False}  # khối A: 100%
    clean_criteria["b1_discovery"] = {"score": 0, "na": False}  # khối B: 0%

    total, blocks = compute_scores(clean_criteria)

    # A(15) và B(30) sống ⇒ A chiếm 15/45, B chiếm 30/45 của thang 100.
    assert blocks["A"] == 33.33
    assert blocks["B"] == 0.0
    assert total == 33.33


def test_khong_co_tieu_chi_nao_duoc_cham_thi_diem_0():
    clean_criteria = {c.code: {"score": None, "na": True} for c in CRITERIA}

    total, blocks = compute_scores(clean_criteria)

    assert total == 0.0
    assert blocks == {}
