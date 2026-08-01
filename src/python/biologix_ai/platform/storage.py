import hashlib
import os
from dataclasses import dataclass

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError


@dataclass(frozen=True)
class StoredObject:
    key: str
    size: int
    sha256: str


class ArtifactStorage:
    def __init__(self):
        self.bucket = os.getenv("S3_BUCKET", "biologix-artifacts")
        self.client = boto3.client(
            "s3",
            endpoint_url=os.getenv("S3_ENDPOINT_URL") or None,
            region_name=os.getenv("AWS_REGION", "us-east-1"),
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID") or None,
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY") or None,
            config=Config(signature_version="s3v4"),
        )

    def ensure_bucket(self):
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError:
            self.client.create_bucket(Bucket=self.bucket)

    def put(self, key: str, content: bytes, content_type: str) -> StoredObject:
        self.ensure_bucket()
        digest = hashlib.sha256(content).hexdigest()
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=content,
            ContentType=content_type,
            Metadata={"sha256": digest},
        )
        return StoredObject(key=key, size=len(content), sha256=digest)

    def get(self, key: str):
        return self.client.get_object(Bucket=self.bucket, Key=key)
