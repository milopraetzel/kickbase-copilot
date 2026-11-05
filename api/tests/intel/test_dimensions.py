"""Spielprofil-Dimensionen (intel/dimensions): reine Skalen-Logik, Emils GO 24.08."""

from kickbase_agent.intel.dimensions import (
    Dim,
    effizienz_dim,
    einsatz_dim,
    empfehlung,
    player_dimensions,
    punktebasis_dim,
    risiko_dim,
    startelf_dim,
    upside_dim,
)


def test_startelf_fuses_li_with_kb_prob():
    assert startelf_dim("starter", 1).score == 95
    assert startelf_dim("starter", 1).label == "Sehr hoch"
    assert startelf_dim("starter", 5).score == 60
    assert startelf_dim("bench", 2).score == 33
    out = startelf_dim("out", 1)
    assert out.score == 0 and out.tone == "bad" and out.label == "Fällt aus"
    assert startelf_dim(None, None).label == "Offen"


def test_einsatz_proxy_is_honestly_labeled():
    d = einsatz_dim(3)
    assert d.label == "Rotation" and "Proxy" in d.note
    assert einsatz_dim(None).label == "Unbekannt"


def test_punktebasis_prefers_percentile_over_absolute():
    p = punktebasis_dim(90, percentile=0.97)
    assert p.score == 97 and "Top 3 %" in p.note
    a = punktebasis_dim(90, percentile=None)
    assert a.score == 75 and a.label == "Hoch"
    assert punktebasis_dim(None).label == "Keine Daten"


def test_upside_combines_trend_and_tip():
    assert upside_dim(0.8, None).score == 80
    assert upside_dim(0.5, 1.0).score == 70          # +20 Punkte Tipp-Deckel
    assert upside_dim(None, None).score == 50


def test_risiko_accumulates_reasons_and_caps():
    r = risiko_dim("out", "Kreuzbandriss", 5, -200_000, 10_000_000)
    assert r.score == 100 and r.tone == "bad"
    assert "Kreuzbandriss" in r.note and "KB-Stufe 5" in r.note
    ok = risiko_dim("starter", None, 1, 50_000, 10_000_000)
    assert ok.label == "Niedrig" and ok.tone == "neon"


def test_effizienz_points_per_million():
    d = effizienz_dim(96, 15_000_000)   # 6.4 Pkt/Mio
    assert d.label == "6.4 Pkt/Mio" and d.tone == "warn"
    top = effizienz_dim(85, 5_800_000)  # 14.7 → sehr gut
    assert top.tone == "neon" and "Sehr gut" in top.note
    assert effizienz_dim(0, 5_000_000).label == "Keine Daten"


def test_player_dimensions_order_and_types():
    dims = player_dimensions(
        li_status="starter", injury_reason=None, kb_prob=2, avg_points=85,
        percentile=0.8, trend_signal=0.7, tip_weight=0.8, mv24=100_000,
        market_value=10_300_000,
    )
    assert [d.key for d in dims] == [
        "startelf", "einsatz", "punkte", "upside", "risiko", "effizienz",
    ]
    assert all(isinstance(d, Dim) and 0 <= d.score <= 100 for d in dims)


def test_empfehlung_chip_rules():
    low_risk = risiko_dim("starter", None, 1, 0, 10_000_000)
    high_risk = risiko_dim("out", "Muskel", 5, -300_000, 10_000_000)
    assert empfehlung(0.6, low_risk) == ("Kaufen", "neon")
    assert empfehlung(0.6, high_risk) == ("Vorsicht", "warn")
    assert empfehlung(0.5, low_risk) == ("Beobachten", "warn")
    assert empfehlung(0.2, low_risk) == ("Meiden", "dim")
