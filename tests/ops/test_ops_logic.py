from services.ops import ops_logic as L


class TestManagerRule:
    def test_ops_team_username_is_manager_case_insensitive(self):
        assert L.is_manager("Kittaboon.L", None, None) is True
        assert L.is_manager("PATCHARAPAN.P", 5, "999999") is True

    def test_admin_department_is_manager(self):
        assert L.is_manager("someone.else", 21, "999999") is True

    def test_admin_employee_id_is_manager(self):
        assert L.is_manager("someone.else", 5, "680043") is True
        assert L.is_manager("someone.else", None, "670108") is True

    def test_non_team_non_admin_is_not_manager(self):
        assert L.is_manager("someone.else", 5, "999999") is False

    def test_no_username_no_admin_fields(self):
        assert L.is_manager(None, None, None) is False

    def test_require_manager_raises_forbidden(self):
        try:
            L.require_manager(False)
        except L.OpsForbidden as exc:
            assert exc.http_status == 403
            assert exc.message == L.MSG_MANAGER_ONLY
        else:
            raise AssertionError("expected OpsForbidden")

    def test_require_manager_passes(self):
        L.require_manager(True)  # must not raise


class TestStatusInput:
    def test_valid_status_without_remark(self):
        assert L.validate_status_input("To-Do", None) is None

    def test_reject_without_remark_raises(self):
        try:
            L.validate_status_input("Reject", None)
        except L.OpsError as exc:
            assert exc.http_status == 400
            assert exc.field_errors == {"remark": L.MSG_REJECT_REMARK_REQUIRED}
        else:
            raise AssertionError("expected OpsError")

    def test_reject_with_blank_remark_raises(self):
        try:
            L.validate_status_input("Reject", "   ")
        except L.OpsError:
            pass
        else:
            raise AssertionError("expected OpsError")

    def test_reject_with_remark_strips_and_returns(self):
        assert L.validate_status_input("Reject", "  ไม่อนุมัติ  ") == "ไม่อนุมัติ"

    def test_invalid_status_raises(self):
        try:
            L.validate_status_input("Bogus", None)
        except L.OpsError as exc:
            assert exc.http_status == 400
        else:
            raise AssertionError("expected OpsError")

    def test_non_reject_ignores_remark_requirement(self):
        for status in ("Open", "To-Do", "In Progress", "Review", "Done"):
            assert L.validate_status_input(status, None) is None


class TestReviewInput:
    def test_passed_while_review(self):
        assert L.validate_review_input("Review", "passed", None) is None

    def test_changes_requested_without_note_raises(self):
        try:
            L.validate_review_input("Review", "changes_requested", None)
        except L.OpsError as exc:
            assert exc.field_errors == {"note": L.MSG_CHANGES_REQUESTED_NOTE_REQUIRED}
        else:
            raise AssertionError("expected OpsError")

    def test_changes_requested_with_blank_note_raises(self):
        try:
            L.validate_review_input("Review", "changes_requested", "   ")
        except L.OpsError:
            pass
        else:
            raise AssertionError("expected OpsError")

    def test_changes_requested_with_note_strips(self):
        assert L.validate_review_input("Review", "changes_requested", "  แก้ A  ") == "แก้ A"

    def test_not_in_review_raises_conflict(self):
        for status in ("Open", "To-Do", "In Progress", "Done", "Reject"):
            try:
                L.validate_review_input(status, "passed", None)
            except L.OpsConflict as exc:
                assert exc.http_status == 409
                assert exc.message == L.MSG_REVIEW_NOT_IN_REVIEW
            else:
                raise AssertionError(f"expected OpsConflict for status={status}")

    def test_invalid_result_raises_before_state_check(self):
        # even while Review, a bogus result value is still rejected
        try:
            L.validate_review_input("Review", "bogus", None)
        except L.OpsError as exc:
            assert exc.http_status == 400
        else:
            raise AssertionError("expected OpsError")


class TestProjectStateRules:
    def test_assignees_editable_while_open(self):
        for status in ("Open", "To-Do", "In Progress", "Review"):
            L.check_project_assignees_editable(status)  # must not raise

    def test_assignees_not_editable_when_closed(self):
        for status in ("Done", "Reject"):
            try:
                L.check_project_assignees_editable(status)
            except L.OpsConflict as exc:
                assert exc.http_status == 409
                assert exc.message == L.MSG_PROJECT_CLOSED_ASSIGNEES
            else:
                raise AssertionError(f"expected OpsConflict for status={status}")

    def test_plan_editable_while_open(self):
        for status in ("Open", "To-Do", "In Progress", "Review"):
            L.check_project_plan_editable(status)  # must not raise

    def test_plan_not_editable_when_closed(self):
        for status in ("Done", "Reject"):
            try:
                L.check_project_plan_editable(status)
            except L.OpsConflict as exc:
                assert exc.message == L.MSG_PROJECT_CLOSED_PLAN
            else:
                raise AssertionError(f"expected OpsConflict for status={status}")


