FROM python:3.11-slim-buster

WORKDIR /app

# Install ffmpeg and other dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    build-essential \
    git \
    libssl-dev \
    libffi-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .
COPY Procfile .

ENV BOT_TOKEN="your_bot_token_here" # Replace with your actual bot token or set via environment variable

CMD ["python", "bot.py"]
