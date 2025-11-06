# app.py — uvicorn app:app ile uyumluluk shimi
from proje import app  # proje.py içindeki FastAPI örneğini getir
__all__ = ["app"]