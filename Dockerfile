FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create directories for data and bot files with proper permissions
RUN mkdir -p /app/data /app/data/bots && chmod 755 /app/data /app/data/bots

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Expose port for web server (required by Render)
EXPOSE 10000

# Command to run the application
CMD ["python", "app.py"]