import logging
import os
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.middleware.wsgi import WSGIMiddleware
import uvicorn # Though uvicorn is run from CLI, importing can be good for type hinting or programmatic use if ever needed.

# Configure logging for the entire application (FastAPI + Dash + financial_tracker)
# This will be the primary logging configuration.
# Ensure this is called before any other module (like app or financial_tracker) might try to log.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(module)s - %(funcName)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    # filename='app_main.log', # Optionally, direct all logs to a specific file for combined app
    # force=True # Use with caution if needing to override other basicConfigs
)

# Import the Dash app instance from app.py
# This assumes app.py is in the same directory and 'app' is the Dash instance.
try:
    from app import app as dash_app # dash_app is the Dash application object
    logging.info("Successfully imported Dash app from app.py")
except ImportError as e:
    logging.critical(f"CRITICAL ERROR: Failed to import Dash app from app.py: {e}. Dash UI will not be available.")
    dash_app = None # Placeholder

# Import the data update function from financial_tracker.py
try:
    from financial_tracker import perform_daily_data_update
    logging.info("Successfully imported perform_daily_data_update from financial_tracker.py")
except ImportError as e:
    logging.critical(f"CRITICAL ERROR: Failed to import perform_daily_data_update from financial_tracker.py: {e}. API endpoint will not function correctly.")
    perform_daily_data_update = None

# Import KPI fetching and saving functions
try:
    from metrics_fetcher import get_consolidated_kpi_data, save_kpi_data_to_csv
    logging.info("Successfully imported KPI functions from metrics_fetcher.py")
except ImportError as e:
    logging.critical(f"CRITICAL ERROR: Failed to import from metrics_fetcher.py: {e}. KPI endpoint will not function correctly.")
    get_consolidated_kpi_data = None
    save_kpi_data_to_csv = None


# Initialize FastAPI app
app_fastapi = FastAPI(title="Financial Metrics API & Dashboard")

# --- API Endpoint Definition ---
@app_fastapi.post("/api/update-data", status_code=204)
async def api_trigger_update_data(request: Request):
    """
    Triggers the data update process for financial metrics.
    Requires a valid API token in the 'X-API-Token' header.
    The token value is read from the 'API_UPDATE_TOKEN' environment variable.
    """
    EXPECTED_API_TOKEN = os.environ.get('API_UPDATE_TOKEN')

    if not EXPECTED_API_TOKEN:
        logging.error("SERVER CONFIG ERROR: API_UPDATE_TOKEN environment variable is not set for FastAPI app.")
        raise HTTPException(status_code=500, detail="Server configuration error: API token not set.")

    auth_token = request.headers.get('X-API-Token')

    if not auth_token or auth_token != EXPECTED_API_TOKEN:
        logging.warning(f"Unauthorized API access attempt to /api/update-data. Provided token: '{auth_token}'")
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid or missing API token.")

    if perform_daily_data_update is None:
        logging.error("SERVER ERROR: perform_daily_data_update function not available (import failed). API endpoint cannot function.")
        raise HTTPException(status_code=500, detail="Server error: Update function not available. Check server logs.")

    try:
        logging.info("Authenticated API call received for /api/update-data (FastAPI). Attempting data update process.")
        # perform_daily_data_update is a synchronous function.
        # If it were async, we would 'await' it.
        # For now, FastAPI will run this synchronous function in a thread pool.
        update_successful = perform_daily_data_update()

        if update_successful:
            logging.info("Data update process successful via API call (FastAPI).")
            return Response(status_code=204) # Return HTTP 204 for success with no content
        else:
            logging.error("Data update process failed via API call (FastAPI - perform_daily_data_update returned False).")
            raise HTTPException(status_code=400, detail="Data update process failed. Check server logs for specific errors.")
    except Exception as e:
        logging.exception("Unhandled exception during API-triggered data update (FastAPI):")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred during data update: {str(e)}")

# --- New KPI Endpoint ---
@app_fastapi.get("/api/consolidated-kpi")
async def get_consolidated_kpi():
    """
    Provides consolidated KPI data.
    Fetches data using get_consolidated_kpi_data, saves it to CSV, and returns the data.
    """
    logging.info("Endpoint /api/consolidated-kpi execution started.")

    if not get_consolidated_kpi_data or not save_kpi_data_to_csv:
        logging.error("SERVER ERROR: KPI functions (get_consolidated_kpi_data or save_kpi_data_to_csv) not available due to import failure.")
        raise HTTPException(status_code=500, detail="Server error: KPI processing functions are not available. Check server logs.")

    try:
        # 1. Fetch KPI data
        kpi_data = get_consolidated_kpi_data() # This is a synchronous function

        # 2. Check for errors during fetching (already logged by metrics_fetcher)
        if not kpi_data: # Should not happen if get_consolidated_kpi_data always returns a dict
            logging.error("get_consolidated_kpi_data returned None or empty. This should not happen.")
            # Still attempt to save, as save_kpi_data_to_csv handles empty dicts.
            # And return an appropriate response.
            kpi_data = {"error": "Failed to fetch any KPI data or data was empty."} # Ensure kpi_data is a dict

        if "mstr_kpi_error" in kpi_data or "bitcoin_kpi_error" in kpi_data:
            logging.warning(f"Fetched KPI data contains error indicators: MSTR error: {kpi_data.get('mstr_kpi_error')}, Bitcoin error: {kpi_data.get('bitcoin_kpi_error')}")
            # Proceed to save and return whatever was fetched

        # 3. Save data to CSV
        try:
            # save_kpi_data_to_csv is synchronous
            save_kpi_data_to_csv(kpi_data)
            logging.info("Successfully attempted to save KPI data to CSV.")
        except Exception as csve:
            # Log the error but don't let it stop the API from returning fetched data
            logging.exception(f"Error saving KPI data to CSV: {csve}")
            # Optionally, add a note to the response:
            # kpi_data["csv_save_error"] = str(csve) # This would alter the response based on save status.
                                                 # For now, requirement is to return fetched data regardless.

        logging.info("Endpoint /api/consolidated-kpi execution finished successfully.")
        return kpi_data # FastAPI serializes dict to JSON

    except HTTPException:
        raise # Re-raise HTTPException directly if it's one we've thrown (like the 500 for import issues)
    except Exception as e:
        logging.exception("Unhandled exception in /api/consolidated-kpi endpoint:")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred while processing KPI data: {str(e)}")

# --- Mount Dash App ---
# This makes the Dash app accessible under the FastAPI application.
if dash_app:
    logging.info("Mounting Dash app to FastAPI at root ('/').")
    # The WSGIMiddleware converts the Dash app's Flask server (WSGI) into an ASGI app that FastAPI can use.
    app_fastapi.mount("/", WSGIMiddleware(dash_app.server))
    logging.info("Dash app successfully mounted.")
else:
    logging.error("Dash app object ('dash_app') is None due to import failure. Dash UI will not be served.")

# --- Main execution block (for running with uvicorn directly, e.g., python main.py) ---
# This allows running 'python main.py' for local development.
# The Docker container will use 'uvicorn main:app_fastapi --host 0.0.0.0 --port 8050'.
if __name__ == "__main__":
    logging.info("Starting FastAPI server with Uvicorn directly from main.py (for local development)")
    # Note: For development, 'uvicorn main:app_fastapi --reload --host 0.0.0.0 --port 8050' from CLI is typical.
    uvicorn.run("main:app_fastapi", host="0.0.0.0", port=8050, log_level="info", reload=True)
