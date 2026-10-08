from fake_mongo import FakeClient
from pipeline_fuel_places import refresh_places

PLANT = {"_id": "x", "client": "ACON", "plant_code": "A109", "Latitude": "14.05", "Longitude": "100.56"}
POI = {"code": "L009", "name": "อู่ MENA", "lat": 13.76, "lng": 100.76, "radius": 150, "geofence": [], "status": "A"}


class FakeBesttech:
    def __init__(self, rows=None, error=None):
        self.rows, self.error = rows or [], error

    def location(self):
        if self.error:
            raise self.error
        return self.rows


def test_refresh_writes_plants_and_pois_and_drops_old_ones():
    client = FakeClient()
    client["atms"]["plants"].replace_one({"_id": "x"}, PLANT)
    client["analytics"]["fuel_places"].replace_one({"_id": "old"}, {"_id": "old"})
    result = refresh_places(client, FakeBesttech([POI]))
    ids = sorted(d["_id"] for d in client["analytics"]["fuel_places"].find({}))
    assert ids == ["besttech:L009", "plant:A109"] and result == {"plants": 1, "besttech_pois": 1, "records": 2}


def test_besttech_down_still_writes_plants():
    client = FakeClient()
    client["atms"]["plants"].replace_one({"_id": "x"}, PLANT)
    result = refresh_places(client, FakeBesttech(error=RuntimeError("TooManyRequests")))
    assert [d["_id"] for d in client["analytics"]["fuel_places"].find({})] == ["plant:A109"]
    assert result["besttech_error"] == "TooManyRequests" and result["records"] == 1


def test_besttech_down_keeps_last_weeks_pois():
    client = FakeClient()
    client["atms"]["plants"].replace_one({"_id": "x"}, PLANT)
    places = client["analytics"]["fuel_places"]
    places.replace_one({"_id": "besttech:L009"}, {"_id": "besttech:L009", "kind": "poi"})
    places.replace_one({"_id": "plant:OLD"}, {"_id": "plant:OLD", "kind": "plant"})
    refresh_places(client, FakeBesttech(error=RuntimeError("TooManyRequests")))
    assert sorted(d["_id"] for d in places.find({})) == ["besttech:L009", "plant:A109"]
