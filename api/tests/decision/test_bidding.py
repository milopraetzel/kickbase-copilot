from kickbase_agent.decision.bidding import TeamValue, choose_bid


def test_prefers_team_opportunity_over_largest_win_probability():
    def win(amount):
        points = 220 if amount < 6_500_000 else 215 if amount < 7_000_000 else 195
        return TeamValue(11, points, 10_000_000 - amount)

    def probability(amount):
        return .35 if amount < 6_500_000 else .60 if amount < 7_000_000 else .80

    decision = choose_bid(
        market_value=6_000_000, minimum_bid=6_000_000, maximum_bid=7_000_000,
        evaluate_win=win, evaluate_loss=lambda: TeamValue(11, 190, 5_000_000),
        probability=probability, additional_prices=[6_500_000],
    )
    assert decision.amount == 6_500_000
    assert next(s for s in decision.scenarios if s["selected"])["expected_points"] == 205


def test_economic_cap_preserves_eleventh_slot_exactly():
    result = choose_bid(
        market_value=1_000_000, minimum_bid=1_000_000, maximum_bid=5_000_000,
        evaluate_win=lambda b: TeamValue(11 if b <= 2_345_678 else 10, 110, 5_000_000-b),
        evaluate_loss=lambda: TeamValue(10, 150, 5_000_000),
    )
    assert result.max_bid_eur == 2_345_678
    assert result.amount == 1_000_000
    assert result.probability_status == "unknown"
    assert all(s["win_probability"] is None for s in result.scenarios)


def test_cheaper_equal_team_beats_unnecessary_purchase():
    result = choose_bid(
        market_value=1_000_000, minimum_bid=1_000_000, maximum_bid=2_000_000,
        evaluate_win=lambda b: TeamValue(11, 500, 3_000_000-b),
        evaluate_loss=lambda: TeamValue(11, 500, 3_000_000),
    )
    assert result.amount is None


def test_infeasible_or_invalid_scenarios_never_produce_bid():
    for value in (None, TeamValue(11, float("nan"), 1), TeamValue(11, 500, -1)):
        assert choose_bid(
            market_value=1, minimum_bid=1, maximum_bid=2,
            evaluate_win=lambda b, value=value: value, evaluate_loss=lambda: TeamValue(10, 400, 10),
        ).amount is None


def test_negative_points_allowed_when_completing_eleven():
    assert choose_bid(
        market_value=1, minimum_bid=1, maximum_bid=2,
        evaluate_win=lambda b: TeamValue(11, -5, 5-b),
        evaluate_loss=lambda: TeamValue(10, 0, 5),
    ).amount == 1


def test_equal_acquisition_alternatives_do_not_deadlock_completion():
    assert choose_bid(
        market_value=1, minimum_bid=1, maximum_bid=2,
        evaluate_win=lambda b: TeamValue(11, 100, 5-b),
        evaluate_loss=lambda: TeamValue(11, 100, 4),
    ).amount == 1


# Gebotsregel V3 (Emil 25.09.2026): Ohne geladenes Zuschlagsmodell ist der Einstieg das
# Zielgebot des Aufrufers, begrenzt auf [Mindestpreis, wirtschaftliche Obergrenze].


def _completion(target, *, useful_up_to=1_200_000, probability=None):
    # Bis useful_up_to füllt der Kauf den elften Platz; darüber fehlt das Geld für den
    # Rest der Elf und die Mannschaft wäre schlechter als der Ersatzweg.
    return choose_bid(
        market_value=1_000_000, minimum_bid=1_000_000, maximum_bid=1_300_000,
        evaluate_win=lambda b: (
            TeamValue(11, 100, 2_000_000 - b) if b <= useful_up_to
            else TeamValue(10, 80, 2_000_000 - b)
        ),
        evaluate_loss=lambda: TeamValue(10, 90, 2_000_000),
        probability=probability,
        target_amount=target,
        target_reason="Gebotsregel V3: +6,5 % auf den Marktwert.",
    )


