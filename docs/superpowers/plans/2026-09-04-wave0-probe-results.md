# Wave 0 probe results (2026-09-04)

Evidence file for the Wave 0 tasks of `2026-09-04-review-and-optimisation-plan.md`. Every
table here is measured output, not inference. Tasks that depend on these answers are named
under each conclusion.

---

## T0.3 — Browser-build probe

Two questions: (a) does the Docker base image ship the full Chromium build that
`channel="chromium"` requires, and (b) what does the browser actually look like to a
fingerprinting script under each launch mode?

### (a) Docker base image contents

Base image confirmed from `Dockerfile:32` — `mcr.microsoft.com/playwright/python:v1.59.0-noble`.
Inspected the **amd64** variant, because that is what production runs: the GHCR image is built by
`.github/workflows/build.yml` on `ubuntu-latest`. (This workstation is aarch64, so the pull used
`--platform linux/amd64`.)

```
$ docker run --rm --platform linux/amd64 mcr.microsoft.com/playwright/python:v1.59.0-noble \
    sh -c 'ls -1 /ms-playwright'
chromium-1217
chromium_headless_shell-1217
ffmpeg-1011
firefox-1511
webkit-2272

$ ... find /ms-playwright -maxdepth 3 -name chrome
/ms-playwright/chromium-1217/chrome-linux64/chrome
```

**Conclusion (a): the full Chromium build is already present.** `/ms-playwright/chromium-1217/`
contains `chrome-linux64/chrome` alongside `INSTALLATION_COMPLETE`, and the headless shell is a
separate directory (`chromium_headless_shell-1217/chrome-headless-shell-linux64/`).

⇒ **T1.1 must NOT add `RUN playwright install chromium` to the Dockerfile.** The plan made that
step conditional on this probe; the condition is not met. Adding it would re-download ~180 MB
into the image for no gain.

Incidental note for anyone re-running plan §6.3: that snippet globs
`chromium_headless_shell-*/chrome-linux/headless_shell`, which is the **arm64** directory layout.
On amd64 the path is `chrome-headless-shell-linux64/`. The snippet works on this workstation and
will not work in the container.

### (b) Fingerprint by launch mode

Measured on this workstation with the application's real launch arguments
(`src/book_alerter/sources/amazon.py:245-247`: `headless=True`,
`args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]`), Playwright 1.59.0,
Chromium build 1217 (Chrome for Testing 147.0.7727.15).

| Launch mode | `HeadlessChrome` in UA | `navigator.plugins.length` | `window.chrome` |
|---|---|---|---|
| `launch()` — **what the app does today** | **yes** | **0** | **undefined** |
| `launch(channel="chromium")` | yes | 5 | object |
| `launch_persistent_context()` | yes | 0 | undefined |
| `launch_persistent_context(channel="chromium")` | yes | 5 | object |
| `launch_persistent_context(channel="chromium", user_agent=<clean>)` | **no** | 5 | object |

Constant across every mode: `navigator.webdriver = false`, `navigator.languages = ['en-GB']`,
`hardwareConcurrency = 20`.

**Conclusion (b) — two findings, one of which corrects the plan.**

1. `channel="chromium"` is worth doing, and for a reason the plan did not state: it swaps the
   headless shell for the full Chrome build, which takes `navigator.plugins.length` from **0 to 5**
   and makes `window.chrome` a real object instead of `undefined`. Both are first-order
   headless-detection signals — arguably stronger tells than the UA string, because a UA is
   trivially spoofed and a missing plugin array is not.
2. **`channel="chromium"` does NOT remove the `HeadlessChrome` token from the user agent.**
   Finding F13 implies switching to the real-Chrome build gets you out of announcing headless;
   measurement says it does not. Only an explicit `user_agent=` override clears it.

   ⇒ This does **not** invalidate T1.1 — that task already specifies deriving a clean UA and
   states "never the literal `HeadlessChrome`". The prescription was right; only F13's rationale
   was incomplete. **No plan amendment is required for T1.1**, but the UA override is now known to
   be load-bearing rather than belt-and-braces, so it must not be dropped as redundant.
