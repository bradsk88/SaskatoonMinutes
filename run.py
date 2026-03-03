#!/usr/bin/env python3
"""Entry point for the Saskatoon Council Meeting Summarizer."""

from app.main import app

if __name__ == "__main__":
    app.run(debug=True, port=5000)
