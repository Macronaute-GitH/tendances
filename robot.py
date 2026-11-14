"""Robot « Tendances » : choisit chaque semaine une trentaine de recettes de saison populaires sur des sites de cuisine.

Il ne recopie aucune recette : il publie seulement le titre, le site, le lien, la note et les produits de saison.
L'application télécharge ce petit fichier ; l'utilisateur ouvre la page d'origine ou importe la recette s'il le veut.

Politesse :
- il respecte le fichier robots.txt de chaque site et attend 2 secondes entre deux pages d'un même site ;
- il lit au plus ~25 recettes par site et par semaine.

Utilisation :  python robot.py            (écrit docs/tendances.json)
               python robot.py --mois 7   (pour essayer un autre mois)
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import html
import json
import math
import random
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlparse

ICI = Path(__file__).resolve().parent
SORTIE = ICI / "docs" / "tendances.json"
HISTORIQUE = ICI / "docs" / "historique.json"
NOM_ROBOT = "TambouilleBot"
UA = f"Mozilla/5.0 (compatible; {NOM_ROBOT}/1.0; selection hebdomadaire de recettes de saison)"
PAUSE = 2.0           # secondes entre deux pages d'un même site
LECTURES_MAX = 25     # pages de recettes lues par site
TAILLE = 30           # recettes publiées
SEMAINES_SANS_REDITE = 8

# ---------------------------------------------------------------- calendrier (le même que l'app)

SAISONS = json.loads((ICI / "saisons.json").read_text(encoding="utf-8"))


def simplifier(s: str) -> str:
    s = s.lower().replace("œ", "oe")
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def mots(nom: str) -> list[str]:
    """Comme seasonWords() dans l'app : sans accents, tirets ni pluriel."""
    nom = re.sub(r"\((?:s|x|es)\)", "s", nom)  # « pomme(s) de terre » = « pommes de terre »
    s = simplifier(nom.split("(")[0].split(",")[0])
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return [w[:-1] if len(w) > 3 and w[-1] in "sx" else w for w in s.split()]


ENTREES = [(mots(m), p) for p in SAISONS["produits"] for m in p["mots"]] + [(mots(m), None) for m in SAISONS["toute_annee"]]
TRANSFORMES = set(SAISONS["transformes"])


def position(liste: list[str], suite: list[str]) -> int:
    for i in range(len(liste) - len(suite) + 1):
        if liste[i:i + len(suite)] == suite:
            return i
    return -1


def produit_de(ingredient: str) -> dict | None:
    """Comme produceOf() dans l'app."""
    w = mots(ingredient)
    if not w or any(x in TRANSFORMES for x in w):
        return None
    trouves = [(len(e), position(w, e), p) for e, p in ENTREES if position(w, e) >= 0]
    if not trouves:
        return None
    trouves.sort(key=lambda t: (-t[0], t[1]))
    return trouves[0][2]


def produits_du_titre(texte: str) -> set[str]:
    """Produits de saison cités dans un titre ou une adresse (pour trier les candidats avant de lire la page)."""
    w = mots(texte.replace("-", " ").replace("_", " "))
    return {p["nom"] for e, p in ENTREES if p and position(w, e) >= 0}


# ---------------------------------------------------------------- lecture des sites

_dernier: dict[str, float] = {}
_robots: dict[str, urllib.robotparser.RobotFileParser] = {}


def autorise(url: str) -> bool:
    hote = urlparse(url).netloc
    if hote not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            rp.parse(lire(f"https://{hote}/robots.txt", verifier=False).splitlines())
        except Exception:
            rp.parse([])
        _robots[hote] = rp
    return _robots[hote].can_fetch(NOM_ROBOT, url)


def lire(url: str, verifier: bool = True, limite: int = 6_000_000) -> str:
    if verifier and not autorise(url):
        raise PermissionError(f"interdit par robots.txt : {url}")
    hote = urlparse(url).netloc
    attente = _dernier.get(hote, 0) + PAUSE - time.time()
    if attente > 0:
        time.sleep(attente)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "fr-FR,fr"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            b = r.read(limite)
    finally:
        _dernier[hote] = time.time()
    if b[:2] == b"\x1f\x8b":
        b = gzip.decompress(b)
    return b.decode("utf-8", "replace")


def adresses(xml: str) -> list[str]:
    return [html.unescape(u) for u in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)]


