"""hood.vantis.sh candidate scraper.

The mechanism lives in `scraper.py`; this file is the site's configuration.
See knowledge/sources/intel-hood-vantis.yml for what this feed is worth -
in short, a discovery source whose wallet tags are claims to verify, never
score inputs.
"""
from .. import config
from . import scraper

SOURCE_NAME = "hood.vantis.sh"

# Probed before the browser fallback. Unknown paths 404 and are skipped.
API_PATHS = ["/api/wallets", "/api/leaderboard", "/api/holders", "/api/data"]


def run():
    return scraper.run(
        SOURCE_NAME,
        config.HOOD_BASE_URL,
        api_paths=API_PATHS,
        force_browser=config.HOOD_FORCE_BROWSER,
        tag="hood",
    )


if __name__ == "__main__":
    run()
