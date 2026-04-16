FROM python:3.11-slim
WORKDIR /app
RUN mkdir -p /data/shards
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Node A=5002, Node B=5003, Node C=5004 (set via PORT env var at runtime)
EXPOSE 5002 5003 5004
CMD ["python", "app.py"]
