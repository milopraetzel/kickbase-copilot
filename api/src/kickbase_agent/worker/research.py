"""KI-Rechercheur-Slot: baut Kandidaten + News, lässt Claude verdichten, postet nach Discord.

Läuft als eigener Morgen-Slot (08:30, vor dem 09:00-Puls). Fehlt der API-Key oder
fällt eine Quelle aus, wird sauber übersprungen — nie ein Crash des Workers.
"""

from __future__ import annotations

from kickbase_agent.config import get_settings
from kickbase_agent.db.session import SessionLocal
from kickbase_agent.intel.collector import collect_from_players
from kickbase_agent.intel.history import load_history, load_performance
from kickbase_agent.intel.researcher import AiResearcher, ResearchBriefing
from kickbase_agent.kickbase.factory import make_kickbase_client
from kickbase_agent.news.kicker_rss import KickerRSSClient
from kickbase_agent.notify.discord_channel import DiscordChannel
from kickbase_agent.scouts.ligainsider.profiteur import find_profiteurs, profiteur_lines
from kickbase_agent.scouts.ligainsider.service import ScoutResult, enrich_candidates


def profiteur_lines_from_scout(scout: ScoutResult, market_ids: set[str]) -> list[str]:
    """Profiteur-Zeilen aus einem kombinierten (Kader+Markt) Scout-Ergebnis."""
    if scout.snapshot is None:
        return []
    market = [c for c in scout.candidates if c.player_id in market_ids]
    return profiteur_lines(
        find_profiteurs(market, scout.candidates, scout.availabilities, scout.snapshot.injuries)
    )


def format_research(b: ResearchBriefing) -> str:
    lines = ["🧠 **KI-Recherche** — Geheimtipps & Risiken"]
    if b.tips:
        lines.append("💎 __Tipps__")
        lines += [f"• **{t.get('name', '?')}** — {t.get('reason', '')}" for t in b.tips]
    if b.risks:
        lines.append("⚠️ __Risiken__")
        lines += [f"• **{r.get('name', '?')}** — {r.get('reason', '')}" for r in b.risks]
    if b.take:
        lines.append(f"🎯 {b.take}")
    if not (b.tips or b.risks or b.take):
        lines.append("-# keine klaren Signale heute")
    lines.append("-# KI-Beratung · keine Transaktion · Dry-Run")
    return "\n".join(lines)


async def run_research(channel: DiscordChannel) -> bool:
    """Baut Kandidaten + News, ruft Claude, postet die Recherche. True = gepostet."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        await channel.send_info("🧠 KI-Rechercheur übersprungen — kein ANTHROPIC_API_KEY gesetzt.")
        return False

    client, auth = await make_kickbase_client()
    lg = auth.league_id
    if lg is None:
        return False

    squad = await client.get_squad(lg)
    market = await client.get_market(lg)
    players = [sp.player for sp in squad] + [mp.player for mp in market]
    ids = [p.id for p in players]
    fixtures = await client.get_matchdays()
    team_names = await client.get_team_names()
    with SessionLocal() as session:
        history = load_history(session, ids)
        perf = load_performance(session, ids)
    candidates = await collect_from_players(players, fixtures, team_names, history, perf)
    scout = await enrich_candidates(candidates)
    candidates = scout.candidates
    market_ids = {mp.player.id for mp in market}
    plines = profiteur_lines_from_scout(scout, market_ids)

    try:
        news = await KickerRSSClient().fetch()
    except Exception:  # noqa: BLE001 — News-Ausfall darf die Recherche nicht killen
        news = []

    try:
        briefing = await AiResearcher(settings.anthropic_api_key).research(
            candidates, news, plines
        )
    except Exception as exc:  # noqa: BLE001 — freundlicher Hinweis statt Traceback
        await channel.send_info(
            f"🧠 KI-Recherche nicht möglich ({type(exc).__name__}). "
            "Häufigste Ursache: Anthropic-Guthaben aufgebraucht — unter "
            "console.anthropic.com → Plans & Billing prüfen."
        )
        return False
    await channel.send_info(format_research(briefing), kind="recherche")
    return True
