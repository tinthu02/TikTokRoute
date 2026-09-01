"""
Test tối thiểu cho scoring/scoring.py — chỉ test các hàm THUẦN LOGIC
(normalize, parse thời gian, tính điểm), không test load_and_merge/save_csv
vì đó là I/O (đọc/ghi CSV) — ngoài phạm vi mục 5.
"""
from scoring.scoring import (
    normalize_minmax,
    parse_time,
    fix_overnight_time,
    compute_final_scores,
)


class TestNormalizeMinmax:
    def test_scales_to_0_1_range(self):
        result = normalize_minmax([10, 20, 30])
        assert result[0] == 0.0
        assert result[-1] == 1.0
        assert 0.0 < result[1] < 1.0

    def test_all_equal_values_does_not_crash(self):
        result = normalize_minmax([5, 5, 5])
        assert len(result) == 3
        assert all(0.0 <= v <= 1.0 for v in result)

    def test_empty_list(self):
        assert normalize_minmax([]) == []


class TestParseTime:
    def test_parses_hh_mm(self):
        assert parse_time("08:30") == 8 * 60 + 30

    def test_invalid_input_returns_none(self):
        assert parse_time("") is None
        assert parse_time("không rõ") is None


class TestFixOvernightTime:
    def test_normal_hours_unchanged(self):
        open_m, close_m = fix_overnight_time(8 * 60, 17 * 60)
        assert open_m == 8 * 60
        assert close_m == 17 * 60

    def test_overnight_hours_adjusted(self):
        # Mở 22:00, đóng 02:00 -> đóng phải hiểu là sáng hôm sau (26:00)
        open_m, close_m = fix_overnight_time(22 * 60, 2 * 60)
        assert close_m > open_m

    def test_none_values_pass_through(self):
        open_m, close_m = fix_overnight_time(None, None)
        assert open_m is None
        assert close_m is None


class TestComputeFinalScores:
    def _poi(self, **overrides):
        base = {
            "name": "Test POI",
            "type": "cafe",
            "tiktok_views": 1000, "tiktok_likes": 100, "tiktok_comments": 10,
            "gmaps_rating": 4.5, "gmaps_reviews_count": 200,
            "open_time": "08:00", "close_time": "22:00",
        }
        base.update(overrides)
        return base

    def test_returns_same_number_of_pois(self):
        pois = [self._poi(name="A"), self._poi(name="B"), self._poi(name="C")]
        result = compute_final_scores(pois)
        assert len(result) == 3

    def test_sorted_by_attraction_score_descending(self):
        pois = [
            self._poi(name="Low", gmaps_rating=2.0, gmaps_reviews_count=5),
            self._poi(name="High", gmaps_rating=4.9, gmaps_reviews_count=5000),
        ]
        result = compute_final_scores(pois)
        scores = [r["attraction_score"] for r in result]
        assert scores == sorted(scores, reverse=True)

    def test_no_gmaps_rating_falls_back_to_tiktok_only(self):
        poi = self._poi(gmaps_rating=0, gmaps_reviews_count=0)
        result = compute_final_scores([poi])
        assert result[0]["attraction_score"] == result[0]["tiktok_score_norm"]

    def test_include_in_route_flag_present(self):
        result = compute_final_scores([self._poi()])
        assert "include_in_route" in result[0]
        assert isinstance(result[0]["include_in_route"], bool)