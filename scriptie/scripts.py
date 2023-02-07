"""
Logic for enumerating and running scripts.
"""

from typing import NamedTuple, Any, cast
from collections.abc import Iterable, Callable
from dataclasses import dataclass, field

import asyncio

from pathlib import Path

from enum import Enum

import os

import signal

import re

import datetime

from fractions import Fraction

from subprocess import PIPE

from textwrap import dedent


class Argument(NamedTuple):
    description: str | None
    type: str


@dataclass
class Script:
    executable: Path

    args: list[Argument] = field(default_factory=list)

    name: str = "unnamed_script"
    description: str | None = None


# Regex for matching scriptie declarations of the form:
#
#    ## key: value
#
# Or multi-line form:
#
#    ## key: A multi-line value may be continued
#    ##      onto a newline like this, so long as
#    ##      at least two spaces follow the '##'.
#    ##
#    ##      Blank lines are allowed.
#    ##
#    ##      Indentation will also be preserved
#    ##      after any common whitespace is removed.
#    ##
#    ##          This will be indented by 4-spaces.
#
SCRIPTIE_MULTILINE_DECLARATION_RE = re.compile(
    (
        # Ignore leading whitespace
        r"^[ \t]*"
        # The ##, key and colon
        r"## [a-zA-Z0-9_-]+[ \t]*:"
        # First line of value
        r".*$"
        # Subseqeuent lines of value
        r"(?:"
        r"(?:\n^[ \t]*##  +.*$)"
        r"|"
        r"(?:\n^[ \t]*##[ \t]*$)"  # Empty lines may or may not have any special indent
        r")*"
    ),
    re.MULTILINE,
)

# Regex matching a single line declaration
SCRIPTIE_DECLARATION_RE = re.compile(r"^\s*## ([a-zA-Z0-9_-]+)\s*:\s*(.*)$")


def _extract_declarations(file_contents: str) -> dict[str, list[str]]:
    """Extract declarations from a file."""
    declarations: dict[str, list[str]] = {}
    for match in SCRIPTIE_MULTILINE_DECLARATION_RE.finditer(file_contents):
        lines_without_hashes = [
            line.partition("##")[2]
            for line in match.group(0).splitlines()
        ]
        
        key, _, first_line = lines_without_hashes[0].partition(":")
        key = key.strip()
        first_line = first_line.strip()
        
        remaining_lines = "\n".join(lines_without_hashes[1:])
        remaining_lines = dedent(remaining_lines).rstrip()
        
        if first_line and remaining_lines:
            value = first_line + "\n" + remaining_lines
        else:
            value = first_line or remaining_lines
        
        declarations.setdefault(key, []).append(value)
    return declarations


def _parse_argument(arg_spec: str) -> Argument:
    arg_type, _, arg_description = arg_spec.partition(" ")
    return Argument(
        description=arg_description.strip() or None,
        type=arg_type,
    )


def enumerate_scripts(script_dir: Path) -> Iterable[Script]:
    """
    Enumerate the scripts within a directory.

    Any executable file in the root of that directory is considered a script.
    Files in subdirectories are ignored.

    A script may contain declarations which are any line matching the format:

        ## type: value

    Note the double hash.

    The following declaration types are defined:

    * ``## name: <value>`` Gives a name for the script. If not present, the
      filename (less its extension will be used.
    * ``## description: <value>`` Gives an optional description of the purpose
      of the script.
    * ``## arg: <type> <description>`` Defines the purpose of an argument to
      the script. The description is optional. Repeated declarations define
      subsequent arguments. This value is considered entirely informational and
      :py:class:`RunningScript` and the server will not enforce argument types
      or counts.
    """
    for file in script_dir.iterdir():
        if file.is_file() and os.access(file, mode=os.X_OK):
            declarations = _extract_declarations(file.read_text())

            name = declarations.get("name", [file.name.rsplit(".", maxsplit=1)[0]])[0]

            description: str | None = None
            if "description" in declarations:
                description = "\n".join(declarations["description"])

            args = [
                _parse_argument(arg_spec) for arg_spec in declarations.get("arg", [])
            ]

            yield Script(
                executable=file,
                name=name,
                description=description,
                args=args,
            )


