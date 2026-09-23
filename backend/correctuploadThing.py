import base64
import json
import os
import requests

TOKEN = "eyJhcGlLZXkiOiJza19saXZlX2VkNmExYzBhNjFkZmE3MjRhNWFhMzBhMThkYzMzY2I1YmVkODcyNjNmNGM1ZGI0ZDIyN2MxY2Y3NmZkYTg3ZDIiLCJhcHBJZCI6ImRvMG0za2dqdGMiLCJyZWdpb25zIjpbInNlYTEiXX0="

data = json.loads(base64.b64decode(TOKEN))
API_KEY = data["apiKey"]

file_name = "rec1.mp4"
file_size = os.path.getsize(file_name)

# 1. Get signed URL
r = requests.post(
    "https://api.uploadthing.com/v7/prepareUpload",
    headers={
        "x-uploadthing-api-key": API_KEY,
        "Content-Type": "application/json"
    },
    json={
        "fileName": file_name,
        "fileSize": file_size,
        "fileType": "video/mp4"
    }
)

print("Prepare:", r.status_code)

if r.status_code != 200:
    print(r.text)
    exit()

upload = r.json()

# 2. Upload using multipart/form-data
with open(file_name, "rb") as f:
    r = requests.put(
        upload["url"],
        files={
            "file": (file_name, f, "video/mp4")
        }
    )

print("Upload:", r.status_code)
print(r.text)
print("File key:", upload["key"])