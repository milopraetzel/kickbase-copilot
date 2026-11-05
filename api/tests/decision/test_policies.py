"""Decision-Policies: Kauf-, Verkaufs- und Angebotslogik unter Guardrails."""

from kickbase_agent.decision.guardrails import Guardrails
from kickbase_agent.decision.policies import (
    IncomingOffer,
    buy_blocker_reason,
    evaluate_incoming_offer,
    formation_need_counts,
    positions_in_need,
    propose_accepts,
    propose_buys,
    propose_sells,
)
from kickbase_agent.intel.models import ExplosionCandidate, Signal

LEAGUE = "1234567"


def cand(
    pid: str,
    mv: int,
    score: float,
    name: str = "Spieler",
    injury_severity: str | None = None,
    injury_reason: str = "",
    position: int = 3,
) -> ExplosionCandidate:
    return ExplosionCandidate(
        player_id=pid,
        name=name,
        team_id="1",
        position=position,
        current_market_value=mv,
        total_score=score,
        signals=[],
        injury_severity=injury_severity,
        injury_reason=injury_reason,
    )


def viable_squad(strong: float = 0.8) -> list[ExplosionCandidate]:
    """Formationsfähiger 14er-Kader (1 TW, 5 ABW, 5 MF, 3 ST), alle über der
    Verkaufsschwelle — Verkaufs-Tests hängen ihre Kandidaten hier an, weil
    propose_sells seit dem Kader-Deckungs-Fix den VOLLEN Kader erwartet."""
    squad = [cand("gk1", 2_000_000, strong, position=1)]
    squad += [cand(f"d{i}", 2_000_000, strong, position=2) for i in range(5)]
    squad += [cand(f"m{i}", 2_000_000, strong, position=3) for i in range(5)]
    squad += [cand(f"s{i}", 2_000_000, strong, position=4) for i in range(3)]
    return squad


def squad_with(*extra: ExplosionCandidate) -> list[ExplosionCandidate]:
    return viable_squad() + list(extra)


# --- propose_buys ---

def test_buy_strong_candidate_within_budget():
    props = propose_buys([cand("1", 3_000_000, 0.8)], budget=20_000_000,
                         league_id=LEAGUE, guardrails=Guardrails())
    assert len(props) == 1
    assert props[0].action.action_type == "bid"
    assert props[0].action.player_id == "1"
    assert props[0].rule_id
    assert props[0].requires_approval is False  # < 5M


def test_buy_skips_low_score():
    props = propose_buys([cand("1", 3_000_000, 0.4)], budget=20_000_000,
                         league_id=LEAGUE, guardrails=Guardrails())
    assert props == []


def test_buy_skips_when_over_max_bid_pct():
    # 10M Gebot > 60% von 12M (=7,2M)
    props = propose_buys([cand("1", 10_000_000, 0.9)], budget=12_000_000,
                         league_id=LEAGUE, guardrails=Guardrails())
    assert props == []


def test_buy_respects_daily_cap_and_budget_drain():
    cands = [cand(str(i), 4_000_000, 0.9) for i in range(6)]
    props = propose_buys(cands, budget=100_000_000, league_id=LEAGUE,
                         guardrails=Guardrails(daily_buy_cap=3))
    assert len(props) == 3  # daily_buy_cap (explizit 3; Default seit 23.08. = 5)


def test_buy_over_5m_requires_approval():
    # Gebot = MW = 6M > 5M-Schwelle → immer Approval; 6M < 25% von 100M
    props = propose_buys([cand("1", 6_000_000, 0.9)], budget=100_000_000,
                         league_id=LEAGUE, guardrails=Guardrails())
    assert props[0].requires_approval is True


def test_buy_never_drops_below_min_balance():
    props = propose_buys([cand("1", 4_000_000, 0.9)], budget=4_000_000,
                         league_id=LEAGUE,
                         guardrails=Guardrails(min_balance=1_000_000))
    assert props == []  # 4M Budget - 4M Gebot = 0 < min_balance 1M


# --- propose_sells ---

def test_sell_weak_squad_player():
    props = propose_sells(squad_with(cand("9", 2_000_000, 0.2)), league_id=LEAGUE,
                          guardrails=Guardrails())
    assert len(props) == 1
    # Review-Fix: nicht-dringend → listen (Computer-Angebot ~103% MW mitnehmen)
    assert props[0].action.action_type == "list"
    assert props[0].action.player_id == "9"


def test_sell_skips_protected():
    g = Guardrails(protected_player_ids=frozenset({"9"}))
    props = propose_sells(squad_with(cand("9", 2_000_000, 0.2)), league_id=LEAGUE,
                          guardrails=g)
    assert props == []


def test_sell_keeps_strong_player():
    props = propose_sells(squad_with(cand("9", 2_000_000, 0.8)), league_id=LEAGUE,
                          guardrails=Guardrails())
    assert props == []


def test_sell_respects_daily_cap():
    weak = [cand(f"w{i}", 1_000_000, 0.1) for i in range(5)]
    props = propose_sells(squad_with(*weak), league_id=LEAGUE,
                          guardrails=Guardrails(daily_sell_cap=3))
    assert len(props) == 3  # explizit 3; Default seit 23.08. = 5


# --- propose_sells: Kader-Deckung (Review-Fund P0) ---

def test_derived_minimum_coverage_from_formations():
    """Die Mindestbesetzung wird aus den 9 Formationen ABGELEITET, nicht geraten:
    1 TW immer, ABW min 3, MF min 2, ST min 1."""
    from kickbase_agent.decision.policies import _MIN_KEEPERS, _MIN_PER_POSITION

    assert _MIN_KEEPERS == 1
    assert _MIN_PER_POSITION == {2: 3, 3: 2, 4: 1}


