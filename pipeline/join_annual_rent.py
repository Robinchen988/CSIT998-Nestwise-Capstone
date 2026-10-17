from datetime import date

import pandas as pd

PROPERTY_CSV = "data/Melbourne_housing_FULL.csv"
MAPPING_CSV = "data/suburb_precinct_mapping.csv"
BENCHMARKS_CSV = "data/rent_benchmarks_long.csv"
OUTPUT_CSV = "data/property_with_annual_rent.csv"


df = pd.read_csv(PROPERTY_CSV)
df["Suburb"] = df["Suburb"].replace({"Fawkner Lot": "Fawkner", "viewbank": "Viewbank"})

MANUAL_COORDS = {
    "MacLeod": (-37.7243102, 145.0590133),
    "Olinda": (-37.8557452, 145.1798592),
}
for suburb, (lat, lon) in MANUAL_COORDS.items():
    mask = df["Suburb"] == suburb
    df.loc[mask, "Lattitude"] = df.loc[mask, "Lattitude"].fillna(lat)
    df.loc[mask, "Longtitude"] = df.loc[mask, "Longtitude"].fillna(lon)

print("Property rows:", len(df))


mapping = pd.read_csv(MAPPING_CSV)
suburb_to_precinct = dict(zip(mapping["property_suburb"], mapping["matched_precinct"]))
suburb_to_region = dict(zip(mapping["property_suburb"], mapping["region"]))

benchmarks = pd.read_csv(BENCHMARKS_CSV)

precinct_lookup = {
    (row.dwelling_sheet, row.precinct, row.quarter): row.median_weekly_rent
    for row in benchmarks[benchmarks["granularity"] == "precinct"].itertuples()
}
region_lookup = {
    (row.dwelling_sheet, row.region, row.quarter): row.median_weekly_rent
    for row in benchmarks[benchmarks["granularity"] == "region"].itertuples()
}
print(f"Precinct lookup entries: {len(precinct_lookup)}")
print(f"Region lookup entries: {len(region_lookup)}")


def nearest_quarter_label(sale_date: pd.Timestamp) -> str:
    year = sale_date.year
    candidates = [
        (date(year - 1, 12, 31), f"Dec {year - 1}"),
        (date(year, 3, 31), f"Mar {year}"),
        (date(year, 6, 30), f"Jun {year}"),
        (date(year, 9, 30), f"Sep {year}"),
        (date(year, 12, 31), f"Dec {year}"),
        (date(year + 1, 3, 31), f"Mar {year + 1}"),
    ]
    sale_day = sale_date.date()
    return min(candidates, key=lambda c: abs((c[0] - sale_day).days))[1]


def dwelling_sheet_for(type_code, bedrooms) -> str | None:
    if pd.isna(bedrooms):
        return None
    bedrooms = int(bedrooms)
    if type_code in ("h", "t"):
        bucket = min(max(bedrooms, 2), 4)
        return f"{bucket} bedroom house"
    if type_code == "u":
        bucket = min(max(bedrooms, 1), 3)
        return f"{bucket} bedroom flat"
    return None


SALE_QUARTERS = [f"{q} {y}" for y in (2016, 2017, 2018) for q in ("Mar", "Jun", "Sep", "Dec")]

house_rent_rows = benchmarks[
    (benchmarks["granularity"] == "region")
    & (benchmarks["dwelling_sheet"].isin(["2 bedroom house", "3 bedroom house", "4 bedroom house"]))
    & (benchmarks["quarter"].isin(SALE_QUARTERS))
]
flat_rent_rows = benchmarks[
    (benchmarks["granularity"] == "region")
    & (benchmarks["dwelling_sheet"].isin(["1 bedroom flat", "2 bedroom flat", "3 bedroom flat"]))
    & (benchmarks["quarter"].isin(SALE_QUARTERS))
]

def count_weighted_avg_weekly_rent(rows: pd.DataFrame) -> float:
    return (rows["median_weekly_rent"] * rows["count"]).sum() / rows["count"].sum()

house_weekly_rent = count_weighted_avg_weekly_rent(house_rent_rows)
flat_weekly_rent = count_weighted_avg_weekly_rent(flat_rent_rows)

house_median_price = df.loc[df["Type"].isin(["h", "t"]), "Price"].median()
unit_median_price = df.loc[df["Type"] == "u", "Price"].median()

ASSUMED_YIELD = {
    "house": (house_weekly_rent * 52) / house_median_price,
    "unit": (flat_weekly_rent * 52) / unit_median_price,
}

print("Derived gross-yield assumption (2016-2018, this dataset + DFFH):")
print(f"  house/townhouse: weekly rent ${house_weekly_rent:.0f} -> annual ${house_weekly_rent*52:,.0f}"
      f" / median price ${house_median_price:,.0f} = {ASSUMED_YIELD['house']:.4f} gross yield")
print(f"  unit/apartment:  weekly rent ${flat_weekly_rent:.0f} -> annual ${flat_weekly_rent*52:,.0f}"
      f" / median price ${unit_median_price:,.0f} = {ASSUMED_YIELD['unit']:.4f} gross yield")


sale_dates = pd.to_datetime(df["Date"], dayfirst=True)

annual_rents = []
rent_sources = []

for row, sale_date in zip(df.itertuples(), sale_dates):
    quarter = nearest_quarter_label(sale_date)
    precinct = suburb_to_precinct.get(row.Suburb)
    region = suburb_to_region.get(row.Suburb)
    sheet = dwelling_sheet_for(row.Type, row.Bedroom2)

    weekly_rent = None
    source = None

    if precinct and pd.notna(precinct):
        if sheet and (sheet, precinct, quarter) in precinct_lookup:
            weekly_rent = precinct_lookup[(sheet, precinct, quarter)]
            source = "precinct_exact_sheet"
        elif ("All properties", precinct, quarter) in precinct_lookup:
            weekly_rent = precinct_lookup[("All properties", precinct, quarter)]
            source = "precinct_all_properties"

    if weekly_rent is None and region and pd.notna(region):
        if sheet and (sheet, region, quarter) in region_lookup:
            weekly_rent = region_lookup[(sheet, region, quarter)]
            source = "region_exact_sheet"
        elif ("All properties", region, quarter) in region_lookup:
            weekly_rent = region_lookup[("All properties", region, quarter)]
            source = "region_all_properties"

    if weekly_rent is not None:
        annual_rents.append(round(weekly_rent * 52))
        rent_sources.append(source)
        continue

    if row.Type in ("h", "t") and pd.notna(row.Price):
        annual_rents.append(round(row.Price * ASSUMED_YIELD["house"]))
        rent_sources.append("assumed_yield")
    elif row.Type == "u" and pd.notna(row.Price):
        annual_rents.append(round(row.Price * ASSUMED_YIELD["unit"]))
        rent_sources.append("assumed_yield")
    else:
        annual_rents.append(None)
        rent_sources.append("unavailable")

df["annual_rent"] = annual_rents
df["rent_source"] = rent_sources


print(df["rent_source"].value_counts())
print(f"\n{df['annual_rent'].notna().mean():.1%} of rows have an annual_rent value")

df.to_csv(OUTPUT_CSV, index=False)
print(f"\nSaved to {OUTPUT_CSV}")
