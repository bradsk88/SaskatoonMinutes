# Saskatoon Council Meeting Summarizer

A web app that ingests Saskatoon City Council meeting data from the
[eSCRIBE portal](https://pub-saskatoon.escribemeetings.com/), provides
plain-language summaries of agenda items, and links directly to video
timestamps so you can watch the discussion on any topic.

## Features

- **Browse meetings** - Paginated list of past City Council meetings
- **Agenda items with timestamps** - See every agenda item with a clickable
  timestamp that jumps to that point in the meeting video
- **Local summarization** - Generate plain-language summaries using local
  extractive summarization (no dependencies) or a local AI model
- **Embedded video** - Watch the meeting video directly in the app
- **Mobile-friendly** - Responsive design that works on all devices
- **No cloud dependencies** - Everything runs locally

## Quick Start

```bash
# Clone and enter the repo
git clone https://github.com/bradsk88/SaskatoonMinutes.git
cd SaskatoonMinutes

# Create a virtual environment and install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run the app (works out of the box with local extractive summaries)
python run.py
```

Then open http://localhost:5000 in your browser.

## Summarization Backends

Set `SUMMARIZER_BACKEND` in your `.env` file (or as an environment variable):

| Backend | Value | Dependencies | Description |
|---|---|---|---|
| **Extractive** (default) | `extractive` | None | Zero-dependency sentence scoring. Picks the most relevant sentences from each agenda item. Fast, runs anywhere. |
| **Local AI model** | `local` | `transformers`, `torch` | Abstractive summarization using a local Hugging Face model (distilbart by default). Better quality, but needs ~1.5GB of model downloads on first run. |

### Using the local AI model

```bash
pip install transformers torch
echo 'SUMMARIZER_BACKEND=local' >> .env
python run.py
```

You can change the model with `SUMMARIZER_MODEL` (default: `sshleifer/distilbart-cnn-12-6`).

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `SUMMARIZER_BACKEND` | No | `extractive` | Summarization engine: `extractive` or `local` |
| `SUMMARIZER_MODEL` | No | `sshleifer/distilbart-cnn-12-6` | Hugging Face model for the `local` backend |
| `FLASK_SECRET_KEY` | No | `dev-key-change-me` | Secret key for Flask sessions |

## How It Works

1. **Scraper** (`app/scraper.py`) - Fetches meeting data from the eSCRIBE
   platform's internal API endpoints. Extracts agenda items and video
   bookmark timestamps from the meeting pages.

2. **Summarizer** (`app/summarizer.py`) - Two backends:
   - *Extractive* — scores sentences by word frequency, position, and title
     overlap, then selects the top sentences. No external dependencies.
   - *Local* — runs a Hugging Face summarization pipeline locally.

3. **Web app** (`app/main.py`) - Flask application serving the UI and
   API endpoints. The frontend fetches data asynchronously and shows which
   summarization engine is active.

## Data Source

All meeting data comes from the City of Saskatoon's public eSCRIBE portal
at https://pub-saskatoon.escribemeetings.com/. The app uses the same
internal AJAX endpoints that the eSCRIBE website uses.

## License

MIT
