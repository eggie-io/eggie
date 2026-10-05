import threading

import pytest

from eggie_api.routes.jobs import JobFailed, JobRegistry


def test_job_moves_from_running_to_done_and_keeps_its_result():
    started, release = threading.Event(), threading.Event()

    def work(log):
        started.set()
        release.wait(2)
        log("built\n")
        return {"status": "started_ok"}

    reg = JobRegistry()
    job_id = reg.submit(work)
    assert started.wait(2), "the job must run without the caller waiting on it"
    assert reg.get(job_id).state == "running"

    release.set()
    job = reg.wait(job_id, timeout=2)
    assert job.state == "done"
    assert job.result == {"status": "started_ok"}
    assert job.finished_at is not None


def test_failed_job_keeps_the_guests_own_stderr():
    # A bare exit code is unactionable: the reason lives in compose's stderr.
    def work(log):
        raise JobFailed("network edge declared as external, but could not be found")

    reg = JobRegistry()
    job = reg.wait(reg.submit(work), timeout=2)
    assert job.state == "failed"
    assert "network edge" in job.detail


def test_unexpected_exception_fails_the_job_instead_of_wedging_it_in_running():
    def work(log):
        raise ValueError("compose file vanished")

    reg = JobRegistry()
    job = reg.wait(reg.submit(work), timeout=2)
    assert job.state == "failed"
    assert "compose file vanished" in job.detail


def test_logs_can_be_followed_while_the_job_is_still_running():
    # A first image build takes minutes; output must be readable long before
    # the job ends.
    release = threading.Event()

    def work(log):
        log("pulling nginx\n")
        release.wait(2)
        log("done\n")

    reg = JobRegistry()
    job_id = reg.submit(work)
    stream = reg.follow(job_id)

    assert next(stream) == "pulling nginx\n"
    assert reg.get(job_id).state == "running"

    release.set()
    assert "done\n" in list(stream)
    assert reg.get(job_id).state == "done"


def test_follow_of_a_finished_job_replays_its_output_and_ends():
    reg = JobRegistry()
    job_id = reg.submit(lambda log: log("one\n") or log("two\n"))
    reg.wait(job_id, timeout=2)
    assert list(reg.follow(job_id)) == ["one\n", "two\n"]


def test_unknown_job_is_not_found():
    reg = JobRegistry()
    with pytest.raises(KeyError):
        list(reg.follow("nope"))


def test_finished_jobs_are_evicted_oldest_first():
    # Every job holds its whole log buffer; a long-lived API would otherwise
    # keep one per compose operation forever.
    reg = JobRegistry(max_finished=2)
    done = []
    for _ in range(4):
        job_id = reg.submit(lambda write: write("x\n"))
        reg.wait(job_id, timeout=2)
        done.append(job_id)

    # Eviction runs when a job is submitted, so the fourth submit drops the
    # oldest finished job and keeps the two newest.
    assert reg.get(done[0]) is None
    assert [reg.get(j) is not None for j in done[1:]] == [True, True, True]
