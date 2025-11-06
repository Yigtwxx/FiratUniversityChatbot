# app.py — Firat U. 1. Sinif Asistani (PDF-Only) — Saglam Surum "V6+"
# -----------------------------------------------------------------------------
# - Yalnizca yerel PDF (Internet yok)
# - pdfplumber ile akilli metin cikarma:
#     1) tek sutun,
#     2) iki sutun crop,
#     3) kelime kutularindan satir kur (header/footer ayikla)
# - TR ASCII normalizasyonu + hafif kokleme + tokenizasyon + bigram
# - Esanlam genisletme (SYN) + NIYET algilama (pass_grade / appeal)
# - BM25 (baslik/anahtar/govde agirlik, bigram bonus) + niyete gore boost/penalty
# - Fuzzy arama (difflib) + kisa tek-kelime prefix eslesmesi
# - Guvenli cevaplama: "gercek hit yoksa" veya niyet uyumsuz ise uydurma yok
# - /chat basit UI, /reindex, /health, TTL onbellek
# -----------------------------------------------------------------------------

from __future__ import annotations

import math
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import difflib

import pdfplumber  # pip install pdfplumber
from fastapi import Body, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ===================== Ayarlar =====================
DOCS_DIR: Path = Path(os.getenv("DOCS_DIR", "docs"))
CACHE_TTL: int = int(os.getenv("CACHE_TTL", "300"))
APP_TITLE: str = os.getenv("APP_TITLE", "Firat U. 1. Sinif Asistani — PDF Only")
DEBUG: bool = os.getenv("DEBUG", "0") == "1"

# Kayit/arama ayarlari
MIN_BODY_LEN = 25
WINDOW_SENT = 2
MAX_KWS_PER_REC = 12
TOP_K_RETURN = 5
SNIPPET_CHARS = 480
ASCII_KEEP = r"[^\w\s%/\.\-\(\),:]"

# BM25 agirliklari
W_TITLE, W_KWS, W_BODY, W_BIGR = 1.35, 1.20, 1.00, 1.15

# ===================== On-derlenen Regex'ler =====================
TOKEN_RE = re.compile(r"[a-z0-9%]+")
SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
HEADING_MADDE_RE = re.compile(r"^Madde\s+\d+", flags=re.I)
SORU_BLOK_RE = re.compile(
    r"(Soru\s*[:\-]\s*)(.+?)(?:Cevap\s*[:\-]\s*)(.+?)(?:Anahtar(?:\s*Kelimeler)?\s*[:\-]\s*(.+?))?(?:(?:---)|$)",
    flags=re.I | re.S,
)

# ===================== Yardimcilar =====================
def tr_ascii_lower(s: str) -> str:
    """TR karakterleri ASCII'ye indirger, kucultur ve sade lestirir."""
    if not s:
        return ""
    table = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    s = s.translate(table).lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(ASCII_KEEP, " ", s)
    return re.sub(r"\s+", " ", s).strip()


TR_SUFFIXES = (
    "lari", "leri", "lar", "ler", "nin", "nin", "nın", "nun", "nün",
    "si", "sı", "su", "sü", "i", "ı", "u", "ü"
)

def stem_tr(tok: str) -> str:
    """Basit ve zarar vermeyen ek kesici (yanlis koklemeyi en aza indir)."""
    for suf in TR_SUFFIXES:
        if tok.endswith(suf) and len(tok) > len(suf) + 1:
            return tok[: -len(suf)]
    return tok

def tokenize(s: str) -> List[str]:
    base = TOKEN_RE.findall(tr_ascii_lower(s))
    return [stem_tr(t) for t in base]

def bigrams(tokens: List[str]) -> List[str]:
    return [f"{tokens[i]} {tokens[i+1]}" for i in range(len(tokens) - 1)]

