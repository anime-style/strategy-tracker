import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import csv
import os
import logging

# Setup Logging
logging.basicConfig(
    filename='financial_tracker.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(module)s - %(funcName)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Placeholder - VERIFY AND UPDATE THIS VALUE with Microstrategy's latest reported Bitcoin holdings.
MSTR_BTC_HOLDINGS = 205000

def get_current_price(ticker_symbol):
    try:
        ticker = yf.Ticker(ticker_symbol)
        info = ticker.info

        price_keys = []
        # Use different key priorities based on asset type
        if "-USD" in ticker_symbol.upper() or ticker_symbol.upper() == "BTC": # Assuming crypto
            price_keys = ['regularMarketPrice', 'marketPrice', 'previousClose', 'currentPrice', 'bid', 'ask']
        else: # Assuming equity
            price_keys = ['currentPrice', 'regularMarketPrice', 'marketPrice', 'previousClose', 'bid', 'ask']

        for key in price_keys:
            price = info.get(key)
            if price is not None and price > 0: # Ensure price is a positive value
                return float(price)

        # Fallback for crypto if common fields fail, check for 'financialData' which might contain it
        if "-USD" in ticker_symbol.upper() and info.get('financialData'):
            price = info.get('financialData').get('currentPrice')
            if price is not None and price > 0:
                return float(price)

        # If info dict itself is minimal or seems incomplete, this might indicate an issue with the ticker symbol in yfinance
        if not info or len(info) < 5 : # Arbitrary small number of keys
            logging.error(f"Limited information available for {ticker_symbol}. It might be delisted, an invalid ticker, or data is temporarily unavailable.")
            return None

        logging.warning(f"Could not find a valid price for {ticker_symbol} using preferred keys. Check available info: {list(info.keys())}")
        return None
    except Exception as e:
        # Check if the error is due to a common issue for delisted/invalid tickers
        if "No market data found" in str(e) or "No data found for ticker" in str(e) or "failed to decrypt" in str(e).lower() or "private key" in str(e).lower(): # Added more checks for common yf issues
            logging.error(f"No market data found for {ticker_symbol} (or access issue). It might be delisted, an invalid ticker, or a temporary yfinance problem.")
        else:
            logging.error(f"Error fetching current price for {ticker_symbol}: {e}", exc_info=True)
        return None

def get_historical_data(ticker_symbol, period="1y"):
    try:
        ticker = yf.Ticker(ticker_symbol)
        history = ticker.history(period=period)
        if history.empty:
            info = ticker.info # Check if info is also minimal
            if not info or not info.get('shortName'):
                 logging.warning(f"No historical data found for {ticker_symbol} for period {period}. Ticker might be invalid or delisted.")
            else:
                 logging.warning(f"No historical data found for {ticker_symbol} for period {period}, though ticker appears valid (Name: {info.get('shortName')}).")
        else:
            logging.info(f"Successfully fetched historical data for {ticker_symbol} for period {period}.")
        return history
    except Exception as e:
        logging.error(f"Error fetching historical data for {ticker_symbol}: {e}", exc_info=True)
        return pd.DataFrame() # Return empty DataFrame

def get_shares_outstanding(ticker_symbol):
    try:
        ticker = yf.Ticker(ticker_symbol)
        info = ticker.info

        preferred_keys = ['sharesOutstanding', 'impliedSharesOutstanding', 'floatShares']
        shares = None
        key_used = ""

        for key in preferred_keys:
            val = info.get(key)
            if val is not None and val > 0:
                shares = val
                key_used = key
                logging.info(f"Found shares for {ticker_symbol} using key '{key_used}': {shares}")
                break

        if shares is None: # If direct keys didn't work or gave zero, try calculating from market cap
            market_cap = info.get('marketCap')

            # Try to get a valid price for calculation
            price_for_calc = None
            price_calc_keys = ['regularMarketPrice', 'currentPrice', 'previousClose']
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
                key_used = "marketCap / " + price_key_used_for_calc
                logging.info(f"Calculated shares for {ticker_symbol} using '{key_used}': {shares}")
            else:
                logging.warning(f"Could not retrieve or calculate shares outstanding for {ticker_symbol} from provided keys or market cap. Info dict has keys: {list(info.keys()) if isinstance(info, dict) else 'Info not a dict'}")
                return None

        return shares
    except Exception as e:
        logging.error(f"Error fetching shares outstanding for {ticker_symbol}: {e}", exc_info=True)
        return None

def calculate_mstr_mnav(btc_holdings, btc_price, mstr_shares_outstanding):
    if not all([isinstance(btc_holdings, (int, float)), isinstance(btc_price, (int, float)), isinstance(mstr_shares_outstanding, (int, float))]):
        logging.error(f"Missing or invalid type for one or more inputs for MNAV calculation. btc_holdings: {btc_holdings} (type {type(btc_holdings)}), btc_price: {btc_price} (type {type(btc_price)}), mstr_shares: {mstr_shares_outstanding} (type {type(mstr_shares_outstanding)})")
        return None
    if mstr_shares_outstanding == 0:
        logging.error("MSTR shares outstanding is zero, cannot calculate MNAV.")
        return None
    logging.info("Successfully calculated MSTR MNAV.")
    return (btc_holdings * btc_price) / mstr_shares_outstanding


def display_summary_report(summary_data):
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

    print(f"Assumed BTC Holdings: {summary_data.get('mstr_btc_holdings', 'N/A'):,} BTC")

    # MNAV
    current_mnav = summary_data.get('current_mnav')
    if current_mnav is not None and isinstance(current_mnav, (int, float)):
        print(f"Estimated Current MNAV: ${current_mnav:,.2f}")
        market_vs_mnav = summary_data.get('market_vs_mnav_percentage')
        if market_vs_mnav is not None and isinstance(market_vs_mnav, (int, float)):
            print(f"  Market vs MNAV: {market_vs_mnav:+.2f}%")
        else:
            print(f"  Market vs MNAV: {market_vs_mnav if market_vs_mnav is not None else 'N/A'}")
    else:
        print(f"Estimated Current MNAV: {current_mnav if current_mnav is not None else 'N/A'}")

    avg_hist_mnav = summary_data.get('avg_hist_mnav')
    if avg_hist_mnav is not None and isinstance(avg_hist_mnav, (int, float)):
        print(f"Average Historical MNAV (1yr): ${avg_hist_mnav:,.2f}")
        curr_vs_hist_mnav = summary_data.get('current_mnav_vs_hist_avg_percentage')
        if curr_vs_hist_mnav is not None and isinstance(curr_vs_hist_mnav, (int, float)):
            print(f"  Current MNAV vs Hist. Avg: {curr_vs_hist_mnav:+.2f}%")
        else:
            print(f"  Current MNAV vs Hist. Avg: {curr_vs_hist_mnav if curr_vs_hist_mnav is not None else 'N/A'}")
    else:
        print(f"Average Historical MNAV (1yr): {avg_hist_mnav if avg_hist_mnav is not None else 'N/A'}")

    # Implied Volatility
    iv_data = summary_data.get('iv_data')
    if iv_data and isinstance(iv_data, dict):
        print(f"\nImplied Volatility (Expiration: {iv_data.get('selected_expiration_date', 'N/A')}):")
        call_strike = iv_data.get('atm_call_strike')
        call_iv = iv_data.get('atm_call_iv')
        if call_strike is not None and call_iv is not None and isinstance(call_strike, (int,float)) and isinstance(call_iv, (int,float)):
            print(f"  Near-ATM Call (Strike: ${call_strike:,.2f}): {call_iv:.2%} IV")
        else:
            print(f"  Near-ATM Call: Strike or IV N/A (Strike: {call_strike}, IV: {call_iv})")

        put_strike = iv_data.get('atm_put_strike')
        put_iv = iv_data.get('atm_put_iv')
        if put_strike is not None and put_iv is not None and isinstance(put_strike, (int,float)) and isinstance(put_iv, (int,float)):
            print(f"  Near-ATM Put (Strike: ${put_strike:,.2f}): {put_iv:.2%} IV")
        else:
            print(f"  Near-ATM Put: Strike or IV N/A (Strike: {put_strike}, IV: {put_iv})")
    elif iv_data is None: # Explicitly handle if iv_data itself is None (not fetched)
        print("\nImplied Volatility: Data not available.")
    else: # If iv_data is not a dict or None, print its representation
        print(f"\nImplied Volatility: Invalid data format ({iv_data})")


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

    print("--- End of Report ---")

def log_daily_metrics(metrics_data):
    daily_log_file = "daily_metrics_log.csv"
    # Order of keys in metrics_data dictionary will determine column order if file is new
    # For consistency, especially if metrics_data could have varying keys/order, define header explicitly
    header = [
        'Date', 'MSTR_Price', 'MSTR_MNAV',
        'MSTR_IV_Call_Strike', 'MSTR_IV_Call_IV', 'MSTR_IV_Put_Strike', 'MSTR_IV_Put_IV', 'MSTR_IV_Expiration',
        'STRK_Price', 'STRF_Price', 'BTC_Price',
        'MSTR_Shares_Outstanding', 'MSTR_BTC_Holdings'
    ]

    file_exists = os.path.exists(daily_log_file)

    try:
        with open(daily_log_file, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(header)

            # Ensure row is written in the same order as the header
            row_to_write = [metrics_data.get(col_name, 'N/A') for col_name in header]
            writer.writerow(row_to_write)
        logging.info(f"Successfully logged current metrics to {daily_log_file}")
    except IOError as e:
        logging.error(f"Error writing to {daily_log_file}: {e}", exc_info=True)
    except Exception as e:
        logging.error(f"An unexpected error occurred during logging: {e}", exc_info=True)

def get_near_atm_iv(ticker_symbol, current_stock_price):
    if current_stock_price is None:
        logging.error(f"Current stock price for {ticker_symbol} is None. Cannot proceed.")
        return None
    try:
        ticker = yf.Ticker(ticker_symbol)
        exp_dates = ticker.options
        if not exp_dates:
            logging.warning(f"No option expiration dates found for {ticker_symbol}.")
            return None

        # Select Target Expiration Date
        selected_date_str = None
        target_exp_dt_obj = None
        now = datetime.now()

        # Try to find a date 30-60 days out
        best_fallback_exp_dt_obj = None
        min_days_diff_fallback = float('inf')

        parsed_exp_dates = []
        for d_str in exp_dates:
            try:
                exp_dt = datetime.strptime(d_str, "%Y-%m-%d")
                parsed_exp_dates.append((d_str, exp_dt))
            except ValueError:
                logging.warning(f"Could not parse expiration date string: {d_str}")
                continue

        # Sort parsed dates to ensure we prioritize earlier dates if multiple satisfy criteria
        parsed_exp_dates.sort(key=lambda x: x[1])

        for d_str, exp_dt in parsed_exp_dates:
            days_to_expiry = (exp_dt - now).days

            # Update best_fallback_exp_dt_obj with the closest date in the future
            if days_to_expiry >= 0 and days_to_expiry < min_days_diff_fallback:
                min_days_diff_fallback = days_to_expiry
                best_fallback_exp_dt_obj = (d_str, exp_dt)

            if 30 <= days_to_expiry <= 60:
                if target_exp_dt_obj is None or exp_dt < target_exp_dt_obj[1]: # Prioritize earliest in range
                    target_exp_dt_obj = (d_str, exp_dt)

        if target_exp_dt_obj:
            selected_date_str = target_exp_dt_obj[0]
            logging.info(f"Selected target expiration date (30-60 days): {selected_date_str} for {ticker_symbol}")
        elif best_fallback_exp_dt_obj: # Fallback to nearest future date
            selected_date_str = best_fallback_exp_dt_obj[0]
            logging.info(f"No option expiration found in 30-60 day range for {ticker_symbol}. Using nearest available: {selected_date_str}")
        else: # Should not happen if exp_dates was not empty and contained future dates
             logging.error(f"No suitable future expiration dates found for {ticker_symbol} among parsed dates.")
             return None


        if not selected_date_str: # Final check
            logging.critical(f"selected_date_str is still None for {ticker_symbol} despite available exp_dates.")
            return None

        logging.info(f"Fetching options chain for {ticker_symbol} with expiration: {selected_date_str}")
        chain = ticker.option_chain(selected_date_str)

        if chain.calls.empty and chain.puts.empty:
            logging.warning(f"Both calls and puts are empty for {ticker_symbol} on {selected_date_str}.")
            return None

        results = {
            "selected_expiration_date": selected_date_str,
            "atm_call_iv": None, "atm_call_strike": None,
            "atm_put_iv": None, "atm_put_strike": None
        }

        # Find Near-ATM Call
        if not chain.calls.empty:
            chain.calls['abs_strike_diff'] = abs(chain.calls['strike'] - current_stock_price)
            sorted_calls = chain.calls.sort_values(by='abs_strike_diff').reset_index(drop=True)
            if not sorted_calls.empty:
                for i in range(min(2, len(sorted_calls))): # Try first 2 ATM calls
                    atm_call_candidate = sorted_calls.iloc[i]
                    iv = atm_call_candidate.get('impliedVolatility')
                    strike = atm_call_candidate.get('strike')
                    if pd.notna(iv) and pd.notna(strike) and iv > 0: # IV should be positive
                        results["atm_call_iv"] = iv
                        results["atm_call_strike"] = strike
                        logging.info(f"Selected ATM call for {ticker_symbol} on {selected_date_str}: Strike {strike}, IV {iv} (attempt {i+1})")
                        break
                    else:
                        logging.warning(f"ATM call candidate {i+1} for {ticker_symbol} on {selected_date_str} (Strike: {strike}) has missing/invalid IV: {iv}. Trying next if available.")
                if results["atm_call_iv"] is None:
                    logging.warning(f"Could not find valid IV for any near-ATM call for {ticker_symbol} on {selected_date_str} after {min(2, len(sorted_calls))} attempts.")
            else:
                logging.warning(f"Sorted calls list is empty for {ticker_symbol} on {selected_date_str}.")
        else:
            logging.warning(f"Calls chain is empty for {ticker_symbol} on {selected_date_str}.")

        # Find Near-ATM Put
        if not chain.puts.empty:
            chain.puts['abs_strike_diff'] = abs(chain.puts['strike'] - current_stock_price)
            sorted_puts = chain.puts.sort_values(by='abs_strike_diff').reset_index(drop=True)
            if not sorted_puts.empty:
                for i in range(min(2, len(sorted_puts))): # Try first 2 ATM puts
                    atm_put_candidate = sorted_puts.iloc[i]
                    iv = atm_put_candidate.get('impliedVolatility')
                    strike = atm_put_candidate.get('strike')
                    if pd.notna(iv) and pd.notna(strike) and iv > 0: # IV should be positive
                        results["atm_put_iv"] = iv
                        results["atm_put_strike"] = strike
                        logging.info(f"Selected ATM put for {ticker_symbol} on {selected_date_str}: Strike {strike}, IV {iv} (attempt {i+1})")
                        break
                    else:
                        logging.warning(f"ATM put candidate {i+1} for {ticker_symbol} on {selected_date_str} (Strike: {strike}) has missing/invalid IV: {iv}. Trying next if available.")
                if results["atm_put_iv"] is None:
                    logging.warning(f"Could not find valid IV for any near-ATM put for {ticker_symbol} on {selected_date_str} after {min(2, len(sorted_puts))} attempts.")
            else:
                logging.warning(f"Sorted puts list is empty for {ticker_symbol} on {selected_date_str}.")
        else:
            logging.warning(f"Puts chain is empty for {ticker_symbol} on {selected_date_str}.")

        # Check if we got at least one IV
        if results["atm_call_iv"] is None and results["atm_put_iv"] is None:
            logging.error(f"Could not retrieve valid IV for EITHER ATM call or put for {ticker_symbol} on {selected_date_str}.")
            # Depending on requirements, we might still return partial data if one was found.
            # For now, if both are None, it's a more significant failure for "near-ATM IV".
            # If the goal is to get *any* IV, then this check might be relaxed.
            # Return None here implies the function failed to meet its primary goal of getting representative ATM IV.
            return None

        return results

    except Exception as e:
        logging.error(f"Error fetching or processing option data for {ticker_symbol}: {e}", exc_info=True)
        # import traceback # No longer needed with exc_info=True
        # traceback.print_exc()
        return None

if __name__ == "__main__":
    # Initialize report_data dictionary
    report_data = {
        'script_run_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'mstr_price': None, 'mstr_shares_outstanding': None, 'mstr_btc_holdings': MSTR_BTC_HOLDINGS,
        'current_mnav': None, 'market_vs_mnav_percentage': None,
        'avg_hist_mnav': None, 'current_mnav_vs_hist_avg_percentage': None,
        'iv_data': None,
        'strk_price': None, 'strf_price': None, 'btc_price': None
    }

    # Print persistent warnings first
    logging.info(f"Script execution started.")
    print(f"--- Microstrategy Bitcoin Holdings (Placeholder): {MSTR_BTC_HOLDINGS:,} BTC ---")
    print("Note: This BTC holding number is a placeholder and should be verified from official Microstrategy announcements.\n")

    tickers = ["MSTR", "STRK", "STRF", "BTC-USD", "INVALIDTICKERXYZ"]
    current_prices = {} # Store current prices here

    # Fetch current prices - less verbose now
    for tick in tickers:
        price = get_current_price(tick)
        if price is not None:
            current_prices[tick] = price
        # Warnings for failed fetches are inside get_current_price / now logged

    report_data['mstr_price'] = current_prices.get("MSTR")
    report_data['strk_price'] = current_prices.get("STRK")
    report_data['strf_price'] = current_prices.get("STRF")
    report_data['btc_price'] = current_prices.get("BTC-USD")

    if report_data['btc_price'] and report_data['btc_price'] > 80000: # Adjusted threshold
        # This is a user-facing warning, so print is okay.
        print("Warning: The fetched BTC-USD price from yfinance seems unusually high. Cross-verify with other sources.\n")
        logging.warning(f"High BTC-USD price detected: {report_data['btc_price']}")

    # MSTR MNAV Calculation
    mstr_current_price_for_calc = report_data['mstr_price']
    btc_current_price_for_calc = report_data['btc_price']
    mstr_shares_outstanding_val = get_shares_outstanding("MSTR")
    report_data['mstr_shares_outstanding'] = mstr_shares_outstanding_val

    current_mnav_val = None
    if MSTR_BTC_HOLDINGS and btc_current_price_for_calc and mstr_shares_outstanding_val :
        current_mnav_val = calculate_mstr_mnav(MSTR_BTC_HOLDINGS, btc_current_price_for_calc, mstr_shares_outstanding_val)
        report_data['current_mnav'] = current_mnav_val
        if mstr_current_price_for_calc and current_mnav_val and current_mnav_val > 0:
            report_data['market_vs_mnav_percentage'] = ((mstr_current_price_for_calc / current_mnav_val) - 1) * 100
    else:
        logging.warning("Cannot calculate current MSTR MNAV due to missing critical data (BTC price, MSTR shares, or BTC holdings).")

    # MSTR Historical MNAV
    avg_hist_mnav_val = None
    if mstr_shares_outstanding_val is None: # Check if shares are still None
        logging.warning("MSTR shares outstanding is None before historical MNAV. Attempting re-fetch.")
        mstr_shares_outstanding_val = get_shares_outstanding("MSTR") # Attempt re-fetch
        if mstr_shares_outstanding_val is not None:
            report_data['mstr_shares_outstanding'] = mstr_shares_outstanding_val
        else:
            logging.error("Failed to re-fetch MSTR shares outstanding for historical MNAV. Cannot proceed with historical MNAV.")

    mstr_hist = get_historical_data("MSTR", period="1y")
    btc_hist = get_historical_data("BTC-USD", period="1y")

    if mstr_hist is not None and not mstr_hist.empty and \
       btc_hist is not None and not btc_hist.empty and \
       mstr_shares_outstanding_val is not None and mstr_shares_outstanding_val > 0:

        if 'Close' in mstr_hist.columns and 'Close' in btc_hist.columns:
            try:
                mstr_hist_normalized = mstr_hist.copy()
                mstr_hist_normalized.index = pd.to_datetime(mstr_hist_normalized.index, utc=True).normalize()
                btc_hist_normalized = btc_hist.copy()
                btc_hist_normalized.index = pd.to_datetime(btc_hist_normalized.index, utc=True).normalize()

                merged_data = pd.merge(mstr_hist_normalized[['Close']], btc_hist_normalized[['Close']], left_index=True, right_index=True, suffixes=('_MSTR', '_BTC'))

                if not merged_data.empty:
                    merged_data['MNAV'] = (MSTR_BTC_HOLDINGS * merged_data['Close_BTC']) / mstr_shares_outstanding_val

                    csv_filename = "mstr_historical_mnav.csv"
                    merged_data.to_csv(csv_filename)
                    logging.info(f"Saved historical MNAV data to {csv_filename}")

                    avg_hist_mnav_val = merged_data['MNAV'].mean()
                    report_data['avg_hist_mnav'] = avg_hist_mnav_val

                    if current_mnav_val and avg_hist_mnav_val and avg_hist_mnav_val > 0:
                        report_data['current_mnav_vs_hist_avg_percentage'] = ((current_mnav_val / avg_hist_mnav_val) - 1) * 100
                else:
                    logging.warning("Could not merge MSTR and BTC historical data for historical MNAV.")
            except Exception as e:
                logging.error(f"Error during historical MNAV calculation: {e}", exc_info=True)
        else:
            logging.warning("'Close' column missing in MSTR or BTC historical data for historical MNAV.")
    else:
        logging.warning("Cannot calculate historical MNAV (missing MSTR/BTC hist data, or MSTR shares is zero/None).")

    # MSTR Implied Volatility
    if mstr_current_price_for_calc: # Check if MSTR price is available
        iv_data_val = get_near_atm_iv("MSTR", mstr_current_price_for_calc)
        report_data['iv_data'] = iv_data_val
    else:
        logging.warning("Cannot fetch Implied Volatility for MSTR as its current price is unavailable.")

    # --- Display Summary Report ---
    # This remains for user console output
    display_summary_report(report_data)

    # --- Log Daily Metrics ---
    daily_log_data = {
        'Date': report_data['script_run_time'],
        'MSTR_Price': report_data['mstr_price'],
        'MSTR_MNAV': report_data['current_mnav'],
        'MSTR_IV_Call_Strike': report_data['iv_data'].get('atm_call_strike', 'N/A') if report_data['iv_data'] else 'N/A',
        'MSTR_IV_Call_IV': report_data['iv_data'].get('atm_call_iv', 'N/A') if report_data['iv_data'] else 'N/A',
        'MSTR_IV_Put_Strike': report_data['iv_data'].get('atm_put_strike', 'N/A') if report_data['iv_data'] else 'N/A',
        'MSTR_IV_Put_IV': report_data['iv_data'].get('atm_put_iv', 'N/A') if report_data['iv_data'] else 'N/A',
        'MSTR_IV_Expiration': report_data['iv_data'].get('selected_expiration_date', 'N/A') if report_data['iv_data'] else 'N/A',
        'STRK_Price': report_data['strk_price'],
        'STRF_Price': report_data['strf_price'],
        'BTC_Price': report_data['btc_price'],
        'MSTR_Shares_Outstanding': report_data['mstr_shares_outstanding'],
        'MSTR_BTC_Holdings': report_data['mstr_btc_holdings']
    }
    for key, value in daily_log_data.items():
        if value is None:
            daily_log_data[key] = 'N/A'

    log_daily_metrics(daily_log_data)
    print(f"\nDaily metrics also logged to daily_metrics_log.csv") # Added newline for spacing

    print("\n--- Script Finished ---")
