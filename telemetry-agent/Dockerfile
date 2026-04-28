FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    livekit==0.11.1 \
    livekit-api==0.6.0 \
    redis==5.0.1 \
    asyncio-mqtt==0.16.2 \
    python-dotenv==1.0.0

COPY agent.py .

CMD ["python", "-u", "agent.py"]
