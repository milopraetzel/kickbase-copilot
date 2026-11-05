"""Application configuration loaded from environment variables."""

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Auth-Weg A (bevorzugt): Token direkt aus dem Browser-Login (passwortlos, z. B.
    # bei Apple/Google-Accounts). Auth-Weg B (Fallback): E-Mail + Passwort.
    kickbase_token: str = ""
    kickbase_refresh_token: str = ""
    kickbase_email: str = ""
    kickbase_password: str = ""
    database_url: str = Field(...)

    kickbase_base_url: str = "https://api.kickbase.com"
    kickbase_rate_limit_per_second: float = 1.0
    # Aktive Liga (Emil hat mehrere fast gleichnamige). Leer = erste aus selection.
    kickbase_league_id: str = ""
    # Kaderlimit der aktiven Liga. Laut Kickbase-Hilfe je Liga zwischen 11 und 25
    # Spielern vom Admin einstellbar (help.kickbase.com/en/help/kaderbegrenzung).
    # TODO: echten Wert je Liga aus den Liga-Settings der API lesen — der
    # Settings-Block `selection.it[]` enthält Liga-Regeln; ob darin ein
    # Kaderlimit-Feld existiert, ist ungeprüft (nicht raten, nur den dort
    # bestätigten Wert übernehmen).
    kickbase_squad_limit: int = 18

    openliga_base_url: str = "https://api.openligadb.de"
    kicker_rss_url: str = "https://newsfeed.kicker.de/news/bundesliga"

    log_level: str = "INFO"

    anthropic_api_key: str = ""
    # TypeSafe (System One) beurteilt LigaInsider-Ausfallgründe
    # (scouts/ligainsider/severity_judge.py). Leer = nur die Wortsuche.
    typesafe_api_key: str = ""

    # --- Agent-Sicherheit (Phase 1) ---
    # VERFASSUNG, keine Stellschraube (26.08.26): kommagetrennte Kickbase-
    # Spieler-IDs, die NIE verkauft, gelistet oder per Angebot abgegeben werden.
    # Default 8329 = Olise (Emils Anweisung 25.08.: „Verkaufe nicht Olise, aber
    # sonst mach einfach die Entscheidung"). Bis 26.08. stand diese Anweisung
    # nirgends im Code — `protected_player_ids` war in JEDEM Produktionspfad leer.
    # Erzwungen wird sie an der einzigen Schreibstelle (agent/executor.py);
    # die Vorschlags-Pfade (decision/policies.py, worker/kickoff_guard.py)
    # bieten geschützte Spieler gar nicht erst an. Ändern darf diese Liste NUR
    # Emil (.env / Env `PROTECTED_PLAYER_IDS`), nie der Agent selbst.
    # Leer gesetzt = ausdrücklich kein Schutz (Altverhalten).
    protected_player_ids: str = "8329"
    # Standardmäßig führt der Agent KEINE echten Schreib-Aktionen aus.
    agent_dry_run: bool = True
    # Nur in dieser Liga sind Live-Writes ohne agent_allow_live erlaubt (Test-Liga).
    agent_test_league_id: str = ""
    # Muss explizit True sein, damit außerhalb der Test-Liga live geschrieben wird.
    agent_allow_live: bool = False
    # Ärmt NUR den Discord-Freigabe-Pfad (execute_staged nach menschlichem Klick):
    # erst mit diesem Flag löst ein Approval außerhalb des regulären Live-Interlocks
    # echte Writes aus. Zweite Verteidigungslinie — der Freigabe-Pfad darf
    # AGENT_ALLOW_LIVE nicht still umgehen (Review-Fund, 3. externes Review).
    agent_approve_live: bool = False
    # User-Agent-String für Kickbase-Requests (leer = httpx-Default). Hinweis zur
    # Datenquelle: Die Kickbase-AGB untersagen automatisiertes Spielen — der
    # Betrieb liegt in Emils Verantwortung; Schreibzugriffe laufen ausschließlich
    # über die explizite Freigabe-Architektur (dry_run, Test-Liga, Approval).
    kickbase_user_agent: str = ""

    # Zielpfad des stündlichen Cockpit-Builds (leer = Job deaktiviert).
    # Auf der Box zeigt er ins geteilte Volume, aus dem nginx die Web-App
    # unter localhost:8080 ausliefert.
    cockpit_html_path: str = ""

    # T-60-Notverkauf vor Anpfiff automatisch ausführen (verhindert 0-Punkte-Spieltag).
    agent_auto_purge: bool = False

    # Tägliche Prämie (Login-Bonus) automatisch einsammeln — risikofreiester Write.
    agent_auto_bonus: bool = False
    # Zwei-DB-Fix (24.08.): Box-API für MCP-Remote-Vorhaben. Gesetzt (lokale .env)
    # legen die MCP-Write-Tools Vorhaben auf der Box an statt lokal auszuführen.
    # Hermes verwendet laut Operator-Anleitung KBA_API_URL/AUTH. Beide Namen
    # müssen dasselbe Ziel wählen; explizit leeres KBA_REMOTE_* bleibt lokal.
    kba_remote_api_url: str = Field(
        default="", validation_alias=AliasChoices("kba_remote_api_url", "kba_api_url"),
    )
    kba_remote_api_auth: str = Field(
        default="", validation_alias=AliasChoices("kba_remote_api_auth", "kba_api_auth"),
    )  # "user:pass" (Traefik-Basic-Auth)
    # Schreib-Token der Box-API (Codex-Folge-Review 24.08., P1): Traefik-Basic-
    # Auth schützt nur von AUSSEN — im Docker-Netz kann jeder Container Uvicorn
    # direkt erreichen. Gesetzt (Box-.env, Emils Hoheit) verlangt POST
    # /api/intents den Header X-KBA-Write-Token; der lokale MCP sendet denselben
    # Wert (lokale .env) mit. Leer = Bestandsverhalten (nur Traefik).
    kba_api_write_token: str = ""

    # Aufstellung automatisch setzen (risikoärmster Write: kein Geld, bis Anpfiff in
    # der App überschreibbar). Eigener Schalter — armt NUR lineup-Writes, keine Trades.
    agent_auto_lineup: bool = False

    # Stufe 2 (Trading autonom) — DORMANT, Default AUS: ärmt NUR den Auto-Trading-
    # Pfad (worker/auto_trading.py → Executor-Interlock für bid/sell/list mit
    # rule_id-Präfix "auto-trading"). Solange 0, fährt der 22:30-Job im Schatten-
    # Modus (kein Write, nur Report). Freischaltung erst, wenn ALLE Stufe-2-
    # Kriterien der Strategie erfüllt sind (docs/STRATEGIE-SAISON-26-27.md:
    # 14 Tage fehlerfreie API-Writes, realisierter Trading-Gewinn > +500 k €,
    # ≥ 1 Spieltag fehlerfreier Anpfiff-Wächter) UND Emil sein explizites GO
    # gegeben hat. Ab 5 M € und Tier-1 bleibt IMMER Emil, in jeder Stufe.
    agent_auto_trading: bool = False

    # Steuerzentrale (Vorhaben mit Veto-Fenster, worker/intents.py; Spec
    # 2026-08-23-steuerzentrale.md): ärmt NUR den Intent-Pfad — Executor-Interlock
    # für bid/sell/list mit rule_id-Präfix "intent-". Default AUS: Vorhaben werden
    # geplant und sind in der Web-App sichtbar, der Intent-Executor meldet ehrlich
    # refused/skipped_dry_run in `result`. Scharf erst nach Emils »scharf«
    # (Box-.env). Nicht gleichzeitig mit AGENT_AUTO_TRADING ärmen (doppelte Gebote).
    agent_auto_intents: bool = False

    # Handels-Autopilot (worker/trade_autopilot.py, Emil 01.10.2026: „alles soll automatisch
    # geschehen ich will nicht dass ich bieten muss“): legt Handelsvorhaben an — ausgeführt
    # werden sie nur über AGENT_AUTO_INTENTS. Bewusst Default AN (Emils Auftrag). 0 ist der
    # Halt NUR für den Handel: keine neuen Handelsgebote, geplante werden geräumt, Verkäufe
    # der Handelsspieler laufen weiter (sonst bliebe ein Minus aus dem Handel stehen).
    agent_trade_autopilot: bool = True

    # Discord-Steuerkarten für Vorhaben sind nur eine optionale Veto-/Sofort-
    # Oberfläche, niemals eine Freigabepflicht. In Emils Vollautomatik aus:
    # Entscheidungen und Ausführung bleiben in Zentrale/Executor, Discord erhält
    # keine Bestätigungsbuttons. Bereits gepostete offene Karten werden stillgelegt.
    discord_intent_controls: bool = True

    # Emil 09.09.26: Discord ist ein Digest-Kanal, kein Live-Log. Routinejobs,
    # Intents und gewöhnliche Fehler laufen weiter und bleiben in DB/WebApp/Logs,
    # zugestellt werden nur Morgen-/Abendbriefing und explizite echte Blocker.
    discord_digest_only: bool = True

    # Selbstumbau (autonomer Lernzyklus, Stufe E; Spec 2026-08-26): ärmt NUR den
    # Weg agent/self_change.py — Branch, Änderung, Tests, `kba guardrails-check`,
    # Commit mit Marker "[selbst]", Deploy, Gesundheitsprüfung, Rollback bei
    # Fehlschlag. Default AUS: Der Zyklus erkennt und urteilt, führt aber nichts
    # aus, sondern schlägt vor. Scharfschalten ist Emils Entscheidung (Box-.env) —
    # der Agent darf dieses Flag nie selbst setzen (eiserne Regel 2).
    agent_self_change: bool = False

    # Stufe 2 (Trading autonom): Auto-Käufe/Verkäufe der Trading-Slots bis zu diesem
    # Betrag/Spieler laufen ohne Approval; darüber + Core-Team bleiben Discord-Proposal.
    # Greift erst, wenn der Trading-Auto-Execute gebaut ist (aktuell dormant).
    agent_trading_max_per_player: int = 1_500_000
    # Sekunden Pause zwischen mehreren echten Kaufgeboten (Rate-Limit-Schonung).
    agent_write_spacing_seconds: float = 2.0

    # --- Discord-Approval-Kanal (Phase 3) ---
    discord_bot_token: str = ""
    discord_channel_id: str = ""
    # Nur diese Discord-User-ID darf Vorschläge freigeben (leer = niemand).
    discord_user_id: str = ""

    # KI-Rechercheur (Phase 2): Server-Slot 08:30 über die Anthropic-API. Default AUS —
    # standardmäßig läuft die Recherche lokal per `kba research` (claude-CLI/Abo).
    ai_research_on_server: bool = False
    # 1-Jahr-Token aus `claude setup-token` — lässt die lokale claude-CLI headless
    # übers Abo laufen (statt abgelaufener OAuth-Session). Leer = CLI-Default-Auth.
    claude_code_oauth_token: str = ""

    # Insider-Quellen der Recherche: Kickbase-YouTube-Kanäle (Handle oder UC-ID,
    # kommagetrennt) — Titel neuer Videos fließen in die Morgen-Recherche.
    community_youtube_channels: str = "@kickbaseguide,UCJ7I7AoSvzL0rWe9rwM4sYg"

    # --- LigaInsider-Scout (Schritt 6): Startelf-Prognose + Ausfälle ---
    ligainsider_enabled: bool = True
    ligainsider_base_url: str = "https://www.ligainsider.de"
    # Lastschonung/höfliches Client-Verhalten: ~1,4 s Abstand zwischen Requests.
    ligainsider_rate_limit_per_second: float = 0.7
    # User-Agent für LigaInsider-Requests (Browser-Kennung).
    ligainsider_user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    # Snapshot höchstens so oft neu holen (Sek.); ein Briefing-Lauf teilt sich einen.
    ligainsider_ttl_seconds: int = 21_600  # 6 h


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
