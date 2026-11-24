"""Tiny R2 helper for the cinekit workflows: get <key> <file> | put <file> <key>. Keys must stay under s2work/cinekit/."""
import os, sys
import boto3

BUCKET = "eduorbex-media"
S3 = boto3.client("s3", endpoint_url=os.environ["CF_R2_ENDPOINT"], aws_access_key_id=os.environ["CF_R2_ACCESS_KEY_ID"],
                  aws_secret_access_key=os.environ["CF_R2_SECRET_ACCESS_KEY"], region_name="auto")


def check(key):
    if not key.startswith("s2work/cinekit/") or ".." in key:
        sys.exit(f"refused key outside s2work/cinekit/: {key}")


if __name__ == "__main__":
    op = sys.argv[1]
    if op == "get":
        check(sys.argv[2]); S3.download_file(BUCKET, sys.argv[2], sys.argv[3])
    elif op == "put":
        check(sys.argv[3])
        ct = "video/mp4" if sys.argv[2].endswith(".mp4") else "application/octet-stream"
        S3.upload_file(sys.argv[2], BUCKET, sys.argv[3], ExtraArgs={"ContentType": ct})
        print("uploaded", sys.argv[3], os.path.getsize(sys.argv[2]), "bytes")
    else:
        sys.exit("usage: r2io.py get <key> <file> | put <file> <key>")