def normalize_pdf_text(text: str) -> str:
    if not text:
        return ""
    # yumusak tire / bullet / gizli bosluk
    text = text.replace("\u00ad", "").replace("\uf0b7", "•").replace("\u200b", "")
    # satir sonu tire birlestirme
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    # satir sonlarini bosluk ile birlestir
    text = text.replace("\r", "\n").replace("\n", " ")
    return re.sub(r"\s{2,}", " ", text).strip()

def is_heading(line: str) -> bool:
    raw = (line or "").strip()
    if not raw:
        return False
    if raw.isupper() and len(raw) >= 6:
        return True
    if raw.endswith(":"):
        return True
    if HEADING_MADDE_RE.match(raw):
        return True
    return False

# ===================== Esanlam (SYN) + Niyet =====================
# (Yanlis yazimlar duzeltildi; but/butunleme/butunleme sinavi, büt, trans/transkript)
SYN: Dict[str, List[str]] = {
    "gecme notu": ["basari notu", "gecme baraji", "not hesabi", "dersi gecme", "ortalama", "gecer not", "baraj"],
    "gecer not": ["gecme notu", "baraj", "basari notu"],
    "devamsizlik": ["devam", "yoklama", "devamsizlik hakki", "devam durumu"],
    "vize": ["ara sinav", "yariyil ici", "orta sinav"],
    "final": ["genel sinav", "donem sonu", "bitirme sinavi"],
    "butunleme": ["butunleme sinavi", "telafi sinavi", "but", "butun", "butunl", "butunleme", "butunle", "but", "butu", "butun", "butunleme exam"],
    "büt": ["but", "butunleme", "butunleme sinavi"],
    "not": ["gecme notu", "not ortalamasi", "not hesabi", "puan", "basari notu"],
    "kayit": ["kayit yenileme", "yeniden kayit", "ders kaydi", "harc odeme"],
    "danisman": ["akademik danisman", "danisman hoca", "danismanlik"],
    "itiraz": ["not itiraz", "puan itiraz", "dilekce", "sonuca itiraz"],
    "ders programi": ["program", "takvim", "ders saati"],
    "program": ["ders programi", "ders saati", "takvim"],
    "transkript": ["not belgesi", "ogrenci transkript", "trans", "transkript belgesi"],
    "obs": ["ogrenci otomasyon", "ogrenci bilgi sistemi", "otomasyon", "obs giris"],
}

# sik kullanilan kisaltmalar icin dogrudan map (kisa tek kelime sorgular)
ALIAS_SHORT: Dict[str, str] = {
    "but": "butunleme",
    "büt": "butunleme",
    "butun": "butunleme",
    "trans": "transkript",
    "transkriptim": "transkript",
    "obs": "obs",
}

def detect_intents(q: str) -> Dict[str, bool]:
    """Sorgudan kaba niyet bayraklari cikarir."""
    qn = tr_ascii_lower(q)
    toks = set(tokenize(qn))
    pass_terms = {"gecme", "gecer", "baraj", "gecme notu", "gecer not", "not ortalamasi", "not", "ortalama"}
    appeal_terms = {"itiraz", "dilekce", "sonuca"}
    return {
        "pass_grade": bool(toks & {"gecme", "gecer", "baraj", "not", "ortalama"}) or any(p in qn for p in pass_terms),
        "appeal": ("itiraz" in toks) or any(a in qn for a in appeal_terms),
    }

def expand_with_syn(q: str) -> List[str]:
    """Once sozluk esanlamlari, sonra niyete ozel alan-terimleri ekler."""
    qn = tr_ascii_lower(q)
    extra: List[str] = []

    # 1) sozluk temelli genisletme
    for base, alts in SYN.items():
        if base in qn:
            for a in alts:
                extra.extend(tokenize(a))

    toks = tokenize(q)
    if len(toks) == 1:
        key = toks[0]
        # ALIAS_SHORT destekle
        if key in ALIAS_SHORT:
            extra.extend(tokenize(ALIAS_SHORT[key]))
        for base, alts in SYN.items():
            if key == base or key in base.split():
                for a in alts:
                    extra.extend(tokenize(a))

    # 2) niyet temelli terimler
    intents = detect_intents(q)
    if intents.get("pass_grade"):
        extra += tokenize("final vize yuzde % oran 50 puan baraj ortalama gecme gecer")
    return extra

