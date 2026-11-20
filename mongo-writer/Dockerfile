FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir motor==3.6.0 redis==5.2.0
COPY writer.py .
CMD ["python", "-u", "writer.py"]
