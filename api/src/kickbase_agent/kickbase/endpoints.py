"""URL path builders for the Kickbase API v4."""

LOGIN = "/v4/user/login"
REFRESH_TOKENS = "/v4/user/refreshtokens"
LEAGUES = "/v4/leagues"
LEAGUES_SELECTION = "/v4/leagues/selection"
COMPETITIONS = "/v4/competitions"
BUNDESLIGA_COMPETITION_ID = "1"


def competition_table(competition_id: str) -> str:
    return f"/v4/competitions/{competition_id}/table"


def competition_matchdays(competition_id: str) -> str:
    return f"/v4/competitions/{competition_id}/matchdays"


def league_me(league_id: str) -> str:
    return f"/v4/leagues/{league_id}/me"


def league_budget(league_id: str) -> str:
    return f"/v4/leagues/{league_id}/me/budget"


def league_ranking(league_id: str) -> str:
    return f"/v4/leagues/{league_id}/ranking"


def league_overview(league_id: str) -> str:
    return f"/v4/leagues/{league_id}/overview"


def squad(league_id: str) -> str:
    return f"/v4/leagues/{league_id}/squad"


def manager_squad(league_id: str, user_id: str) -> str:
    """Kader eines MITSPIELERS — live geprobt 28.08.2026 (12 Manager, Liga BASE).

    Antwort: {u, unm, uim, st, nps, it[]}; je Spieler pi, pn, tid, pos, mv, mvt,
    sdmvt, tfhmvt, mvgl, st, stl, p, ap, pim — und `lo` NUR fuer aufgestellte
    Spieler. Wer kein `lo` hat, sitzt auf der Bank: genau so unterscheidet auch
    unser eigener Kader (models.SquadPlayer.lineup_order).
    """
    return f"/v4/leagues/{league_id}/managers/{user_id}/squad"


def manager_dashboard(league_id: str, user_id: str) -> str:
    """Kopfzahlen eines Mitspielers: tv (Teamwert), tp (Punkte gesamt),
    ap (Spieltag), pl (Platz), prft (Gewinn). Ebenfalls live geprobt."""
    return f"/v4/leagues/{league_id}/managers/{user_id}/dashboard"


def market(league_id: str) -> str:
    return f"/v4/leagues/{league_id}/market"


def player_detail(league_id: str, player_id: str) -> str:
    return f"/v4/leagues/{league_id}/players/{player_id}"


def team_profile(team_id: str, competition_id: str = "1") -> str:
    """Alle Spieler eines Clubs (live geprobt 23.08.26: `it` mit prob/ap/mv/sdmvt/mvgl)."""
    return f"/v4/competitions/{competition_id}/teams/{team_id}/teamprofile"


def player_market_value_history(league_id: str, player_id: str) -> str:
    return f"/v4/leagues/{league_id}/players/{player_id}/marketvalue"


def player_performance(league_id: str, player_id: str) -> str:
    return f"/v4/leagues/{league_id}/players/{player_id}/performance"


# --- Schreib-Operationen (Phase 1) ---

BONUS_COLLECT = "/v4/bonus/collect"


def market_offers(league_id: str, player_id: str) -> str:
    """Gebot auf einen Marktspieler abgeben (POST)."""
    return f"/v4/leagues/{league_id}/market/{player_id}/offers"


def market_offer(league_id: str, player_id: str, offer_id: str) -> str:
    """Eigenes Gebot zurückziehen (DELETE)."""
    return f"/v4/leagues/{league_id}/market/{player_id}/offers/{offer_id}"


def market_offer_accept(league_id: str, player_id: str, offer_id: str) -> str:
    return f"/v4/leagues/{league_id}/market/{player_id}/offers/{offer_id}/accept"


def market_offer_decline(league_id: str, player_id: str, offer_id: str) -> str:
    return f"/v4/leagues/{league_id}/market/{player_id}/offers/{offer_id}/decline"


def market_delist(league_id: str, player_id: str) -> str:
    """Eigenen Spieler vom Markt nehmen (DELETE)."""
    return f"/v4/leagues/{league_id}/market/{player_id}"


def player_sell(league_id: str, player_id: str) -> str:
    """Sofortverkauf an Kickbase (POST)."""
    return f"/v4/leagues/{league_id}/market/{player_id}/sell"


def lineup(league_id: str) -> str:
    """Aufstellung lesen (GET) / setzen (POST)."""
    return f"/v4/leagues/{league_id}/lineup"
