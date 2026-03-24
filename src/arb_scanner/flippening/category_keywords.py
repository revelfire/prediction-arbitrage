"""Built-in keyword sets and matching utilities for market category discovery.

Provides default keyword dictionaries for sport categories and fuzzy matching
used to classify prediction market titles into configured categories.
"""

from __future__ import annotations

from arb_scanner.models.config import CategoryConfig

DEFAULT_SPORT_KEYWORDS: dict[str, list[str]] = {
    "esports": [
        "valorant",
        "counter-strike",
        "cs2",
        "rocket league",
        "league of legends",
        "dota 2",
        "dota2",
        "overwatch",
        "blast premier",
        "vcl ",
        "vct ",
    ],
    "nba": [
        "lakers",
        "celtics",
        "warriors",
        "bucks",
        "76ers",
        "heat",
        "knicks",
        "bulls",
        "nets",
        "nuggets",
        "suns",
        "mavericks",
        "clippers",
        "spurs",
        "rockets",
        "grizzlies",
        "timberwolves",
        "pelicans",
        "thunder",
        "pacers",
        "hawks",
        "hornets",
        "wizards",
        "pistons",
        "cavaliers",
        "magic",
        "raptors",
        "kings",
        "trail blazers",
        "jazz",
    ],
    "nhl": [
        "bruins",
        "maple leafs",
        "canadiens",
        "rangers",
        "penguins",
        "blackhawks",
        "red wings",
        "flyers",
        "oilers",
        "avalanche",
        "lightning",
        "panthers",
        "capitals",
        "blues",
        "predators",
        "wild",
        "flames",
        "canucks",
        "jets",
        "hurricanes",
        "senators",
        "kraken",
        "stars",
        "ducks",
        "devils",
        "islanders",
        "sabres",
        "coyotes",
        "blue jackets",
        "sharks",
    ],
    "nfl": [
        "chiefs",
        "eagles",
        "bills",
        "49ers",
        "cowboys",
        "ravens",
        "dolphins",
        "lions",
        "packers",
        "bengals",
        "texans",
        "steelers",
        "chargers",
        "rams",
        "seahawks",
        "jaguars",
        "vikings",
        "bears",
        "jets",
        "saints",
        "broncos",
        "browns",
        "colts",
        "commanders",
        "falcons",
        "cardinals",
        "giants",
        "titans",
        "panthers",
        "buccaneers",
        "raiders",
        "patriots",
    ],
    "mlb": [
        "yankees",
        "dodgers",
        "astros",
        "braves",
        "mets",
        "phillies",
        "padres",
        "cubs",
        "red sox",
        "cardinals",
        "mariners",
        "rangers",
        "twins",
        "guardians",
        "orioles",
        "blue jays",
        "rays",
        "brewers",
        "reds",
        "diamondbacks",
        "giants",
        "pirates",
        "royals",
        "white sox",
        "tigers",
        "athletics",
        "rockies",
        "marlins",
        "nationals",
        "angels",
    ],
    "epl": [
        "arsenal",
        "manchester city",
        "manchester united",
        "liverpool",
        "chelsea",
        "tottenham",
        "newcastle",
        "brighton",
        "aston villa",
        "west ham",
        "brentford",
        "crystal palace",
        "wolves",
        "bournemouth",
        "fulham",
        "everton",
        "nottingham forest",
        "burnley",
        "luton",
        "sheffield united",
        "premier league",
    ],
    "ufc": [
        "ufc",
        "mma",
        "fight night",
        "ppv",
        "conor",
        "mcgregor",
        "adesanya",
        "volkanovski",
        "makhachev",
        "o'malley",
        "chimaev",
        "topuria",
        "strickland",
        "poirier",
    ],
}


def get_category_keywords(category: CategoryConfig, category_id: str) -> list[str]:
    """Return keywords for a category, falling back to built-in sport keywords.

    Args:
        category: Category configuration.
        category_id: Category identifier slug.

    Returns:
        List of lowercase keyword strings.
    """
    if category.discovery_keywords:
        return category.discovery_keywords
    return DEFAULT_SPORT_KEYWORDS.get(category_id, [])


def fuzzy_match_category(
    title: str,
    question: str,
    categories: dict[str, CategoryConfig],
    keyword_map: dict[str, list[str]],
) -> str | None:
    """Return the first category whose keywords appear in the market text.

    Iterates over categories in sorted order for deterministic tiebreaking
    when keyword lists overlap (EC-002).

    Args:
        title: Market title string (case-insensitive).
        question: Market question string (case-insensitive).
        categories: Mapping of category_id to config.
        keyword_map: Mapping of category_id to keyword lists.

    Returns:
        The matching category_id, or None if no keyword matched.
    """
    search_text = title.lower() + " " + question.lower()
    for cat_id in sorted(categories):
        for keyword in keyword_map.get(cat_id, []):
            if keyword in search_text:
                return cat_id
    return None
