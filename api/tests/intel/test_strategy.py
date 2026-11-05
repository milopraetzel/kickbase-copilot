"""Echte Export-Teilmenge vom 11.09.2026, lokal und ohne API reproduzierbar.

Quellen: strategy-2026-09-11/data/{live,audit,history-backfill}.json.
Live SHA256: 0e127132b9deed4b7648c7ce500df2a017bea0b8e93600cb96415d91495560fc.
API-Schlüssel wurden nur ausgewählt; Zahlen/Datum sind unveränderte Beobachtungen.
"""

import json
from copy import deepcopy

import pytest

from kickbase_agent.intel.strategy import build_strategy_insights

REAL_EXPORT = json.loads(r"""
{
  "feed_events": [
    {
      "i": "12676937639",
      "t": 15,
      "dt": "2026-09-11T18:15:10Z",
      "data": {
        "slr": "emil",
        "pi": "3187",
        "pn": "Schäfer",
        "trp": 3898704
      }
    },
    {
      "i": "12676937258",
      "t": 15,
      "dt": "2026-09-11T18:15:07Z",
      "data": {
        "slr": "emil",
        "pi": "89",
        "pn": "Günter",
        "trp": 3646808
      }
    },
    {
      "i": "12676936631",
      "t": 15,
      "dt": "2026-09-11T18:15:04Z",
      "data": {
        "slr": "emil",
        "pi": "15181",
        "pn": "Leszczynski",
        "trp": 3973085
      }
    },
    {
      "i": "12602259239",
      "t": 15,
      "dt": "2026-09-07T07:50:25Z",
      "data": {
        "slr": "emil",
        "pi": "8351",
        "pn": "Højlund",
        "trp": 4639845
      }
    },
    {
      "i": "12602259134",
      "t": 15,
      "dt": "2026-09-07T07:50:22Z",
      "data": {
        "slr": "emil",
        "pi": "14890",
        "pn": "Vogt",
        "trp": 1625348
      }
    },
    {
      "i": "12602258984",
      "t": 15,
      "dt": "2026-09-07T07:50:19Z",
      "data": {
        "slr": "emil",
        "pi": "15472",
        "pn": "Daka",
        "trp": 9787642
      }
    },
    {
      "i": "12592262819",
      "t": 15,
      "dt": "2026-09-06T15:55:56Z",
      "data": {
        "slr": "Osei",
        "byr": "emil",
        "pi": "4406",
        "pn": "Dina Ebimbe",
        "trp": 5548095
      }
    },
    {
      "i": "12588175136",
      "t": 15,
      "dt": "2026-09-06T09:04:28Z",
      "data": {
        "slr": "Osei",
        "byr": "emil",
        "pi": "3081",
        "pn": "Vidović",
        "trp": 8554338
      }
    },
    {
      "i": "12572972096",
      "t": 15,
      "dt": "2026-09-05T07:55:24Z",
      "data": {
        "slr": "Osei",
        "byr": "emil",
        "pi": "2855",
        "pn": "Ache",
        "trp": 15915792
      }
    },
    {
      "i": "12547951547",
      "t": 15,
      "dt": "2026-09-03T18:14:54Z",
      "data": {
        "byr": "emil",
        "pi": "7320",
        "pn": "Scheller",
        "trp": 7976663
      }
    },
    {
      "i": "12544110575",
      "t": 15,
      "dt": "2026-09-03T11:40:02Z",
      "data": {
        "byr": "emil",
        "pi": "15472",
        "pn": "Daka",
        "trp": 12886667
      }
    },
    {
      "i": "12533472821",
      "t": 15,
      "dt": "2026-09-02T18:30:02Z",
      "data": {
        "slr": "emil",
        "pi": "51",
        "pn": "Khedira",
        "trp": 8233478
      }
    },
    {
      "i": "12533434535",
      "t": 15,
      "dt": "2026-09-02T18:26:21Z",
      "data": {
        "byr": "emil",
        "pi": "89",
        "pn": "Günter",
        "trp": 5064699
      }
    },
    {
      "i": "12526562078",
      "t": 15,
      "dt": "2026-09-02T06:35:04Z",
      "data": {
        "slr": "emil",
        "pi": "10134",
        "pn": "Schmetgens",
        "trp": 7422546
      }
    },
    {
      "i": "12526561928",
      "t": 15,
      "dt": "2026-09-02T06:35:01Z",
      "data": {
        "slr": "emil",
        "pi": "7282",
        "pn": "Deman",
        "trp": 9588216
      }
    },
    {
      "i": "12437217264",
      "t": 15,
      "dt": "2026-08-28T17:29:34Z",
      "data": {
        "slr": "Felix K. ",
        "byr": "emil",
        "pi": "4333",
        "pn": "Ullrich",
        "trp": 9400000
      }
    },
    {
      "i": "12437177157",
      "t": 15,
      "dt": "2026-08-28T17:26:02Z",
      "data": {
        "slr": "emil",
        "pi": "13472",
        "pn": "Reggiani",
        "trp": 3396495
      }
    },
    {
      "i": "12417580528",
      "t": 15,
      "dt": "2026-08-27T10:05:01Z",
      "data": {
        "slr": "emil",
        "pi": "3492",
        "pn": "Hoffmeier",
        "trp": 6526611
      }
    },
    {
      "i": "12409031335",
      "t": 15,
      "dt": "2026-08-26T18:29:27Z",
      "data": {
        "slr": "emil",
        "pi": "1954",
        "pn": "Bernardo",
        "trp": 7077515
      }
    },
    {
      "i": "12408944938",
      "t": 15,
      "dt": "2026-08-26T18:18:43Z",
      "data": {
        "byr": "emil",
        "pi": "7282",
        "pn": "Deman",
        "trp": 10642821
      }
    },
    {
      "i": "12408846793",
      "t": 15,
      "dt": "2026-08-26T18:06:59Z",
      "data": {
        "byr": "emil",
        "pi": "10134",
        "pn": "Schmetgens",
        "trp": 9578351
      }
    },
    {
      "i": "12395421805",
      "t": 15,
      "dt": "2026-08-25T16:19:49Z",
      "data": {
        "slr": "emil",
        "pi": "2941",
        "pn": "Scally",
        "trp": 4151452
      }
    },
    {
      "i": "12395414926",
      "t": 15,
      "dt": "2026-08-25T16:19:45Z",
      "data": {
        "slr": "emil",
        "pi": "2722",
        "pn": "Osterhage",
        "trp": 4012852
      }
    },
    {
      "i": "12395414863",
      "t": 15,
      "dt": "2026-08-25T16:19:41Z",
      "data": {
        "slr": "emil",
        "pi": "4576",
        "pn": "Hanche-Olsen",
        "trp": 3051141
      }
    },
    {
      "i": "12395414422",
      "t": 15,
      "dt": "2026-08-25T16:19:37Z",
      "data": {
        "slr": "emil",
        "pi": "12314",
        "pn": "Ndiaye",
        "trp": 2337169
      }
    },
    {
      "i": "12380283781",
      "t": 15,
      "dt": "2026-08-24T12:19:59Z",
      "data": {
        "slr": "emil",
        "pi": "11955",
        "pn": "El Khannouss",
        "trp": 22172376
      }
    },
    {
      "i": "12371920348",
      "t": 15,
      "dt": "2026-08-23T20:24:26Z",
      "data": {
        "byr": "emil",
        "pi": "8329",
        "pn": "Olise",
        "trp": 68017197
      }
    },
    {
      "i": "12348183850",
      "t": 15,
      "dt": "2026-08-21T21:43:27Z",
      "data": {
        "slr": "Felix K. ",
        "byr": "emil",
        "pi": "3492",
        "pn": "Hoffmeier",
        "trp": 8700000
      }
    },
    {
      "i": "12329644480",
      "t": 15,
      "dt": "2026-08-20T07:50:23Z",
      "data": {
        "byr": "emil",
        "pi": "8351",
        "pn": "Højlund",
        "trp": 3620000
      }
    },
    {
      "i": "12321188968",
      "t": 15,
      "dt": "2026-08-19T14:28:20Z",
      "data": {
        "slr": "emil",
        "pi": "12293",
        "pn": "Arévalo",
        "trp": 3905842
      }
    },
    {
      "i": "12321188923",
      "t": 15,
      "dt": "2026-08-19T14:28:18Z",
      "data": {
        "slr": "emil",
        "pi": "2261",
        "pn": "Neuhaus",
        "trp": 1961947
      }
    }
  ],
  "history_rows": [
    {
      "id": 1359,
      "player_id": "8351",
      "recorded_at": "2026-08-19 00:00:00.000000",
      "market_value": 3415594
    },
    {
      "id": 7195,
      "player_id": "3492",
      "recorded_at": "2026-08-21 00:00:00.000000",
      "market_value": 8459326
    },
    {
      "id": 10494,
      "player_id": "11955",
      "recorded_at": "2026-08-23 00:00:00.000000",
      "market_value": 22172376
    },
    {
      "id": 10532,
      "player_id": "8329",
      "recorded_at": "2026-08-23 00:00:00.000000",
      "market_value": 64791589
    },
    {
      "id": 12101,
      "player_id": "12314",
      "recorded_at": "2026-08-24 00:00:00.000000",
      "market_value": 2337169
    },
    {
      "id": 12102,
      "player_id": "2941",
      "recorded_at": "2026-08-24 00:00:00.000000",
      "market_value": 4151452
    },
    {
      "id": 12103,
      "player_id": "2722",
      "recorded_at": "2026-08-24 00:00:00.000000",
      "market_value": 4012852
    },
    {
      "id": 12110,
      "player_id": "4576",
      "recorded_at": "2026-08-24 00:00:00.000000",
      "market_value": 3051141
    },
    {
      "id": 13888,
      "player_id": "1954",
      "recorded_at": "2026-08-25 00:00:00.000000",
      "market_value": 7077515
    },
    {
      "id": 13918,
      "player_id": "7282",
      "recorded_at": "2026-08-25 00:00:00.000000",
      "market_value": 9946575
    },
    {
      "id": 13923,
      "player_id": "10134",
      "recorded_at": "2026-08-25 00:00:00.000000",
      "market_value": 7970845
    },
    {
      "id": 15487,
      "player_id": "3492",
      "recorded_at": "2026-08-26 00:00:00.000000",
      "market_value": 6526611
    },
    {
      "id": 17368,
      "player_id": "13472",
      "recorded_at": "2026-08-27 00:00:00.000000",
      "market_value": 3396495
    },
    {
      "id": 17404,
      "player_id": "4333",
      "recorded_at": "2026-08-27 00:00:00.000000",
      "market_value": 9145782
    },
    {
      "id": 25703,
      "player_id": "51",
      "recorded_at": "2026-09-01 00:00:00.000000",
      "market_value": 8233478
    },
    {
      "id": 25707,
      "player_id": "10134",
      "recorded_at": "2026-09-01 00:00:00.000000",
      "market_value": 7422546
    },
    {
      "id": 25708,
      "player_id": "7282",
      "recorded_at": "2026-09-01 00:00:00.000000",
      "market_value": 9588216
    },
    {
      "id": 27421,
      "player_id": "7320",
      "recorded_at": "2026-09-02 00:00:00.000000",
      "market_value": 7186183
    },
    {
      "id": 27434,
      "player_id": "15472",
      "recorded_at": "2026-09-02 00:00:00.000000",
      "market_value": 11612576
    },
    {
      "id": 27527,
      "player_id": "89",
      "recorded_at": "2026-09-01 00:00:00.000000",
      "market_value": 4677187
    },
    {
      "id": 29082,
      "player_id": "8329",
      "recorded_at": "2026-09-03 00:00:00.000000",
      "market_value": 65005159
    },
    {
      "id": 30589,
      "player_id": "8329",
      "recorded_at": "2026-09-04 00:00:00.000000",
      "market_value": 65013611
    },
    {
      "id": 30602,
      "player_id": "2855",
      "recorded_at": "2026-09-04 00:00:00.000000",
      "market_value": 15590643
    },
    {
      "id": 32084,
      "player_id": "8329",
      "recorded_at": "2026-09-05 00:00:00.000000",
      "market_value": 65026334
    },
    {
      "id": 33393,
      "player_id": "14890",
      "recorded_at": "2026-09-06 00:00:00.000000",
      "market_value": 1625348
    },
    {
      "id": 33398,
      "player_id": "8351",
      "recorded_at": "2026-09-06 00:00:00.000000",
      "market_value": 4639845
    },
    {
      "id": 33399,
      "player_id": "8329",
      "recorded_at": "2026-09-06 00:00:00.000000",
      "market_value": 65037725
    },
    {
      "id": 33401,
      "player_id": "15472",
      "recorded_at": "2026-09-06 00:00:00.000000",
      "market_value": 9787642
    },
    {
      "id": 33526,
      "player_id": "4406",
      "recorded_at": "2026-09-05 00:00:00.000000",
      "market_value": 5426234
    },
    {
      "id": 34900,
      "player_id": "8329",
      "recorded_at": "2026-09-07 00:00:00.000000",
      "market_value": 65047096
    },
    {
      "id": 35193,
      "player_id": "2261",
      "recorded_at": "2026-08-18 00:00:00.000000",
      "market_value": 1961947
    },
    {
      "id": 36222,
      "player_id": "8329",
      "recorded_at": "2026-09-08 00:00:00.000000",
      "market_value": 65057803
    },
    {
      "id": 36909,
      "player_id": "8329",
      "recorded_at": "2026-09-09 00:00:00.000000",
      "market_value": 65064350
    },
    {
      "id": 37410,
      "player_id": "15181",
      "recorded_at": "2026-09-10 00:00:00.000000",
      "market_value": 3973085
    },
    {
      "id": 37413,
      "player_id": "3187",
      "recorded_at": "2026-09-10 00:00:00.000000",
      "market_value": 3898704
    },
    {
      "id": 37415,
      "player_id": "8329",
      "recorded_at": "2026-09-10 00:00:00.000000",
      "market_value": 65065938
    },
    {
      "id": 37416,
      "player_id": "89",
      "recorded_at": "2026-09-10 00:00:00.000000",
      "market_value": 3646808
    },
    {
      "player_id": "2261",
      "recorded_at": "2026-08-18T00:00:00+00:00",
      "market_value": 1961947,
      "source": "api_history",
      "captured_at": "2026-09-11T20:01:30.469896+00:00"
    },
    {
      "player_id": "12293",
      "recorded_at": "2026-08-18T00:00:00+00:00",
      "market_value": 3905842,
      "source": "api_history",
      "captured_at": "2026-09-11T20:01:31.478748+00:00"
    },
    {
      "player_id": "11955",
      "recorded_at": "2026-08-23T00:00:00+00:00",
      "market_value": 22172376,
      "source": "api_history",
      "captured_at": "2026-09-11T20:01:40.482467+00:00"
    },
    {
      "player_id": "12314",
      "recorded_at": "2026-08-24T00:00:00+00:00",
      "market_value": 2337169,
      "source": "api_history",
      "captured_at": "2026-09-11T20:01:41.492796+00:00"
    },
    {
      "player_id": "4576",
      "recorded_at": "2026-08-24T00:00:00+00:00",
      "market_value": 3051141,
      "source": "api_history",
      "captured_at": "2026-09-11T20:01:42.494110+00:00"
    },
    {
      "player_id": "2722",
      "recorded_at": "2026-08-24T00:00:00+00:00",
      "market_value": 4012852,
      "source": "api_history",
      "captured_at": "2026-09-11T20:01:43.496841+00:00"
    },
    {
      "player_id": "2941",
      "recorded_at": "2026-08-24T00:00:00+00:00",
      "market_value": 4151452,
      "source": "api_history",
      "captured_at": "2026-09-11T20:01:44.517459+00:00"
    },
    {
      "player_id": "1954",
      "recorded_at": "2026-08-25T00:00:00+00:00",
      "market_value": 7077515,
      "source": "api_history",
      "captured_at": "2026-09-11T20:01:45.499722+00:00"
    },
    {
      "player_id": "3492",
      "recorded_at": "2026-08-21T00:00:00+00:00",
      "market_value": 8459326,
      "source": "api_history",
      "captured_at": "2026-09-11T20:01:46.506820+00:00"
    },
    {
      "player_id": "3492",
      "recorded_at": "2026-08-26T00:00:00+00:00",
      "market_value": 6526611,
      "source": "api_history",
      "captured_at": "2026-09-11T20:01:46.506820+00:00"
    },
    {
      "player_id": "13472",
      "recorded_at": "2026-08-27T00:00:00+00:00",
      "market_value": 3396495,
      "source": "api_history",
      "captured_at": "2026-09-11T20:01:49.527485+00:00"
    },
    {
      "player_id": "7282",
      "recorded_at": "2026-08-25T00:00:00+00:00",
      "market_value": 9946575,
      "source": "api_history",
      "captured_at": "2026-09-11T20:01:54.528472+00:00"
    },
    {
      "player_id": "7282",
      "recorded_at": "2026-09-01T00:00:00+00:00",
      "market_value": 9588216,
      "source": "api_history",
      "captured_at": "2026-09-11T20:01:54.528472+00:00"
    },
    {
      "player_id": "10134",
      "recorded_at": "2026-08-25T00:00:00+00:00",
      "market_value": 7970845,
      "source": "api_history",
      "captured_at": "2026-09-11T20:01:55.532906+00:00"
    },
    {
      "player_id": "10134",
      "recorded_at": "2026-09-01T00:00:00+00:00",
      "market_value": 7422546,
      "source": "api_history",
      "captured_at": "2026-09-11T20:01:55.532906+00:00"
    },
    {
      "player_id": "51",
      "recorded_at": "2026-09-01T00:00:00+00:00",
      "market_value": 8233478,
      "source": "api_history",
      "captured_at": "2026-09-11T20:01:57.528912+00:00"
    },
    {
      "player_id": "3081",
      "recorded_at": "2026-09-05T00:00:00+00:00",
      "market_value": 8373531,
      "source": "api_history",
      "captured_at": "2026-09-11T20:02:00.558175+00:00"
    },
    {
      "player_id": "15472",
      "recorded_at": "2026-09-02T00:00:00+00:00",
      "market_value": 11612576,
      "source": "api_history",
      "captured_at": "2026-09-11T20:02:02.613809+00:00"
    },
    {
      "player_id": "15472",
      "recorded_at": "2026-09-06T00:00:00+00:00",
      "market_value": 9787642,
      "source": "api_history",
      "captured_at": "2026-09-11T20:02:02.613809+00:00"
    },
    {
      "player_id": "14890",
      "recorded_at": "2026-09-06T00:00:00+00:00",
      "market_value": 1625348,
      "source": "api_history",
      "captured_at": "2026-09-11T20:02:03.550974+00:00"
    },
    {
      "player_id": "8351",
      "recorded_at": "2026-08-19T00:00:00+00:00",
      "market_value": 3415594,
      "source": "api_history",
      "captured_at": "2026-09-11T20:02:04.566694+00:00"
    },
    {
      "player_id": "8351",
      "recorded_at": "2026-09-06T00:00:00+00:00",
      "market_value": 4639845,
      "source": "api_history",
      "captured_at": "2026-09-11T20:02:04.566694+00:00"
    },
    {
      "player_id": "15181",
      "recorded_at": "2026-09-10T00:00:00+00:00",
      "market_value": 3973085,
      "source": "api_history",
      "captured_at": "2026-09-11T20:02:09.564003+00:00"
    },
    {
      "player_id": "89",
      "recorded_at": "2026-09-01T00:00:00+00:00",
      "market_value": 4677187,
      "source": "api_history",
      "captured_at": "2026-09-11T20:02:10.564719+00:00"
    },
    {
      "player_id": "89",
      "recorded_at": "2026-09-10T00:00:00+00:00",
      "market_value": 3646808,
      "source": "api_history",
      "captured_at": "2026-09-11T20:02:10.564719+00:00"
    },
    {
      "player_id": "3187",
      "recorded_at": "2026-09-10T00:00:00+00:00",
      "market_value": 3898704,
      "source": "api_history",
      "captured_at": "2026-09-11T20:02:11.564386+00:00"
    }
  ],
  "performance_rows": [
    {
      "id": 719,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 1,
      "points": 119,
      "minutes": 28,
      "match_date": "2025-08-22 18:30:00.000000"
    },
    {
      "id": 720,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 2,
      "points": 0,
      "minutes": 0,
      "match_date": "2025-08-30 16:30:00.000000"
    },
    {
      "id": 721,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 3,
      "points": 0,
      "minutes": 0,
      "match_date": "2025-09-13 16:30:00.000000"
    },
    {
      "id": 722,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 4,
      "points": 136,
      "minutes": 74,
      "match_date": "2025-09-20 13:30:00.000000"
    },
    {
      "id": 723,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 5,
      "points": 0,
      "minutes": 0,
      "match_date": "2025-09-26 18:30:00.000000"
    },
    {
      "id": 724,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 6,
      "points": 0,
      "minutes": 0,
      "match_date": "2025-10-04 16:30:00.000000"
    },
    {
      "id": 725,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 7,
      "points": 26,
      "minutes": 4,
      "match_date": "2025-10-18 16:30:00.000000"
    },
    {
      "id": 726,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 8,
      "points": 194,
      "minutes": 102,
      "match_date": "2025-10-25 13:45:00.000000"
    },
    {
      "id": 727,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 9,
      "points": 154,
      "minutes": 95,
      "match_date": "2025-11-01 17:30:00.000000"
    },
    {
      "id": 728,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 10,
      "points": 0,
      "minutes": 0,
      "match_date": "2025-11-08 14:30:00.000000"
    },
    {
      "id": 729,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 11,
      "points": 59,
      "minutes": 20,
      "match_date": "2025-11-22 14:30:00.000000"
    },
    {
      "id": 730,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 12,
      "points": 237,
      "minutes": 80,
      "match_date": "2025-11-29 14:30:00.000000"
    },
    {
      "id": 731,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 13,
      "points": 160,
      "minutes": 103,
      "match_date": "2025-12-06 14:30:00.000000"
    },
    {
      "id": 732,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 14,
      "points": 109,
      "minutes": 77,
      "match_date": "2025-12-14 16:30:00.000000"
    },
    {
      "id": 733,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 15,
      "points": 0,
      "minutes": 0,
      "match_date": "2025-12-21 16:30:00.000000"
    },
    {
      "id": 734,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 16,
      "points": 0,
      "minutes": 0,
      "match_date": "2026-01-11 16:30:00.000000"
    },
    {
      "id": 735,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 17,
      "points": 229,
      "minutes": 108,
      "match_date": "2026-01-14 19:30:00.000000"
    },
    {
      "id": 736,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 18,
      "points": 32,
      "minutes": 9,
      "match_date": "2026-01-17 17:30:00.000000"
    },
    {
      "id": 737,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 19,
      "points": 62,
      "minutes": 88,
      "match_date": "2026-01-24 14:30:00.000000"
    },
    {
      "id": 738,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 20,
      "points": 63,
      "minutes": 70,
      "match_date": "2026-01-31 17:30:00.000000"
    },
    {
      "id": 739,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 21,
      "points": 0,
      "minutes": 0,
      "match_date": "2026-02-08 16:30:00.000000"
    },
    {
      "id": 740,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 22,
      "points": 188,
      "minutes": 99,
      "match_date": "2026-02-14 14:30:00.000000"
    },
    {
      "id": 741,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 23,
      "points": 85,
      "minutes": 102,
      "match_date": "2026-02-21 14:30:00.000000"
    },
    {
      "id": 742,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 24,
      "points": 16,
      "minutes": 4,
      "match_date": "2026-02-28 17:30:00.000000"
    },
    {
      "id": 743,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 25,
      "points": 177,
      "minutes": 96,
      "match_date": "2026-03-06 19:30:00.000000"
    },
    {
      "id": 744,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 26,
      "points": 12,
      "minutes": 12,
      "match_date": "2026-03-14 14:30:00.000000"
    },
    {
      "id": 745,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 27,
      "points": 131,
      "minutes": 72,
      "match_date": "2026-03-21 14:30:00.000000"
    },
    {
      "id": 746,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 28,
      "points": 97,
      "minutes": 90,
      "match_date": "2026-04-04 13:30:00.000000"
    },
    {
      "id": 747,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 29,
      "points": 214,
      "minutes": 97,
      "match_date": "2026-04-11 16:30:00.000000"
    },
    {
      "id": 748,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 30,
      "points": 125,
      "minutes": 96,
      "match_date": "2026-04-19 15:30:00.000000"
    },
    {
      "id": 749,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 31,
      "points": 148,
      "minutes": 102,
      "match_date": "2026-04-25 13:30:00.000000"
    },
    {
      "id": 750,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 32,
      "points": 166,
      "minutes": 104,
      "match_date": "2026-05-02 13:30:00.000000"
    },
    {
      "id": 751,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 33,
      "points": 173,
      "minutes": 48,
      "match_date": "2026-05-09 16:30:00.000000"
    },
    {
      "id": 752,
      "player_id": "6668",
      "season": "2025/2026",
      "matchday": 34,
      "points": 0,
      "minutes": 0,
      "match_date": "2026-05-16 13:30:00.000000"
    },
    {
      "id": 10460,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 1,
      "points": 356,
      "minutes": 102,
      "match_date": "2025-08-22 18:30:00.000000"
    },
    {
      "id": 10461,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 2,
      "points": 290,
      "minutes": 107,
      "match_date": "2025-08-30 16:30:00.000000"
    },
    {
      "id": 10462,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 3,
      "points": 264,
      "minutes": 98,
      "match_date": "2025-09-13 16:30:00.000000"
    },
    {
      "id": 10463,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 4,
      "points": 84,
      "minutes": 38,
      "match_date": "2025-09-20 13:30:00.000000"
    },
    {
      "id": 10464,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 5,
      "points": 223,
      "minutes": 80,
      "match_date": "2025-09-26 18:30:00.000000"
    },
    {
      "id": 10465,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 6,
      "points": 118,
      "minutes": 97,
      "match_date": "2025-10-04 16:30:00.000000"
    },
    {
      "id": 10466,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 7,
      "points": 264,
      "minutes": 96,
      "match_date": "2025-10-18 16:30:00.000000"
    },
    {
      "id": 10467,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 8,
      "points": 269,
      "minutes": 102,
      "match_date": "2025-10-25 13:45:00.000000"
    },
    {
      "id": 10468,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 9,
      "points": 86,
      "minutes": 34,
      "match_date": "2025-11-01 17:30:00.000000"
    },
    {
      "id": 10469,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 10,
      "points": 57,
      "minutes": 101,
      "match_date": "2025-11-08 14:30:00.000000"
    },
    {
      "id": 10470,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 11,
      "points": 686,
      "minutes": 99,
      "match_date": "2025-11-22 14:30:00.000000"
    },
    {
      "id": 10471,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 12,
      "points": 120,
      "minutes": 54,
      "match_date": "2025-11-29 14:30:00.000000"
    },
    {
      "id": 10472,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 13,
      "points": 287,
      "minutes": 103,
      "match_date": "2025-12-06 14:30:00.000000"
    },
    {
      "id": 10473,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 14,
      "points": 193,
      "minutes": 102,
      "match_date": "2025-12-14 16:30:00.000000"
    },
    {
      "id": 10474,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 15,
      "points": 284,
      "minutes": 92,
      "match_date": "2025-12-21 16:30:00.000000"
    },
    {
      "id": 10475,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 16,
      "points": 530,
      "minutes": 86,
      "match_date": "2026-01-11 16:30:00.000000"
    },
    {
      "id": 10476,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 17,
      "points": 142,
      "minutes": 94,
      "match_date": "2026-01-14 19:30:00.000000"
    },
    {
      "id": 10477,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 18,
      "points": 407,
      "minutes": 39,
      "match_date": "2026-01-17 17:30:00.000000"
    },
    {
      "id": 10478,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 19,
      "points": 152,
      "minutes": 98,
      "match_date": "2026-01-24 14:30:00.000000"
    },
    {
      "id": 10479,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 20,
      "points": 155,
      "minutes": 106,
      "match_date": "2026-01-31 17:30:00.000000"
    },
    {
      "id": 10480,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 21,
      "points": 160,
      "minutes": 66,
      "match_date": "2026-02-08 16:30:00.000000"
    },
    {
      "id": 10481,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 22,
      "points": 0,
      "minutes": 0,
      "match_date": "2026-02-14 14:30:00.000000"
    },
    {
      "id": 10482,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 23,
      "points": 177,
      "minutes": 97,
      "match_date": "2026-02-21 14:30:00.000000"
    },
    {
      "id": 10483,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 24,
      "points": 106,
      "minutes": 99,
      "match_date": "2026-02-28 17:30:00.000000"
    },
    {
      "id": 10484,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 25,
      "points": 0,
      "minutes": 0,
      "match_date": "2026-03-06 19:30:00.000000"
    },
    {
      "id": 10485,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 26,
      "points": 177,
      "minutes": 92,
      "match_date": "2026-03-14 14:30:00.000000"
    },
    {
      "id": 10486,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 27,
      "points": 320,
      "minutes": 95,
      "match_date": "2026-03-21 14:30:00.000000"
    },
    {
      "id": 10487,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 28,
      "points": 99,
      "minutes": 45,
      "match_date": "2026-04-04 13:30:00.000000"
    },
    {
      "id": 10488,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 29,
      "points": 156,
      "minutes": 60,
      "match_date": "2026-04-11 16:30:00.000000"
    },
    {
      "id": 10489,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 30,
      "points": 152,
      "minutes": 49,
      "match_date": "2026-04-19 15:30:00.000000"
    },
    {
      "id": 10490,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 31,
      "points": 166,
      "minutes": 54,
      "match_date": "2026-04-25 13:30:00.000000"
    },
    {
      "id": 10491,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 32,
      "points": 290,
      "minutes": 57,
      "match_date": "2026-05-02 13:30:00.000000"
    },
    {
      "id": 10492,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 33,
      "points": 334,
      "minutes": 98,
      "match_date": "2026-05-09 16:30:00.000000"
    },
    {
      "id": 10493,
      "player_id": "8329",
      "season": "2025/2026",
      "matchday": 34,
      "points": 81,
      "minutes": 34,
      "match_date": "2026-05-16 13:30:00.000000"
    },
    {
      "id": 21733,
      "player_id": "6668",
      "season": "2026/2027",
      "matchday": 1,
      "points": 110,
      "minutes": 85,
      "match_date": "2026-08-28 18:30:00.000000"
    },
    {
      "id": 21737,
      "player_id": "8329",
      "season": "2026/2027",
      "matchday": 1,
      "points": 249,
      "minutes": 96,
      "match_date": "2026-08-28 18:30:00.000000"
    },
    {
      "id": 31107,
      "player_id": "8329",
      "season": "2026/2027",
      "matchday": 2,
      "points": 125,
      "minutes": 37,
      "match_date": "2026-09-05 16:30:00.000000"
    },
    {
      "id": 31284,
      "player_id": "6668",
      "season": "2026/2027",
      "matchday": 2,
      "points": 0,
      "minutes": 0,
      "match_date": "2026-09-05 16:30:00.000000"
    }
  ],
  "squad_rows": [
    {
      "pid": "4333",
      "name": "Ullrich",
      "mv": 5620487,
      "position": 2,
      "kb_prob": 3
    },
    {
      "pid": "2855",
      "name": "Ache",
      "mv": 13652487,
      "position": 4,
      "kb_prob": 3
    },
    {
      "pid": "1580",
      "name": "Schwäbe",
      "mv": 15844170,
      "position": 1,
      "kb_prob": 1
    },
    {
      "pid": "6668",
      "name": "Minjae",
      "mv": 12913190,
      "position": 2,
      "kb_prob": 2
    },
    {
      "pid": "3081",
      "name": "Vidović",
      "mv": 6706206,
      "position": 3,
      "kb_prob": 1
    },
    {
      "pid": "8329",
      "name": "Olise",
      "mv": 65065938,
      "position": 3,
      "kb_prob": 2
    },
    {
      "pid": "7320",
      "name": "Scheller",
      "mv": 9456614,
      "position": 2,
      "kb_prob": 1
    },
    {
      "pid": "4406",
      "name": "Dina Ebimbe",
      "mv": 4803289,
      "position": 3,
      "kb_prob": 3
    }
  ],
  "market_rows": [],
  "manager_rows": [],
  "budget": 2920437,
  "open_bids": [],
  "saved_lineup": {
    "it": [
      {
        "i": "4333",
        "lo": 4
      },
      {
        "i": "2855",
        "lo": 10
      },
      {
        "i": "1580",
        "lo": 0
      },
      {
        "i": "6668",
        "lo": 1
      },
      {
        "i": "3081",
        "lo": 7
      },
      {
        "i": "8329",
        "lo": 6
      },
      {
        "i": "7320",
        "lo": 2
      },
      {
        "i": "4406",
        "lo": 9
      }
    ]
  },
  "generated_at": "2026-09-11T19:52:37.519976+00:00",
  "user_id": "2663242",
  "user_name": "emil"
}
""")


