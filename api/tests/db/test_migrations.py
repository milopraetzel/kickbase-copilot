"""Die Migrations-Kette muss GENAU einen Kopf haben.

Warum ein eigener Test (26.08.): Beim Zusammenführen zweier Zweige bekamen zwei
Migrationen denselben ``down_revision`` — Git merkte nichts, die Suite blieb grün
(Tests bauen ihr Schema über ``Base.metadata.create_all``, nicht über Alembic),
und der Container ging auf der Box in eine Neustart-Schleife: „Multiple head
revisions are present". Genau diese Lücke schließt dieser Test: Er liest die
echten Versionsdateien, nicht eine Attrappe.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

API_ROOT = Path(__file__).resolve().parents[2]


def _script() -> ScriptDirectory:
    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "src/kickbase_agent/db/migrations"))
    return ScriptDirectory.from_config(cfg)


def test_migration_chain_has_exactly_one_head():
    heads = _script().get_heads()
    assert len(heads) == 1, (
        "Mehrere Alembic-Köpfe: "
        + ", ".join(sorted(heads))
        + " — beim Zusammenführen zweier Zweige muss die jüngere Migration auf die "
        "ältere zeigen (down_revision), sonst startet der Container nicht mehr."
    )


def test_every_revision_is_reachable_from_the_head():
    """Keine verwaiste Datei: Jede Revision liegt auf dem Weg zum Kopf."""
    script = _script()
    all_revs = {rev.revision for rev in script.walk_revisions()}
    reachable = {rev.revision for rev in script.iterate_revisions(script.get_heads()[0], "base")}
    assert all_revs == reachable, f"nicht erreichbar: {sorted(all_revs - reachable)}"
