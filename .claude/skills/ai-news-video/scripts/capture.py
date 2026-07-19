"""Robust URL -> 16:9 PNG screenshot capturer for news/release pages.

Usage:  PYTHONPATH=<repo> python capture.py <jobs.json>
        jobs.json = [{"url": "...", "out": "/abs/path/shotNN.png", "scroll": 300?}, ...]

Strips consent modals / sticky overlays with JS (selector clicks can't reach
consent iframes), captures a 1600x900 viewport at 2x. Prints one JSON result
line per job; "ok" is a byte-size heuristic only — ALWAYS view the PNGs
(contact sheet) before using them; cookie walls and error pages produce
plausible-sized files.
"""
import asyncio, json, sys
from pathlib import Path

from src.animation.frame_renderer import _chromium_launch_kwargs

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
STRIP = """() => {
  const kill=[];
  document.querySelectorAll('*').forEach(el=>{const s=getComputedStyle(el);const z=parseInt(s.zIndex)||0;
    if((s.position==='fixed'||s.position==='sticky')&&z>=100&&el.getBoundingClientRect().height>150)kill.push(el);
    const id=(el.id||'')+' '+(el.className||'');
    if(/cookie|consent|gdpr|cmp|onetrust|didomi|sourcepoint|modal|overlay|backdrop|interstitial|paywall|newsletter|subscribe-popup/i.test(id)&&el.getBoundingClientRect().height>120)kill.push(el);});
  kill.forEach(el=>el.remove());
  document.querySelectorAll('iframe').forEach(f=>{const t=(f.title||f.id||f.src||'');if(/consent|cookie|cmp|sp_message|privacy/i.test(t))f.remove();});
  document.documentElement.style.overflow='auto';document.body.style.overflow='auto';document.body.style.position='static';
}"""


async def cap(browser, job):
    ctx = await browser.new_context(viewport={"width": 1600, "height": 900},
                                    user_agent=UA, device_scale_factor=2)
    pg = await ctx.new_page()
    try:
        await pg.goto(job["url"], wait_until="domcontentloaded", timeout=45000)
        await pg.wait_for_timeout(2600)
        try:
            await pg.evaluate(STRIP)
        except Exception:
            pass
        await pg.wait_for_timeout(500)
        if job.get("scroll"):
            await pg.evaluate(f"window.scrollTo(0,{job['scroll']})")
            await pg.wait_for_timeout(800)
        img = await pg.screenshot(type="png", timeout=60000, animations="disabled",
                                  clip={"x": 0, "y": 0, "width": 1600, "height": 900})
        out = Path(job["out"]); out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(img)
        print(json.dumps({"out": out.name, "ok": out.stat().st_size > 9000,
                          "bytes": out.stat().st_size, "url": job["url"]}))
    except Exception as e:
        print(json.dumps({"out": Path(job["out"]).name, "ok": False,
                          "error": f"{type(e).__name__}:{str(e)[:70]}", "url": job["url"]}))
    finally:
        await ctx.close()


async def main(jobs):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch(**_chromium_launch_kwargs())
        for j in jobs:
            await cap(b, j)
        await b.close()

if __name__ == "__main__":
    asyncio.run(main(json.loads(Path(sys.argv[1]).read_text())))