def build(**overrides):
    inputs = deepcopy(REAL_EXPORT)
    inputs.update(overrides)
    return build_strategy_insights(**inputs)


def test_confirmed_feed_pairs_match_independent_retrospective():
    result = build()
    assert result["summary"]["confirmed_purchases"] == 12
    assert result["summary"]["confirmed_sales"] == 19
    assert result["summary"]["complete_round_trips"] == 6
    assert result["summary"]["realized_pnl_eur"] == -8_880_870
    assert result["summary"]["unknown_cost_basis_sales"] == 13
    assert result["summary"]["unrealized_pnl_eur"] == -10_107_064
    assert result["summary"]["holdings_with_cost_basis"] == 6


def test_duplicate_feed_does_not_duplicate_purchases_or_realized_pnl():
    baseline = build()
    doubled = build(feed_events=REAL_EXPORT["feed_events"] * 2)
    assert doubled["summary"] == baseline["summary"]
    assert doubled["round_trips"] == baseline["round_trips"]


def test_holdings_cannot_turn_audit_buy_price_into_a_confirmed_purchase():
    squad = deepcopy(REAL_EXPORT["squad_rows"])
    for player in squad:
        player["buy_price"] = player["mv"]
        player["bid_outcome"] = "won"
    result = build(feed_events=[], squad_rows=squad)
    assert result["summary"]["confirmed_purchases"] == 0
    assert result["summary"]["holdings_with_cost_basis"] == 0
    assert result["summary"]["unrealized_pnl_eur"] is None


