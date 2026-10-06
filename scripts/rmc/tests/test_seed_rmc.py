import pytest

from seed_rmc import FIELDS, vehicle_docs


def test_vehicle_docs_keep_exactly_the_mapping_fields():
    docs = vehicle_docs({"data": [{"id": 1, "code": "6496", "plate_no": "71-6496 สบ.", "plate_no_only": "716496",
                                   "driver_name": "A", "driver_id": 10, "device_types_id": 2, "extra": "x"}]})
    assert list(docs[0]) == list(FIELDS) and "extra" not in docs[0]


def test_vehicle_docs_reject_empty_files():
    with pytest.raises(ValueError):
        vehicle_docs({"data": []})