# ---------- Fuzzy yardimcilar ----------
def fuzzy_expand_terms(q_tokens: List[str], vocab: List[str], cutoff: float = 0.82, topn: int = 3) -> List[str]:
    """
    difflib.get_close_matches ile sorgu terimleri icin benzer sozluk terimleri ekler.
    cutoff ~ [0..1]; 0.82 pratik bir esiktir.
    """
    out: List[str] = []
    for qt in q_tokens:
        # oncelikle ALIAS_SHORT
        if qt in ALIAS_SHORT:
            out.extend(tokenize(ALIAS_SHORT[qt]))
        # difflib ile yakin eslesmeler
        close = difflib.get_close_matches(qt, vocab, n=topn, cutoff=cutoff)
        out.extend(close)
    return out

# ===================== PDF -> Kayit Cikarimi =====================
# Kayit: {"q":str,"a":str,"page":int,"file":str,"kws":[str],"is_heading":bool}
def _assemble_lines_from_words(words, page_height: float, header_ratio=0.08, footer_ratio=0.08) -> str:
    """Kelime kutularindan satir kurar, header/footer'i konuma gore ayiklar."""
    top_cut = page_height * header_ratio
    bot_cut = page_height * (1 - footer_ratio)
    rows: Dict[float, List[Dict[str, Any]]] = {}
    for w in words:
        top = float(w.get("top", 0))
        bottom = float(w.get("bottom", 0))
        if top < top_cut or bottom > bot_cut:
            continue  # baslik / altbilgi / sayfa numarasi
        key = round(top / 2.0, 1)  # kaba satir gruplama
        rows.setdefault(key, []).append(w)

    lines: List[str] = []
    for _, ws in sorted(rows.items(), key=lambda x: x[0]):
        ws.sort(key=lambda w: float(w.get("x0", 0)))
        line = " ".join(w.get("text", "") for w in ws)
        line = re.sub(r"\s{2,}", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)

def _extract_text_single(page) -> str:
    """Tek sutun varsayar; toleranslari biraz yuksek tutar."""
    try:
        raw = page.extract_text(x_tolerance=2.0, y_tolerance=1.5, keep_blank_chars=False) or ""
    except Exception:
        raw = ""
    return normalize_pdf_text(raw)

def _extract_text_two_cols(page) -> str:
    """Iki sutun PDF’lerde soldan saga sirayla oku."""
    try:
        w, h = page.width, page.height
        gutter = w * 0.06  # sutun arasi bosluk
        left_box = (w * 0.06, h * 0.06, w / 2 - gutter, h - h * 0.06)
        right_box = (w / 2 + gutter, h * 0.06, w - w * 0.06, h - h * 0.06)

        def crop_read(bbox):
            with page.crop(bbox) as c:
                txt = c.extract_text(x_tolerance=2.2, y_tolerance=1.6, keep_blank_chars=False) or ""
                return normalize_pdf_text(txt)

        left = crop_read(left_box)
        right = crop_read(right_box)
        merged = "\n".join([t for t in [left, right] if t])
        return merged.strip()
    except Exception:
        return ""

def _page_text_with_fallback(page) -> str:
    """
    Sira:
      1) tek sutun,
      2) iki sutun,
      3) kelime kutularindan satir kur (header/footer ayikla).
    """
    txt = _extract_text_single(page)
    if len(txt) >= 60:
        return txt

    txt2 = _extract_text_two_cols(page)
    if len(txt2) >= 60:
        return txt2

    try:
        words = page.extract_words(use_text_flow=True, keep_blank_chars=False) or []
    except Exception:
        words = []
    if words:
        built = _assemble_lines_from_words(words, page_height=page.height)
        built = normalize_pdf_text(built)
        if len(built) >= 40:
            return built

    return txt or txt2 or ""

