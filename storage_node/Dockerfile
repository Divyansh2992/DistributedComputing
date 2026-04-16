FROM python:3.11-slim
WORKDIR /app
RUN mkdir -p /data/shards
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5002 5003 5004
# Use gunicorn with 2 workers for production (Flask dev server is single-threaded)
# The PORT environment variable is set in docker-compose for each node instance
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-5002} --workers 2 --timeout 60 app:app"]
