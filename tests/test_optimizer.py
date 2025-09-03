from app.optimizer import haversine_km

def test_haversine_zero():
    assert abs(haversine_km((0,0),(0,0))) < 1e-9
