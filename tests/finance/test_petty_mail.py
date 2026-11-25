from services.finance import petty_mail as M


class TestRenderGateBlocked:
    def test_lists_every_missing_thing(self):
        html = M.render_gate_blocked("03-B02-2610-001",
                                     [{"label": "ใบชั่งน้ำหนัก (สำเนา)"},
                                      {"label": "เลขที่บัตรชั่ง (แถวที่ 3)"}])
        assert "ใบชั่งน้ำหนัก (สำเนา)" in html and "แถวที่ 3" in html

    def test_names_the_document(self):
        assert "03-B02-2610-001" in M.render_gate_blocked("03-B02-2610-001", [{"label": "x"}])

    def test_escapes_html_from_data(self):
        html = M.render_gate_blocked("03-B02-2610-001", [{"label": "<script>bad()</script>"}])
        assert "<script>" not in html


class TestRenderReturned:
    def test_carries_the_reason(self):
        html = M.render_returned("03-B02-2610-001", "ใบชั่งไม่ครบ 2 ใบ")
        assert "ใบชั่งไม่ครบ 2 ใบ" in html

    def test_blank_reason_still_renders(self):
        assert "03-B02-2610-001" in M.render_returned("03-B02-2610-001", None)


class TestRenderDuplicate:
    def test_points_at_the_other_claim(self):
        html = M.render_duplicate("03-B02-2610-002",
                                  [{"message": "บัตรชั่งใบนี้ของรถคันนี้เบิกไปแล้ว",
                                    "form_id": "03-B02-2609-001", "line_no": 4}])
        assert "03-B02-2609-001" in html and "แถวที่ 4" in html


class TestRecipients:
    def test_clerk_is_always_included(self):
        to = M.gate_recipients(clerk_email="clerk@x.com", approver_email="boss@x.com")
        assert "clerk@x.com" in to

    def test_approver_too(self):
        to = M.gate_recipients(clerk_email="clerk@x.com", approver_email="boss@x.com")
        assert set(to) == {"clerk@x.com", "boss@x.com"}

    def test_missing_addresses_are_dropped_not_sent_as_blank(self):
        assert M.gate_recipients(clerk_email=None, approver_email="boss@x.com") == ["boss@x.com"]

    def test_the_same_person_is_listed_once(self):
        assert M.gate_recipients(clerk_email="a@x.com", approver_email="a@x.com") == ["a@x.com"]

    def test_nobody_means_empty(self):
        assert M.gate_recipients(clerk_email=None, approver_email=None) == []


class TestCardLikeAdvance:
    """อีเมลเงินสดย่อยหน้าตาเดียวกับ Advance · หัวเรื่อง [Petty Cash] (ผู้ใช้สั่ง 25/11/2026)"""

    def test_head_pending_is_a_card_with_details_and_button(self, monkeypatch):
        monkeypatch.setenv("FE_BASE_URL", "https://menait-service.vercel.app")
        html = M.render_head_pending("BK-A04-2611-001", "ณิชกานต์ วงษ์อุบล", 500)
        assert "background:#2ea84f" in html and "รอท่านอนุมัติ" in html
        assert "ณิชกานต์ วงษ์อุบล" in html and "500.00 บาท" in html and "BK-A04-2611-001" in html
        assert "https://menait-service.vercel.app/finance/petty/BK-A04-2611-001" in html and "เปิดรายการ" in html

    def test_no_button_without_fe_url(self, monkeypatch):
        monkeypatch.delenv("FE_BASE_URL", raising=False)
        assert "เปิดรายการ" not in M.render_head_pending("X", None, 1)

    def test_names_are_escaped(self):
        assert "<script>" not in M.render_head_pending("X", "<script>bad()</script>", 1)

    def test_subject_prefix(self, monkeypatch):
        sent = []
        monkeypatch.setattr(M.mail, "send_finance_email", lambda to, subject, html: sent.append(subject))
        M.notify_head_pending("BK-A04-2611-001", "x", 500, ["h@x"])
        M.notify_round_rejected(1, "LB-B02-2611-B9", "ไม่ครบ", holder_email="o@x")
        assert sent == ["[Petty Cash] รอหัวหน้าอนุมัติ BK-A04-2611-001", "[Petty Cash] ไม่อนุมัติเคลียร์ LB-B02-2611-B9"]
