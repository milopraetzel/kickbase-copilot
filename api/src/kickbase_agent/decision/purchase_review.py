"""Gemeinsame Prüfung datierter Kaufentscheidungen und ihrer Mannschaftsfinanzierung."""

from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any

from kickbase_agent.decision.guardrails import protected_player_ids
from kickbase_agent.decision.overdraft import (
    MIDWEEK_SWAP_BRIDGE_EUR,
    PLANNING_OVERDRAFT_UNTIL,
    overdraft_active,
    swap_sale_proceeds,
)
from kickbase_agent.trading.finance import overdraft_floor

REVIEW_LIFETIME = timedelta(hours=3)
TEAM_PURPOSES = frozenset({"lineup_completion", "lineup_upgrade"})
# Notbesetzungs-Fenster (16.09.2026, Decision-Record lineup.emergency_bench_window_hours):
# 24 h waren KUERZER als die Laufzeit der Auktionen, in denen eine Notbesetzung
# ueberhaupt erworben werden kann — ein Kickbase-Listing laeuft bis zu 48 h, das
# Gebot muss also lange vor der Frist stehen. Ergebnis war ein Deadlock: teure
# Starter wurden mangels Deckung storniert, bezahlbare Bankspieler vom Fenster
# gesperrt, der Kader blieb bei 8 (drei Leerplaetze = -300 Punkte je Spieltag).
# 96 h decken Auktionslaufzeit plus Vorlauf und halten den Befund vom 13.09.
# (Guether, 128 h vor der Frist) weiterhin gesperrt. Die zweite Sicherung bleibt
# unveraendert: Bankrollen nur bei belegter eigener Kaderluecke (< 11 Spieler).
EMERGENCY_BENCH_WINDOW = timedelta(hours=96)


def emergency_bench_fallback_allowed(now: datetime, deadline: datetime | None) -> bool:
    """Bankspieler nur als befristete Notbesetzung vor der offiziellen Frist."""
    if deadline is None or now.tzinfo is None or deadline.tzinfo is None:
        return False
    return timedelta(0) < deadline - now <= EMERGENCY_BENCH_WINDOW


def is_engine_review(review: Any) -> bool:
    """Teilweise Engine-Belege dürfen nicht in den schwächeren Altvertrag fallen."""
    return isinstance(review, dict) and (
        review.get("purpose") in TEAM_PURPOSES
        or any(key in review for key in ("engine_version", "plan_id", "team_plan", "bid_scenarios"))
    )


def reviewed_bid_amount(review: Any) -> int | None:
    """Geprüfter Ausgangspreis; eine wirtschaftliche Kappe ist kein gewähltes Gebot."""
    if not isinstance(review, dict):
        return None
    if is_engine_review(review):
        scenarios = review.get("bid_scenarios")
        if not isinstance(scenarios, list):
            return None
        selected = [s.get("amount") for s in scenarios
                    if isinstance(s, dict) and s.get("selected") is True]
        return selected[0] if len(selected) == 1 and type(selected[0]) is int else None
    return review.get("target_bid_eur", review.get("max_bid_eur"))


def _money_rows(rows: Any, *, key: str = "amount_eur") -> dict[str, int] | None:
    if not isinstance(rows, list):
        return None
    out = {}
    for row in rows:
        if not isinstance(row, dict):
            return None
        pid, amount = row.get("player_id"), row.get(key)
        if not isinstance(pid, str) or not pid or pid in out or type(amount) is not int:
            return None
        if amount <= 0:
            return None
        out[pid] = amount
    return out


def _commitments(
    purchases: dict[str, int], bids: dict[str, int], owned: set[str],
    *, player_id: str | None = None, amount: int | None = None,
) -> dict[str, int]:
    # Ein Ziel ist entweder schon Besitz, ein tatsächliches Gebot oder noch Planung.
    commitments = {pid: value for pid, value in purchases.items() if pid not in owned}
    for pid, value in bids.items():
        # Eine geplante Erhöhung bleibt reserviert, auch solange live noch der
        # alte Betrag steht. Ein höheres Istgebot verdrängt die kleinere Planung.
        commitments[pid] = max(value, commitments.get(pid, 0))
    if player_id is not None and amount is not None:
        commitments[player_id] = amount  # Bestehendes Zielgebot wird ersetzt, nicht addiert.
    return commitments


