"""studio.deploy.Remote.run reads a command's output to its end -- the server's EOF -- and not only until the
exit status is in: an SSH server can send the exit status while the end of a long output (the tail of a big
server.log over a slow link) is still on its way.  Stopping at the exit status lost the newest lines of
`tail -n 20000 server.log`, and an old Warden setup PIN was read from a log whose last start never arrived."""
import time

from fivem2open77.studio import deploy as D


class Chan:
    """A session channel whose exit status is there at once and whose output comes after it, a piece every
    GAP seconds, then (EOF) the end of it."""

    def __init__(self, pieces, gap=0.15, eof=True, code=0):
        self.pieces, self.gap, self.send_eof, self.code = list(pieces), gap, eof, code
        self.buf, self.sent = b"", 0
        self.eof_received = self.closed = False
        self.t0 = None

    def set_combine_stderr(self, v):
        pass

    def settimeout(self, t):
        pass

    def exec_command(self, cmd):
        self.cmd, self.t0 = cmd, time.time()

    def _pump(self):
        now = time.time()
        while self.pieces and now >= self.t0 + self.gap * (self.sent + 1):
            self.buf += self.pieces.pop(0)
            self.sent += 1
        if not self.pieces and self.send_eof and now >= self.t0 + self.gap * (self.sent + 1):
            self.eof_received = True

    def recv_ready(self):
        self._pump()
        return bool(self.buf)

    def recv(self, n):
        self._pump()
        d, self.buf = self.buf[:n], self.buf[n:]
        return d

    def exit_status_ready(self):
        return True

    def recv_exit_status(self):
        return self.code

    def close(self):
        self.closed = True


class Client:
    def __init__(self, chan):
        self.chan = chan

    def get_transport(self):
        chan = self.chan

        class T:
            def open_session(self):
                return chan
        return T()


def remote(chan):
    r = object.__new__(D.Remote)
    r.client = Client(chan)
    return r


LOG = [b"2026-10-22 12:23:30.000 +00:00 [INF] Server configuration: UDP port=11840\n  Warden setup PIN: 56793301\n",
       b"2026-10-22 12:23:48.580 +00:00 [INF] Warden admin panel listening on http://0.0.0.0:11842.\n",
       b"2026-10-23 03:02:01.834 +00:00 [WRN] Warden setup PIN rejected from 127.0.0.1.\n",
       b"2026-10-23 03:09:39.465 +00:00 [INF] Warden administrator account 'XEROX710' created."]   # (no newline at the end)


def test_the_output_is_read_to_its_end_not_to_the_exit_status():
    got = []
    code, out = remote(Chan(LOG)).run("tail -n 20000 server.log", timeout=30, on_line=got.append)
    lines = out.splitlines()
    assert code == 0 and len(lines) == 5, lines
    assert lines[1] == "  Warden setup PIN: 56793301" and lines[-1].endswith("account 'XEROX710' created.")
    assert got == lines                                         # (each line to on_line, in order)


def test_a_server_that_never_sends_its_eof_does_not_hang_the_command(monkeypatch):
    monkeypatch.setattr(D, "EOF_GRACE", 0.6)
    t0 = time.time()
    code, out = remote(Chan(LOG, eof=False, code=3)).run("tail server.log", timeout=30, check=False)
    took = time.time() - t0
    assert code == 3 and len(out.splitlines()) == 5 and took < 0.15 * 5 + 0.6 + 1.5, (took, out)


def test_a_failing_command_still_raises_with_its_last_lines():
    try:
        remote(Chan([b"step 1\n", b"FAILED: the disk is full\n"], code=2)).run("deploy", timeout=30)
    except D.DeployError as e:
        assert "exit 2" in str(e) and "the disk is full" in str(e)
    else:
        raise AssertionError("no DeployError")
