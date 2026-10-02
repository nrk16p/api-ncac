"""Unit tests for services/ops/people_repo.py pieces that don't need a real Postgres connection."""
from types import SimpleNamespace

from services.ops import people_repo as P


class TestPersonFromUser:
    def _user(self, **kw):
        base = dict(
            id=1, username="kittaboon.l", employee_id="680001", firstname="Kittaboon",
            lastname="L.", image_url="https://img/a.png", department=None, position=None,
        )
        base.update(kw)
        return SimpleNamespace(**base)

    def test_full_mapping(self):
        dept = SimpleNamespace(department_name_en="IT")
        pos = SimpleNamespace(position_name_en="Engineer")
        user = self._user(department=dept, position=pos)
        person = P.person_from_user(user)
        assert person == {
            "employee_id": "680001", "name": "Kittaboon L.", "username": "kittaboon.l",
            "image_url": "https://img/a.png", "department": "IT", "position": "Engineer",
        }

    def test_missing_department_and_position_are_none(self):
        person = P.person_from_user(self._user())
        assert person["department"] is None
        assert person["position"] is None

    def test_missing_employee_id_falls_back_to_db_id(self):
        person = P.person_from_user(self._user(employee_id=None))
        assert person["employee_id"] == "1"

    def test_missing_lastname_uses_firstname_only(self):
        person = P.person_from_user(self._user(lastname=None))
        assert person["name"] == "Kittaboon"

    def test_missing_both_names_falls_back_to_username(self):
        person = P.person_from_user(self._user(firstname=None, lastname=None))
        assert person["name"] == "kittaboon.l"
