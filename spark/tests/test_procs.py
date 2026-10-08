"""Task 8's /proc readers, on /proc trees the tests build. Every file is in the kernel's own format, as
/proc/<pid>/status, comm and cmdline read on the Spark (fs/proc/array.c, task_mmu.c): status's fields tab-separated,
its sizes right-aligned in 8 columns with " kB", Uid's four ids (real, effective, saved, filesystem); comm the short
name and a newline; cmdline each argument and a NUL. A kernel thread's status has no Vm or Rss lines and its cmdline is
empty. Every pid, uid, size and name here is a stand-in; none was read from the box."""

from pathlib import Path

import pytest

from spark import procs
from spark.messages import Holder

KIB_PER_GIB = 1024 * 1024
SPARK, DAN, AGENT = 2001, 1000, 1002  # stand-in uids
USERS = {SPARK: "spark", DAN: "chendaniely", AGENT: "agent"}
REAL_USER = procs._user


@pytest.fixture
def proc(tmp_path, monkeypatch):
    """A /proc of no processes yet, with what else /proc holds beside them, which a scan must pass over."""
    root = tmp_path / "proc"
    root.mkdir()
    (root / "meminfo").write_text("MemTotal:       127622144 kB\nMemAvailable:   73400320 kB\n")
    (root / "sys" / "kernel" / "random").mkdir(parents=True)
    (root / "self").symlink_to("1")
    monkeypatch.setattr(procs, "_user", lambda uid: USERS.get(uid, str(uid)))
    return root


def process(proc: Path, pid: int, comm: str, *, uid: int | tuple[int, int, int, int], argv: tuple[str, ...] = (),
            rss_anon_kib: int | None = None, vm_rss_kib: int | None = None, state: str = "S (sleeping)") -> None:
    """A process in `proc`, its files as the kernel writes them. With no rss_anon_kib it has no memory lines at all,
    as a kernel thread's status hasn't. status's Name escapes only a backslash and a line break, as the kernel's does
    (checked on the Spark, 2026-10-08); comm holds the name raw."""
    uids = uid if isinstance(uid, tuple) else (uid,) * 4
    folder = proc / str(pid)
    folder.mkdir()
    name = comm.replace("\\", "\\\\").replace("\n", "\\n")
    lines = [f"Name:\t{name}", "Umask:\t0022", f"State:\t{state}", f"Tgid:\t{pid}", "Ngid:\t0", f"Pid:\t{pid}",
             "PPid:\t1", "TracerPid:\t0", "Uid:\t" + "\t".join(map(str, uids)),
             "Gid:\t" + "\t".join(map(str, uids)), "FDSize:\t64", "Groups:\t4 27 ", f"NStgid:\t{pid}",
             f"NSpid:\t{pid}", f"NSpgid:\t{pid}", f"NSsid:\t{pid}", "Kthread:\t0"]
    if rss_anon_kib is not None:
        vm_rss = rss_anon_kib + 8192 if vm_rss_kib is None else vm_rss_kib
        lines += [f"VmPeak:\t{vm_rss * 2:>8} kB", f"VmSize:\t{vm_rss * 2:>8} kB", f"VmLck:\t{0:>8} kB",
                  f"VmPin:\t{0:>8} kB", f"VmHWM:\t{vm_rss:>8} kB", f"VmRSS:\t{vm_rss:>8} kB",
                  f"RssAnon:\t{rss_anon_kib:>8} kB", f"RssFile:\t{vm_rss - rss_anon_kib:>8} kB",
                  f"RssShmem:\t{0:>8} kB", f"VmData:\t{rss_anon_kib:>8} kB", f"VmStk:\t{132:>8} kB",
                  f"VmExe:\t{2048:>8} kB", f"VmLib:\t{4096:>8} kB", f"VmPTE:\t{64:>8} kB", f"VmSwap:\t{0:>8} kB",
                  f"HugetlbPages:\t{0:>8} kB", "CoreDumping:\t0", "THP_enabled:\t1"]
    lines += ["Threads:\t1", "SigQ:\t0/481234", "Cpus_allowed_list:\t0-19", "voluntary_ctxt_switches:\t10",
              "nonvoluntary_ctxt_switches:\t2"]
    (folder / "status").write_text("\n".join(lines) + "\n")
    (folder / "comm").write_text(comm + "\n")
    (folder / "cmdline").write_bytes(b"".join(arg.encode() + b"\0" for arg in argv))


