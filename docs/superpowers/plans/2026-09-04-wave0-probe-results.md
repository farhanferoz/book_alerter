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

## T0.4 — Product-page fixtures

_Pending._

## T0.5 — Bookfinder condition strings

_Pending._