def test_sell_eleven_man_squad_proposes_nothing():
    """Reproduzierter P0: 11er-Kader (exakt 4-4-2-stellbar), alle unter der
    Schwelle → früher 3 Vorschläge inkl. des einzigen Torwarts, jetzt KEINE
    (jeder Verkauf fiele unter 11 Spieler / unter die Formations-Deckung)."""
    squad = [cand("gk", 1_000_000, 0.05, position=1)]
    squad += [cand(f"d{i}", 1_000_000, 0.1, position=2) for i in range(4)]
    squad += [cand(f"m{i}", 1_000_000, 0.1, position=3) for i in range(4)]
    squad += [cand(f"s{i}", 1_000_000, 0.1, position=4) for i in range(2)]
    assert len(squad) == 11
    props = propose_sells(squad, league_id=LEAGUE, guardrails=Guardrails())
    assert props == []


def test_sell_never_proposes_the_only_goalkeeper():
    """Der einzige Torwart ist nie verkaufbar — schwächere Feldspieler dahinter
    werden trotzdem noch vorgeschlagen (skip statt Abbruch)."""
    squad = [cand("gk", 1_000_000, 0.0, position=1)]  # schwächster im Kader
    squad += [cand("d0", 1_000_000, 0.1, position=2)]
    squad += [cand(f"d{i}", 2_000_000, 0.8, position=2) for i in range(1, 5)]
    squad += [cand("m0", 1_000_000, 0.15, position=3)]
    squad += [cand(f"m{i}", 2_000_000, 0.8, position=3) for i in range(1, 5)]
    squad += [cand(f"s{i}", 2_000_000, 0.8, position=4) for i in range(3)]
    assert len(squad) == 14
    props = propose_sells(squad, league_id=LEAGUE, guardrails=Guardrails())
    sold = {p.action.player_id for p in props}
    assert "gk" not in sold
    assert sold == {"d0", "m0"}


def test_sell_fourteen_man_squad_caps_at_three_and_keeps_formation():
    """14er-Kader mit 4 schwachen Spielern: Cap begrenzt auf 3, und der
    Rest-Kader kann danach weiterhin eine gültige Formation stellen."""
    from kickbase_agent.decision.policies import _can_field_any_formation

    squad = [cand("gk", 2_000_000, 0.8, position=1)]
    squad += [cand("d0", 1_000_000, 0.10, position=2)]
    squad += [cand(f"d{i}", 2_000_000, 0.8, position=2) for i in range(1, 5)]
    squad += [cand("m0", 1_000_000, 0.05, position=3),
              cand("m1", 1_000_000, 0.12, position=3)]
    squad += [cand(f"m{i}", 2_000_000, 0.8, position=3) for i in range(2, 5)]
    squad += [cand("s0", 1_000_000, 0.08, position=4)]
    squad += [cand(f"s{i}", 2_000_000, 0.8, position=4) for i in range(1, 3)]
    assert len(squad) == 14
    props = propose_sells(squad, league_id=LEAGUE, guardrails=Guardrails())
    assert len(props) == 3  # daily_sell_cap
    sold = {p.action.player_id for p in props}
    assert sold == {"m0", "s0", "d0"}  # die drei schwächsten verkaufbaren
    remaining = [c for c in squad if c.player_id not in sold]
    assert len(remaining) == 11
    counts: dict[int, int] = {}
    for c in remaining:
        counts[c.position] = counts.get(c.position, 0) + 1
    assert _can_field_any_formation(counts)


def test_sell_formation_rule_blocks_before_cap():
    """Nicht das Cap, sondern die Deckung stoppt: von 4 Mittelfeldspielern sind
    3 schwach, aber nach 2 Verkäufen wäre mit nur 1 MF keine Formation mehr
    stellbar (Minimum über alle Formationen: 2 MF) — der dritte wird geskippt,
    obwohl 11 Spieler bleiben würden."""
    squad = [cand("gk", 2_000_000, 0.8, position=1)]
    squad += [cand(f"d{i}", 2_000_000, 0.8, position=2) for i in range(5)]
    squad += [cand("m0", 1_000_000, 0.05, position=3),
              cand("m1", 1_000_000, 0.10, position=3),
              cand("m2", 1_000_000, 0.15, position=3),
              cand("m3", 2_000_000, 0.8, position=3)]
    squad += [cand(f"s{i}", 2_000_000, 0.8, position=4) for i in range(4)]
    assert len(squad) == 14
    props = propose_sells(squad, league_id=LEAGUE, guardrails=Guardrails())
    assert {p.action.player_id for p in props} == {"m0", "m1"}


def test_sell_long_injury_also_respects_formation_rule():
    """Bewusste Entscheidung (dokumentiert in propose_sells): auch der
    Langzeit-Sofortverkauf darf die Aufstellung nicht unmöglich machen —
    lieber MW-Verlust als ein 0-Punkte-Loch am Spieltag."""
    squad = [cand("gk", 1_000_000, 0.8, position=1)]
    squad += [cand(f"d{i}", 2_000_000, 0.8, position=2) for i in range(4)]
    squad += [cand(f"m{i}", 2_000_000, 0.8, position=3) for i in range(3)]
    squad += [cand("mx", 1_000_000, 0.0, position=3,
                   injury_severity="long", injury_reason="LI: Kreuzbandriss")]
    squad += [cand(f"s{i}", 2_000_000, 0.8, position=4) for i in range(2)]
    assert len(squad) == 11  # Verkauf fiele unter 11 → auch LONG wird gehalten
    props = propose_sells(squad, league_id=LEAGUE, guardrails=Guardrails())
    assert props == []


