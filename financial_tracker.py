"""
Financial Tracker for Microstrategy (MSTR) and Bitcoin (BTC-USD).

This script fetches financial data from Yahoo Finance (yfinance) and
Microstrategy's Bitcoin holdings from bitcointreasuries.net.
It calculates:
- Current prices for MSTR, STRK, STRF, and BTC-USD.
- Microstrategy's Net Asset Value (MNAV) based on its BTC holdings.
- Historical MNAV for MSTR over the past year.
- Implied Volatility (IV) for near-the-money MSTR options.

The script logs key daily metrics to 'daily_metrics_log.csv', saves historical
MNAV data to 'mstr_historical_mnav.csv', and maintains a general operational log
in 'financial_tracker.log'.

Key outputs include a console summary report and the generated CSV/log files.
"""
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import csv
import os
import logging
import requests
from bs4 import BeautifulSoup
import re
import io # For StringIO for pd.read_html
import numpy as np

# --- Setup Logging ---
# Configures basic logging to file and console.
# File log includes timestamp, level, module, function, and message.
logging.basicConfig(
    filename='financial_tracker.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(module)s - %(funcName)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# --- Global Configuration ---
# Fallback Placeholder - VERIFY AND UPDATE THIS VALUE with Microstrategy's latest reported Bitcoin holdings.
# This value is used if fetching live data from bitcointreasuries.net fails.
FALLBACK_MSTR_BTC_HOLDINGS = 205000

def get_current_price(ticker_symbol: str) -> float | None:
    """
    Fetches the current market price for a given ticker symbol using yfinance.

    It tries a sequence of keys from the ticker's info object to find a valid price,
    with different preferred keys for cryptocurrencies (ending in "-USD") versus equities.
    Logs errors if fetching fails or no valid price is found.

    Args:
        ticker_symbol (str): The stock or cryptocurrency ticker symbol (e.g., "MSTR", "BTC-USD").

    Returns:
        float | None: The current market price as a float, or None if an error occurs or
                      no valid price can be found.
    """
    try:
        ticker = yf.Ticker(ticker_symbol)
        info = ticker.info

        price_keys = []
        # Use different key priorities based on asset type (crypto vs. equity)
        if "-USD" in ticker_symbol.upper() or ticker_symbol.upper() == "BTC": # Assuming crypto
            price_keys = ['regularMarketPrice', 'marketPrice', 'previousClose', 'currentPrice', 'bid', 'ask']
        else: # Assuming equity
            price_keys = ['currentPrice', 'regularMarketPrice', 'marketPrice', 'previousClose', 'bid', 'ask']

        for key in price_keys:
            price = info.get(key)
            if price is not None and price > 0: # Ensure price is a positive value
                logging.info(f"Found price for {ticker_symbol} using key '{key}': {price}")
                return float(price)

        # Fallback for crypto if common fields fail (e.g. some crypto tickers might have price under 'financialData')
        if "-USD" in ticker_symbol.upper() and info.get('financialData'):
            price = info.get('financialData').get('currentPrice')
            if price is not None and price > 0:
                logging.info(f"Found price for {ticker_symbol} using fallback key 'financialData.currentPrice': {price}")
                return float(price)

        # If info dict itself is minimal or seems incomplete, this might indicate an issue with the ticker symbol
        if not info or len(info) < 5 : # Arbitrary threshold for minimal info
            logging.error(f"Limited information available for {ticker_symbol}. It might be delisted, an invalid ticker, or data is temporarily unavailable.")
            return None

        logging.warning(f"Could not find a valid price for {ticker_symbol} using preferred keys. Check available info: {list(info.keys())}")
        return None
    except Exception as e:
        # Check if the error is due to common yfinance issues for delisted/invalid tickers
        if "No market data found" in str(e) or "No data found for ticker" in str(e) or "failed to decrypt" in str(e).lower() or "private key" in str(e).lower():
            logging.error(f"No market data found for {ticker_symbol} (or access issue). It might be delisted, an invalid ticker, or a temporary yfinance problem.")
        else:
            logging.error(f"Error fetching current price for {ticker_symbol}: {e}", exc_info=True) # Log with traceback
        return None

def get_historical_data(ticker_symbol: str, period: str = "1y") -> pd.DataFrame | None:
    """
    Fetches historical market data for a given ticker symbol using yfinance.

    Args:
        ticker_symbol (str): The stock or cryptocurrency ticker symbol (e.g., "MSTR", "BTC-USD").
        period (str, optional): The period for which to fetch data (e.g., "1y", "6mo", "1mo").
                                Defaults to "1y".

    Returns:
        pd.DataFrame | None: A pandas DataFrame with historical data (OHLC, Volume, etc.),
                              or an empty DataFrame if an error occurs or no data is found.
                              Returns None only if a very unexpected error occurs, typically returns empty DataFrame.
    """
    try:
        ticker = yf.Ticker(ticker_symbol)
        history = ticker.history(period=period)
        if history.empty:
            info = ticker.info # Check if info is also minimal to differentiate invalid ticker from just no history
            if not info or not info.get('shortName'): # 'shortName' is a common field for valid tickers
                 logging.warning(f"No historical data found for {ticker_symbol} for period {period}. Ticker might be invalid or delisted.")
            else:
                 logging.warning(f"No historical data found for {ticker_symbol} for period {period}, though ticker appears valid (Name: {info.get('shortName')}).")
        else:
            logging.info(f"Successfully fetched historical data for {ticker_symbol} for period {period}.")
        return history
    except Exception as e:
        logging.error(f"Error fetching historical data for {ticker_symbol}: {e}", exc_info=True)
        return pd.DataFrame() # Return empty DataFrame on error to allow downstream checks like .empty

def get_shares_outstanding(ticker_symbol: str) -> float | None:
    """
    Fetches the number of outstanding shares for a given equity ticker symbol using yfinance.

    Tries a sequence of preferred keys: 'sharesOutstanding', 'impliedSharesOutstanding', 'floatShares'.
    If direct keys fail, it attempts to calculate shares from marketCap and current/regularMarketPrice.

    Args:
        ticker_symbol (str): The stock ticker symbol (e.g., "MSTR").

    Returns:
        float | None: The number of shares outstanding, or None if it cannot be determined.
    """
    try:
        ticker = yf.Ticker(ticker_symbol)
        info = ticker.info

        preferred_keys = ['sharesOutstanding', 'impliedSharesOutstanding', 'floatShares']
        shares = None
        key_used = ""

        for key in preferred_keys:
            val = info.get(key)
            if val is not None and val > 0: # Shares must be positive
                shares = float(val)
                key_used = key
                logging.info(f"Found shares for {ticker_symbol} using key '{key_used}': {shares}")
                break

        if shares is None: # If direct keys didn't work or gave zero, try calculating from market cap
            logging.info(f"Direct share keys failed for {ticker_symbol}. Attempting calculation from marketCap.")
            market_cap = info.get('marketCap')

            price_for_calc = None
            price_calc_keys = ['regularMarketPrice', 'currentPrice', 'previousClose'] # Order of preference for price
            price_key_used_for_calc = ""
            for p_key in price_calc_keys:
                p_val = info.get(p_key)
                if p_val is not None and p_val > 0:
                    price_for_calc = p_val
                    price_key_used_for_calc = p_key
                    break

            if market_cap is not None and market_cap > 0 and price_for_calc is not None:
                calculated_shares = market_cap / price_for_calc
                shares = calculated_shares
                key_used = f"marketCap ({market_cap}) / {price_key_used_for_calc} ({price_for_calc})"
                logging.info(f"Calculated shares for {ticker_symbol} using '{key_used}': {shares}")
            else:
                logging.warning(f"Could not retrieve or calculate shares outstanding for {ticker_symbol} from provided keys or market cap. MarketCap: {market_cap}, PriceForCalc: {price_for_calc}. Info dict keys: {list(info.keys()) if isinstance(info, dict) else 'Info not a dict'}")
                return None

        return shares
    except Exception as e:
        logging.error(f"Error fetching shares outstanding for {ticker_symbol}: {e}", exc_info=True)
        return None

def get_market_cap(ticker_symbol: str) -> float | None:
    """
    Fetches the market capitalization for a given equity ticker symbol using yfinance.

    Args:
        ticker_symbol (str): The stock ticker symbol (e.g., "MSTR").

    Returns:
        float | None: The market capitalization, or None if it cannot be determined or is invalid.
    """
    try:
        ticker = yf.Ticker(ticker_symbol)
        info = ticker.info
        market_cap = info.get('marketCap')

        if market_cap is not None and market_cap > 0:
            logging.info(f"Successfully fetched market cap for {ticker_symbol}: {market_cap}")
            return float(market_cap)
        else:
            logging.warning(f"Market cap for {ticker_symbol} is missing, None, zero, or negative. Value: {market_cap}. Info keys: {list(info.keys()) if isinstance(info, dict) else 'Info not a dict'}")
            return None
    except Exception as e:
        logging.error(f"Error fetching market cap for {ticker_symbol}: {e}", exc_info=True)
        return None

def calculate_mstr_mnav(btc_holdings: int, btc_price: float, mstr_market_cap: float) -> float | None:
    """
    Calculates Microstrategy's Multiplied Net Asset Value (MNAV).

    MNAV is calculated as: MSTR Market Cap / (Total BTC Holdings * Current BTC Price).

    Args:
        btc_holdings (int): The total number of Bitcoins held by Microstrategy.
        btc_price (float): The current market price of one Bitcoin.
        mstr_market_cap (float): The market capitalization of Microstrategy.

    Returns:
        float | None: The calculated MNAV (ratio), or None if inputs are invalid or
                      the denominator (BTC value) is zero.
    """
    # Validate inputs
    if not all([isinstance(btc_holdings, (int, float)),
                isinstance(btc_price, (int, float)),
                isinstance(mstr_market_cap, (int, float))]):
        logging.error(f"Missing or invalid type for one or more inputs for MNAV calculation. btc_holdings: {btc_holdings} (type {type(btc_holdings)}), btc_price: {btc_price} (type {type(btc_price)}), mstr_market_cap: {mstr_market_cap} (type {type(mstr_market_cap)})")
        return None

    if btc_holdings < 0 or btc_price < 0: # Basic sanity check for negative inputs
        logging.warning(f"Negative values provided for btc_holdings or btc_price: Holdings {btc_holdings}, Price {btc_price}. MNAV calculation will proceed if values are non-zero.")
        # Allow calculation to proceed if values are non-zero, as negative BTC value might be a scenario to observe if not strictly an error.
        # If strict positivity is required for holdings/price, additional checks can be added here.

    btc_total_value = btc_holdings * btc_price

    if btc_total_value == 0:
        logging.error(f"BTC total value is zero (Holdings: {btc_holdings}, Price: {btc_price}), cannot calculate MNAV ratio.")
        return None

    # The check for mstr_market_cap == 0 is implicitly handled:
    # If mstr_market_cap is 0, mnav will be 0. This is a valid mathematical outcome.
    # If it were a required positive value, a check similar to btc_total_value would be here.

    mnav = mstr_market_cap / btc_total_value
    logging.info(f"Successfully calculated MSTR MNAV ratio: {mnav:.4f} (Market Cap: {mstr_market_cap}, BTC Holdings: {btc_holdings}, BTC Price: {btc_price}, Total BTC Value: {btc_total_value})")
    return mnav


def get_mstr_btc_holdings_from_web() -> int | None:
    """
    Scrapes Microstrategy's current BTC holdings from bitcointreasuries.net.

    The function tries a specific CSS selector pattern first, then falls back to a broader
    text-based search to find the "BTC balance" and its corresponding numerical value.

    Returns:
        int | None: The number of BTC held by Microstrategy, or None if scraping fails
                      or the value cannot be reliably extracted.
    """
    url = "https://bitcointreasuries.net/entities/microstrategy" # Reverted to correct URL
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}

    try:
        logging.info(f"Attempting to fetch data from {url}") # Generalizing log message
        response = requests.get(url, headers=headers, timeout=15) # Increased timeout slightly for potentially larger page
        response.raise_for_status()
        logging.info(f"Successfully fetched page content from {url}")

        soup = BeautifulSoup(response.content, 'lxml')

        # --- Part 1: Scrape Current Total BTC Holdings (existing logic) ---
        # This part remains largely the same to ensure the primary return value is preserved.
        current_total_btc_holdings = None
        # (Primary Strategy for current holdings - div.label/div.value pattern)
        # This structure was observed on similar pages.
        label_div = soup.find(lambda tag: tag.name == 'div' and 'label' in tag.get('class', []) and "BTC balance" in tag.get_text(strip=True))

        value_str = None
        if label_div:
            parent_wrapper = label_div.parent
            if parent_wrapper:
                value_div = parent_wrapper.find('div', class_='value') # Assumes value is in a sibling div with class 'value'
                if value_div:
                    value_str = value_div.get_text(strip=True)
                    logging.info(f"Found BTC balance using specific div.label/div.value pattern: {value_str}")

        # Fallback Strategy: More general search if the specific one fails
        if not value_str:
            logging.info("Specific div.label/div.value pattern not found or failed. Trying broader search for 'BTC balance' label.")
            # Find any element containing "BTC balance" text, case-insensitive, across all tags
            label_elements = soup.find_all(True, string=re.compile(r'BTC balance', re.IGNORECASE))

            if not label_elements:
                logging.error("Broad search: Could not find any element containing 'BTC balance' label on the page.")
                return None

            found_value_after_label = False
            for label_element in label_elements:
                # Try to find the value in the next few siblings or children of the parent of the label
                current_search_element = label_element
                for _ in range(3): # Search up to 3 levels up in the DOM tree for the parent container
                    parent = current_search_element.parent
                    if not parent: break # Stop if no parent

                    all_text_in_context = parent.find_all(string=True, recursive=True) # Get all text nodes within this parent
                    label_seen_in_context = False

                    for text_node in all_text_in_context:
                        if text_node and isinstance(text_node, str): # Check if it's a string-like object
                            stripped_text_node = text_node.strip()

                            # Confirm current label_element's text is in this node (or part of it) to set context
                            if label_element.string and label_element.string.strip() in stripped_text_node:
                                label_seen_in_context = True
                                continue

                            if label_seen_in_context: # Only look for numbers after the specific label has been seen
                                # Regex for a number with commas (at least 3 digits), not starting with $, not a price/decimal
                                match = re.fullmatch(r"([0-9,]{3,})", stripped_text_node)
                                if match and '$' not in stripped_text_node and '.' not in stripped_text_node:
                                    potential_value = match.group(1)
                                    # Heuristic: plausible large number for BTC holdings
                                    if potential_value.count(',') >= 1 or \
                                       (',' not in potential_value and len(potential_value) >= 3 and int(potential_value.replace(',','')) > 1000) or \
                                       len(potential_value.replace(',','')) > 5: # e.g. "1,234" or "10000" or "200000"
                                        value_str = potential_value
                                        logging.info(f"Found potential BTC balance using broader search near label '{label_element.get_text(strip=True)}': {value_str}")
                                        found_value_after_label = True
                                        break
                    if found_value_after_label:
                        break
                    current_search_element = parent
                    if not current_search_element or current_search_element.name == '[document]': # Stop if top is reached
                        break
                if found_value_after_label:
                    break

            if not value_str:
                logging.error("Could not extract BTC holdings value using refined fallback search method.")
                return None

        if value_str:
            cleaned_value_str = value_str.replace(',', '')
            if cleaned_value_str.isdigit():
                current_total_btc_holdings = int(cleaned_value_str)
                logging.info(f"Successfully parsed CURRENT TOTAL BTC holdings: {current_total_btc_holdings}")
            else:
                logging.error(f"Extracted CURRENT TOTAL BTC holdings value '{cleaned_value_str}' is not a valid integer after cleaning.")
                # Do not return yet; attempt table scraping even if this part fails.
        else:
            # This case should ideally be caught by earlier checks for value_str
            logging.error("CURRENT TOTAL BTC holdings value string is empty or None after all search attempts.")
            # Do not return yet.

        # --- Part 2: Scrape "Balance Sheet History" Table ---
        df_balance_sheet = None
        table_scraped_successfully = False

        try:
            logging.info("Attempting to scrape 'Balance Sheet History' table using pd.read_html as first pass.")
            match_regex = re.compile(r"Date.*BTC Balance.*(Change|Change Cost Basis).*(Market Price|Market Value).*(Stock Price|Value per Share MSTR)", re.IGNORECASE | re.DOTALL)
            # Wrap response.text in io.StringIO for pd.read_html
            tables = pd.read_html(io.StringIO(response.text), match=match_regex)

            if tables:
                logging.info(f"pd.read_html found {len(tables)} table(s) matching initial criteria.")
                for i, table_candidate in enumerate(tables):
                    logging.info(f"Inspecting table candidate {i} from pd.read_html. Columns: {table_candidate.columns.tolist()}, Shape: {table_candidate.shape}")
                    if table_candidate.shape[0] > 5 and (4 < table_candidate.shape[1] < 8) :
                        df_balance_sheet = table_candidate
                        logging.info(f"Selected table candidate {i} from pd.read_html as potential Balance Sheet History table.")
                        table_scraped_successfully = True
                        break
            else: # tables is empty or None
                 logging.warning("pd.read_html did not find any tables matching the regex criteria.")

        except ValueError as ve: # Specifically catch ValueError if pd.read_html finds no table matching regex
            logging.warning(f"pd.read_html raised ValueError (no table found with match): {ve}. Will attempt manual parsing.")
        except Exception as e_html: # Catch other potential errors from pd.read_html
            logging.error(f"An unexpected error occurred during pd.read_html processing: {e_html}", exc_info=True)
            logging.info("Will attempt manual BeautifulSoup parsing due to pd.read_html error.")

        # Fallback or Primary: Manual BeautifulSoup Parsing
        if not table_scraped_successfully:
            try: # Encapsulate manual parsing attempt
                logging.info("Attempting manual parsing of 'Balance Sheet History' table using BeautifulSoup.")
                html_table = None
                # Strategy 1: Find header then table
                history_header = soup.find(lambda tag: tag.name and tag.name.lower() in ['h2','h3','h4'] and 'Balance Sheet History' in tag.get_text(strip=True))
                if history_header:
                    logging.info(f"Found header for history table: '{history_header.get_text(strip=True)}'")
                    # Try to find table as next sibling or in next sibling div
                    candidate = history_header.find_next_sibling()
                    while candidate and not html_table:
                        if candidate.name == 'table':
                            html_table = candidate
                            break
                        html_table = candidate.find('table') # Check if table is nested in a div
                        if html_table:
                            break
                        candidate = candidate.find_next_sibling()

                # Strategy 2: If header method fails, find table by column content
                if not html_table:
                    logging.warning("Could not find table via header proximity. Searching all tables for specific headers.")
                    all_tables_on_page = soup.find_all('table')
                    logging.info(f"Found {len(all_tables_on_page)} tables on page. Iterating to find the correct one...")
                    for i, candidate_table in enumerate(all_tables_on_page):
                        thead = candidate_table.find('thead')
                        header_row = thead.find('tr') if thead else candidate_table.find('tr') # Fallback to first tr if no thead
                        if header_row:
                            header_texts = [cell.get_text(strip=True).lower() for cell in header_row.find_all(['th', 'td'])]
                            # Check for presence of key headers
                            if ("date" in header_texts and
                                "btc balance" in header_texts and
                                ("change cost basis" in header_texts or "change" in header_texts) and # allow for "Change" as well
                                "market price" in header_texts):
                                html_table = candidate_table
                                logging.info(f"Found plausible history table (candidate {i}) by inspecting headers: {header_texts}")
                                break
                        if html_table: break # Found it

                if html_table:
                    logging.info("Found <table> element for Balance Sheet History via manual search.")
                    table_data = []
                    headers = []

                    thead = html_table.find('thead')
                    header_row_source = None
                    if thead and thead.find('tr'):
                        header_row_source = thead.find('tr')
                        headers = [th.get_text(strip=True) for th in header_row_source.find_all('th')]
                        logging.info(f"Parsed table headers from <thead>: {headers}")

                    if not headers: # Fallback if no <thead> or no <th> in <thead>
                        first_tr = html_table.find('tr')
                        if first_tr:
                            potential_headers = [cell.get_text(strip=True) for cell in first_tr.find_all(['th', 'td'])]
                            # A simple heuristic: if it contains 'Date' and 'BTC Balance' (case insensitive)
                            if any("date" in h.lower() for h in potential_headers) and \
                               any("btc balance" in h.lower() for h in potential_headers):
                                headers = potential_headers
                                logging.info(f"Used first row as headers: {headers}")
                                data_rows_start_index = 1 # Data rows start from the second tr
                            else:
                                data_rows_start_index = 0 # Assume first row is data
                                logging.warning("Could not identify clear header row from first <tr>. Will use generic column names or rely on data structure.")
                        else:
                            logging.warning("No <tr> elements found in table to determine headers or data.")
                            data_rows_start_index = 0
                    else: # Headers found in <thead>
                        data_rows_start_index = 0 # Data rows start from first <tr> in <tbody> or table

                    tbody = html_table.find('tbody')
                    all_trs = html_table.find_all('tr')
                    rows_to_parse = tbody.find_all('tr') if tbody else all_trs[data_rows_start_index:]

                    logging.info(f"Attempting to parse {len(rows_to_parse)} data rows from the table.")
                    for row in rows_to_parse:
                        cells = [cell.get_text(strip=True) for cell in row.find_all('td')]
                        if cells: # Only add if there are data cells
                            table_data.append(cells)

                    if table_data:
                        if headers:
                             # Ensure number of headers matches number of columns in data, take minimum
                            num_cols = len(table_data[0])
                            df_balance_sheet = pd.DataFrame(table_data, columns=headers[:num_cols])
                        else: # No headers identified, pandas will assign default 0, 1, ...
                            df_balance_sheet = pd.DataFrame(table_data)
                            logging.warning("Created DataFrame with generic integer column names as headers were not definitively parsed.")

                        logging.info(f"Manual parsing created DataFrame with shape {df_balance_sheet.shape}")
                        table_scraped_successfully = True
                    else:
                        logging.warning("Manual parsing: No data rows extracted from table.")
                else:
                    logging.warning("Manual parsing: Could not find the Balance Sheet History <table> element after all attempts.")
            except Exception as e_manual_parse:
                logging.error(f"Error during manual BeautifulSoup parsing of Balance Sheet History: {e_manual_parse}", exc_info=True)

        # Universal Cleaning & Saving, only if a table was successfully scraped by either method
        if table_scraped_successfully and df_balance_sheet is not None and not df_balance_sheet.empty:
            logging.info(f"Proceeding with cleaning. Initial columns: {df_balance_sheet.columns.tolist()}")

            rename_map = {}
            for col in df_balance_sheet.columns: # Iterate over actual columns in the DataFrame
                col_str = str(col).lower().replace('\n', ' ').strip()
                if "date" == col_str: rename_map[col] = "Date"
                elif "btc balance" == col_str: rename_map[col] = "BTC_Balance"
                # Try to match "Change Cost Basis" or just "Change" if it's likely the BTC quantity change
                elif "change cost basis" == col_str : rename_map[col] = "Change_Cost_Basis"
                elif "change" == col_str and "cost basis" not in col_str : rename_map[col] = "BTC_Change"
                elif "cost basis" == col_str and "change" not in col_str: rename_map[col] = "Total_Cost_Basis"
                elif "market price" == col_str: rename_map[col] = "Market_Price_BTC"
                elif "stock price" == col_str: rename_map[col] = "Stock_Price_MSTR"

            df_balance_sheet = df_balance_sheet.rename(columns=rename_map)
            logging.info(f"Attempted renaming. Columns now: {df_balance_sheet.columns.tolist()}")

            desired_cols = ["Date", "BTC_Balance", "BTC_Change", "Total_Cost_Basis", "Change_Cost_Basis", "Market_Price_BTC", "Stock_Price_MSTR"]
            cols_to_keep = [col for col in desired_cols if col in df_balance_sheet.columns]

            if not cols_to_keep:
                 logging.error("No standard columns found after renaming. Aborting table processing and saving.")
            else:
                df_balance_sheet = df_balance_sheet[cols_to_keep]
                logging.info(f"Selected final columns for CSV: {df_balance_sheet.columns.tolist()}")

                if "Date" in df_balance_sheet.columns:
                    df_balance_sheet["Date"] = pd.to_datetime(df_balance_sheet["Date"], errors='coerce')
                    df_balance_sheet.dropna(subset=['Date'], inplace=True)

                numeric_cols = [col for col in df_balance_sheet.columns if col != 'Date']
                for col in numeric_cols:
                    df_balance_sheet[col] = df_balance_sheet[col].astype(str).str.replace(r'[$,+()]', '', regex=True)
                    df_balance_sheet[col] = df_balance_sheet[col].replace(r'^(—|-|\s*)$', pd.NA, regex=True)
                    df_balance_sheet[col] = pd.to_numeric(df_balance_sheet[col], errors='coerce')

                if not df_balance_sheet.empty:
                    csv_filename = "mstr_balance_sheet_history.csv"
                    df_balance_sheet.to_csv(csv_filename, index=False)
                    logging.info(f"Successfully cleaned and saved 'Balance Sheet History' to {csv_filename} with {len(df_balance_sheet)} rows and columns: {df_balance_sheet.columns.tolist()}.")
                else:
                    logging.warning("Balance sheet DataFrame is empty after cleaning. Not saving CSV.")
        elif not table_scraped_successfully:
            logging.warning("Failed to extract Balance Sheet History table by any method (pd.read_html or manual parsing).")

        return current_total_btc_holdings

    except requests.exceptions.RequestException as e:
        logging.error(f"RequestException while fetching data from {url}: {e}", exc_info=True)
        return None
    except Exception as e:
        logging.error(f"An unexpected error occurred in get_mstr_btc_holdings_from_web function: {e}", exc_info=True)
        return None

