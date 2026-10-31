"""Doğrulama xətalarının Azərbaycan dilində, ardıcıl formatda təqdimi (FastAPI-dən asılı deyil)."""
from __future__ import annotations


def _pattern_options(pattern: str) -> str:
    return ", ".join(pattern.strip("^$()").split("|"))


def _message(err: dict) -> str:
    kind = err.get("type", "")
    ctx = err.get("ctx") or {}
    if kind == "missing":
        return "məcburi parametr göndərilməyib"
    if kind == "greater_than_equal":
        return f"ən azı {ctx.get('ge')} olmalıdır"
    if kind == "less_than_equal":
        return f"ən çoxu {ctx.get('le')} ola bilər"
    if kind == "greater_than":
        return f"{ctx.get('gt')} dəyərindən böyük olmalıdır"
    if kind == "less_than":
        return f"{ctx.get('lt')} dəyərindən kiçik olmalıdır"
    if kind in ("int_parsing", "int_from_float", "int_type"):
        return "tam ədəd olmalıdır"
    if kind in ("float_parsing", "float_type"):
        return "ədəd olmalıdır"
    if kind in ("bool_parsing", "bool_type"):
        return "true və ya false olmalıdır"
    if kind == "string_type":
        return "mətn olmalıdır"
    if kind == "string_pattern_mismatch":
        return f"bu dəyərlərdən biri olmalıdır: {_pattern_options(str(ctx.get('pattern', '')))}"
    if kind == "json_invalid":
        return "JSON formatı yanlışdır"
    return str(err.get("msg") or "yanlış dəyər")


def describe_validation_errors(errors: list[dict]) -> list[dict]:
    """Pydantic/FastAPI xətalarını [{field, message}] formasına salır ('query'/'body' prefiksi atılır)."""
    out = []
    for err in errors:
        loc = [str(p) for p in err.get("loc", ())]
        field = ".".join(loc[1:] if len(loc) > 1 else loc) or "sorğu"
        out.append({"field": field, "message": _message(err)})
    return out


def validation_detail(errors: list[dict]) -> str:
    """{detail: mətn} cavabı üçün tək sətir."""
    items = describe_validation_errors(errors)
    return "Yanlış sorğu: " + "; ".join(f"{i['field']} - {i['message']}" for i in items)