def extract_blocks_from_pdf(path: Path) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    with pdfplumber.open(str(path)) as pdf:
        for pno, page in enumerate(pdf.pages, start=1):
            txt = _page_text_with_fallback(page)
            if not txt:
                if DEBUG:
                    print(f"[WARN] bos sayfa atlandi: {path.name} s:{pno}")
                continue

            produced = 0

            # 1) Soru / Cevap / Anahtar
            for m in SORU_BLOK_RE.finditer(txt + " ---"):
                soru = (m.group(2) or "").strip()
                cevap = (m.group(3) or "").strip()
                if soru and cevap and len(cevap) >= MIN_BODY_LEN:
                    kline = (m.group(4) or "")
                    kws = [k.strip() for k in re.split(r"[,;/|]", kline) if k.strip()]
                    items.append(
                        {
                            "q": soru,
                            "a": cevap,
                            "page": pno,
                            "file": path.name,
                            "kws": kws[:MAX_KWS_PER_REC],
                            "is_heading": False,
                        }
                    )
                    produced += 1

            # 2) Baslik -> Paragraf
            if produced == 0:
                lines = [ln.strip() for ln in (page.extract_text() or "").splitlines()]
                chunks: List[str] = []
                buf: List[str] = []
                for ln in lines:
                    if is_heading(ln) and buf:
                        chunks.append("\n".join(buf))
                        buf = [ln]
                    else:
                        buf.append(ln)
                if buf:
                    chunks.append("\n".join(buf))
                for ch in chunks:
                    parts = [ln for ln in ch.splitlines() if ln.strip()]
                    if not parts:
                        continue
                    head = parts[0].strip()
                    body = normalize_pdf_text(" ".join(parts[1:]))
                    if is_heading(head) and len(body) >= MIN_BODY_LEN:
                        items.append(
                            {
                                "q": head.rstrip(":").strip(),
                                "a": body,
                                "page": pno,
                                "file": path.name,
                                "kws": [],
                                "is_heading": True,
                            }
                        )
                        produced += 1

            # 3) Cumle penceresi (mutlaka en az 1 kayit)
            if produced == 0:
                sentences = [s.strip() for s in SENT_SPLIT_RE.split(txt) if s.strip()]
                win = " ".join(sentences[:WINDOW_SENT]) if sentences else txt
                if len(win) < MIN_BODY_LEN:
                    win = txt
                head = (re.split(r"[.!?]", win)[0] or "Genel Hukum").strip()
                head = (head[:80] + "...") if len(head) > 80 else head
                items.append(
                    {
                        "q": head,
                        "a": normalize_pdf_text(win),
                        "page": pno,
                        "file": path.name,
                        "kws": [],
                        "is_heading": False,
                    }
                )

    # Otomatik anahtar kelime + alias takviyesi
    for it in items:
        # govdeden sik kelimeler
        if not it["kws"]:
            toks = [t for t in tokenize(it["a"]) if len(t) > 1]
            freq: Dict[str, int] = {}
            for t in toks:
                freq[t] = freq.get(t, 0) + 1
            auto = [k for k, _ in sorted(freq.items(), key=lambda x: x[1], reverse=True)[:MAX_KWS_PER_REC]]
            it["kws"] = auto
        # alias ekle
        extra_alias: List[str] = []
        flat_text = tr_ascii_lower(it["q"] + " " + it["a"])
        if "butunleme" in flat_text or "telafi" in flat_text:
            extra_alias += ["but", "butunleme", "butunleme sinavi", "butun"]
        if "transkript" in flat_text or "not belgesi" in flat_text:
            extra_alias += ["trans", "transkript", "not belgesi"]
        if "obs" in flat_text or "ogrenci otomasyon" in flat_text:
            extra_alias += ["obs", "ogrenci otomasyon", "ogrenci bilgi sistemi"]
        it["kws"] = list(dict.fromkeys((it["kws"] + [*map(tr_ascii_lower, extra_alias)])))[:MAX_KWS_PER_REC]
    return items

