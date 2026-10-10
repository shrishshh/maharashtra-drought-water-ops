"""Headless browser check + screenshots of every screen (Playwright, Chromium).

Saves PNGs to outputs/screens/ (laptop 1366x900 and phone 390x844) and reports
console errors and failed API requests, so the design can be reviewed without
opening the site.

Usage:
  .venv\\Scripts\\python.exe scripts\\screenshots.py --url http://localhost:4173/
  .venv\\Scripts\\python.exe scripts\\screenshots.py --url https://main.<app>.amplifyapp.com/ --run-jobs
--run-jobs also submits one city_evaluate, one village_plan and one village_fraud job from the UI (live AWS).
--og writes frontend/public/og-image.png (1200x630, the city blunt-vs-fair comparison) for link previews.
The countdown is tested in both states with a mocked clock (before / after 16 Oct 2026 IST).
"""

import argparse
import io
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parents[1] / "outputs" / "screens"
OG = Path(__file__).resolve().parents[1] / "frontend" / "public" / "og-image.png"
IST = timezone(timedelta(hours=5, minutes=30))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--run-jobs", action="store_true")
    ap.add_argument("--og", action="store_true", help="write the Open Graph preview image")
    args = ap.parse_args()
    base = args.url.rstrip("/") + "/"
    OUT.mkdir(parents=True, exist_ok=True)
    problems, shots = [], []

    with sync_playwright() as p:
        browser = p.chromium.launch()

        def page_for(viewport, mobile=False):
            ctx = browser.new_context(viewport=viewport, device_scale_factor=1, is_mobile=mobile, has_touch=mobile)
            page = ctx.new_page()
            page.on("console", lambda m: m.type == "error" and problems.append(f"console: {m.text[:200]}"))
            page.on("requestfailed", lambda r: "openstreetmap" not in r.url and problems.append(f"request failed: {r.url[:120]}"))
            page.on("response", lambda r: r.status >= 400 and "/jobs" not in r.url and problems.append(f"HTTP {r.status}: {r.url[:120]}"))
            return page

        def shot(page, name, full=True, locator=None):
            path = OUT / f"{name}.png"
            (page.locator(locator).first.screenshot(path=str(path)) if locator
             else page.screenshot(path=str(path), full_page=full))
            shots.append(path.name)
            print(f"  saved {path.name}")

        def settle(page, text, timeout=30000):
            page.get_by_text(text, exact=False).first.wait_for(timeout=timeout)
            page.wait_for_timeout(1500)  # tiles / charts

        # ---------------- laptop
        page = page_for({"width": 1366, "height": 900})
        page.goto(base + "#/")
        settle(page, "areas run dry instead of")
        shot(page, "01_home", full=True)

        page.goto(base + "#/city")
        settle(page, "Blunt cut")
        shot(page, "02_city_top", full=False)
        shot(page, "03_city_full", full=True)
        shot(page, "04_city_leaks", locator="section.card:has-text('Leak zones')")
        if args.run_jobs:
            t0 = time.time()
            page.get_by_role("button", name="Simulate my plan").click()
            page.get_by_text("Your plan saves").wait_for(timeout=150000)
            print(f"  city job finished in the browser in {time.time() - t0:.0f} s")
            page.wait_for_timeout(800)
            shot(page, "05_city_your_plan", locator="section.card:has-text('Try your own plan')")

        if args.og:
            from PIL import Image

            og = browser.new_context(viewport={"width": 1240, "height": 900}).new_page()
            og.goto(base + "#/city")
            settle(og, "Blunt cut")
            og.add_style_tag(content=".topbar { display: none !important; }")  # sticky nav would cover the top
            img = Image.open(io.BytesIO(og.locator("section.grid2").first.screenshot()))
            w = img.width
            img = img.crop((0, 0, w, round(w * 630 / 1200))).resize((1200, 630), Image.LANCZOS)
            img.save(OG, optimize=True)
            print(f"  saved {OG.name} (1200x630)")

        page.goto(base + "#/village")
        settle(page, "First come, first served vs JalNyay")
        shot(page, "06_village_top", full=False)
        page.get_by_role("radio", name="Tuljapur + Naldurg").click()
        page.wait_for_timeout(2500)
        shot(page, "07_village_two_fill_points", full=False)
        shot(page, "08_village_compare", locator="section.card:has-text('First come, first served vs JalNyay')")
        shot(page, "08b_village_naldurg", locator="section.card:has-text('Add Naldurg as a second filling point')")
        shot(page, "09_village_fleet", locator="section.card:has-text('How many tankers')")
        page.get_by_role("button", name="C006").first.click() if page.get_by_role("button", name="C006").count() else \
            page.locator("button.claim").first.click()
        page.wait_for_timeout(2500)
        shot(page, "10_village_fraud", locator="section.card:has-text('Suspicious tanker trips')")
        if args.run_jobs:
            t0 = time.time()
            page.get_by_role("button", name="Re-run fraud check on AWS").click()
            page.get_by_text("Checked live on AWS Lambda").wait_for(timeout=90000)
            print(f"  fraud re-check finished in the browser in {time.time() - t0:.0f} s")
            shot(page, "10b_village_fraud_live", locator="section.card:has-text('Suspicious tanker trips')")
        shot(page, "11_village_ranked", locator="section.card:has-text('Villages ranked by need')")
        shot(page, "12_village_full", full=True)
        if args.run_jobs:
            page.get_by_role("radio", name="Tuljapur only").click()
            page.locator("input[aria-label='Number of tankers']").fill("8")
            t0 = time.time()
            page.get_by_role("button", name="Plan tankers on AWS").click()
            page.get_by_text("live from AWS Lambda").wait_for(timeout=200000)
            print(f"  village job finished in the browser in {time.time() - t0:.0f} s")
            page.wait_for_timeout(2500)
            shot(page, "13_village_live_8_tankers", full=False)

        page.goto(base + "#/how")
        settle(page, "Which AWS service does what")
        shot(page, "14_how_it_works", full=True)

        # ---------------- countdown, mocked clock (both states)
        for name, when, expect in [("00a_countdown_before", datetime(2026, 10, 15, 23, 59, 0, tzinfo=IST), "until the 10% water cut"),
                                   ("00b_countdown_day1", datetime(2026, 10, 16, 0, 0, 30, tzinfo=IST), "day 1"),
                                   ("00c_countdown_day5", datetime(2026, 10, 20, 12, 0, 0, tzinfo=IST), "day 5")]:
            cpage = page_for({"width": 1366, "height": 900})
            cpage.clock.set_fixed_time(when)
            cpage.goto(base + "#/")
            cpage.locator(".countdown").wait_for(timeout=30000)
            text = cpage.locator(".countdown").inner_text()
            after = "day" in expect
            hero = cpage.locator(".hero h1").inner_text()
            ok = (expect in text and (("in-force" in (cpage.locator(".countdown").get_attribute("class") or "")) == after)
                  and (("has been in force since 16 Oct 2026" in hero) if after else ("starts 16 Oct 2026" in hero)))
            print(f"  countdown at {when.isoformat()}: {'OK' if ok else 'WRONG'} -> {' '.join(text.split())[:80]}")
            if not ok:
                problems.append(f"countdown wrong at {when.isoformat()}: {text!r}")
            shot(cpage, name, locator=".hero")

        # ---------------- phone
        page = page_for({"width": 390, "height": 844}, mobile=True)
        for route, text, name in [("#/", "areas run dry instead of", "20_phone_home"),
                                  ("#/city", "Blunt cut", "21_phone_city"),
                                  ("#/village", "First come, first served vs JalNyay", "22_phone_village"),
                                  ("#/how", "Which AWS service does what", "23_phone_how")]:
            page.goto(base + route)
            settle(page, text)
            shot(page, name, full=False)
            overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
            if overflow:
                problems.append(f"horizontal scroll on phone: {route}")
        browser.close()

    print(f"\n{len(shots)} screenshots in {OUT}")
    print("Problems:" if problems else "No console errors, failed requests or phone overflow.")
    for pr in dict.fromkeys(problems):
        print("  -", pr)


if __name__ == "__main__":
    main()
