"""Typer CLI entry — exposed as the `kba` command."""

from __future__ import annotations

import typer

from kickbase_agent.cli.competition import app as competition_app
from kickbase_agent.cli.dashboard import dashboard_html_command
from kickbase_agent.cli.forecast import app as forecast_app
from kickbase_agent.cli.guardrails import (
    freeze_command,
    freeze_status_command,
    guardrails_check_command,
    guardrails_seal_command,
    unfreeze_command,
)
from kickbase_agent.cli.kader import kader_command
from kickbase_agent.cli.learn import learn_evidence_command, learn_migrate_command
from kickbase_agent.cli.login import login_command
from kickbase_agent.cli.market_forecast import app as market_forecast_app
from kickbase_agent.cli.markt import markt_command
from kickbase_agent.cli.news import news_command
from kickbase_agent.cli.plan import plan_command
from kickbase_agent.cli.probe import probe_command
from kickbase_agent.cli.reports import report_add_command, reports_command
from kickbase_agent.cli.research import (
    post_discord_command,
    research_command,
    research_data_command,
)
from kickbase_agent.cli.rules import app as rules_app
from kickbase_agent.cli.rules import onboarding_command
from kickbase_agent.cli.snapshot import history_reload_command, snapshot_command
from kickbase_agent.cli.staging import staging_seed_command
from kickbase_agent.cli.tips import tip_command, tips_command

app = typer.Typer(
    help="Kickbase Agent — CLI for manual data operations.",
    no_args_is_help=True,
)
app.add_typer(competition_app, name="competition")
app.add_typer(forecast_app, name="forecast")
app.add_typer(market_forecast_app, name="market-forecast")
app.add_typer(rules_app, name="rules")


@app.callback()
def _main() -> None:
    """Kickbase Agent command-line interface."""


app.command(name="onboarding")(onboarding_command)
app.command(name="staging-seed")(staging_seed_command)
app.command(name="login")(login_command)
app.command(name="kader")(kader_command)
app.command(name="learn-migrate")(learn_migrate_command)
app.command(name="learn-evidence")(learn_evidence_command)
app.command(name="markt")(markt_command)
app.command(name="news")(news_command)
app.command(name="probe")(probe_command)
app.command(name="snapshot")(snapshot_command)
app.command(name="history-reload")(history_reload_command)
app.command(name="plan")(plan_command)
app.command(name="research")(research_command)
app.command(name="research-data")(research_data_command)
app.command(name="post-discord")(post_discord_command)
app.command(name="report-add")(report_add_command)
app.command(name="reports")(reports_command)
app.command(name="tip")(tip_command)
app.command(name="tips")(tips_command)
app.command(name="dashboard-html")(dashboard_html_command)
# Sicherungen (autonomer Lernzyklus, Stufe A): das Tor vor jedem Selbst-Deploy
# und der Einfrier-Zustand, den nur ein Mensch wieder aufhebt.
app.command(name="guardrails-check")(guardrails_check_command)
app.command(name="guardrails-seal")(guardrails_seal_command)
app.command(name="freeze-status")(freeze_status_command)
app.command(name="freeze")(freeze_command)
app.command(name="unfreeze")(unfreeze_command)


if __name__ == "__main__":
    app()