# --- evaluate_incoming_offer ---

def test_accept_good_offer_with_flat_prediction():
    p = evaluate_incoming_offer(
        player_id="5", market_value=2_000_000, offer_price=2_100_000, offer_id="77",
        league_id=LEAGUE, mv_prediction_7d=-0.02, guardrails=Guardrails(),
    )
    assert p is not None
    assert p.action.action_type == "accept"
    assert p.action.offer_id == "77"


def test_reject_offer_when_player_rising():
    p = evaluate_incoming_offer(
        player_id="5", market_value=2_000_000, offer_price=2_100_000, offer_id="77",
        league_id=LEAGUE, mv_prediction_7d=0.05, guardrails=Guardrails(),
    )
    assert p is None  # MW steigt -> behalten


def test_ignore_lowball_offer():
    p = evaluate_incoming_offer(
        player_id="5", market_value=2_000_000, offer_price=2_010_000, offer_id="77",
        league_id=LEAGUE, mv_prediction_7d=-0.02, guardrails=Guardrails(),
    )
    assert p is None  # < 103% MW


def test_decline_offer_on_protected_player():
    g = Guardrails(protected_player_ids=frozenset({"5"}))
    p = evaluate_incoming_offer(
        player_id="5", market_value=2_000_000, offer_price=3_000_000, offer_id="77",
        league_id=LEAGUE, mv_prediction_7d=-0.1, guardrails=g,
    )
    assert p is not None
    assert p.action.action_type == "decline"


def test_buy_bid_opens_low_with_psych_offset():
    """Gebots-Staffelung (pay-as-bid): Erstgebot nur +2 % über MW — wer sofort
    das volle Overpay bietet, zahlt es auch ohne Konkurrenz. Krummer Tiebreaker
    bleibt drin."""
    g = Guardrails()
    props = propose_buys([cand("1", 5_000_000, 0.9)], budget=60_000_000,
                         league_id=LEAGUE, guardrails=g)
    assert len(props) == 1
    bid = props[0].action.amount
    assert bid == int(5_000_000 * (1 + g.bid_ladder_opening)) + g.bid_psych_offset
    assert bid < int(5_000_000 * (1 + g.bid_overpay_fraction))  # unter Ziel-Level
    assert bid % 1_000 != 0   # krumm — schlägt glatte Rivalen-Gebote


def test_buy_rationale_names_escalation_target():
    """Die rationale macht das Ziel-Level transparent: Briefing/Cockpit zeigen,
    wohin die Eskalation kurz vor Listing-Ablauf gehen soll."""
    g = Guardrails()
    props = propose_buys([cand("1", 5_000_000, 0.9)], budget=60_000_000,
                         league_id=LEAGUE, guardrails=g)
    target = int(5_000_000 * (1 + g.bid_overpay_fraction)) + g.bid_psych_offset
    assert "Erstgebot 5.11M €" in props[0].rationale
    assert f"Ziel bei Eskalation {target / 1_000_000:.2f}M €" in props[0].rationale


def test_buy_budget_caps_check_opening_bid_not_target():
    """Kappung (max_bid_pct) und min_balance prüfen das ERSTGEBOT — das Ziel ist
    nur eine spätere, separat freizugebende Empfehlung."""
    g = Guardrails()
    # max_single_bid = 60% von 17,4M = 10,44M: Erstgebot 10,213M passt,
    # das Ziel-Level 10,513M läge darüber — Vorschlag kommt trotzdem.
    props = propose_buys([cand("1", 10_000_000, 0.9)], budget=17_400_000,
                         league_id=LEAGUE, guardrails=g)
    assert len(props) == 1
    assert props[0].action.amount == int(10_000_000 * 1.02) + g.bid_psych_offset
    # min_balance analog: Budget deckt das Erstgebot, nicht das Ziel-Level.
    g2 = Guardrails(max_bid_pct_of_budget=1.0)
    props2 = propose_buys([cand("1", 10_000_000, 0.9)], budget=10_300_000,
                          league_id=LEAGUE, guardrails=g2)
    assert len(props2) == 1


def test_zero_score_without_injury_is_listed_not_sold():
    """Review-Fix: Score 0.0 allein ist KEIN Sofortverkaufsgrund — ohne Ausfall-Info
    läuft auch ein miserabler Spieler über den normalen Listen-Pfad (~103% Angebot)."""
    props = propose_sells(squad_with(cand("9", 2_000_000, 0.0)), league_id=LEAGUE,
                          guardrails=Guardrails())
    assert len(props) == 1
    assert props[0].action.action_type == "list"


def test_long_term_injury_sells_immediately():
    """Langzeit-Ausfall (Kreuzband & Co.): sofort raus — vor 22:00 gilt der alte Preis."""
    c = cand("9", 2_000_000, 0.0, injury_severity="long",
             injury_reason="LI: Kreuzbandriss")
    props = propose_sells(squad_with(c), league_id=LEAGUE, guardrails=Guardrails())
    assert len(props) == 1
    assert props[0].action.action_type == "sell"
    assert "Langzeit" in props[0].rationale
    assert "Kreuzbandriss" in props[0].rationale


