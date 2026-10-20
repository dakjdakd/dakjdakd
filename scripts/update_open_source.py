"""Collect merged PRs and main-branch commits, build Pages and the README panel."""

import argparse
import base64
import hashlib
import html
import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.parse import quote

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
USER = "dakjdakd"
SITE = ROOT / "docs"
DATA_FILE = SITE / "open-source-data.json"
OUT = ROOT / "assets" / "open-source-terminal.gif"
CONFIG_FILE = ROOT / "scripts" / "open_source_config.json"
TEMPLATE = ROOT / "scripts" / "open_source_template.html"
TZ = timezone(timedelta(hours=8))


def api(path, **params):
    # gh is available locally and on GitHub's Ubuntu runners. Argument arrays
    # preserve search phrases without shell quoting or exposing the token.
    command = ["gh", "api", path, "-X", "GET", "-H", "Accept: application/vnd.github+json"]
    for key, value in params.items():
        command.extend(["-f", f"{key}={value}"])
    for attempt in range(3):
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=90)
        if result.returncode == 0:
            return json.loads(result.stdout)
        status = re.search(r"HTTP (\d+)", result.stderr)
        code = int(status[1]) if status else 0
        # GitHub's secondary limit is request-shape based and retrying the same
        # search immediately only extends the lockout. Let the collector keep
        # the last verified snapshot and try again on the next schedule tick.
        if code == 403 and "secondary rate limit" in result.stderr.lower():
            raise HTTPError(path, code, result.stderr.strip(), {}, None)
        if code not in (403, 429, 502, 503) or attempt == 2:
            raise HTTPError(path, code, result.stderr.strip(), {}, None)
        time.sleep(20)


def search(kind, query, start=None, end=None):
    date_field = "committer-date" if kind == "commits" else "created"
    search_query = query
    if start or end:
        period = f"{start}..{end}" if start and end else f">={start}" if start else f"<={end}"
        search_query += f" {date_field}:{period}"
    results = []
    for page in range(1, 11):
        for attempt in range(3):
            response = api(f"search/{kind}", q=search_query, per_page=100, page=page,
                           sort=date_field, order="asc")
            if not response.get("incomplete_results"):
                break
            if attempt < 2:
                time.sleep(2)
        if response.get("incomplete_results"):
            raise RuntimeError(f"GitHub {kind} search is incomplete; keeping the previous panel")
        if response["total_count"] > 1000:
            # GitHub caps each search at 1,000 results. Split into disjoint UTC
            # intervals, including within a day, until each query can paginate.
            latest = api(f"search/{kind}", q=search_query, per_page=1,
                         sort=date_field, order="desc")
            if latest.get("incomplete_results") or not response["items"] or not latest["items"]:
                raise RuntimeError(f"GitHub {kind} search is incomplete; keeping the previous panel")
            dates = [item["commit"]["committer"]["date"] if kind == "commits" else item["created_at"]
                     for item in (response["items"][0], latest["items"][0])]
            first, last = [datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
                           for value in dates]
            if last - first < timedelta(seconds=1):
                raise RuntimeError(f"GitHub {kind} search exceeds 1,000 results in one second; keeping the previous panel")
            middle = (first + (last - first) / 2).replace(microsecond=0)
            before = middle.isoformat(timespec="seconds").replace("+00:00", "Z")
            after = (middle + timedelta(seconds=1)).isoformat(timespec="seconds").replace("+00:00", "Z")
            return search(kind, query, start, before) + search(kind, query, after, end)
        results.extend(response["items"])
        if page * 100 >= response["total_count"]:
            break
    return results


def external(repo, config=None):
    config = config or {}
    excluded_owners = {USER.lower(), *(owner.lower() for owner in config.get("excluded_owners", []))}
    excluded_repos = {name.lower() for name in config.get("excluded_repositories", [])}
    return repo.split("/", 1)[0].lower() not in excluded_owners and repo.lower() not in excluded_repos


def normalize(pr, repo=None, config=None):
    repo = repo or pr["repository_url"].split("/repos/", 1)[1]
    merged = pr.get("merged_at") or (pr.get("pull_request") or {}).get("merged_at")
    if not merged or not external(repo, config) or pr.get("base", {}).get("repo", {}).get("private"):
        return None
    return {"repo": repo, "number": pr.get("number") or int(pr["html_url"].rsplit("/", 1)[1]),
            "html_url": pr["html_url"], "title": pr["title"], "merged_at": merged}


def credited(message, emails):
    # Names alone can match a different person; only verified account emails count.
    return any(email.lower() in emails for email in re.findall(
        r"^Co-authored-by:\s*[^\n<>]+<([^\s<>]+)>\s*$", message, re.I | re.M))


def rate_limited(error):
    return error.code in (403, 429) and (
        error.code == 429 or "secondary rate limit" in str(error.reason).lower())


