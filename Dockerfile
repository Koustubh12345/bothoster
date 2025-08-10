FROM python:3.11-slim


Set working directory inside the container

WORKDIR /app


Install system dependencies needed for some Python packages

RUN apt-get update && apt-get install -y \
    build-essential \
    libffi-dev \
    libssl-dev \
    git \
    && rm -rf /var/lib/apt/lists/*


Copy requirements file and install Python dependencies

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


Copy the rest of the application code

COPY . .


Create data directories with appropriate permissions for the bot to write to

RUN mkdir -p /app/data/bots /app/data/mirror /app/data/logs /app/data/templates && chmod -R 755 /app/data


Set environment variables

ENV PYTHONUNBUFFERED=1


Expose the port the web server will run on (required by Render)

EXPOSE 10000


The command to run when the container starts

CMD ["python", "app.py"]
