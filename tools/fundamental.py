"""
Fundamental analysis tool — free cash flow from yfinance cash flow statement.
"""

import pandas as pd
import yfinance as yf


def get_free_cash_flow(symbol: str) -> str:
    """
    Fetch the last 4 annual periods of Free Cash Flow (Operating Cash Flow minus
    Capital Expenditures) from yfinance cash flow statements.
    Returns a formatted string for the Fundamental Analyst agent.
    """
    ticker = symbol
    tk = yf.Ticker(ticker)
    cf = tk.cashflow  # columns = fiscal year end dates, rows = line items

    if cf is None or cf.empty:
        return f"[{ticker}] Cash flow statement not available."

    # Normalise index to lowercase for robust lookup
    cf.index = cf.index.str.lower().str.replace(" ", "_")

    ocf_keys   = ["operating_cash_flow", "total_cash_from_operating_activities",
                  "cash_from_operations", "net_cash_provided_by_operating_activities"]
    capex_keys = ["capital_expenditures", "capital_expenditure",
                  "purchase_of_plant,_property_&_equipment",
                  "purchase_of_ppe", "capex"]

    def find_row(keys):
        for k in keys:
            if k in cf.index:
                return cf.loc[k]
        return None

    ocf_row   = find_row(ocf_keys)
    capex_row = find_row(capex_keys)

    if ocf_row is None:
        return f"[{ticker}] Operating cash flow line not found in cash flow statement."

    lines = [f"FREE CASH FLOW — {ticker}", "─" * 40]
    for col in cf.columns[:4]:                     # last 4 fiscal years
        year  = str(col)[:10]
        ocf   = ocf_row.get(col, None)
        capex = capex_row.get(col, None) if capex_row is not None else None

        if pd.isna(ocf):
            lines.append(f"{year}: OCF=N/A")
            continue

        ocf_val = float(ocf)
        if capex is not None and not pd.isna(capex):
            capex_val = float(capex)
            # yfinance reports capex as negative; normalise to negative spend
            if capex_val > 0:
                capex_val = -capex_val
            fcf = ocf_val + capex_val          # OCF - |capex|
            lines.append(
                f"{year}: OCF=${ocf_val/1e9:.2f}B  CapEx=${capex_val/1e9:.2f}B  "
                f"FCF=${fcf/1e9:.2f}B"
            )
        else:
            lines.append(f"{year}: OCF=${ocf_val/1e9:.2f}B  CapEx=N/A  FCF=N/A")

    return "\n".join(lines)