def merged_prs(previous, config, previous_commits=()):
    # Keep verified history even when GitHub Search temporarily misses a result.
    prs = {p["html_url"]: p for p in previous if external(p["repo"], config)}
    search_available = True

    def optional_search(kind, query):
        nonlocal search_available
        if not search_available:
            return None
        try:
            return search(kind, query)
        except HTTPError as error:
            if not rate_limited(error):
                raise
            search_available = False
            print("GitHub Search is rate limited; keeping the previous verified snapshot", flush=True)
            return None

    print("Collecting authored and pinned merged PRs...", flush=True)
    for item in optional_search("issues", f"is:pr is:merged author:{USER}") or []:
        pr = normalize(item, config=config)
        if pr:
            prs[pr["html_url"]] = {**prs.get(pr["html_url"], {}), **pr}

    for entry in config["included_prs"]:
        if not external(entry["repo"], config):
            continue
        pr = normalize(api(f"repos/{entry['repo']}/pulls/{entry['number']}"), entry["repo"], config)
        if pr:
            prs[pr["html_url"]] = {**prs.get(pr["html_url"], {}), **pr}

    user = api(f"users/{USER}")
    emails = {f"{USER}@users.noreply.github.com", f"{user['id']}+{USER}@users.noreply.github.com"}
    if user.get("email"):
        emails.add(user["email"].lower())
    authored_commits = optional_search("commits", f"author:{USER} -user:{USER}") or []
    commits = {}
    for commit in authored_commits:
        if (commit.get("author") or {}).get("login", "").lower() == USER.lower():
            emails.add(commit["commit"]["author"]["email"].lower())
            repo = commit["repository"]["full_name"]
            if external(repo, config) and not commit["repository"].get("private") and not commit["repository"].get("fork"):
                commits[(repo, commit["sha"])] = commit

    print("Discovering verified co-author commit credits...", flush=True)
    queries = [f'"Co-authored-by: {USER}" -user:{USER}'] + [
        f'"Co-authored-by" "{email}" -user:{USER}' for email in sorted(emails)]
    for query in queries:
        for commit in optional_search("commits", query) or []:
            repo = commit["repository"]["full_name"]
            if external(repo, config) and not commit["repository"].get("private") and not commit["repository"].get("fork"):
                if credited(commit["commit"]["message"], emails):
                    commits[(repo, commit["sha"])] = commit

    standalone = {(c["repo"], c["sha"]): c for c in previous_commits if external(c["repo"], config)}
    # Recheck archived commits: a later PR association should replace the commit
    # entry, and temporary Search omissions must not erase verified contributions.
    for key, c in standalone.items():
        commits.setdefault(key, {"sha": c["sha"], "commit": {"message": c["title"],
                            "committer": {"date": c["committed_at"]}}})
    groups = {}
    for (repo, sha), commit in commits.items():
        groups.setdefault(sha, []).append((repo, commit))
    known_repos = {p["repo"] for p in prs.values()} | {c["repo"] for c in standalone.values()}
    repo_info, covered = {}, {}

    def pr_commit_shas(repo):
        if repo not in covered:
            covered[repo] = set()
            for p in prs.values():
                if p["repo"] != repo:
                    continue
                if "commit_shas" not in p:
                    info = api(f"repos/{repo}/pulls/{p['number']}")
                    shas = {info["merge_commit_sha"]} if info.get("merge_commit_sha") else set()
                    for page in range(1, 4):  # This endpoint exposes at most 250 PR commits.
                        items = api(f"repos/{repo}/pulls/{p['number']}/commits", per_page=100, page=page)
                        shas.update(c["sha"] for c in items)
                        if len(items) < 100:
                            break
                    p["commit_shas"] = sorted(shas)
                covered[repo].update(p["commit_shas"])
        return covered[repo]
    print(f"Checking PR associations for {len(groups)} distinct credited commits...", flush=True)
    for sha, candidates in groups.items():
        if any(sha in p.get("commit_shas", []) for p in prs.values()):
            for repo, _ in candidates:
                standalone.pop((repo, sha), None)
            continue
        # Repository copies contain identical upstream commits. Prefer the verified
        # source so a copied history does not masquerade as another contribution.
        known_sources = [(repo, c) for repo, c in candidates if repo in known_repos]
        for repo, commit in known_sources or candidates:
            number = re.search(r"\(#(\d+)\)\s*$", commit["commit"]["message"].splitlines()[0])
            if number and f"https://github.com/{repo}/pull/{number[1]}" in prs:
                standalone.pop((repo, sha), None)
                break
            # Some copied repositories rewrite parent SHAs. Verify their squash
            # commits against the original PR using message and author metadata.
            copied_pr = False
            if number and commit["commit"].get("author"):
                for p in prs.values():
                    if p["repo"] == repo or p["number"] != int(number[1]):
                        continue
                    if "merge_commit_fingerprint" not in p:
                        info = api(f"repos/{p['repo']}/pulls/{p['number']}")
                        merged = api(f"repos/{p['repo']}/commits/{info['merge_commit_sha']}")["commit"]
                        p["merge_commit_fingerprint"] = {"message": merged["message"], "author": merged["author"]}
                    fingerprint = p["merge_commit_fingerprint"]
                    original_author, copy_author = fingerprint["author"], commit["commit"]["author"]
                    if (fingerprint["message"] == commit["commit"]["message"]
                            and original_author["name"] == copy_author["name"]
                            and original_author["email"].lower() == copy_author["email"].lower()
                            and datetime.fromisoformat(original_author["date"].replace("Z", "+00:00"))
                            == datetime.fromisoformat(copy_author["date"].replace("Z", "+00:00"))):
                        standalone.pop((repo, sha), None)
                        copied_pr = True
                        break
            if copied_pr:
                break
            found = False
            for item in api(f"repos/{repo}/commits/{sha}/pulls", per_page=100):
                base_repo = item["base"]["repo"]
                if base_repo["private"] or base_repo["full_name"].lower() != repo.lower():
                    continue
                pr = normalize(item, repo, config)
                if pr:
                    prs[pr["html_url"]] = {**prs.get(pr["html_url"], {}), **pr}
                    found = True
            if found:
                standalone.pop((repo, sha), None)
                covered.pop(repo, None)
                break
            if sha in pr_commit_shas(repo):
                standalone.pop((repo, sha), None)
                break
            if repo not in repo_info:
                repo_info[repo] = api(f"repos/{repo}")
            info = repo_info[repo]
            if info["private"] or info["fork"]:
                continue
            branch = info["default_branch"]
            try:
                # Status remains on every page; page 2 omits large file diffs.
                comparison = api(f"repos/{repo}/compare/{sha}...{quote(branch, safe='')}", per_page=1, page=2)
            except HTTPError as error:
                if error.code == 404:
                    continue
                raise
            if comparison["status"] not in ("ahead", "identical"):
                standalone.pop((repo, sha), None)
                continue
            standalone[(repo, sha)] = {"repo": repo, "sha": sha, "branch": branch,
                "html_url": f"https://github.com/{repo}/commit/{sha}",
                "title": commit["commit"]["message"].splitlines()[0],
                "committed_at": commit["commit"]["committer"]["date"]}
            break
    # Reconcile every project: copied histories can contain commits from a PR in
    # another repository, and a later discovered PR can cover earlier candidates.
    if standalone:
        all_pr_shas = set().union(*(pr_commit_shas(repo) for repo in {p["repo"] for p in prs.values()}))
        standalone = {key: c for key, c in standalone.items() if c["sha"] not in all_pr_shas}
    return (sorted(prs.values(), key=lambda p: (p["merged_at"], p["html_url"]), reverse=True),
            sorted(standalone.values(), key=lambda c: (c["committed_at"], c["html_url"]), reverse=True))