class TestTaskStateRules:
    def test_create_blocked_when_project_rejected(self):
        try:
            L.check_task_create_allowed("Reject")
        except L.OpsConflict as exc:
            assert exc.message == L.MSG_PROJECT_REJECTED_TASK_CREATE
        else:
            raise AssertionError("expected OpsConflict")

    def test_create_allowed_when_project_done(self):
        L.check_task_create_allowed("Done")  # must not raise — follow-up fixes

    def test_create_allowed_other_statuses(self):
        for status in ("Open", "To-Do", "In Progress", "Review"):
            L.check_task_create_allowed(status)

    def test_move_target_blocked_when_rejected(self):
        try:
            L.check_task_move_target("Reject")
        except L.OpsConflict as exc:
            assert exc.message == L.MSG_PROJECT_REJECTED_TASK_MOVE
        else:
            raise AssertionError("expected OpsConflict")

    def test_move_target_allowed_when_done(self):
        L.check_task_move_target("Done")

    def test_editable_only_while_open(self):
        L.check_task_editable("Open")
        for status in ("To-Do", "In Progress", "Review", "Done", "Reject"):
            try:
                L.check_task_editable(status)
            except L.OpsConflict as exc:
                assert exc.message == L.MSG_TASK_NOT_OPEN
            else:
                raise AssertionError(f"expected OpsConflict for status={status}")

    def test_assignees_editable_until_closed(self):
        for status in ("Open", "To-Do", "In Progress", "Review"):
            L.check_task_assignees_editable(status)
        for status in ("Done", "Reject"):
            try:
                L.check_task_assignees_editable(status)
            except L.OpsConflict as exc:
                assert exc.message == L.MSG_TASK_CLOSED
            else:
                raise AssertionError(f"expected OpsConflict for status={status}")

    def test_plan_editable_until_closed(self):
        for status in ("Open", "To-Do", "In Progress", "Review"):
            L.check_task_plan_editable(status)
        for status in ("Done", "Reject"):
            try:
                L.check_task_plan_editable(status)
            except L.OpsConflict as exc:
                assert exc.message == L.MSG_TASK_CLOSED
            else:
                raise AssertionError(f"expected OpsConflict for status={status}")

    def test_validate_task_title_strips(self):
        assert L.validate_task_title("  ทำรายงาน  ") == "ทำรายงาน"

    def test_validate_task_title_blank_raises(self):
        for bad in (None, "", "   "):
            try:
                L.validate_task_title(bad)
            except L.OpsError as exc:
                assert exc.field_errors == {"title": L.MSG_TASK_TITLE_REQUIRED}
            else:
                raise AssertionError("expected OpsError")


class TestAssignees:
    def test_valid_usernames_pass_through_lowercased(self):
        assert L.validate_assignee_usernames(["Kittaboon.L", "sutiwat.c"]) == ["kittaboon.l", "sutiwat.c"]

    def test_dedupes_case_insensitive_preserving_first_order(self):
        assert L.validate_assignee_usernames(["sutiwat.c", "SUTIWAT.C", "kittaboon.l"]) == [
            "sutiwat.c", "kittaboon.l"
        ]

    def test_blank_entries_skipped(self):
        assert L.validate_assignee_usernames(["", "  ", "kittaboon.l"]) == ["kittaboon.l"]

    def test_empty_list_ok(self):
        assert L.validate_assignee_usernames([]) == []
        assert L.validate_assignee_usernames(None) == []

    def test_non_team_username_raises_400(self):
        try:
            L.validate_assignee_usernames(["someone.else"])
        except L.OpsError as exc:
            assert exc.http_status == 400
            assert exc.field_errors == {"usernames": L.MSG_ASSIGNEE_INVALID}
        else:
            raise AssertionError("expected OpsError")

    def test_all_ops_team_members_accepted(self):
        assert sorted(L.validate_assignee_usernames(L.OPS_TEAM)) == sorted(L.OPS_TEAM)

    def test_exclude_owner_removes_case_insensitive(self):
        assert L.exclude_owner(["kittaboon.l", "sutiwat.c"], "Kittaboon.L") == ["sutiwat.c"]

    def test_exclude_owner_no_owner_username(self):
        assert L.exclude_owner(["kittaboon.l"], None) == ["kittaboon.l"]


class TestComments:
    def test_author_may_act_on_own_comment(self):
        L.check_comment_author("E1", "E1")  # must not raise

    def test_non_author_forbidden(self):
        try:
            L.check_comment_author("E1", "E2")
        except L.OpsForbidden as exc:
            assert exc.http_status == 403
            assert exc.message == L.MSG_COMMENT_OWN_ONLY
        else:
            raise AssertionError("expected OpsForbidden")