def test_api_history_wins_over_database_independent_of_input_order():
    normal = build()
    reversed_rows = build(history_rows=list(reversed(REAL_EXPORT["history_rows"])))
    assert [(t["feed_id"], t["market_value"]) for t in normal["trades"]] == [
        (t["feed_id"], t["market_value"]) for t in reversed_rows["trades"]
    ]
    sales = [t for t in normal["trades"] if t["seller"] == "emil"]
    assert len(sales) == 19
    assert all(t["amount"] == t["market_value"] for t in sales)


def test_update_window_observation_stays_visible_but_out_of_quantiles():
    result = build()
    uncertain = [t for t in result["trades"] if t["update_boundary"] and t["buyer"] == "emil"]
    assert [t["feed_id"] for t in uncertain] == ["12371920348"]
    assert result["purchase_premiums"]["n_boundary_excluded"] == 1
    assert result["purchase_premiums"]["n_priced"] == sum(
        t["kind"] == "kb_purchase" and not t["update_boundary"] for t in result["trades"]
    )


def test_zero_minute_matchdays_are_preserved_and_seasons_are_separate():
    result = build()
    player = next(p for p in result["players"] if p["player_id"] == "6668")
    current = next(s for s in player["season_stats"] if s["season"] == "2026/2027")
    assert current["n_rows"] == 2
    assert current["n_appearances"] == 1
    assert current["zero_minute_rows"] == 1
    assert current["avg_points"] == 55
    assert current["small_sample"] is True
    assert current["points_distribution"]["0"] == 1
    assert len(player["season_stats"]) == 2


