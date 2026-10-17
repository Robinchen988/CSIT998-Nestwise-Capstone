import math
import sys
from pathlib import Path

import pandas as pd
import psycopg2
import torch
from sentence_transformers import SentenceTransformer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import DB_DSN

INPUT_CSV = "data/property_with_description.csv"
MODEL_NAME = "nomic-ai/nomic-embed-text-v1.5"
BATCH_SIZE = 256


def _pick_device() -> str:
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"

TYPE_LABEL = {"h": "house", "u": "apartment", "t": "townhouse"}


df = pd.read_csv(INPUT_CSV)
print("Rows:", len(df))


df["Bedroom2"] = df["Bedroom2"].fillna(df["Rooms"])

before = len(df)
df = df.dropna(subset=["Price", "Bedroom2", "Bathroom"]).reset_index(drop=True)
print(f"Dropped {before - len(df)} rows missing price/bedrooms/bathrooms")
print(f"Remaining: {len(df)} rows ({len(df) / before:.1%})")


model = SentenceTransformer(MODEL_NAME, trust_remote_code=True, device=_pick_device())

prefixed = ["search_document: " + text for text in df["description"]]
embeddings = model.encode(prefixed, batch_size=BATCH_SIZE, show_progress_bar=True, normalize_embeddings=True)

print("Embedding shape:", embeddings.shape)


def to_pynone(value):
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def row_to_record(row):
    return {
        "suburb": row.Suburb,
        "address": to_pynone(row.Address),
        "property_type": TYPE_LABEL.get(row.Type, row.Type),
        "price": int(row.Price) if pd.notna(row.Price) else None,
        "bedrooms": int(row.Bedroom2),
        "bathrooms": int(row.Bathroom),
        "car_spaces": int(row.Car) if pd.notna(row.Car) else None,
        "land_size": to_pynone(row.Landsize),
        "building_area": to_pynone(row.BuildingArea),
        "year_built": int(row.YearBuilt) if pd.notna(row.YearBuilt) and 1840 <= row.YearBuilt <= 2020 else None,
        "distance_cbd": float(row.Distance),
        "latitude": to_pynone(row.Lattitude),
        "longitude": to_pynone(row.Longtitude),
        "annual_rent": int(row.annual_rent),
        "description": row.description,
        "sale_date": pd.to_datetime(row.Date, dayfirst=True).date(),
        "rent_source": to_pynone(row.rent_source),
    }


conn = psycopg2.connect(DB_DSN)
cur = conn.cursor()

insert_sql = """
    INSERT INTO properties (
        suburb, address, property_type, price, bedrooms, bathrooms,
        car_spaces, land_size, building_area, year_built, distance_cbd,
        latitude, longitude, annual_rent, description, embedding,
        sale_date, rent_source
    ) VALUES (
        %(suburb)s, %(address)s, %(property_type)s, %(price)s, %(bedrooms)s, %(bathrooms)s,
        %(car_spaces)s, %(land_size)s, %(building_area)s, %(year_built)s, %(distance_cbd)s,
        %(latitude)s, %(longitude)s, %(annual_rent)s, %(description)s, %(embedding)s,
        %(sale_date)s, %(rent_source)s
    )
"""

inserted = 0
for row, embedding in zip(df.itertuples(), embeddings):
    record = row_to_record(row)
    record["embedding"] = embedding.tolist()
    cur.execute(insert_sql, record)
    inserted += 1

conn.commit()
print(f"Inserted: {inserted}")

cur.close()
conn.close()