def recognition_badges(readme, source):
    urls = list(dict.fromkeys(html.unescape(value.rstrip(".,;")) for value in
                             re.findall(r'https://[^\s<>"\')]+', readme)))
    rules = [
        ("hg", "HelloGitHub", "RECOMMENDED BY", r"https://hellogithub\.com/repository/[\w-]+", "https://api.hellogithub.com/v1/widgets/recommend.svg?"),
        ("ts", "GitHub Trending", "TRENDING RECORD", r"https://trendshift\.io/repositories/\d+", "https://trendshift.io/api/badge/repositories/"),
        ("ph", "Product Hunt", "FIND US ON", r"https://(?:www\.)?producthunt\.com/(?:posts|products)/[\w-]+", "https://api.producthunt.com/widgets/embed-image/"),
        ("award", "JavaScript Open Source Award 2026 — AI Project of the Year", "WINNER", r"https://tanstack\.com/blog/tanstack-open-source-awards-2026", "https://raw.githubusercontent.com/TanStack/ai/"),
    ]
    badges = []
    for kind, name, label, link_pattern, image_prefix in rules:
        link = next((url for url in urls if re.fullmatch(link_pattern + r"(?:\?[^#]*)?(?:#[^\s]*)?", url)), None)
        image = next((url for url in urls if url.startswith(image_prefix) and
                      (kind != "award" or "js-open-source-award-2026-ai-project-of-the-year.svg" in url)), None)
        if link and image:
            badges.append({"kind": kind, "name": name, "label": label, "url": link, "image": image, "source": source})
    return badges


