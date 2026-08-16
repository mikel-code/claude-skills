"""Dependency gate for the url-to-markdown ladder.

Checks external (non-pip) tools and exits EXIT_NOT_CONFIGURED with install
instructions when something required is missing. pip-level deps
(trafilatura) resolve via `uv run` inline metadata and are not checked here.

Only curl is hard-required: Chrome missing degrades Tier 2 to a gate-fail
(the ladder still works via Jina), and Firecrawl is optional by design — so
neither is gated here.
"""

from __future__ import annotations

import shutil
import sys

from contract import EXIT_NOT_CONFIGURED


def check_deps() -> None:
    if shutil.which("curl") is None:
        print("url-to-markdown: curl not found — install Xcode command line "
              "tools or `brew install curl`.", file=sys.stderr)
        sys.exit(EXIT_NOT_CONFIGURED)


if __name__ == "__main__":
    check_deps()
    print("all required external dependencies present")
