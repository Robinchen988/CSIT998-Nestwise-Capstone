from app.core.config import OPEX_RATE, OTHER_ACQUISITION_COSTS

_current = {
    "opex_rate": OPEX_RATE,
    "other_acquisition_costs": OTHER_ACQUISITION_COSTS,
}

_SPEC = {
    "opex_rate": {
        "label": "运营支出 = 年租金的 {pct},行业惯例 25%~30%,数据集不含实际支出",
        "kind": "rate",
        "bounds": (0.0, 1.0),
    },
    "other_acquisition_costs": {
        "label": "印花税之外的购置开销 {money}(过户、验房等),粗略值,可按实际报价改",
        "kind": "money",
        "bounds": (0, 200_000),
    },
}


def opex_rate() -> float:
    return _current["opex_rate"]


def other_acquisition_costs() -> int:
    return _current["other_acquisition_costs"]


def bounds(name: str) -> tuple[float, float]:
    if name not in _SPEC:
        raise KeyError(f"Unknown assumption {name!r}; available: {', '.join(_SPEC)}")
    return _SPEC[name]["bounds"]


def set_rate(name: str, value: float) -> None:
    if name not in _SPEC:
        raise KeyError(f"Unknown assumption {name!r}; available: {', '.join(_SPEC)}")
    low, high = _SPEC[name]["bounds"]
    if not low <= value < high:
        raise ValueError(f"{name} must be in [{low}, {high}); got {value}")
    _current[name] = value if _SPEC[name]["kind"] == "rate" else int(value)


def snapshot() -> dict:
    return dict(_current)


def describe(snap: dict | None = None) -> list[str]:
    values = _current if snap is None else snap
    out = []
    for key, spec in _SPEC.items():
        value = values[key]
        out.append(spec["label"].format(
            pct=f"{value * 100:.1f}%" if spec["kind"] == "rate" else "",
            money=f"${int(value):,}" if spec["kind"] == "money" else "",
        ))
    return out
