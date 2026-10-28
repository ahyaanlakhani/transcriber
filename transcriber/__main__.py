"""`python -m transcriber` — start the local server and open the browser."""
import argparse
import logging
import threading
import webbrowser

import uvicorn

from .app import create_app
from .config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Meeting Transcriber")
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser window")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config()
    url = f"http://127.0.0.1:{config.port}"
    print(f"Meeting Transcriber is running at {url}  (press Ctrl+C to stop)")
    if not config.groq_api_key:
        print("No GROQ_API_KEY in .env yet - recordings will wait in the queue until you add it and restart.")
    if not args.no_browser:
        threading.Timer(1.5, webbrowser.open, args=[url]).start()
    uvicorn.run(create_app(config), host="127.0.0.1", port=config.port, log_level="warning")


if __name__ == "__main__":
    main()
