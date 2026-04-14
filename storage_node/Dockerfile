FROM python:3.11-slim
WORKDIR /app
RUN mkdir -p /data/shards
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5002
# Use gunicorn with 2 workers for production (Flask dev server is single-threaded)
CMD ["gunicorn", "--bind", "0.0.0.0:5002", "--workers", "2", "--timeout", "60", "app:app"]
