"""Regression checks for contribution attribution and stable scheduled updates."""
from copy import deepcopy
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError

import update_open_source as panel


# Server errors and request timeouts get bounded retries in the shared client,
# including the compare endpoint that failed in workflow #155.
success = panel.subprocess.CompletedProcess([], 0, '{"status":"ahead"}', "")
for code in (500, 502, 503, 504):
    failure = panel.subprocess.CompletedProcess([], 1, "", f"gh: Server Error (HTTP {code})")
    with patch.object(panel.subprocess, "run", side_effect=[failure, success]) as run, \
            patch.object(panel.time, "sleep"):
        assert panel.api("repos/team/library/compare/sha...main")["status"] == "ahead"
        assert run.call_count == 2

timeout = panel.subprocess.TimeoutExpired(["gh", "api"], 90)
with patch.object(panel.subprocess, "run", side_effect=[timeout, success]) as run, \
        patch.object(panel.time, "sleep"):
    assert panel.api("repos/team/library")["status"] == "ahead"
    assert run.call_count == 2

failure = panel.subprocess.CompletedProcess([], 1, "", "gh: heavy server load (HTTP 500)")
with patch.object(panel.subprocess, "run", return_value=failure) as run, patch.object(panel.time, "sleep"):
    try:
        panel.api("repos/team/library/compare/sha...main")
        raise AssertionError("Repeated server errors must exhaust retries")
    except HTTPError as error:
        assert error.code == 500 and run.call_count == 3

