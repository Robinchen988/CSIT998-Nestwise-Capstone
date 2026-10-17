import csv
from pathlib import Path

_CSV = Path(__file__).resolve().parents[2] / "data" / "suburb_crime.csv"

_data: dict | None = None


def _load() -> dict:
    global _data
    if _data is not None:
        return _data
    if not _CSV.exists():
        raise RuntimeError(
            f"Crime data missing ({_CSV.name}). Restore the data folder from the repository.")
    rows = {}
    year = None
    with _CSV.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rows[row["suburb"].strip().lower()] = {
                "lga": row["lga"],
                "rate_per_100k": float(row["rate_per_100k"]),
            }
            year = row.get("year") or year
    _data = {"by_suburb": rows, "year": year}
    return _data


def warm_up() -> dict:
    data = _load()
    return {"suburbs": len(data["by_suburb"]), "year": data["year"]}


def crime_for(suburb) -> dict | None:
    if not suburb:
        return None
    return _load()["by_suburb"].get(str(suburb).strip().lower())


def describe(info: dict | None) -> str:
    if not info:
        return "该区无罪案数据"
    data = _load()
    return (f"所在 {info['lga']} 区罪案率 {info['rate_per_100k']:,.0f} 起/10 万人"
            f"({data['year']} 年,**LGA 级**数据,同区所有房源相同)")
