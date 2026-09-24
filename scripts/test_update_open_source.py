"""Check that scheduled refreshes retain co-authored merged PRs."""

from unittest.mock import patch

import update_open_source


def fake_api(path, **params):
    if path == "search/issues":
        return {
            "total_count": 1,
            "items": [{
                "title": "Haystack fix",
                "html_url": "https://github.com/deepset-ai/haystack/pull/12884",
                "repository_url": "https://api.github.com/repos/deepset-ai/haystack",
                "pull_request": {"merged_at": "2026-09-24T00:00:00Z"},
            }],
        }
    number = int(path.rsplit("/", 1)[1])
    return {
        "title": f"AI SDK fix {number}",
        "html_url": f"https://github.com/vercel/ai/pull/{number}",
        "merged_at": "2026-09-21T20:00:00Z",
    }


with patch.object(update_open_source, "api", side_effect=fake_api):
    prs = update_open_source.merged_prs()

assert len(prs) == 3
assert {pr["html_url"] for pr in prs} == {
    "https://github.com/deepset-ai/haystack/pull/12884",
    "https://github.com/vercel/ai/pull/21228",
    "https://github.com/vercel/ai/pull/21229",
}
