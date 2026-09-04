"""live_offers_deterministic_tiebreak — S4 (2026-09-04 shipping-chain review):
`book_live_offers` / `product_live_offers`'s ROW_NUMBER tiebreak drops
`total_minor ASC`.

D14 says current-best selection lives in Python, in one place, "so the
Prime rule and the unknown-shipping rule (rank on effective, cascade-imputed
total) apply in one place." The view's `latest_per_offer` CTE partitions by
`({id}, source, condition, COALESCE(seller, ''))` and needs SOME tiebreak
when that partition carries more than one live row in a single scrape (e.g.
WOB "Very Good" + "Like New" both normalising to `used_vg`) -- but ranking
that tiebreak on raw `total_minor` was a second, divergent copy of the
ranking rule, on the WRONG metric: `total_minor` is `price + (shipping or
0)`, which folds unknown shipping to zero, so it systematically favours an
unknown-shipping row over a genuinely cheaper known-shipping one. Reviewer's
reproduction: a live offer with observed price=3000/shipping=299 (raw total
3299) was discarded in a partition against one with price=3100/shipping=NULL
(raw total 3100) -- the survivor's true effective total (3100 + a 299
cascade estimate = 3399) was actually the MORE expensive of the two, but
`total_minor ASC` never let Python see the alternative to compare.

Switching the tiebreak to `id ASC` removes the bias without pretending to
solve ranking in SQL at all -- `id` carries no pricing information, so
whichever row survives is arbitrary rather than systematically wrong in one
direction. This is deliberately NOT a full fix: within a partition, Python's
selection still only ever sees whichever single row this tiebreak keeps: a
genuinely cheaper offer sharing the exact same (source, condition, seller)
slot in the exact same scrape can still lose to a co-partitioned row. The
reviewer judged this reachable but rare (needs two distinct live prices in
one partition in one scrape) and structural rather than demonstrated on a
committed capture. Fully deferring the choice to Python would mean not
collapsing the partition at all, a larger change than this finding asked
for.

Purely a view swap -- no table or data changes, so this migration is much
lower-risk than 0021/0020's table rebuilds. Down-migration restores the
0021-era DDL (`total_minor ASC` reinstated), which is exactly what shipped
between migrations 0021 and this one.
"""

from __future__ import annotations

from alembic import op

from book_alerter.db.views import (
    BOOK_LIVE_OFFERS_VIEW_SQL,
    DROP_BOOK_LIVE_OFFERS_VIEW_SQL,
    DROP_PRODUCT_LIVE_OFFERS_VIEW_SQL,
    PRODUCT_LIVE_OFFERS_VIEW_SQL,
)

revision = "0024_live_offers_deterministic_tiebreak"
down_revision = "0023_product_metadata_status"
branch_labels = None
depends_on = None

# 0021-era DDL (total_minor ASC as the ROW_NUMBER tiebreak), frozen here for
# the downgrade path -- identical to what db/views.py exported before this
# migration repointed its "current" constants at the id-based tiebreak. Same
# rationale as migrations 0020/0021's own inlined copies of their
# predecessor's DDL.
_PRE_0024_BOOK_LIVE_OFFERS_VIEW_SQL = """
CREATE VIEW book_live_offers AS
WITH live_offers AS (
    SELECT o.book_id, o.source, o.total_minor, o.price_minor, o.shipping_minor,
           o.condition, o.seller, o.url, o.id, o.last_seen_at AS last_seen
    FROM priceobservation o
    WHERE o.source != 'keepa'
),
latest_scrape_per_source AS (
    SELECT book_id, source, MAX(last_seen) AS latest_seen
    FROM live_offers
    GROUP BY book_id, source
),
entity_latest AS (
    SELECT book_id, MAX(latest_seen) AS global_latest
    FROM latest_scrape_per_source
    GROUP BY book_id
),
latest_per_offer AS (
    SELECT lo.book_id, lo.source, lo.total_minor, lo.price_minor, lo.shipping_minor,
           lo.condition, lo.seller, lo.url,
           ROW_NUMBER() OVER (
               PARTITION BY lo.book_id, lo.source, lo.condition, COALESCE(lo.seller, '')
               ORDER BY lo.last_seen DESC, lo.total_minor ASC, lo.id ASC
           ) AS rn
    FROM live_offers lo
    JOIN latest_scrape_per_source l
      ON l.book_id = lo.book_id AND l.source = lo.source
    JOIN entity_latest g ON g.book_id = lo.book_id
    WHERE lo.last_seen = l.latest_seen
      AND julianday(g.global_latest) - julianday(l.latest_seen) <= 1.0
)
SELECT book_id, source, total_minor, price_minor, shipping_minor, condition, seller, url
FROM latest_per_offer
WHERE rn = 1
"""

_PRE_0024_PRODUCT_LIVE_OFFERS_VIEW_SQL = """
CREATE VIEW product_live_offers AS
WITH live_offers AS (
    SELECT o.product_id, o.source, o.total_minor, o.price_minor, o.shipping_minor,
           o.condition, o.seller, o.url, o.id, o.last_seen_at AS last_seen
    FROM productobservation o
    WHERE o.source != 'keepa'
),
latest_scrape_per_source AS (
    SELECT product_id, source, MAX(last_seen) AS latest_seen
    FROM live_offers
    GROUP BY product_id, source
),
entity_latest AS (
    SELECT product_id, MAX(latest_seen) AS global_latest
    FROM latest_scrape_per_source
    GROUP BY product_id
),
latest_per_offer AS (
    SELECT lo.product_id, lo.source, lo.total_minor, lo.price_minor, lo.shipping_minor,
           lo.condition, lo.seller, lo.url,
           ROW_NUMBER() OVER (
               PARTITION BY lo.product_id, lo.source, lo.condition, COALESCE(lo.seller, '')
               ORDER BY lo.last_seen DESC, lo.total_minor ASC, lo.id ASC
           ) AS rn
    FROM live_offers lo
    JOIN latest_scrape_per_source l
      ON l.product_id = lo.product_id AND l.source = lo.source
    JOIN entity_latest g ON g.product_id = lo.product_id
    WHERE lo.last_seen = l.latest_seen
      AND julianday(g.global_latest) - julianday(l.latest_seen) <= 1.0
)
SELECT product_id, source, total_minor, price_minor, shipping_minor, condition, seller, url
FROM latest_per_offer
WHERE rn = 1
"""


def upgrade() -> None:
    op.execute(DROP_BOOK_LIVE_OFFERS_VIEW_SQL)
    op.execute(DROP_PRODUCT_LIVE_OFFERS_VIEW_SQL)
    op.execute(BOOK_LIVE_OFFERS_VIEW_SQL)
    op.execute(PRODUCT_LIVE_OFFERS_VIEW_SQL)


def downgrade() -> None:
    op.execute(DROP_BOOK_LIVE_OFFERS_VIEW_SQL)
    op.execute(DROP_PRODUCT_LIVE_OFFERS_VIEW_SQL)
    op.execute(_PRE_0024_BOOK_LIVE_OFFERS_VIEW_SQL)
    op.execute(_PRE_0024_PRODUCT_LIVE_OFFERS_VIEW_SQL)