def test_target_is_the_entry_when_competition_is_unknown():
    result = _completion(1_065_000)

    assert result.amount == 1_065_000
    assert result.probability_status == "unknown"
    assert [s["amount"] for s in result.scenarios if s["selected"]] == [1_065_000]
    assert result.reason.startswith("Gebotsregel V3")
    assert result.max_bid_eur == 1_200_000


def test_target_above_the_economic_cap_is_capped():
    result = _completion(1_250_000, useful_up_to=1_123_456)

    assert result.amount == result.max_bid_eur == 1_123_456
    assert [s["amount"] for s in result.scenarios if s["selected"]] == [1_123_456]
    assert "Obergrenze" in result.reason


def test_target_below_the_minimum_bids_the_minimum():
    result = _completion(900_000)

    assert result.amount == 1_000_000
    assert [s["amount"] for s in result.scenarios if s["selected"]] == [1_000_000]


def test_without_target_the_minimum_entry_stays():
    result = _completion(None)

    assert result.amount == 1_000_000
    assert result.reason.startswith("Kleinster sinnvoller Einstieg")


def test_a_loaded_competition_model_decides_instead_of_the_target():
    result = _completion(1_151_234, probability=lambda amount: 0.5)

    assert result.probability_status == "model"
    assert result.amount == 1_000_000  # gleiche Chance, mehr Restgeld


def test_a_model_without_probabilities_still_uses_the_target():
    # Review 25.09.2026, Befund 4: ein geladenes Modell, das None liefert (etwa außerhalb
    # seines Marktwertbereichs), fiel auf MW + 0 zurück.
    result = _completion(1_065_000, probability=lambda amount: None)

    assert result.probability_status == "unknown"
    assert result.amount == 1_065_000


def test_target_stays_below_an_equally_good_cheaper_alternative():
    # Gleich gute Alternative zum Mindestpreis 1.100.000 €. Hebt V3 das Ziel genau
    # dorthin, sind beide gleich teuer, und der Planer darf den Ersatz nehmen, auf den
    # dann nur der Marktwert geboten wird (Produktionsform-Test 25.09.2026).
    result = choose_bid(
        market_value=1_000_000, minimum_bid=1_000_000, maximum_bid=1_300_000,
        evaluate_win=lambda b: TeamValue(11, 100, 10_000_000 - b),
        evaluate_loss=lambda: TeamValue(11, 100, 10_000_000 - 1_100_000),
        target_amount=1_150_000,
        target_reason="Gebotsregel V3: +15,0 % auf den Marktwert.",
    )

    assert result.max_bid_eur == 1_100_000
    assert 1_095_000 <= result.amount < 1_100_000
    assert "gekürzt" in result.reason
    assert "gleich guten, günstigeren Ersatz" in result.reason  # Satzkomma bleibt Komma


def test_target_is_shortened_instead_of_costing_another_slot():
    # Review 25.09.2026, Befund 1: Ab 1.050.000 € fehlt das Geld für ein zweites Ziel.
    # Der Kauf bliebe besser als der Ersatzweg, aber schlechter als beim Mindestpreis.
    result = choose_bid(
        market_value=1_000_000, minimum_bid=1_000_000, maximum_bid=1_300_000,
        evaluate_win=lambda b: (
            TeamValue(11, 200, 5_000_000 - b) if b <= 1_050_000
            else TeamValue(10, 150, 5_000_000 - b)
        ),
        evaluate_loss=lambda: TeamValue(10, 120, 5_000_000),
        target_amount=1_080_000,
        target_reason="Gebotsregel V3: +8,0 % auf den Marktwert.",
    )

    assert result.max_bid_eur == 1_300_000
    assert 1_045_000 <= result.amount <= 1_050_000
    [selected] = [s for s in result.scenarios if s["selected"]]
    assert selected["amount"] == result.amount
    assert selected["win"]["filled_slots"] == 11
    assert "gekürzt" in result.reason
