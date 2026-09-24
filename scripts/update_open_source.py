"""Refresh the animated open-source panel in the profile README."""

import json
import math
import os
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
USER = "dakjdakd"
OUT = ROOT / "assets" / "open-source-terminal.gif"
FONT = ROOT / "assets" / "fonts" / "JetBrainsMono-Regular.ttf"
BOLD = ROOT / "assets" / "fonts" / "JetBrainsMono-Bold.ttf"
TZ = timezone(timedelta(hours=8))


def api(path, **params):
    url = "https://api.github.com/" + path
    if params:
        url += "?" + urlencode(params)
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "dakjdakd-profile"}
    if token := os.environ.get("GH_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        return json.load(response)


def merged_prs():
    results = []
    for page in range(1, 11):  # GitHub Search exposes at most 1,000 results.
        data = api("search/issues", q=f"is:pr is:merged author:{USER}", per_page=100, page=page)
        results.extend(
            item for item in data["items"]
            if item["pull_request"]["merged_at"]
            and item["repository_url"].split("/repos/", 1)[1].split("/", 1)[0].lower() != USER.lower()
        )
        if page * 100 >= data["total_count"]:
            break
    return sorted(results, key=lambda item: item["pull_request"]["merged_at"], reverse=True)


def shorten(value, font, max_width, draw):
    if draw.textlength(value, font=font) <= max_width:
        return value
    while value and draw.textlength(value + "…", font=font) > max_width:
        value = value[:-1]
    return value + "…"


def stars(value):
    return f"{value / 1000:.1f}k" if value >= 1000 else str(value)


def draw_panel(prs):
    recent = prs[:10]
    count = max(1, len(recent))
    width, height = 1000, 46 + 209 + 85 + 37 + count * 43 + 44
    image = Image.new("RGB", (width, height), "#081a10")
    d = ImageDraw.Draw(image)
    reg = lambda n: ImageFont.truetype(FONT, n)
    bold = lambda n: ImageFont.truetype(BOLD, n)
    f11, f12, f13, f25, f40 = reg(11), reg(12), reg(13), bold(25), bold(40)
    green, bright, dim, line = "#81e990", "#a4ffaf", "#5fae6d", "#28643a"

    # A quiet scanline texture keeps the reference's terminal feel.
    for y in range(0, height, 4):
        d.line((1, y, width - 2, y), fill="#0a1d12")
    d.rectangle((0, 0, width - 1, height - 1), outline="#28583a")
    d.rectangle((1, 1, width - 2, 45), fill="#0d2718")
    d.text((20, 16), "///// OSS_SIGNAL_MONITOR /////", font=f11, fill=green)
    d.text((width - 20, 16), "[ AUTO SYNC: ON ]", font=f11, fill=green, anchor="ra")
    for y in (46, 255, 340, 377 + count * 43):
        for x in range(0, width, 8):
            d.line((x, y, min(x + 4, width), y), fill=line)

    d.text((width / 2, 88), "DAKJDAKD / PUBLIC ACTIVITY", font=f12, fill=green, anchor="mm")
    d.text((width / 2, 226), f"COMMUNITY CONTRIBUTIONS SYNCED  /  {len(prs)} MERGED PRs", font=f12, fill=green, anchor="mm")
    metric_labels = ["MERGED PULL REQUESTS", "PUBLIC REPOSITORIES", "LATEST MERGE"]
    repos = {p["repository_url"].split("/repos/", 1)[1] for p in prs}
    latest = datetime.fromisoformat(prs[0]["pull_request"]["merged_at"].replace("Z", "+00:00")).astimezone(TZ).strftime("%m.%d") if prs else "--"
    metric_values = [f"{len(prs):02}", f"{len(repos):02}", latest]
    for i, (value, label) in enumerate(zip(metric_values, metric_labels)):
        x = 20 + i * (width // 3)
        d.text((x, 273), value, font=f25, fill=bright)
        d.text((x, 315), label, font=f11, fill=dim)
        if i < 2:
            for y in range(256, 340, 8):
                d.line((width // 3 * (i + 1), y, width // 3 * (i + 1), y + 4), fill=line)
    d.text((20, 354), f"> VERIFIED MERGED CONTRIBUTIONS [{len(recent):02}/{len(prs):02}]", font=f11, fill=green)
    d.text((width - 20, 354), "REPO STARS", font=f11, fill=green, anchor="ra")

    repo_stars = {}
    for pr in recent:
        repo = pr["repository_url"].split("/repos/", 1)[1]
        if repo not in repo_stars:
            repo_stars[repo] = api(f"repos/{repo}")["stargazers_count"]
    for i, pr in enumerate(recent):
        y = 377 + i * 43
        d.line((0, y, width, y), fill="#1a422a")
        repo = pr["repository_url"].split("/repos/", 1)[1]
        merged = datetime.fromisoformat(pr["pull_request"]["merged_at"].replace("Z", "+00:00")).astimezone(TZ)
        d.text((20, y + 14), f"{i+1:02}", font=f12, fill=dim)
        d.text((65, y + 14), merged.strftime("%m.%d"), font=f12, fill=bright)
        title = shorten(f"{repo} / {pr['title']}", f13, 680, d)
        d.text((160, y + 13), title, font=f13, fill=bright)
        star_text = stars(repo_stars[repo])
        d.text((width - 20, y + 13), star_text, font=f13, fill=bright, anchor="ra")
        star_x = width - 31 - d.textlength(star_text, font=f13)
        star_y = y + 20
        points = [
            (star_x + (6 if n % 2 == 0 else 2.6) * math.sin(n * math.pi / 5),
             star_y - (6 if n % 2 == 0 else 2.6) * math.cos(n * math.pi / 5))
            for n in range(10)
        ]
        d.polygon(points, fill=bright)
    if not recent:
        d.text((width / 2, 399), "NO MERGED PULL REQUESTS YET", font=f12, fill=green, anchor="mm")
    foot_y = 377 + count * 43
    d.rectangle((1, foot_y + 1, width - 2, height - 2), fill="#0d2718")
    now = datetime.now(TZ).strftime("%Y.%m.%d / %H:%M CST")
    d.text((20, foot_y + 17), f"> LAST_SYNC: {now}", font=f11, fill=green)
    d.text((width - 20, foot_y + 17), "EXPLORE ALL MERGED PRs >", font=f11, fill=green, anchor="ra")
    return image, f40


def animated_gif(base, title_font):
    title1, title2 = "OPEN SOURCE", "ONLINE"
    draw = ImageDraw.Draw(base)
    x1 = (base.width - draw.textlength(title1, font=title_font)) / 2
    x2 = (base.width - draw.textlength(title2 + "_", font=title_font)) / 2

    def frame(first, second, cursor=False):
        image = base.convert("RGBA")
        glow = Image.new("RGBA", image.size)
        g = ImageDraw.Draw(glow)
        g.text((x1, 111), first, font=title_font, fill=(80, 255, 110, 180))
        g.text((x2, 157), second + ("_" if cursor else ""), font=title_font, fill=(80, 255, 110, 180))
        image = Image.alpha_composite(image, glow.filter(ImageFilter.GaussianBlur(7)))
        d = ImageDraw.Draw(image)
        d.text((x1, 111), first, font=title_font, fill="#a4ffaf")
        d.text((x2, 157), second + ("_" if cursor else ""), font=title_font, fill="#a4ffaf")
        return image.convert("RGB").quantize(colors=48, method=Image.Quantize.MEDIANCUT)

    frames, durations = [frame("", "")], [200]
    for i in range(1, len(title1) + 1):
        frames.append(frame(title1[:i], ""))
        durations.append(100)
    frames.append(frame(title1, ""))
    durations.append(150)
    for i in range(1, len(title2) + 1):
        frames.append(frame(title1, title2[:i]))
        durations.append(100)
    frames.extend([frame(title1, title2, True), frame(title1, title2), frame(title1, title2, True)])
    durations.extend([400, 250, 2400])
    buffer = BytesIO()
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:], duration=durations, loop=0, optimize=True, disposal=2)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(buffer.getvalue())


if __name__ == "__main__":
    prs = merged_prs()
    base, title_font = draw_panel(prs)
    animated_gif(base, title_font)
    print(f"Rendered {len(prs)} merged PRs ({min(len(prs), 10)} visible) to {OUT}")