def load_all() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    for p in sorted(DOCS_DIR.rglob("*.pdf")):
        try:
            out.extend(extract_blocks_from_pdf(p))
        except Exception as e:
            print(f"[WARN] {p} okunamadi: {e}")
    return out

# ===================== BM25 =====================
class BM25Index:
    def __init__(self, items: List[Dict[str, Any]]):
        self.items = items

        for it in self.items:
            it["q_tokens"] = tokenize(it["q"])               # Baslik
            it["kw_tokens"] = tokenize(" ".join(it["kws"]))  # Anahtar
            it["a_tokens"] = tokenize(it["a"])               # Govde
            it["q_bi"] = bigrams(it["q_tokens"])
            it["kw_bi"] = bigrams(it["kw_tokens"])
            it["a_bi"] = bigrams(it["a_tokens"])

        self.N = len(self.items)
        self.avg_q = sum(len(it["q_tokens"]) for it in self.items) / max(1, self.N)
        self.avg_k = sum(len(it["kw_tokens"]) for it in self.items) / max(1, self.N)
        self.avg_a = sum(len(it["a_tokens"]) for it in self.items) / max(1, self.N)

        # DF tabloları
        self.df_q: Dict[str, int] = {}
        self.df_k: Dict[str, int] = {}
        self.df_a: Dict[str, int] = {}
        for it in self.items:
            for t in set(it["q_tokens"]):
                self.df_q[t] = self.df_q.get(t, 0) + 1
            for t in set(it["kw_tokens"]):
                self.df_k[t] = self.df_k.get(t, 0) + 1
            for t in set(it["a_tokens"]):
                self.df_a[t] = self.df_a.get(t, 0) + 1

        self.k1, self.b = 1.5, 0.75

        # Basit ters indeks (keyword fallback)
        self.inv: Dict[str, List[int]] = {}
        for idx, it in enumerate(self.items):
            total = set(it["q_tokens"]) | set(it["kw_tokens"]) | set(it["a_tokens"])
            for t in total:
                self.inv.setdefault(t, []).append(idx)

        # Fuzzy icin sozluk
        self.vocab: List[str] = sorted(set(self.inv.keys()))

    def _idf(self, term: str, field: str) -> float:
        df = {"q": self.df_q, "k": self.df_k, "a": self.df_a}[field].get(term, 0)
        N = self.N
        return math.log(1 + (N - df + 0.5) / (df + 0.5)) if N > 0 else 0.0

    def _bm25_field(self, q_terms: List[str], doc_terms: List[str], avgdl: float, tag: str) -> float:
        if not q_terms or not doc_terms:
            return 0.0
        dl = len(doc_terms)
        tf: Dict[str, int] = {}
        for t in doc_terms:
            tf[t] = tf.get(t, 0) + 1
        s = 0.0
        for t in q_terms:
            f = tf.get(t, 0)
            if f == 0:
                continue
            idf = self._idf(t, tag)
            s += idf * (f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * (dl / max(1.0, avgdl)))))
        return s

    def _score_doc(self, it: Dict[str, Any], q_terms: List[str], q_bi: List[str], intents: Dict[str, bool]) -> float:
        # Temel skorlar
        s = 0.0
        s += W_TITLE * self._bm25_field(q_terms, it["q_tokens"], self.avg_q, "q")
        s += W_KWS   * self._bm25_field(q_terms, it["kw_tokens"], self.avg_k, "k")
        s += W_BODY  * self._bm25_field(q_terms, it["a_tokens"], self.avg_a, "a")

        # Bigrama ekstra bonus
        if q_bi:
            hit = len(set(q_bi) & (set(it["q_bi"]) | set(it["kw_bi"]) | set(it["a_bi"])))
            if hit > 0:
                s *= (W_BIGR + 0.02 * min(3, hit))

        # Baslik/anahtar kesisimi kucuk bonuslar
        qset = set(q_terms)
        head_hit = len(qset & set(it["q_tokens"]))
        kw_hit   = len(qset & set(it["kw_tokens"]))
        if head_hit:
            s *= (1.05 + 0.02 * min(3, head_hit))
        if kw_hit:
            s *= (1.05 + 0.02 * min(3, kw_hit))

        # Sorgu ifadesi aynen geciyorsa bonus
        q_phrase = " ".join(q_terms)
        if q_phrase and (q_phrase in " ".join(it["q_tokens"]) or q_phrase in " ".join(it["a_tokens"])):
            s *= 1.15

        # ----- Niyet tabanli ayar -----
        if intents.get("pass_grade"):
            body = set(it["a_tokens"])
            if {"final", "vize"} & body:
                s *= 1.20
            if any(t.isdigit() for t in it["a_tokens"]):
                s *= 1.08
            if "%" in " ".join(it["a_tokens"]):
                s *= 1.06

        # Sorguda 'itiraz' yoksa itiraz agirlikli kaydi dusur
        if (not intents.get("appeal")) and (
            "itiraz" in it["a_tokens"] or "itiraz" in it["kw_tokens"] or "itiraz" in it["q_tokens"]
        ):
            s *= 0.55

        if it.get("is_heading"):
            s *= 1.05
        return s

    # ---- Kisa tek-kelime/prefix & fuzzy destekli arama ----
    def _prefix_candidates(self, q: str, limit: int = TOP_K_RETURN) -> List[Tuple[float, Dict[str, Any]]]:
        """Cok kisa (<=4 harf) tek kelime sorgular icin prefix ve alias temelli adaylar."""
        qtoks = tokenize(q)
        if len(qtoks) != 1:
            return []
        key = qtoks[0]
        # alias map
        mapped = ALIAS_SHORT.get(key, key)
        # prefix tarama
        cands: List[Tuple[float, Dict[str, Any]]] = []
        for idx, it in enumerate(self.items):
            pool = set(it["kw_tokens"]) | set(it["q_tokens"])
            hit = any(t.startswith(mapped[: max(2, len(mapped))]) for t in pool)
            if hit:
                base = 1.0 + (0.2 if it.get("is_heading") else 0.0)
                cands.append((base, it))
        cands.sort(key=lambda x: x[0], reverse=True)
        return cands[:limit]

    def search(self, query: str, top_k: int = TOP_K_RETURN) -> List[Tuple[float, Dict[str, Any]]]:
        base = tokenize(query)
        # fuzzy genisletme
        fuzzy_extra = fuzzy_expand_terms(base, self.vocab, cutoff=0.82, topn=3)
        extra = expand_with_syn(query) + fuzzy_extra
        q_terms = [t for t in (base + extra) if t] or base

        # kisa/prefix adaylari (ornegin "but", "trans", "obs")
        prefix_hits = self._prefix_candidates(query, limit=top_k)

        q_bi = bigrams(q_terms)
        intents = detect_intents(query)

        scored: List[Tuple[float, Dict[str, Any]]] = []
        for it in self.items:
            sc = self._score_doc(it, q_terms, q_bi, intents)
            if sc > 0:
                scored.append((sc, it))

        # prefix adaylarini hafif yukari cek
        boost_map = {id(it): 0.15 for _, it in prefix_hits}
        scored = [(s * (1.0 + boost_map.get(id(it), 0.0)), it) for (s, it) in scored]

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:top_k]

    def keyword_fallback(self, query: str, limit: int = TOP_K_RETURN) -> List[Tuple[float, Dict[str, Any]]]:
        qtok = set(tokenize(query))
        if not qtok:
            return []
        candidate_ids: Dict[int, float] = {}
        for t in qtok:
            for idx in self.inv.get(t, []):
                candidate_ids[idx] = candidate_ids.get(idx, 0.0) + 1.0

        # fuzzy fallback: benzer tokenlari da say
        for t in qtok:
            close = difflib.get_close_matches(t, self.vocab, n=5, cutoff=0.8)
            for ct in close:
                for idx in self.inv.get(ct, []):
                    candidate_ids[idx] = candidate_ids.get(idx, 0.0) + 0.6  # fuzzy katkisi

        cands: List[Tuple[float, Dict[str, Any]]] = []
        for idx, hit in candidate_ids.items():
            it = self.items[idx]
            bonus = 0.5 if it.get("is_heading") else 0.0
            cands.append((hit + bonus, it))
        cands.sort(key=lambda x: x[0], reverse=True)
        return cands[:limit]

