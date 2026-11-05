"""Signal functions producing Signal objects from raw data."""

from __future__ import annotations

import statistics
from datetime import date

from kickbase_agent.db.models import PlayerHistory
from kickbase_agent.intel.models import Signal
from kickbase_agent.kickbase.models import Fixture, MatchdayPerformance

# Kickbase-Meta: Rohpunkte (konstante Grundlast) schlagen tor-abhängige Ausreißer.
# Der Rohpunkte-Scout bekommt daher das höchste Gewicht in der Punkte-Bewertung.
# TREND 0.15 → 0.25 (31.08., Momentum-Doktrin — Emils Auftrag „Steiger kaufen"):
# Die Engine mittelt gewichtet über die Summe (1.10), effektiv Basis 36,4 % /
# Form 22,7 % / Gegner 18,2 % / Trend 22,7 %. Bewusst OHNE Renormierung der
# anderen Gewichte: Der Elf-Score schließt den Trend aus und rechnet weiter
# exakt auf 0.40/0.25/0.20 — die Aufstellung bleibt von der Börsen-Doktrin
# unberührt (Emils Punkte-Primat). Rückrunden-Review: Gewicht wieder senken,
# wenn Punkte wichtiger werden als Marktwert.
BASE_POINTS_WEIGHT = 0.40
FORM_WEIGHT = 0.25
MATCHUP_WEIGHT = 0.20
TREND_WEIGHT = 0.25

# Kickbase-Spieltagspunkte liegen grob bei 0–300; ein Formsprung von ±60 Punkten
# gegenüber dem Saisonschnitt sättigt den Score.
_FORM_SATURATION = 120.0

# Eine verlässliche Punkte-Grundlast von ~100 Pkt (25%-Perzentil) sättigt den Score.
_BASE_SATURATION = 100.0


def current_and_last_season(today: date | None = None) -> tuple[str, str]:
    """Saisonfenster der Punkte-Signale: aktuelle + letzte Saison (Format "2025/2026").

    Bundesliga-Saisons laufen August–Mai; ab Juli zählt das neue Saisonjahr.
    Ältere Saisons sagen über die heutige Rolle eines Spielers nichts mehr aus.
    """
    today = today or date.today()
    start_year = today.year if today.month >= 7 else today.year - 1
    return (f"{start_year}/{start_year + 1}", f"{start_year - 1}/{start_year}")


def _played_in_window(
    performances: list[MatchdayPerformance], today: date | None
) -> list[MatchdayPerformance]:
    """Nur GESPIELTE Einsätze (Minuten > 0) aus aktueller + letzter Saison.

    Review-Fund: Der Performance-Endpoint liefert auch ZUKÜNFTIGE Spieltage
    (0 Punkte / 0 Minuten) — ungefiltert zogen sie jedes Form-Signal auf ~0.
    Bankspiele ohne Einsatz fliegen ebenfalls raus (0 Punkte ≠ schlechte Form).
    """
    window = set(current_and_last_season(today))
    return [p for p in performances if p.minutes > 0 and p.season in window]


def _shrink(score: float, n: int, full_n: int = 3) -> float:
    """Kleine Stichprobe → Score Richtung 0.5 ziehen (n gespielte Spiele von full_n).

    Einfaches Shrinkage: score = 0.5 + (score − 0.5) · n/full_n. Zwei starke Spiele
    sind ein Hinweis, kein Beweis — der Score nähert sich Neutralität statt Extremen.
    """
    if n >= full_n:
        return score
    return 0.5 + (score - 0.5) * n / full_n


def base_points_signal(
    performances: list[MatchdayPerformance],
    min_games: int = 3,
    today: date | None = None,
) -> Signal:
    """Verlässliche Punkte-Grundlast — das untere Quartil der Spieltagspunkte.

    Base-Point-Sammler (Sechser, IV der Topteams) liefern konstant hohe Punkte
    ohne Torbeteiligung → hohe Untergrenze. Tor-abhängige Spieler haben eine
    niedrige Untergrenze mit seltenen Ausreißern. Es zählen nur GESPIELTE Spiele
    (Minuten > 0) aus aktueller + letzter Saison; unter `min_games` Einsätzen wird
    der Score Richtung 0.5 geschrumpft (dünne Datenlage ist kein starkes Signal).
    """
    played = [p.points for p in _played_in_window(performances, today)]
    if not played:
        return Signal(
            name="base_points",
            score=0.5,
            weight=BASE_POINTS_WEIGHT,
            reason="Keine Einsätze in aktueller/letzter Saison.",
        )
    # 25%-Perzentil = die verlässliche Untergrenze (schlechte Tage zählen mit).
    floor = (
        statistics.quantiles(played, n=4)[0] if len(played) >= 4 else min(played)
    )
    score = _shrink(max(0.0, min(1.0, floor / _BASE_SATURATION)), len(played), min_games)
    if len(played) < min_games:
        reason = f"Nur {len(played)} Einsätze — Grundlast {floor:.0f} Pkt, vorsichtig gewichtet."
    elif score > 0.6:
        reason = f"Verlässlicher Punktesammler: ≥ {floor:.0f} Pkt in 3 von 4 Spielen."
    elif score < 0.3:
        reason = f"Unbeständig/tor-abhängig: Grundlast nur {floor:.0f} Pkt."
    else:
        reason = f"Solide Grundlast: {floor:.0f} Pkt (unteres Quartil)."
    return Signal(name="base_points", score=score, weight=BASE_POINTS_WEIGHT, reason=reason)


