from plates import normalize_plate, split_besttech_vehicle, terminus_plate


def test_normalize_standard_forms():
    assert normalize_plate("71-8623") == "สบ.71-8623"
    assert normalize_plate("สบ.71-0043") == "สบ.71-0043"
    assert normalize_plate("71-1191(Menatransport)") == "สบ.71-1191"
    assert normalize_plate("71 – 1191") == "สบ.71-1191"


def test_normalize_keeps_other_formats_trimmed():
    assert normalize_plate("  กว4506 ") == "กว4506"
    assert normalize_plate("3ฒภ5383") == "3ฒภ5383"


def test_normalize_empty():
    assert normalize_plate(None) is None
    assert normalize_plate("   ") is None
    assert normalize_plate(float("nan")) is None


def test_split_besttech_vehicle():
    assert split_besttech_vehicle("ME152 (71-8635 สบ.)") == ("ME152", "สบ.71-8635")
    assert split_besttech_vehicle("70-6294 สบ.") == (None, "สบ.70-6294")
    assert split_besttech_vehicle("") == (None, None)


def test_terminus_plate():
    assert terminus_plate("สบ.71-8623") == "71-8623"
    assert terminus_plate("71-8623") == "71-8623"
    assert terminus_plate("กว4506") == "กว4506"
