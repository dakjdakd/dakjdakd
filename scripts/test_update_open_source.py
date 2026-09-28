"""Check that scheduled refreshes retain co-authored merged PRs."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

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

signature = update_open_source.content_signature(prs, {"vercel/ai": 26901})
assert signature == update_open_source.content_signature(prs, {"vercel/ai": 26902})
assert signature != update_open_source.content_signature(prs, {"vercel/ai": 27000})
with TemporaryDirectory() as directory:
    output = Path(directory) / "panel.gif"
    with patch.object(update_open_source, "OUT", output):
        assert update_open_source.needs_update(signature)
        Image.new("RGB", (1, 1)).save(output, format="GIF", comment=signature)
        assert not update_open_source.needs_update(signature)
        assert update_open_source.needs_update(b"changed content")
