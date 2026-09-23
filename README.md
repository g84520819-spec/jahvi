# Jahvi

## Start the backend

From the repository root, with `DATABASE_URL` and `JWT_SECRET` available in the environment:

```bash
python -m uvicorn main:app --app-dir backend --host 0.0.0.0 --port 8000
```

The API is available at `http://localhost:8000`.

## Install FFmpeg

The extraction backend uses both `ffmpeg` and `ffprobe` to cut, concatenate,
and export videos. On Ubuntu or the dev container, run:

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg
ffmpeg -version
ffprobe -version
```

Then install the Python dependencies and start the backend:

```bash
python -m pip install -r backend/requirements.txt
python -m uvicorn main:app --app-dir backend --host 0.0.0.0 --port 8000
```

The Headshot Lab uses `patchless.py` by default. Selecting `Exact gap` in the
UI uses `patcher.py` instead. Both modules perform the FFmpeg editing steps.

## Start the frontend

In a second terminal:

```bash
python -m http.server 5000 --directory frontend
```

Open `http://localhost:5000`. The frontend signup page sends requests to the backend at port `8000`.