3. Persistent context makes no difference to the fingerprint on its own. Its value is exactly what
   F14 says — cookie and profile continuity across fetches — not stealth.

Recommended `BrowserSession` configuration for T1.1, all three together:
`launch_persistent_context(user_data_dir=..., channel="chromium", headless=True, args=[...],
user_agent=<derived, no "Headless">, locale="en-GB", timezone_id="Europe/London",
viewport=1366x768)`.

---

## T0.2 — Delivery-location pinning probe → **Q1 SETTLED (hypothesis F1c REJECTED)**

This probe was supposed to test whether pinning a delivery postcode changes the AOD delivery
promise. It answered a bigger question first, so both answers are recorded.

### The actual cause of the shipping flip (F1)

One live capture of book 3's AOD page (`969353137X`, 2026-09-04 15:02 UTC, 10 offer rows, no bot
challenge) shows the cause directly in the markup:

| Price | `data-csa-c-delivery-price` | Delivery promise text | Conditional? |
|---|---|---|---|
| — | `£2.80` | `£2.80 delivery 11 - 15 September.` | no |
| — | `FREE` | `FREE delivery 19 - 23 October **on your first order to UK or Ireland**.` | **YES** |
| — | `£2.80` | `£2.80 delivery 9 - 10 September.` (JJ_Books) | no |
| — | `FREE` | `FREE delivery 17 - 23 September **on your first order to UK or Ireland**.` | **YES** |
| … | … | 8 of 10 rows carry the same "on your first order" wording | **YES** |

Amazon is offering a **first-order promotional free delivery** to what it believes is a
brand-new visitor. `data-csa-c-delivery-price` reads `FREE`, so the parser stores
`shipping_minor = 0` — but that price applies only to a customer who has never ordered before.

Running the application's own production parser over that captured page:

```
$ parse_offer_listing(<captured html>, ...)
  price=1816  shipping=   0  seller=Retail Maharaj      cond=new
  price=2000  shipping= 280  seller=JJ_Books            cond=used_vg
  price=2350  shipping=   0  seller=Pappy Mart          cond=new
  price=2420  shipping=   0  seller=swestbooks          cond=used_vg
  price=2422  shipping=   0  seller=swestbooks          cond=new
  price=3125  shipping=   0  seller=Greyloop Limited    cond=new
  price=3711  shipping=   0  seller=Book_Bloom          cond=new
  price=4491  shipping=   0  seller=Fast Cat Books UK   cond=new
  price=4539  shipping=   0  seller=Fast Cat Books UK   cond=used_g

rows recorded as shipping = 0: 8/9
```

**Conclusion: F1 and F14 are the same bug.** `_fetch_offers_for_asin` builds a brand-new browser
with no cookies for every item fetch, so Amazon classifies every scrape as a first-time visitor
and frequently serves the first-order promo; the parser cannot tell a promotional price from a
real one and records £0.00. When the promo is not served, the identical offer records £2.80.
That is precisely the 280 / 0 / NULL alternation F1 measured.

The contrast confirms the mechanism rather than merely fitting it: the one row on this page with
an **unconditional** promise (JJ_Books, `£2.80 delivery 9 - 10 September`) is the one row the
parser gets right — and JJ_Books is the very seller F1 cites as flipping. When it was recorded as
0, the page must have shown it the promo too.

### The pinning question itself

Two independent attempts, both from the T0.3-validated launch configuration:

1. **API path** (the mechanism the plan named as a candidate). `POST` to the glow address-change
   endpoint. The required `anti-csrftoken-a2z` token could **not** be extracted from the served
   page (`token_found: false`); the POST returned 200 but was a no-op — after reload
   `#glow-ingress-line2` still read `Update location`, i.e. no location was set.
