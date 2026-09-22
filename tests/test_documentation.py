"""Keep the maintained documentation free of broken local links."""

import re
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\((<[^>]+>|[^)\s]+)(?:\s+['\"].*?['\"])?\)")


def maintained_markdown_files():
    """Yield public project documentation, excluding generated and private data."""
    yield from ROOT.glob("*.md")
    yield from (ROOT / "docs").rglob("*.md")
    yield from (ROOT / "skills" / "jobhunter").rglob("*.md")


def local_link_target(document, raw_target):
    """Resolve a Markdown target, or return ``None`` for external and page links."""
    target = raw_target.strip("<>")
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    return (document.parent / unquote(parsed.path)).resolve()


class DocumentationTests(unittest.TestCase):
    """Validate navigation through the maintained Markdown entry points."""

    def test_local_links_exist(self):
        """Every local Markdown link must point to an existing file or directory."""
        missing = []
        for document in maintained_markdown_files():
            text = document.read_text(encoding="utf-8")
            for match in MARKDOWN_LINK.finditer(text):
                target = local_link_target(document, match.group(1))
                if target is not None and not target.exists():
                    missing.append(
                        f"{document.relative_to(ROOT)} -> {target.relative_to(ROOT)}"
                    )
        self.assertEqual(missing, [], "Broken local links:\n" + "\n".join(missing))


if __name__ == "__main__":
    unittest.main()
