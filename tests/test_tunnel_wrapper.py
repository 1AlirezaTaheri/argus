#!/usr/bin/env python3
"""Regression tests for docker/tunnel-entrypoint.sh.

The wrapper shipped broken twice, and both defects were invisible from the
outside: the container was up, the health check was satisfied by the previous
binary-exists probe, and nothing in the test suite exercised the script at all.

Each test below corresponds to a bug that actually happened:

1. **The FIFO deadlock.** `exec 3>"$FIFO"` opened the write end before any
   reader existed. Opening a FIFO for writing blocks until a reader attaches, so
   the script hung on that line and cloudflared was never started. Observed as
   an empty container log, no cloudflared process, and a status file that said
   `starting` forever -- while `docker compose ps` reported the container running.

2. **The wrong hostname.** The first extraction matched *any*
   `https://*.trycloudflare.com` in the output. cloudflared also logs
   documentation links, so the wrapper published `https://api.trycloudflare.com`
   as the tunnel's address and the bot's Web App button pointed at Cloudflare's
   API host, which answers 405 to everything. Correct output was still visible
   in the container log; only the published file was wrong.

3. **No EOF.** `exec 3<>"$FIFO"` fixed the deadlock by holding a write end,
   which means the read loop can never see end-of-file. A cloudflared that died
   on its own would have left the wrapper blocked in `read` forever: a running
   container serving nothing, which is precisely the outage the health check
   exists to catch.

So these tests drive the real script with a fake `cloudflared`, and assert on
the files it writes and the exit code it returns -- not on whether it appears
to be running.

Requires a POSIX shell, so it skips on Windows rather than failing there.
"""
from __future__ import annotations

import os
import pathlib
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
WRAPPER = REPO / "docker" / "tunnel-entrypoint.sh"

POSIX = shutil.which("sh") is not None and os.name == "posix"

pytestmark = pytest.mark.skipif(
    not POSIX, reason="the tunnel wrapper is a POSIX shell script"
)

# What cloudflared actually prints, abridged. The decoy matters: it is the
# documentation link that the original extraction mistook for the tunnel.
BANNER = """
2026-10-11T23:04:10Z INF Version 2024.10.0
2026-10-11T23:04:10Z INF See https://api.trycloudflare.com for documentation
2026-10-11T23:04:10Z INF +----------------------------------------------------------------+
2026-10-11T23:04:10Z INF |  Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):  |
2026-10-11T23:04:10Z INF |  https://std-paperbacks-latina-acceptance.trycloudflare.com                                     |
2026-10-11T23:04:10Z INF +----------------------------------------------------------------+
2026-10-11T23:04:52Z INF Registered tunnel connection connIndex=0
"""


def make_fake_cloudflared(
    directory: pathlib.Path, lines: str, *, delay: float = 0.0
) -> pathlib.Path:
    """Put a stand-in `cloudflared` first on PATH.

    The wrapper invokes it by name, so a PATH entry is how the real thing is
    replaced -- the script under test is otherwise unchanged and unmodified.

    `lines` is *output*, not shell: each line is emitted with `printf` rather
    than pasted into the script. The banners above are log lines, and without
    this the fake tried to run them (`Syntax error: "(" unexpected`, then
    `not found`) while the wrapper -- correctly -- reported a clean exit and
    published nothing. That was indistinguishable from a broken wrapper until
    the fake's own stderr was read.

    `delay` appends a `sleep`, so a test can observe a fake that is still
    running without smuggling shell into the body.
    """
    body = "\n".join(
        f"printf '%s\\n' {shlex.quote(line)}"
        for line in lines.strip("\n").splitlines()
    )
    if delay:
        body += f"\nsleep {delay:g}"
    path = directory / "cloudflared"
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def run_wrapper(
    fake_body: str,
    *,
    args: tuple[str, ...] = ("tunnel", "--url", "http://admin:8080"),
    timeout: float = 25.0,
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path, pathlib.Path]:
    """Run the wrapper against a fake cloudflared and return its results.

    A private directory holds the FIFO and the two files the wrapper writes, so
    runs cannot see each other's state -- a stale `registered` from a previous
    test would let a broken script pass.
    """
    work = pathlib.Path(tempfile.mkdtemp(prefix="argus-tunnel-"))
    (work / "bin").mkdir()
    make_fake_cloudflared(work / "bin", fake_body)

    url_file = work / "tunnel-url.conf"
    status_file = work / "tunnel-status.conf"

    env = dict(os.environ)
    env["PATH"] = f"{work / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    env["TUNNEL_URL_FILE"] = str(url_file)
    env["TUNNEL_STATUS_FILE"] = str(status_file)
    env["TUNNEL_FIFO"] = str(work / "fifo")

    proc = subprocess.run(
        ["/bin/sh", str(WRAPPER), *args],
        capture_output=True, text=True, timeout=timeout, env=env, cwd=work,
    )
    return proc, url_file, status_file


