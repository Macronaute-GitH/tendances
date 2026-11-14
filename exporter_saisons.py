"""Copie le calendrier de saison de l'app (Seasons.kt) dans saisons.json, pour que le robot utilise exactement le même.

À relancer après chaque modification du calendrier dans l'app :
    python exporter_saisons.py
"""
import json
import re
from pathlib import Path

ICI = Path(__file__).resolve().parent
KT = ICI.parent / "app/src/main/java/fr/atable/app/data/Seasons.kt"


def mois(spec: str) -> list[int]:
    m = re.fullmatch(r"range\((\d+),\s*(\d+)\)", spec)
    if m:
        a, b = int(m[1]), int(m[2])
        return list(range(a, b + 1)) if a <= b else list(range(a, 13)) + list(range(1, b + 1))
    m = re.fullmatch(r"m\(([\d,\s]+)\)", spec)
    return [int(x) for x in m[1].split(",")]


def liste(nom: str, src: str) -> list[str]:
    bloc = re.search(rf"val {nom}[^=]*=\s*(?:listOf|setOf)\((.*?)\n\)", src, re.S)[1]
    return re.findall(r'"([^"]+)"', bloc)


src = KT.read_text(encoding="utf-8")
produits = []
for m in re.finditer(r'Produce\("([^"]+)",\s*listOf\(([^)]*)\),\s*((?:range|m)\([^)]*\))(,\s*fruit = true)?\)', src):
    produits.append({"nom": m[1], "mots": re.findall(r'"([^"]+)"', m[2]), "mois": mois(m[3]), "fruit": bool(m[4])})

data = {"produits": produits, "toute_annee": liste("ALL_YEAR", src), "transformes": liste("PROCESSED", src)}
(ICI / "saisons.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"{len(produits)} produits, {len(data['toute_annee'])} de toute l'année, {len(data['transformes'])} mots « transformé »")
