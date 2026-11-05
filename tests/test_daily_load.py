from daily_load import _trigger_source


# A run on GitHub is labeled by its event; a local run is "cli".
def test_trigger_source_labels_github_and_local_runs():
    assert _trigger_source({"GITHUB_EVENT_NAME": "schedule"}) == "github-schedule"
    assert _trigger_source({"GITHUB_EVENT_NAME": "workflow_dispatch"}) == "github-workflow_dispatch"
    assert _trigger_source({}) == "cli"
