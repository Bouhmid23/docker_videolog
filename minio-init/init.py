import json
import os

from minio import Minio
from minio.error import S3Error


client = Minio(
    "minio:9000",
    access_key=os.environ["MINIO_ROOT_USER"],
    secret_key=os.environ["MINIO_ROOT_PASSWORD"],
    secure=False,
)

for bucket in ("recordings", "segments"):
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)

    try:
        client.delete_bucket_policy(bucket)
    except S3Error as error:
        if error.code != "NoSuchBucketPolicy":
            raise

print(json.dumps({"status": "ready", "buckets": ["recordings", "segments"]}))
