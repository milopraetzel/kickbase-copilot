"""SQLAlchemy ORM models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from kickbase_agent.db.session import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kickbase_token: Mapped[str] = mapped_column(Text, nullable=False)
    kickbase_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)

    leagues: Mapped[list[League]] = relationship(back_populates="user")


class League(Base):
    __tablename__ = "leagues"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    kickbase_league_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    budget: Mapped[int] = mapped_column(BigInteger, default=0)
    team_value: Mapped[int] = mapped_column(BigInteger, default=0)

    user: Mapped[User] = relationship(back_populates="leagues")


class Player(Base):
    __tablename__ = "players"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kickbase_player_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    team: Mapped[str] = mapped_column(String(64), nullable=False)
    current_market_value: Mapped[int] = mapped_column(BigInteger, default=0)

    history: Mapped[list[PlayerHistory]] = relationship(back_populates="player")


class PlayerHistory(Base):
    __tablename__ = "player_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"), nullable=False, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    market_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    points: Mapped[int] = mapped_column(Integer, default=0)
    is_starter: Mapped[bool] = mapped_column(Boolean, default=False)

    player: Mapped[Player] = relationship(back_populates="history")


class TransferAction(Base):
    """Audit-Log + Idempotenz-Anker jeder Schreib-Aktion des Agenten."""

    __tablename__ = "transfer_actions"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_transfer_idem"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # bid | cancel_bid | accept | decline | list | delist | sell | lineup | bonus
    player_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    # pending | proposed | executed | failed | unknown | skipped_dry_run
    # | refused | rejected | expired | noop  (Details: Docstring in agent/executor.py)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel_msg_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    raw_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Serialisierte PlannedAction — erlaubt Ausführen nach Approval (offer_id,
    # formation, player_ids etc. bleiben erhalten).
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Freigabe-TTL: Vorschlag ist nur bis hierhin ausführbar — ein später
    # geklickter Approve-Button endet als 'expired' statt als alter Trade.
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PlayerPerformance(Base):
    """Punkte + Minuten pro Spieltag (Grundlage des Form-Signals)."""

    __tablename__ = "player_performance"
    __table_args__ = (
        UniqueConstraint("player_id", "season", "matchday", name="uq_perf_matchday"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    season: Mapped[str] = mapped_column(String(16), nullable=False)
    matchday: Mapped[int] = mapped_column(Integer, nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    match_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    points_observed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    minutes_observed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_finished: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CommunityTip(Base):
    """Recherche-/Insider-Tipp (KI-Recherche, YouTube, Reddit) — fließt als Boost
    in die Kauf-Scores der Engine (Nachfrage-Proxy: Hype treibt Kickbase-MW)."""

    __tablename__ = "community_tips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    # +1.0 = Kauf-Tipp … -1.0 = Verkaufs-/Meide-Tipp; Betrag = Stärke (max 1.0)
    weight: Mapped[float] = mapped_column(nullable=False, default=1.0)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="ki-recherche")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NewsItem(Base):
    __tablename__ = "news_items"
    __table_args__ = (UniqueConstraint("content_hash", name="uq_news_hash"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    relevance_score: Mapped[float] = mapped_column(default=0.0)


class Intent(Base):
    """Vorhaben der Engine (Steuerzentrale, Spec 2026-08-23-steuerzentrale.md):

    eine beschlossene Aktion, die zu `execute_at` autonom ausgeführt wird, wenn
    Emil bis dahin kein Veto einlegt. Höchstens EIN offenes (`planned`) Vorhaben
    je (kind, player_id). Kickbase-Writes laufen ausschließlich über den
    Intent-Executor → ActionExecutor (`executed_action_id` verknüpft das Audit).
    """

    __tablename__ = "intents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # bid | list | sell | lineup
    player_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    player_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    team_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # {"score", "elf_score", "li_status", "kb_prob", "tips": [...], "rule_id", ...}
    sources: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    listing_type: Mapped[str] = mapped_column(String(16), nullable=False, default="kickbase")
    # kickbase (Auto-Zuschlag beim Ablauf) | manager (manuelle Annahme, kein Ablauf)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execute_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="planned", index=True
    )
    # planned | executed | vetoed | expired | failed | superseded
    emil_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    adjusted_from: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    executed_action_id: Mapped[int | None] = mapped_column(
        ForeignKey("transfer_actions.id"), nullable=True
    )
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Discord-Karte (Spec 2026-08-31-discord-veto-buttons §4.3): Message-ID der
    # Veto-Button-Karte + der Stand (updated_at), den die Karte zuletzt gezeigt
    # hat. Bewusst eigene Spalten statt sources-JSON — der Planner überschreibt
    # sources bei Updates. Beide Felder bleiben API-intern (nie im JSON).
    discord_msg_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    discord_card_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Veto-Vormerkung (Review 31.08.): Emils Veto-Klick im `executing`-Fenster
    # lief bisher spurlos ins Leere (rowcount 0) — der pbaa-Retry belebte das
    # Vorhaben danach wieder. Jetzt wird der Wunsch hier vorgemerkt und beim
    # Scheitern des laufenden Versuchs bzw. vor jedem Retry als vetoed eingelöst.
    veto_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # pbaa-Retry-Zähler als eigene Spalte (Review 31.08.): in `sources` wurde er
    # vom stündlichen Planner-Update wholesale mit überschrieben — das 3er-Budget
    # verdoppelte sich faktisch (dasselbe Argument wie bei discord_msg_id).
    # Seit 02.10.2026 gemeinsamer Zähler aller vorübergehenden Verweigerungen
    # (pbaa-Lücke und Lesefehler im Preflight, worker/intents.preflight_read_error).
    pbaa_retries: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class JobRun(Base):
    """Heartbeat eines Scheduler-Jobs (Emils Regel 25.08.: „prüf, ob die Crons gelaufen sind").

    Jeder Lauf von `make_scheduled_job` schreibt hier Start und Ende — die EINZIGE
    Stelle, an der alle Jobs vorbeikommen. Der Wächter (worker/job_health.py) rechnet
    aus Trigger + letztem Lauf, ob ein Job überfällig ist; „läuft" (Container up) ist
    KEIN Beweis mehr, dass die Jobs auch feuern.
    """

    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # running | ok | failed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running", index=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Report(Base):
    """Gespeicherter Bericht des Agenten (Briefing, Recherche, Schatten-Scan, Planner, Retro …)
    — Spec 2026-08-23-steuerzentrale.md, Zone „Berichte": alles, was nach Discord geht,
    bleibt hier als Volltext nachlesbar (Emils Wunsch 23.08.)."""

    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # briefing | recherche | schatten | auto_trading | planner | executor | retro |
    # bid_watch | info | sonstiges
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # worker-Jobname | mcp:discord_post | cli:post-discord
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Atomarer At-most-once-Claim für die zwei täglichen Discord-Slots.
    delivery_slot: Mapped[str | None] = mapped_column(
        String(32), nullable=True, unique=True
    )


class LearningEvidence(Base):
    """Ein Beleg FÜR oder GEGEN eine Erkenntnis (Spec 2026-08-25-lern-bilanz-design.md).

    Die Behauptung selbst bleibt im Repo (`LEARNINGS.md`, Anker `[L-051]`) — hier
    wächst nur, was der Betrieb seither beobachtet hat. Deshalb ist `learning_id`
    bewusst KEIN Foreign Key: Die Quelle liegt außerhalb der Datenbank, und ein
    umformulierter oder verschobener Eintrag darf seine Geschichte nicht verlieren.

    `(learning_id, ref)` ist eindeutig: Jedes Ereignis zählt genau EINMAL, egal wie
    oft der Sammler über dasselbe Fenster läuft — sonst wüchse jede Bilanz mit der
    Zahl der Läufe statt mit der Zahl der Beobachtungen.

    `source` trennt gemessen (`auto`, aus einer Regel) von beurteilt (`agent`, von
    mir eingetragen). Diese Trennung ist der Kern von Emils Wunsch, im UI zu sehen,
    was das System weiß und was es nur meint — sie wird nie vom Aufrufer gesetzt.

    Belege werden NIE gelöscht: Eine widerlegte Annahme behält ihre Geschichte.
    """

    __tablename__ = "learning_evidence"
    __table_args__ = (
        UniqueConstraint("learning_id", "ref", name="uq_learning_evidence_ref"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    learning_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    #: Zeitpunkt des EREIGNISSES (nicht des Eintragens) — die Bilanz misst die Welt.
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    verdict: Mapped[str] = mapped_column(String(8), nullable=False)   # confirm | refute
    source: Mapped[str] = mapped_column(String(8), nullable=False)    # auto | agent
    #: Eindeutige Ereignis-Referenz: `action:39`, `feed:12364455874`, `job:Reconcile@…`
    ref: Mapped[str] = mapped_column(String(200), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: Welche Auto-Regel den Beleg erzeugt hat (leer bei `agent`).
    rule_key: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

class AgentFreeze(Base):
    """Selbst-Einfrieren (agent/freeze.py): der Agent sperrt seine eigenen Geld-Writes.

    Genau eine Zeile ohne `released_at` ist der aktive Zustand. Einfrieren darf der
    Agent selbst (Tagesverlust, unklare Writes, fehlgeschlagener Selbst-Deploy),
    entsperren ausschliesslich ein Mensch (`kba unfreeze`) — deshalb steht die
    Freigabe in eigenen Spalten und ueberschreibt den urspruenglichen Grund nie.
    """

    __tablename__ = "agent_freeze"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    readonly_since: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # verlust | fehl-writes | deploy | manuell
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="manuell")
    evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class JevJudgment(Base):
    """Eine gestellte Jev-Frage mit Antwort, Regel und Folge — oder eine gescheiterte
    Anfrage (Spec 2026-10-01-jev-dashboard-design.md, `intel/jev_journal.py`).

    Reines Protokoll für die Jev-Seite und die Treffer-Bilanz: Keine Stelle entscheidet aus
    dieser Tabelle; die Urteile wirken weiter nur über die Caches der Judges. Die Folge
    steht so da, wie die Regel sie beim Fragen ableitete (mit dem Regeltext dieses
    Zeitpunkts), damit eine spätere Schwellenänderung alte Zeilen nicht umdeutet.
    Aufbewahrung 90 Tage (Aufräumen 03:33).
    """

    __tablename__ = "jev_judgments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    # schwere | rolle | rueckkehr
    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # ausfall | kaufpruefung | kader | naechstes_spiel | kaufhinweis | handel
    purpose: Mapped[str] = mapped_column(String(24), nullable=False, default="")
    # ok | ungueltig | ausfall
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    #: Fragen derselben Anfrage teilen eine ID (die Bündelung beeinflusst Jev, L-114).
    batch_id: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    batch_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    player_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    player_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    club: Mapped[str | None] = mapped_column(String(100), nullable=True)
    #: Was Jev las: Titel des Anreißers bzw. der LigaInsider-Grund.
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    answer: Mapped[str | None] = mapped_column(String(32), nullable=True)
    probability: Mapped[float | None] = mapped_column(nullable=True)
    probabilities: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    #: Die typisierte Antwort so, wie Jev sie lieferte (je Frage-ID).
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    rule: Mapped[str] = mapped_column(Text, nullable=False, default="")
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    error: Mapped[str | None] = mapped_column(String(64), nullable=True)
