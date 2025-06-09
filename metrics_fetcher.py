import requests
import json
import logging
from datetime import datetime
import csv
import os

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Define constants for the URLs
MSTR_KPI_URL = "https://api.microstrategy.com/btc/mstrKpiData"
BITCOIN_KPI_URL = "https://api.microstrategy.com/btc/bitcoinKpis"
CSV_FILENAME = "consolidated_kpi_history.csv"

def get_consolidated_kpi_data():
    """
    Fetches KPI data from MSTR and Bitcoin APIs, consolidates them, and adds a timestamp.

    Returns:
        A dictionary containing the consolidated KPI data.
    """
    consolidated_data = {}

    # Fetch data from MSTR_KPI_URL
    try:
        response_mstr = requests.get(MSTR_KPI_URL)
        response_mstr.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)
        mstr_kpi_data = response_mstr.json()
        consolidated_data.update(mstr_kpi_data)
        logging.info(f"Successfully fetched MSTR KPI data from {MSTR_KPI_URL}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching MSTR KPI data from {MSTR_KPI_URL}: {e}")
        consolidated_data["mstr_kpi_error"] = f"Failed to fetch data: {e}"
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding JSON response from {MSTR_KPI_URL}: {e}")
        consolidated_data["mstr_kpi_error"] = f"Failed to parse JSON data: {e}"

    # Fetch data from BITCOIN_KPI_URL
    try:
        response_bitcoin = requests.get(BITCOIN_KPI_URL)
        response_bitcoin.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)
        bitcoin_kpi_data = response_bitcoin.json()
        # Simple update; if key clashes occur, Bitcoin data will overwrite MSTR data for that key.
        # Consider prefixing keys if necessary, e.g., {'bitcoin_marketCap': value}
        consolidated_data.update(bitcoin_kpi_data)
        logging.info(f"Successfully fetched Bitcoin KPI data from {BITCOIN_KPI_URL}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching Bitcoin KPI data from {BITCOIN_KPI_URL}: {e}")
        consolidated_data["bitcoin_kpi_error"] = f"Failed to fetch data: {e}"
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding JSON response from {BITCOIN_KPI_URL}: {e}")
        consolidated_data["bitcoin_kpi_error"] = f"Failed to parse JSON data: {e}"

    # Add timestamp
    consolidated_data['timestamp'] = datetime.utcnow().isoformat()

    return consolidated_data

def save_kpi_data_to_csv(data_dict):
    """
    Saves the given data dictionary to a CSV file.
    Appends data if the file exists, otherwise creates a new file with headers.

    Args:
        data_dict: A dictionary containing the data to save.
    """
    if not data_dict:
        logging.warning("No data provided to save_kpi_data_to_csv. Skipping.")
        return

    file_exists = os.path.exists(CSV_FILENAME)
    # It's also good practice to check if the file is empty even if it exists
    is_empty_file = False
    if file_exists:
        is_empty_file = os.path.getsize(CSV_FILENAME) == 0

    try:
        with open(CSV_FILENAME, 'a', newline='') as csvfile:
            # Ensure all potential keys are included in fieldnames
            # This might need adjustment if data_dict structure varies significantly
            # For now, using keys from the current data_dict
            fieldnames = list(data_dict.keys())

            # Ensure 'timestamp' is a field, and perhaps make it the first column for consistency
            if 'timestamp' not in fieldnames and any(data_dict.values()):
                # This case should ideally not happen if get_consolidated_kpi_data always adds it
                # and data_dict is not empty.
                logging.warning("Timestamp missing in non-empty data_dict, adding current time.")
                data_dict['timestamp'] = datetime.utcnow().isoformat()
                fieldnames.append('timestamp')

            # Reorder fieldnames to have 'timestamp' first if it exists and was added.
            # This also handles the case where timestamp was already there.
            if 'timestamp' in fieldnames:
                fieldnames.remove('timestamp')
                fieldnames.insert(0, 'timestamp')
            else:
                # If timestamp was not in keys and data_dict was empty or all Nones,
                # it wouldn't have been added. Add it now to ensure it's a column.
                # This ensures the timestamp column is created even for an empty data record,
                # though get_consolidated_kpi_data should prevent totally empty dicts.
                if not any(data_dict.values()): # If dict is empty or all values are None/False/0
                    data_dict['timestamp'] = datetime.utcnow().isoformat()
                fieldnames.insert(0, 'timestamp')


            writer = csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction='ignore')

            if not file_exists or is_empty_file:
                writer.writeheader()
                logging.info(f"Writing CSV header to {CSV_FILENAME}")

            writer.writerow(data_dict)
            logging.info(f"Successfully wrote data to {CSV_FILENAME}")
    except IOError as e:
        logging.error(f"Error writing to CSV file {CSV_FILENAME}: {e}")
    except Exception as e:
        logging.error(f"An unexpected error occurred during CSV writing: {e}")
