CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS properties (
    id             SERIAL PRIMARY KEY,
    suburb         TEXT NOT NULL,
    address        TEXT,
    property_type  TEXT NOT NULL CHECK (property_type IN ('house', 'apartment', 'townhouse')),
    price          INTEGER NOT NULL,
    bedrooms       INTEGER NOT NULL,
    bathrooms      INTEGER NOT NULL,
    car_spaces     INTEGER,
    land_size      REAL,
    building_area  REAL,
    year_built     INTEGER,
    distance_cbd   REAL NOT NULL,
    latitude       REAL,
    longitude      REAL,
    annual_rent    INTEGER NOT NULL,
    description    TEXT NOT NULL,
    embedding      vector(768) NOT NULL,
    sale_date      DATE,

    rent_source    TEXT
);

CREATE INDEX IF NOT EXISTS properties_embedding_idx
    ON properties USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS properties_price_idx ON properties (price);
CREATE INDEX IF NOT EXISTS properties_suburb_idx ON properties (suburb);
CREATE INDEX IF NOT EXISTS properties_bedrooms_idx ON properties (bedrooms);

CREATE TABLE IF NOT EXISTS users (
    id             SERIAL PRIMARY KEY,
    username       TEXT NOT NULL,
    username_key   TEXT NOT NULL UNIQUE,
    email          TEXT,
    email_key      TEXT UNIQUE,
    password_hash  TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions(user_id);

CREATE TABLE IF NOT EXISTS favorites (
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    property_id  INTEGER NOT NULL,
    snapshot     JSONB NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, property_id)
);
