"""
FMP sector vocabulary, curated large-cap ticker lists, and company discovery logic.
"""

# ---------------------------------------------------------------------------
# FMP sector names (must match FMP screener API vocabulary exactly)
# ---------------------------------------------------------------------------
FMP_SECTORS = [
    "Technology",
    "Healthcare",
    "Energy",
    "Financials",
    "Consumer Cyclical",       # GICS: Consumer Discretionary
    "Consumer Defensive",      # GICS: Consumer Staples
    "Industrials",
    "Basic Materials",         # GICS: Materials
    "Real Estate",
    "Utilities",
    "Communication Services",
]


# ---------------------------------------------------------------------------
# Top ~12 large-cap S&P 500 representatives per FMP sector, ordered roughly
# by market cap. Used as the primary source for company discovery.
# ---------------------------------------------------------------------------
_SECTOR_TICKERS: dict[str, list[str]] = {
    "Technology":             ["AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CSCO", "AMD", "INTC", "TXN", "QCOM", "IBM", "HPQ"],
    "Healthcare":             ["LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ABT", "DHR", "BMY", "AMGN", "PFE", "GILD"],
    "Energy":                 ["XOM", "CVX", "COP", "EOG", "SLB", "MPC", "VLO", "PSX", "OXY", "HES", "DVN", "BKR"],
    "Financials":             ["JPM", "BAC", "WFC", "GS", "MS", "BLK", "C", "AXP", "SCHW", "USB", "PNC", "TFC"],
    "Consumer Cyclical":      ["AMZN", "TSLA", "HD", "MCD", "NKE", "SBUX", "LOW", "TGT", "BKNG", "GM", "F", "MAR"],
    "Consumer Defensive":     ["PG", "KO", "PEP", "WMT", "COST", "PM", "MO", "CL", "KMB", "GIS", "SYY", "HSY"],
    "Industrials":            ["HON", "CAT", "GE", "RTX", "LMT", "DE", "UNP", "UPS", "BA", "EMR", "ETN", "MMM"],
    "Basic Materials":        ["LIN", "APD", "ECL", "SHW", "NUE", "FCX", "NEM", "VMC", "MLM", "CF", "MOS", "ALB"],
    "Real Estate":            ["PLD", "AMT", "EQIX", "CCI", "SPG", "WELL", "DLR", "O", "PSA", "EXR", "AVB", "VTR"],
    "Utilities":              ["NEE", "DUK", "SO", "D", "SRE", "EXC", "XEL", "WEC", "PPL", "AEP", "ES", "ETR"],
    "Communication Services": ["GOOGL", "META", "NFLX", "DIS", "CMCSA", "VZ", "T", "CHTR", "TMUS", "EA", "WBD", "OMC"],
}


def fetch_companies_for_sectors(sectors: list[str]) -> list[str]:
    """
    Return up to 10 tickers for the given sectors using the curated fallback map.
    Distributes slots evenly across sectors; ties broken by order in _SECTOR_TICKERS.
    """
    if not sectors:
        return []

    per_sector = max(1, 10 // len(sectors))
    extra      = 10 - per_sector * len(sectors)  # distribute leftover slots to first sectors
    result: list[str] = []
    seen: set[str]    = set()

    for i, sector in enumerate(sectors):
        pool  = _SECTOR_TICKERS.get(sector, [])
        slots = per_sector + (1 if i < extra else 0)
        for ticker in pool:
            if ticker not in seen and len(result) < 10:
                result.append(ticker)
                seen.add(ticker)
            if len([t for t in result if t in pool]) >= slots:
                break

    return result[:10]
