"""Line counting used by the ``count-lines`` command."""

import importlib


def test_count_file_counts_lines(cutting, tmp_path):
    commands = importlib.import_module("esphomerelease.commands")
    path = tmp_path / "sample.txt"
    path.write_text("one\ntwo\nthree\n")
    assert commands.count_file(str(path)) == 3


def test_count_file_empty_file_is_zero(cutting, tmp_path):
    commands = importlib.import_module("esphomerelease.commands")
    path = tmp_path / "empty.txt"
    path.write_text("")
    assert commands.count_file(str(path)) == 0