def _reserved_amount(
    purchases: dict[str, int], bids: dict[str, int], owned: set[str],
    *, player_id: str | None = None, amount: int | None = None,
) -> int:
    return sum(
        _commitments(purchases, bids, owned, player_id=player_id, amount=amount).values()
    )


# --- Tausch-Finanzierung (Planungs-Dispo, Emil 22./23.09.2026) ----------------------
#
# Ein Plan darf mehr binden als das Konto, wenn der Überhang an bedingte Sofortverkäufe
# ungeschützter eigener Spieler gekoppelt ist (Tausch-Paar: Gebot + wartender Verkauf).
# Der Beleg steht im gemeinsamen Plan als `swap_sales` (je Zeile Verkaufsspieler,
# gekoppelter Kauf, Marktwert, angerechneter Erlös ≤ 90 % MW) und im Kaufbeleg des
# gekoppelten Ziels als `swap_sale`. Angerechnet wird je Paar höchstens der Kaufbetrag:
# verliert das Gebot, fällt auch der Verkauf weg, ein Überschuss finanziert nichts
# anderes. Belege OHNE `swap_sales` verhalten sich exakt wie vor dem Dispo.

_SWAP_KEYS = ("player_id", "for_player_id", "market_value_eur", "proceeds_eur")


def _swap_rows(plan: Any) -> list[dict] | None:
    """Tauschzeilen des Plans; [] ohne Tausch, None bei widersprüchlichem Beleg."""
    rows = plan.get("swap_sales") if isinstance(plan, dict) else None
    if rows is None:
        return []
    if not isinstance(rows, list):
        return None
    sold: set[str] = set()
    bought: set[str] = set()
    out = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != set(_SWAP_KEYS):
            return None
        pid, target = row["player_id"], row["for_player_id"]
        value, proceeds = row["market_value_eur"], row["proceeds_eur"]
        if (not isinstance(pid, str) or not pid or not isinstance(target, str) or not target
                or pid == target or pid in sold or target in bought
                or type(value) is not int or type(proceeds) is not int
                or value <= 0 or not 0 < proceeds <= swap_sale_proceeds(value)):
            return None
        sold.add(pid)
        bought.add(target)
        out.append(dict(row))
    return out


def _swap_proof_veto(review: dict, plan: dict, purchases: dict[str, int],
                     owned: set[str]) -> str | None:
    """Belegtreue: Plan- und Kaufbeleg nennen dieselbe Kopplung, nie einen Geschützten."""
    rows = _swap_rows(plan)
    if rows is None:
        return "Tausch-Beleg ungültig — gekoppelte Sofortverkäufe nicht eindeutig belegt."
    protected = protected_player_ids()
    for row in rows:
        if row["for_player_id"] not in purchases:
            return "Tausch-Beleg ungültig — gekoppelter Kauf steht nicht im Mannschaftsplan."
        if row["player_id"] not in owned or row["player_id"] in purchases:
            return "Tausch-Beleg ungültig — Verkaufsspieler gehört nicht zum eigenen Kader."
        if row["player_id"] in protected:
            return (
                "Tausch verweigert — geschützter Spieler ist nie Verkaufsseite eines Tauschs "
                "(Verfassung)."
            )
    own = next((row for row in rows if row["for_player_id"] == review.get("player_id")), None)
    sale = review.get("swap_sale")
    expected = None if own is None else {k: own[k] for k in _SWAP_KEYS if k != "for_player_id"}
    if sale != expected:
        return "Tausch-Beleg ungültig — Kaufbeleg und Plan nennen verschiedene Verkäufe."
    return None