# --- Main Data Update Function ---
def perform_daily_data_update() -> bool:
    """
    Performs the complete daily data fetching, processing, and logging sequence.

    This function encapsulates the main logic previously found in the
    `if __name__ == "__main__":` block. It fetches current and historical
    financial data, calculates MNAV and IV, scrapes web data,
    displays a summary report to console, and logs data to CSV files and a log file.

    Returns:
        bool: True if the process completes through all major steps, False otherwise
              (though current implementation mostly logs errors and continues).
              For now, it will return True if it reaches the end of its operations.
    """
    # Initialize report_data dictionary to store all fetched/calculated values for the summary
    report_data = {
        'script_run_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'mstr_price': None, 'mstr_shares_outstanding': None, 'mstr_market_cap': None,
        'mstr_btc_holdings': None,
        'mstr_btc_holdings_source': None,
        'current_mnav': None, 'market_vs_mnav_percentage': None,
        'avg_hist_mnav': None, 'current_mnav_vs_hist_avg_percentage': None,
        'iv_data': None, # This will store the dictionary from get_near_atm_iv or None
        'strk_price': None, 'strf_price': None, 'btc_price': None
    }

    # Logging script execution start is now handled by the caller if __name__ == "__main__"
    # Or, if this function is called as a library, the caller handles that.

    # --- Determine MSTR BTC Holdings (Dynamic or Fallback) ---
    dynamic_btc_holdings = get_mstr_btc_holdings_from_web() # This function now also saves balance sheet history
    current_mstr_btc_holdings = FALLBACK_MSTR_BTC_HOLDINGS # Default to fallback
    using_fallback_btc_holdings = True

    if dynamic_btc_holdings is not None and dynamic_btc_holdings > 0:
        current_mstr_btc_holdings = dynamic_btc_holdings
        using_fallback_btc_holdings = False
        logging.info(f"Using dynamically fetched MSTR BTC holdings: {current_mstr_btc_holdings}")
        report_data['mstr_btc_holdings_source'] = "dynamic"
    else:
        logging.warning(f"Failed to fetch dynamic BTC holdings or value was invalid ({dynamic_btc_holdings}). Using fallback placeholder: {current_mstr_btc_holdings}")
        report_data['mstr_btc_holdings_source'] = "fallback_placeholder"

    report_data['mstr_btc_holdings'] = current_mstr_btc_holdings

    # --- Display BTC Holdings Info (User-facing console print) ---
    print("\n--- Microstrategy Bitcoin Holdings ---")
    if using_fallback_btc_holdings:
        print(f"Warning: Using FALLBACK placeholder for MSTR BTC Holdings: {current_mstr_btc_holdings:,} BTC.")
        print("         (Failed to fetch live data from bitcointreasuries.net or data was invalid)")
    else:
        print(f"Successfully fetched MSTR BTC Holdings from bitcointreasuries.net: {current_mstr_btc_holdings:,} BTC.")
    print("Note: Always cross-verify this number with official Microstrategy announcements for critical decisions.\n")

    # --- Fetch Current Prices for Tickers ---
    tickers_to_fetch = ["MSTR", "STRK", "STRF", "BTC-USD", "INVALIDTICKERXYZ"]
    current_prices_fetched = {}
    for tick in tickers_to_fetch:
        price = get_current_price(tick)
        if price is not None:
            current_prices_fetched[tick] = price

    report_data['mstr_price'] = current_prices_fetched.get("MSTR")
    report_data['strk_price'] = current_prices_fetched.get("STRK")
    report_data['strf_price'] = current_prices_fetched.get("STRF")
    report_data['btc_price'] = current_prices_fetched.get("BTC-USD")

    if report_data['btc_price'] and report_data['btc_price'] > 80000:
        print("Warning: The fetched BTC-USD price from yfinance seems unusually high. Cross-verify with other sources.\n")
        logging.warning(f"High BTC-USD price detected: {report_data['btc_price']}")

    # --- MSTR MNAV Calculation ---
    mstr_current_price_for_calc = report_data['mstr_price']
    btc_current_price_for_calc = report_data['btc_price']
    mstr_shares_outstanding_val = get_shares_outstanding("MSTR")
    report_data['mstr_shares_outstanding'] = mstr_shares_outstanding_val
    mstr_market_cap_val = get_market_cap("MSTR")
    report_data['mstr_market_cap'] = mstr_market_cap_val

    current_mnav_val = None
    if current_mstr_btc_holdings and btc_current_price_for_calc and mstr_market_cap_val :
        current_mnav_val = calculate_mstr_mnav(current_mstr_btc_holdings, btc_current_price_for_calc, mstr_market_cap_val)
        report_data['current_mnav'] = current_mnav_val
        if mstr_current_price_for_calc and current_mnav_val and current_mnav_val > 0: # current_mnav_val could be zero if market_cap is huge
            report_data['market_vs_mnav_percentage'] = ((mstr_current_price_for_calc / current_mnav_val) - 1) * 100
    else:
        logging.warning("Cannot calculate current MSTR MNAV due to missing critical data (BTC price, MSTR market cap, or BTC holdings).")

    # --- MSTR Historical MNAV Calculation ---
    avg_hist_mnav_val = None
    # Note: mstr_shares_outstanding_val is kept for logging/display, but mstr_market_cap_val is used for MNAV.
    # The re-fetch logic for shares outstanding is removed as market cap is the critical factor for MNAV now.

    mstr_hist_df = get_historical_data("MSTR", period="1y")
    btc_hist_df = get_historical_data("BTC-USD", period="1y")

    if mstr_hist_df is not None and not mstr_hist_df.empty and \
       btc_hist_df is not None and not btc_hist_df.empty and \
       mstr_market_cap_val is not None and mstr_market_cap_val > 0:

        if 'Close' in mstr_hist_df.columns and 'Close' in btc_hist_df.columns:
            try:
                logging.warning("Using current MSTR market cap for historical MNAV calculations. For more precision, historical daily market cap would be required.")
                mstr_hist_normalized = mstr_hist_df.copy()
                mstr_hist_normalized.index = pd.to_datetime(mstr_hist_normalized.index, utc=True).normalize()
                btc_hist_normalized = btc_hist_df.copy()
                btc_hist_normalized.index = pd.to_datetime(btc_hist_normalized.index, utc=True).normalize()

                merged_data_df = pd.merge(mstr_hist_normalized[['Close']], btc_hist_normalized[['Close']], left_index=True, right_index=True, suffixes=('_MSTR', '_BTC'))

                if not merged_data_df.empty:
                    # Calculate the denominator for historical MNAV
                    # Ensure current_mstr_btc_holdings is not None and is positive, otherwise denominator could be zero or invalid
                    if current_mstr_btc_holdings is not None and current_mstr_btc_holdings > 0:
                        merged_data_df['MNAV_BTC_Value_Denominator'] = current_mstr_btc_holdings * merged_data_df['Close_BTC']
                    else:
                        merged_data_df['MNAV_BTC_Value_Denominator'] = np.nan # Avoids issues if holdings are invalid
                        logging.warning("Current MSTR BTC holdings are zero, None, or invalid for historical MNAV denominator calculation.")

                    # Calculate historical MNAV ratio
                    # Ensure mstr_market_cap_val is not None before this calculation
                    if mstr_market_cap_val is not None and mstr_market_cap_val > 0:
                        # Denominator can still be zero if historical BTC price ('Close_BTC') was zero
                        merged_data_df['MNAV'] = mstr_market_cap_val / merged_data_df['MNAV_BTC_Value_Denominator']

                        # Handle potential division by zero if MNAV_BTC_Value_Denominator is 0
                        merged_data_df['MNAV'].replace([float('inf'), -float('inf')], np.nan, inplace=True)
                        logging.info("Calculated historical MNAV ratio. Note: 'inf' or 'NaN' may appear if historical BTC price was zero or holdings were invalid.")
                    else:
                        merged_data_df['MNAV'] = np.nan
                        logging.warning("MSTR market cap is missing or invalid, cannot calculate historical MNAV ratio.")

                    # Clean up the temporary denominator column if it exists
                    if 'MNAV_BTC_Value_Denominator' in merged_data_df.columns:
                        merged_data_df.drop(columns=['MNAV_BTC_Value_Denominator'], inplace=True)

                    historical_mnav_csv_filename = "mstr_historical_mnav.csv"
                    merged_data_df.to_csv(historical_mnav_csv_filename)
                    logging.info(f"Saved historical MNAV data to {historical_mnav_csv_filename}")

                    avg_hist_mnav_val = merged_data_df['MNAV'].mean()
                    report_data['avg_hist_mnav'] = avg_hist_mnav_val

                    if current_mnav_val and avg_hist_mnav_val and avg_hist_mnav_val > 0:
                        report_data['current_mnav_vs_hist_avg_percentage'] = ((current_mnav_val / avg_hist_mnav_val) - 1) * 100
                else:
                    logging.warning("Could not merge MSTR and BTC historical data for historical MNAV calculation (no common dates).")
            except Exception as e:
                logging.error(f"Error during historical MNAV calculation or CSV saving: {e}", exc_info=True)
        else:
            logging.warning("'Close' column missing in MSTR or BTC historical data; cannot calculate historical MNAV.")
    else:
        logging.warning("Cannot calculate historical MNAV (missing MSTR/BTC historical data, or MSTR market cap is zero/None).")

    # --- MSTR Implied Volatility Fetching ---
    if mstr_current_price_for_calc:
        iv_data_dict = get_near_atm_iv("MSTR", mstr_current_price_for_calc)
        report_data['iv_data'] = iv_data_dict
    else:
        logging.warning("Cannot fetch Implied Volatility for MSTR as its current price is unavailable.")
        report_data['iv_data'] = None

    # --- Display Summary Report (User-facing console output) ---
    display_summary_report(report_data)

    # --- Log Daily Metrics to CSV ---
    daily_log_data_dict = {
        'Date': report_data['script_run_time'],
        'MSTR_Price': report_data['mstr_price'],
        'MSTR_MNAV': report_data['current_mnav'],
        'MSTR_IV_Call_Strike': report_data.get('iv_data', {}).get('atm_call_strike', 'N/A') if report_data.get('iv_data') else 'N/A',
        'MSTR_IV_Call_IV': report_data.get('iv_data', {}).get('atm_call_iv', 'N/A') if report_data.get('iv_data') else 'N/A',
        'MSTR_IV_Put_Strike': report_data.get('iv_data', {}).get('atm_put_strike', 'N/A') if report_data.get('iv_data') else 'N/A',
        'MSTR_IV_Put_IV': report_data.get('iv_data', {}).get('atm_put_iv', 'N/A') if report_data.get('iv_data') else 'N/A',
        'MSTR_IV_Expiration': report_data.get('iv_data', {}).get('selected_expiration_date', 'N/A') if report_data.get('iv_data') else 'N/A',
        'STRK_Price': report_data['strk_price'],
        'STRF_Price': report_data['strf_price'],
        'BTC_Price': report_data['btc_price'],
        'MSTR_Shares_Outstanding': report_data['mstr_shares_outstanding'],
        'MSTR_Market_Cap': report_data['mstr_market_cap'],
        'MSTR_BTC_Holdings': report_data['mstr_btc_holdings'],
        'MSTR_BTC_Holdings_Source': report_data['mstr_btc_holdings_source']
    }
    for key, value in daily_log_data_dict.items():
        if value is None:
            daily_log_data_dict[key] = 'N/A'

    log_daily_metrics(daily_log_data_dict)
    print(f"\nDaily metrics also logged to daily_metrics_log.csv")

    # End of main operational logic
    return True # Indicate successful completion

