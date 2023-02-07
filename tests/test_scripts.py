import pytest

from typing import Callable

import os

from textwrap import dedent

import time

from scriptie.scripts import (
    _extract_declarations,
    _parse_argument,
    enumerate_scripts,
    Script,
    Argument,
    RunningScript,
)

from pathlib import Path


def test_extract_declarations() -> None:
    assert (
        _extract_declarations(
            """
                Non-declaration
                ## foo: abc
                Another non-declaration between
                ## bar: 1
                
                Some repeated declarations (and varied whitespace around colon)
                ## bar: 2
                ## bar:3
                
                ## bar : 4 
                
                Mult-line;
                ## baz: Line one
                ##      Line two
                ##        Indented line three
                ##
                ##      Line four after empty
                
                Multi-line with first line blank
                ## qux:
                ##  The value
            """
        )
        == {
            "foo": ["abc"],
            "bar": ["1", "2", "3", "4"],
            "baz": ["Line one\nLine two\n  Indented line three\n\nLine four after empty"],
            "qux": ["The value"],
        }
    )


@pytest.mark.parametrize(
    "arg_spec, exp",
    [
        ("int", Argument(type="int", description=None)),
        ("int    ", Argument(type="int", description=None)),
        ("str Foobar", Argument(type="str", description="Foobar")),
        ("str   Foo bar baz  ", Argument(type="str", description="Foo bar baz")),
    ],
)
def test_parse_argument(arg_spec: str, exp: Argument) -> None:
    assert _parse_argument(arg_spec) == exp


def test_enumerate_scripts(tmp_path: Path) -> None:
    not_executable = tmp_path / "not_executable.txt"
    not_executable.write_text("Not me!")

    not_a_file = tmp_path / "not_a_file"
    not_a_file.mkdir()

    no_declarations = tmp_path / "no_declarations.sh"
    no_declarations.write_text("Nothing\nhere.")
    no_declarations.chmod(0o777)

    no_extension = tmp_path / "no_extension"
    no_extension.write_text("Nothing\nhere.")
    no_extension.chmod(0o777)

    two_extensions = tmp_path / "two.extensions.sh"
    two_extensions.write_text("Nothing\nhere.")
    two_extensions.chmod(0o777)

    with_declarations = tmp_path / "with_declarations.sh"
    with_declarations.write_text(
        """
            Foo
            ## name: With Declarations
            ## description: Has some declarations
            ## arg: str
            ## arg: int Arg description
        """
    )
    with_declarations.chmod(0o777)

    scripts = {script.executable: script for script in enumerate_scripts(tmp_path)}

    assert set(scripts.keys()) == {
        no_declarations,
        no_extension,
        two_extensions,
        with_declarations,
    }

    assert scripts[no_declarations] == Script(
        executable=no_declarations,
        name="no_declarations",
        description=None,
        args=[],
    )

    assert scripts[no_extension] == Script(
        executable=no_extension,
        name="no_extension",
        description=None,
        args=[],
    )

    assert scripts[two_extensions] == Script(
        executable=two_extensions,
        name="two.extensions",
        description=None,
        args=[],
    )

    assert scripts[with_declarations] == Script(
        executable=with_declarations,
        name="With Declarations",
        description="Has some declarations",
        args=[
            Argument(type="str", description=None),
            Argument(type="int", description="Arg description"),
        ],
    )


