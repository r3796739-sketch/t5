# Use a lightweight Python image
FROM python:3.10-slim

# Set environment variables
ENV PYTHONUNBUFFERED True

# Set the working directory
ENV APP_HOME /app
WORKDIR $APP_HOME

# Copy all files to the container
COPY . ./

# Install dependencies
# Ensure your requirements.txt is in the same folder!
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir gunicorn

# Run the application with Gunicorn
# timeout 0 allows Cloud Run to handle timeouts
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app