class RunningScript:
    """
    An object which manages script execution and monitoring.
    """

    script: Script
    args: list[str]

    start_time: datetime.datetime
    end_time: datetime.datetime | None

    output: str
    status: str
    progress: tuple[float, float]

    # Also used as an indicator of exited-ness. Will only be set to a non-None
    # value after all other changes (i.e. output, status, progress) have
    # been registered.
    return_code: int | None

    # Called (and the list emptied) whenever one of the above changes
    _on_change: list[Callable[[], None]]

    _subprocess: asyncio.subprocess.Process | None

    _run_task: asyncio.Task
    _stdout_task: asyncio.Task | None
    _stderr_task: asyncio.Task | None

    def __init__(self, script: Script, args: list[str] = []) -> None:
        self.script = script
        self.args = args

        self.start_time = datetime.datetime.now()
        self.end_time = None

        self.output = ""
        self.status = ""
        self.progress = (0.0, 0.0)
        self.return_code = None

        self._on_change = []

        self._subprocess = None

        self._run_task = asyncio.create_task(self._run(), name="run_task")
        self._stdout_task = None
        self._stderr_task = None

    async def _run(self) -> None:
        self._subprocess = await asyncio.create_subprocess_exec(
            str(self.script.executable),
            *self.args,
            cwd=str(self.script.executable.parent),
            stdout=PIPE,
            stderr=PIPE,
            bufsize=0,
            # Launch the script in a new process group to make it possible to
            # kill the whole subprocess and any children by group.
            preexec_fn=os.setsid,
        )
        assert self._subprocess.stdout is not None
        assert self._subprocess.stderr is not None
        self._stdout_task = asyncio.create_task(
            self._read_stream(self._subprocess.stdout),
            name="stdout_task",
        )
        self._stderr_task = asyncio.create_task(
            self._read_stream(self._subprocess.stderr),
            name="stderr_task",
        )

    def _report_change(self) -> None:
        """Trigger all current on change callbacks."""
        callbacks = self._on_change
        self._on_change = []
        for callback in callbacks:
            callback()

    async def _read_stream(self, stream: asyncio.StreamReader) -> None:
        """
        Read the a stdout/stderr stream, parsing out all scriptie progress and
        status directives which appear.
        """
        try:
            while line_bytes := await stream.readline():
                line = line_bytes.decode("utf-8")

                self.output += line

                # Find progress/status declarations
                if match := SCRIPTIE_DECLARATION_RE.match(line):
                    key, value = match.groups()
                    if key == "progress":
                        numer, _, denom = value.strip().partition("/")
                        try:
                            self.progress = (
                                float(numer.strip()),
                                float(denom.strip() or "1"),
                            )
                        except ValueError:
                            pass
                    elif key == "status":
                        self.status = value.strip()

                self._report_change()
        finally:
            # To avoid duplicate final end change events, only send final
            # change event for stdout stream closure
            assert self._subprocess is not None
            assert self._subprocess.stdout is not None
            if stream is self._subprocess.stdout:
                # Also wait for the stderr task to end, ensuring that our
                # reported final change will come after any due to stderr messages.
                assert (
                    self._stderr_task is not None
                )  # NB created alongside _stdout_task
                await self._stderr_task

                # Ensure we wait for actual termination, not just closure of
                # stdout/stderr
                self.return_code = await self._subprocess.wait()
                self.end_time = datetime.datetime.now()

                self._report_change()

    async def kill(self, terminate_timeout: float = 5.0) -> None:
        """
        Kill the script, waiting until all outputs are flushed.

        Will initially send SIGTERM and if, after terminate_timeout seconds,
        the command is still running will send SIGKILL.
        """
        await self._run_task

        # Awaiting the above ensures the following
        assert self._subprocess is not None
        assert self._stdout_task is not None
        assert self._stderr_task is not None

        # NB: self._subprocess.kill() will kill only the outer process, not
        # necessarily any new processes it spawned. This can, for example,
        # leave the stdout/stderr streams hanging whilst children quit -- if
        # indeed they ever do.
        #
        # Instead we kill the while process group we created for the script to
        # run in.
        if self._subprocess.returncode is None:
            os.killpg(os.getpgid(self._subprocess.pid), signal.SIGTERM)

        # Give the script time to terminate gracefully
        try:
            await asyncio.wait_for(self._subprocess.wait(), terminate_timeout)
        except asyncio.TimeoutError:
            if self._subprocess.returncode is None:
                os.killpg(os.getpgid(self._subprocess.pid), signal.SIGKILL)

        await self._stdout_task
        await self._stderr_task

    async def _wait_for_change_or_exit(self, check_changed: Callable[[], bool]) -> None:
        """
        Block until the process terminates or check_changed returns True.
        """
        while (
            # Not exited
            self.return_code is None
            # Thing of interest has not changed
            and not check_changed()
        ):
            # Wait for some kind of change
            event = asyncio.Event()
            self._on_change.append(event.set)
            await event.wait()

    async def get_output(self, after: int | None = None) -> str:
        """
        If called with after = None, will immediately return all output
        (interleaved stdout/stderr) produced so far (which may be none).

        If given the length of the string read previously, will block until
        more is available (or the script exits) returning only the new
        characters.
        """
        if after is None:
            return self.output

        await self._wait_for_change_or_exit(lambda: len(self.output) > cast(int, after))

        return self.output[after:]

    async def get_status(self, old_status: str | None = None) -> str:
        """
        If called with no arguments, will immediately return the most recent
        status reported by the script (if any).

        If given an old status value, will block until a status different from
        this takes effect (or the script ends), returning the latest status.
        """
        await self._wait_for_change_or_exit(lambda: self.status != old_status)

        return self.status

    async def get_progress(
        self, old_progress: list[float] | tuple[float, float] | None = None
    ) -> tuple[float, float]:
        """
        If called with no arguments, will immediately return the current
        progress reported by the script.

        If given an old progress value, will block until the progress changes
        (or the script ends), returning the latest status.
        """
        # Yuck: Better support JSON-encoded RPCs to this method
        if isinstance(old_progress, list):
            assert len(old_progress) == 2
            old_progress = cast(tuple[float, float], tuple(old_progress))
        await self._wait_for_change_or_exit(lambda: self.progress != old_progress)

        return self.progress

    async def get_return_code(self) -> int:
        """
        Blocks until the process terminates, returning the return code.
        """
        await self._wait_for_change_or_exit(lambda: self.return_code is not None)

        assert self.return_code is not None
        return self.return_code

    async def get_end_time(self) -> datetime.datetime:
        """
        Blocks until the process terminates, returning the end time code.
        """
        await self.get_return_code()
        assert self.end_time is not None
        return self.end_time
