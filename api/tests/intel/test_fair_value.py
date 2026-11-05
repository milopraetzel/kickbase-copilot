"""Fair-Value-Modell (intel/fair_value): Potenz-Fit, Konfidenz-Dämpfung, enrich.

Reine Mathe-Tests ohne Netz/DB: eine synthetische Potenz-Wolke muss a/b
rekonstruieren, der Fit-Filter („etabliert") muss greifen, die Dämpfung muss
die ABWEICHUNG mischen (nicht zwei Preise), und alle Randfälle liefern None
statt zu raten.
"""

from __future__ import annotations

import pytest

from kickbase_agent.intel import fair_value as fv

#: Synthetische Kurve: mv = A · ap^B — exakt auf der Kurve liegende Wolke.
A, B = 50_000.0, 1.4


def _cloud(n: int = 40, ap0: int = 20, step: int = 5) -> list[dict]:
    """n Spieler exakt auf der Kurve, alle etabliert (tp = ap · 30 ≥ 600)."""
    out = []
    for i in range(n):
        ap = ap0 + i * step
        out.append({"ap": ap, "mv": int(A * ap**B), "tp": ap * 30})
    return out


def _on_curve(ap: float) -> float:
    return A * ap**B


# ------------------------------------------------------------------- Fit


def test_fit_reconstructs_power_curve_from_synthetic_cloud():
    curve = fv.fit_fair_curve(_cloud())
    assert curve is not None
    assert curve["n"] == 40
    assert abs(curve["b"] - B) < 0.02
    assert abs(curve["a"] - A) / A < 0.05


def test_fit_filters_unestablished_players():
    cloud = _cloud()
    # Unter der MW-Grenze, unter der tp-Grenze, ap fehlt/0 — alles keine Stützpunkte.
    cloud += [
        {"ap": 90, "mv": 2_999_999, "tp": 5000},        # MW < 3 M
        {"ap": 90, "mv": 9_000_000, "tp": 599},          # tp < 600
        {"ap": 0, "mv": 9_000_000, "tp": 5000},          # ap 0
        {"mv": 9_000_000, "tp": 5000},                   # ap fehlt
        {"ap": 90, "tp": 5000},                          # mv fehlt
    ]
    curve = fv.fit_fair_curve(cloud)
    assert curve is not None and curve["n"] == 40


def test_fit_without_tp_falls_back_to_ap_threshold():
    # Ohne Gesamtpunkte gilt ap ≥ 60: 12 Spieler ab ap 60 stützen, ap 59 nicht.
    cloud = [{"ap": 60 + i * 10, "mv": int(_on_curve(60 + i * 10))} for i in range(12)]
    cloud.append({"ap": 59, "mv": int(_on_curve(59))})
    curve = fv.fit_fair_curve(cloud)
    assert curve is not None and curve["n"] == 12


def test_fit_needs_at_least_min_points():
    assert fv.fit_fair_curve(_cloud(n=fv.MIN_FIT_POINTS - 1)) is None
    assert fv.fit_fair_curve([]) is None
    assert fv.fit_fair_curve(_cloud(n=fv.MIN_FIT_POINTS)) is not None


def test_fit_degenerate_same_ap_returns_none():
    cloud = [{"ap": 100, "mv": 5_000_000 + i, "tp": 3000} for i in range(20)]
    assert fv.fit_fair_curve(cloud) is None


# ------------------------------------------------------------ fair_value


@pytest.fixture
def curve() -> dict:
    c = fv.fit_fair_curve(_cloud())
    assert c is not None
    return c


def test_fair_value_full_confidence_lands_on_curve(curve):
    # tp ≥ 1000 → volle Anrechnung: fair = Kurvenwert, egal wo der MW steht.
    fair = fv.fair_value(100, 2000, 10_000_000, curve)
    assert fair == pytest.approx(_on_curve(100), rel=0.02)