def test_missing_performance_rows_are_not_backfilled_with_zeroes():
    rows = [r for r in REAL_EXPORT["performance_rows"] if r["matchday"] != 2]
    result = build(performance_rows=rows)
    player = next(p for p in result["players"] if p["player_id"] == "6668")
    current = next(s for s in player["season_stats"] if s["season"] == "2026/2027")
    assert current["n_rows"] == 1
    assert current["avg_points"] == 110
    assert current["observed_matchdays"] == [1]
    assert player["coverage"]["missing_matchdays_are_unknown"] is True


def test_future_performance_rows_are_excluded_using_real_earlier_reference_date():
    result = build(generated_at="2026-08-30T00:00:00+00:00")
    player = next(p for p in result["players"] if p["player_id"] == "6668")
    current = next(s for s in player["season_stats"] if s["season"] == "2026/2027")
    assert current["observed_matchdays"] == [1]
    assert result["coverage"]["future_performance_rows_excluded"] == 2


def test_saved_lineup_is_authoritative_over_squad_prediction():
    squad = deepcopy(REAL_EXPORT["squad_rows"])
    for row in squad:
        row["elf"] = False
    portfolio = build(squad_rows=squad)["portfolio"]
    assert portfolio["empty_slots"] == [3, 5, 8]
    assert len(portfolio["occupied_slots"]) == 8
    assert portfolio["team_value_eur"] == 134_062_381
    assert portfolio["net_assets_eur"] == 136_982_818


