import os
import argparse

from app.web import run as run_server


def main():
    # Entry point for uv run main.py
    parser = argparse.ArgumentParser(description="ChipJar server")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    parser.add_argument("--reload", action="store_true", default=os.environ.get("RELOAD", "0").lower() in ("1", "true", "yes"), help="Enable auto-reload on code changes (development only)")
    args = parser.parse_args()

    run_server(host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