def test_short_term_injury_is_held_not_sold():
    """Kurzzeit-Ausfall (Grippe, angeschlagen): HALTEN — weder verkaufen noch listen."""
    c = cand("9", 2_000_000, 0.0, injury_severity="short", injury_reason="LI: Grippe")
    props = propose_sells(squad_with(c), league_id=LEAGUE, guardrails=Guardrails())
    assert props == []


def test_unknown_injury_severity_is_held_conservatively():
    """Unklare Ausfalldauer wird konservativ wie Kurzzeit behandelt (beobachten)."""
    c = cand("9", 2_000_000, 0.0, injury_severity="unknown", injury_reason="LI: Ausfall")
    props = propose_sells(squad_with(c), league_id=LEAGUE, guardrails=Guardrails())
    assert props == []


def test_held_injured_players_do_not_consume_sell_slots():
    """Gehaltene Verletzte blockieren keine Verkaufs-Slots für echte Kandidaten."""
    injured = [cand(f"i{i}", 1_000_000, 0.0, injury_severity="short") for i in range(3)]
    weak = [cand(f"w{i}", 1_000_000, 0.2) for i in range(3)]
    props = propose_sells(squad_with(*injured, *weak), league_id=LEAGUE,
                          guardrails=Guardrails())
    assert len(props) == 3
    assert {p.action.player_id for p in props} == {"w0", "w1", "w2"}
    assert all(p.action.action_type == "list" for p in props)


# --- Vereinslimit (Review-Fund, Liga-Regel lpc aus der API) ---

def test_club_limit_blocks_third_player_of_same_club():
    cands = [cand("1", 5_000_000, 0.9), cand("2", 5_000_000, 0.85)]
    # beide Kandidaten vom selben Club "1" (cand setzt team_id="1")
    props = propose_buys(cands, budget=60_000_000, league_id=LEAGUE,
                         guardrails=Guardrails(),
                         club_counts={"1": 1}, max_per_club=2)
    # Kader hat schon 1 vom Club → nur EIN weiterer Vorschlag erlaubt,
    # der eigene Vorschlag zählt mit (kein totes Zweitgebot)
    assert len(props) == 1


def test_club_limit_none_means_unlimited():
    cands = [cand("1", 5_000_000, 0.9), cand("2", 5_000_000, 0.85)]
    props = propose_buys(cands, budget=60_000_000, league_id=LEAGUE,
                         guardrails=Guardrails(), club_counts={"1": 5},
                         max_per_club=None)
    assert len(props) == 2


def test_conviction_targets_get_higher_escalation_target():
    """Staffelung: Conviction ändert NICHT das Erstgebot (beide steigen niedrig
    ein), sondern das Ziel-Level in der rationale (+9 % statt +5 %)."""
    g = Guardrails()
    props = propose_buys([cand("1", 10_000_000, 0.9)], budget=60_000_000,
                         league_id=LEAGUE, guardrails=g, conviction_ids={"1"})
    normal = propose_buys([cand("1", 10_000_000, 0.9)], budget=60_000_000,
                          league_id=LEAGUE, guardrails=g)
    assert props[0].action.amount == normal[0].action.amount  # gleiches Erstgebot
    conviction_target = int(10_000_000 * (1 + g.bid_overpay_conviction)) + g.bid_psych_offset
    normal_target = int(10_000_000 * (1 + g.bid_overpay_fraction)) + g.bid_psych_offset
    assert f"{conviction_target / 1_000_000:.2f}M €" in props[0].rationale
    assert f"{normal_target / 1_000_000:.2f}M €" in normal[0].rationale


# --- max_bid_premium wird hart erzwungen (Review-Fund P0) ---

def test_small_mv_bid_capped_at_max_premium():
    """Der fixe Psycho-Offset (+13.337) riss bei kleinen Marktwerten die
    110-%-Kappe: MW 100k → Erstgebot 115.337 statt ≤ 110.000. Jetzt werden
    Gebot UND Eskalations-Ziel exakt auf die Kappe gesetzt (nicht geskippt —
    die Kappe IST das Maximalgebot)."""
    g = Guardrails()
    props = propose_buys([cand("1", 100_000, 0.9)], budget=20_000_000,
                         league_id=LEAGUE, guardrails=g)
    assert len(props) == 1  # gekappt, nicht verworfen
    cap = int(100_000 * g.max_bid_premium)
    assert cap == 110_000
    assert props[0].action.amount == cap  # exakt die Kappe
    # Ungekappt wären es 115.337 gewesen — die Kappe griff wirklich.
    assert int(100_000 * (1 + g.bid_ladder_opening)) + g.bid_psych_offset > cap
    # Auch das Eskalations-Ziel in der rationale ist gekappt (0.11M €, nicht 0.12M €).
    assert "Ziel bei Eskalation 0.11M €" in props[0].rationale


def test_escalation_target_capped_even_when_opening_bid_is_not():
    """MW 200k (Conviction): das Erstgebot (217.337) liegt noch UNTER der Kappe
    (220.000) und bleibt unverändert — aber das Conviction-Ziel (+9 % → 231.337)
    wird in der rationale auf die Kappe gekappt."""
    g = Guardrails()
    props = propose_buys([cand("1", 200_000, 0.9)], budget=20_000_000,
                         league_id=LEAGUE, guardrails=g, conviction_ids={"1"})
    assert len(props) == 1
    opening = int(200_000 * (1 + g.bid_ladder_opening)) + g.bid_psych_offset
    cap = int(200_000 * g.max_bid_premium)
    assert opening < cap
    assert props[0].action.amount == opening          # Erstgebot NICHT gekappt
    assert f"Ziel bei Eskalation {cap / 1_000_000:.2f}M €" in props[0].rationale
    uncapped_target = int(200_000 * (1 + g.bid_overpay_conviction)) + g.bid_psych_offset
    assert f"{uncapped_target / 1_000_000:.2f}M €" not in props[0].rationale


