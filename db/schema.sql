-- IIT Khana schema. Phase 1.
--
-- Design notes:
--   * No `date` column. The source is a repeating weekly cycle (DECISIONS.md D3).
--   * No `serving_window` column. No source data exists (DECISIONS.md D11).
--   * One row per ITEM, not per meal. The source ships comma-joined strings.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS hall (
    id         INTEGER PRIMARY KEY,   -- upstream campusmess hall id, NOT hall number
    name       TEXT    NOT NULL UNIQUE,
    hall_type  TEXT,                  -- Boys | Girls | Co-ed
    location   TEXT
);

CREATE TABLE IF NOT EXISTS menu_item (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    hall_id          INTEGER NOT NULL REFERENCES hall(id),
    day_of_week      TEXT    NOT NULL,   -- Monday..Sunday
    meal             TEXT    NOT NULL,   -- Breakfast | Lunch | Dinner

    item_raw         TEXT    NOT NULL,   -- exactly as upstream wrote it
    item_normalized  TEXT    NOT NULL,   -- lowercased, depunctuated, parens dropped
    item_canonical   TEXT    NOT NULL,   -- cluster representative (spelling-normalised)
    tags             TEXT,               -- veg | egg | non-veg. NULL until Phase 1 C2.

    is_extra         INTEGER NOT NULL,   -- 0 = base menu (description), 1 = extras
    position         INTEGER NOT NULL,   -- index within its field; makes the row addressable
    source_row_id    INTEGER,            -- upstream menu row id
    last_updated     TEXT,               -- upstream lastUpdated, ISO8601

    -- Natural key. Makes re-ingest idempotent: the same slot+field+position
    -- always overwrites itself rather than inserting a duplicate.
    UNIQUE (hall_id, day_of_week, meal, is_extra, position)
);

CREATE INDEX IF NOT EXISTS idx_menu_slot   ON menu_item (day_of_week, meal);
CREATE INDEX IF NOT EXISTS idx_menu_hall   ON menu_item (hall_id);
CREATE INDEX IF NOT EXISTS idx_menu_canon  ON menu_item (item_canonical);
CREATE INDEX IF NOT EXISTS idx_menu_tags   ON menu_item (tags);

-- One row per (hall, day, meal) slot, whether or not it produced any items.
-- Exists so the coverage report can distinguish "empty" from "absent", which a
-- menu_item-only schema cannot do.
CREATE TABLE IF NOT EXISTS slot (
    hall_id       INTEGER NOT NULL REFERENCES hall(id),
    day_of_week   TEXT    NOT NULL,
    meal          TEXT    NOT NULL,
    n_base        INTEGER NOT NULL,   -- items parsed from `description`
    n_extra       INTEGER NOT NULL,   -- items parsed from `extras`
    status        TEXT    NOT NULL,   -- present | empty | malformed
    note          TEXT,               -- why, when status != present
    PRIMARY KEY (hall_id, day_of_week, meal)
);