def test_wrapper_is_present_and_executable() -> None:
    assert WRAPPER.exists(), "docker/tunnel-entrypoint.sh is missing"
    assert WRAPPER.read_text(encoding="utf-8").startswith("#!/bin/sh")


def test_publishes_the_tunnel_hostname_not_a_documentation_link() -> None:
    """Bug 2: `api.trycloudflare.com` must never be published.

    The banner's own slug has to reach the file, because that file is what the
    bot puts in its Web App button. Getting this wrong does not raise anywhere:
    the button is well-formed, it just points at the wrong host.
    """
    proc, url_file, status_file = run_wrapper(BANNER)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    published = url_file.read_text(encoding="utf-8").strip()
    assert published == "https://std-paperbacks-latina-acceptance.trycloudflare.com", (
        f"published {published!r}, which is not the tunnel's hostname"
    )
    assert "api.trycloudflare.com" not in published
    assert "published https://std-paperbacks" in proc.stdout, (
        "the hostname must also appear in the container log, for an operator"
    )

    # The wrapper published, then its fake cloudflared exited, so the final
    # state is `exited` and not `registered`. That is the health check working
    # as intended: a status file that still said `registered` after the process
    # serving the tunnel was gone is exactly the lie the old
    # `cloudflared tunnel --help` probe told for eleven hours. The file is
    # asserted to be truthful about a *finished* run, not merely to have been
    # written.
    status = status_file.read_text(encoding="utf-8")
    assert "state=exited" in status, status
    assert "state=registered" not in status, (
        "a finished tunnel still claims to be registered"
    )


