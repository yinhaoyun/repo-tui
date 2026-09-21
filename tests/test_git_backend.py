from repo_tui.git_backend import _parse_numstat, _parse_status_v2


def test_parse_numstat_basic():
    out = "3\t1\tfoo.py\n0\t0\tbar.txt\n"
    assert _parse_numstat(out) == {"foo.py": (3, 1), "bar.txt": (0, 0)}


def test_parse_numstat_binary_file():
    out = "-\t-\timage.png\n"
    assert _parse_numstat(out) == {"image.png": (0, 0)}


def test_parse_status_v2_branch_and_ahead_behind():
    out = "\n".join(
        [
            "# branch.oid abcdef",
            "# branch.head main",
            "# branch.upstream origin/main",
            "# branch.ab +2 -1",
        ]
    )
    branch, detached, ahead, behind, files = _parse_status_v2(out)
    assert branch == "main"
    assert not detached
    assert ahead == 2
    assert behind == 1
    assert files == []


def test_parse_status_v2_detached():
    out = "# branch.head (detached)"
    branch, detached, ahead, behind, files = _parse_status_v2(out)
    assert detached
    assert branch == ""


def test_parse_status_v2_files():
    out = "\n".join(
        [
            "# branch.head main",
            "1 M. N... 100644 100644 100644 aaaa bbbb README.md",
            "1 .M N... 100644 100644 100644 aaaa bbbb tracked.py",
            "? untracked.txt",
        ]
    )
    _, _, _, _, files = _parse_status_v2(out)
    codes = {f.path: f.code for f in files}
    assert codes["README.md"] == "M."
    assert codes["tracked.py"] == ".M"
    assert codes["untracked.txt"] == "??"
    untracked = next(f for f in files if f.path == "untracked.txt")
    assert untracked.is_untracked


def test_parse_status_v2_rename():
    out = "\n".join(
        [
            "# branch.head main",
            "2 R. N... 100644 100644 100644 aaaa bbbb R100 new_name.py\told_name.py",
        ]
    )
    _, _, _, _, files = _parse_status_v2(out)
    assert files[0].path == "new_name.py"