def form_signal(
    performances: list[MatchdayPerformance],
    lookback: int = 3,
    today: date | None = None,
) -> Signal:
    """Form = Ø der letzten `lookback` GESPIELTEN Spiele vs. Schnitt des Saisonfensters.

    Ungespielte Einträge (zukünftige Spieltage, Bank ohne Einsatz) werden vorab
    entfernt — sonst zieht jeder noch nicht gespielte Spieltag mit 0 Punkten die
    Form aller Spieler systematisch auf ~0 (Review-Fund). Bei unter 3 gespielten
    Spielen wird der Score Richtung 0.5 geschrumpft.
    """
    played = _played_in_window(performances, today)
    if len(played) < 2:
        return Signal(
            name="form",
            score=0.5,
            weight=FORM_WEIGHT,
            reason="Nicht genug Punkte-Historie für Form-Analyse.",
        )
    recent = played[-lookback:]
    recent_avg = sum(p.points for p in recent) / len(recent)
    overall_avg = sum(p.points for p in played) / len(played)

    delta = recent_avg - overall_avg
    score = _shrink(max(0.0, min(1.0, 0.5 + delta / _FORM_SATURATION)), len(played))

    if score > 0.7:
        reason = f"Starker Aufwärtstrend: Ø {recent_avg:.0f} Pkt in letzten {lookback} Spielen."
    elif score < 0.3:
        reason = f"Formkrise: nur Ø {recent_avg:.0f} Pkt zuletzt."
    else:
        reason = f"Form stabil bei Ø {recent_avg:.0f} Pkt."
    return Signal(name="form", score=score, weight=FORM_WEIGHT, reason=reason)


def matchup_signal(
    player_team_id: str,
    fixtures: list[Fixture],
    team_names: dict[str, str] | None = None,
) -> Signal:
    """Score = margen-bereinigte Sieg-Wahrscheinlichkeit des Teams im nächsten Spiel.

    Aus den Wettquoten (o1/ox/o2): Favorit → hoher Score (gute Punkte-Aussicht),
    Außenseiter → niedriger Score.
    """
    team_names = team_names or {}
    next_fixture = next(
        (
            f
            for f in sorted(fixtures, key=lambda x: x.matchday)
            if not f.is_finished
            and player_team_id in (f.home_team_id, f.away_team_id)
        ),
        None,
    )
    neutral_reason = "Kein kommendes Spiel mit Quoten gefunden."
    if next_fixture is None:
        return Signal(name="matchup", score=0.5, weight=MATCHUP_WEIGHT, reason=neutral_reason)

    is_home = next_fixture.home_team_id == player_team_id
    o1, ox, o2 = next_fixture.home_win_odds, next_fixture.draw_odds, next_fixture.away_win_odds
    if not o1 or not ox or not o2:
        return Signal(name="matchup", score=0.5, weight=MATCHUP_WEIGHT, reason=neutral_reason)

    implied = (1 / o1, 1 / ox, 1 / o2)
    total = sum(implied)
    win_prob = (implied[0] if is_home else implied[2]) / total
    # Zentrierung (Review-Fund): score == win_prob bestrafte AUSGEGLICHENE Spiele —
    # beide Teams lagen bei ~0.35 und wurden künstlich runtergezogen. Neutralpunkt
    # ist die Drei-Wege-Gleichverteilung (1/3): offenes Spiel → 0.50, klarer
    # Favorit (~75 %) → ~1.0, klarer Außenseiter (~10 %) → ~0.2.
    score = max(0.0, min(1.0, 0.5 + (win_prob - 1 / 3) * 1.2))

    opponent_id = next_fixture.away_team_id if is_home else next_fixture.home_team_id
    opponent = team_names.get(opponent_id, "Gegner")
    venue = "H" if is_home else "A"
    # Begründung (Review-Fund): Der Text nennt die ECHTE Siegwahrscheinlichkeit
    # aus den Quoten — NICHT den zentrierten Score (der machte aus 33 % Siegchance
    # „50 %"). Der Score selbst bleibt die zentrierte Bewertung von oben.
    win_pct = win_prob * 100
    if score > 0.6:
        reason = f"Favorit gegen {opponent} ({venue}, Siegchance {win_pct:.0f}%)."
    elif score < 0.3:
        reason = f"Außenseiter gegen {opponent} ({venue}, Siegchance {win_pct:.0f}%)."
    else:
        reason = (
            f"Offenes Spiel gegen {opponent} ({venue}, Siegchance {win_pct:.0f}% — "
            f"ausgeglichen, neutral gewertet)."
        )
    return Signal(name="matchup", score=score, weight=MATCHUP_WEIGHT, reason=reason)