def engine(proc: Path, pid: int = 200, port: int = 801, *, uid: int | tuple[int, int, int, int] = SPARK,
           comm: str = "llama-server", rss_anon_kib: int = 2 * KIB_PER_GIB) -> None:
    process(proc, pid, comm, uid=uid, rss_anon_kib=rss_anon_kib,
            argv=(f"/opt/local-ai/engines/llama.cpp/build/bin/{comm}", "--host", "127.0.0.1", "--port", str(port),
                  "--model", "/srv/models/model.gguf", "--ctx-size", "8192"))


def gib(n: float) -> int:
    return int(n * KIB_PER_GIB)


def test_rss_anon_reads_the_anonymous_figure_not_vmrss(proc):
    process(proc, 200, "llama-server", uid=SPARK, vm_rss_kib=4194304, rss_anon_kib=1048576)
    assert procs.rss_anon_gib(200, proc=proc) == 1.0


def test_rss_anon_of_a_gone_process_is_none(proc):
    assert procs.rss_anon_gib(200, proc=proc) is None


def test_rss_anon_of_a_status_without_it_is_none(proc):
    process(proc, 2, "kthreadd", uid=0)  # a kernel thread: no memory lines
    assert procs.rss_anon_gib(2, proc=proc) is None


def test_a_process_name_cant_add_a_line_to_its_status(proc):
    # The kernel escapes a line break in status's Name, but not every character Python counts as one (\x1c, \x85).
    process(proc, 310, "x\x1cRssAnon:\t999", uid=SPARK, state="Z (zombie)")  # a zombie: no memory lines of its own
    assert procs.rss_anon_gib(310, proc=proc) is None


def test_port_of_reads_runnings_proxy():
    assert procs.port_of("http://127.0.0.1:801") == 801
    assert procs.port_of("nonsense") is None
    assert procs.port_of("http://127.0.0.1") is None  # no port
    assert procs.port_of("http://127.0.0.1:${PORT}") is None  # render's macro, never filled in


def test_engine_pid_finds_the_engine_by_its_port_among_sparks_processes(proc):
    process(proc, 150, "llama-server", uid=SPARK, rss_anon_kib=gib(1),  # spark's, on another port
            argv=("llama-server", "--host", "127.0.0.1", "--port", "803"))
    process(proc, 160, "python3", uid=SPARK, rss_anon_kib=gib(1),  # spark's, port 801 in its argv, not an engine
            argv=("python3", "-m", "http.server", "--port", "801"))
    engine(proc, 200, 801)
    assert procs.engine_pid(801, spark_uid=SPARK, proc=proc) == 200
    assert procs.engine_pid(802, spark_uid=SPARK, proc=proc) is None
    process(proc, 300, "python3", uid=SPARK, rss_anon_kib=gib(0.1),  # spark launch, before its exec
            argv=("/opt/local-ai/app/.venv/bin/python3", "/opt/local-ai/app/.venv/bin/spark", "launch", "coder"))
    assert procs.engine_pid(801, spark_uid=SPARK, recorded=300, proc=proc) == 300
    assert procs.engine_pid(801, spark_uid=SPARK, recorded=301, proc=proc) == 200  # 301 is gone: the scan's answer


def test_a_recorded_pid_that_isnt_a_live_process_of_sparks_is_not_taken(proc):
    engine(proc, 200, 801)
    process(proc, 300, "python3", uid=DAN, rss_anon_kib=gib(1), argv=("python3",))
    process(proc, 310, "llama-server", uid=SPARK, state="Z (zombie)")  # exited, not yet reaped
    assert procs.engine_pid(801, spark_uid=SPARK, recorded=300, proc=proc) == 200
    assert procs.engine_pid(801, spark_uid=SPARK, recorded=310, proc=proc) == 200


