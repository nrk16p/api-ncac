import numpy as np
import pytest

from series_codec import (COLUMNS, FUEL_MISSING, decode_column, decode_columns, deg_to_int, encode_columns,
                          fuel_to_int, fuel_to_litres, int_to_deg)


def sample(n=3):
    return {
        "m": np.array([0, 1, 1439][:n]),
        "fuel": np.array([1824, FUEL_MISSING, 0][:n]),
        "fuel_lo": np.array([1800, FUEL_MISSING, 0][:n]),
        "fuel_hi": np.array([1850, FUEL_MISSING, 0][:n]),
        "speed": np.array([0, 45, 255][:n]),
        "engine": np.array([0, 1, 1][:n]),
        "lat": np.array([1379576, 0, 1428651][:n]),
        "lng": np.array([10055717, 0, 10078388][:n]),
    }


def test_round_trip():
    cols = sample()
    raw = encode_columns(cols)
    assert set(raw) == set(COLUMNS)
    back = decode_columns(raw, 3)
    for name in COLUMNS:
        assert back[name].tolist() == cols[name].tolist()


def test_decode_single_column():
    raw = encode_columns(sample())
    assert decode_column(raw["fuel_hi"], "fuel_hi", 3).tolist() == [1850, -1, 0]


def test_little_endian_layout():
    raw = encode_columns({**sample(2), "m": np.array([1, 256])})
    assert raw["m"] == b"\x01\x00\x00\x01"
    assert len(raw["lat"]) == 8 and len(raw["speed"]) == 2


def test_length_mismatch_raises():
    cols = sample()
    cols["speed"] = np.array([1, 2])
    with pytest.raises(ValueError):
        encode_columns(cols)


def test_missing_column_raises():
    cols = sample()
    del cols["lng"]
    with pytest.raises(ValueError):
        encode_columns(cols)


def test_fuel_scaling_litres_and_percent():
    assert fuel_to_int([182.44, None, -1, float("nan")], "dl").tolist() == [1824, -1, -1, -1]
    assert fuel_to_int([55.25, 100.0], "cpct").tolist() == [5525, 10000]
    litres = fuel_to_litres(np.array([5525, -1]), "cpct", 200.0)
    assert litres[0] == pytest.approx(110.5) and np.isnan(litres[1])
    assert fuel_to_litres(np.array([1824]), "dl", 999.0)[0] == pytest.approx(182.4)


def test_degrees():
    ints = deg_to_int([13.7957633, float("nan")])
    assert ints.tolist() == [1379576, 0]
    assert int_to_deg(ints)[0] == pytest.approx(13.79576)