def project_info(repo, config, old):
    info = api(f"repos/{repo}")
    if info["private"]:
        raise RuntimeError(f"Refusing to publish private repository {repo}")
    try:
        readme = api(f"repos/{repo}/readme")
        content = base64.b64decode(readme["content"]).decode("utf-8", errors="replace")
        badges = recognition_badges(content, readme["html_url"])
    except HTTPError as error:
        if error.code != 404:
            raise
        badges = old.get("badges", [])
    return {"repo": repo, "name": config["project_names"].get(repo, info["name"]),
            "stars": info["stargazers_count"], "description": info.get("description") or "",
            "url": info["html_url"], "badges": badges}


def stars(value):
    return f"{value / 1000:.1f}k" if value >= 1000 else str(value)


def content_signature(data):
    visible = {"prs": [{k:v for k,v in p.items() if k not in ("commit_shas", "merge_commit_fingerprint")} for p in data["prs"]],
               "commits": data.get("commits", []),
               "projects": [dict(p, stars=stars(p["stars"])) for p in data["projects"]]}
    digest = hashlib.sha256(json.dumps(visible, sort_keys=True, ensure_ascii=False).encode())
    for path in (Path(__file__), TEMPLATE, CONFIG_FILE, ROOT / "assets/fonts/JetBrainsMono-Regular.ttf", ROOT / "assets/fonts/JetBrainsMono-Bold.ttf"):
        digest.update(path.read_bytes().replace(b"\r\n", b"\n") if path.suffix != ".ttf" else path.read_bytes())
    return digest.hexdigest()


def panel_signature():
    try:
        with Image.open(OUT) as image:
            return image.info.get("comment", b"").decode()
    except (OSError, UnicodeDecodeError):
        return ""


def build_site(data):
    SITE.mkdir(exist_ok=True)
    (SITE / "fonts").mkdir(exist_ok=True)
    for name in ("JetBrainsMono-Regular.ttf", "JetBrainsMono-Bold.ttf"):
        (SITE / "fonts" / name).write_bytes((ROOT / "assets/fonts" / name).read_bytes())
    (SITE / ".nojekyll").touch()
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    page = TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    (SITE / "index.html").write_text(page, encoding="utf-8")


def collect():
    old = json.loads(DATA_FILE.read_text(encoding="utf-8")) if DATA_FILE.exists() else {"prs": [], "projects": []}
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    prs, commits = merged_prs(old["prs"], config, old.get("commits", []))
    activity = sorted(prs + commits, key=lambda p: p.get("merged_at") or p["committed_at"], reverse=True)
    repos = list(dict.fromkeys(p["repo"] for p in activity))
    existing = {p["repo"]: p for p in old["projects"]}
    print(f"Checking recognition badges in {len(repos)} project READMEs...", flush=True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        projects = list(pool.map(lambda repo: project_info(repo, config, existing.get(repo, {})), repos))
    data = {"prs": prs, "commits": commits, "projects": projects}
    signature = content_signature(data)
    changed = signature != panel_signature() or not (SITE / "index.html").exists()
    if changed:
        data.update(updated_at=datetime.now(TZ).isoformat(timespec="seconds"), signature=signature)
        build_site(data)
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(f"changed={str(changed).lower()}\n")
    print(f"{'Changed' if changed else 'No visible changes'}: {len(prs)} merged PRs + {len(commits)} main-branch commits across {len(projects)} projects")


def render_panel():
    from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout
    from functools import partial

    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(SITE)))
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as playwright:
            options = {"headless": True}
            if executable := os.environ.get("CHROMIUM_EXECUTABLE_PATH"):
                options["executable_path"] = executable
            browser = playwright.chromium.launch(**options)
            page = browser.new_page(viewport={"width": 1288, "height": 1100}, device_scale_factor=1)
            page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="domcontentloaded")
            page.evaluate("document.fonts.ready")
            try:
                page.wait_for_function("[...document.images].every(image => image.complete)", timeout=15000)
            except BrowserTimeout:
                pass
            page.evaluate("[...document.images].filter(i => !i.naturalWidth).forEach(i => { i.hidden=true; i.parentElement.classList.add('unavailable') })")
            page.add_style_tag(content=".cursor{animation:none!important;opacity:1!important}a{text-decoration:none!important}")
            panel = page.locator(".monitor")
            image = Image.open(BytesIO(panel.screenshot())).convert("RGB")
            frame = image.quantize(colors=128, method=Image.Quantize.MEDIANCUT)
            page.add_style_tag(content=".cursor{opacity:0!important}")
            other = Image.open(BytesIO(panel.screenshot())).convert("RGB").quantize(palette=frame)
            OUT.parent.mkdir(exist_ok=True)
            frame.save(OUT, format="GIF", save_all=True, append_images=[other], duration=[2400, 700],
                       loop=0, optimize=True, disposal=1, comment=data["signature"].encode())
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print(f"Rendered the first 6 projects to {OUT}; all projects available on Pages")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--render", action="store_true")
    args = parser.parse_args()
    render_panel() if args.render else collect()
