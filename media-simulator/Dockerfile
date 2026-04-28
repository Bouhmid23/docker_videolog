FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    livekit==0.11.1 \
    livekit-api==0.6.0 \
    minio==7.2.0 \
    python-dotenv==1.0.0

COPY simulator.py .

CMD ["python", "-u", "simulator.py"]
