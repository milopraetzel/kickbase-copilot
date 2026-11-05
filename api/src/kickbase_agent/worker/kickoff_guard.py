"""Dynamischer Spieltags-Wächter — 2-Stufen-Notbremse nach Experten-Leitfaden.

Kernregel (offizielle Kickbase-Hilfe, verifiziert): Aufstellung UND Kontostand
werden genau EINMAL pro Spieltag geprüft — zum offiziellen Anpfiff des ERSTEN
Spiels des Spieltags. „Zu Beginn jedes Spieltags […] wird deine Aufstellung für
die Bewertung eingefroren. Gleichzeitig prüfen wir deinen Kontostand." Minus zu
diesem Zeitpunkt = NULL Punkte für den GESAMTEN Spieltag (~1.000–1.400 Punkte).
Ein Notverkauf aus der ELF kostet doppelt (Emils Fund 27.08., offiziell
bestätigt): die entgangenen Spielerpunkte (~60–90) UND 100 Strafpunkte, denn
„für jede unbesetzte Position werden Dir 100 Punkte abgezogen" — nachrücken
kann niemand, weil die Kaskade die Bank per Konstruktion vorher geleert hat.
Ein Verkauf aus Trading/Bank kostet dagegen NICHTS: Der Platz bleibt besetzt.
Selbst der teuerste Fall (~160–190 je Elf-Verkauf) bleibt weit unter den
~1.000–1.400 Punkten, die ein Minus-Konto kostet — die Notbremse lohnt immer.

Konsequenz fürs Modell: Es gibt EINE unwiderrufliche Deadline pro Spieltag
(`matchday_deadline`), nie eine je Einzelspiel. Nach der Deadline sind Lineup-
Writes für diesen Spieltag wirkungslos (gelten erst für den Folgespieltag) und
eine Notbremse wäre sinnlos (die Konto-Prüfung ist schon gelaufen) — zwischen
Deadline und nächstem Spieltag laufen daher KEINE Lineup-/Purge-Aktionen.

Ablauf (Zeiten relativ zur Spieltags-Deadline = frühester Anpfiff):
- T-120  Gebots-Storno: offene Kaufgebote, deren Zuschlag das Konto ins Minus
         reißen würde, werden storniert (größte zuerst; via `ofs`/`uoid`).
- T-60   PING an Emil: Kontostand + Notverkaufs-Kaskade als Plan — 45 Minuten
         Zeit für die menschliche Lösung.
- T-35   Aufstellung final setzen (separater Job).
- T-15   ENFORCE: Konto immer noch < Puffer und AGENT_AUTO_PURGE=1 → gnadenlos
         die Kaskade abverkaufen. Lieber mit 10 Spielern 800 Punkte (900 minus
         100 Strafe für den leeren Platz) als mit 11 Spielern 0. Erfolg wird
         NIE angenommen, sondern je Aktion am Executor-Status und am frisch
         gelesenen Kontostand verifiziert.

Verkaufs-Kaskade (Stufe für Stufe, nur bis der Puffer steht):
1. Trading-Material — schwächstes Momentum zuerst.
2. Bankdrücker (nicht in der aktuellen Aufstellung) — billigste zuerst.
3. Letzte Rettung: aufgestellte Feldspieler, billigste zuerst; Torwart ganz
   zuletzt.

Closing-Bell (täglich 21:33): Langzeit-Verletzte VOR dem 22-Uhr-MW-Update melden.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from kickbase_agent.decision.guardrails import protected_player_ids
from kickbase_agent.decision.kickoff_freeze import KICKOFF_MIN_BALANCE
from kickbase_agent.kickbase.errors import KickbaseDataError
from kickbase_agent.kickbase.factory import make_kickbase_client
from kickbase_agent.kickbase.models import Fixture, SquadPlayer
from kickbase_agent.scouts.ligainsider.models import InjuryEntry
from kickbase_agent.scouts.ligainsider.severity import Severity, injury_severity

# Konto muss vor Anpfiff mindestens hier stehen — EIN Puffer für die ganze
# Kaskade: einheitliches T-120-Exposure-Gate (decision/kickoff_freeze, Preflight),
# Storno und T-15-Notbremse rechnen mit derselben Zahl, sonst ließe das Gate
# Gebote durch, die der Storno gleich wieder streichen müsste.
PURGE_MIN_BALANCE = KICKOFF_MIN_BALANCE


async def _blocker(channel, text: str) -> None:
    """Alarm, den nur Emil lösen kann — auch im Discord-Digest-Modus zustellen.

    Review-Fund 17.09.: `discord_digest_only` steht per Default auf True, und
    `send_info` archiviert dann nur. Damit wären „NOTBREMSE FEHLGESCHLAGEN",
    „Kontostand UNBEKANNT" und der T-60-Minus-Alarm still geblieben — genau die
    Fälle, in denen Emil SOFORT in der App handeln muss. `send_blocker` stellt
    unabhängig vom Digest zu; Kanäle ohne diese Methode (Fakes, ältere
    Implementierungen) bekommen den unveränderten Text über `send_info`.
    """
    send = getattr(channel, "send_blocker", None)
    if send is None:
        await channel.send_info(text)
        return
    await send(text)


def next_kickoff(fixtures: list[Fixture], now: datetime) -> datetime | None:
    """Frühester zukünftiger Anpfiff — NUR Anzeige (Cockpit), KEINE Deadline.

    Die Aufstellungs-/Konto-Deadline ist `matchday_deadline` — ein Sa/So-Anstoß
    mitten im laufenden Spieltag ist kein Stichtag mehr.
    """
    upcoming = [
        f.kickoff for f in fixtures
        if f.kickoff is not None and not f.is_finished and f.kickoff > now
    ]
    return min(upcoming, default=None)


def matchday_deadline(
    fixtures: list[Fixture], now: datetime
) -> tuple[int, datetime] | None:
    """Nächste unwiderrufliche Spieltags-Deadline: (Spieltag, frühester Anpfiff).

    Kickbase sperrt die Aufstellung und prüft das Konto genau EINMAL pro
    Spieltag — beim offiziellen Anpfiff des ERSTEN Spiels („Nach dem Start des
    ersten Spiels eines Spieltags kannst du deine Aufstellung für die restlichen
    Partien nicht mehr anpassen."). Gewählt wird der nächste Spieltag, dessen
    frühester Anpfiff in der Zukunft liegt; None ohne bekannte Termine.

    WICHTIG: `is_finished` wird hier bewusst ignoriert — sonst sähe der laufende
    Spieltag nach dem Fr-Spiel wie „Start = Sa 15:30" aus, und genau dieser
    Denkfehler (Deadline je Einzelspiel) war der P0-Bug.
    """
    starts: dict[int, datetime] = {}
    for f in fixtures:
        if f.kickoff is None:
            continue
        cur = starts.get(f.matchday)
        if cur is None or f.kickoff < cur:
            starts[f.matchday] = f.kickoff
    future = [(md, start) for md, start in starts.items() if start > now]
    if not future:
        return None
    return min(future, key=lambda pair: pair[1])


def running_matchday(
    fixtures: list[Fixture], now: datetime, grace: timedelta = timedelta(hours=3)
) -> int | None:
    """Spieltag, der gerade LÄUFT (Deadline vorbei, letzte Partie noch offen).

    In diesem Fenster sind Lineup-Writes für den Spieltag wirkungslos (die Elf
    ist eingefroren, Änderungen gelten erst für den Folgespieltag) — die
    Aufstellungs-Cronjobs überspringen es. `grace` fängt hängende
    `is_finished`-Flags ab: 3 h nach dem letzten Anpfiff gilt die Partie als
    durch.
    """
    by_md: dict[int, list[Fixture]] = {}
    for f in fixtures:
        if f.kickoff is not None:
            by_md.setdefault(f.matchday, []).append(f)
    running: list[tuple[datetime, int]] = []
    for md, fs in by_md.items():
        start = min(f.kickoff for f in fs)
        if start > now:
            continue  # noch nicht angepfiffen
        over = all(f.is_finished for f in fs) or now > max(f.kickoff for f in fs) + grace
        if not over:
            running.append((start, md))
    return max(running)[1] if running else None


def _likely_starter(
    lineup_prob: int | None,
    *,
    is_li_bench: bool = False,
    negative_points: bool = False,
) -> int:
    """0 = startet eher NICHT (KB 4/5, expliziter LI-Bankstatus oder KB 3 mit Minuspunkten),
    1 = alles andere.

    `prob` 4 („Unwahrscheinlich") und 5 („Ausgeschlossen") sind eine
    Startelf-PROGNOSE, keine Punkte-Garantie: Ein Vierer kann eingewechselt
    werden und punkten, ein Dreier („Unsicher") kann draußen bleiben. Ist ein
    Spieler bei LigaInsider explizit als Bank/Ausfall geführt (`is_li_bench`)
    oder steht er bei `prob == 3` mit negativen Gesamtpunkten da (z. B. Hack
    mit -31 Punkten), geht er in S3 vor echten Startern wie Tigges.

    Unbekanntes `prob` (None — z. B. ohne Member-Abo) zählt konservativ als
    Starter. Sind ALLE Werte None, ist die Reihenfolge exakt die alte
    (Marktwert aufsteigend); bei gemischten Daten ändert sie sich bewusst —
    ein bekannter Fünfer geht dann vor einen unbekannten Spieler.
    """
    if is_li_bench:
        return 0
    if lineup_prob is not None and lineup_prob >= 4:
        return 0
    if lineup_prob == 3 and negative_points:
        return 0
    return 1


def _fielded_sort_key(
    row: tuple[str, str, int, int | None, bool, int, int, bool],
) -> tuple[int, int, int, int, int]:
    """Sortierung der S3-Gruppe: erst Nutzen, dann Preis — je Gruppe anders.

    Wer vermutlich nicht startet (KB 4/5, LI-Bank oder KB 3 mit Minuspunkten),
    geht ZUERST und davon die TEUERSTEN: Sie decken den Fehlbetrag mit den
    wenigsten Verkäufen (jeder Verkauf kostet einen Kaderplatz, und unter 11
    steht keine Formation mehr), und ihr Marktwert ist totes Kapital, das
    ohnehin weg soll.
    Bei den echten Startern gehen zuerst stark fallende Spieler (<= -100k/Tag,
    z. B. Ulrich -190k), danach mehrfach besetzte Positionen vor einem
    Allein-Positionsinhaber (z. B. einziger Stürmer Tigges) und formstarke
    Steiger (>= 300 Pkt & >= +150k/Tag) zuletzt — innerhalb jeder Stufe
    billigste zuerst.
    """
    _pid, _name, mv, prob, is_li_bench, points, mv24, sole_pos = row
    if not _likely_starter(prob, is_li_bench=is_li_bench, negative_points=points < 0):
        return (0, 0, 0, 0, -mv)
    falling_tier = 0 if mv24 <= -100_000 else 1
    sole_pos_tier = 1 if sole_pos else 0
    hot_starter_tier = 1 if (points >= 300 and mv24 >= 150_000) else 0
    return (1, falling_tier, sole_pos_tier, hot_starter_tier, mv)


def build_purge_cascade(
    squad: list[SquadPlayer],
    lineup_ids: set[str],
    trading_ids: set[str],
    momentum_by_id: dict[str, float] | None = None,
    protected_ids: frozenset[str] | None = None,
    li_bench_ids: set[str] | frozenset[str] | None = None,
) -> list[tuple[str, str, int, str]]:
    """Strikte Verkaufs-Hierarchie: (player_id, name, mv, stufe)-Liste in Reihenfolge.

    Geschützte Spieler (Verfassung, Default Olise) stehen gar nicht erst in der
    Kaskade: Der Executor verweigert sie ohnehin, aber ein Notverkauf, der
    garantiert als `refused` endet, kostet in den 15 Minuten vor Anpfiff genau
    die Zeit, die für den nächsten — wirksamen — Verkauf fehlt.
    """
    mom = momentum_by_id or {}
    protected = protected_player_ids() if protected_ids is None else protected_ids
    bench_set = frozenset(li_bench_ids) if li_bench_ids else frozenset()
    pos_counts: dict[int, int] = {}
    for sp in squad:
        pos = int(sp.player.position)
        pos_counts[pos] = pos_counts.get(pos, 0) + 1
    trading, bench, fielded, keeper = [], [], [], []
    for sp in squad:
        p = sp.player
        if p.id in protected:
            continue
        row = (p.id, p.last_name or p.first_name, p.market_value, p.lineup_prob)
        if p.id in trading_ids and p.id not in lineup_ids:
            trading.append(row)
        elif p.id not in lineup_ids:
            bench.append(row)
        elif int(p.position) == 1:
            keeper.append(row)
        else:
            pos = int(p.position)
            fielded.append(
                (
                    p.id,
                    p.last_name or p.first_name,
                    p.market_value,
                    p.lineup_prob,
                    p.id in bench_set,
                    int(p.points or 0),
                    int(p.mv_change_24h or 0),
                    pos_counts.get(pos, 0) == 1,
                )
            )
    trading.sort(key=lambda r: mom.get(r[0], 0.0))   # schwächstes Momentum zuerst
    bench.sort(key=lambda r: r[2])                    # billigste zuerst
    # S3 sortiert nach NUTZEN, erst dann nach Preis: Ist der Kader so dünn,
    # dass auch prob-4/5- oder LI-Bank-Spieler in die Elf müssen, gehen sie vor
    # echten Startern; unter den Startern gehen starke Marktwert-Verlierer vor
    # formstarken Allein-Stürmern wie Tigges.
    fielded.sort(key=_fielded_sort_key)
    return (
        [(pid, n, mv, "S1 Trading") for pid, n, mv, _ in trading]
        + [(pid, n, mv, "S2 Bank") for pid, n, mv, _ in bench]
        + [
            (
                pid, n, mv,
                "S3 Startelf!"
                if _likely_starter(prob, is_li_bench=is_b, negative_points=pts < 0)
                else "S3 Elf (Start unwahrsch.)",
            )
            for pid, n, mv, prob, is_b, pts, _mv24, _sole in fielded
        ]
        + [(pid, n, mv, "S3 Torwart!!") for pid, n, mv, _ in keeper]
    )


#: Kickbase zieht für JEDEN unbesetzten Aufstellungsplatz 100 Punkte ab
#: (offizielle Hilfe, „Rund um Kontostand & Aufstellung", geprüft 27.08.2026).
#: Emil kannte die Regel, unsere Kaskade rechnete sie nicht mit — sie bezifferte
#: einen Elf-Verkauf mit 60–90 statt 160–190 Punkten.
EMPTY_SLOT_PENALTY = 100


def purge_point_cost(sells: list[tuple[str, str, int, str]]) -> int:
    """Strafpunkte, die dieser Notverkauf kostet — je leer bleibendem Elf-Platz 100.

    Nur S3-Verkäufe (Elf/Torwart) reißen ein Loch: Trading und Bank stehen
    definitionsgemäß nicht in der Aufstellung, ihr Verkauf kostet null Punkte.
    Nachrücken kann niemand — erreicht die Kaskade S3, war die Bank schon leer.
    Die entgangenen Spielerpunkte kommen obendrauf; die schätzt diese Funktion
    bewusst NICHT (jede Zahl dafür wäre erfunden).
    """
    return EMPTY_SLOT_PENALTY * sum(1 for row in sells if row[3].startswith("S3"))


def select_cascade_sells(
    cascade: list[tuple[str, str, int, str]], budget: int, buffer: int = PURGE_MIN_BALANCE
) -> list[tuple[str, str, int, str]]:
    """Nimmt aus der Kaskade genau so viele Verkäufe, bis Konto ≥ Puffer."""
    needed = buffer - budget
    out: list[tuple[str, str, int, str]] = []
    raised = 0
    for row in cascade:
        if raised >= needed:
            break
        out.append(row)
        raised += row[2]
    return out


def select_bids_to_cancel(
    bids: list[dict], budget: int, buffer: int = PURGE_MIN_BALANCE
) -> list[dict]:
    """T-120: Gebote (größte zuerst) streichen, bis selbst der Zuschlag ALLER
    verbleibenden Gebote das Konto nicht mehr unter den Puffer reißen kann."""
    exposure = sum(b["amount"] for b in bids)
    to_cancel: list[dict] = []
    for b in sorted(bids, key=lambda x: -x["amount"]):
        if budget - exposure >= buffer:
            break
        to_cancel.append(b)
        exposure -= b["amount"]
    return to_cancel


def closing_bell_lines(
    squad: list[SquadPlayer], injuries: list[InjuryEntry]
) -> list[str]:
    """LANGZEIT-verletzte Kaderspieler → 'jetzt vor 22:00 verkaufen'-Zeilen."""
    lines: list[str] = []
    for sp in squad:
        last = sp.player.last_name or ""
        for inj in injuries:
            if last and last in inj.player_name:
                if injury_severity(inj.reason) is Severity.LONG:
                    lines.append(
                        f"• **{inj.player_name}** ({inj.reason}) — MW "
                        f"{sp.player.market_value / 1e6:.1f}M: Sofortverkauf VOR 22:00 "
                        "sichert noch den alten Preis!"
                    )
                break
    return lines


async def run_closing_bell(channel) -> bool:
    """21:33 täglich: frischer LI-Scan; nur bei Langzeit-Ausfall im Kader laut."""
    from kickbase_agent.scouts.ligainsider.scraper import clear_cache, fetch_snapshot

    client, auth = await make_kickbase_client()
    lg = auth.league_id
    if lg is None:
        return False
    squad = await client.get_squad(lg)
    clear_cache()  # Closing-Bell braucht den FRISCHEN Stand, kein 6h-Cache
    snap = await fetch_snapshot()
    lines = closing_bell_lines(squad, snap.injuries)
    if not lines:
        return False
    # Textpräzision (Review-Fund): der Discord-Bot versteht keine Chat-Befehle
    # (kein on_message-Handler) — »mach den Trade« gehört an Claude im CHAT.
    await channel.send_info(
        "🔔 **Closing-Bell (vor dem 22-Uhr-Update)** — Langzeit-Ausfall im Kader:\n"
        + "\n".join(lines)
        + "\n▶️ Sag Claude im Chat: »mach den Trade« — dann läuft der Verkauf sofort "
        "(der Discord-Bot hier liest keine Nachrichten)."
    )
    return True


async def _gather_li_bench_ids(squad: list[SquadPlayer]) -> frozenset[str]:
    """LigaInsider-Bank-/Ausfall-IDs für den Kader ermitteln (fail-safe leer bei Fehler)."""
    from kickbase_agent.config import get_settings
    from kickbase_agent.scouts.ligainsider.availability import resolve_availability
    from kickbase_agent.scouts.ligainsider.models import Status
    from kickbase_agent.scouts.ligainsider.scraper import fetch_snapshot

    try:
        if not get_settings().ligainsider_enabled or not squad:
            return frozenset()
        snap = await fetch_snapshot()
        kb = [
            (
                str(sp.player.id),
                sp.player.last_name or sp.player.first_name or "",
                str(sp.player.team_id or ""),
            )
            for sp in squad
        ]
        avails, _ = resolve_availability(kb, snap.lineups, snap.injuries, snap.scraped_tids)
        return frozenset(a.player_id for a in avails if a.status in {Status.BENCH, Status.OUT})
    except Exception:
        return frozenset()


async def _gather_state(client, session, lg: str):
    """Kader, Aufstellung, Trading-Zone, Momentum — Grundlage der Kaskade."""
    from kickbase_agent.intel.history import load_history
    from kickbase_agent.trading.orchestrator import load_trading_holdings
    from kickbase_agent.trading.scanner import momentum
    from kickbase_agent.trading.trade_book import load_trade_book

    squad = await client.get_squad(lg)
    squad_ids = [sp.player.id for sp in squad]
    trading_ids = set(load_trading_holdings(session, squad_ids))
    try:
        book = load_trade_book(
            session, now=datetime.now(UTC), squad_ids=squad_ids, league_id=lg
        )
        trading_ids |= set(book.positions.keys())
    except Exception:
        pass
    # Aufstellung aus dem squad-Endpoint (`lo` = Lineup-Order, live verifiziert)
    raw = await client._get(f"/v4/leagues/{lg}/squad")
    lineup_ids = {str(p.get("i")) for p in raw.get("it", []) if "lo" in p}
    history = load_history(session, squad_ids)
    momentum_by_id = {
        pid: momentum([h.market_value for h in rows], window=5).daily_change
        for pid, rows in history.items() if len(rows) >= 3
    }
    return squad, lineup_ids, trading_ids, momentum_by_id


async def run_bid_cancel_guard(channel) -> str:
    """T-120: Gebots-Storno — kein offenes Gebot darf das Konto sprengen können."""
    from kickbase_agent.agent.executor import ActionExecutor, PlannedAction
    from kickbase_agent.config import get_settings
    from kickbase_agent.db.session import SessionLocal

    client, auth = await make_kickbase_client()
    lg = auth.league_id
    if lg is None:
        return "no_league"
    try:
        budget = await client.get_budget(lg)
    except KickbaseDataError as exc:
        await _blocker(channel,
            f"🚨 **T-120: Kontostand UNBEKANNT** ({exc}) — ohne Kontostand kein "
            "Storno-Entscheid. Bitte offene Gebote SELBST in der App prüfen!"
        )
        return "budget_unknown"
    bids = await client.get_own_bids(lg, auth.user_id)
    to_cancel = select_bids_to_cancel(bids, budget)
    if not to_cancel:
        await channel.send_info(
            f"🛡️ T-120: {len(bids)} offene Gebote, Konto {budget / 1e6:.1f}M — "
            "kein Zuschlag kann uns ins Minus reißen. Keine neuen Gebote bis Anpfiff!"
        )
        return "ok"
    names = ", ".join(f"{b['name']} ({b['amount'] / 1e6:.1f}M)" for b in to_cancel)
    if get_settings().agent_auto_purge:
        # Ehrlichkeit statt Wunschdenken: der Executor kann refused/failed/
        # skipped_dry_run liefern — nur "executed" zählt als storniert.
        results: list[tuple[dict, str]] = []
        with SessionLocal() as session:
            ex = ActionExecutor(client, session)
            for b in to_cancel:
                res = await ex.execute(PlannedAction(
                    action_type="cancel_bid", league_id=lg, player_id=b["player_id"],
                    offer_id=b["offer_id"], rule_id="kickoff-bid-cancel",
                    rationale="T-120: Gebot könnte Konto beim Zuschlag ins Minus reißen"),
                    dry_run=False)
                results.append((b, res.status))
        failed = [(b, st) for b, st in results if st != "executed"]
        if not failed:
            await channel.send_info(
                f"🛡️ **T-120 Gebots-Storno ausgeführt**: {names} — Zuschläge können "
                "das Konto nicht mehr sprengen."
            )
            return "cancelled"
        ok = ", ".join(b["name"] for b, st in results if st == "executed") or "keins"
        bad = ", ".join(f"{b['name']} → {st}" for b, st in failed)
        await _blocker(channel,
            f"🚨 **T-120 STORNO FEHLGESCHLAGEN** — storniert: {ok}; offen geblieben: "
            f"{bad}. Diese Gebote können das Konto beim Zuschlag weiter ins Minus "
            "reißen — SOFORT manuell in der App zurückziehen!"
        )
        return "cancel_failed"
    await _blocker(channel,
        f"⚠️ T-120: Diese Gebote könnten das Konto beim Zuschlag ins Minus reißen: "
        f"{names}. AGENT_AUTO_PURGE=0 → bitte selbst zurückziehen."
    )
    return "alert"


async def run_debt_ping(channel) -> str:
    """T-60: NUR melden — Emil hat 45 Minuten für die menschliche Lösung."""
    from kickbase_agent.db.session import SessionLocal

    client, auth = await make_kickbase_client()
    lg = auth.league_id
    if lg is None:
        return "no_league"
    try:
        budget = await client.get_budget(lg)
    except KickbaseDataError as exc:
        await _blocker(channel,
            f"🚨 **T-60: Kontostand UNBEKANNT** ({exc}) — bitte Konto SELBST in der "
            "App prüfen. Die T-15-Notbremse verkauft bei unbekanntem Stand NICHTS."
        )
        return "budget_unknown"
    if budget >= PURGE_MIN_BALANCE:
        await channel.send_info(
            f"🛡️ Anpfiff-Wächter T-60: Konto ok ({budget / 1e6:.2f}M) — alles bereit."
        )
        return "ok"
    with SessionLocal() as session:
        squad, lineup_ids, trading_ids, mom = await _gather_state(client, session, lg)
    li_bench = await _gather_li_bench_ids(squad)
    cascade = build_purge_cascade(
        squad, lineup_ids, trading_ids, mom, li_bench_ids=li_bench
    )
    sells = select_cascade_sells(cascade, budget)
    plan = " → ".join(f"{n} ({mv / 1e6:.1f}M, {stufe})" for _, n, mv, stufe in sells)
    # Emil soll den Preis SEHEN, bevor er entscheidet: Elf-Verkäufe kosten je
    # 100 Strafpunkte für den leeren Platz — bei dünnem Kader ist das der
    # Unterschied zwischen „ärgerlich" und „halber Spieltag weg".
    strafe = purge_point_cost(sells)
    preis = (
        f"\nPreis: {strafe} Strafpunkte ({strafe // EMPTY_SLOT_PENALTY} leere "
        f"Elf-Plätze à 100) plus deren Spielerpunkte."
        if strafe else "\nPreis: 0 Strafpunkte — trifft nur Bank/Trading, die Elf bleibt voll."
    )
    # Textpräzision (Review-Fund): »mach den Notverkauf« versteht nur Claude im
    # CHAT — der Discord-Bot reagiert ausschließlich auf Buttons.
    await _blocker(channel,
        f"🚨 **T-60: Konto {budget / 1e6:.2f}M im Minus!** Beim Anpfiff im Minus = "
        f"NULL Punkte für den ganzen Spieltag.\nNotverkaufs-Kaskade: {plan}{preis}\n"
        "▶️ Du hast bis T-15: sag Claude im Chat: »mach den Notverkauf« oder regle "
        "es selbst in der App — sonst verkauft der Wächter bei T-15 automatisch "
        "(AGENT_AUTO_PURGE=1)."
    )
    return "alert"


async def run_purge_enforce(channel) -> str:
    """T-15: Konto immer noch < Puffer → Kaskade gnadenlos abverkaufen (wenn geärmt)."""
    from kickbase_agent.agent.executor import ActionExecutor, PlannedAction
    from kickbase_agent.config import get_settings
    from kickbase_agent.db.session import SessionLocal

    client, auth = await make_kickbase_client()
    lg = auth.league_id
    if lg is None:
        return "no_league"
    try:
        budget = await client.get_budget(lg)
    except KickbaseDataError as exc:
        # Zustand unbekannt ≠ Konto leer: NIEMALS auf Verdacht verkaufen.
        await _blocker(channel,
            f"🚨🚨 **T-15: Kontostand UNBEKANNT** ({exc}) — die Notbremse verkauft "
            "NICHTS auf Verdacht. SOFORT App öffnen und Konto selbst glattstellen!"
        )
        return "budget_unknown"
    if budget >= PURGE_MIN_BALANCE:
        return "ok"  # T-60-Ping hat gereicht — still bleiben
    with SessionLocal() as session:
        squad, lineup_ids, trading_ids, mom = await _gather_state(client, session, lg)
        li_bench = await _gather_li_bench_ids(squad)
        cascade = build_purge_cascade(
            squad, lineup_ids, trading_ids, mom, li_bench_ids=li_bench
        )
        sells = select_cascade_sells(cascade, budget)
        names = ", ".join(f"{n} ({stufe})" for _, n, _, stufe in sells)
        if not get_settings().agent_auto_purge:
            await _blocker(channel,
                f"🚨🚨 **T-15 — LETZTE WARNUNG: Konto {budget / 1e6:.2f}M!** "
                f"AGENT_AUTO_PURGE=0 → ich DARF nicht verkaufen. Plan: {names}. "
                "SOFORT App öffnen!"
            )
            return "alert"
        ex = ActionExecutor(client, session)
        # Status je Verkauf einsammeln — der Executor kann refused/failed/
        # skipped_dry_run liefern; nur "executed" zählt als verkauft.
        # INKREMENTELL (Review-Fund): nach JEDEM echten Verkauf den Kontostand
        # FRISCH lesen und stoppen, sobald der Puffer steht — die Kaskade darf
        # keinen Spieler mehr abverkaufen als nötig. Liefert der Re-Read keinen
        # Kontostand, ist der Zustand unbekannt → ebenfalls STOPP statt blind
        # weiterverkaufen.
        results: list[tuple[str, str, str]] = []  # (Name, Stufe, Status)
        after = budget  # letzter BEKANNTER Stand; ändert sich nur durch "executed"
        after_unknown = False
        # Über die VOLLE Kaskade, nicht über die Vorauswahl `sells` (Codex-Fund
        # 27.08.): `select_cascade_sells` schätzt nur vorab, wie viele Verkäufe
        # nötig sein DÜRFTEN, und dient der Vorschau-Meldung. Lief die Schleife
        # nur über dieses Präfix, beendete ein refused/failed darin die
        # Notbremse, obwohl in der Kaskade noch verkäufliche Spieler standen —
        # bei negativem Konto kostet das den ganzen Spieltag. Zu weit verkauft
        # wird trotzdem nicht: Der frische Kontostand nach jedem echten Verkauf
        # bricht ab, sobald der Puffer steht.
        for pid, n, _mv, stufe in cascade:
            res = await ex.execute(PlannedAction(
                action_type="sell", league_id=lg, player_id=pid,
                rule_id="kickoff-purge",
                rationale="T-15-Notbremse: Konto vor der Spieltags-Deadline glattstellen"),
                dry_run=False)
            results.append((n, stufe, res.status))
            if res.status != "executed":
                continue  # nichts verkauft → Kontostand unverändert, nächste Stufe
            # Beweis statt Behauptung: Kontostand FRISCH von der API — erst
            # Konto ≥ 0 ist der Spieltag gerettet (Prüfung genau zur Deadline).
            try:
                after = await client.get_budget(lg)
            except KickbaseDataError:
                after_unknown = True
                break
            if after >= PURGE_MIN_BALANCE:
                break  # Puffer steht — Rest der Kaskade bleibt im Kader
    failed = [(n, stufe, st) for n, stufe, st in results if st != "executed"]
    if after_unknown:
        sold = ", ".join(
            f"{n} ({stufe})" for n, stufe, st in results if st == "executed"
        ) or "keiner"
        await _blocker(channel,
            f"🚨 **NOTBREMSE GESTOPPT — Kontostand UNBEKANNT** (Budget-Antwort ohne "
            f"Kontostand). Bereits verkauft: {sold}. Keine weiteren Verkäufe auf "
            "Verdacht — SOFORT App öffnen und Konto selbst prüfen!"
        )
        return "purge_failed"
    # Drei ehrliche Zustände (Review-Fund: „Erfolg" feuerte schon bei Konto ≥ 0,
    # obwohl die Verkaufsschleife bis ≥ PURGE_MIN_BALANCE zielt):
    #   after ≥ Puffer        → Erfolg
    #   0 ≤ after < Puffer    → Teil-Erfolg (Spieltag gerettet, Puffer fehlt)
    #   after < 0             → Alarm (wie bisher)
    sold = ", ".join(f"{n} ({stufe})" for n, stufe, _st in results)
    if not failed and after >= PURGE_MIN_BALANCE:
        stopp = (
            f" Kaskade nach {len(results)} von {len(cascade)} möglichen Verkäufen "
            "gestoppt — Puffer steht, der Rest bleibt im Kader."
            if len(results) < len(cascade)
            else ""
        )
        await _blocker(channel,
            f"🚨 **T-15 NOTBREMSE ausgeführt** — verkauft: {sold}. "
            f"Konto {budget / 1e6:.2f}M → {after / 1e6:.2f}M.{stopp} Lieber 10 Spieler "
            "mit Punkten als 11 mit null."
        )
        return "purged"
    if not failed and after >= 0:
        await channel.send_info(
            f"⚠️ **T-15 Notbremse: TEIL-Erfolg** — verkauft: {sold}. "
            f"Konto {budget / 1e6:.2f}M → {after / 1e6:.2f}M — positiv, aber unter dem "
            "10.000-€-Puffer — beobachten! Keine neuen Gebote bis Anpfiff; ein später "
            "Zuschlag könnte das Konto zur Deadline wieder ins Minus reißen."
        )
        return "purged_below_buffer"
    status_txt = "; ".join(f"{n} ({stufe}) → {st}" for n, stufe, st in results) or "keine Aktion"
    lage = (
        f"Konto noch {after / 1e6:.2f}M im Minus"
        if after < 0
        else f"Konto {after / 1e6:.2f}M, aber Verkäufe unvollständig"
    )
    await _blocker(channel,
        f"🚨 **NOTBREMSE FEHLGESCHLAGEN** ({status_txt}) — {lage}. "
        "SOFORT manuell in der App verkaufen, sonst NULL Punkte für den Spieltag!"
    )
    return "purge_failed"


async def schedule_kickoff_guards(scheduler, scheduled, channel) -> str:
    """Täglich: Spieltags-Deadline ermitteln und T-120/T-60/T-35/T-15-Wächter setzen.

    ALLE Wächter hängen an der EINEN Deadline des Spieltags (frühester Anpfiff),
    nie an einzelnen Sa/So-Anstößen: Aufstellung und Konto-Prüfung sind ab da
    eingefroren, spätere Wächter am selben Spieltag wären wirkungslos — und ein
    T-15-„Notverkauf" eines Startelf-Spielers am Sa würde nur ein 0-Punkte-Loch
    reißen, das niemand mehr stopfen kann.
    """
    from apscheduler.triggers.date import DateTrigger

    from kickbase_agent.worker.lineup_job import run_lineup_set

    client, auth = await make_kickbase_client()
    if not auth.leagues:
        return "no_league"
    fixtures = await client.get_matchdays()
    now = datetime.now(UTC)
    upcoming = matchday_deadline(fixtures, now)
    # 96h-Horizont (Review-Fund): 36h verfehlte Do→Fr (37,5h) und englische
    # Wochen — die Wächter entstanden erst am Spieltag-Morgen selbst (Single Point
    # of Failure). Mit 4 Tagen sind sie Tage vorher eingeloggt; das tägliche
    # Neu-Planen (feste Job-IDs + replace_existing) und der on_ready-Replan nach
    # Neustarts halten sie aktuell.
    if upcoming is None or (upcoming[1] - now).total_seconds() > 96 * 3600:
        return "keine Spieltags-Deadline in den nächsten 4 Tagen"
    matchday, deadline = upcoming

    marks = (
        ("T-120", 120, lambda: run_bid_cancel_guard(channel)),
        ("T-60", 60, lambda: run_debt_ping(channel)),
        ("T-35", 35, lambda: run_lineup_set(channel)),
        ("T-15", 15, lambda: run_purge_enforce(channel)),
    )
    added = []
    # Catch-up (Review-Fund): Startet der Worker z. B. bei T-10 neu, liegen alle
    # run_at-Zeitpunkte in der Vergangenheit — APScheduler verwirft solche
    # DateTrigger und KEIN Wächter liefe, obwohl die Deadline noch bevorsteht.
    # Jeder Wächter, dessen Fenster schon BEGONNEN hat (T-x verstrichen, Deadline
    # aber in der Zukunft — von matchday_deadline garantiert), wird deshalb
    # SOFORT einmalig nachgeholt: gestaffelt in Kaskaden-Reihenfolge (Storno →
    # Ping → Elf → Purge), damit z. B. der Purge nie vor dem Gebots-Storno läuft.
    # Die festen Job-IDs + replace_existing dedupen einen noch wartenden Catch-up,
    # falls der Planer (on_ready + 07:03-Cron) doppelt anläuft.
    catchup_delay = timedelta(seconds=5)
    for label, minutes_before, factory in marks:
        run_at = deadline - timedelta(minutes=minutes_before)
        if run_at <= now:
            run_at = now + catchup_delay
            catchup_delay += timedelta(seconds=15)
            added.append(f"{label} (verpasst → läuft SOFORT)")
        else:
            added.append(label)
        scheduler.add_job(
            scheduled(f"Spieltag {matchday} {label}", factory),
            DateTrigger(run_date=run_at),
            id=f"kickoff-{label}-md{matchday}",
            replace_existing=True,
        )
    marks_txt = ", ".join(added)
    return (
        f"Spieltag {matchday} — Deadline {deadline:%a %H:%M} UTC (erster Anpfiff) — "
        f"Wächter gesetzt: {marks_txt}"
    )