# Retry exhaustion defers a run without rewriting the published data or GIF.
# Errors from discovery, comparison and project metadata share this protection.
with TemporaryDirectory() as directory:
    root = panel.Path(directory)
    data_file, gif_file = root / "open-source-data.json", root / "panel.gif"
    page_file = root / "index.html"
    for path in (data_file, gif_file, page_file):
        path.write_bytes(b"previous published content")
    output, summary = root / "output", root / "summary"
    with patch.object(panel, "DATA_FILE", data_file), patch.object(panel, "OUT", gif_file), \
            patch.object(panel, "SITE", root), patch.dict(panel.os.environ, {
                "GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary)}):
        for error in (HTTPError("search/commits", 500, "heavy server load", {}, None),
                      HTTPError("repos/team/library/compare/sha...main", 500, "heavy server load", {}, None),
                      HTTPError("repos/team/library/readme", 503, "unavailable", {}, None), timeout):
            with patch.object(panel, "collect_snapshot", side_effect=error), patch("builtins.print") as log:
                panel.collect()
                assert "::warning::Contribution refresh deferred" in log.call_args.args[0]
            assert output.read_text().endswith("changed=false\ndeferred=true\n")
            assert "No new data was published" in summary.read_text()
            assert all(path.read_bytes() == b"previous published content" for path in (data_file, gif_file, page_file))
        for code in (403, 404):
            with patch.object(panel, "collect_snapshot", side_effect=HTTPError("repos/team/library", code, "denied", {}, None)):
                try:
                    panel.collect()
                    raise AssertionError("Permanent errors must still fail")
                except HTTPError as error:
                    assert error.code == code
        data_file.unlink()
        with patch.object(panel, "collect_snapshot", side_effect=HTTPError("search/commits", 500, "unavailable", {}, None)):
            try:
                panel.collect()
                raise AssertionError("An outage without a published snapshot must fail")
            except HTTPError:
                pass


def pr(repo, number, merged="2026-10-11T00:00:00Z"):
    return {"number": number, "title": f"fix: issue {number}",
            "html_url": f"https://github.com/{repo}/pull/{number}",
            "repository_url": f"https://api.github.com/repos/{repo}",
            "merged_at": merged, "base": {"repo": {"full_name": repo, "private": False}}}


email = "164857476+dakjdakd@users.noreply.github.com"
config = {"included_prs": [{"repo": "vercel/ai", "number": 21228}, {"repo": "vercel/ai", "number": 21229}]}
archived = panel.normalize(pr("old/project", 5))


def commit(repo, sha, message, fork=False):
    return {"sha": sha, "repository": {"full_name": repo, "fork": fork}, "commit": {"message": message,
            "committer": {"date": "2026-10-09T17:45:00Z"}}}


def fake_search(kind, query):
    if kind == "issues":
        return [pr("vercel/ai", 21228), pr("sample/project", 1), pr("dakjdakd/own", 2), pr("sample/project", 3, None)]
    if query.startswith("author:"):
        return [{"author": {"login": "dakjdakd"}, "sha": "authored", "repository": {"full_name": "sample/project"},
                 "commit": {"author": {"email": email}, "message": "fix: issue 1 (#1)"}}]
    return [
        commit("team/library", "verified", f"fix\n\nCo-authored-by: First Person <other@example.com>\nCo-authored-by: Lucien <{email}>"),
        commit("team/library", "unrelated", "fix\n\nCo-authored-by: dakjdakd <different@example.com>"),
        commit("someone/fork", "forked", f"fix\n\nCo-authored-by: Lucien <{email}>", fork=True),
    ]


def fake_api(path, **params):
    if path.startswith("users/"):
        return {"id": 164857476, "email": None}
    if path == "repos/team/library/commits/verified/pulls":
        return [pr("team/library", 9), pr("dakjdakd/own", 10)]
    if path in ("repos/vercel/ai/pulls/21228", "repos/vercel/ai/pulls/21229"):
        return pr("vercel/ai", int(path.rsplit("/", 1)[1]))
    raise AssertionError(f"Unexpected API call: {path}")


with patch.object(panel, "search", side_effect=fake_search), patch.object(panel, "api", side_effect=fake_api):
    prs, commits = panel.merged_prs([archived], config)
assert len(prs) == 5
assert commits == []
assert {p["html_url"] for p in prs} == {
    "https://github.com/old/project/pull/5", "https://github.com/sample/project/pull/1",
    "https://github.com/team/library/pull/9", "https://github.com/vercel/ai/pull/21228",
    "https://github.com/vercel/ai/pull/21229",
}
assert not panel.credited(f"mentions {email} without an author trailer", {email})

# Personal accounts and known personal-project copies must be removed from
# archived history as well as fresh PR, author and co-author search results.
ownership_config = {"excluded_owners": ["dafyy321-pixel"],
                    "excluded_repositories": ["IbrahimKomba/skill-simmer"],
                    "included_prs": [{"repo": "DAFYY321-PIXEL/SnapCal", "number": 1}]}
personal_repos = ["dakjdakd/own", "dafyy321-pixel/SnapCal", "IbrahimKomba/skill-simmer"]
personal_commits = [dict(commit(repo, "personal", f"fix\n\nCo-authored-by: Lucien <{email}>"),
                         author={"login": panel.USER}) for repo in personal_repos]
for c in personal_commits:
    c["commit"]["author"] = {"email": email}
personal_history = [dict(repo=repo, sha="personal", title="fix",
                        committed_at="2026-10-01T00:00:00Z", html_url=f"https://github.com/{repo}/commit/personal")
                    for repo in personal_repos]
with patch.object(panel, "search", side_effect=lambda kind, query:
                  [pr(repo, 1) for repo in personal_repos] if kind == "issues" else personal_commits), \
        patch.object(panel, "api", side_effect=fake_api):
    owned_prs, owned_commits = panel.merged_prs([archived]+[
        {"repo": repo, "html_url": f"https://github.com/{repo}/pull/1"} for repo in personal_repos],
        ownership_config, personal_history)
assert owned_prs == [archived] and owned_commits == []
assert not panel.external("IBRAHIMKOMBA/SKILL-SIMMER", ownership_config)
assert not panel.external("DAFYY321-PIXEL/NEW-PERSONAL-PROJECT", ownership_config)
assert panel.external("another-team/skill-simmer", ownership_config)

# A GitHub secondary limit must keep the published snapshot usable instead of
# failing the scheduled job before Pages can be refreshed.
limited_error = HTTPError("search/commits", 403, "secondary rate limit", {}, None)
with patch.object(panel, "search", side_effect=limited_error), patch.object(panel, "api", side_effect=fake_api):
    limited_prs, limited_commits = panel.merged_prs([archived], {"included_prs": []}, [])
assert limited_prs == [archived] and limited_commits == []

# Default-branch ancestry, PR/commit deduplication and later PR discovery.
candidate_shas = ["main", "feature", "diverged", "squash", "in-pr", "later-covered", "discovery"]
def branch_search(kind, query):
    if kind == "issues":
        return [pr("team/library", 1)]
    if query.startswith("author:"):
        return [dict(commit("team/library", sha, f"fix: {sha}"), author={"login": panel.USER},
                     commit={"message": f"fix: {sha}", "author": {"email": email},
                             "committer": {"date": "2026-10-09T17:45:00Z"}}) for sha in candidate_shas]
    return []

def branch_api(path, **params):
    if path.startswith("users/"):
        return {"id": 164857476}
    if "/commits/" in path and path.endswith("/pulls"):
        sha = path.split("/commits/")[1].split("/")[0]
        return [pr("team/library", 2)] if sha == "discovery" else []
    if path.endswith("/pulls/1"):
        return dict(pr("team/library", 1), merge_commit_sha="squash")
    if path.endswith("/pulls/2"):
        return dict(pr("team/library", 2), merge_commit_sha="discovery")
    if path.endswith("/pulls/1/commits"):
        return [{"sha": "in-pr"}, {"sha": "copied"}]
    if path.endswith("/pulls/2/commits"):
        return [{"sha": "later-covered"}]
    if path == "repos/team/library":
        return {"private": False, "fork": False, "default_branch": "release/main"}
    if "/compare/" in path:
        assert path.endswith("...release%2Fmain")
        sha = path.split("/compare/")[1].split("...")[0]
        return {"status": {"feature": "behind", "diverged": "diverged", "archived": "identical"}.get(sha, "ahead")}
    raise AssertionError(f"Unexpected API call: {path}")

history = [{"repo": "team/library", "sha": "archived", "branch": "main", "title": "fix: archived",
            "html_url": "https://github.com/team/library/commit/archived", "committed_at": "2026-10-01T00:00:00Z"}]
with patch.object(panel, "search", side_effect=branch_search), patch.object(panel, "api", side_effect=branch_api):
    branch_prs, branch_commits = panel.merged_prs([], {"included_prs": []}, history)
assert {p["number"] for p in branch_prs} == {1, 2}
assert {c["sha"] for c in branch_commits} == {"main", "archived"}
assert all(c["branch"] == "release/main" for c in branch_commits)

def copied_api(path, **params):
    if path.startswith("repos/copy/library"):
        return branch_api(path.replace("copy/library", "team/library"), **params)
    return branch_api(path, **params)
copied_history = [dict(history[0], repo="copy/library", sha="copied", html_url="https://github.com/copy/library/commit/copied")]
with patch.object(panel, "search", side_effect=branch_search), patch.object(panel, "api", side_effect=copied_api):
    _, copy_commits = panel.merged_prs([], {"included_prs": []}, copied_history)
assert {c["sha"] for c in copy_commits} == {"main"}

# A repository copy rewrites the SHA but preserves the original squash credit.
original = dict(panel.normalize(pr("team/library", 1)), merge_commit_fingerprint={
    "message": "fix: issue 1 (#1)", "author": {"email": email, "date": "2026-10-01T00:00:00Z", "name": "Lucien"}})
rewritten = dict(commit("copy/library", "rewritten", "fix: issue 1 (#1)"), author={"login": panel.USER})
rewritten["commit"]["author"] = dict(original["merge_commit_fingerprint"]["author"], date="2026-10-01T08:00:00.000+08:00")
with patch.object(panel, "search", side_effect=lambda kind, query:
                  [rewritten] if kind == "commits" and query.startswith("author:") else []), patch.object(panel, "api", side_effect=branch_api):
    _, rewritten_commits = panel.merged_prs([original], {"included_prs": []})
assert rewritten_commits == []

# An archived standalone commit becomes a PR when GitHub adds its association.
with patch.object(panel, "search", return_value=[]), patch.object(panel, "api", side_effect=lambda path, **kw:
                  [pr("team/library", 3)] if path.endswith("/pulls") else {"id": 164857476}):
    converted_prs, converted_commits = panel.merged_prs([], {"included_prs": []}, history)
assert [p["number"] for p in converted_prs] == [3] and converted_commits == []
with patch.object(panel, "api", return_value={"incomplete_results": True, "total_count": 0, "items": []}), patch.object(panel.time, "sleep"):
    try:
        panel.search("issues", "anything")
        raise AssertionError("Incomplete search must not replace the published snapshot")
    except RuntimeError:
        pass

# Overflow requires recursive splitting, even when all results share one day.
search_items = []
for hour, count in [(0, 1000), (1, 1000), (2, 201)]:
    stamp = f"2026-10-16T0{hour}:00:00Z"
    search_items.extend({"id": f"{hour}-{i}", "created_at": stamp,
                         "commit": {"committer": {"date": stamp}}} for i in range(count))


def paged_search_api(path, **params):
    assert params["sort"] == ("committer-date" if path.endswith("commits") else "created")
    items = search_items
    if " " in params["q"]:
        period = params["q"].split(" ", 1)[1].split(":", 1)[1]
        if ".." in period:
            start, end = period.split("..")
        elif period.startswith(">="):
            start, end = period[2:], "9999"
        else:
            assert period.startswith("<=")
            start, end = "", period[2:]
        items = [item for item in items if start <= item["created_at"] <= end]
    items = sorted(items, key=lambda item: item["created_at"], reverse=params["order"] == "desc")
    offset = (params.get("page", 1) - 1) * params["per_page"]
    return {"total_count": len(items), "incomplete_results": False,
            "items": items[offset:offset + params["per_page"]]}


for kind in ("commits", "issues"):
    with patch.object(panel, "api", side_effect=paged_search_api):
        found = panel.search(kind, "anything")
    assert len(found) == len({item["id"] for item in found}) == 2201
    assert {item["id"] for item in found} == {item["id"] for item in search_items}

with patch.object(panel, "api", side_effect=lambda path, **params:
                  {"total_count": 201, "incomplete_results": True, "items": []}
                  if "committer-date:>=" in params["q"] else paged_search_api(path, **params)), \
        patch.object(panel.time, "sleep"):
    try:
        panel.search("commits", "anything")
        raise AssertionError("An incomplete partition must not replace the published snapshot")
    except RuntimeError:
        pass

search_items = search_items[:1000]
with patch.object(panel, "api", side_effect=paged_search_api):
    assert len(panel.search("commits", "anything")) == 1000
search_items.append(dict(search_items[0], id="overflow"))
with patch.object(panel, "api", side_effect=paged_search_api):
    try:
        panel.search("commits", "anything")
        raise AssertionError("An unsplittable search must not silently truncate contributions")
    except RuntimeError:
        pass

readme = '''<a href="https://trendshift.io/repositories/14318?utm_source=repository-badge&amp;utm_medium=badge"><img src="https://trendshift.io/api/badge/repositories/14318"></a>
[HelloGitHub](https://hellogithub.com/repository/abc) <img src="https://api.hellogithub.com/v1/widgets/recommend.svg?rid=abc&amp;theme=light">
<a href="https://www.producthunt.com/posts/cherry-studio?embed=true&amp;utm_source=badge"><img src="https://api.producthunt.com/widgets/embed-image/v1/featured.svg?post_id=496640&amp;theme=light"></a>
<a href="https://tanstack.com/blog/tanstack-open-source-awards-2026"><img src="https://raw.githubusercontent.com/TanStack/ai/main/media/js-open-source-award-2026-ai-project-of-the-year.svg"></a>
<img src="https://img.shields.io/badge/X-black"> https://deepwiki.com/team/project'''
badges = panel.recognition_badges(readme, "https://github.com/team/project/blob/main/README.md")
assert {b["kind"] for b in badges} == {"ts", "hg", "ph", "award"}
assert next(b["image"] for b in badges if b["kind"] == "hg").endswith("rid=abc&theme=light")

data = {"prs": prs, "projects": [{"repo": "team/library", "stars": 26901, "badges": badges}]}
signature = panel.content_signature(data)
other = deepcopy(data)
other["updated_at"] = "2027-01-01"
other["projects"][0]["stars"] = 26902
assert signature == panel.content_signature(other)
other["projects"][0]["stars"] = 27000
assert signature != panel.content_signature(other)
other = deepcopy(data)
other["prs"][0]["commit_shas"] = ["cached-evidence"]
other["prs"][0]["merge_commit_fingerprint"] = {"cached": "evidence"}
assert signature == panel.content_signature(other)
other["commits"] = branch_commits
assert signature != panel.content_signature(other)
print("Passed: co-author discovery, main-branch ancestry, PR/commit deduplication, pinned/history retention, complete partitioned search, badge whitelist, stable signatures")
