FROM python:3.11-slim

WORKDIR /app

# Dependencies first so the layer caches across code edits.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# DRY_RUN defaults on in config.py; compose can override it.
CMD ["python", "main.py", "--agent", "technical-example"]
