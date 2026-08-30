-- ---------------------------------------------------------------------------
-- 007_carousel_designs: reusable, user-owned carousel render contracts.
--
-- Apply AFTER 005_transfer_baseline. The SPA never talks to this table
-- directly: authenticated API routes enforce ownership and the backend uses
-- DATABASE_URL from .env. This keeps one source of truth across devices while
-- retaining the existing Supabase Auth session boundary.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS carousel_designs (
    owner_email text        NOT NULL,
    design_id   text        NOT NULL,
    name        text        NOT NULL,
    payload     jsonb       NOT NULL,
    sort_order  integer     NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_email, design_id),
    CHECK (jsonb_typeof(payload) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_carousel_designs_owner_order
    ON carousel_designs (owner_email, sort_order);

ALTER TABLE carousel_designs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE carousel_designs FROM anon, authenticated;

COMMENT ON TABLE carousel_designs IS
    'User-owned frozen carousel render contracts; accessed only by authenticated backend routes.';
COMMENT ON COLUMN carousel_designs.payload IS
    'Validated CarouselDesign JSON copied into agent session state when a run starts.';
