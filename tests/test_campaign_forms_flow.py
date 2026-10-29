"""เส้นทางของแบบฟอร์มแคมเปญ — ข้ามคำถาม / ส่งแบบฟอร์มเลยตามคำตอบ (ไม่แตะ Mongo)"""

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from routes.campaign_forms import _validate_answers
from schemas.campaign_forms import CampaignFormCreate, answer_path

# แบบสำรวจปีใหม่ (FRM-GO-003): ไม่สะดวก → ข้อเหตุผล (เส้นทางแยก) · ขอคิดดูก่อน → ส่งเลย
FORM = {
    "name": "ปีใหม่ 2570",
    "questions": [
        {
            "id": "join",
            "label": "มาร่วมงานไหม",
            "type": "single",
            "required": True,
            "options": ["มาแน่นอน", "ไม่สะดวก", "ขอคิดดูก่อน"],
            "branches": [{"option": "ไม่สะดวก", "goto": "why"}, {"option": "ขอคิดดูก่อน", "goto": "end"}],
        },
        {"id": "size", "label": "ไซซ์เสื้อ", "type": "single", "required": True, "options": ["M", "L"]},
        {"id": "travel", "label": "เดินทางอย่างไร", "type": "short", "required": True, "next": "end"},
        {"id": "why", "label": "เหตุผลที่ไม่สะดวก", "type": "paragraph", "required": True},
    ],
}


def _questions():
    return [q.model_dump() for q in CampaignFormCreate(**FORM).questions]


def _ids(answers):
    return [q["id"] for q in answer_path(_questions(), answers)]


def test_path_follows_branches_and_next():
    assert _ids({"join": "มาแน่นอน"}) == ["join", "size", "travel"]
    assert _ids({"join": "ไม่สะดวก"}) == ["join", "why"]
    assert _ids({"join": "ขอคิดดูก่อน"}) == ["join"]
    assert _ids({}) == ["join", "size", "travel"]


def test_end_branch_needs_no_other_answers():
    assert _validate_answers(_questions(), {"join": "ขอคิดดูก่อน"}) == {"join": "ขอคิดดูก่อน"}


def test_skipped_answers_are_dropped_not_required():
    answers = {"join": "ไม่สะดวก", "size": "M", "why": "ติดธุระ"}
    assert _validate_answers(_questions(), answers) == {"join": "ไม่สะดวก", "why": "ติดธุระ"}


def test_required_on_path_still_enforced():
    with pytest.raises(HTTPException) as exc:
        _validate_answers(_questions(), {"join": "ไม่สะดวก"})
    assert [e["question_id"] for e in exc.value.detail] == ["why"]


def test_backward_jump_rejected():
    bad = {**FORM, "questions": [*FORM["questions"][:3], {**FORM["questions"][3], "next": "join"}]}
    with pytest.raises(ValidationError, match="ถัดลงไป"):
        CampaignFormCreate(**bad)


def test_branch_to_missing_option_or_question_rejected():
    q0 = FORM["questions"][0]
    with pytest.raises(ValidationError, match="ไม่มี"):
        CampaignFormCreate(**{**FORM, "questions": [{**q0, "branches": [{"option": "X", "goto": "end"}]}]})
    with pytest.raises(ValidationError, match="ไม่มีแล้ว"):
        CampaignFormCreate(**{**FORM, "questions": [{**q0, "branches": [{"option": "ไม่สะดวก", "goto": "gone"}]}]})


def test_branches_dropped_on_non_single():
    q = {"id": "m", "label": "x", "type": "multiple", "options": ["a"], "branches": [{"option": "a", "goto": "end"}]}
    assert CampaignFormCreate(name="f", questions=[q]).questions[0].branches == []
