from __future__ import annotations

"""Built-in keyword sets per sport for fuzzy sports market discovery on Polymarket.

Provides default keyword dictionaries and matching utilities used to classify
prediction market titles and questions into a known sport category.
"""

DEFAULT_SPORT_KEYWORDS: dict[str, list[str]] = {
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
        "jones",
        "adesanya",
        "pereira",
        "volkanovski",
        "makhachev",
        "o'malley",
        "chimaev",
        "topuria",
        "strickland",
        "poirier",
        "diaz",
        "paul",
    ],
}


def get_sport_keywords(
    config_keywords: dict[str, list[str]],
    sport: str,
) -> list[str]:
    """Return keyword list for a sport, preferring config overrides over defaults.

    If ``sport`` is present in ``config_keywords`` the caller-supplied list is
    returned unchanged, allowing full replacement of the built-in set.  When
    the sport is absent from ``config_keywords`` the function falls back to
    ``DEFAULT_SPORT_KEYWORDS``.  An empty list is returned when the sport is
    unknown in both sources.

    Args:
        config_keywords: Caller-supplied keyword overrides keyed by sport slug.
        sport: Lowercase sport identifier, e.g. ``"nba"`` or ``"ufc"``.

    Returns:
        A list of lowercase keyword strings for the requested sport.
    """
    if sport in config_keywords:
        return config_keywords[sport]
    return DEFAULT_SPORT_KEYWORDS.get(sport, [])


def fuzzy_match_sport(
    title: str,
    question: str,
    allowed: set[str],
    keywords: dict[str, list[str]],
) -> str | None:
    """Return the first sport whose keywords appear in the combined market text.

    The function concatenates the lowercased ``title`` and ``question`` into a
    single search string and then iterates over ``allowed`` sports in sorted
    order so that the result is deterministic when keyword lists overlap across
    sports (EC-004).  The first sport for which any keyword is found as a
    substring of the search text is returned.

    Args:
        title: Market title string (case-insensitive).
        question: Market question string (case-insensitive).
        allowed: Set of sport slugs to consider, e.g. ``{"nba", "nfl"}``.
        keywords: Mapping of sport slug to list of lowercase keyword strings.
            Typically produced by calling :func:`get_sport_keywords` for each
            sport, or passed directly as ``DEFAULT_SPORT_KEYWORDS``.

    Returns:
        The matching sport slug, or ``None`` if no keyword matched.
    """
    search_text = title.lower() + " " + question.lower()
    for sport in sorted(allowed):
        for keyword in keywords.get(sport, []):
            if keyword in search_text:
                return sport
    return None
