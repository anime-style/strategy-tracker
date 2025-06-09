# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
# Declare API_UPDATE_TOKEN environment variable.
# This should be set during 'docker run' e.g., -e API_UPDATE_TOKEN="your_secret_token"
ENV API_UPDATE_TOKEN=""

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container at /app
COPY financial_tracker.py .
COPY app.py .
COPY entrypoint.sh .

# Make the entrypoint script executable
RUN chmod +x ./entrypoint.sh

# Expose port 8050 to the outside world
EXPOSE 8050

# Define the entrypoint for the container
ENTRYPOINT ["./entrypoint.sh"]

# To build this Docker image:
# ---------------------------
# Run the following command in the directory containing this Dockerfile:
# docker build -t financial-metrics-app .

# To run the Docker container:
# ----------------------------
# After building the image, run the following command:
# docker run -p 8050:8050 financial-metrics-app
#
# This will start the application:
# 1. `financial_tracker.py` will run to generate/update data files.
# 2. `app.py` (Dash web server) will start.
#
# Access the application by opening a web browser to:
# http://localhost:8050