def test_normal_mv_bid_unchanged_by_premium_cap():
    """Gegenprobe: bei normalen Marktwerten ändert die Kappe nichts — Erstgebot
    bleibt die Staffel-Formel (+2 % + Psycho-Offset, krumm)."""
    g = Guardrails()
    props = propose_buys([cand("1", 5_000_000, 0.9)], budget=60_000_000,
                         league_id=LEAGUE, guardrails=g)
    expected = int(5_000_000 * (1 + g.bid_ladder_opening)) + g.bid_psych_offset
    assert props[0].action.amount == expected  # 5.113.337 < Kappe 5.500.000
    assert expected < int(5_000_000 * g.max_bid_premium)


# --- Manager-Preisanker (24.08.; err 5080: Anker = max(Listing-Preis, MW)) ---

def test_manager_listing_anchors_bid_at_listing_price():
    """Manager-Listing über MW: das Erstgebot springt auf Listing-Preis + Offset
    (statt der niedrigen Kickbase-Staffel), bleibt aber unter der 110-%-Kappe."""
    g = Guardrails()
    props = propose_buys([cand("1", 10_000_000, 0.8)], budget=60_000_000,
                         league_id=LEAGUE, guardrails=g,
                         manager_listing_prices={"1": 10_500_000})
    assert len(props) == 1
    assert props[0].action.amount == 10_500_000 + g.bid_psych_offset
    assert "Anker max(Listing, MW)" in props[0].rationale


def test_manager_listing_below_mv_anchors_at_market_value():
    """err 5080 „UnderpayNotAllowed" (Leiter-Beweis 24.08.): Manager-Listings
    verlangen ≥ 100 % MW — der Listing-Preis ist Deko. Selbst eine künftig auf
    Underpay getunte Staffel (bid_ladder_opening < 0, Sniper-Idee) darf bei
    Manager-Listings nie unter MW + Offset landen; der Anker ist max(Listing, MW)."""
    g = Guardrails(bid_ladder_opening=-0.05)  # Staffel allein läge bei 95 % MW
    props = propose_buys([cand("1", 10_000_000, 0.8)], budget=60_000_000,
                         league_id=LEAGUE, guardrails=g,
                         manager_listing_prices={"1": 6_000_000})
    assert len(props) == 1
    assert props[0].action.amount == 10_000_000 + g.bid_psych_offset  # MW-Anker, nie 95 %
    # Gegenprobe: dasselbe Listing als Kickbase-Listing dürfte weiter underpayen.
    kb = propose_buys([cand("1", 10_000_000, 0.8)], budget=60_000_000,
                      league_id=LEAGUE, guardrails=g)
    assert kb[0].action.amount == int(10_000_000 * 0.95) + g.bid_psych_offset


def test_manager_anchor_never_lowers_standard_bid():
    """Listing UNTER dem Standard-Erstgebot (Schnäppchen): max() behält die Staffel."""
    g = Guardrails()
    props = propose_buys([cand("1", 10_000_000, 0.8)], budget=60_000_000,
                         league_id=LEAGUE, guardrails=g,
                         manager_listing_prices={"1": 9_000_000})
    expected = int(10_000_000 * (1 + g.bid_ladder_opening)) + g.bid_psych_offset
    assert props[0].action.amount == expected


def test_manager_anchor_capped_at_premium():
    """Verkäufer verlangt mehr als 110 % MW: die Kappe bleibt das Maximalgebot."""
    g = Guardrails()
    props = propose_buys([cand("1", 10_000_000, 0.8)], budget=60_000_000,
                         league_id=LEAGUE, guardrails=g,
                         manager_listing_prices={"1": 12_000_000})
    assert props[0].action.amount == int(10_000_000 * g.max_bid_premium)


def test_kickbase_listing_unaffected_by_anchor_map():
    g = Guardrails()
    props = propose_buys([cand("1", 10_000_000, 0.8)], budget=60_000_000,
                         league_id=LEAGUE, guardrails=g,
                         manager_listing_prices={"other": 12_000_000})
    expected = int(10_000_000 * (1 + g.bid_ladder_opening)) + g.bid_psych_offset
    assert props[0].action.amount == expected


# --- Trend-Verkaufsregel (24.08., Emils Hoffmeier-Punkt: Wertverlierer ohne Pfad) ---

def trend_cand(
    pid: str,
    total: float = 0.50,
    trend: float = 0.10,
    lineup: float = 0.20,
    injury: str | None = None,
    position: int = 3,
) -> ExplosionCandidate:
    return ExplosionCandidate(
        player_id=pid,
        name=f"Spieler {pid}",
        team_id="1",
        position=position,
        current_market_value=14_000_000,
        total_score=total,
        lineup_score=lineup,
        signals=[Signal(name="trend", score=trend, weight=0.15,
                        reason="Marktwert fällt (-3.59M in 7 Tagen).")],
        injury_severity=injury,
    )


def test_trend_sell_lists_value_loser_above_score_threshold():
    """Score 0.50 (über Verkaufsschwelle), aber Trend abgestürzt + kein
    Startelf-Pfad → listen mit rule_id sell_trend."""
    props = propose_sells(squad_with(trend_cand("h1")), LEAGUE, Guardrails())
    mine = [p for p in props if p.action.player_id == "h1"]
    assert len(mine) == 1
    assert mine[0].rule_id == "sell_trend"
    assert mine[0].action.action_type == "list"
    assert "Wertverlierer" in mine[0].rationale


