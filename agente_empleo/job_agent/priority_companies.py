"""Empresas con prioridad de puntaje. No filtran: una oferta de otra empresa sigue en la lista."""
from __future__ import annotations

import re
import unicodedata

BONUS = 8

# El nombre visible solo matchea por estos alias. "Williams" o "Haas" a secas pegan con otras empresas.
_ALIAS_ONLY = {"Williams", "Haas", "Formula 1"}

_ALIASES: dict[str, list[str]] = {
    "Google": ["alphabet"],
    "Meta": ["meta platforms", "facebook"],
    "Amazon": ["amazon.com", "amazon web services"],
    "Uber": ["uber eats", "uber technologies"],
    "Block": ["square"],
    "Snap": ["snapchat"],
    "ByteDance": ["tiktok"],
    "dbt Labs": ["dbt labs", "getdbt"],
    "Booking.com": ["booking holdings"],
    "DoorDash": ["doordash"],
    "Oracle Red Bull Racing": ["red bull racing", "oracle red bull"],
    "Racing Bulls": ["visa cash app racing bulls"],
    "Mercedes-AMG Petronas": ["mercedes-amg", "mercedes amg petronas", "mercedes f1"],
    "McLaren Racing": ["mclaren f1", "mclaren mastercard"],
    "Scuderia Ferrari": ["ferrari hp", "scuderia ferrari"],
    "Aston Martin F1": ["aston martin aramco", "aston martin f1"],
    "Williams": ["williams racing", "williams f1", "atlassian williams"],
    "Haas": ["haas f1", "haas formula", "tgr haas"],
    "Audi": ["audi revolut", "audi f1"],
    "Alpine": ["bwt alpine", "alpine f1", "alpine racing"],
    "Cadillac": ["cadillac formula", "cadillac f1"],
    "Formula 1": ["formula one"],
    "New York Red Bulls": ["red bull new york", "ny red bulls"],
    "LA Clippers": ["los angeles clippers"],
    "Los Angeles Lakers": ["la lakers"],
    "LAFC": ["los angeles fc", "los angeles football club"],
    "Inter Miami": ["inter miami cf"],
    "AFC Bournemouth": ["bournemouth"],
    "Brighton & Hove Albion": ["brighton and hove albion", "brighton"],
    "Manchester City": ["man city"],
    "Manchester United": ["man utd", "man united"],
    "Newcastle United": ["newcastle"],
    "Nottingham Forest": ["nottm forest"],
    "Tottenham Hotspur": ["tottenham", "spurs"],
    "Athletics": ["oakland athletics", "las vegas athletics"],
    "The Athletic": ["the athletic"],
    "Utah Mammoth": ["utah hockey club"],
}