def test_missing_sources_remain_unknown_and_cash_is_not_capped():
    partial = build(budget=None, open_bids=None, saved_lineup=None)
    assert partial["status"] == "partial"
    assert partial["portfolio"]["cash_after_all_bids_eur"] is None
    assert partial["portfolio"]["empty_slots"] is None
    amount = next(
        t["data"]["trp"]
        for t in REAL_EXPORT["feed_events"]
        if t["data"].get("byr", "").strip() == "emil"
    )
    scenario = build(open_bids=[{"amount": amount}])
    assert scenario["portfolio"]["cash_after_all_bids_eur"] == REAL_EXPORT["budget"] - amount


def test_inputs_are_not_mutated_and_output_is_json_safe():
    inputs = deepcopy(REAL_EXPORT)
    saved = deepcopy(inputs)
    result = build_strategy_insights(**inputs)
    assert inputs == saved
    json.dumps(result, allow_nan=False)
    with pytest.raises(ValueError):
        build(generated_at="not-a-date")


def test_missing_feed_is_unknown_not_zero_confirmed_transfers():
    result = build(feed_events=None)
    assert result["summary"]["confirmed_purchases"] is None
    assert result["summary"]["complete_round_trips"] is None
    assert result["summary"]["transfer_count"] is None
    assert result["purchase_premiums"]["n"] is None