def test_trend_sell_lists_starters_too():
    """Fable 5.1: Auch Starter mit MW-Delle werden aktiv rotiert/listed."""
    props = propose_sells(squad_with(trend_cand("h1", lineup=0.60)), LEAGUE, Guardrails())
    mine = [p for p in props if p.action.player_id == "h1"]
    assert len(mine) == 1
    assert mine[0].rule_id == "sell_trend"


def test_trend_sell_spares_mild_trends():
    props = propose_sells(squad_with(trend_cand("h1", trend=0.30)), LEAGUE, Guardrails())
    assert not [p for p in props if p.action.player_id == "h1"]


def test_trend_sell_lists_short_injured_value_losers():
    """Fable 5.1: Kurzzeit-Ausfall ist kein Freifahrtschein für fallenden MW."""
    props = propose_sells(
        squad_with(trend_cand("h1", injury="short")), LEAGUE, Guardrails()
    )
    mine = [p for p in props if p.action.player_id == "h1"]
    assert len(mine) == 1
    assert mine[0].action.action_type == "list"


def test_trend_sell_respects_protected_and_coverage():
    g = Guardrails().with_protected(["h1"])
    props = propose_sells(squad_with(trend_cand("h1")), LEAGUE, g)
    assert not [p for p in props if p.action.player_id == "h1"]
    # Deckung: der EINZIGE Torwart fällt nie der Trend-Regel zum Opfer.
    squad = viable_squad()
    squad[0] = trend_cand("gk1", position=1)
    assert not [p for p in propose_sells(squad, LEAGUE, Guardrails())
                if p.action.player_id == "gk1"]


# --- Positions-Not-Regel (24.08., Kader-Gesetz: Toleranz bei Positions-Lücke) ---

def test_positions_in_need_flags_gaps_and_single_keeper():
    """Bedarf entspricht fehlenden Plätzen der nächstliegenden echten Formation."""
    assert positions_in_need({1: 1, 2: 5, 3: 5, 4: 0}) == {4}
    assert positions_in_need({1: 2, 2: 5, 3: 5, 4: 3}) == set()
    assert positions_in_need({1: 2, 2: 3, 3: 2, 4: 2}) == {3, 4}


def test_formation_need_counts_accepts_complete_541_without_reserve():
    assert formation_need_counts({1: 1, 2: 5, 3: 4, 4: 1}) == {}


def test_only_actual_missing_slots_receive_need_premium():
    g = Guardrails(daily_buy_cap=3)
    props = propose_buys(
        [
            cand("s1", 1_000_000, 0.95, position=4),
            cand("s2", 1_000_000, 0.90, position=4),
            cand("s3", 1_000_000, 0.85, position=4),
        ],
        budget=20_000_000,
        league_id=LEAGUE,
        guardrails=g,
        need_positions={4},
        need_counts={4: 1},
    )
    assert len(props) == 3
    assert [p.action.squad_need for p in props] == [True, False, False]


def test_need_position_lowers_buy_threshold():
    """ST-Not: ein 0.50er-Stürmer (unter der 0.55-Schwelle) wird vorgeschlagen,
    mit transparenter Not-Begründung."""
    g = Guardrails()
    st = cand("st1", 5_000_000, 0.50, position=4)
    assert propose_buys([st], budget=30_000_000, league_id=LEAGUE, guardrails=g) == []
    props = propose_buys([st], budget=30_000_000, league_id=LEAGUE, guardrails=g,
                         need_positions={4})
    assert len(props) == 1
    assert "Positions-Not ST" in props[0].rationale


def test_need_tolerance_only_applies_to_needed_position():
    """MF 0.50 bleibt draußen, auch wenn ST in Not ist — und der sortierte Loop
    bricht nicht vorzeitig ab (Not-Kandidat NACH stärkerem Nicht-Not-Kandidaten)."""
    g = Guardrails()
    mf = cand("mf1", 5_000_000, 0.52, position=3)
    st = cand("st1", 5_000_000, 0.50, position=4)
    props = propose_buys([mf, st], budget=30_000_000, league_id=LEAGUE,
                         guardrails=g, need_positions={4})
    assert [p.action.player_id for p in props] == ["st1"]


def test_below_tolerance_floor_still_breaks():
    g = Guardrails()
    st = cand("st1", 5_000_000, 0.40, position=4)  # unter 0.55 − 0.07
    assert propose_buys([st], budget=30_000_000, league_id=LEAGUE, guardrails=g,
                        need_positions={4}) == []


# --- propose_accepts (24.08., Accept-Pfad: Angebote auf eigene Listings) ---

def _offer(pid: str, mv: int, amount: int, oid: str = "O1", name: str = "Spieler"):
    return IncomingOffer(pid, name, "1", 3, mv, amount, oid)


def _positions(squad):
    return {c.player_id: c.position for c in squad}