def test_a_hostname_is_not_published_without_the_banner() -> None:
    """A URL in passing prose is not an assignment.

    This is the inverse of the bug above and the reason the fix keys off the
    banner line rather than merely filtering out `api.`: Cloudflare's own hosts
    are not the only URLs in that output, and a filter list has to be maintained
    forever while a positive match does not.
    """
    proc, url_file, _ = run_wrapper(
        "2026-10-11T23:04:10Z INF docs at https://clever-dryer-flies.trycloudflare.com\n"
        "2026-10-11T23:04:11Z INF Registered tunnel connection connIndex=0\n"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not url_file.exists(), (
        "a hostname was published from a line that never announced one"
    )


def test_terminates_when_cloudflared_exits() -> None:
    """Bug 3: the wrapper must see EOF and fall through.

    With `exec 3<>"$FIFO"` the loop holds its own write end, so this read never
    returns. The wrapper would then sit in `read` while Docker reports a running
    container that serves nothing -- the exact state the health check exists to
    expose, made undetectable by the wrapper itself.

    The timeout is the assertion: hanging is the failure mode.
    """
    proc, _, status_file = run_wrapper("exit 0", timeout=20.0)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "state=exited" in status_file.read_text(encoding="utf-8")


def test_exits_nonzero_when_the_tunnel_was_reaped() -> None:
    """A reaped tunnel must be replaced, not retried.

    cloudflared retries `Unauthorized: Tunnel not found` forever, which is how
    the container sat healthy for eleven hours while every registration was
    refused. Exiting non-zero is what makes `restart: unless-stopped` mint a new
    hostname -- without it, retrying is the *only* thing that happens, and a
    reaped name never comes back.
    """
    proc, _, status_file = run_wrapper(
        "2026-10-11T20:32:10Z ERR Register tunnel error from server side "
        'error="Unauthorized: Tunnel not found" connIndex=0\n',
        timeout=20.0,
    )
    assert proc.returncode == 75, (
        f"expected the reaped-tunnel exit code, got {proc.returncode}"
    )
    assert "state=reaped" in status_file.read_text(encoding="utf-8")
    assert "reaped by Cloudflare" in proc.stdout, (
        "the reason must be in the container log, not only in a file"
    )


def test_does_not_hang_when_the_binary_is_missing() -> None:
    """A missing binary must fail loudly, not deadlock.

    `> "$FIFO"` is evaluated before the command is looked up, so an absent
    cloudflared blocks the child in open() and the parent in its own open(),
    both forever. That would be a third way to hold a container in `starting`.

    PATH is not simply emptied: without `mkdir`, `mkfifo`, `date` and `grep` the
    wrapper dies on its own preamble with a different status and exit code, and
    the test would pass for the wrong reason. Every tool the script legitimately
    needs is linked in; `cloudflared` alone is withheld.
    """
    work = pathlib.Path(tempfile.mkdtemp(prefix="argus-tunnel-"))
    bindir = work / "bin"
    bindir.mkdir()

    for tool in ("mkdir", "dirname", "rm", "mkfifo", "date", "grep", "head",
                 "cat", "mv", "printf", "sleep"):
        found = shutil.which(tool)
        if found:
            (bindir / tool).symlink_to(found)

    url_file = work / "tunnel-url.conf"
    status_file = work / "tunnel-status.conf"

    env = dict(os.environ)
    env["PATH"] = str(bindir)
    env["TUNNEL_URL_FILE"] = str(url_file)
    env["TUNNEL_STATUS_FILE"] = str(status_file)
    env["TUNNEL_FIFO"] = str(work / "fifo")

    proc = subprocess.run(
        ["/bin/sh", str(WRAPPER), "tunnel"],
        capture_output=True, text=True, timeout=20.0, env=env, cwd=work,
    )
    assert proc.returncode == 76, (
        f"expected 76, got {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    )
    assert "state=no-binary" in status_file.read_text(encoding="utf-8")
    assert "cloudflared is not installed" in proc.stdout


def test_status_reads_registered_while_the_tunnel_is_up() -> None:
    """What the health check actually sees in production.

    The test above proves the file is truthful once the tunnel is gone. This
    proves it says `registered` while cloudflared is alive, which is the state
    the compose health check greps for -- and if it never said so, every
    container would report unhealthy and the check would be useless in the other
    direction.
    """
    work = pathlib.Path(tempfile.mkdtemp(prefix="argus-tunnel-"))
    (work / "bin").mkdir()
    status_file = work / "tunnel-status.conf"
    make_fake_cloudflared(work / "bin", BANNER, delay=6.0)

    env = dict(os.environ)
    env["PATH"] = f"{work / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    env["TUNNEL_URL_FILE"] = str(work / "tunnel-url.conf")
    env["TUNNEL_STATUS_FILE"] = str(status_file)
    env["TUNNEL_FIFO"] = str(work / "fifo")

    proc = subprocess.Popen(
        ["/bin/sh", str(WRAPPER), "tunnel"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=env, cwd=work,
    )
    try:
        deadline = time.monotonic() + 12.0
        seen = ""
        while time.monotonic() < deadline:
            if status_file.exists():
                seen = status_file.read_text(encoding="utf-8")
                if "state=registered" in seen:
                    break
            time.sleep(0.1)
        assert "state=registered" in seen, (
            f"while the tunnel was alive the status never read registered: {seen!r}"
        )
        published = pathlib.Path(work / "tunnel-url.conf").read_text(encoding="utf-8")
        assert published.strip() == (
            "https://std-paperbacks-latina-acceptance.trycloudflare.com"
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def test_status_is_reset_on_start_so_a_stale_success_cannot_survive() -> None:
    """The health check must not be able to read yesterday's success.

    The wrapper runs again in the same volume after every restart. If it wrote
    `state=registered` only when it had news, the file would still say so from
    the previous run while the new one was still starting -- and the health
    check would pass during precisely the outage it was added to catch.

    This already happened once in another form: the old probe ran
    `cloudflared tunnel --help` and reported healthy through an outage.
    """
    work = pathlib.Path(tempfile.mkdtemp(prefix="argus-tunnel-"))
    (work / "bin").mkdir()
    status_file = work / "tunnel-status.conf"
    status_file.write_text("state=registered url=https://stale.old.host\n", encoding="utf-8")

    make_fake_cloudflared(
        work / "bin",
        # Long enough for the health check to have a chance to observe it.
        "still starting",
        delay=3.0,
    )

    env = dict(os.environ)
    env["PATH"] = f"{work / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    env["TUNNEL_URL_FILE"] = str(work / "tunnel-url.conf")
    env["TUNNEL_STATUS_FILE"] = str(status_file)
    env["TUNNEL_FIFO"] = str(work / "fifo")

    proc = subprocess.Popen(
        ["/bin/sh", str(WRAPPER), "tunnel"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=env, cwd=work,
    )
    try:
        deadline = time.monotonic() + 10.0
        seen = ""
        while time.monotonic() < deadline:
            seen = status_file.read_text(encoding="utf-8")
            if "starting" in seen:
                break
            time.sleep(0.1)
        assert "state=starting" in seen, (
            f"the stale success survived startup: {seen!r}"
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def test_healthcheck_command_in_compose_reads_the_status_file() -> None:
    """The compose health check and the wrapper must agree on what to read.

    A test that only exercises the script would pass while the container was
    still health-checked on `cloudflared tunnel --help` -- which exits 0 whenever
    the binary exists, and reported healthy through the eleven-hour outage this
    whole mechanism exists to prevent.
    """
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")

    # Comments are stripped first. The compose file deliberately explains *why*
    # the old probe was useless, and that explanation contains its exact text --
    # so an unfiltered assertion matched its own documentation and failed.
    active = "\n".join(
        line for line in compose.splitlines() if not line.lstrip().startswith("#")
    )

    assert "tunnel --help" not in active, (
        "the binary-exists health check is back"
    )
    assert "state=registered" in active, (
        "the tunnel is no longer health-checked on its registration status"
    )
    assert "tunnel_url" in active, "the published hostname volume is not wired"
    assert "docker/tunnel.Dockerfile" in active, (
        "the tunnel no longer builds the image that has a shell to run a wrapper"
    )


def test_a_refused_registration_degrades_rather_than_exiting() -> None:
    """A permanent state for a reason that may not be permanent.

    Cloudflare answers `Register tunnel error` for more than one cause, and only
    some of them mean the tunnel is finished. Exiting on every one of them would
    restart-loop through a transient Cloudflare-side outage -- and nothing here
    restarts on `unhealthy`, so the honest answer is to report degraded and let
    an operator see it.

    The state must also *persist*: a degraded tunnel whose next line is
    ordinary noise has to stay degraded, not quietly fall back to registered.
    """
    body = (
        "2026-10-11T23:04:10Z INF |  Your quick Tunnel has been created! Visit it at:  |\n"
        "2026-10-11T23:04:10Z INF |  https://std-paperbacks-latina-acceptance.trycloudflare.com  |\n"
        "2026-10-11T23:05:00Z ERR Register tunnel error from server side "
        'error="no route to origin" connIndex=0\n'
        "2026-10-11T23:05:01Z INF some ordinary noise that changes nothing\n"
        "2026-10-11T23:05:02Z INF some more ordinary noise\n"
    )
    work = pathlib.Path(tempfile.mkdtemp(prefix="argus-tunnel-"))
    (work / "bin").mkdir()
    status_file = work / "tunnel-status.conf"
    make_fake_cloudflared(work / "bin", body, delay=4.0)

    env = dict(os.environ)
    env["PATH"] = f"{work / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    env["TUNNEL_URL_FILE"] = str(work / "tunnel-url.conf")
    env["TUNNEL_STATUS_FILE"] = str(status_file)
    env["TUNNEL_FIFO"] = str(work / "fifo")

    proc = subprocess.Popen(
        ["/bin/sh", str(WRAPPER), "tunnel"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=env, cwd=work,
    )
    try:
        deadline = time.monotonic() + 12.0
        seen = ""
        while time.monotonic() < deadline:
            if status_file.exists():
                seen = status_file.read_text(encoding="utf-8")
                if "state=degraded" in seen:
                    break
            time.sleep(0.1)
        assert "state=degraded" in seen, (
            f"a refused registration never degraded the tunnel: {seen!r}"
        )
        # The hostname is still published: a degraded tunnel may recover, and
        # throwing away the name would make recovery impossible.
        published = pathlib.Path(work / "tunnel-url.conf").read_text(encoding="utf-8")
        assert published.strip() == (
            "https://std-paperbacks-latina-acceptance.trycloudflare.com"
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def test_degraded_is_not_confused_with_a_reap() -> None:
    """The two must not be interchangeable.

    A reap exits 75 so Docker can mint a new hostname; a degradation waits. If a
    generic registration error were treated as a reap, a transient Cloudflare
    outage would churn through hostnames, and each new one would have to be
    re-published to the bot before the Mini App worked again.
    """
    reap_line = (
        'ERR Register tunnel error from server side error="Unauthorized: '
        'Tunnel not found" connIndex=0\n'
    )
    proc, _, status_file = run_wrapper(
        "2026-10-11T23:04:10Z INF |  Your quick Tunnel has been created! Visit it at:  |\n"
        "2026-10-11T23:04:10Z INF |  https://std-paperbacks-latina-acceptance.trycloudflare.com  |\n"
        + reap_line,
        timeout=20.0,
    )
    assert proc.returncode == 75, (
        f"a reap must exit so a new hostname is minted, got {proc.returncode}"
    )
    assert "state=reaped" in status_file.read_text(encoding="utf-8")


def test_a_reaped_tunnel_keeps_the_last_hostname_published() -> None:
    """So an operator can see what was lost.

    The file is read by the bot, which will fall back to it on the next start;
    deleting it on a reap would leave nothing to compare a replacement against.
    """
    proc, url_file, status_file = run_wrapper(
        "2026-10-11T23:04:10Z INF |  Your quick Tunnel has been created! Visit it at:  |\n"
        "2026-10-11T23:04:10Z INF |  https://std-paperbacks-latina-acceptance.trycloudflare.com  |\n"
        '2026-10-11T23:05:00Z ERR Register tunnel error error="Unauthorized: '
        'Tunnel not found"\n',
        timeout=20.0,
    )
    assert proc.returncode == 75
    assert url_file.exists(), "the last hostname must survive a reap"
    assert "state=reaped" in status_file.read_text(encoding="utf-8")


def test_the_image_is_named_so_it_cannot_overwrite_its_own_input() -> None:
    """`image:` on a build is required here, not decorative.

    Without it compose names the result after the Dockerfile's first `FROM` and
    tagged the official `cloudflare/cloudflared:2024.10.0` -- the very image the
    Dockerfile copies the binary out of. The first build was correct because the
    binary had already been copied; the second would have copied it from a
    previous build of itself.
    """
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    assert "argus-tunnel:" in compose, "the tunnel image has no explicit name"

    tunnel_block = subprocess.run(
        ["awk", "/^  tunnel:/{f=1} f&&/^  [a-z_-]+:$/&&!/^  tunnel:/{f=0} f",
         str(REPO / "docker-compose.yml")],
        capture_output=True, text=True,
    ).stdout
    assert tunnel_block.count("    image:") == 1, (
        f"the tunnel declares {tunnel_block.count('    image:')} image keys"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
