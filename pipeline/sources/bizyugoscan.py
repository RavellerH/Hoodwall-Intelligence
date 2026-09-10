"""bizyugoscan.com candidate scraper.

A wallet-tracking site named after the `bizyugo` entity already in the
knowledge base. Its structure has not been inspected from inside this
project, so nothing here assumes a layout: the generic scraper extracts
full addresses and masks from whatever the page and its JSON endpoints
return, and the browser fallback covers a client-rendered table.

Everything it produces lands in `candidates` for enrichment and scoring to
judge. Nothing it says about a wallet is taken as fact - see
knowledge/sources/bizyugoscan.yml.
"""
from .. import config
from . import scraper

SOURCE_NAME = "bizyugoscan.com"


def run():
    return scraper.run(
        SOURCE_NAME,
        config.BIZYUGOSCAN_BASE_URL,
        force_browser=config.BIZYUGOSCAN_FORCE_BROWSER,
        tag="bizyugoscan",
    )


if __name__ == "__main__":
    run()
