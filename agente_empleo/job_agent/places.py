"""Ubicaciones con sentido geográfico, no solo por texto.

'Londres, Reino Unido' tiene que contar como United Kingdom aunque el aviso esté en español
y no diga 'London'. 'Berlín' tiene que contar como European Union. El Reino Unido no entra
en la Unión Europea: si alguien lo quiere, lo declara aparte.
"""
from __future__ import annotations

import re
import unicodedata

# País -> nombres (es/en) y ciudades como aparecen en LinkedIn.
_COUNTRIES: dict[str, dict[str, list[str]]] = {
    "reino unido": {
        "names": ["reino unido", "united kingdom", "uk", "gran bretana", "great britain", "inglaterra", "england", "escocia", "scotland", "gales", "wales", "irlanda del norte", "northern ireland"],
        "cities": ["londres", "london", "manchester", "birmingham", "edimburgo", "edinburgh", "glasgow", "bristol", "leeds", "liverpool", "cambridge", "oxford", "cardiff", "belfast"],
    },
    "irlanda": {
        "names": ["irlanda", "ireland", "republica de irlanda", "republic of ireland"],
        "cities": ["dublin", "cork", "galway"],
    },
    "espana": {
        "names": ["espana", "spain"],
        "cities": ["madrid", "barcelona", "valencia", "sevilla", "malaga", "bilbao", "zaragoza"],
    },
    "portugal": {"names": ["portugal"], "cities": ["lisboa", "lisbon", "porto"]},
    "francia": {"names": ["francia", "france"], "cities": ["paris", "lyon", "marsella", "marseille", "toulouse", "lille"]},
    "alemania": {
        "names": ["alemania", "germany", "deutschland"],
        "cities": ["berlin", "munich", "munchen", "hamburgo", "hamburg", "frankfurt", "colonia", "cologne", "stuttgart", "dusseldorf"],
    },
    "italia": {"names": ["italia", "italy"], "cities": ["roma", "rome", "milan", "milano", "turin", "torino", "florencia", "florence"]},
    "paises bajos": {"names": ["paises bajos", "netherlands", "holanda", "holland"], "cities": ["amsterdam", "rotterdam", "la haya", "the hague", "utrecht", "eindhoven"]},
    "belgica": {"names": ["belgica", "belgium"], "cities": ["bruselas", "brussels", "amberes", "antwerp"]},
    "luxemburgo": {"names": ["luxemburgo", "luxembourg"], "cities": []},
    "austria": {"names": ["austria"], "cities": ["viena", "vienna"]},
    "suiza": {"names": ["suiza", "switzerland"], "cities": ["zurich", "ginebra", "geneva"]},
    "suecia": {"names": ["suecia", "sweden"], "cities": ["estocolmo", "stockholm", "gotemburgo", "gothenburg"]},
    "noruega": {"names": ["noruega", "norway"], "cities": ["oslo"]},
    "dinamarca": {"names": ["dinamarca", "denmark"], "cities": ["copenhague", "copenhagen"]},
    "finlandia": {"names": ["finlandia", "finland"], "cities": ["helsinki"]},
    "islandia": {"names": ["islandia", "iceland"], "cities": ["reykjavik"]},
    "polonia": {"names": ["polonia", "poland"], "cities": ["varsovia", "warsaw", "cracovia", "krakow"]},
    "chequia": {"names": ["chequia", "czechia", "republica checa", "czech republic"], "cities": ["praga", "prague"]},
    "eslovaquia": {"names": ["eslovaquia", "slovakia"], "cities": ["bratislava"]},
    "hungria": {"names": ["hungria", "hungary"], "cities": ["budapest"]},
    "rumania": {"names": ["rumania", "romania"], "cities": ["bucarest", "bucharest"]},
    "bulgaria": {"names": ["bulgaria"], "cities": ["sofia"]},
    "grecia": {"names": ["grecia", "greece"], "cities": ["atenas", "athens"]},
    "croacia": {"names": ["croacia", "croatia"], "cities": ["zagreb"]},
    "eslovenia": {"names": ["eslovenia", "slovenia"], "cities": ["liubliana", "ljubljana"]},
    "estonia": {"names": ["estonia"], "cities": ["tallin", "tallinn"]},
    "letonia": {"names": ["letonia", "latvia"], "cities": ["riga"]},
    "lituania": {"names": ["lituania", "lithuania"], "cities": ["vilna", "vilnius"]},
    "malta": {"names": ["malta"], "cities": ["la valeta", "valletta"]},
    "chipre": {"names": ["chipre", "cyprus"], "cities": ["nicosia"]},
    "estados unidos": {
        "names": ["estados unidos", "united states", "usa", "us", "eeuu"],
        "cities": ["new york", "nyc", "san francisco", "seattle", "austin", "boston", "chicago", "miami", "los angeles", "atlanta", "denver", "washington"],
    },
    "canada": {"names": ["canada"], "cities": ["toronto", "montreal", "vancouver"]},
    "mexico": {"names": ["mexico"], "cities": ["ciudad de mexico", "cdmx", "mexico city", "guadalajara", "monterrey"]},
    "argentina": {"names": ["argentina"], "cities": ["buenos aires", "cordoba", "rosario", "mendoza"]},
    "chile": {"names": ["chile"], "cities": ["santiago"]},
    "colombia": {"names": ["colombia"], "cities": ["bogota"]},
    "peru": {"names": ["peru"], "cities": ["lima"]},
    "uruguay": {"names": ["uruguay"], "cities": ["montevideo"]},
    "brasil": {"names": ["brasil", "brazil"], "cities": ["sao paulo", "rio de janeiro"]},
    "costa rica": {"names": ["costa rica"], "cities": ["san jose"]},
    "panama": {"names": ["panama"], "cities": ["ciudad de panama"]},
    "ecuador": {"names": ["ecuador"], "cities": ["quito"]},
    "paraguay": {"names": ["paraguay"], "cities": ["asuncion"]},
    "bolivia": {"names": ["bolivia"], "cities": ["la paz"]},
}

