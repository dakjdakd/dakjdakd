"""Regression checks for contribution attribution and stable scheduled updates."""
from copy import deepcopy
from unittest.mock import patch

import update_open_source as panel


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
print("Passed: co-author discovery, main-branch ancestry, PR/commit deduplication, pinned/history retention, complete search, badge whitelist, stable signatures")
