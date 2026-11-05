# `docs/` — Dokumentations-Wegweiser

Willkommen in der Dokumentation des **Kickbase-Agenten**. Damit du sofort findest, was du suchst, ist die gesamte Dokumentation in vier klare Ebenen unterteilt:

---

## 1. Gesamtverständnis & Einstieg (Von Grund auf)

| Datei | Für wen | Inhalt |
|---|---|---|
| **[`SYSTEM-HANDBUCH.md`](SYSTEM-HANDBUCH.md)** | **Emil, Entwickler & KI (Startpunkt)** | **Das Programm von Grund auf erklärt:** Grundidee, Spielregeln (`11/11` Starter + `Konto >= 0 €` am Freitag), die 3-Phasen-Wochen-Trajektorie (Akkumulation, Konsolidierung/Heilung, Anpfiff-Kaskade) und alle 7 technischen Schichten. |
| **[`../README.md`](../README.md)** | Alle | Kurzüberblick auf Repo-Ebene, Systemdiagramm und lokale Schnellstart-Befehle. |
| **[`../CLAUDE.md`](../CLAUDE.md)** / **[`../AGENTS.md`](../AGENTS.md)** | KI-Agenten (Pflichtlektüre) | Verbindliche Arbeitsregeln, Tabus, Job-Tabelle und Bedienungsanleitung für Claude Code, Hermes, Antigravity und Codex. |
| **[`PROJEKT-ZIEL.md`](PROJEKT-ZIEL.md)** | Emil + KI-Agenten | Das definierte Projektende: vier Spieltage in Folge die 5 Kriterien `D1`–`D5` (Elf, Geld, Betrieb, Punkte, Ruhe) erfüllen. |

---

## 2. Technische Referenz (Architektur, API & Begriffe)

| Datei | Für wen | Inhalt |
|---|---|---|
| **[`ARCHITEKTUR.md`](ARCHITEKTUR.md)** | Entwickler & KI | Detaillierte Modul-Landkarte (`api/src/kickbase_agent/`), Entscheidungs-Pipeline, `ActionExecutor`-Schutzschichten, Datenbank-Schema (`kba.db`) und Scheduler-Jobs. |
| **[`API.md`](API.md)** | Bediener (Mensch & KI) | Vollständige Referenz der 23 MCP-Tools (`kickbase`), der FastAPI-Endpunkte (`/api/*`), der CLI (`uv run kba ...`) und der Cockpit-Seiten. |
| **[`GLOSSAR.md`](GLOSSAR.md)** | Alle | Nachschlagewerk aller Kickbase-API-Felder (`b`, `pbaa`, `tfhmvt`, `exs`, `lo`, `prob`, `st`) und internen Fachbegriffe (`Planungs-Dispo`, `Swap-Bridge`, `Handelsbuch`, `Gleitpfad`). |

---

## 3. Betrieb, Deploy & Erfahrungswissen (Produktion)

| Datei | Für wen | Inhalt |
|---|---|---|
| **[`BETRIEB.md`](BETRIEB.md)** | Betreiber & DevOps-KI | Produktions-Runbook für die Box (`root@203.0.113.10`): die 3 Docker-Container, der exakte `rsync`-Deploy-Einzeiler mit `--env-file api/.env`, Deploy-Sperrfenster (`21:25–22:25`), Diagnose-Befehle und Notfall-Checklisten. |
| **[`../.claude/skills/kickbase/LEARNINGS.md`](../.claude/skills/kickbase/LEARNINGS.md)** | KI-Agenten & Emil | Chronologisches Archiv aller realen Betriebslektionen (`L-001` bis `L-125+`) mit Ursachen und Code-Belegen. |
| **[`../api/src/kickbase_agent/knowledge/parameter_history.json`](../api/src/kickbase_agent/knowledge/parameter_history.json)** | Engine & Audit | Strukturiertes Decision-Log jeder Parameter- und Regeländerung (`reason`, `expect`, `review`, `commit`). |

---

## 4. Strategie, Studien & Design-Spezifikationen

| Datei | Für wen | Inhalt |
|---|---|---|
| **[`STRATEGIE-SAISON-26-27.md`](STRATEGIE-SAISON-26-27.md)** | Emil + Orchestrator | Strategischer Masterplan für die Saison 2026/27, KPIs und Kapitalallokation. |
| **[`PROZESS.md`](PROZESS.md)** | Emil | Anschauliche Erklärung des Entscheidungswegs ohne Code-Jargon anhand durchgerechneter Beispiele. |
| **[`RECHERCHE-META-STRATEGIEN.md`](RECHERCHE-META-STRATEGIEN.md)** | Orchestrator | Externe Faktenbasis zu Kickbase-Punkten, Prämien, Marktwert-Mechanik und Gebotsverhalten. |
| **[`STUDIE-TURNAROUND-2026-09-19.md`](STUDIE-TURNAROUND-2026-09-19.md)** | Architektur-Historie | Analyse und Entwurf des MILP-Mannschaftsoptimierers und der versiegelten Kaufbelege (`purchase_review`). |
| **[`ANALYSE-GEBOTSPREIS-2026-09-25.md`](ANALYSE-GEBOTSPREIS-2026-09-25.md)** | Architektur-Historie | Empirische Auswertung der Gebotsaufschläge und Auktions-Zuschlagswahrscheinlichkeiten. |
| **`superpowers/specs/`** | Entwickler | Einzel-Spezifikationen der Subsysteme (`2026-10-01-handels-radar.md`, `2026-10-01-handels-autopilot.md`, `2026-08-23-steuerzentrale.md` u. a.). |
| **`archive/`** | Historie | Abgelöste historische Dokumente und frühere Übergabe-Notizen. |

Stand: 28.10.2026 · Branch: `main` · 3.374 Tests grün.
