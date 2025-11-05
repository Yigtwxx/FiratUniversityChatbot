# FiratUniversityChatbot
Fırat University Assistant: An offline Turkish question-answering and document search system built on local PDFs using FastAPI, pdfplumber, and BM25.
Here’s a polished, drop-in **README.md** for your project—fully in English, professional, and tailored to your FastAPI + pdfplumber + BM25 app and Hugging Face Spaces setup.

---

# Fırat University Assistant — Offline PDF QA & Search

**Description:** **Fırat University Assistant:** Offline Turkish search and question-answering from local PDFs (FastAPI + pdfplumber, BM25).

<p align="left">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-0.111%2B-009688?logo=fastapi&logoColor=white">
  <img alt="License" src="https://img.shields.io/badge/License-MIT-blue">
  <img alt="Status" src="https://img.shields.io/badge/Status-Active-brightgreen">
</p>

A **local, offline** Turkish **document search & Q&A** assistant built for Fırat University resources.
The app reads **only local PDFs** in `docs/`, extracts text with **pdfplumber**, builds a lightweight **BM25** index with Turkish-aware normalization, and answers users via a minimal **FastAPI** web UI.

---

## ✨ Key Features

* **Offline by design:** No internet calls; answers come strictly from local PDFs in `docs/`.
* **Robust PDF parsing:** Single/dual column detection, header/footer removal, word-box line assembly, hyphen fixups.
* **Turkish text pipeline:** ASCII normalization, light stemming, tokenization, bigram matching.
* **Query expansion & intent:** Synonym (SYN) expansion, fuzzy matching, and intent flags (e.g., *pass grade*, *appeal*).
* **BM25 ranking with signals:** Weighted title/keywords/body, bigram bonuses, and prefix matches for short queries.
* **Answer safety:** If there’s no solid match or intent mismatch, the system refuses to hallucinate a reply.
* **Fast UI:** Clean chat interface (no framework required), mobile-friendly, keyboard shortcuts, chip shortcuts.

---

## 🧠 How It Works (Pipeline)

1. **Ingestion:** PDFs from `DOCS_DIR` (default `./docs`) are parsed with fallback strategies:

   * single column → dual column crop → word-box line assembly (header/footer filtered by position).
2. **Block building:** The app extracts Q/A blocks, headings+paragraphs, or sentence windows for indexing.
3. **Indexing:** Turkish-aware tokens + bigrams are fed to a custom BM25 index (title/keywords/body + bigram bonuses).
4. **Query understanding:** TR ASCII normalization, synonym expansion, fuzzy terms, and simple intent detection.
5. **Retrieval:** Top-K candidates are scored; strict safety checks avoid wrong/irrelevant answers.
6. **Answering:** A focused snippet is returned + source (file name and page), rendered in the web UI.

---

## 📁 Repository Structure

```
.
├─ app.py                     # FastAPI app + PDF parsing + BM25 + API
├─ requirements.txt           # Python dependencies
├─ .env.example               # Environment variables (see below)
├─ docs/                      # Your local PDF corpus (input only)
├─ templates/
│   └─ chat.html              # Chat UI (minimal, mobile-stable)
├─ static/
│   └─ firat-logo.png         # App/brand icon
└─ README.md                  # You are here
```

> **Note:** `templates/chat.html` provides the UI. `static/` is optional but recommended for logos and assets.

---

## ⚙️ Requirements

* **Python** 3.10+
* See `requirements.txt`:

  ```
  fastapi>=0.111
  uvicorn>=0.30
  pdfplumber>=0.11
  jinja2>=3.1
  aiofiles>=23.2
  ```

  *(If you keep a `.env` loader, add it to requirements as well.)*

---

## 🔧 Configuration (Environment)

Create a `.env` file (or set environment variables in your platform):

```dotenv
# Where PDFs live
DOCS_DIR=docs

# Index cache TTL (seconds)
CACHE_TTL=300

# App title
APP_TITLE="Firat Universitesi Asistani"

# Debug logs (0/1)
DEBUG=0

# Local port (overridden by hosting platform if needed)
PORT=7860
```

---

## 🚀 Run Locally

