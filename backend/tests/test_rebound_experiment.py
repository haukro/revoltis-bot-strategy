from app.rebound_experiment import smoke_cases


def test_preregistered_rebound_smoke_cases():
    checks = smoke_cases()
    assert checks == {
        "utc_4h_boundaries": True,
        "latest_equal_low": True,
        "confirmation_after_low": True,
        "no_lookahead": True,
    }
