"""Auto-Aufstellung setzen: die empfohlene beste 11 wirklich eintragen.

Läuft Fr 19:45 (nach dem LI-Lineup-Leak) + Sa/So 10:00 (späte Ausfälle). Der Write
geht durch den ActionExecutor; scharf nur mit AGENT_AUTO_LINEUP=1 (eigener Schalter,
armt keine Trades). Ohne Schalter: Dry-Run-Post „hätte gesetzt". Reicht der Kader
für keine volle Formation, wird eine Teil-Elf mit allen unterbringbaren Spielern
gesetzt (L-109) — jeder leere Platz kostet 100 Punkte, ein Bankspieler null.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from kickbase_agent.agent.executor import ActionExecutor, PlannedAction
from kickbase_agent.cli.plan import build_plan
from kickbase_agent.config import get_settings
from kickbase_agent.db.session import SessionLocal
from kickbase_agent.decision.lineup import MIN_SQUAD_SIZE, Lineup
from kickbase_agent.decision.lineup_service import recommend_lineup
from kickbase_agent.kickbase.factory import make_kickbase_client
from kickbase_agent.notify.discord_channel import DiscordChannel
from kickbase_agent.trading.trade_book import load_trade_book
from kickbase_agent.worker.kickoff_guard import matchday_deadline, running_matchday

_log = logging.getLogger(__name__)

_POS = {1: "TW", 2: "ABW", 3: "MIT", 4: "STU"}


def format_lineup_message(lineup: Lineup, status: str, armed: bool) -> str:
    """Discord-Text: was gesetzt wurde (oder im Dry-Run gesetzt würde)."""
    if not lineup.starters:
        return (
            "⚠️ **Auto-Aufstellung**: Kader reicht für keine gültige Formation "
            "— bisherige bleibt."
        )
    rows = []
    for pos in (1, 2, 3, 4):
        names = [p.name.split()[-1] for p in lineup.starters if p.position == pos]
        if names:
            rows.append(f"{_POS[pos]}  {', '.join(names)}")
    if lineup.open_slots:
        # Teil-Elf (L-109): die Lücke gehört in die Meldung — jeder leere Platz
        # kostet 100 Punkte, und nur ein Kauf vor der Frist schließt ihn.
        rows.append(
            f"--  {lineup.open_slots} von 11 Plätzen LEER (Kader {len(lineup.starters)})"
        )
    if status == "executed" and armed:
        head = f"⚽ **Aufstellung gesetzt · {lineup.formation}**"
        tail = "-# bis Anpfiff in der App überschreibbar"
    elif status == "unverified":
        head = f"🚨 **Aufstellung NICHT bestätigt · {lineup.formation}**"
        tail = (
            "-# Kickbase meldete Erfolg, aber die gelesene Aufstellung weicht ab — "
            "bitte SOFORT in der App prüfen"
        )
    elif status == "duplicate":
        head = f"⚽ **Aufstellung unverändert · {lineup.formation}**"
        tail = "-# identische Elf heute bereits gesetzt — nichts zu tun"
    elif status in ("skipped_dry_run", "refused") or not armed:
        head = f"⚽ **[DRY-RUN] Würde Aufstellung setzen · {lineup.formation}**"
        tail = "-# scharf schalten: AGENT_AUTO_LINEUP=1"
    else:
        head = f"⚠️ **Aufstellung-Set fehlgeschlagen ({status}) · {lineup.formation}**"
        tail = "-# bitte manuell in der App setzen"
    return head + "\n```\n" + "\n".join(rows) + "\n```\n" + tail


async def run_lineup_set_gated(channel: DiscordChannel) -> str:
    """Cron-Pfad (Fr 19:45 + Sa/So 10:00): erst die Spieltags-Deadline prüfen.

    Kickbase friert die Aufstellung beim Anpfiff des ERSTEN Spiels des Spieltags
    ein — läuft der Spieltag bereits, wäre ein Write für ihn wirkungslos (er
    gälte erst für den Folgespieltag) und wird übersprungen (Log, kein Fehler).
    Der Sa/So-10:00-Slot bleibt für Spieltage sinnvoll, die erst Sa/So starten
    (kein Fr-Spiel, englische Wochen). Ohne Fixture-Daten laufen wir wie bisher
    weiter (fail-open: der Write ist dann schlimmstenfalls wirkungslos, nie
    schädlich fürs Konto).
    """
    client, _auth = await make_kickbase_client()
    fixtures = await client.get_matchdays()
    md = running_matchday(fixtures, datetime.now(UTC))
    if md is not None:
        _log.info("Auto-Aufstellung: Aufstellung gesperrt bis Spieltag %d vorbei — skip", md)
        return "locked"
    return await run_lineup_set(channel)


async def run_lineup_gap_check(channel: DiscordChannel) -> str:
    """Elf-Lücken-Wächter: steht die Aufstellung schmaler da als der Kader erlaubt?

    Anlass (27.08.2026, real passiert): Um 12:03 setzte der Job die beste Elf,
    um 12:05 ging Hoffmeier per Vorhaben weg — und hinterließ ein Loch, das bis
    zum nächsten festen Aufstellungs-Slot (Fr 19:45) niemand geschlossen hätte.
    Emil hat es nicht bemerkt, ich auch nicht; aufgefallen ist es zufällig zwei
    Stunden später. Kickbase zieht für jeden unbesetzten Platz 100 Punkte ab
    (L-080) — dieses Loch war also 100 Punkte wert, für nichts.

    Bewusst billig: Nur `get_squad` (ein Aufruf), gezählt wird `lo`. Erst wenn
    wirklich eine Lücke klafft, läuft die teure Neuberechnung (build_plan +
    recommend_lineup, 1–2 min). Läuft der Spieltag schon, ist jeder Write
    wirkungslos — dann still aussteigen statt Rechenzeit verbrennen.
    """
    client, auth = await make_kickbase_client()
    lg = auth.league_id
    if lg is None:
        return "no_league"
    if running_matchday(await client.get_matchdays(), datetime.now(UTC)) is not None:
        return "locked"
    squad = await client.get_squad(lg)
    # Handelsspieler (Handels-Autopilot, Emil 01.10.2026: „sie bleiben Bank“) zählen weder als
    # Lücke noch als Platz; steht einer in der Elf, wird neu gesetzt — sonst sperrte der
    # Starter-Schutz seinen Verkauf.
    trade = _trade_ids({str(sp.player.id) for sp in squad})
    regular = [sp for sp in squad if str(sp.player.id) not in trade]
    fielded_trade = [sp for sp in squad if str(sp.player.id) in trade
                     and sp.lineup_order is not None]
    aufgestellt = sum(1 for sp in regular if sp.lineup_order is not None)
    soll = min(MIN_SQUAD_SIZE, len(regular))
    if fielded_trade:
        status = await run_lineup_set(channel)
        _log.warning("Handelsspieler in der Elf — neu gesetzt (%s)", status)
        return f"handel_bank:{status}"
    unfit_fielded = [
        sp for sp in regular
        if sp.lineup_order is not None
        and type(getattr(sp.player, "fitness_status", None)) is int
        and getattr(sp.player, "fitness_status", None) != 0
        and any(
            other.lineup_order is None
            and getattr(other.player, "position", None) == getattr(sp.player, "position", None)
            and type(getattr(other.player, "fitness_status", None)) is int
            and getattr(other.player, "fitness_status", None) == 0
            for other in regular
        )
    ]
    if unfit_fielded:
        status = await run_lineup_set(channel)
        _log.warning("Unfitter Starter trotz fittem Bank-Ersatz — neu gesetzt (%s)", status)
        return f"unfit_starter:{status}"
    if aufgestellt >= soll:
        if len(regular) < MIN_SQUAD_SIZE:
            # Ehrlicher Status (Turnaround-Studie P0-4): 1049 Läufe meldeten bei
            # Kader 8 „ok (8/8)". Mehr als der Kader lässt sich nicht aufstellen —
            # kein Neuaufbau, keine Nachricht —, aber jeder leere Platz kostet 100
            # Punkte. GRENZE: `job_runs` kennt nur ok/failed und speichert diesen
            # Rückgabewert nicht; er erscheint in Log und Aufrufer, die Job-Historie
            # bleibt „ok" (offen, braucht eine Migration). Den Alarm stellt deshalb
            # der Ergebnis-Wächter (worker/outcome_guard), nicht die Historie.
            return f"unvollständig ({aufgestellt}/{MIN_SQUAD_SIZE})"
        return f"ok ({aufgestellt}/{soll})"
    _log.warning(
        "Elf-Lücke: %d aufgestellt, %d moeglich (Kader %d ohne Handelsspieler) — setze neu",
        aufgestellt, soll, len(regular),
    )
    status = await run_lineup_set(channel)
    if status != "executed":
        # Nicht schliessbar (Kader traegt keine Formation) — der Aufstellungs-Job
        # hat das bereits gemeldet; hier NICHT nachlegen, sonst kommt dieselbe
        # Nachricht alle 30 Minuten.
        return f"gap_open:{status}"
    await channel.send_info(
        f"🧩 **Elf-Lücke geschlossen**: Es standen nur {aufgestellt} von {soll} "
        f"möglichen Spielern in der Aufstellung — jeder leere Platz hätte beim "
        f"Anpfiff 100 Punkte gekostet. Ursache ist fast immer ein Abgang nach dem "
        f"letzten Aufstellen."
    )
    return f"gap_closed:{status}"


def _trade_ids(squad_ids: set[str]) -> frozenset[str]:
    """Handelsspieler laut Handelsbuch — sie bleiben auf der Bank (Emil 01.10.2026)."""
    with SessionLocal() as session:
        book = load_trade_book(session, now=datetime.now(UTC), squad_ids=squad_ids)
    return frozenset(book.positions)


async def run_lineup_set(channel: DiscordChannel) -> str:
    """Beste 11 berechnen und (wenn geärmt) via Executor setzen. Gibt den Status zurück.
    Handelsspieler stehen nie in der Elf (`Plan.lineup_squad`, Emil 01.10.2026)."""
    settings = get_settings()
    plan = await build_plan()
    client, _auth = await make_kickbase_client()
    with SessionLocal() as session:
        rec = await recommend_lineup(client, session, plan.lineup_squad)
        lineup = rec.lineup
        if not lineup.starters:
            await channel.send_info(format_lineup_message(lineup, "no_lineup", False))
            return "no_lineup"
        label = (
            f"Teil-Elf {len(lineup.starters)}/11" if lineup.open_slots else "Beste 11"
        )
        action = PlannedAction(
            action_type="lineup",
            league_id=plan.league_id,
            formation=lineup.formation,
            player_ids=lineup.write_ids,
            rule_id="auto-lineup",
            rationale=f"{label} ({lineup.formation}), xPoints {lineup.expected_score:.2f}",
        )
        armed = settings.agent_auto_lineup
        result = await ActionExecutor(client, session).execute(
            action, dry_run=False if armed else None
        )
    status = result.status
    if status == "executed" and armed:
        status = await _verify_lineup(client, plan.league_id, lineup)
    if status == "duplicate" and lineup.open_slots:
        # Unveränderte Teil-Elf: Der Lücken-Wächter landet hier alle 30 Minuten,
        # wenn nicht jeder Spieler unterbringbar ist (z. B. zwei Torhüter) —
        # dieselbe Nachricht immer wieder wäre Lärm ohne neue Information.
        return status
    await channel.send_info(format_lineup_message(lineup, status, armed))
    return status


async def _verify_lineup(client, league_id: str, lineup: Lineup) -> str:
    """Wirkung statt Status (Beweisregel 1): stehen die gesetzten Spieler wirklich drin?

    Das Schreibformat einer Teil-Elf (None-Lücken) ist nur über die Rücklese
    beweisbar — antwortet Kickbase 200 und übernimmt die Plätze nicht, wäre
    „executed" eine Lüge und die Lücke fiele erst am Spieltag auf.
    """
    try:
        squad = await client.get_squad(league_id)
    except Exception as exc:  # noqa: BLE001 — Rücklese darf den Job nicht abbrechen
        _log.warning("Aufstellung: Rücklese fehlgeschlagen (%s)", exc)
        return "executed"
    fielded = {sp.player.id for sp in squad if sp.lineup_order is not None}
    missing = [c.name for c in lineup.starters if c.player_id not in fielded]
    if missing:
        _log.error("Aufstellung gesetzt, aber nicht übernommen: %s", ", ".join(missing))
        return "unverified"
    return "executed"


#: Minuten vor der Spieltags-Frist für den späten Lücken-Check. Nach T-15
#: (Notbremse, kann Spieler verkaufen) und nach Auktionen, die kurz vor dem
#: Anpfiff zuschlagen; früh genug für die Neuberechnung (1–2 min).
LATE_GAP_CHECK_MINUTES = 8


async def schedule_late_gap_check(scheduler, scheduled, channel) -> str:
    """Später Elf-Lücken-Check bei T-8 (18.09.2026, L-109).

    Der halbstündliche Wächter (:12/:42), T-35 und T-15 laufen alle VOR einer
    Auktion, die z. B. um 20:16 bei Anpfiff 20:30 zuschlägt — der gekaufte
    Spieler stünde dann nie in der Elf. Eigener Planer statt eines fünften
    Eintrags in `schedule_kickoff_guards`: die Anpfiff-Kaskade ist versiegelter
    Sicherungs-Code. Der Check selbst respektiert den Spieltags-Freeze
    (`running_matchday`) und rechnet nur bei echter Lücke neu.
    """
    from apscheduler.triggers.date import DateTrigger

    client, auth = await make_kickbase_client()
    if not auth.leagues:
        return "no_league"
    now = datetime.now(UTC)
    upcoming = matchday_deadline(await client.get_matchdays(), now)
    if upcoming is None or (upcoming[1] - now).total_seconds() > 96 * 3600:
        return "keine Spieltags-Deadline in den nächsten 4 Tagen"
    matchday, deadline = upcoming
    run_at = deadline - timedelta(minutes=LATE_GAP_CHECK_MINUTES)
    if run_at <= now:
        return f"Spieltag {matchday}: T-{LATE_GAP_CHECK_MINUTES} schon vorbei — kein später Check"
    scheduler.add_job(
        scheduled(f"Spieltag {matchday} T-{LATE_GAP_CHECK_MINUTES} Elf-Lücke",
                  lambda: run_lineup_gap_check(channel)),
        DateTrigger(run_date=run_at),
        id=f"kickoff-late-gap-md{matchday}",
        replace_existing=True,
    )
    return f"Spieltag {matchday} — später Elf-Lücken-Check {run_at:%a %H:%M} UTC"