def test_duplicate_real_lineup_slot_is_unknown_not_a_partial_valid_lineup():
    lineup = deepcopy(REAL_EXPORT["saved_lineup"])
    lineup["it"].append(deepcopy(lineup["it"][0]))
    result = build(saved_lineup=lineup)
    assert result["status"] == "partial"
    assert result["portfolio"]["occupied_slots"] is None
    assert "saved_lineup_slots" in result["coverage"]["unknown_sources"]


@pytest.mark.parametrize("reverse", [False, True])
def test_performance_source_priority_retains_capture_time(reverse):
    # Dieselbe reale Beobachtung mit Collector-Provenienz; keine neue API-Antwort.
    rows = deepcopy(REAL_EXPORT["performance_rows"])
    direct = dict(rows[-1], source="api_performance", captured_at=REAL_EXPORT["generated_at"])
    rows.append(direct)
    result = build(performance_rows=list(reversed(rows)) if reverse else rows)
    player = next(p for p in result["players"] if p["player_id"] == direct["player_id"])
    point = next(
        p
        for p in player["performance"]
        if (p["season"], p["matchday"]) == (direct["season"], direct["matchday"])
    )
    assert point["source"] == "api_performance"
    assert point["captured_at"] == REAL_EXPORT["generated_at"]


def test_cached_api_rows_cannot_become_played_just_because_time_passes():
    from datetime import datetime, timedelta

    row = deepcopy(REAL_EXPORT["performance_rows"][-1])
    kickoff = datetime.fromisoformat(row["match_date"])
    row.update(source="api_performance", captured_at=(kickoff - timedelta(days=1)).isoformat())
    result = build(performance_rows=[row])
    player = next(p for p in result["players"] if p["player_id"] == row["player_id"])
    assert player["performance"] == []
    assert result["coverage"]["future_performance_rows_excluded"] == 1


def test_zero_api_row_on_capture_day_remains_unknown_until_later_observation():
    from datetime import datetime, timedelta

    row = deepcopy(next(r for r in REAL_EXPORT["performance_rows"]
                        if r["minutes"] == 0 and r["points"] == 0))
    match = datetime.fromisoformat(row["match_date"])
    row.update(source="api_performance", captured_at=match.isoformat())
    result = build(performance_rows=[row])
    player = next(p for p in result["players"] if p["player_id"] == row["player_id"])
    assert player["performance"] == []
    row["captured_at"] = (match + timedelta(days=1)).isoformat()
    result = build(performance_rows=[row])
    player = next(p for p in result["players"] if p["player_id"] == row["player_id"])
    assert player["performance"][0]["minutes"] == 0
