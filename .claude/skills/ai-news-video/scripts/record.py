"""Record slow-scroll screen-capture clips (webm) of source pages, for native
`video` beats in collage scenes.

Usage:  PYTHONPATH=<repo> python record.py <jobs.json>
        jobs.json = [{"name": "clip01", "url": "...", "out_dir": "/abs/assets/shared",
                      "start_scroll": 0?, "total_scroll": 2600?}, ...]

Playwright recordVideo at 1600x900 (device_scale_factor MUST be 1 for
recordVideo); consent modals stripped; ~6s smooth Python-driven scroll.
The webm is finalized only on context close. Verify each clip afterward:
`ffmpeg -ss 3 -i clip.webm -frames:v 1 check.png` and LOOK at the frame.
"""
import asyncio, json, shutil, sys, tempfile
from pathlib import Path

from src.animation.frame_renderer import _chromium_launch_kwargs
from capture import STRIP, UA  # same modal-stripping JS


async def rec(browser, job, tmp):
    ctx = await browser.new_context(viewport={"width": 1600, "height": 900}, user_agent=UA,
                                    device_scale_factor=1, record_video_dir=str(tmp),
                                    record_video_size={"width": 1600, "height": 900})
    pg = await ctx.new_page()
    try:
        await pg.goto(job["url"], wait_until="domcontentloaded", timeout=45000)
        await pg.wait_for_timeout(2600)
        try:
            await pg.evaluate(STRIP)
        except Exception:
            pass
        await pg.wait_for_timeout(400)
        if job.get("start_scroll"):
            await pg.evaluate(f"window.scrollTo(0,{job['start_scroll']})")
            await pg.wait_for_timeout(500)
        total = job.get("total_scroll", 2600)
        steps = 34
        for _ in range(steps):
            await pg.evaluate(f"window.scrollBy(0,{total/steps})")
            await pg.wait_for_timeout(170)
        await pg.wait_for_timeout(400)
        vid = pg.video
        await ctx.close()  # finalizes the webm
        src = Path(await vid.path())
        dst = Path(job["out_dir"]) / f"{job['name']}.webm"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        print(json.dumps({"clip": dst.name, "ok": dst.exists(), "bytes": dst.stat().st_size}))
    except Exception as e:
        try:
            await ctx.close()
        except Exception:
            pass
        print(json.dumps({"clip": job["name"], "ok": False,
                          "error": f"{type(e).__name__}:{str(e)[:70]}", "url": job["url"]}))


async def main(jobs):
    from playwright.async_api import async_playwright
    tmp = Path(tempfile.mkdtemp(prefix="ainews_rec_"))
    async with async_playwright() as p:
        b = await p.chromium.launch(**_chromium_launch_kwargs())
        for j in jobs:
            await rec(b, j, tmp)
        await b.close()

if __name__ == "__main__":
    asyncio.run(main(json.loads(Path(sys.argv[1]).read_text())))
