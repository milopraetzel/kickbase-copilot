"""Commits-Loader (knowledge/commits.py): Pfadkette, Reihenfolge, System-Einträge."""

from __future__ import annotations

import json
from datetime import datetime

from kickbase_agent.knowledge import commits

ENTRY_OLD = {
    "sha": "a" * 40,
    "date": "2026-08-21T10:00:00+02:00",
    "subject": "fix(review): Transportfehler -> unknown",
    "body": "Warum: Timeout darf nie als executed gelten.",
}
ENTRY_NEW = {
    "sha": "b" * 40,
    "date": "2026-08-23T09:00:00+02:00",
    "subject": "feat(zentrale): Protokoll v2",
    "body": "",
}


def _write(tmp_path, entries) -> str:
    file = tmp_path / "commits.json"
    file.write_text(json.dumps(entries), encoding="utf-8")
    return str(file)


# ------------------------------------------------------------------ load_commits


def test_load_commits_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.delenv(commits.COMMITS_PATH_ENV, raising=False)
    assert commits.load_commits(tmp_path / "gibts-nicht.json") == []


def test_load_commits_no_path_and_no_defaults_returns_empty(tmp_path, monkeypatch):
    monkeypatch.delenv(commits.COMMITS_PATH_ENV, raising=False)
    monkeypatch.setattr(
        commits, "DEFAULT_CANDIDATES", (tmp_path / "a.json", tmp_path / "b.json")
    )
    assert commits.load_commits() == []


def test_load_commits_sorts_newest_first_even_if_file_unordered(tmp_path):
    path = _write(tmp_path, [ENTRY_OLD, ENTRY_NEW])  # aeltester zuerst in der Datei
    out = commits.load_commits(path)
    assert [c["sha"][0] for c in out] == ["b", "a"]
    assert out[0]["subject"] == ENTRY_NEW["subject"]
    assert out[1]["body"] == ENTRY_OLD["body"]


def test_load_commits_env_path_wins_over_defaults(tmp_path, monkeypatch):
    env_file = _write(tmp_path, [ENTRY_OLD])
    monkeypatch.setenv(commits.COMMITS_PATH_ENV, env_file)
    out = commits.load_commits()
    assert len(out) == 1
    assert out[0]["sha"] == ENTRY_OLD["sha"]


def test_load_commits_default_candidate_used_when_present(tmp_path, monkeypatch):
    monkeypatch.delenv(commits.COMMITS_PATH_ENV, raising=False)
    candidate = tmp_path / "volume" / "commits.json"
    candidate.parent.mkdir()
    candidate.write_text(json.dumps([ENTRY_NEW]), encoding="utf-8")
    monkeypatch.setattr(
        commits, "DEFAULT_CANDIDATES", (tmp_path / "fehlt.json", candidate)
    )
    assert [c["sha"][0] for c in commits.load_commits()] == ["b"]


def test_load_commits_skips_invalid_entries_and_survives_bad_json(tmp_path):
    path = _write(
        tmp_path,
        [
            {"sha": "x" * 40},  # ohne date/subject
            "kein-dict",
            {"sha": "", "date": "2026-08-22T10:00:00+02:00", "subject": "leer"},
            ENTRY_OLD,
        ],
    )
    out = commits.load_commits(path)
    assert len(out) == 1
    assert out[0]["sha"] == ENTRY_OLD["sha"]

    (tmp_path / "commits.json").write_text("{kaputt", encoding="utf-8")
    assert commits.load_commits(path) == []

    (tmp_path / "commits.json").write_text(json.dumps({"nicht": "liste"}), encoding="utf-8")
    assert commits.load_commits(path) == []


# ------------------------------------------------------------------ system_entries


def test_system_entries_protocol_format(tmp_path, monkeypatch):
    long_body = "x" * 500
    monkeypatch.setenv(
        commits.COMMITS_PATH_ENV, _write(tmp_path, [{**ENTRY_OLD, "body": long_body}])
    )
    [entry] = commits.system_entries()
    assert entry["kind"] == "system"
    assert entry["title"] == f"System: {ENTRY_OLD['subject']}"
    assert entry["sources"] == {"commit": "a" * 7}
    assert entry["status"] == "deployt"
    assert entry["emil"] == ""
    assert len(entry["why"]) == commits.WHY_MAX_CHARS  # 399 Zeichen + Ellipse
    assert entry["why"].endswith("…")
    parsed = datetime.fromisoformat(entry["ts"])  # muss fuer den Protokoll-Mischer parsen
    assert parsed.tzinfo is not None


def test_system_entries_limit_and_bad_dates_skipped(tmp_path, monkeypatch):
    entries = [
        {**ENTRY_NEW, "sha": "c" * 40, "date": "kaputt"},  # unparsebar -> raus
        {**ENTRY_NEW, "sha": "d" * 40, "date": "2026-08-23T10:00:00"},  # naiv -> raus
        ENTRY_NEW,
        ENTRY_OLD,
    ]
    monkeypatch.setenv(commits.COMMITS_PATH_ENV, _write(tmp_path, entries))
    assert len(commits.system_entries()) == 2
    assert len(commits.system_entries(limit=3)) == 1  # 2 kaputte + 1 gueltiger im Fenster
    assert commits.system_entries(limit=0) == []


def test_system_entries_without_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv(commits.COMMITS_PATH_ENV, str(tmp_path / "fehlt.json"))
    assert commits.system_entries() == []


def test_system_entries_strip_git_trailers_from_why(tmp_path, monkeypatch):
    body = (
        "Warum: Feed-Cache statt Live-Call.\n"
        "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>\n"
        "Signed-off-by: Emil"
    )
    monkeypatch.setenv(
        commits.COMMITS_PATH_ENV, _write(tmp_path, [{**ENTRY_NEW, "body": body}])
    )
    [entry] = commits.system_entries()
    assert entry["why"] == "Warum: Feed-Cache statt Live-Call."
