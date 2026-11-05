"""KI-Rechercheur: robustes JSON-Parsing + Discord-Formatierung (ohne echten Claude-Call)."""

from kickbase_agent.intel.models import ExplosionCandidate, Signal
from kickbase_agent.intel.researcher import (
    ClaudeAuthError,
    ClaudeCliError,
    ResearchBriefing,
    _parse_json,
    build_user_prompt,
    parse_research,
)
from kickbase_agent.news.models import NewsArticle
from kickbase_agent.worker.research import format_research


def test_parse_plain_json():
    raw = '{"tips": [{"name": "X", "reason": "steigt"}], "risks": [], "take": "kaufen"}'
    data = _parse_json(raw)
    assert data["tips"][0]["name"] == "X"
    assert data["take"] == "kaufen"


def test_parse_json_with_code_fence():
    raw = '```json\n{"tips": [], "risks": [], "take": "ruhig"}\n```'
    assert _parse_json(raw)["take"] == "ruhig"


def test_format_research_renders_all_sections():
    b = ResearchBriefing(
        tips=[{"name": "Bernardo", "reason": "unter Marktwert"}],
        risks=[{"name": "Vogt", "reason": "Rotationsgefahr"}],
        take="Diese Woche defensiv bleiben.",
    )
    text = format_research(b)
    assert "KI-Recherche" in text
    assert "Bernardo" in text and "unter Marktwert" in text
    assert "Vogt" in text and "Rotationsgefahr" in text
    assert "Diese Woche defensiv bleiben." in text
    assert "keine Transaktion" in text   # Beratungs-Disclaimer


def test_format_research_quiet_when_empty():
    text = format_research(ResearchBriefing())
    assert "keine klaren Signale" in text


def test_parse_research_caps_and_maps():
    raw = '{"tips":[{"name":"A"},{"name":"B"},{"name":"C"},{"name":"D"}],"risks":[],"take":"x"}'
    b = parse_research(raw)
    assert len(b.tips) == 3        # auf max 3 gedeckelt
    assert b.take == "x"


def test_build_user_prompt_marks_news_untrusted():
    from datetime import datetime
    cand = ExplosionCandidate(
        player_id="1", name="Musiala", team_id="2", position=3,
        current_market_value=25_000_000, total_score=0.7,
        signals=[Signal(name="form", score=0.8, weight=0.25, reason="stark")],
    )
    news = [NewsArticle(source="kicker", url="", title="Musiala trifft doppelt",
                        body="", published_at=datetime(2026, 8, 19))]
    prompt = build_user_prompt([cand], news)
    assert "Musiala" in prompt
    assert "UNSICHERE EXTERNE DATEN" in prompt   # Prompt-Injection-Rahmen
    assert "Musiala trifft doppelt" in prompt


def test_auth_error_is_cli_error():
    assert issubclass(ClaudeAuthError, ClaudeCliError)