_COMPANIES = [
    # Producto global
    "Google", "Meta", "Amazon", "Apple", "Microsoft", "Netflix", "Nvidia", "Uber", "Airbnb", "Stripe",
    "Spotify", "Shopify", "Block", "Atlassian", "Cloudflare", "Databricks", "Snowflake", "dbt Labs",
    "Salesforce", "Adobe", "LinkedIn", "DoorDash", "Instacart", "Coinbase", "Robinhood", "Plaid",
    "Intuit", "ServiceNow", "Workday", "Datadog", "MongoDB", "Confluent", "Palantir", "CrowdStrike",
    "Twilio", "PayPal", "Visa", "Mastercard", "Figma", "Notion", "Ramp", "Brex", "Chime", "Affirm",
    "SoFi", "Fivetran", "Hightouch", "Tesla", "OpenAI", "Anthropic", "ByteDance", "Snap", "Pinterest",
    "Reddit", "Discord", "Roblox", "Epic Games", "Duolingo", "Canva", "Riot Games",
    # Producto regional y relocation
    "Mercado Libre", "Nubank", "Rappi", "iFood", "dLocal", "Kavak", "Ualá", "Wildlife", "PedidosYa",
    "Despegar", "Booking.com", "Adyen", "Revolut", "Klarna", "Wise", "Monzo", "Checkout.com",
    "Delivery Hero", "Zalando", "HelloFresh", "Celonis", "Arm", "Grab", "Shopee", "Coupang",
    "Careem", "Talabat", "noon",
    # Movilidad y delivery en Europa
    "Bolt", "Glovo", "Deliveroo", "Just Eat", "Wolt", "Getir", "Flink", "Cabify", "BlaBlaCar",
    "FREENOW", "Heetch", "Voi", "Dott", "Lime", "Stuart",
    # Comercio y coleccionables
    "Fanatics", "Nike", "New Era", "Lids", "JD Sports", "Dick's Sporting Goods", "Mitchell & Ness",
    "Topps", "Panini", "Upper Deck", "Goldin", "Whatnot", "eBay",
    # Apuestas y fantasy
    "DraftKings", "FanDuel", "Flutter", "Betfair", "Paddy Power", "Sky Bet", "BetMGM", "bet365",
    "Entain", "ESPN Bet", "Penn Entertainment", "Caesars Entertainment", "PrizePicks",
    "Underdog Fantasy", "Sleeper", "Sorare", "Betsson", "Kindred", "Tipico",
    # Datos, media y performance
    "Sportradar", "Genius Sports", "Stats Perform", "Opta", "Second Spectrum", "StatsBomb", "DAZN",
    "ESPN", "Sky Sports", "beIN Sports", "The Athletic", "WSC Sports", "Hudl", "Catapult", "Whoop",
    "Two Circles",
    # Entradas
    "Live Nation", "Ticketmaster", "SeatGeek", "StubHub",
    # Ligas y dueños
    "NBA", "NFL", "MLB", "NHL", "PGA Tour", "Formula 1", "UEFA", "Premier League", "MLS", "FIFA",
    "Red Bull",
    # Fórmula 1
    "Oracle Red Bull Racing", "Racing Bulls", "Mercedes-AMG Petronas", "McLaren Racing",
    "Scuderia Ferrari", "Aston Martin F1", "Williams", "Haas", "Audi", "Alpine", "Cadillac",
    # Premier League
    "Arsenal", "Aston Villa", "AFC Bournemouth", "Brentford", "Brighton & Hove Albion", "Chelsea",
    "Coventry City", "Crystal Palace", "Everton", "Fulham", "Hull City", "Ipswich Town",
    "Leeds United", "Liverpool", "Manchester City", "Manchester United", "Newcastle United",
    "Nottingham Forest", "Sunderland", "Tottenham Hotspur",
    # MLS
    "Atlanta United", "Austin FC", "CF Montréal", "Charlotte FC", "Chicago Fire", "Colorado Rapids",
    "Columbus Crew", "D.C. United", "FC Cincinnati", "FC Dallas", "Houston Dynamo", "Inter Miami",
    "LA Galaxy", "LAFC", "Minnesota United", "Nashville SC", "New England Revolution",
    "New York City FC", "New York Red Bulls", "Orlando City", "Philadelphia Union",
    "Portland Timbers", "Real Salt Lake", "San Diego FC", "San Jose Earthquakes", "Seattle Sounders",
    "Sporting Kansas City", "St. Louis City", "Toronto FC", "Vancouver Whitecaps",
    # NBA
    "Atlanta Hawks", "Boston Celtics", "Brooklyn Nets", "Charlotte Hornets", "Chicago Bulls",
    "Cleveland Cavaliers", "Dallas Mavericks", "Denver Nuggets", "Detroit Pistons",
    "Golden State Warriors", "Houston Rockets", "Indiana Pacers", "LA Clippers", "Los Angeles Lakers",
    "Memphis Grizzlies", "Miami Heat", "Milwaukee Bucks", "Minnesota Timberwolves",
    "New Orleans Pelicans", "New York Knicks", "Oklahoma City Thunder", "Orlando Magic",
    "Philadelphia 76ers", "Phoenix Suns", "Portland Trail Blazers", "Sacramento Kings",
    "San Antonio Spurs", "Toronto Raptors", "Utah Jazz", "Washington Wizards",
    # NFL
    "Arizona Cardinals", "Atlanta Falcons", "Baltimore Ravens", "Buffalo Bills", "Carolina Panthers",
    "Chicago Bears", "Cincinnati Bengals", "Cleveland Browns", "Dallas Cowboys", "Denver Broncos",
    "Detroit Lions", "Green Bay Packers", "Houston Texans", "Indianapolis Colts", "Jacksonville Jaguars",
    "Kansas City Chiefs", "Las Vegas Raiders", "Los Angeles Chargers", "Los Angeles Rams",
    "Miami Dolphins", "Minnesota Vikings", "New England Patriots", "New Orleans Saints",
    "New York Giants", "New York Jets", "Philadelphia Eagles", "Pittsburgh Steelers",
    "San Francisco 49ers", "Seattle Seahawks", "Tampa Bay Buccaneers", "Tennessee Titans",
    "Washington Commanders",
    # MLB
    "Arizona Diamondbacks", "Atlanta Braves", "Baltimore Orioles", "Boston Red Sox", "Chicago Cubs",
    "Chicago White Sox", "Cincinnati Reds", "Cleveland Guardians", "Colorado Rockies", "Detroit Tigers",
    "Houston Astros", "Kansas City Royals", "Los Angeles Angels", "Los Angeles Dodgers", "Miami Marlins",
    "Milwaukee Brewers", "Minnesota Twins", "New York Mets", "New York Yankees", "Athletics",
    "Philadelphia Phillies", "Pittsburgh Pirates", "San Diego Padres", "San Francisco Giants",
    "Seattle Mariners", "St. Louis Cardinals", "Tampa Bay Rays", "Texas Rangers", "Toronto Blue Jays",
    "Washington Nationals",
    # NHL
    "Anaheim Ducks", "Boston Bruins", "Buffalo Sabres", "Calgary Flames", "Carolina Hurricanes",
    "Chicago Blackhawks", "Colorado Avalanche", "Columbus Blue Jackets", "Dallas Stars",
    "Detroit Red Wings", "Edmonton Oilers", "Florida Panthers", "Los Angeles Kings", "Minnesota Wild",
    "Montreal Canadiens", "Nashville Predators", "New Jersey Devils", "New York Islanders",
    "New York Rangers", "Ottawa Senators", "Philadelphia Flyers", "Pittsburgh Penguins",
    "San Jose Sharks", "Seattle Kraken", "St. Louis Blues", "Tampa Bay Lightning", "Toronto Maple Leafs",
    "Utah Mammoth", "Vancouver Canucks", "Vegas Golden Knights", "Washington Capitals", "Winnipeg Jets",
]


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.lower()


def _contains(needle: str, text: str) -> bool:
    if not needle:
        return False
    return re.search(r"(?<![a-z0-9+#])" + re.escape(needle) + r"(?![a-z0-9+#])", text) is not None


def _needles() -> list[tuple[str, str]]:
    pairs: list[tuple[str, str, int]] = []
    for name in _COMPANIES:
        if name not in _ALIAS_ONLY:
            needle = _norm(name)
            pairs.append((needle, name, len(needle)))
        for alias in _ALIASES.get(name, []):
            needle = _norm(alias)
            pairs.append((needle, name, len(needle)))
    pairs.sort(key=lambda item: item[2], reverse=True)
    return [(needle, display) for needle, display, _length in pairs]


_NEEDLES = _needles()


def priority_company(company: str) -> str | None:
    """Devuelve el nombre de la lista si la empresa de la oferta es esa, o una marca del mismo grupo."""
    text = _norm(company)
    if not text:
        return None
    for needle, display in _NEEDLES:
        if _contains(needle, text):
            return display
    return None