def _swap_funding_veto(
    plan: dict, *, commitments: dict[str, int], budget: int, owned: set[str],
    now: datetime, not_funded: str,
) -> str | None:
    """Mehr gebunden als Konto: nur als gedeckter Tausch auf Dispo zulässig."""
    rows = _swap_rows(plan)
    if not rows:
        return not_funded
    if not overdraft_active(now, until=PLANNING_OVERDRAFT_UNTIL):
        return (
            f"Planungs-Dispo abgelaufen ({PLANNING_OVERDRAFT_UNTIL:%d.%m.%Y}) — Tausch nur "
            "noch aus echtem Geld. " + not_funded
        )
    protected = protected_player_ids()
    for row in rows:
        if row["player_id"] in protected:
            return (
                "Tausch verweigert — geschützter Spieler ist nie Verkaufsseite eines Tauschs "
                "(Verfassung)."
            )
        if row["player_id"] not in owned:
            return (
                "Tausch-Verkauf nicht gedeckt — Verkaufsspieler steht nicht mehr im eigenen "
                "Kader."
            )
    team_value = plan.get("team_value_eur")
    if type(team_value) is not int or team_value <= 0:
        return "Tausch-Beleg ohne Teamwert — 33-%-Grenze nicht prüfbar, Gebot verweigert."
    reserved = sum(commitments.values())
    open_bid_exposure = (
        plan["open_bid_exposure_eur"]
        if type(plan.get("open_bid_exposure_eur")) is int and plan["open_bid_exposure_eur"] > 0
        else 0
    )
    limits = plan.get("technical_limits") if isinstance(plan.get("technical_limits"), dict) else {}
    raw_trade_cover = limits.get("trade_sale_cover_eur", 0)
    trade_cover = (
        raw_trade_cover if type(raw_trade_cover) is int and raw_trade_cover > 0 else 0
    )
    deficit = max(0, -(budget + trade_cover - open_bid_exposure))
    credit = sum(
        min(row["proceeds_eur"], commitments[row["for_player_id"]] + deficit)
        for row in rows if row["for_player_id"] in commitments
    )
    raw_bridge = limits.get("midweek_swap_bridge_eur", 0)
    bridge = (
        min(raw_bridge, MIDWEEK_SWAP_BRIDGE_EUR)
        if type(raw_bridge) is int and raw_bridge > 0 and credit > 0
        else 0
    )
    if reserved - credit - bridge > budget + trade_cover:
        return (
            f"Tausch netto nicht gedeckt: {reserved} € Verpflichtungen, {credit} € "
            f"gekoppelte Sofortverkäufe, {budget} € Konto."
        )
    floor = overdraft_floor(team_value, budget)
    if budget - reserved < floor:
        return (
            f"Tausch sprengt die 33-%-Grenze: Konto nach allen Zuschlägen "
            f"{budget - reserved} €, Minus-Limit {floor} € (Teamwert {team_value} €)."
        )
    return None