def fixture_horizon_multiplier(
    player_team_id: str,
    fixtures: list[Fixture],
    *,
    horizon: int = 4,
) -> float:
    """Multiplikator [0.85, 1.15] für die 4-Spieltage-Horizontprojektion aus Wettquoten.

    Fehlen Quoten (z. B. späte Spieltage im Horizont oder Test-Fixtures ohne Quoten),
    geht der jeweilige Spieltag neutral mit Faktor 1.0 ein.
    """
    if not player_team_id or not fixtures or horizon <= 0:
        return 1.0
    upcoming = [
        f
        for f in sorted(fixtures, key=lambda x: getattr(x, "matchday", 0))
        if not getattr(f, "is_finished", False)
        and player_team_id in (getattr(f, "home_team_id", None), getattr(f, "away_team_id", None))
    ][:horizon]
    if not upcoming:
        return 1.0
    multipliers: list[float] = []
    for f in upcoming:
        o1 = getattr(f, "home_win_odds", None)
        ox = getattr(f, "draw_odds", None)
        o2 = getattr(f, "away_win_odds", None)
        if not o1 or not ox or not o2 or o1 <= 0 or ox <= 0 or o2 <= 0:
            multipliers.append(1.0)
            continue
        implied = (1 / o1, 1 / ox, 1 / o2)
        total = sum(implied)
        is_home = getattr(f, "home_team_id", None) == player_team_id
        win_prob = (implied[0] if is_home else implied[2]) / total
        mult = max(0.85, min(1.15, 1.0 + (win_prob - 1 / 3) * 0.35))
        multipliers.append(mult)
    while len(multipliers) < horizon:
        multipliers.append(1.0)
    return sum(multipliers) / len(multipliers)


def trend_signal(history: list[PlayerHistory]) -> Signal:
    """Score based on market-value slope over the last ≤7 days."""
    if len(history) < 2:
        return Signal(
            name="trend",
            score=0.5,
            weight=TREND_WEIGHT,
            reason="Kein Marktwert-Verlauf verfügbar.",
        )
    sorted_hist = sorted(history, key=lambda h: h.recorded_at)[-7:]
    first = sorted_hist[0].market_value
    last = sorted_hist[-1].market_value
    if first == 0:
        return Signal(
            name="trend", score=0.5, weight=TREND_WEIGHT, reason="Startwert 0, Trend unklar."
        )
    pct_change = (last - first) / first
    # ±5% over the window saturates the score
    score = max(0.0, min(1.0, 0.5 + pct_change * 10.0))

    diff_m = (last - first) / 1_000_000
    if score > 0.7:
        reason = f"Marktwert steigt stark (+{diff_m:.2f}M in 7 Tagen)."
    elif score < 0.3:
        reason = f"Marktwert fällt ({diff_m:+.2f}M in 7 Tagen)."
    else:
        reason = f"Marktwert stabil ({diff_m:+.2f}M in 7 Tagen)."
    return Signal(name="trend", score=score, weight=TREND_WEIGHT, reason=reason)


# Momentum-Doktrin (31.08.): Tages-Trend als Wendepunkt-Marker. Bewusst KEIN
# Engine-Signal — der Collector hängt ihn erst NACH der Score-Berechnung an
# (Anzeige + Verkaufs-Policy Pass 3); das Signal-Modell verlangt trotzdem ein
# Gewicht > 0, in ein gewichtetes Mittel geht es aber nie ein.
TREND_1D_WEIGHT = 0.05


def trend_1d_signal(history: list[PlayerHistory]) -> Signal:
    """Jüngster Marktwert-Tagesschritt, gleiche Skala wie trend_signal (±5 % sättigt).

    Score > 0.5 = zuletzt gestiegen, ≤ 0.5 = zuletzt gefallen oder flach. Die
    Gewinnmitnahme (propose_sells Pass 3) liest daran den Wendepunkt ab: ein
    7-Tage-Steiger, dessen jüngster Schritt nicht mehr steigt, hat seinen
    Gipfel womöglich gesehen — verkauft wird zum vollen aktuellen Marktwert.
    """
    if len(history) < 2:
        return Signal(
            name="trend_1d",
            score=0.5,
            weight=TREND_1D_WEIGHT,
            reason="Kein Tages-Verlauf verfügbar.",
        )
    sorted_hist = sorted(history, key=lambda h: h.recorded_at)[-2:]
    prev, last = sorted_hist[0].market_value, sorted_hist[1].market_value
    if prev == 0:
        return Signal(
            name="trend_1d", score=0.5, weight=TREND_1D_WEIGHT, reason="Startwert 0, unklar."
        )
    pct = (last - prev) / prev
    score = max(0.0, min(1.0, 0.5 + pct * 10.0))
    return Signal(
        name="trend_1d",
        score=score,
        weight=TREND_1D_WEIGHT,
        reason=f"Letzter Tagesschritt {(last - prev) / 1_000:+.0f}k €.",
    )