def test_accepts_take_best_offer_above_floor_ordered_by_amount():
    """Dedupe aufs höchste Angebot je Spieler, Reihenfolge nach Betrag (Konto-
    Wirkung zuerst), Floor 103 % erzwungen."""
    squad = viable_squad()
    offers = [
        _offer("m0", 10_000_000, 10_300_000, "A"),
        _offer("m0", 10_000_000, 10_500_000, "B"),   # besser — gewinnt
        _offer("m1", 20_000_000, 21_000_000, "C"),   # größte Konto-Wirkung → zuerst
        _offer("m2", 10_000_000, 10_200_000, "D"),   # 102 % < Floor → verfallen lassen
    ]
    props = propose_accepts(offers, _positions(squad), LEAGUE, Guardrails())
    assert [(p.action.player_id, p.action.offer_id) for p in props] == [("m1", "C"),
                                                                        ("m0", "B")]
    assert all(p.action.action_type == "accept" for p in props)
    assert props[0].requires_approval is True  # 21 M > 5-M-Schwelle


def test_accepts_never_break_formation_or_squad_minimum():
    """Vogt-Fall: das Angebot auf den EINZIGEN Stürmer wird nie angenommen;
    ebenso stoppt die 11er-Grenze."""
    squad = [cand("gk1", 2_000_000, 0.8, position=1)]
    squad += [cand(f"d{i}", 2_000_000, 0.8, position=2) for i in range(4)]
    squad += [cand(f"m{i}", 2_000_000, 0.8, position=3) for i in range(5)]
    squad += [cand("st1", 2_000_000, 0.8, position=4)]  # einziger ST, 11er-Kader
    offers = [_offer("st1", 1_000_000, 1_100_000, "S"), _offer("m0", 1_000_000, 1_100_000, "M")]
    props = propose_accepts(offers, _positions(squad), LEAGUE, Guardrails())
    assert props == []  # ST risse die Formation, m0 risse die 11er-Grenze


def test_accepts_respect_protection_and_unknown_players():
    g = Guardrails().with_protected(["m0"])
    squad = viable_squad()
    offers = [
        _offer("m0", 10_000_000, 10_400_000),
        _offer("fremd", 10_000_000, 10_400_000, "X"),   # nicht (mehr) im Kader
        IncomingOffer("m1", "NoOid", "1", 3, 10_000_000, 10_400_000, ""),  # ohne uoid
    ]
    assert propose_accepts(offers, _positions(squad), LEAGUE, g) == []


# --- Verfassung greift auch OHNE explizite Guardrails-Liste (26.08.) ---------
#
# Bis heute war `protected_player_ids` in jedem Produktionspfad leer: Alle
# Aufrufer benutzen DEFAULT_GUARDRAILS, und dessen Liste ist per Default leer.
# Die Vorschlags-Pfade fragen deshalb jetzt `all_protected_ids` (Feld + Env).

PROTECTED = "8329"  # Olise


def test_default_guardrails_never_propose_selling_a_protected_player():
    props = propose_sells(
        squad_with(cand(PROTECTED, 2_000_000, 0.1)), league_id=LEAGUE,
        guardrails=Guardrails(),
    )
    assert props == []


def _falling_protected() -> ExplosionCandidate:
    """Wertverlierer ohne Startelf-Pfad — Pass 2 von propose_sells würde greifen."""
    return ExplosionCandidate(
        player_id=PROTECTED, name="Olise", team_id="1", position=3,
        current_market_value=2_000_000, total_score=0.8, lineup_score=0.2,
        signals=[Signal(name="trend", score=0.05, weight=0.15, reason="MW fällt")],
    )


def test_default_guardrails_never_propose_a_trend_sale_of_a_protected_player():
    """Pass 2 (Wertverlierer ohne Startelf-Pfad) darf ihn ebenfalls nicht greifen."""
    props = propose_sells(
        squad_with(_falling_protected()), league_id=LEAGUE, guardrails=Guardrails()
    )
    assert props == []


def test_trend_sale_control_without_protection(monkeypatch):
    """Gegenprobe, damit der Test oben nicht aus dem falschen Grund grün ist."""
    monkeypatch.setenv("PROTECTED_PLAYER_IDS", "")
    props = propose_sells(
        squad_with(_falling_protected()), league_id=LEAGUE, guardrails=Guardrails()
    )
    assert len(props) == 1 and props[0].rule_id == "sell_trend"


def test_default_guardrails_never_accept_an_offer_for_a_protected_player():
    squad = viable_squad() + [cand(PROTECTED, 10_000_000, 0.8, position=3)]
    offers = [_offer(PROTECTED, 10_000_000, 12_000_000)]
    assert propose_accepts(offers, _positions(squad), LEAGUE, Guardrails()) == []


def test_incoming_offer_for_a_protected_player_is_declined_by_default():
    p = evaluate_incoming_offer(
        player_id=PROTECTED, market_value=2_000_000, offer_price=3_000_000,
        offer_id="77", league_id=LEAGUE, mv_prediction_7d=-0.1, guardrails=Guardrails(),
    )
    assert p is not None and p.action.action_type == "decline"


def test_empty_protected_env_restores_old_selling_behaviour(monkeypatch):
    monkeypatch.setenv("PROTECTED_PLAYER_IDS", "")
    props = propose_sells(
        squad_with(cand(PROTECTED, 2_000_000, 0.1)), league_id=LEAGUE,
        guardrails=Guardrails(),
    )
    assert len(props) == 1 and props[0].action.player_id == PROTECTED


# --- Gestaffelte Overpay-Kappe: Kader-Bedarf darf teurer werden (26.08.) -----


