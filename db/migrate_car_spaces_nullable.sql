-- car_spaces: an unknown number of car spaces must be distinguishable from 0 spaces.
-- This file is a migration note; tests never execute it. Confirm before running it on an existing database.
--
-- 1) Relax the column constraint (safe and reversible; no existing value changes):
ALTER TABLE properties ALTER COLUMN car_spaces DROP NOT NULL;
ALTER TABLE properties ALTER COLUMN car_spaces DROP DEFAULT;
--
-- 2) Existing 0s cannot be told apart from unknown values inside the database, so no data is rewritten here.
--    To get correct NULLs, reload the data with pipeline/load_properties.py (a missing Car value becomes NULL).
--
-- 3) After the reload, retrain and rebuild the valuation artefacts:
--      python -m app.analytics.train_valuation
--      python -m app.analytics.crossfit_valuation
--      python -m app.analytics.calibrate_valuation
-- 4) A reload reassigns property ids, so saved favourites may point to a different property.
