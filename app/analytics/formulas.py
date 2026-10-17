def gross_yield(annual_rent: int | None, price: int | None) -> float | None:
    if annual_rent is None or not price:
        return None
    return annual_rent / price


def noi(annual_rent: int | None, operating_expenses: int | None) -> int | None:
    if annual_rent is None or operating_expenses is None:
        return None
    return annual_rent - operating_expenses


def cap_rate(noi_value: int | None, price: int | None) -> float | None:
    if noi_value is None or not price:
        return None
    return noi_value / price


def roi(net_gain: int | None, total_cost: int | None) -> float | None:
    if net_gain is None or not total_cost:
        return None
    return net_gain / total_cost


_VIC_DUTY_BRACKETS = (
    (25_000,    0,     0.014, 0),
    (130_000,   350,   0.024, 25_000),
    (960_000,   2_870, 0.06,  130_000),
    (None,      0,     0.055, 0),
)


def stamp_duty_vic(price: int | None) -> int | None:
    if price is None or price <= 0:
        return None
    for cap, base, rate, threshold in _VIC_DUTY_BRACKETS:
        if cap is None or price <= cap:
            if float(price).is_integer():
                milli = base * 1000 + round(rate * 1000) * (int(price) - threshold)
                dollars, rest = divmod(milli, 1000)
            else:
                exact = base + rate * (price - threshold)
                dollars, rest = int(exact), (exact - int(exact)) * 1000
            return dollars + (1 if rest > 500 else 0)
    return None


def _duty_unrounded(price: float) -> float:
    for cap, base, rate, threshold in _VIC_DUTY_BRACKETS:
        if cap is None or price <= cap:
            return base + rate * (price - threshold)
    raise AssertionError("unreachable: last bracket has no cap")


def roi_unrounded(price, annual_rent, opex_rate: float, other_costs: float) -> float | None:
    if price is None or annual_rent is None or price <= 0:
        return None
    total = price + _duty_unrounded(price) + other_costs
    return annual_rent * (1 - opex_rate) / total if total > 0 else None


def roi_order_sql(rent_col: str = "annual_rent", price_col: str = "price",
                  opex_param: str = "%(opex_rate)s::float8", fees_param: str = "%(other_costs)s::float8") -> str:
    p = price_col
    whens, tail = [], None
    for cap, base, rate, threshold in _VIC_DUTY_BRACKETS:
        term = f"{base} + {rate} * ({p} - {threshold})"
        if cap is None:
            tail = term
        else:
            whens.append(f"WHEN {p} <= {cap} THEN {term}")
    duty = f"CASE WHEN {p} <= 0 THEN NULL {' '.join(whens)} ELSE {tail} END"
    return f"(({rent_col} * (1 - {opex_param})) * 1.0 / NULLIF({p} + ({duty}) + {fees_param}, 0))"


def roi_certified_prefix(rois_desc: list, cutoff: float | None, other_costs: float) -> int:
    if cutoff is None:
        return len(rois_desc)
    denom = float(other_costs) - 0.5
    if denom <= 0:
        return 0
    threshold = cutoff + (0.5 + 1.5 * cutoff) / denom + 1e-9
    n = 0
    for r in rois_desc:
        if r is None or not r > threshold:
            break
        n += 1
    return n


def investment_metrics(price, annual_rent, opex_rate: float, other_costs: float) -> dict:
    operating_expenses = (
        int(round(annual_rent * opex_rate)) if annual_rent is not None else None
    )
    noi_value = noi(annual_rent, operating_expenses)
    if noi_value is not None:
        noi_value = int(round(noi_value))
    duty = stamp_duty_vic(price)
    total_cost = price + duty + other_costs if price and duty is not None else None
    return {
        "gross_yield": gross_yield(annual_rent, price),
        "operating_expenses": operating_expenses,
        "noi": noi_value,
        "cap_rate": cap_rate(noi_value, price),
        "roi": roi(noi_value, total_cost),
        "stamp_duty": duty,
        "total_cost": int(total_cost) if total_cost is not None else None,
    }
