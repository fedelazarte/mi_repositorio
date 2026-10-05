from job_agent.schedule import cron_line, plist_dict


def test_plist_runs_diario_at_the_given_hour(tmp_path):
    payload = plist_dict(
        python="/usr/bin/python3",
        workdir=tmp_path,
        hour=9,
        minute=15,
        log_path=tmp_path / "diario.log",
        env={"JOB_AGENT_SMTP_HOST": "smtp.gmail.com"},
    )
    assert payload["ProgramArguments"][-2:] == ["job_agent", "diario"]
    assert payload["StartCalendarInterval"] == {"Hour": 9, "Minute": 15}
    assert payload["WorkingDirectory"] == str(tmp_path)
    assert payload["EnvironmentVariables"]["JOB_AGENT_SMTP_HOST"] == "smtp.gmail.com"
    assert payload["ProgramArguments"][-1] == "diario"


def test_cron_line_points_at_the_project(tmp_path):
    line = cron_line(python="/usr/bin/python3", workdir=tmp_path, hour=9, minute=0, log_path=tmp_path / "diario.log")
    assert line.startswith("0 9 * * *")
    assert "job_agent diario" in line
    assert str(tmp_path) in line