class TestAttachments:
    def test_valid_file_passes(self):
        L.check_attachment_file("application/pdf", 1024)

    def test_too_large_raises(self):
        try:
            L.check_attachment_file("application/pdf", L.MAX_ATTACHMENT_BYTES + 1)
        except L.OpsError as exc:
            assert exc.message == L.MSG_ATTACHMENT_TOO_LARGE
        else:
            raise AssertionError("expected OpsError")

    def test_exactly_at_limit_ok(self):
        L.check_attachment_file("image/png", L.MAX_ATTACHMENT_BYTES)

    def test_disallowed_mime_raises(self):
        try:
            L.check_attachment_file("application/x-executable", 10)
        except L.OpsError as exc:
            assert exc.message == L.MSG_ATTACHMENT_TYPE_INVALID
        else:
            raise AssertionError("expected OpsError")

    def test_allowed_mime_samples(self):
        for mime in (
            "image/jpeg", "image/png", "application/pdf", "text/csv",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/zip",
        ):
            L.check_attachment_file(mime, 10)

    def test_owner_may_upload(self):
        L.check_attachment_owner("E1", "E1", is_mgr=False)

    def test_non_owner_forbidden(self):
        try:
            L.check_attachment_owner("E1", "E2", is_mgr=False)
        except L.OpsForbidden as exc:
            assert exc.message == L.MSG_ATTACHMENT_OWN_ONLY
        else:
            raise AssertionError("expected OpsForbidden")

    def test_manager_may_upload_to_anyones(self):
        L.check_attachment_owner("E1", "E2", is_mgr=True)  # must not raise

    def test_safe_file_name_strips_path(self):
        # only the last path segment survives — both '/' and '\' are treated as separators
        assert L.safe_file_name("../../etc/passwd") == "passwd"
        assert L.safe_file_name("C:\\Users\\bew\\report.pdf") == "report.pdf"

    def test_safe_file_name_collapses_special_chars(self):
        assert L.safe_file_name("แผน งาน (final)!!.docx") == "final_.docx"

    def test_safe_file_name_empty_falls_back(self):
        assert L.safe_file_name("") == "file"
        assert L.safe_file_name(None) == "file"

    def test_safe_file_name_truncates_long_names(self):
        long_name = ("a" * 300) + ".txt"
        assert len(L.safe_file_name(long_name)) <= 150


class TestIdPatterns:
    def test_project_id_pattern(self):
        assert L.PROJECT_ID_RE.match("OPS-2026-012")
        assert not L.PROJECT_ID_RE.match("OPS-26-012")
        assert not L.PROJECT_ID_RE.match("ISS-2026-012")

    def test_issue_id_pattern(self):
        assert L.ISSUE_ID_RE.match("ISS-2026-031")
        assert not L.ISSUE_ID_RE.match("ISS-2026-31")  # needs at least 3 digits (zero-padded)

    def test_task_id_pattern(self):
        assert L.TASK_ID_RE.match("TSK-2026-007")
        assert L.TASK_ID_RE.match("TSK-2026-1000")  # grows beyond 3 digits
        assert not L.TASK_ID_RE.match("TSK-2026-07")


class TestSanitizeRequirementHtml:
    def test_none_passthrough(self):
        assert L.sanitize_requirement_html(None) is None

    def test_allowed_tags_kept(self):
        html = "<p>hello <strong>world</strong> <u>underline</u> <mark>mark</mark></p>"
        out = L.sanitize_requirement_html(html)
        assert "<p>" in out and "<strong>" in out and "<u>" in out and "<mark>" in out

    def test_lists_kept(self):
        html = "<ol><li>one</li></ol><ul><li>two</li></ul><br>"
        out = L.sanitize_requirement_html(html)
        assert "<ol>" in out and "<ul>" in out and "<li>" in out and "<br" in out

    def test_script_and_disallowed_tags_stripped(self):
        out = L.sanitize_requirement_html("<script>alert(1)</script><div>text</div>")
        assert "<script>" not in out
        assert "alert(1)" not in out  # script is a clean_content_tag — its content is dropped too
        assert "<div>" not in out
        assert out == "text"  # div is just unwrapped — its text content is kept

    def test_anchor_http_href_kept(self):
        out = L.sanitize_requirement_html('<a href="https://example.com">link</a>')
        assert 'href="https://example.com"' in out

    def test_anchor_mailto_href_kept(self):
        out = L.sanitize_requirement_html('<a href="mailto:a@b.com">mail</a>')
        assert 'href="mailto:a@b.com"' in out

    def test_anchor_javascript_href_stripped(self):
        out = L.sanitize_requirement_html('<a href="javascript:alert(1)">bad</a>')
        assert "javascript:" not in out

    def test_anchor_onclick_attr_stripped(self):
        out = L.sanitize_requirement_html('<a href="https://x.com" onclick="evil()">x</a>')
        assert "onclick" not in out

    def test_span_font_size_style_kept(self):
        out = L.sanitize_requirement_html('<span style="font-size: 18px">big</span>')
        assert "font-size" in out

    def test_span_other_style_properties_stripped(self):
        out = L.sanitize_requirement_html('<span style="font-size:18px;position:fixed;color:red">x</span>')
        assert "position" not in out
        assert "color" not in out
        assert "font-size" in out

    def test_plain_text_untouched(self):
        assert L.sanitize_requirement_html("plain text, no html") == "plain text, no html"
