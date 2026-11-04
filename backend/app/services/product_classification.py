"""Conservative name-derived catalogue metadata, separate from item matching.

Never infer tax codes, merge products, change SKU, or overwrite reviewed metadata.
This is a deterministic catalogue parser, not a trained ML classifier.
"""
import re


BRANDS = [
    (r"\b(?:image\s*star|imagstar)\b", "Image Star"),
    (r"\bimage\s+toner\b", "Image Toner"),
    (r"\bfrontech\b", "Frontech"), (r"\bdell\b", "Dell"),
    (r"\basus\b", "Asus"), (r"\bacer\b", "Acer"),
    (r"\blenovo\b", "Lenovo"), (r"\b(?:hewlett.packard|hp)\b", "HP"),
    (r"\b(?:western\s+digital|wd)\b", "Western Digital"),
    (r"\bseagate\b", "Seagate"), (r"\bsamsung\b", "Samsung"),
    (r"\b(?:hi[ -]?focus|hifocus)\b", "Hi-Focus"),
    (r"\bdahua\b", "Dahua"), (r"\bhikvision\b", "Hikvision"),
    (r"\bunv\b", "UNV"), (r"\bmarx\b", "Marx"),
    (r"\btp[ -]?link\b", "TP-Link"), (r"\bd[ -]?link\b", "D-Link"),
    (r"\blogitech\b", "Logitech"), (r"\bcanon\b", "Canon"),
    (r"\bepson\b", "Epson"), (r"\buricom\b", "Uricom"),
]
TYPES = [
    (r"\btoner\b|\bcartridge\b", "Toner cartridge"),
    (r"\bthermal\s+(?:paper|roll)\b", "Thermal paper"),
    (r"\b(?:laptop|notebook)\b", "Laptop"),
    (r"\b(?:monitor|led\s+display|lcd\s+display)\b", "Monitor"),
    (r"\b(?:hard\s*(?:disk|drive)|hdd)\b", "Hard disk"),
    (r"\bssd\b|\bsolid.state\b", "SSD"),
    (r"\bnvr\b", "NVR"), (r"\bdvr\b", "DVR"),
    (r"\bcamera\b(?!\s+(?:cable|box|power))", "Camera"),
    (r"\brack\b", "Equipment rack"),
    (r"\b(?:cable|coil)\b", "Cable"),
    (r"\b(?:poe|switch|hub)\b", "Network switch"),
    (r"\bmouse\b", "Mouse"), (r"\bkeyboard\b", "Keyboard"),
    (r"\bprinter\b", "Printer"), (r"\b(?:power\s+supply|smps|adapter)\b", "Power supply"),
    (r"\bmedia\s+converter\b", "Media converter"),
]


def infer_product_classification(name: str) -> dict:
    text = re.sub(r"\s+", " ", str(name or "")).strip()
    brand = next((label for pattern, label in BRANDS if re.search(pattern, text, re.I)), None)
    kind = next((label for pattern, label in TYPES if re.search(pattern, text, re.I)), None)
    models = []
    if kind == "Toner cartridge":
        models = re.findall(r"\b(?:Q\d{4}[A-Z]?|TF\d{3,6}|\d{2,4}[A-Z](?:\s+ULTIMATE)?)\b", text, re.I)
        if not models:
            models = re.findall(r"\b\d{4}\b", text)
    elif kind == "Equipment rack":
        models = re.findall(r"\b(?:\d+U|\d{3}[ -]?[A-Z])\b", text, re.I)
    elif kind in {"Hard disk", "SSD"}:
        models = re.findall(r"\b\d+(?:\.\d+)?\s*(?:TB|GB)\b", text, re.I)
        models += re.findall(r"\b(?:PURPLE|BLUE|BLACK|RED)\b", text, re.I)
    else:
        # A mixed letter/digit model is stronger evidence than an arbitrary word.
        clean = re.split(r"\bS/?N\s*:", text, flags=re.I)[0]
        models = re.findall(r"\b(?=[A-Z0-9-]*[A-Z])(?=[A-Z0-9-]*\d)[A-Z][A-Z0-9-]{3,}\b", clean, re.I)
    models = list(dict.fromkeys(re.sub(r"\s+", " ", value.upper()) for value in models))
    return {"product_type": kind, "brand": brand, "model": " / ".join(models) or None}


def classified_product(product: dict) -> dict:
    result = dict(product)
    inferred = infer_product_classification(result.get("name", ""))
    sources = {}
    for key, suggestion in inferred.items():
        current = result.get(key)
        if current and str(current).strip().casefold() not in {"unclassified", "unknown"}:
            sources[key] = "saved"
        elif suggestion:
            result[key] = suggestion
            sources[key] = "name_derived"
        else:
            result[key] = None
            sources[key] = "needs_review"
    result["classification_sources"] = sources
    return result
