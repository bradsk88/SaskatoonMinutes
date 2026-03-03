# Saskatoon Council Meeting Summarizer

A web app that ingests Saskatoon City Council meeting data from the
[eSCRIBE portal](https://pub-saskatoon.escribemeetings.com/), provides
AI-generated plain-language summaries of agenda items, and links directly
to video timestamps so you can watch the discussion on any topic.

## Features

- **Browse meetings** - Paginated list of past City Council meetings
- **Agenda items with timestamps** - See every agenda item with a clickable
  timestamp that jumps to that point in the meeting video
- **AI summaries** - Generate plain-language summaries of agenda items
  using Claude (requires an Anthropic API key)
- **Embedded video** - Watch the meeting video directly in the app
- **Mobile-friendly** - Responsive design that works on all devices

## Quick Start

```bash
# Clone and enter the repo
git clone https://github.com/bradsk88/SaskatoonMinutes.git
cd SaskatoonMinutes

# Create a virtual environment and install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# (Optional) Set up your API key for AI summaries
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY

# Run the app
python run.py
```

Then open http://localhost:5000 in your browser.

## Configuration

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | No | Anthropic API key for AI summaries. Without it, the app still works but the "Generate AI Summaries" button won't produce results. |
| `FLASK_SECRET_KEY` | No | Secret key for Flask sessions. Defaults to a dev key. |

## How It Works

1. **Scraper** (`app/scraper.py`) - Fetches meeting data from the eSCRIBE
   platform's internal API endpoints. Extracts agenda items and video
   bookmark timestamps from the meeting pages.

2. **Summarizer** (`app/summarizer.py`) - Sends agenda item titles and
   content to the Claude API to generate concise, citizen-friendly summaries.

3. **Web app** (`app/main.py`) - Flask application serving the UI and
   API endpoints. The frontend fetches data asynchronously for a snappy
   experience.

## Data Source

All meeting data comes from the City of Saskatoon's public eSCRIBE portal
at https://pub-saskatoon.escribemeetings.com/. The app uses the same
internal AJAX endpoints that the eSCRIBE website uses.

## License

MIT