def test_whisper_server_is_an_engine_too(proc):
    engine(proc, 210, 805, comm="whisper-server")
    assert procs.engine_pid(805, spark_uid=SPARK, proc=proc) == 210


def test_an_engine_of_another_user_is_never_matched(proc):
    engine(proc, 200, 801, uid=DAN)
    assert procs.engine_pid(801, spark_uid=SPARK, proc=proc) is None
    engine(proc, 201, 802, uid=(DAN, SPARK, SPARK, SPARK))  # spark's only in its effective ids: the real uid counts
    assert procs.engine_pid(802, spark_uid=SPARK, proc=proc) is None


def test_nvidia_apps_parse_and_na_reads_as_unknown():
    assert procs.parse_nvidia_apps("200, 26624\n300, [N/A]\n") == {200: 26.0, 300: None}
    assert procs.parse_nvidia_apps("") == {}


def test_top_holders_name_stack_models_by_label_and_others_by_comm_and_user(proc):
    engine(proc, 200, 801)
    process(proc, 400, "python3", uid=DAN, rss_anon_kib=gib(32), argv=("python3", "train.py"))
    process(proc, 410, "bash", uid=DAN, rss_anon_kib=gib(0.01), argv=("-bash",))  # under 1 GiB
    process(proc, 2, "kthreadd", uid=0)  # a kernel thread: no memory lines
    holders = procs.top_holders({"gemma": ("Gemma", 27.0)}, {}, engine_pids={200}, dan_uids={DAN}, proc=proc)
    assert holders == [Holder("python3 (chendaniely)", 32.0, True), Holder("Gemma", 27.0, False)]


def test_a_cuda_job_counts_at_nvidia_smis_figure(proc):
    process(proc, 400, "python3", uid=AGENT, rss_anon_kib=gib(1), argv=("python3", "finetune.py"))
    holders = procs.top_holders({}, procs.parse_nvidia_apps("400, 20480\n"), engine_pids=set(), dan_uids={DAN},
                                proc=proc)
    assert holders == [Holder("python3 (agent)", 20.0, False)]


def test_a_process_nvidia_smi_cant_size_counts_at_its_rss_anon(proc):
    process(proc, 400, "python3", uid=DAN, rss_anon_kib=gib(3), argv=("python3",))
    holders = procs.top_holders({}, {400: None}, engine_pids=set(), dan_uids={DAN}, proc=proc)
    assert holders == [Holder("python3 (chendaniely)", 3.0, True)]


def test_no_holder_is_named_by_its_command_line(proc):
    process(proc, 400, "python3", uid=DAN, rss_anon_kib=gib(5),
            argv=("python3", "serve.py", "--token-file-here", "/home/someone/token"))
    holders = procs.top_holders({}, {}, engine_pids=set(), dan_uids={DAN}, proc=proc)
    assert [h.name for h in holders] == ["python3 (chendaniely)"]
    assert not any("--token-file-here" in h.name or "serve.py" in h.name for h in holders)


def test_an_engine_is_counted_once_as_its_model(proc):
    engine(proc, 200, 801, rss_anon_kib=gib(2))
    holders = procs.top_holders({"gemma": ("Gemma", 27.0)}, {200: 25.0}, engine_pids={200}, dan_uids={DAN},
                                proc=proc)
    assert holders == [Holder("Gemma", 27.0, False)]


def test_holders_are_the_largest_first_from_1_gib_and_limit_of_them(proc):
    process(proc, 400, "python3", uid=DAN, rss_anon_kib=gib(1), argv=("python3",))  # exactly 1 GiB: counts
    process(proc, 401, "node", uid=AGENT, rss_anon_kib=gib(1) - 1, argv=("node",))  # just under: doesn't
    process(proc, 402, "java", uid=AGENT, rss_anon_kib=gib(9), argv=("java",))
    loaded = {"gemma": ("Gemma", 27.0), "embed": ("the embedder", 4.0)}
    everyone = procs.top_holders(loaded, {}, engine_pids=set(), dan_uids={DAN}, proc=proc, limit=10)
    assert everyone == [Holder("Gemma", 27.0, False), Holder("java (agent)", 9.0, False),
                        Holder("the embedder", 4.0, False), Holder("python3 (chendaniely)", 1.0, True)]
    assert procs.top_holders(loaded, {}, engine_pids=set(), dan_uids={DAN}, proc=proc) == everyone[:3]