def test_position_need_bid_is_marked_and_targets_120_percent():
    """Positions-Not = Kader-Bedarf: Das Gebot trägt den Marker, und das
    Eskalations-Ziel steigt auf 120 % MW (Ljubicic-Lehre: 108 % verlieren)."""
    g = Guardrails()
    props = propose_buys([cand("1", 10_000_000, 0.9, position=4)], budget=60_000_000,
                         league_id=LEAGUE, guardrails=g, need_positions={4})
    assert len(props) == 1
    assert props[0].action.squad_need is True
    target = int(10_000_000 * (1 + g.bid_overpay_need)) + g.bid_psych_offset
    assert target >= int(10_000_000 * 1.20)      # 120-%-Stufe der Erwartung erreicht
    assert f"Ziel bei Eskalation {target / 1_000_000:.2f}M €" in props[0].rationale
    assert "Kader-Bedarf ⇒ Kappe 135% MW statt 110%" in props[0].rationale
    # Vollautomation: Ein echter Starterbedarf wartet nicht mehr auf eine
    # Discord-Erinnerung, sondern steigt direkt wettbewerbsfähig ein.
    assert props[0].action.amount == target


def test_bid_without_need_keeps_low_opening_ladder():
    g = Guardrails()
    props = propose_buys(
        [cand("1", 10_000_000, 0.9, position=4)],
        budget=60_000_000,
        league_id=LEAGUE,
        guardrails=g,
    )
    assert props[0].action.amount == int(10_000_000 * (1 + g.bid_ladder_opening)) + (
        g.bid_psych_offset
    )


def test_need_bid_cap_is_135_percent_not_110():
    """Manager verlangt 130 % MW für einen Spieler, ohne den die Formation nicht
    steht: früher endete das an der 110-%-Kappe, jetzt geht es durch."""
    g = Guardrails()
    props = propose_buys([cand("1", 10_000_000, 0.9, position=4)], budget=60_000_000,
                         league_id=LEAGUE, guardrails=g, need_positions={4},
                         manager_listing_prices={"1": 13_000_000})
    assert len(props) == 1
    assert props[0].action.amount == 13_000_000 + g.bid_psych_offset
    assert props[0].action.amount <= int(10_000_000 * g.max_bid_premium_need)


def test_need_bid_still_capped_at_the_need_premium():
    g = Guardrails()
    props = propose_buys([cand("1", 10_000_000, 0.9, position=4)], budget=60_000_000,
                         league_id=LEAGUE, guardrails=g, need_positions={4},
                         manager_listing_prices={"1": 20_000_000})
    assert props[0].action.amount == int(10_000_000 * g.max_bid_premium_need)


def test_bid_without_position_need_keeps_standard_cap_and_marker():
    """Gegenprobe: ohne Kader-Bedarf bleibt alles wie bisher (110 %, kein Marker)."""
    g = Guardrails()
    props = propose_buys([cand("1", 10_000_000, 0.9)], budget=60_000_000,
                         league_id=LEAGUE, guardrails=g,
                         manager_listing_prices={"1": 20_000_000})
    assert props[0].action.squad_need is False
    assert props[0].action.amount == int(10_000_000 * g.max_bid_premium)


# ---------------------------------------------------------------- Kauf-Diagnose
# Lehre vom 28.08.: Der Planner legte null Vorhaben an, obwohl das Kader-Gesetz
# verletzt war. Der Grund (kein Kandidat passte in die konto-gekoppelte
# Gebotskappe) war nur durch Code-Lesen + Box-Lauf zu ermitteln. buy_blocker_reason
# beantwortet dieselbe Frage in einer Zeile.


def test_blocker_none_wenn_ein_kauf_moeglich_waere() -> None:
    """Kandidat über Schwelle UND unter der Kappe ⇒ keine Blockade zu melden."""
    g = Guardrails()
    reason = buy_blocker_reason([cand("a", 1_000_000, 0.80)], 10_000_000, g)
    assert reason is None


def test_blocker_meldet_leeres_universum() -> None:
    reason = buy_blocker_reason([], 10_000_000, Guardrails())
    assert reason is not None
    assert "Kauf-Universum" in reason


def test_blocker_meldet_zu_schwache_scores() -> None:
    """Alle unter der Schwelle ⇒ Marktlage, nicht Geldmangel."""
    g = Guardrails()
    reason = buy_blocker_reason([cand("a", 1_000_000, 0.20)], 10_000_000, g)
    assert reason is not None
    assert "Schwelle" in reason
    assert "0.20" in reason


def test_blocker_meldet_gebotskappe_mit_billigstem_kandidaten() -> None:
    """Der reale Fall vom 28.08.: Score reicht, aber das Konto deckelt das Gebot."""
    g = Guardrails()
    # Konto 9,23 M ⇒ Kappe 5,54 M. Beide Kandidaten sind stark, aber zu teuer.
    reason = buy_blocker_reason(
        [cand("teuer", 20_000_000, 0.70), cand("billig", 6_490_000, 0.60, name="Schnellbacher")],
        9_231_524,
        g,
    )
    assert reason is not None
    assert "Gebotskappe" in reason
    # Der BILLIGSTE über der Schwelle ist die nützliche Information — er sagt,
    # wie viel Konto fehlt.
    assert "Schnellbacher" in reason


def test_blocker_beruecksichtigt_positions_not() -> None:
    """Positions-Not senkt die Schwelle — ein Kandidat knapp darunter zählt dann mit."""
    g = Guardrails()
    schwach = cand("s", 30_000_000, g.buy_score_threshold - 0.03, position=4)
    ohne_not = buy_blocker_reason([schwach], 9_231_524, g)
    mit_not = buy_blocker_reason([schwach], 9_231_524, g, need_positions={4})
    assert "Schwelle" in (ohne_not or "")      # ohne Not: zu schwach
    assert "Gebotskappe" in (mit_not or "")    # mit Not: stark genug, aber zu teuer
