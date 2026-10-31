import unittest

from app.errors import describe_validation_errors, validation_detail


def err(kind, loc, **ctx):
    return {"type": kind, "loc": loc, "msg": "English text", "ctx": ctx}


class ValidationMessageTests(unittest.TestCase):
    def test_range_errors(self):
        items = describe_validation_errors([err("greater_than_equal", ("query", "hour"), ge=6),
                                            err("less_than_equal", ("query", "hour"), le=23)])
        self.assertEqual(items[0], {"field": "hour", "message": "ən azı 6 olmalıdır"})
        self.assertEqual(items[1]["message"], "ən çoxu 23 ola bilər")

    def test_missing_and_types(self):
        items = describe_validation_errors([err("missing", ("query", "route_id")),
                                            err("int_parsing", ("query", "stop_seq")),
                                            err("float_parsing", ("body", "minutes"))])
        self.assertEqual([i["field"] for i in items], ["route_id", "stop_seq", "minutes"])
        self.assertEqual(items[0]["message"], "məcburi parametr göndərilməyib")
        self.assertEqual(items[1]["message"], "tam ədəd olmalıdır")

    def test_pattern_lists_allowed_values(self):
        e = err("string_pattern_mismatch", ("query", "direction"), pattern="^(forward|backward)$")
        self.assertEqual(describe_validation_errors([e])[0]["message"],
                         "bu dəyərlərdən biri olmalıdır: forward, backward")

    def test_nested_body_field_and_fallbacks(self):
        self.assertEqual(describe_validation_errors([err("missing", ("body", "a", "b"))])[0]["field"], "a.b")
        self.assertEqual(describe_validation_errors([err("missing", ("body",))])[0]["field"], "body")
        self.assertEqual(describe_validation_errors([{"type": "weird", "loc": (), "msg": "x"}])[0],
                         {"field": "sorğu", "message": "x"})

    def test_detail_is_single_string(self):
        d = validation_detail([err("greater_than_equal", ("query", "hour"), ge=6)])
        self.assertIsInstance(d, str)
        self.assertIn("hour", d)


if __name__ == "__main__":
    unittest.main()