2. **UI path.** Click `#nav-global-location-popover-link`, fill `#GLUXZipUpdateInput`, submit.
   Failed earlier still: `#glow-ingress-line2` was not present on the served homepage at all
   (30 s timeout).

And decisively: **the delivery promises were identical with and without the pin attempt** — same
sellers, same "on your first order" wording, same `FREE` / `£2.80` split.

**Conclusion: hypothesis F1c (location-dependent promise) is REJECTED**, and the pinning
mechanism does not work for a logged-out headless session as specified.

⇒ **T1.2 is gated OUT by the plan's own condition** ("Only if T0.2 concluded the mechanism
works"). It is marked dropped rather than deleted, with this evidence, so it can be revisited if
a future need for stable delivery *dates* (as opposed to prices) appears.
⇒ **T2.5 takes its second branch**, which is now the primary fix rather than a fallback: a
`delivery_text`-driven rule treating a conditional promise as not-free. The observed marker is
`on your first order`, which the plan already listed verbatim.
⇒ **T1.5 (capture `delivery_text`) is a hard dependency of T2.5**, not a diagnostic nicety.
⇒ **T1.1's persistent profile helps the accuracy problem too**, not just the block rate: a
returning profile stops qualifying for the first-order promo, so the scraped value converges on
the price the user would actually pay.

Caveat, stated honestly: the pinning attempt was time-boxed to two mechanisms. A more elaborate
flow (full modal fetch to harvest the token) was not attempted, because the location hypothesis
had already been falsified by the promise text and so the task's motivation was gone.

## T0.4 — Product-page fixtures (complete: fixtures (a)–(e) captured, **Q3 answered**)

Fixture (e), the Echo Dot `B09B96TG33`, was captured by T0.1's new script on 2026-09-04 15:02 UTC
— both `dp` and `aod`, neither bot-challenged.

### Q3 — is the Echo Dot's empty offer listing genuine, or a parser gap? → **GENUINE**

Counting DOM nodes rather than string occurrences (the raw HTML contains the substring
`aod-offer` 22 times, but those are element ids, CSS rules and script templates, not rows):

| Selector | Nodes |
|---|---|
| `#aod-offer` | **0** |
| `.aod-offer` | 0 |
| `#aod-offer-list` | 1 — present but contains only inputs and one empty div |
| `#aod-pinned-offer` | **1** |
| `.olpOffer` | 0 |