# ===================== Snippet =====================
def strip_keyword_lines(ans: str) -> str:
    return re.sub(r"Anahtar\s*Kelimeler\s*[:\-].*", "", ans, flags=re.I).strip()

def best_snippet(text: str, query: str, max_chars: int = SNIPPET_CHARS) -> str:
    """
    Sorgu terimlerinin yogun oldugu pencereye yaslanarak snippet cikarir.
    (Pozisyon-temelli secim + uzunluk kesmesi)
    """
    clean = strip_keyword_lines(text)
    if len(clean) <= max_chars:
        return clean

    toks = tokenize(clean)
    qset = set(tokenize(query))
    if not toks:
        return clean[:max_chars]

    # Pencereyi, ilk en guclu kesisimin yakinina konumlandir
    win_size = max(40, min(120, len(toks) // 4))
    best_i, best_hit = 0, -1
    step = max(10, win_size // 3)
    for i in range(0, len(toks), step):
        win = toks[i: i + win_size]
        hit = len(set(win) & qset)
        if hit > best_hit:
            best_hit, best_i = hit, i

    snippet = " ".join(toks[best_i: best_i + win_size])
    return (snippet[:max_chars].rsplit(" ", 1)[0] + "...") if len(snippet) > max_chars else snippet

# ===================== Onbellek =====================
_INDEX: Optional[BM25Index] = None
_CACHE_AT: float = 0.0

def ensure_index(force: bool = False) -> None:
    """Indeksi CACHE_TTL'e gore tazeler."""
    global _INDEX, _CACHE_AT
    now = time.time()
    if (not _INDEX) or force or (now - _CACHE_AT > CACHE_TTL):
        items = load_all()
        _INDEX = BM25Index(items)
        _CACHE_AT = now
        print(f"[READY] {len(items)} kayit indekslendi. (pdf={sum(1 for _ in DOCS_DIR.rglob('*.pdf'))}, dir={DOCS_DIR})")

# ===================== FastAPI =====================
app = FastAPI(title=APP_TITLE)
templates = Jinja2Templates(directory="templates")
if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

@app.on_event("startup")
def _on_start() -> None:
    ensure_index(force=True)

@app.get("/", include_in_schema=False)
def home():
    return RedirectResponse(url="/chat", status_code=307)

@app.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request):
    tpl = Path("templates/chat.html")
    if tpl.exists():
        return templates.TemplateResponse("chat.html", {"request": request})

    # Basit fallback sayfa
    html = """<!doctype html><meta charset='utf-8'>
    <title>Firat U. 1. Sinif Asistani</title>
    <style>
      :root{color-scheme:light dark}
      body{font-family:system-ui,Segoe UI,Roboto,Arial;margin:40px;max-width:900px}
      textarea{width:100%;height:110px;padding:10px;border-radius:10px}
      button{padding:10px 16px;border-radius:10px}
      pre{white-space:pre-wrap;background:#f6f7f8;padding:12px;border-radius:10px}
      .src{color:#555}
    </style>
    <h2>Firat U. 1. Sinif Asistani</h2>
    <p><small>Sadece <b>docs/</b> klasorundeki PDF’lerden cevap verir. Ornek: <i>gecme notu, devamsizlik, yaz okulu...</i></small></p>
    <textarea id=q placeholder="Sorunuzu yazin"></textarea><br>
    <button onclick="ask()">Sor</button>
    <pre id=out></pre>
    <script>
      async function ask(){
        const q=document.getElementById('q').value.trim();
        const r=await fetch('/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:q})});
        const j=await r.json(); const s=(j.sources||[]).map(x=>"- "+x).join("\\n");
        document.getElementById('out').textContent=(j.answer||j.error||"")+"\\n\\nKaynaklar:\\n"+(s||"-");
      }
    </script>
    """
    return HTMLResponse(html)

@app.post("/ask")
def ask(body: dict = Body(default={})):
    """Her zaman {answer,sources,error} dondurur; 422 vermez."""
    try:
        q = (str(body.get("question") or body.get("q") or "")).strip()
        if not q:
            return JSONResponse({"answer": "Soru bos olamaz.", "sources": [], "error": None})

        ensure_index()
        assert _INDEX is not None

        # 1) BM25 (+ fuzzy, + prefix etkisi iceren search)
        scored = _INDEX.search(q, top_k=TOP_K_RETURN)

        # 2) Keyword + fuzzy fallback
        if not scored:
            scored = _INDEX.keyword_fallback(q, limit=TOP_K_RETURN)

        # 3) Hala yoksa: durustce bulunamadi (uydurma yok)
        if not scored:
            return JSONResponse(
                {"answer": "Uygun bir yanit bulunamadi. Farkli kelimelerle deneyin.", "sources": [], "error": None}
            )

        # En iyi aday + guvenlik: gercek token ortusmesi sart
        best_score, best = scored[0]
        qset = set(tokenize(q))
        doc_tokens = set(best.get("a_tokens", [])) | set(best.get("q_tokens", [])) | set(best.get("kw_tokens", []))
        if len(qset & doc_tokens) == 0:
            return JSONResponse(
                {"answer": "Uygun bir yanit bulunamadi. Farkli kelimelerle deneyin.", "sources": [], "error": None}
            )

        # Negatif sinyal: sorgu 'itiraz' icermiyorsa ancak cevap 'itiraz' agirlikli ise reddet
        if ("itiraz" not in qset) and ("itiraz" in doc_tokens):
            return JSONResponse(
                {"answer": "Uygun bir yanit bulunamadi. Farkli kelimelerle deneyin.", "sources": [], "error": None}
            )

        # Pass-grade niyeti icin cevapta final/vize/sayi/% sinyali yoksa reddet
        intents = detect_intents(q)
        if intents.get("pass_grade"):
            joined = " ".join(best.get("a_tokens", []))
            has_exam_terms = ("final" in joined and "vize" in joined) or re.search(r"\d", joined) or "%" in joined
            if not has_exam_terms:
                return JSONResponse(
                    {"answer": "Uygun bir yanit bulunamadi. Farkli kelimelerle deneyin.", "sources": [], "error": None}
                )

        # Gectiyse snippet uret
        ans = best_snippet(best["a"], q, max_chars=SNIPPET_CHARS)
        src = f"{best['file']} s:{best['page']}"
        return JSONResponse({"answer": ans, "sources": [src], "error": None})

    except Exception as e:
        return JSONResponse({"answer": "", "sources": [], "error": f"{type(e).__name__}: {e}"})

@app.post("/reindex")
def reindex():
    ensure_index(force=True)
    return {"status": "ok"}

@app.get("/health")
def health():
    cnt = len(list(DOCS_DIR.rglob("*.pdf")))
    return {
        "status": "ok",
        "pdf_count": cnt,
        "docs_dir": str(DOCS_DIR),
        "indexed": len(_INDEX.items) if _INDEX else 0,
    }