def test_a_holder_name_holds_no_control_character(proc):
    # comm is raw: any user can name a process with a line break or a terminal's escape sequence (15 bytes at most).
    process(proc, 400, "py\nthon\x1b[2J", uid=DAN, rss_anon_kib=gib(2), argv=("python3",))
    # The registry refuses such a label, but top_holders takes whatever it is given.
    loaded = {"gemma": ("Gemma", 27.0), "odd": ("an\x1b[1modd\tlabel", 5.0)}
    holders = procs.top_holders(loaded, {}, engine_pids=set(), dan_uids={DAN}, proc=proc)
    assert [h.name for h in holders] == ["Gemma", "an [1modd label", "py thon [2J (chendaniely)"]
    assert all(c.isprintable() for h in holders for c in h.name)


def unreadable(monkeypatch, pid: int, error: type[OSError]) -> None:
    """Every file of `pid`'s raises `error` when read: PermissionError as a /proc mounted hidepid=noaccess gives for
    another user's process, ProcessLookupError (ESRCH) as a read gives once the process has exited."""
    real = Path.read_bytes

    def read_bytes(self):
        if self.parent.name == str(pid):
            raise error(f"stand-in {error.__name__}: {self.name}")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)


def test_a_process_proc_wont_let_us_read_is_an_error_not_a_silence(proc, monkeypatch):
    process(proc, 150, "python3", uid=DAN, rss_anon_kib=gib(30), argv=("python3",))
    engine(proc, 200, 801)
    unreadable(monkeypatch, 150, PermissionError)
    with pytest.raises(PermissionError):
        procs.rss_anon_gib(150, proc=proc)
    with pytest.raises(PermissionError):
        procs.top_holders({}, {}, engine_pids={200}, dan_uids={DAN}, proc=proc)
    with pytest.raises(PermissionError):
        procs.engine_pid(801, spark_uid=SPARK, proc=proc)
    with pytest.raises(PermissionError):
        procs.engine_pid(801, spark_uid=SPARK, recorded=150, proc=proc)


def test_a_process_that_exits_mid_read_is_passed_over(proc, monkeypatch):
    process(proc, 150, "python3", uid=DAN, rss_anon_kib=gib(30), argv=("python3",))
    process(proc, 400, "java", uid=AGENT, rss_anon_kib=gib(9), argv=("java",))
    engine(proc, 200, 801)
    unreadable(monkeypatch, 150, ProcessLookupError)
    assert procs.rss_anon_gib(150, proc=proc) is None
    assert procs.top_holders({}, {}, engine_pids={200}, dan_uids={DAN}, proc=proc) == [
        Holder("java (agent)", 9.0, False)]
    assert procs.engine_pid(801, spark_uid=SPARK, proc=proc) == 200
    assert procs.engine_pid(801, spark_uid=SPARK, recorded=150, proc=proc) == 200


def test_a_holder_whose_uid_has_no_name_shows_its_uid(proc, monkeypatch):
    monkeypatch.setattr(procs, "_user", REAL_USER)  # the system's own lookup, which has no such uid
    process(proc, 400, "python3", uid=3999999, rss_anon_kib=gib(2), argv=("python3",))
    holders = procs.top_holders({}, {}, engine_pids=set(), dan_uids={DAN}, proc=proc)
    assert holders == [Holder("python3 (3999999)", 2.0, False)]


def test_a_process_on_nvidia_smis_list_that_proc_doesnt_show_is_not_named(proc):
    holders = procs.top_holders({}, {500: 30.0}, engine_pids=set(), dan_uids={DAN}, proc=proc)
    assert holders == []