def _team_plan_veto(review: dict, player_id: str, amount: int, now: datetime) -> str | None:
    for key in ("engine_version", "plan_id"):
        if not isinstance(review.get(key), str) or not review[key].strip():
            return "Mannschaftsplan ungültig — Engine-Version oder Plan-ID fehlt."
    plan = review.get("team_plan")
    if not isinstance(plan, dict) or plan.get("plan_id") != review["plan_id"]:
        return "Mannschaftsplan ungültig — gemeinsame Planbindung fehlt."
    if plan.get("observed_at") != review.get("observed_at"):
        return "Mannschaftsplan ungültig — Beobachtungszeit gehört nicht zur Kaufprüfung."
    purchases = _money_rows(plan.get("purchases"))
    bids = _money_rows(plan.get("open_bids"))
    owned_rows = plan.get("owned_player_ids")
    if (purchases is None or bids is None or not isinstance(owned_rows, list)
            or any(not isinstance(pid, str) or not pid for pid in owned_rows)):
        return "Mannschaftsplan ungültig — Käufe, Gebote oder tatsächlicher Besitz fehlen."
    owned = set(owned_rows)
    if player_id not in purchases or player_id in owned:
        return "Mannschaftsplan ungültig — Ziel ist kein noch zu erwerbender Plankauf."
    if review.get("purpose") == "lineup_completion":
        filled = plan.get("baseline_filled_slots")
        if type(filled) is not int or not 0 <= filled < 11:
            return "Mannschaftsplan ungültig — Auffüllkauf ohne belegte Aufstellungslücke."
    for row in plan["purchases"]:
        cap = row.get("max_bid_eur")
        if type(cap) is not int or cap < row["amount_eur"]:
            return "Mannschaftsplan ungültig — individuelle Plankappe fehlt."
        if row["player_id"] == player_id and review["max_bid_eur"] > cap:
            return "Mannschaftsplan: individuelle Preisobergrenze überschreitet die Plankappe."
    if type(plan.get("budget_eur")) is not int:
        return "Mannschaftsplan ungültig — belegte Geldbeträge fehlen."
    for key in ("open_bid_exposure_eur", "reserved_cash_eur"):
        if type(plan.get(key)) is not int or plan[key] < 0:
            return "Mannschaftsplan ungültig — belegte Geldbeträge fehlen."
    recovery = plan.get("funding_recovery")
    recovered = isinstance(recovery, dict) and recovery.get("status") == "funded"
    if plan["budget_eur"] < 0 and not _swap_rows(plan) and not recovered:
        return "Mannschaftsplan ungültig — belegte Geldbeträge fehlen."
    if plan["open_bid_exposure_eur"] != sum(bids.values()):
        return "Mannschaftsplan ungültig — offene Gebote und Bindung widersprechen sich."
    if plan["reserved_cash_eur"] != _reserved_amount(purchases, bids, owned):
        return "Mannschaftsplan ungültig — gemeinsame Geldbindung stimmt nicht."
    problem = _swap_proof_veto(review, plan, purchases, owned)
    if problem is not None:
        return problem
    commitments = _commitments(purchases, bids, owned, player_id=player_id, amount=amount)
    if sum(commitments.values()) > plan["budget_eur"]:
        problem = _swap_funding_veto(
            plan, commitments=commitments, budget=plan["budget_eur"], owned=owned, now=now,
            not_funded="Mannschaftsplan nicht finanziert — alle Zuschläge überschreiten das Konto.",
        )
        if problem is not None:
            return problem
    scenarios = review.get("bid_scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        return "Mannschaftsplan ungültig — geprüfte Preisszenarien fehlen."
    prices = set()
    selected = []
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            return "Mannschaftsplan ungültig — Preisszenario nicht lesbar."
        price, probability = scenario.get("amount"), scenario.get("win_probability")
        if type(price) is not int or price <= 0 or price in prices:
            return "Mannschaftsplan ungültig — Preisszenarien nicht eindeutig."
        prices.add(price)
        if (probability is not None and (type(probability) not in (int, float)
                or not isfinite(probability) or not 0 <= probability <= 1)):
            return "Mannschaftsplan ungültig — Zuschlagswahrscheinlichkeit unzulässig."
        if type(scenario.get("selected")) is not bool:
            return "Mannschaftsplan ungültig — Szenarioauswahl fehlt."
        if scenario["selected"]:
            selected.append(price)
    if selected != [purchases[player_id]]:
        return "Mannschaftsplan ungültig — gewähltes Szenario passt nicht zum Plankauf."
    if amount not in prices:
        return "Mannschaftsplan: Gebotspreis wurde nicht als Mannschaftsalternative geprüft."
    return None


def team_funding_veto(
    review: dict | None, *, player_id: str, amount: int,
    budget: int | None, open_bids: list[dict] | None, owned_player_ids: set[str] | None,
    now: datetime | None = None,
) -> str | None:
    """Frische Verpflichtungen ersetzen den Snapshot; Erlöshoffnungen zählen niemals.

    Einzige Ausnahme (Planungs-Dispo bis 31.10.2026): der im Plan belegte, gekoppelte
    Sofortverkauf eines Tauschs. Dann prüft diese Funktion auch die Kickbase-33-%-Grenze
    — der Executor tut das für Engine-Gebote nicht selbst, er ruft nur diese Funktion
    (ohne `now`; dann gilt die Uhr).
    """
    if not is_engine_review(review):
        return None
    assert review is not None
    bids = _money_rows(open_bids, key="amount")
    if type(budget) is not int or bids is None or owned_player_ids is None:
        return "Mannschaftsplan: frisches Konto, eigene Gebote oder tatsächlicher Besitz unbekannt."
    if player_id in owned_player_ids:
        return "Mannschaftsplan: Zielspieler gehört bereits zum eigenen Kader — neu planen."
    plan = review.get("team_plan", {})
    purchases = _money_rows(plan.get("purchases"))
    if purchases is None:
        return "Mannschaftsplan: konkrete Käufe fehlen."
    commitments = _commitments(
        purchases, bids, owned_player_ids, player_id=player_id, amount=amount,
    )
    reserved = sum(commitments.values())
    if reserved > budget:
        return _swap_funding_veto(
            plan, commitments=commitments, budget=budget, owned=set(owned_player_ids),
            now=now or datetime.now(UTC),
            not_funded=(
                f"Mannschaftsplan nicht finanziert: {reserved} € für alle Verpflichtungen "
                f"bei {budget} € Konto; keine geplanten Verkaufserlöse eingerechnet."
            ),
        )
    return None


def team_selection_veto(
    reviews: dict[str, dict], amounts: dict[str, int], *, now: datetime | None = None,
) -> str | None:
    """Auch gleichzeitige Anpassungen dürfen die gemeinsame Planreserve nicht mehrfach nutzen."""
    engine = {pid: r for pid, r in reviews.items() if is_engine_review(r)}
    if not engine:
        return None
    plan = next(iter(engine.values()))["team_plan"]
    if any(r["team_plan"] != plan for r in engine.values()):
        return "Mannschaftsplan: unterschiedliche gemeinsame Pläne — Auswahl erneut berechnen."
    purchases = _money_rows(plan["purchases"])
    bids = _money_rows(plan["open_bids"])
    assert purchases is not None and bids is not None
    purchases.update({pid: amount for pid, amount in amounts.items() if pid in engine})
    # Bereits gebotene Ziele können angepasst werden; dann ersetzt der neue Betrag sie.
    bids.update({pid: amounts[pid] for pid in engine if pid in bids and pid in amounts})
    owned = set(plan["owned_player_ids"])
    commitments = _commitments(purchases, bids, owned)
    if sum(commitments.values()) > plan["budget_eur"]:
        return _swap_funding_veto(
            plan, commitments=commitments, budget=plan["budget_eur"], owned=owned,
            now=now or datetime.now(UTC),
            not_funded="Mannschaftsplan: gemeinsame Gebotsanpassungen überschreiten das "
            "belegte Konto.",
        )
    return None


def purchase_review_veto(
    review: dict[str, Any] | None,
    *,
    player_id: str,
    amount: int | None,
    now: datetime,
    required: bool = True,
) -> str | None:
    """None erlaubt das Gebot, ein Text begründet die Ablehnung."""
    if review is None:
        return (
            "Kaufprüfung fehlt — autonomes Gebot ohne ausgewählte Kaufentscheidung verweigert."
            if required else None
        )
    if not isinstance(review, dict):
        return "Kaufprüfung ungültig — strukturierte Kaufentscheidung fehlt."
    if type(review.get("schema_version")) is not int or review["schema_version"] != 1:
        return "Kaufprüfung ungültig — unbekannte Schemaversion."
    if not player_id or review.get("player_id") != player_id:
        return "Kaufprüfung ungültig — Entscheidung gehört nicht zum Zielspieler."
    if review.get("selected") is not True:
        return "Kaufprüfung verweigert — Spieler wurde nicht zum Kauf ausgewählt."
    cap = review.get("max_bid_eur")
    if type(cap) is not int or cap <= 0:
        return "Kaufprüfung ungültig — positive ganzzahlige Preisobergrenze fehlt."
    if type(amount) is not int or amount <= 0:
        return "Kaufprüfung ungültig — positiver ganzzahliger Gebotsbetrag fehlt."
    if amount > cap:
        return f"Kaufprüfung: Gebot {amount} überschreitet die Preisobergrenze {cap}."
    market_value = review.get("market_value_eur")
    if type(market_value) is not int or market_value <= 0:
        return "Kaufprüfung ungültig — beobachteter Marktwert fehlt."
    purpose, reasons = review.get("purpose"), review.get("reasons")
    if not isinstance(purpose, str) or not purpose.strip():
        return "Kaufprüfung ungültig — Kaufzweck fehlt."
    if (not isinstance(reasons, list) or not reasons
            or any(not isinstance(r, str) or not r.strip() for r in reasons)):
        return "Kaufprüfung ungültig — belegte Kaufgründe fehlen."
    dates = []
    for key in ("observed_at", "valid_until"):
        value = review.get(key)
        if not isinstance(value, str):
            return "Kaufprüfung ungültig — datiertes Gültigkeitsfenster fehlt."
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return "Kaufprüfung ungültig — datiertes Gültigkeitsfenster unlesbar."
        if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
            return "Kaufprüfung ungültig — Zeitpunkte müssen ausdrücklich UTC sein."
        dates.append(stamp)
    if dates[1] - dates[0] > REVIEW_LIFETIME:
        return "Kaufprüfung ungültig — Gültigkeitsfenster überschreitet maximal 3 Stunden."
    if now.tzinfo is None or not dates[0] <= now.astimezone(UTC) < dates[1]:
        return "Kaufprüfung abgelaufen oder noch nicht gültig — Entscheidung neu erstellen."
    if is_engine_review(review):
        return _team_plan_veto(review, player_id, amount, now.astimezone(UTC))
    return None
