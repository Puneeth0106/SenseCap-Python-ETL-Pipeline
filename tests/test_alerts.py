from datetime import UTC, datetime, timedelta

import daily_load


# A recent Alpha timestamp is fresh, while an old one is not.
def test_alpha_freshness():
    class Connection:
        def __init__(self, timestamp):
            self.timestamp = timestamp

        def execute(self, *_args):
            return self

        def fetchone(self):
            return (self.timestamp,)

    assert daily_load._alpha_is_fresh(Connection(datetime.now(UTC) - timedelta(minutes=5)))
    assert not daily_load._alpha_is_fresh(Connection(datetime.now(UTC) - timedelta(minutes=31)))
    assert not daily_load._alpha_is_fresh(Connection(None))


# Moisture counts as live only when a reading above 0 is under 30 minutes old.
def test_moisture_is_live():
    class Connection:
        def __init__(self, timestamp):
            self.timestamp = timestamp
            self.args = None

        def execute(self, sql, args):
            self.sql, self.args = sql, args
            return self

        def fetchone(self):
            return (self.timestamp,)

    recent = Connection(datetime.now(UTC) - timedelta(minutes=5))
    assert daily_load._moisture_is_live(recent)
    assert "value > 0" in recent.sql
    assert recent.args == ("Soil_Moisture",)
    assert not daily_load._moisture_is_live(Connection(datetime.now(UTC) - timedelta(minutes=31)))
    assert not daily_load._moisture_is_live(Connection(None))


# A failed alert request must not expose its secret URL or crash the loader.
def test_ping_failure_is_logged_without_url(monkeypatch, caplog):
    def fail(*_args, **_kwargs):
        raise daily_load.requests.ConnectionError("secret-url")

    monkeypatch.setattr(daily_load.requests, "get", fail)
    daily_load._ping("secret-url", "job-ran")
    assert "job-ran" in caplog.text
    assert "secret-url" not in caplog.text
