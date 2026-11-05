"""Deterministische Entscheidungs-Engine: aus Signalen werden geprüfte Vorschläge.

Reine, testbare Funktionen. Kein API-Zugriff, keine Seiteneffekte. Jeder Vorschlag
trägt eine rule_id (Regelbasis) — ohne die kann er den ActionExecutor nicht erreichen.
Claude begründet Vorschläge nur, löst aber nie selbst eine Transaktion aus.
"""
