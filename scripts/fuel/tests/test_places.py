import pytest

from places import besttech_places, find_place, haversine_m, plant_places, point_in_polygon

SQUARE = [{"lat": 13.0, "lng": 100.0}, {"lat": 13.0, "lng": 100.01}, {"lat": 13.01, "lng": 100.01},
          {"lat": 13.01, "lng": 100.0}]


def test_haversine():
    assert haversine_m(13.0, 100.0, 13.0, 100.0) == 0
    assert haversine_m(13.0, 100.0, 13.001, 100.0) == pytest.approx(111.2, abs=0.5)


def test_point_in_polygon():
    assert point_in_polygon(13.005, 100.005, SQUARE)
    assert not point_in_polygon(13.02, 100.005, SQUARE)


def test_plant_places_from_atms_rows():
    rows = [{"client": "ACON", "plant_code": "A109", "Latitude": "14.0543628", "Longitude": "100.5684819"},
            {"client": "X", "plant_code": "", "Latitude": "14.0", "Longitude": "100.0"},
            {"client": "X", "plant_code": "B1", "Latitude": "nan", "Longitude": "100.0"}]
    assert plant_places(rows) == [{"_id": "plant:A109", "kind": "plant", "code": "A109", "name": "ACON A109",
                                   "lat": 14.0543628, "lng": 100.5684819, "radius_m": 300.0, "polygon": []}]


def test_besttech_places_keep_active_with_shape():
    rows = [{"code": "L008", "name": "LAB วัชรพล", "lat": 13.86578, "lng": 100.643171, "radius": 0,
             "geofence": SQUARE[:3] + [SQUARE[3]], "status": "A"},
            {"code": "L009", "name": "อู่ MENA", "lat": 13.76, "lng": 100.76, "radius": 150, "geofence": [], "status": "A"},
            {"code": "L010", "name": "old", "lat": 13.0, "lng": 100.0, "radius": 0, "geofence": [], "status": "D"}]
    out = besttech_places(rows)
    assert [p["_id"] for p in out] == ["besttech:L008", "besttech:L009"]
    assert len(out[0]["polygon"]) == 4 and out[1]["radius_m"] == 150.0 and out[1]["polygon"] == []


def test_find_place_nearest_containing():
    places = [{"name": "far", "lat": 13.0, "lng": 100.0, "radius_m": 300.0, "polygon": []},
              {"name": "near", "lat": 13.0005, "lng": 100.0, "radius_m": 300.0, "polygon": []},
              {"name": "poly", "lat": 13.005, "lng": 100.005, "radius_m": 0.0, "polygon": SQUARE}]
    assert find_place(13.0006, 100.0, places)["name"] == "near"
    assert find_place(13.009, 100.009, places)["name"] == "poly"
    assert find_place(14.0, 100.0, places) is None and find_place(None, None, places) is None
