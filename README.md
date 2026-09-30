# Jahvi

## Start the backend and workers

Install `backend/requirements.txt`, then configure `DATABASE_URL`, `JWT_SECRET`,
`UPLOADTHING_TOKEN`, and one private shared worker secret in the environment.
The same worker secret must be present in the entry backend and both workers.
Never expose it to the frontend. For local development, configure:

```text
JAHVI_WORKER_SHARED_SECRET=<long-random-secret>
JAHVI_WORKER_1_URL=http://127.0.0.1:8101
JAHVI_WORKER_2_URL=http://127.0.0.1:8102
JAHVI_WORKER_1_PORT=8101
JAHVI_WORKER_2_PORT=8102
JAHVI_WORKER_1_CAPACITY=1
JAHVI_WORKER_2_CAPACITY=1
JAHVI_WORKER_CAPACITY=1
```

Start each command from the repository root in its own terminal. The entry API
is the only process the frontend should call:

```bash
python -m uvicorn main:app --app-dir backend --host 0.0.0.0 --port 8000
```

```bash
JAHVI_WORKER_ID=worker-1 JAHVI_WORKER_CAPACITY=1 python -m uvicorn main:app --app-dir backend/worker1 --host 0.0.0.0 --port "$JAHVI_WORKER_1_PORT"
```

```bash
JAHVI_WORKER_ID=worker-2 JAHVI_WORKER_CAPACITY=1 python -m uvicorn main:app --app-dir backend/worker2 --host 0.0.0.0 --port "$JAHVI_WORKER_2_PORT"
```

Each worker folder contains its own copy of the worker app, processing
modules, and runtime dependencies; neither imports code from the other worker
or the entry API. Install that folder's `requirements.txt` on each worker.
Configure the same `DATABASE_URL`, `UPLOADTHING_TOKEN`, and
`JAHVI_WORKER_SHARED_SECRET` on the entry and both workers. Keep worker ports
private; HMAC authenticates requests but does not encrypt them. Use private
networking or TLS between services in deployment.

## Install FFmpeg

The extraction backend uses both `ffmpeg` and `ffprobe` to cut, concatenate,
and export videos. On Ubuntu or the dev container, run:

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg
ffmpeg -version
ffprobe -version
```

Then install the Python dependencies and start the three backend processes above:

```bash
python -m pip install -r backend/requirements.txt
```

The Headshot Lab uses `patchless.py` by default. Selecting `Exact gap` in the
UI uses `patcher.py` instead. Both modules perform the FFmpeg editing steps.

## Start the frontend

In a second terminal:

```bash
python -m http.server 5000 --directory frontend
```

Open `http://localhost:5000`. The frontend signup page sends requests to the backend at port `8000`.