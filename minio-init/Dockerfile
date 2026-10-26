FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir minio==7.2.0

COPY init.py .

CMD ["python", "-u", "init.py"]