def display_summary_report(summary_data: dict) -> None:
    """
    Prints a well-formatted summary report of financial metrics to the console.

    Args:
        summary_data (dict): A dictionary containing the metrics to be displayed.
                             Expected keys include 'script_run_time', 'btc_price', 'mstr_price',
                             'mstr_shares_outstanding', 'mstr_market_cap', 'mstr_btc_holdings', 'current_mnav',
                             'market_vs_mnav_percentage', 'avg_hist_mnav',
                             'current_mnav_vs_hist_avg_percentage', 'iv_data',
                             'strk_price', 'strf_price'.
                             Handles missing data gracefully by printing 'N/A'.
    Returns:
        None
    """
    print("\n--- Financial Summary Report ---")
    print(f"Report Time: {summary_data.get('script_run_time', 'N/A')}")

    # --- Bitcoin Price ---
    btc_price = summary_data.get('btc_price')
    if btc_price is not None and isinstance(btc_price, (int, float)):
        print(f"\nBTC-USD Price: ${btc_price:,.2f}")
    else:
        print(f"\nBTC-USD Price: {btc_price if btc_price is not None else 'N/A'}")

    # --- MSTR Data ---
    print("\n--- MicroStrategy (MSTR) ---")
    mstr_price = summary_data.get('mstr_price')
    if mstr_price is not None and isinstance(mstr_price, (int, float)):
        print(f"Current Price: ${mstr_price:,.2f}")
    else:
        print(f"Current Price: {mstr_price if mstr_price is not None else 'N/A'}")

    mstr_shares = summary_data.get('mstr_shares_outstanding')
    if mstr_shares is not None and isinstance(mstr_shares, (int, float)):
        print(f"Shares Outstanding: {mstr_shares:,.0f}")
    else:
        print(f"Shares Outstanding: {mstr_shares if mstr_shares is not None else 'N/A'}")

    mstr_market_cap_disp = summary_data.get('mstr_market_cap')
    if mstr_market_cap_disp is not None and isinstance(mstr_market_cap_disp, (int, float)):
        print(f"Market Cap: ${mstr_market_cap_disp:,.0f}")
    else:
        print(f"Market Cap: {mstr_market_cap_disp if mstr_market_cap_disp is not None else 'N/A'}")

    mstr_btc_holdings_val = summary_data.get('mstr_btc_holdings', 'N/A')
    holdings_source = summary_data.get('mstr_btc_holdings_source', '')
    source_info = f"({holdings_source})" if holdings_source else ""
    if isinstance(mstr_btc_holdings_val, (int, float)):
        print(f"Assumed BTC Holdings: {mstr_btc_holdings_val:,} BTC {source_info}")
    else:
        print(f"Assumed BTC Holdings: {mstr_btc_holdings_val} BTC {source_info}")


    # MNAV
    current_mnav = summary_data.get('current_mnav')
    if current_mnav is not None and isinstance(current_mnav, (int, float)):
        print(f"MSTR Multiplied NAV (Market Cap / BTC Value): {current_mnav:,.2f}")
        market_vs_mnav = summary_data.get('market_vs_mnav_percentage')
        if market_vs_mnav is not None and isinstance(market_vs_mnav, (int, float)):
            # The interpretation of this percentage changes with MNAV being a ratio.
            # Label is kept generic; user can decide if this metric is still useful.
            print(f"  Stock Price vs MNAV-Implied Price: {market_vs_mnav:+.2f}%")
        else:
            print(f"  Stock Price vs MNAV-Implied Price: {market_vs_mnav if market_vs_mnav is not None else 'N/A'}")
    else:
        print(f"MSTR Multiplied NAV (Market Cap / BTC Value): {current_mnav if current_mnav is not None else 'N/A'}")

    avg_hist_mnav = summary_data.get('avg_hist_mnav')
    if avg_hist_mnav is not None and isinstance(avg_hist_mnav, (int, float)):
        print(f"Average Historical Multiplied NAV (1yr): {avg_hist_mnav:,.2f}")
        curr_vs_hist_mnav = summary_data.get('current_mnav_vs_hist_avg_percentage')
        if curr_vs_hist_mnav is not None and isinstance(curr_vs_hist_mnav, (int, float)):
            print(f"  Current Multiplied NAV vs Hist. Avg: {curr_vs_hist_mnav:+.2f}%")
        else:
            print(f"  Current Multiplied NAV vs Hist. Avg: {curr_vs_hist_mnav if curr_vs_hist_mnav is not None else 'N/A'}")
    else:
        print(f"Average Historical Multiplied NAV (1yr): {avg_hist_mnav if avg_hist_mnav is not None else 'N/A'}")

    # Implied Volatility
    iv_data = summary_data.get('iv_data')
    if iv_data and isinstance(iv_data, dict): # Check if iv_data is a dictionary
        print(f"\nImplied Volatility (Expiration: {iv_data.get('selected_expiration_date', 'N/A')}):")
        call_strike = iv_data.get('atm_call_strike')
        call_iv = iv_data.get('atm_call_iv')
        # Ensure strike and IV are numbers for formatting
        if call_strike is not None and call_iv is not None and isinstance(call_strike, (int,float)) and isinstance(call_iv, (int,float)):
            print(f"  Near-ATM Call (Strike: ${call_strike:,.2f}): {call_iv:.2%} IV")
        else:
            print(f"  Near-ATM Call: Strike or IV N/A (Strike: {call_strike if call_strike is not None else 'N/A'}, IV: {call_iv if call_iv is not None else 'N/A'})")

        put_strike = iv_data.get('atm_put_strike')
        put_iv = iv_data.get('atm_put_iv')
        if put_strike is not None and put_iv is not None and isinstance(put_strike, (int,float)) and isinstance(put_iv, (int,float)):
            print(f"  Near-ATM Put (Strike: ${put_strike:,.2f}): {put_iv:.2%} IV")
        else:
            print(f"  Near-ATM Put: Strike or IV N/A (Strike: {put_strike if put_strike is not None else 'N/A'}, IV: {put_iv if put_iv is not None else 'N/A'})")
    elif iv_data is None:
        print("\nImplied Volatility: Data not available (fetch might have failed).")
    else:
        print(f"\nImplied Volatility: Invalid data format received ({type(iv_data)}).")


    # --- Other Tickers ---
    print("\n--- Other Related Tickers ---")
    strk_price = summary_data.get('strk_price')
    if strk_price is not None and isinstance(strk_price, (int, float)):
        print(f"STRK Current Price: ${strk_price:,.2f}")
    else:
        print(f"STRK Current Price: {strk_price if strk_price is not None else 'N/A'}")

    strf_price = summary_data.get('strf_price')
    if strf_price is not None and isinstance(strf_price, (int, float)):
        print(f"STRF Current Price: ${strf_price:,.2f}")
    else:
        print(f"STRF Current Price: {strf_price if strf_price is not None else 'N/A'}")

    print("\n--- End of Report ---")

