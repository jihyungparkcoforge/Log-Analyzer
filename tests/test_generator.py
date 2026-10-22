"""Tests for scripts/generate_logs.py: fixture mode, ad-hoc mode and write_log.

The script is imported as a module and always pointed at tmp_path (fixtures are
injected through main's `fixtures` argument), so the repo's data/ is never touched
and pytest never runs the script against it.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from log_analyzer.detection import (
    DetectionInput,
    RepeatedMessageDetector,
    SpikeDetector,
)
from log_analyzer.parsing import LogParser

SCRIPT = Path(__file__).parent.parent / "scripts" / "generate_logs.py"


def load_generator():
    """Import scripts/generate_logs.py as a module."""
    spec = importlib.util.spec_from_file_location("generate_logs", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = load_generator()


@pytest.fixture
def fixtures(tmp_path):
    """A FIXTURES-shaped mapping rooted in tmp_path, mirroring the real one."""
    return {
        "comma": tmp_path / "fx" / "sample.log",
        "json": tmp_path / "fx" / "sample.jsonl",
    }


def read(path: Path) -> tuple[bytes, dict]:
    """Return a log's bytes and its parsed sibling metadata."""
    return path.read_bytes(), json.loads(Path(f"{path}.metadata.json").read_text())


# --- Reserved slots ---------------------------------------------------------


def test_serializers_are_comma_only_and_fixtures_reserve_json():
    """Only comma is registered today, but FIXTURES already names the json slot."""
    assert list(gen.SERIALIZERS) == ["comma"]
    assert gen.FIXTURES["comma"].name == "sample.log"
    assert gen.FIXTURES["json"].name == "sample.jsonl"
    assert set(gen.SERIALIZERS) <= set(gen.FIXTURES)


# --- Fixture mode (--seed) --------------------------------------------------


def test_seed_writes_fixture_and_sibling_metadata(fixtures):
    """--seed alone writes the comma fixture and its metadata, and skips unregistered json."""
    assert gen.main(["--seed", "42"], fixtures=fixtures) == 0
    log, metadata = read(fixtures["comma"])
    assert log
    assert metadata["format"] == "comma"
    assert metadata["seed"] == 42
    assert not fixtures["json"].exists()
    assert not Path(f"{fixtures['json']}.metadata.json").exists()


def test_same_seed_gives_identical_bytes(fixtures, tmp_path):
    """Running the same seed twice reproduces the log and the metadata exactly."""
    gen.main(["--seed", "42"], fixtures=fixtures)
    first = read(fixtures["comma"])
    other = {name: tmp_path / "again" / path.name for name, path in fixtures.items()}
    gen.main(["--seed", "42"], fixtures=other)
    assert read(other["comma"]) == first
    assert (
        Path(f"{other['comma']}.metadata.json").read_bytes()
        == Path(f"{fixtures['comma']}.metadata.json").read_bytes()
    )


def test_different_seeds_differ(fixtures, tmp_path):
    """Different seeds give different background traffic."""
    gen.main(["--seed", "1"], fixtures=fixtures)
    other = {name: tmp_path / "b" / path.name for name, path in fixtures.items()}
    gen.main(["--seed", "2"], fixtures=other)
    assert read(fixtures["comma"])[0] != read(other["comma"])[0]


def test_fixture_mode_writes_every_registered_serializer(fixtures, monkeypatch):
    """Registering a second serializer makes the same loop write its fixture too."""

    class StubJson(gen.CommaSerializer):
        """Stand-in for a future serializer: comma lines under the name `json`."""

        name = "json"

    monkeypatch.setitem(gen.SERIALIZERS, "json", StubJson)
    assert gen.main(["--seed", "42"], fixtures=fixtures) == 0
    _, comma_meta = read(fixtures["comma"])
    _, json_meta = read(fixtures["json"])
    assert json_meta["format"] == "json"
    assert json_meta["seed"] == comma_meta["seed"] == 42
    # One plan_items() call feeds every format, so the planted incident is shared.
    assert json_meta["spike"] == comma_meta["spike"]
    assert json_meta["total_lines"] == comma_meta["total_lines"]


def test_comma_bytes_do_not_depend_on_other_registered_formats(
    fixtures, tmp_path, monkeypatch
):
    """Adding a serializer later must not change the committed comma fixture."""
    gen.main(["--seed", "42"], fixtures=fixtures)
    alone = read(fixtures["comma"])

    class StubJson(gen.CommaSerializer):
        """Stand-in for a future serializer."""

        name = "json"

    monkeypatch.setitem(gen.SERIALIZERS, "json", StubJson)
    other = {name: tmp_path / "two" / path.name for name, path in fixtures.items()}
    gen.main(["--seed", "42"], fixtures=other)
    assert read(other["comma"]) == alone


def test_seed_with_format_limits_fixtures_to_that_format(fixtures, monkeypatch):
    """--seed with an explicit --format writes only that format's fixture."""

    class StubJson(gen.CommaSerializer):
        """Stand-in for a future serializer."""

        name = "json"

    monkeypatch.setitem(gen.SERIALIZERS, "json", StubJson)
    assert gen.main(["--seed", "3", "--format", "comma"], fixtures=fixtures) == 0
    assert fixtures["comma"].exists()
    assert not fixtures["json"].exists()