@pytest.mark.parametrize(
    ("tp", "weight"),
    [(0, 0.5), (199, 0.5), (200, 0.75), (599, 0.75), (600, 0.9), (999, 0.9),
     (1000, 1.0), (5000, 1.0), (None, 0.75)],
)
def test_fair_value_dampens_the_deviation_not_the_price(tp, weight, curve):
    mv = 10_000_000
    raw = curve["a"] * 100 ** curve["b"]
    fair = fv.fair_value(100, tp, mv, curve)
    # Gedämpft wird die ABWEICHUNG: fair = mv + w · (Kurve − mv).
    assert fair == round(mv + weight * (raw - mv))


def test_fair_value_edge_cases_return_none(curve):
    assert fv.fair_value(0, 2000, 10_000_000, curve) is None      # ap 0
    assert fv.fair_value(-5, 2000, 10_000_000, curve) is None     # ap negativ
    assert fv.fair_value(100, 2000, 10_000_000, None) is None     # keine Kurve
    assert fv.fair_value(100, 2000, 0, curve) is None             # mv 0 = kein Anker
    assert fv.fair_value(100, 2000, 10_000_000, {"a": 0, "b": 1}) is None
    assert fv.fair_value(100, 2000, 10_000_000, {"x": 1}) is None


def test_fair_value_never_negative():
    # Extrem fallende Kurve + hoher MW: das Ergebnis wird bei 0 gekappt.
    fair = fv.fair_value(2, 5000, 1_000_000, {"a": 1.0, "b": 1.0, "n": 12})
    assert fair is not None and fair >= 0


# ----------------------------------------------------------------- enrich


def test_enrich_sets_all_three_keys_in_place(curve):
    rows = [
        {"avg": 100, "punkte": 2000, "mv": 5_000_000},   # unter der Kurve → Δ positiv
        {"ap": 100, "tp": 2000, "mv": 90_000_000},        # über der Kurve → Δ negativ
    ]
    fv.enrich(rows, curve)
    below, above = rows
    assert below["fair_eur"] == pytest.approx(_on_curve(100), rel=0.02)
    assert below["fair_fmt"].endswith(" M€")
    assert below["fair_delta_pct"] > 0
    assert above["fair_delta_pct"] < 0
    # Δ ist gerundet (ganze Prozentpunkte) und aus fair/mv berechnet.
    assert below["fair_delta_pct"] == round(
        (below["fair_eur"] - 5_000_000) / 5_000_000 * 100
    )
    assert isinstance(below["fair_delta_pct"], int)


def test_enrich_is_none_safe(curve):
    rows = [
        {},                                   # gar nichts
        {"mv": 5_000_000},                    # kein Ø
        {"avg": 100, "punkte": 2000},         # kein MW
        {"avg": 100, "punkte": 2000, "mv": 0},
        {"avg": True, "mv": 5_000_000},       # bool ist keine Zahl
    ]
    fv.enrich(rows, curve)
    for r in rows:
        assert r["fair_eur"] is None and r["fair_fmt"] is None
        assert r["fair_delta_pct"] is None
    # Ohne Kurve: Keys da, alles None — Renderer zeigen „–".
    row = [{"avg": 100, "punkte": 2000, "mv": 5_000_000}]
    fv.enrich(row, None)
    assert row[0] == {"avg": 100, "punkte": 2000, "mv": 5_000_000,
                      "fair_eur": None, "fair_fmt": None, "fair_delta_pct": None}


def test_enrich_prefers_detail_keys_over_cloud_keys(curve):
    # avg/punkte (Detail) schlagen ap/tp (Wolke) — beide Schreibweisen verstanden.
    rows = [{"avg": 100, "ap": 10, "punkte": 2000, "tp": 10, "mv": 5_000_000}]
    fv.enrich(rows, curve)
    assert rows[0]["fair_eur"] == pytest.approx(_on_curve(100), rel=0.02)


def test_fmt_eur_matches_cockpit_money_style():
    assert fv._fmt_eur(31_550_000) == "31,55 M€"
    assert fv._fmt_eur(64_000_000) == "64 M€"
    assert fv._fmt_eur(900_000) == "900 T€"


def test_docstring_names_inspiration_and_no_future_claim():
    doc = fv.__doc__ or ""
    assert "base-xi.de" in doc and "KEIN Zukunftsblick" in doc
    assert "eigene" in doc  # Berechnung/Daten sind eigene
