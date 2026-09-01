"""
Test tối thiểu cho routing/core.py — chỉ test các hàm THUẦN LOGIC
(không I/O, không phụ thuộc DB/network), theo đúng mục 5 trong kế hoạch
cấu trúc lại: haversine, feasibility check, và vài hàm suy luận liên quan.
"""
import math

from routing.core import (
    haversine_km,
    is_feasible,
    infer_visit_duration,
    simulate_day,
    safe_float,
    safe_int,
)


class TestHaversineKm:
    def test_same_point_is_zero(self):
        assert haversine_km(11.94, 108.45, 11.94, 108.45) == 0.0

    def test_known_distance_dalat_to_hcmc_approx(self):
        # Đà Lạt -> TP.HCM thực tế ~230-260km đường chim bay
        dist = haversine_km(11.9404, 108.4583, 10.7769, 106.7009)
        assert 220 < dist < 260

    def test_symmetric(self):
        a = haversine_km(11.94, 108.45, 12.00, 108.50)
        b = haversine_km(12.00, 108.50, 11.94, 108.45)
        assert math.isclose(a, b)


class TestIsFeasible:
    def _poi(self, open_min=8 * 60, close_min=17 * 60, visit_min=60):
        return {"open_min": open_min, "close_min": close_min, "visit_min": visit_min}

    def test_feasible_within_hours(self):
        poi = self._poi()
        ok, start, end = is_feasible(poi, arrive=9 * 60, user_end=20 * 60)
        assert ok is True
        assert start == 9 * 60
        assert end == 9 * 60 + 60

    def test_arrive_before_open_waits_until_open(self):
        poi = self._poi(open_min=8 * 60)
        ok, start, end = is_feasible(poi, arrive=6 * 60, user_end=20 * 60)
        assert ok is True
        assert start == 8 * 60  # chờ tới giờ mở, không bắt đầu lúc 6h

    def test_infeasible_when_ends_after_close(self):
        poi = self._poi(close_min=10 * 60, visit_min=60)
        ok, _, end = is_feasible(poi, arrive=9 * 60 + 30, user_end=20 * 60)
        assert ok is False
        assert end > poi["close_min"]

    def test_infeasible_when_ends_after_user_end(self):
        poi = self._poi(close_min=23 * 60, visit_min=60)
        ok, _, _ = is_feasible(poi, arrive=21 * 60 + 30, user_end=22 * 60)
        assert ok is False


class TestInferVisitDuration:
    def test_uses_csv_value_when_valid_and_not_default_45(self):
        assert infer_visit_duration("cafe", 1, 100, csv_value=90) == 90

    def test_falls_back_to_base_when_csv_value_is_default_45(self):
        # 45 bị coi là giá trị mặc định/chưa xác thực -> phải suy luận lại
        result = infer_visit_duration("cafe", 1, 100, csv_value=45)
        assert result != 45 or result == infer_visit_duration("cafe", 1, 100, csv_value=0)

    def test_never_below_15_minutes(self):
        result = infer_visit_duration("unknown_type_xyz", 0, 1, csv_value=0)
        assert result >= 15

    def test_high_review_count_increases_duration(self):
        low = infer_visit_duration("cafe", 1, 10, csv_value=0)
        high = infer_visit_duration("cafe", 1, 6000, csv_value=0)
        assert high > low


class TestSimulateDay:
    def test_empty_list_returns_zero(self):
        total_km, feasible, timeline = simulate_day([], user_start=8 * 60, user_end=20 * 60)
        assert total_km == 0.0
        assert feasible == 0
        assert timeline == []

    def test_single_reachable_poi_is_feasible(self):
        poi = {
            "lat": 11.94, "lng": 108.45,
            "open_min": 7 * 60, "close_min": 20 * 60, "visit_min": 30,
        }
        total_km, feasible, timeline = simulate_day(
            [poi], user_start=8 * 60, user_end=20 * 60,
            start_lat=11.94, start_lng=108.45,
        )
        assert feasible == 1
        assert len(timeline) == 1
        assert timeline[0]["feasible"] is True


class TestSafeCoercion:
    def test_safe_float_handles_junk(self):
        assert safe_float("nan") == 0.0
        assert safe_float(None) == 0.0
        assert safe_float("3.5") == 3.5
        assert safe_float("abc", default=1.0) == 1.0

    def test_safe_int_handles_junk(self):
        assert safe_int("") == 0
        assert safe_int("42") == 42
        assert safe_int("3.9") == 3  # ép qua float trước rồi int