class TestRunningScript:
    @pytest.fixture
    def make_script(self, tmp_path: Path) -> Callable[[str], Script]:
        def make_script(script: str) -> Script:
            script_file = tmp_path / "script.sh"
            script_file.write_text("#!/usr/bin/env bash\n" + dedent(script).strip())
            script_file.chmod(0o777)

            return Script(script_file)

        return make_script

    async def test_instant_exit(self) -> None:
        rs = RunningScript(Script(Path("true")))
        assert await rs.get_return_code() == 0

    async def test_non_zero_exit(self, make_script: Callable[[str], Script]) -> None:
        rs = RunningScript(make_script("exit 123"))
        assert await rs.get_return_code() == 123

    async def test_wait_for_exit(self, make_script: Callable[[str], Script]) -> None:
        before = time.monotonic()
        rs = RunningScript(make_script("sleep 0.1"))
        assert await rs.get_return_code() == 0
        after = time.monotonic()
        assert after - before > 0.1

    async def test_get_end_time(self, make_script: Callable[[str], Script]) -> None:
        rs = RunningScript(make_script("sleep 0.1"))
        assert (
            await rs.get_end_time() - rs.start_time
        ).total_seconds() == pytest.approx(0.1, abs=0.05)

    async def test_output(self, make_script: Callable[[str], Script]) -> None:
        rs = RunningScript(
            make_script(
                """
                echo hello, world
                sleep 0.1
                echo goodbye 1>&2
                """
            )
        )
        # Should get partial result
        assert await rs.get_output(0) == "hello, world\n"

        # Should get it again if ask from 0 (or everything so far)
        assert await rs.get_output(0) == "hello, world\n"
        assert await rs.get_output() == "hello, world\n"

        # If we ask for more we should block, then should also get interleaved
        # stdout/stderr content
        assert await rs.get_output(len("hello, world\n")) == "goodbye\n"

        # After exit we should get nothing more without blocking
        await rs.get_return_code()
        assert await rs.get_output(len("hello, world\ngoodbye\n")) == ""

    async def test_working_directory(
        self,
        tmp_path: Path,
        make_script: Callable[[str], Script],
    ) -> None:
        (tmp_path / "foo.txt").write_text("Hello, world!")
        rs = RunningScript(make_script("cat ./foo.txt"))

        await rs.get_return_code()
        assert await rs.get_output() == "Hello, world!"

    async def test_get_status(self, make_script: Callable[[str], Script]) -> None:
        rs = RunningScript(
            make_script(
                """
                echo "## status: Started..."
                sleep 0.05
                echo "## status: Finished..."
                """
            )
        )
        assert await rs.get_status("") == "Started..."
        assert await rs.get_status("") == "Started..."
        assert await rs.get_status() == "Started..."

        # If we ask for change should block
        assert await rs.get_status("Started...") == "Finished..."

        # After exit we should get nothing more without blocking
        await rs.get_return_code()
        assert await rs.get_status("Finished...") == "Finished..."

    async def test_get_progress(self, make_script: Callable[[str], Script]) -> None:
        rs = RunningScript(
            make_script(
                """
                sleep 0.05
                echo "## progress: 0.5"
                sleep 0.05
                echo "## progress: 3/4"
                sleep 0.05
                echo "## progress: 4 / 4 "
                """
            )
        )
        # Initially should have 'invalid' progress
        assert await rs.get_progress() == (0, 0)
        # Then wait for first progress
        assert await rs.get_progress((0, 0)) == (0.5, 1)
        # Shouldn't block if ask again
        assert await rs.get_progress((0, 0)) == (0.5, 1)
        assert await rs.get_progress() == (0.5, 1)

        # If we ask for change should block
        assert await rs.get_progress((0.5, 1)) == (3, 4)

        # Alternative formatting
        assert await rs.get_progress((3, 4)) == (4, 4)

        # After exit we should get nothing more without blocking
        await rs.get_return_code()
        assert await rs.get_progress((4, 4)) == (4, 4)

    async def test_kill_terminate(self, make_script: Callable[[str], Script]) -> None:
        rs = RunningScript(
            make_script(
                """
                echo You will see this...
                sleep 10
                echo You can't print this...
                """
            )
        )
        # Make sure started
        assert await rs.get_output(0) == "You will see this...\n"

        await rs.kill()

        assert await rs.get_return_code() < 0
        assert await rs.get_output() == "You will see this...\n"

    async def test_kill_timeout(self, make_script: Callable[[str], Script]) -> None:
        rs = RunningScript(
            make_script(
                """
                trap -- "" SIGTERM
                
                echo You will see this...
                sleep 10
                echo You can't print this...
                """
            )
        )
        # Make sure started
        assert await rs.get_output(0) == "You will see this...\n"

        await rs.kill(0.1)

        assert await rs.get_output() == "You will see this...\n"
        assert await rs.get_return_code() < 0

    async def test_kill_repeated(self, make_script: Callable[[str], Script]) -> None:
        rs = RunningScript(
            make_script(
                """
                echo You will see this...
                sleep 10
                echo You can't print this...
                """
            )
        )
        # Make sure started
        assert await rs.get_output(0) == "You will see this...\n"

        await rs.kill()
        await rs.kill()
        await rs.kill()

        assert await rs.get_return_code() < 0
        assert await rs.get_output() == "You will see this...\n"