# --- Ad-hoc mode (--out) ----------------------------------------------------


def test_out_writes_that_path_and_metadata_not_fixtures(fixtures, tmp_path):
    """--out writes the file and its sibling metadata and leaves fixtures alone."""
    out = tmp_path / "adhoc" / "foo.log"
    assert gen.main(["--out", str(out)], fixtures=fixtures) == 0
    assert out.is_file()
    assert Path(f"{out}.metadata.json").is_file()
    assert not fixtures["comma"].exists()
    assert not fixtures["comma"].parent.exists()


def test_unseeded_out_metadata_has_no_seed_key(tmp_path):
    """An unseeded ad-hoc run records the format but no seed."""
    out = tmp_path / "foo.log"
    gen.main(["--out", str(out)])
    _, metadata = read(out)
    assert metadata["format"] == "comma"
    assert "seed" not in metadata


def test_out_with_seed_is_reproducible(tmp_path):
    """--out plus --seed gives identical files on every run and records the seed."""
    a, b = tmp_path / "a.log", tmp_path / "b.log"
    gen.main(["--out", str(a), "--seed", "7"])
    gen.main(["--out", str(b), "--seed", "7"])
    assert read(a) == read(b)
    assert read(a)[1]["seed"] == 7


def test_out_with_seed_does_not_write_fixtures(fixtures, tmp_path):
    """When both are given, ad-hoc wins and no fixture is written."""
    out = tmp_path / "foo.log"
    assert gen.main(["--out", str(out), "--seed", "7"], fixtures=fixtures) == 0
    assert out.is_file()
    assert not fixtures["comma"].parent.exists()


def test_out_default_format_is_comma(tmp_path):
    """--format defaults to comma in ad-hoc mode and can be given explicitly."""
    a, b = tmp_path / "a.log", tmp_path / "b.log"
    gen.main(["--out", str(a), "--seed", "5"])
    gen.main(["--out", str(b), "--seed", "5", "--format", "comma"])
    assert read(a) == read(b)


# --- Errors -----------------------------------------------------------------


def test_no_arguments_exits_2_and_writes_nothing(fixtures, capsys):
    """Without --seed or --out the script refuses rather than overwrite fixtures."""
    assert gen.main([], fixtures=fixtures) == 2
    assert "--seed" in capsys.readouterr().err
    assert not fixtures["comma"].parent.exists()


def test_format_alone_still_needs_seed_or_out(fixtures):
    """--format by itself is not enough to pick a mode."""
    assert gen.main(["--format", "comma"], fixtures=fixtures) == 2
    assert not fixtures["comma"].parent.exists()


@pytest.mark.parametrize(
    "extra", [["--seed", "1"], ["--out", "OUT"], ["--out", "OUT", "--seed", "1"]]
)
def test_unknown_format_exits_2_and_writes_nothing(extra, fixtures, tmp_path, capsys):
    """An unsupported --format, including json before it is registered, writes nothing."""
    argv = [str(tmp_path / "x.log") if a == "OUT" else a for a in extra]
    assert gen.main([*argv, "--format", "json"], fixtures=fixtures) == 2
    assert "unsupported format 'json' (supported: comma)" in capsys.readouterr().err
    assert not (tmp_path / "x.log").exists()
    assert not fixtures["comma"].parent.exists()


# --- write_log and the planted data ----------------------------------------


def test_write_log_metadata_matches_the_file_it_wrote(tmp_path):
    """Metadata totals reproduce when the written file is parsed back."""
    import random

    rng = random.Random(9)
    items = gen.plan_items(rng)
    path = tmp_path / "w.log"
    metadata = gen.write_log(path, items, gen.CommaSerializer(rng), seed=9)
    assert read(path)[1] == metadata
    result = LogParser().parse_file(path)
    assert len(result.entries) + result.malformed_count == metadata["total_lines"]
    assert result.malformed_count == metadata["malformed_lines"]
    assert sum(metadata["malformed_by_kind"].values()) == metadata["malformed_lines"]
    assert metadata["malformed_lines"] >= 20
    assert path.read_bytes().count(b"\n") == metadata["total_lines"]


@pytest.mark.parametrize("seed", [1, 2, 3, 42, 2026])
def test_planted_incident_fires_both_detectors_for_any_seed(seed, tmp_path):
    """Spike windows are >= 10 errors with a critical timeout repeat, whatever the seed."""
    out = tmp_path / "p.log"
    gen.main(["--out", str(out), "--seed", str(seed)])
    _, metadata = read(out)
    data = DetectionInput(LogParser().parse_file(out).entries)
    spikes = [f for f in SpikeDetector().detect(data)]
    assert [f.count for f in spikes] == metadata["spike"]["window_error_counts"]
    assert all(f.count >= 10 for f in spikes)
    repeats = [
        f
        for f in RepeatedMessageDetector().detect(data)
        if f.service == metadata["spike"]["dominant_service"]
        and f.message == metadata["spike"]["top_message_template"]
    ]
    assert len(repeats) == 3
    assert all(f.severity == "critical" and f.count >= 15 for f in repeats)
