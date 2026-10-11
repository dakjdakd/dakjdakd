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
    return {"sha": sha, "repository": {"full_name": repo, "fork": fork}, "commit": {"message": message}}


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
    prs = panel.merged_prs([archived], config)
assert len(prs) == 5
assert {p["html_url"] for p in prs} == {
    "https://github.com/old/project/pull/5", "https://github.com/sample/project/pull/1",
    "https://github.com/team/library/pull/9", "https://github.com/vercel/ai/pull/21228",
    "https://github.com/vercel/ai/pull/21229",
}
assert not panel.credited(f"mentions {email} without an author trailer", {email})
with patch.object(panel, "api", return_value={"incomplete_results": True, "total_count": 0, "items": []}):
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
print("Passed: co-author discovery, deduplication, pinned/history retention, complete search, badge whitelist, stable signatures")