So the offer-listing page genuinely carries **no third-party offer rows** — only the pinned
(Amazon's own) offer. `parse_offer_listing` returning 0 offers is correct behaviour, not a miss.

Crucially, no data is lost: the dp path captures that same offer correctly.

```
parse_dp(<Echo Dot dp fixture>) -> 1 candidate
  price_minor=7999  shipping_minor=0  seller='Amazon'  condition=Condition.NEW
```

⇒ **T4.2's "single-seller pages" case is confirmed already tolerated** — `parse_offer_listing`
returns `[]` without raising. The task reduces to adding the fixture test.

### Incidental: F6 confirmed on live markup

The Echo Dot dp has **`#merchant-info` = 0 nodes**, yet `parse_dp` still returned
`seller = 'Amazon'`. That is exactly the F6 defect — an unattributed buy box credited to Amazon.
Here the attribution happens to be true (it is an Amazon device), which is precisely why the bug
is easy to miss. **T2.7 should use this fixture as its regression test**; after T2.7 the expected
seller for this fixture becomes `None`, so T2.7 must update the assertion in the same commit.

### (a)–(d): candidates found and captured, 2026-09-04 16:22–16:30 UTC

The Echo Dot turns out **not** to be a variant page (`#twister` = 0 nodes; the 260 raw
occurrences of "twister" are all script/JSON), and it is in stock. So (a)–(d) needed different
ASINs. Method: searched Amazon UK search-results pages
(`div[data-component-type="s-search-result"][data-asin]`) for candidates, then verified every
marker by **counting selectolax DOM nodes on the actually-rendered page** — never by
substring-matching the raw HTML, per the lesson already learned above (`aod-offer` appears as a
raw substring 22 times on a page with 0 real offer rows).

| Fixture (`*-uk-<kind>-2026-09-04`) | Product | Markers exhibited | Node-count evidence |
|---|---|---|---|
| `B0F3NVWM37` (aod) | Nintendo Switch 2 — Donkey Kong Bananza | **(a)** multi-seller non-Amazon-brand, **(d)** used offers | `#aod-offer-list #aod-offer` = **10**. Running the production `parse_offer_listing` over the fixture: **9 distinct sellers** (CashC, The Games Exchange Ltd (GEX), Yard's Games ×2, Retro Games Europe, Fuzion, Hitcouk, Tekzone UK, TheGamery, RAREWAVES); conditions present: `used_vg` (The Games Exchange Ltd, £58.48), `used_g` (Yard's Games, £55.00), `new` (the other 8 rows). |
| `B0CYT8WL1G` (dp) | adidas Run 70s 2.0 Shoes | **(b)** size/colour variants | `#twister` (the plan's literal marker) = **0 nodes** — see finding below. Real markers: `#twister_feature_div` = **1**, `#inline-twister-row-color_name` = **1**, `#inline-twister-row-size_name` = **1**, `#inline-twister-image-0…42` = **43 image nodes** (one per variant thumbnail). |
| `B0GX54WT36` (dp) | Nintendo Switch 2 Console + Mario Kart World bundle | **(c)** currently unavailable | `#outOfStockBuyBox_feature_div` = **1**, text `"Currently unavailable. We don't know when or if this item will be back in stock."` `#corePriceDisplay_desktop_feature_div` = **0**, `#corePrice_feature_div` = **0**, `#add-to-cart-button` = **0** — no price and no buy path exist on this page at all, not just a hidden one. |
| `B09B96TG33` (already recorded above) | Echo Dot | **(e)** | single-seller Amazon buybox only (Q3, above) |

All four captures: zero `BOT_MARKERS` hits, real `<title>` matching the product, real `<link
rel="canonical">`.

**Finding: `#twister` no longer exists on live Amazon UK markup — the plan's literal marker is
stale.** Full attribute-enumeration of the adidas dp fixture (every `id` containing `twist`, ~90
distinct ids) turned up `#twister_feature_div`, `#apex_dp_twister` (present but empty, 0 nodes —
a legacy alias Amazon still renders but leaves unused), and the `inline-twister-*` family
(`inline-twister-row-<dimension>`, `inline-twister-image-<n>`, etc.) — but the bare `#twister` id
itself matches 0 nodes on both a genuine variant page (adidas) and a genuine non-variant page
(Echo Dot), so it cannot distinguish the two. **Any later task that checks for a variant page must
use `#twister_feature_div` (or `#inline-twister-row-color_name` /
`#inline-twister-row-size_name`), not `#twister`.**

**New finding — AOD-specific soft failure, not caught by `BOT_MARKERS`.** Two independent
`/gp/offer-listing/<asin>?condition=all` requests returned dp-page content instead of the
offer-listing page, silently:
- `B0CYT8WL1G`: first attempt returned real AOD content (4 third-party rows, seen only in an
  uncommitted exploratory probe); the official capture ~1 minute later, and a retry ~10 minutes
  after that, both came back with `<link rel="canonical" href=".../dp/B0DLSB1WWK">` — a
  **different ASIN's** dp page. (The committed `B0CYT8WL1G-uk-aod-2026-09-04.html` therefore does
  **not** exhibit marker (a) — only the dp-side twister markup was usable.)
- `B0F3NVWM37`: the first AOD capture (committed alongside the dp capture) returned
  `<link rel="canonical" href=".../dp/B0F3NVWM37">` — its **own** dp page, 0 offer rows. A retry
  ~20 minutes later (after doing unrelated T0.5 work in between) returned genuine AOD content with
  10 rows, which is the version committed.
- No attempt tripped `BOT_MARKERS` (no "Robot Check" / captcha text) in either failure mode — the
  page that came back was a normal, fully-formed dp page, just not the page that was asked for.
  `_render_amazon_page`'s bot-marker check cannot see this failure mode because there is nothing
  in the returned HTML that looks like an error.

⇒ Recommend whoever owns Wave 1's block-rate work take a look: a URL-vs-`<link rel="canonical">`
mismatch check (offer-listing request whose canonical URL doesn't contain `/gp/offer-listing/` or
whose ASIN doesn't match the request) would catch this where `BOT_MARKERS` cannot. Time-boxed
here rather than chased further, since T0.4's own retries already worked around it and this is a
new finding rather than the task in hand.

## T0.5 — Bookfinder condition strings

Captured book 2 (ISBN `9780008697211`) and book 5 (ISBN `9780753560686`) via
`--source bookfinder` on 2026-09-04 ~16:28 UTC:

- `tests/fixtures/bookfinder/9780008697211-gb-search-2026-09-04.html` — 215,611 bytes, 20 raw
  offer cards / **13** after `data-test-id` dedup, no WAF markers, title
  `"BookFinder.com: Search Results"`.
- `tests/fixtures/bookfinder/9780753560686-gb-search-2026-09-04.html` — 99,850 bytes, 4 raw offer
  cards / **2** after dedup, no WAF markers, same recognised title.

Method note: `BookfinderInlineSource._render()`'s signature changed mid-run (Wave 1's
`BrowserSessionMixin` refactor, uncommitted in the shared working tree at capture time — it now
takes a pre-opened `BrowserContext` instead of a `playwright_factory`), which broke T0.1's
committed script. Patched `scripts/capture_amazon_fixture.py`'s Bookfinder path to open its own
throwaway context and call the new signature (same commit as these fixtures) — the source's own
render/parse code was not touched.

Extracted the raw `Condition: <base> - <grade>` text with `_CONDITION_RE` (the exact regex
`_resolve_condition` already uses — nothing new invented) over every deduped card, cross-checked
against the structured `data-csa-c-condition` attribute, then ran each grade through the current
`condition_from_grade_text`:

| Raw condition/grade string | Structured `data-csa-c-condition` | Mapped `Condition` | Count | Seen in |
|---|---|---|---|---|
| `New` | `NEW` | `new` | 11 | both books |
| `Used - Like New` | `USED` | `used_vg` | 4 | book 2 only |

15 deduped cards total across both pages. Every card's regex match agreed with its structured
attribute, and every one resolved to a real `Condition` — **zero unmapped grade strings found**,
and zero `(no Condition: match)` cards.

**Conclusion: this specific pair of live captures does not reproduce the unknown-condition bug.**
Bookfinder marketplace inventory turns over; sellers listing grades like "Fine", "Near Fine", "As
New", "Fair" or "Ex-library" simply may not be carrying these two exact ISBNs right now, and/or
the production rows that landed as `unknown` were scraped at a different time with different
sellers live. This capture can neither confirm nor refute the plan's speculative T2.6 mapping
("Fine"/"Near Fine"/"As New" → `used_vg`; "Fair" → `used_acceptable`; "Ex-library" keeps its
grade) — there is no live evidence for or against those specific strings in this sample.

⇒ **T2.6 cannot be made evidence-based from this capture alone.** Whoever picks it up has two
honest paths: capture more books (ideally the exact ISBN + source-run combination that produced an
`unknown` row in the production DB, if still recoverable from raw JSON elsewhere), or ship the
plan's speculative mapping as a best-effort default — `_GRADE_HAYSTACK`'s existing
fallback-to-`unknown` means an unrecognised grade never mis-classifies, it just stays `unknown` as
it does today.