def urls_du_plan(source: dict) -> list[str]:
    """Toutes les adresses de recettes d'un plan de site (en suivant les sous-plans)."""
    a_lire, vues, urls = [source["url"]], 0, []
    while a_lire and vues < 12:
        xml = lire(a_lire.pop(0))
        vues += 1
        for u in adresses(xml):
            if u.split("?")[0].endswith(".xml") or "sitemap" in u:
                if source.get("filtre", "") in u:
                    a_lire.append(u)
            else:
                urls.append(u)
    return urls


def urls_du_flux(source: dict) -> list[tuple[str, str]]:
    xml = lire(source["url"])
    items = re.findall(r"<item>(.*?)</item>", xml, re.S)
    sortie = []
    for it in items:
        lien = re.search(r"<link>\s*(.*?)\s*</link>", it, re.S)
        titre = re.search(r"<title>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</title>", it, re.S)
        if lien:
            sortie.append((html.unescape(lien[1]), html.unescape(titre[1]) if titre else ""))
    return sortie


def recette_ld(page: str) -> dict | None:
    """Les données « Recipe » que le site publie pour les moteurs de recherche (schema.org)."""
    for m in re.finditer(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", page, re.S | re.I):
        try:
            d = json.loads(m[1].strip())
        except Exception:
            continue
        pile = [d]
        while pile:
            x = pile.pop()
            if isinstance(x, list):
                pile += x
            elif isinstance(x, dict):
                t = x.get("@type")
                if "Recipe" in (t if isinstance(t, list) else [t]):
                    return x
                if "@graph" in x:
                    pile.append(x["@graph"])
    return None


def minutes(iso: str | None) -> int:
    m = re.fullmatch(r"P(?:\d+D)?T?(?:(\d+)H)?(?:(\d+)M)?.*", iso or "")
    return (int(m[1] or 0) * 60 + int(m[2] or 0)) if m else 0


def nombre(x) -> float:
    try:
        return float(str(x).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def image_de(ld: dict) -> str | None:
    """L'adresse de la photo du plat (sur le site d'origine : elle n'est pas copiée)."""
    img = ld.get("image")
    if isinstance(img, list):
        img = img[-1] if img else None  # souvent du plus petit au plus grand format
    if isinstance(img, dict):
        img = img.get("url") or img.get("contentUrl")
    img = str(img or "").strip()
    return img if img.startswith("https://") else None


def texte(x) -> str:
    """Texte sans balises HTML ni espaces en trop."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(str(x or "")))).strip()


def resume(description) -> str | None:
    """Les 2 premières phrases de la présentation du site (240 caractères au plus)."""
    t = texte(description)
    if len(t) < 20 or re.match(r"(?i)recette\b.*(not[ée]e|personnes)", t):
        return None  # phrase toute faite (« Recette X : 4 personnes, notée 4,8/5… ») : pas utile
    phrases = re.split(r"(?<=[.!?…])\s+", t)
    r = " ".join(phrases[:2])
    if len(r) > 240:
        r = r[:237].rsplit(" ", 1)[0] + "…"
    return r


def fiche(url: str, source: dict, mois: int) -> dict | None:
    """Lit une page de recette et renvoie sa fiche (sans le texte de la recette), ou None si elle n'est pas de saison."""
    ld = recette_ld(lire(url))
    if not ld:
        return None
    if re.match(r"(?i)\s*menus?\b", str(ld.get("name") or "")):
        return None  # « Menu de la semaine » : un article, pas une recette
    ingredients = [str(i) for i in (ld.get("recipeIngredient") or [])]
    trouves = {p["nom"]: p for p in (produit_de(i) for i in ingredients) if p}
    if not trouves:
        return None
    hors = [n for n, p in trouves.items() if mois not in p["mois"]]
    if hors:
        return None
    note = ld.get("aggregateRating") or {}
    if nombre(note.get("ratingValue")) and nombre(note.get("ratingValue")) < 3.8:
        return None  # recette mal notée
    categorie = ld.get("recipeCategory")
    if isinstance(categorie, list):
        categorie = categorie[0] if categorie else None
    titre = html.unescape(str(ld.get("name") or "")).strip()
    titre = re.sub(r"\s*[:\-–|]\s*(la )?(meilleure|vraie) recette.*$", "", titre, flags=re.I)
    return {
        "titre": titre,
        "url": url,
        "site": source["nom"],
        "blog": bool(source.get("blog")),
        "note": round(nombre(note.get("ratingValue")), 1) or None,
        "avis": int(nombre(note.get("ratingCount") or note.get("reviewCount"))) or None,
        "minutes": minutes(ld.get("totalTime")) or None,
        "categorie": str(categorie).strip() if categorie else None,
        "saison": sorted(trouves),
        "image": image_de(ld),
        "description": resume(ld.get("description")),
        "ingredients": [re.sub(r"(?i)\bunit[ée]s?\s+", "", texte(i)) for i in ingredients if texte(i)][:25],
    }


def score(f: dict) -> float:
    """Populaire ET de saison : note × nombre d'avis (en log), plus un bonus par produit de saison."""
    s = 1.0
    if f["avis"]:
        s += (f["note"] or 3) / 5 * math.log10(1 + f["avis"]) * 2
    if f["blog"]:
        s += 1.5  # les blogs ont rarement des notes : on leur laisse leur chance
    return s + 0.3 * len(f["saison"])


# ---------------------------------------------------------------- sélection

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mois", type=int, default=dt.date.today().month)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    mois = args.mois
    semaine = dt.date.today().isocalendar()
    hasard = random.Random(f"{semaine.year}-{semaine.week}")

    historique = json.loads(HISTORIQUE.read_text(encoding="utf-8")) if HISTORIQUE.exists() else []
    deja_vues = {u for h in historique[-SEMAINES_SANS_REDITE:] for u in h["urls"]}
    de_saison = [p["nom"] for p in SAISONS["produits"] if mois in p["mois"]]
    print(f"Mois {mois} : {len(de_saison)} produits de saison")

    sources = json.loads((ICI / "sources.json").read_text(encoding="utf-8"))["sources"]
    fiches: list[dict] = []
    for source in sources:
        try:
            if source["type"] == "plan":
                toutes = urls_du_plan(source)
                # Candidats : les adresses qui citent un produit de saison, mélangées, produit par produit.
                par_produit: dict[str, list[str]] = {}
                for u in toutes:
                    if u in deja_vues:
                        continue
                    for nom in produits_du_titre(urlparse(u).path) & set(de_saison):
                        par_produit.setdefault(nom, []).append(u)
                for liste in par_produit.values():
                    hasard.shuffle(liste)
                candidats, i = [], 0
                while len(candidats) < LECTURES_MAX and any(i < len(l) for l in par_produit.values()):
                    for liste in par_produit.values():
                        if i < len(liste) and liste[i] not in candidats:
                            candidats.append(liste[i])
                    i += 1
                candidats = candidats[:LECTURES_MAX]
                print(f"{source['nom']} : {len(toutes)} recettes, {len(candidats)} lues")
            else:
                items = urls_du_flux(source)
                candidats = [u for u, _ in items if u not in deja_vues][:LECTURES_MAX]
                print(f"{source['nom']} : {len(items)} articles récents, {len(candidats)} lus")
            trouvees = []
            for u in candidats:
                try:
                    f = fiche(u, source, mois)
                except Exception as e:  # une page en erreur ne bloque pas les autres
                    print(f"   ! {u} : {e}")
                    continue
                if f and f["titre"]:
                    trouvees.append(f)
            trouvees.sort(key=score, reverse=True)
            fiches += trouvees[: source.get("max", 6)]
            print(f"   → {len(trouvees)} de saison, {min(len(trouvees), source.get('max', 6))} gardées")
        except Exception as e:
            print(f"!! {source['nom']} ignoré : {e}")

    fiches.sort(key=score, reverse=True)
    choix = fiches[:TAILLE]
    aujourd_hui = dt.date.today().isoformat()
    sortie = {
        "version": 1,
        "date": aujourd_hui,
        "mois": mois,
        "recettes": choix,
    }
    SORTIE.parent.mkdir(exist_ok=True)
    SORTIE.write_text(json.dumps(sortie, ensure_ascii=False, indent=1), encoding="utf-8")
    historique.append({"date": aujourd_hui, "urls": [f["url"] for f in choix]})
    HISTORIQUE.write_text(json.dumps(historique[-SEMAINES_SANS_REDITE:], ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{len(choix)} recettes publiées dans {SORTIE.relative_to(ICI)}")
    for f in choix:
        avis = f"★ {f['note']} ({f['avis']} avis)" if f["avis"] else "—"
        print(f" - {f['titre']} · {f['site']} · {avis} · {', '.join(f['saison'])}")
    if len(choix) < 10:
        sys.exit("Trop peu de recettes trouvées : vérifiez les sources.")


if __name__ == "__main__":
    main()
