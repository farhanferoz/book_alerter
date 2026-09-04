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

## T0.2 — Delivery-location pinning probe

_Pending._

## T0.4 — Product-page fixtures

_Pending._

## T0.5 — Bookfinder condition strings

_Pending._
