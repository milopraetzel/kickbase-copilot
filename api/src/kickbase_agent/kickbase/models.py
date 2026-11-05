"""Pydantic models for Kickbase API responses."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from enum import IntEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Position(IntEnum):
    GOALKEEPER = 1
    DEFENDER = 2
    MIDFIELDER = 3
    STRIKER = 4

    @classmethod
    def from_api(cls, value: int) -> Position:
        return cls(value)


def _integral_status(value: Any, *, invalid: int | None) -> int | None:
    """Parse API status without truncating fractions or accepting bools."""
    if value is None or value == "" or isinstance(value, bool):
        return None if value is None or value == "" else invalid
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else invalid
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return invalid
    return invalid


class Player(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    first_name: str
    last_name: str
    position: Position
    market_value: int
    market_value_trend: int = 0  # mvt: beobachtet 0/1/2, vermutlich mehrtägig (L-118)
    points: int = 0
    team_id: str
    # Spielerfoto-Pfad (`pim`), relativ zum Kickbase-CDN kickbase.b-cdn.net
    # (live verifiziert 20.08.26 — dieselbe Quelle nutzt z. B. kbstats.de;
    # seit dem Member-Abo 23.08.26 echte Porträts statt Trikot-Dummys).
    image_path: str = ""
    # Kickbase-Member-Startelf-Stufe (`prob`, nur mit Member-Abo im Payload).
    # Skala-HYPOTHESE aus Live-Kalibrierung 23.08.26: 1 = sicherer Starter
    # (Stamm-TW Schwäbe), 2 = gesetzt, 5 = Rotation/Bank. Bis zur Validierung
    # gegen echte SP1-Aufstellungen NUR Anzeige/Zweitquelle — LigaInsider
    # bleibt die primäre Startelf-Wahrheit. None = Feld nicht geliefert.
    lineup_prob: int | None = None
    # Rohes Kickbase-Fitnessfeld `st`: 0 = ausdrücklich fit, >0 = Problem,
    # None = nicht geliefert. Fehlend darf nie still zu "fit" werden.
    fitness_status: int | None = None
    # 24-h-Marktwertänderung in € (`tfhmvt`) — der Pfeil der Kickbase-App.
    mv_change_24h: int = 0
    mv_change_24h_observed: bool | None = None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Player:
        prob = raw.get("prob")
        lineup_prob = _integral_status(prob, invalid=0)
        fitness_status = _integral_status(raw.get("st"), invalid=None)
        return cls(
            id=str(raw["i"]),
            first_name=raw.get("fn", ""),
            # Detail und Markt verwenden nachweislich verschiedene Namenskeys.
            last_name=raw["n"] if "n" in raw else raw["ln"],
            position=Position.from_api(raw["pos"]),
            market_value=raw["mv"],
            market_value_trend=raw.get("mvt", 0),
            points=raw.get("p", raw.get("tp", 0)),
            team_id=str(raw["tid"]) if "tid" in raw else "",
            image_path=str(raw.get("pim") or ""),
            lineup_prob=lineup_prob,
            fitness_status=fitness_status,
            mv_change_24h=_integral_status(raw.get("tfhmvt"), invalid=None) or 0,
            mv_change_24h_observed=_integral_status(raw.get("tfhmvt"), invalid=None) is not None,
        )


class SquadPlayer(BaseModel):
    player: Player
    # `st` ist der FITNESS-Status (0 = fit, 2 = angeschlagen, … + Text `stxt`
    # im Detail-Endpoint) — live bewiesen 23.08.26 (Bernardo st=2 bei Achilles-
    # Problemen; lst=1 bei allen 15 Kaderspielern, taugt also nicht als
    # Startelf-Flag). is_starter war daher nie korrekt befüllbar und bleibt
    # als totes Anzeige-Feld konstant False; die echte Startelf kommt aus dem
    # lineup-Endpoint bzw. decision/lineup.
    is_starter: bool = False
    fitness_status: int = 0
    #: `lo` — Platz in der AKTUELL gesetzten Aufstellung (0 = TW … 10), None =
    #: nicht aufgestellt. Anders als `is_starter` ist das die echte Auskunft:
    #: Sie kommt aus derselben Antwort und sagt, wer am Spieltag zaehlt. Bis
    #: 27.08.2026 las nur das MCP-Tool das Feld roh mit; der Elf-Luecken-
    #: Waechter braucht es ebenfalls, und zwei Rohpfade auf dasselbe Feld waeren
    #: genau die Sorte Doppelung, die spaeter auseinanderlaeuft.
    lineup_order: int | None = None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> SquadPlayer:
        lo = raw.get("lo")
        return cls(
            player=Player.from_api(raw),
            is_starter=False,
            fitness_status=int(raw.get("st", 0) or 0),
            lineup_order=None if lo is None else int(lo),
        )


#: Ab diesem Listing-Alter gilt ein Manager-Listing als „Zombie": Live-Probe
#: 23.08. — Kickbase nimmt auf lange abgelaufene Manager-Listings KEINE Offers
#: mehr an (HTTP 500 auf jedes Gebot); die „Schnäppchen" inaktiver Manager im
#: Markt sind Anzeige-Müll. Frische Manager-Listings (< 48 h) sind normal bietbar.
ZOMBIE_LISTING_AGE = timedelta(hours=48)


class MarketPlayer(BaseModel):
    player: Player
    price: int
    expires_at: datetime
    # Verkäufer aus `u.i` = Manager-Listing (live geprobt 20.08.); leer =
    # Kickbase-Listing. Nur Kickbase-Listings tragen einen `exs`-Countdown;
    # bei Manager-Listings ist `dt` der Listing-ZEITPUNKT (Vergangenheit),
    # expires_at stempelt dort also das Listing-Alter, keinen Ablauf.
    seller_id: str = ""
    # Anzahl abgegebener Gebote (`ofc`) — Grundlage der Gebots-Erwartung
    # (25.08.): Bei Kickbase-Listings entscheidet das höchste Gebot, und die
    # Höhe fremder Gebote nennt die API nicht — nur ihre Anzahl.
    offer_count: int = 0

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> MarketPlayer:
        # v4 nutzt prc + dt (ISO-Ablaufzeitpunkt); Varianten: exs (Restsekunden),
        # ältere Fixtures price + exp (Epoch-Timestamp).
        price = raw.get("prc", raw.get("price"))
        if price is None:
            raise KeyError("Markt-Preis fehlt (weder 'prc' noch 'price' vorhanden)")
        # NUR ein echter Countdown (exs > 0) wählt den Countdown-Pfad —
        # Manager-Listings tragen exs: 0 (live 20.08.), und `now + 0` würde
        # sie bei jedem Abruf „frisch" stempeln: der Zombie-Filter wäre für
        # genau die Listings blind, für die er gebaut wurde (Codex-Re-Check
        # 24.08., P2). exs 0/fehlend fällt auf `dt` (Listing-Zeitpunkt) zurück.
        if int(raw.get("exs", 0) or 0) > 0:
            expires_at = datetime.now(UTC) + timedelta(seconds=int(raw["exs"]))
        elif isinstance(raw.get("dt"), str):
            expires_at = datetime.fromisoformat(raw["dt"].replace("Z", "+00:00"))
        elif "exp" in raw:
            expires_at = datetime.fromtimestamp(raw["exp"])
        else:
            expires_at = datetime.now(UTC)
        return cls(
            player=Player.from_api(raw),
            price=int(price),
            expires_at=expires_at,
            seller_id=str((raw.get("u") or {}).get("i") or ""),
            offer_count=int(raw.get("ofc") or 0),
        )

    def is_zombie(self, now: datetime) -> bool:
        """Unkaufbares Manager-Listing (Listing-Alter > 48 h) — nie Gebote drauf.

        Kickbase-Listings rollen aus (via `exs` immer Zukunfts-Ablauf) und sind
        nie Zombies; die seller_id-Bedingung deckt zusätzlich ab, falls ein
        Manager-Listing doch einmal einen Countdown trägt.
        """
        return bool(self.seller_id) and now - self.expires_at > ZOMBIE_LISTING_AGE


def _int(value: Any, default: int = 0) -> int:
    """Robuste int-Konvertierung für API-Zahlen (None/""/Müll → default)."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _iso_dt(value: Any) -> datetime | None:
    """ISO-Zeitstempel der API ("2026-01-05T20:17:19Z") → aware datetime, sonst None.

    Ohne Zeitzone im String wird UTC angenommen (Kickbase liefert durchgehend
    UTC), damit Vergleiche mit `datetime.now(UTC)` nie an naiv/aware scheitern.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class FormEntry(BaseModel):
    """Ein Segment der 5er-Formleiste (`ph` im Player-Detail): gespielt? + Punkte."""

    model_config = ConfigDict(frozen=True)

    played: bool
    points: int


class MatchdaySummary(BaseModel):
    """Ein Eintrag der Spieltags-Übersicht `mdsum` im Player-Detail (Spieltags-Karte
    der App: „Spieltag 32 · Logo 3:3 Logo"). Team-Logos sind CDN-Pfade."""

    model_config = ConfigDict(frozen=True)

    day: int
    home_team_id: str
    away_team_id: str
    home_goals: int = 0
    away_goals: int = 0
    kickoff: datetime | None = None
    is_current: bool = False
    home_img: str = ""
    away_img: str = ""
    # Spiel-Status `mdst` — ANNAHME (Probe 23.08.26): 0 = noch nicht gespielt;
    # bereits gespielte Spiele trugen Tore und ein vergangenes `md`. Welche
    # Werte ≠ 0 genau „läuft" bzw. „beendet" bedeuten, ist nicht verifiziert.
    status: int = 0

    def is_played(self, now: datetime | None = None) -> bool:
        """Gespielt (oder laufend): Anpfiff liegt in der Vergangenheit ODER `mdst` ≠ 0.

        Beide Signale, weil keines allein verifiziert ist: ein vergangenes `md`
        ist das robuste Zeit-Kriterium, `mdst` ≠ 0 fängt Spiele ab, deren
        Anpfiff fehlt (md None) oder deren Status die API vorzieht.
        """
        if self.status != 0:
            return True
        if self.kickoff is None:
            return False
        return self.kickoff <= (now or datetime.now(UTC))

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> MatchdaySummary:
        return cls(
            day=_int(raw.get("day")),
            home_team_id=str(raw.get("t1") or ""),
            away_team_id=str(raw.get("t2") or ""),
            home_goals=_int(raw.get("t1g")),
            away_goals=_int(raw.get("t2g")),
            kickoff=_iso_dt(raw.get("md")),
            is_current=bool(raw.get("cur", False)),
            home_img=str(raw.get("t1im") or ""),
            away_img=str(raw.get("t2im") or ""),
            status=_int(raw.get("mdst")),
        )


class TeamProfilePlayer(BaseModel):
    """Ein Spieler aus dem Team-Profil (`/competitions/1/teams/{tid}/teamprofile`).

    Live geprobt 23.08.26 (Olise, Bayern): i, n, lst, st, pos, mv, mvt, ap, iotm, ofc,
    tid, sdmvt (7-Tage-MW-Änderung), mvgl, pim, prob. Kein tp/g/a — dafür Detail.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    last_name: str
    position: Position
    team_id: str
    market_value: int = 0
    market_value_trend: int = 0
    mv_change_7d: int = 0          # sdmvt
    mv_gain_loss: int = 0          # mvgl
    avg_points: int = 0            # ap
    lineup_prob: int | None = None  # prob 1..5 (Member)
    fitness_status: int = 0        # st
    on_market: bool = False        # iotm
    #: `ofc` — KEIN Bieterzähler: zählt nur die EIGENEN Gebote auf dieses
    #: Listing (Messung 26.08.2026: 49/51 Listings 0, die zwei mit 1 waren
    #: genau unsere). Fremde Gebote sind bei Kickbase unsichtbar. L-079.
    offer_count: int = 0           # ofc
    image_path: str = ""

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> TeamProfilePlayer:
        prob = raw.get("prob")
        return cls(
            id=str(raw["i"]),
            last_name=str(raw.get("n") or raw.get("ln") or ""),
            position=Position.from_api(int(raw.get("pos", 1) or 1)),
            team_id=str(raw.get("tid", "")),
            market_value=int(raw.get("mv", 0) or 0),
            market_value_trend=int(raw.get("mvt", 0) or 0),
            mv_change_7d=int(raw.get("sdmvt", 0) or 0),
            mv_gain_loss=int(raw.get("mvgl", 0) or 0),
            avg_points=int(raw.get("ap", 0) or 0),
            lineup_prob=int(prob) if isinstance(prob, int | float) else None,
            fitness_status=int(raw.get("st", 0) or 0),
            on_market=bool(raw.get("iotm", False)),
            offer_count=int(raw.get("ofc", 0) or 0),
            image_path=str(raw.get("pim") or ""),
        )


class TeamProfile(BaseModel):
    """Team-Profil: Club-Meta + alle Spieler (nur verifizierte Felder: tid, tn, tim, it)."""

    model_config = ConfigDict(frozen=True)

    team_id: str
    name: str
    image_path: str = ""
    players: list[TeamProfilePlayer] = Field(default_factory=list)

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> TeamProfile:
        return cls(
            team_id=str(raw.get("tid", "")),
            name=str(raw.get("tn", "")),
            image_path=str(raw.get("tim") or ""),
            players=[TeamProfilePlayer.from_api(it) for it in raw.get("it", []) if it.get("i")],
        )


class PlayerDetail(BaseModel):
    """Spieler-Detail `GET /v4/leagues/{lid}/players/{pid}` mit den Member-Stats.

    Feld-Semantik live verifiziert (Probe 23.08.26, Olise 8329 gegen den
    App-Screenshot, s. Spec cockpit-v4 §1): tp Punkte, ap Ø Punkte, g Tore,
    a Vorlagen, ismc Einsätze, smdc Einwechslungen, smc S11, sec gespielte
    Sekunden, y/r Karten, st/stxt/dt Fitness, prob Startelf-Stufe, ph letzte
    5 Spiele, mdsum Spieltags-Übersicht. Der Detail-Endpoint liefert den
    Nachnamen als `ln` (Squad/Markt: `n`). Die Felder cv, stud, pes, cs, sl,
    opl, iposl sind ungeklärt und werden bewusst NICHT modelliert.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    first_name: str = ""
    last_name: str = ""
    shirt_number: int = 0
    team_id: str = ""
    team_name: str = ""
    position: Position
    market_value: int = 0
    market_value_trend: int = 0  # mvt: beobachtet 0/1/2, vermutlich mehrtägig (L-118)
    mv_change_24h: int = 0  # tfhmvt: „Seit gestern" in €
    # Startelf-Stufe der Member-Legende (1 Sicher … 5 Ausgeschlossen); None = fehlt
    lineup_prob: int | None = None
    fitness_status: int = 0  # st: 0 = Fit, >0 = angeschlagen/verletzt
    fitness_text: str = ""  # stxt: nur bei st>0 (englisch)
    status_since: datetime | None = None  # dt: „Fit · 05.01.2026"
    total_points: int = 0  # tp
    avg_points: int = 0  # ap
    goals: int = 0  # g
    assists: int = 0  # a
    appearances: int = 0  # ismc (Einsätze)
    sub_appearances: int = 0  # smdc (Einwechslungen, „/1")
    s11: int = 0  # smc (Startelf-Einsätze, „23×")
    seconds_played: int = 0  # sec
    yellow: int = 0  # y
    red: int = 0  # r
    last_points: list[FormEntry] = Field(default_factory=list)  # ph, chronologisch
    matchdays: list[MatchdaySummary] = Field(default_factory=list)  # mdsum
    image_path: str = ""  # pim (CDN-Pfad Porträt)
    team_image: str = ""  # tim (CDN-Pfad Club-Logo, SVG)
    partner: str = ""  # plpt (Datenpartner, „Ligainsider")
    partner_image: str = ""  # plpim (CDN-Pfad Partner-Logo)
    owner_id: str = ""  # oui ("0" = frei)

    @property
    def avg_minutes(self) -> int:
        """Ø Minuten je Einsatz (App: „80′") = sec/60/ismc, gerundet; 0 ohne Einsatz."""
        if self.appearances <= 0:
            return 0
        return round(self.seconds_played / 60 / self.appearances)

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> PlayerDetail:
        prob = raw.get("prob")
        ph = raw.get("ph") if isinstance(raw.get("ph"), list) else []
        mdsum = raw.get("mdsum") if isinstance(raw.get("mdsum"), list) else []
        return cls(
            id=str(raw["i"]),
            first_name=str(raw.get("fn") or ""),
            last_name=str(raw.get("ln") or raw.get("n") or ""),
            shirt_number=_int(raw.get("shn")),
            team_id=str(raw.get("tid") or ""),
            team_name=str(raw.get("tn") or ""),
            position=Position.from_api(int(raw["pos"])),
            market_value=_int(raw.get("mv")),
            market_value_trend=_int(raw.get("mvt")),
            mv_change_24h=_int(raw.get("tfhmvt")),
            lineup_prob=int(prob) if isinstance(prob, int | float) else None,
            fitness_status=_int(raw.get("st")),
            fitness_text=str(raw.get("stxt") or ""),
            status_since=_iso_dt(raw.get("dt")),
            total_points=_int(raw.get("tp")),
            avg_points=_int(raw.get("ap")),
            goals=_int(raw.get("g")),
            assists=_int(raw.get("a")),
            appearances=_int(raw.get("ismc")),
            sub_appearances=_int(raw.get("smdc")),
            s11=_int(raw.get("smc")),
            seconds_played=_int(raw.get("sec")),
            yellow=_int(raw.get("y")),
            red=_int(raw.get("r")),
            last_points=[
                FormEntry(played=bool(e.get("hp", False)), points=_int(e.get("p")))
                for e in ph
                if isinstance(e, dict)
            ],
            matchdays=[MatchdaySummary.from_api(m) for m in mdsum if isinstance(m, dict)],
            image_path=str(raw.get("pim") or ""),
            team_image=str(raw.get("tim") or ""),
            partner=str(raw.get("plpt") or ""),
            partner_image=str(raw.get("plpim") or ""),
            # "0" = frei (Kickbase-Spieler) — auch eine numerische 0 bleibt "0".
            owner_id=str(raw["oui"]) if raw.get("oui") is not None else "",
        )


class MatchdayPerformance(BaseModel):
    """Punkte + Minuten eines Spielers an einem konkreten Spieltag."""

    model_config = ConfigDict(frozen=True)

    season: str
    matchday: int
    points: int
    minutes: int
    match_date: datetime | None = None
    # None bei alten DB-/Cachezeilen: ihre Nullwerte sind nicht als Label belegt.
    points_observed: bool | None = None
    minutes_observed: bool | None = None
    is_finished: bool | None = None
    captured_at: datetime | None = None

    @staticmethod
    def _minutes(raw: Any) -> int:
        value = MatchdayPerformance._observed_minutes(raw)
        return value if value is not None else 0

    @staticmethod
    def _observed_minutes(raw: Any) -> int | None:
        if isinstance(raw, bool) or raw is None:
            return None
        text = str(raw).strip()
        if not re.fullmatch(r"\d+['’]?", text):
            return None
        return int(text.rstrip("'’"))

    @classmethod
    def from_api(cls, season: str, raw: dict[str, Any]) -> MatchdayPerformance:
        match_date = None
        if raw.get("md"):
            match_date = datetime.fromisoformat(str(raw["md"]).replace("Z", "+00:00"))
        return cls(
            season=season,
            matchday=int(raw.get("day", 0)),
            points=_integral_status(raw.get("p"), invalid=None) or 0,
            minutes=cls._minutes(raw.get("mp")),
            match_date=match_date,
            points_observed=_integral_status(raw.get("p"), invalid=None) is not None,
            minutes_observed=cls._observed_minutes(raw.get("mp")) is not None,
        )


class MarketValueSnapshot(BaseModel):
    """Ein Tageswert aus der Marktwert-Historie eines Spielers."""

    model_config = ConfigDict(frozen=True)

    date: datetime
    value: int

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> MarketValueSnapshot:
        stamp = raw.get("dt", raw.get("d"))
        if isinstance(stamp, int | float):
            if stamp > 1e12:  # Millisekunden-Epoche
                date = datetime.fromtimestamp(stamp / 1000)
            elif stamp > 1e9:  # Sekunden-Epoche
                date = datetime.fromtimestamp(stamp)
            else:  # Tage seit Epoche
                date = datetime.fromtimestamp(stamp * 86400)
        else:
            date = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        return cls(date=date, value=int(raw.get("mv", raw.get("v", 0))))


class Fixture(BaseModel):
    """Ein Bundesliga-Spiel mit Team-IDs und Wettquoten (für das Matchup-Signal)."""

    model_config = ConfigDict(frozen=True)

    matchday: int
    home_team_id: str
    away_team_id: str
    home_win_odds: float | None = None
    draw_odds: float | None = None
    away_win_odds: float | None = None
    kickoff: datetime | None = None
    is_finished: bool = False

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Fixture:
        odds = raw.get("bo") or {}
        kickoff = None
        if raw.get("dt"):
            kickoff = datetime.fromisoformat(str(raw["dt"]).replace("Z", "+00:00"))
        return cls(
            matchday=int(raw.get("day", 0)),
            home_team_id=str(raw.get("t1", "")),
            away_team_id=str(raw.get("t2", "")),
            home_win_odds=odds.get("o1"),
            draw_odds=odds.get("ox"),
            away_win_odds=odds.get("o2"),
            kickoff=kickoff,
            is_finished=int(raw.get("st", 0)) == 2,
        )


class League(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    budget: int = 0

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> League:
        # v4 nutzt `i`/`n`; ältere Fixtures `id`/`name`.
        raw_id = raw.get("i", raw.get("id"))
        if raw_id is None:
            raise KeyError("Liga-ID fehlt (weder 'i' noch 'id' vorhanden)")
        return cls(
            id=str(raw_id),
            name=raw.get("n") or raw.get("name") or "",
            budget=raw.get("b", 0),
        )


class RankingEntry(BaseModel):
    """Ein Manager in der Liga-Tabelle (für Dashboard-Rang + Rivalen-Watch)."""

    model_config = ConfigDict(frozen=True)

    user_id: str
    name: str
    rank: int
    points: int
    team_value: int = 0
    # Vorsaison (psp/pspts) — vor Saisonstart die einzigen aussagekräftigen Zahlen,
    # denn sp/tv liefert die API dann als 0.
    prev_rank: int = 0
    prev_points: int = 0
    # Manager-Profilbild (`uim`, live geprobt 24.08.: user/<hash>.jpeg) —
    # relativ zum selben CDN wie Spielerfotos (pim).
    image_path: str = ""

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> RankingEntry:
        return cls(
            user_id=str(raw.get("i", "")),
            name=str(raw.get("n", "")).strip(),
            rank=int(raw.get("spl", 0)),
            points=int(raw.get("sp", 0)),
            team_value=int(raw.get("tv", 0) or 0),
            image_path=str(raw.get("uim") or ""),
            prev_rank=int(raw.get("psp", 0) or 0),
            prev_points=int(raw.get("pspts", 0) or 0),
        )


class LoginResponse(BaseModel):
    token: str = Field(alias="tkn")
    user_id: str = Field(alias="u")
    leagues: list[League] = Field(default_factory=list)