def log_daily_metrics(metrics_data: dict) -> None:
    """
    Logs a dictionary of metrics data to a CSV file ('daily_metrics_log.csv').

    If the CSV file doesn't exist, it creates it and writes a header row.
    Otherwise, it appends the new metrics data as a new row.
    The order of columns is determined by a predefined header list.

    Args:
        metrics_data (dict): A dictionary where keys are column names and values
                             are the metrics to be logged.
    Returns:
        None
    """
    daily_log_file = "daily_metrics_log.csv"
    # Predefined header to ensure consistent column order in the CSV
    header = [
        'Date', 'MSTR_Price', 'MSTR_MNAV',
        'MSTR_IV_Call_Strike', 'MSTR_IV_Call_IV', 'MSTR_IV_Put_Strike', 'MSTR_IV_Put_IV', 'MSTR_IV_Expiration',
        'STRK_Price', 'STRF_Price', 'BTC_Price',
        'MSTR_Shares_Outstanding', 'MSTR_Market_Cap', 'MSTR_BTC_Holdings', 'MSTR_BTC_Holdings_Source'
    ]

    file_exists = os.path.exists(daily_log_file)

    try:
        with open(daily_log_file, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(header) # Write header only if file is new

            # Create a list of values from metrics_data, in the order of the header
            # Use metrics_data.get(col_name, 'N/A') to handle missing keys gracefully
            row_to_write = [metrics_data.get(col_name, 'N/A') for col_name in header]
            writer.writerow(row_to_write)
        logging.info(f"Successfully logged current metrics to {daily_log_file}")
    except IOError as e: # More specific exception for file I/O errors
        logging.error(f"IOError writing to {daily_log_file}: {e}", exc_info=True)
    except Exception as e: # Catch other potential errors during CSV writing
        logging.error(f"An unexpected error occurred during logging to {daily_log_file}: {e}", exc_info=True)

def get_near_atm_iv(ticker_symbol: str, current_stock_price: float | None) -> dict | None:
    """
    Fetches Implied Volatility (IV) for near-at-the-money (ATM) call and put options.

    It selects an option expiration date approximately 30-60 days out.
    If no suitable options are found in that range, it falls back to the nearest available date.
    For ATM options, it tries to find the closest strike to the current stock price.
    If the first ATM option lacks IV, it attempts to use the next closest one.

    Args:
        ticker_symbol (str): The stock ticker symbol (e.g., "MSTR").
        current_stock_price (float | None): The current market price of the stock.
                                            If None, the function cannot determine ATM options.
    Returns:
        dict | None: A dictionary containing the selected expiration date, and IV and strike
                     for the near-ATM call and put. Returns None if data cannot be fetched,
                     options are unavailable, or a valid IV cannot be found.
                     Example: {
                         "selected_expiration_date": "YYYY-MM-DD",
                         "atm_call_iv": 0.55 (float), "atm_call_strike": 150.0 (float),
                         "atm_put_iv": 0.53 (float), "atm_put_strike": 150.0 (float)
                     }
    """
    if current_stock_price is None:
        logging.error(f"Current stock price for {ticker_symbol} is None. Cannot proceed to find ATM options.")
        return None
    try:
        ticker = yf.Ticker(ticker_symbol)
        exp_dates = ticker.options # Get available expiration dates
        if not exp_dates:
            logging.warning(f"No option expiration dates found for {ticker_symbol}.")
            return None

        # --- Select Target Expiration Date ---
        selected_date_str = None
        target_exp_dt_obj_tuple = None # Stores (date_str, datetime_obj) for the 30-60 day target
        now = datetime.now()

        best_fallback_exp_dt_obj_tuple = None # Stores (date_str, datetime_obj) for the closest future date
        min_days_diff_fallback = float('inf')

        parsed_exp_dates = []
        for d_str in exp_dates:
            try:
                exp_dt = datetime.strptime(d_str, "%Y-%m-%d")
                # Ensure the expiration date is in the future
                if exp_dt > now:
                    parsed_exp_dates.append((d_str, exp_dt))
            except ValueError:
                logging.warning(f"Could not parse expiration date string: {d_str}")
                continue

        # Sort future expiration dates by date (earliest first)
        parsed_exp_dates.sort(key=lambda x: x[1])

        for d_str, exp_dt in parsed_exp_dates:
            days_to_expiry = (exp_dt - now).days

            # Update best_fallback_exp_dt_obj with the closest date in the future
            # (This loop structure ensures the first one encountered is the closest if already sorted)
            if days_to_expiry < min_days_diff_fallback : # No need for >=0 as we filtered future dates
                min_days_diff_fallback = days_to_expiry
                best_fallback_exp_dt_obj_tuple = (d_str, exp_dt)

            # Check for preferred 30-60 day range
            if 30 <= days_to_expiry <= 60:
                # If multiple dates are in range, the sort ensures we pick the earliest one
                if target_exp_dt_obj_tuple is None: # Take the first one that fits
                    target_exp_dt_obj_tuple = (d_str, exp_dt)

        if target_exp_dt_obj_tuple:
            selected_date_str = target_exp_dt_obj_tuple[0]
            logging.info(f"Selected target expiration date ({target_exp_dt_obj_tuple[1].strftime('%Y-%m-%d')}, { (target_exp_dt_obj_tuple[1] - now).days } days out) for {ticker_symbol}")
        elif best_fallback_exp_dt_obj_tuple: # Fallback to nearest future date if none in 30-60 day range
            selected_date_str = best_fallback_exp_dt_obj_tuple[0]
            logging.info(f"No option expiration found in 30-60 day range for {ticker_symbol}. Using nearest available: {selected_date_str} ({ (best_fallback_exp_dt_obj_tuple[1] - now).days } days out)")
        else:
             logging.error(f"No suitable future expiration dates found for {ticker_symbol} among parsed dates.")
             return None

        if not selected_date_str: # Should be caught by above logic, but as a safeguard
            logging.critical(f"selected_date_str is still None for {ticker_symbol} despite available exp_dates logic.")
            return None

        logging.info(f"Fetching options chain for {ticker_symbol} with expiration: {selected_date_str}")
        chain = ticker.option_chain(selected_date_str)

        # Check if option chain data (calls or puts) is empty
        if (chain.calls is None or chain.calls.empty) and \
           (chain.puts is None or chain.puts.empty):
            logging.warning(f"Both calls and puts DataFrames are empty or None for {ticker_symbol} on {selected_date_str}.")
            return None

        results = {
            "selected_expiration_date": selected_date_str,
            "atm_call_iv": None, "atm_call_strike": None,
            "atm_put_iv": None, "atm_put_strike": None
        }

        # --- Find Near-ATM Call ---
        if chain.calls is not None and not chain.calls.empty:
            # Calculate absolute difference between strike and current stock price
            chain.calls['abs_strike_diff'] = abs(chain.calls['strike'] - current_stock_price)
            # Sort by this difference to find the closest ATM options
            sorted_calls = chain.calls.sort_values(by='abs_strike_diff').reset_index(drop=True)

            if not sorted_calls.empty:
                # Try up to first 2 closest options if IV is missing
                for i in range(min(2, len(sorted_calls))):
                    atm_call_candidate = sorted_calls.iloc[i]
                    iv = atm_call_candidate.get('impliedVolatility')
                    strike = atm_call_candidate.get('strike')
                    # IV must be a positive number
                    if pd.notna(iv) and pd.notna(strike) and iv > 0:
                        results["atm_call_iv"] = iv
                        results["atm_call_strike"] = strike
                        logging.info(f"Selected ATM call for {ticker_symbol} on {selected_date_str}: Strike {strike}, IV {iv:.4f} (attempt {i+1})")
                        break # Found a valid IV, stop trying
                    else:
                        logging.warning(f"ATM call candidate {i+1} for {ticker_symbol} on {selected_date_str} (Strike: {strike}) has missing/invalid IV: {iv}. Trying next if available.")
                if results["atm_call_iv"] is None: # If loop finished without finding valid IV
                    logging.warning(f"Could not find valid IV for any near-ATM call for {ticker_symbol} on {selected_date_str} after {min(2, len(sorted_calls))} attempts.")
            else:
                logging.warning(f"Sorted calls list is empty for {ticker_symbol} on {selected_date_str} (after filtering).")
        else:
            logging.warning(f"Calls chain is None or empty for {ticker_symbol} on {selected_date_str}.")

        # --- Find Near-ATM Put ---
        if chain.puts is not None and not chain.puts.empty:
            chain.puts['abs_strike_diff'] = abs(chain.puts['strike'] - current_stock_price)
            sorted_puts = chain.puts.sort_values(by='abs_strike_diff').reset_index(drop=True)

            if not sorted_puts.empty:
                for i in range(min(2, len(sorted_puts))):
                    atm_put_candidate = sorted_puts.iloc[i]
                    iv = atm_put_candidate.get('impliedVolatility')
                    strike = atm_put_candidate.get('strike')
                    if pd.notna(iv) and pd.notna(strike) and iv > 0:
                        results["atm_put_iv"] = iv
                        results["atm_put_strike"] = strike
                        logging.info(f"Selected ATM put for {ticker_symbol} on {selected_date_str}: Strike {strike}, IV {iv:.4f} (attempt {i+1})")
                        break
                    else:
                        logging.warning(f"ATM put candidate {i+1} for {ticker_symbol} on {selected_date_str} (Strike: {strike}) has missing/invalid IV: {iv}. Trying next if available.")
                if results["atm_put_iv"] is None:
                    logging.warning(f"Could not find valid IV for any near-ATM put for {ticker_symbol} on {selected_date_str} after {min(2, len(sorted_puts))} attempts.")
            else:
                logging.warning(f"Sorted puts list is empty for {ticker_symbol} on {selected_date_str} (after filtering).")
        else:
            logging.warning(f"Puts chain is None or empty for {ticker_symbol} on {selected_date_str}.")

        # If neither call nor put IV was found, function effectively failed for its main purpose
        if results["atm_call_iv"] is None and results["atm_put_iv"] is None:
            logging.error(f"Could not retrieve valid IV for EITHER near-ATM call or put for {ticker_symbol} on {selected_date_str}.")
            return None # Or return results with None values if partial data is acceptable

        return results

    except Exception as e:
        logging.error(f"Error fetching or processing option data for {ticker_symbol}: {e}", exc_info=True)
        return None

if __name__ == "__main__":
    # Logging is configured globally at the script start.
    # This main block now orchestrates the data update process.
    logging.info("Main execution block started: Calling perform_daily_data_update.")

    update_status = perform_daily_data_update()

    if update_status:
        logging.info("perform_daily_data_update completed successfully.")
        # The function itself now handles the "Script Finished" type console messages.
        # print("Data update process completed successfully.") # Optional console message
    else:
        logging.error("perform_daily_data_update encountered an issue and did not complete all steps (see previous logs).")
        # print("Data update process encountered errors.") # Optional console message

    # The "Script Finished" log message is now part of perform_daily_data_update
