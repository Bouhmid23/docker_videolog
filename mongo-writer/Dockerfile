FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    motor==3.3.2 \
    redis==5.0.1 \
    pymongo==4.6.0 \
    python-dotenv==1.0.0

COPY writer.py .

CMD ["python", "-u", "writer.py"]