# Una región suma países enteros. El Reino Unido no está en la Unión Europea.
_EU = [
    "irlanda", "espana", "portugal", "francia", "alemania", "italia", "paises bajos", "belgica", "luxemburgo", "austria",
    "suecia", "dinamarca", "finlandia", "polonia", "chequia", "eslovaquia", "hungria", "rumania", "bulgaria", "grecia",
    "croacia", "eslovenia", "estonia", "letonia", "lituania", "malta", "chipre",
]
_LATAM = ["mexico", "argentina", "chile", "colombia", "peru", "uruguay", "brasil", "costa rica", "panama", "ecuador", "paraguay", "bolivia"]
_REGIONS: dict[str, list[str]] = {
    "union europea": _EU,
    "european union": _EU,
    "ue": _EU,
    "eu": _EU,
    "europa": _EU + ["reino unido", "suiza", "noruega", "islandia"],
    "europe": _EU + ["reino unido", "suiza", "noruega", "islandia"],
    "latinoamerica": _LATAM,
    "latin america": _LATAM,
    "latam": _LATAM,
}

# "Irlanda" no tiene que matchear "Irlanda del Norte".
_BLOCKED_BY = {
    "irlanda": ["irlanda del norte"],
    "ireland": ["northern ireland"],
    "mexico": ["new mexico"],
}


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.lower()


def _contains(term: str, text: str) -> bool:
    if not term:
        return False
    return re.search(r"(?<![a-z0-9+#])" + re.escape(term) + r"(?![a-z0-9+#])", text) is not None


def _contains_place(needle: str, location: str) -> bool:
    if not _contains(needle, location):
        return False
    return not any(_contains(blocker, location) for blocker in _BLOCKED_BY.get(needle, ()))


def _index() -> tuple[dict[str, str], dict[str, list[str]]]:
    """alias -> país, y país -> todos sus nombres y ciudades."""
    alias_to_country: dict[str, str] = {}
    country_terms: dict[str, list[str]] = {}
    for country, data in _COUNTRIES.items():
        terms = [_norm(t) for t in data["names"] + data["cities"]]
        country_terms[country] = list(dict.fromkeys(terms))
        for term in terms:
            alias_to_country.setdefault(term, country)
    return alias_to_country, country_terms


_ALIAS_TO_COUNTRY, _COUNTRY_TERMS = _index()
_CITY_TO_ALIASES: dict[str, list[str]] = {}
for _country, _data in _COUNTRIES.items():
    _groups: list[list[str]] = []
    # Cada ciudad se agrupa con las que significan lo mismo (london/londres) por posición par no:
    # se agrupan las que el índice ya colapsó al mismo país y son la misma entrada bilingüe,
    # listadas de a pares solo cuando están una al lado de la otra en la definición.
    cities = [_norm(c) for c in _data["cities"]]
    # Pares explícitos: la lista alterna el nombre en español y en inglés cuando existen los dos.
    # Para no adivinar, cada ciudad matchea todas las de su país solo si la preferencia es el país.
    # Una ciudad suelta matchea sus sinónimos, declarados acá.
_CITY_SYNONYMS = [
    ["londres", "london"], ["edimburgo", "edinburgh"], ["munich", "munchen"],
    ["hamburgo", "hamburg"], ["colonia", "cologne"], ["roma", "rome"], ["milan", "milano"],
    ["florencia", "florence"], ["lisboa", "lisbon"], ["estocolmo", "stockholm"],
    ["copenhague", "copenhagen"], ["varsovia", "warsaw"], ["cracovia", "krakow"],
    ["praga", "prague"], ["viena", "vienna"], ["bruselas", "brussels"],
    ["atenas", "athens"], ["dublin"], ["berlin"], ["paris"], ["madrid"], ["barcelona"],
    ["amsterdam"], ["buenos aires"], ["ciudad de mexico", "cdmx", "mexico city"],
]
for _group in _CITY_SYNONYMS:
    for _city in _group:
        _CITY_TO_ALIASES[_city] = _group


def needles_for(preference: str) -> list[str]:
    """Textos que, si aparecen en el aviso, significan que esa preferencia se cumple."""
    key = _norm(preference).strip()
    if not key:
        return []
    if key in _REGIONS:
        members = _REGIONS[key]
        # "Europe" también tiene que reconocer un aviso que dice "Europa", y al revés.
        terms = [name for name, countries in _REGIONS.items() if countries == members]
        for country in members:
            terms.extend(_COUNTRY_TERMS[country])
        return list(dict.fromkeys(terms))
    country = _ALIAS_TO_COUNTRY.get(key)
    if country and key in _COUNTRY_TERMS[country][: len(_COUNTRIES[country]["names"])]:
        return list(_COUNTRY_TERMS[country])
    if key in _CITY_TO_ALIASES:
        return list(_CITY_TO_ALIASES[key])
    return [key]


def matching_preference(preferences: list[str], location: str) -> str | None:
    """Devuelve la preferencia del perfil que cubre esta ubicación, si hay alguna."""
    loc = _norm(location)
    if not loc:
        return None
    for preference in preferences:
        if any(_contains_place(needle, loc) for needle in needles_for(preference)):
            return preference
    return None