```bash
# 1) Create and activate a venv (recommended)
python -m venv .venv
# Windows
. .venv/Scripts/activate
# macOS/Linux
source .venv/bin/activate

# 2) Install deps
pip install -r requirements.txt

# 3) Make sure your PDFs are under ./docs/
mkdir -p docs

# 4) Start the server
uvicorn app:app --host 0.0.0.0 --port 7860
# or: python -m uvicorn app:app --reload
```

Open: `http://localhost:7860/chat`

---

## 🌐 Deploy on Hugging Face Spaces

**Recommended Space Type:** *Docker* or *Python (FastAPI)*.
Set the following **Variables** under *Settings → Variables*:

* `DOCS_DIR=docs`
* `CACHE_TTL=300`
* `APP_TITLE=Firat Universitesi Asistani`
* `DEBUG=0`

> Upload your PDFs into the repository’s `docs/` directory.
> The platform may override `PORT`; the app respects the injected port.

---

## 🛰️ API Endpoints

### `POST /ask`

**Body:**

```json
{ "question": "geçme notu nasıl hesaplanır?" }
```

**Response:**

```json
{
  "answer": "…focused Turkish snippet…",
  "sources": ["file.pdf s:12"],
  "error": null
}
```

**Notes:**

* If no reliable match is found (or intent is incompatible), you’ll get a polite fallback message, **never** a hallucination.

### `POST /reindex`

Forces a full re-scan and re-index of `DOCS_DIR`.

```bash
curl -X POST https://<host>/reindex
```

### `GET /health`

Returns counts and basic status:

```json
{ "status":"ok", "pdf_count":42, "docs_dir":"docs", "indexed":1234 }
```

---

## 💡 Query Tips (Turkish)

* Try short **keywords**: “geçme notu”, “devamsızlık”, “itiraz”, “danışman”, “program”, “transkript”.
* Short aliases supported: **but** → bütünleme, **trans** → transkript, **obs** → öğrenci bilgi sistemi.
* For pass-grade style questions, the system looks for **final/vize/%/numbers** as signals.

---

## 🧩 Implementation Highlights

* **Text normalization:** TR ASCII lowering, bullet & soft hyphen cleanup, smart line merges.
* **Tokenizer & stemmer:** Minimal & conservative to avoid over-stemming Turkish tokens.
* **Synonyms (SYN):** Domain-specific expansions (e.g., *büt/bütünleme*, *trans/transkript*).
* **Intent detection:** Lightweight flags (`pass_grade`, `appeal`) to bias ranking and filter.
* **Ranking:** BM25 on **title**, **keywords**, **body** with field weights, bigram boosts, and prefix candidates.
* **Safety:** Refuses answers when overlap is weak or intent doesn’t match (no “best guess” fabrication).

---

## 🧪 Quick Test (cURL)

```bash
curl -s -X POST http://localhost:7860/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"bütünleme sınavı var mı?"}' | jq
```

---

## ❗ Troubleshooting

* **No answers returned**

  * Ensure PDFs exist under `DOCS_DIR` (default `./docs`).
  * Try `POST /reindex` after modifying PDF files.
  * Increase `CACHE_TTL` only if you need longer cache; otherwise keep it 300s.

* **Poor extraction on complex PDFs**

  * The app tries single column → dual column → word-box assembly. Some scans may still be noisy.
  * Consider pre-processing PDFs (OCR, deskew, higher DPI) if needed.

* **High latency on first query**

  * Index builds on startup and refresh; subsequent queries are cached and fast.

---

## 🔐 Data & Safety Notes

* The assistant **never** reaches the internet and **never** answers outside your **local PDFs**.
* No personal data is stored beyond logs (if `DEBUG=1`).
* For publishing, ensure your PDFs are suitable for public release.

---

## 🗺️ Roadmap

* Optional FAISS layer for hybrid keyword-vector retrieval.
* Per-document filters (faculty, year, regulation).
* PDF change watcher (auto reindex).
* Admin UI for monitoring sources and coverage.

---

## 📜 License

Released under the **MIT License**. See `LICENSE` for details.

---

## 🙌 Acknowledgements

* **pdfplumber** for PDF parsing
* **FastAPI** for the HTTP/API layer
* Inspired by classic **BM25** ranking with domain-aware tweaks

---

> *“Offline RAG-style Q&A for university regulations—fast, safe, and local.”*
