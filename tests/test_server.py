import pytest

from typing import Any
from collections.abc import Callable, Awaitable, AsyncIterable

import asyncio

from pathlib import Path

import aiohttp
from aiohttp import web, ClientSession, MultipartWriter

from multidict import MultiDict

import datetime

import uuid

from textwrap import dedent

from scriptie.server import make_app


@pytest.fixture
def script_dir(tmp_path: Path) -> Path:
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    return script_dir


@pytest.fixture
async def server(
    aiohttp_client: Callable[[web.Application], Awaitable[ClientSession]],
    script_dir: Path,
) -> ClientSession:
    return await aiohttp_client(make_app(script_dir))


class RunningWSCommandError(Exception):
    pass


class RunningWSClient:
    """
    A websocket client for the /running/ws endpoint.
    
    Call (coroutine) methods on this object to send equivalent calls to the far
    end.
    """
    
    _client: ClientSession
    _ws: aiohttp.ClientWebSocketResponse
    
    _waiting_commands: dict[str, asyncio.Future]
    
    _rx_task: asyncio.Task
    
    def __init__(self, client: ClientSession) -> None:
        self._client = client
        self._waiting_commands = {}
    
    async def _open(self) -> None:
        self._ws = await self._client.ws_connect("/running/ws")
        self._rx_task = asyncio.create_task(self._rx_loop())
    
    async def _close(self) -> None:
        self._rx_task.cancel()
        try:
            await self._rx_task
        except asyncio.CancelledError:
            pass
        except:
            raise

    async def _rx_loop(self) -> None:
        try:
            async for msg in self._ws:
                assert msg.type == aiohttp.WSMsgType.TEXT
                response = msg.json()
                future = self._waiting_commands.pop(response["id"])
                if not future.cancelled():
                    if "value" in response:
                        future.set_result(response["value"])
                    else:
                        future.set_exception(RunningWSCommandError(response.get("error", "Missing 'error' or 'value'")))
        except Exception as e:
            while self._waiting_commands:
                self._waiting_commands.popitem()[1].set_exception(e)
            raise
    
    def __getattr__(self, command_type: str) -> Callable[..., Any]:
        async def cmd(**args) -> Any:
            command_id = str(uuid.uuid4())
            future = self._waiting_commands[command_id] = asyncio.Future()
            await self._ws.send_json(dict(args, id=command_id, type=command_type))
            try:
                return await future
            except asyncio.CancelledError:
                await self._ws.send_json({"id": command_id})
                raise
        return cmd


@pytest.fixture
async def running_ws_client(server: ClientSession) -> AsyncIterable[RunningWSClient]:
    client = RunningWSClient(server)
    await client._open()
    try:
        yield client
    finally:
        await client._close()


async def test_script_enumeration(server: ClientSession, script_dir: Path) -> None:
    script_filename = script_dir / "foo.sh"
    script_filename.write_text(
        """
            ## name: Foo script
            ## description: A quick script
            ## arg: first First argument
            ## arg: second Second argument
        """
    )
    script_filename.chmod(0o777)

    resp = await server.get("/scripts/")
    scripts = await resp.json()

    assert scripts == [
        {
            "script": "foo.sh",
            "name": "Foo script",
            "description": "A quick script",
            "args": [
                {"type": "first", "description": "First argument"},
                {"type": "second", "description": "Second argument"},
            ],
        }
    ]
    
    resp = await server.get("/scripts/foo.sh")
    script = await resp.json()

    assert script == {
        "script": "foo.sh",
        "name": "Foo script",
        "description": "A quick script",
        "args": [
            {"type": "first", "description": "First argument"},
            {"type": "second", "description": "Second argument"},
        ],
    }



@pytest.fixture
def make_script(script_dir: Path) -> Callable[[str, str], None]:
    def make_script(name: str, source: str) -> None:
        script = script_dir / name
        script.write_text(dedent(source).strip())
        script.chmod(0o777)

    return make_script


@pytest.fixture
def print_args_sh(make_script: Callable[[str, str], None]) -> None:
    make_script(
        "print_args.sh",
        r"""
            #!/usr/bin/env python
            import sys
            print("\n".join(sys.argv[1:]))
        """,
    )


def multipart_append_form_field(
    mpwriter: MultipartWriter, name: str, value: str
) -> None:
    part = mpwriter.append(
        value,
    )
    part.set_content_disposition("form-data", name=name)


async def test_run_script_no_args(
    server: ClientSession,
    running_ws_client: RunningWSClient,
    print_args_sh: None,
) -> None:
    # Make sure the newly started task causes wait_for_running_change to fire
    wait_for_running_change_task = asyncio.create_task(
        running_ws_client.wait_for_running_change(old_rs_ids=[])
    )
    
    resp = await server.post("/scripts/print_args.sh")
    assert resp.status == 200
    rs_id = await resp.text()

    # Change should have been reported
    new_running = await wait_for_running_change_task
    assert len(new_running) == 1
    assert new_running[0]["id"] == rs_id

    # Wait for script to complete
    await running_ws_client.get_return_code(rs_id=rs_id)

    # Get passed arguments
    args = await (await server.get(f"/running/{rs_id}/output")).text()
    assert args == "\n"


@pytest.mark.parametrize("multipart", [False, True])
async def test_run_script_simple_args(
    server: ClientSession,
    running_ws_client: RunningWSClient,
    print_args_sh: None,
    multipart: bool,
) -> None:
    if not multipart:
        resp = await server.post(
            "/scripts/print_args.sh",
            data={
                "arg0": "The first",
                "arg1": "Another",
            },
        )
    else:
        with MultipartWriter("form-data") as mpwriter:
            multipart_append_form_field(mpwriter, "arg0", "The first")
            multipart_append_form_field(mpwriter, "arg1", "Another")
        resp = await server.post("/scripts/print_args.sh", data=mpwriter)

    assert resp.status == 200
    rs_id = await resp.text()

    # Wait for script to complete
    await running_ws_client.get_return_code(rs_id=rs_id)

    # Get passed arguments
    args = await (await server.get(f"/running/{rs_id}/output")).text()
    assert args.strip().split("\n") == [
        "The first",
        "Another",
    ]


async def test_run_script_with_file(
    server: ClientSession,
    running_ws_client: RunningWSClient,
    print_args_sh: None,
    tmp_path: Path,
) -> None:
    file_to_send = tmp_path / "to_send.txt"
    file_to_send.write_text("Hello, world!")

    with MultipartWriter("form-data") as mpwriter:
        multipart_append_form_field(mpwriter, "arg0", "The first")

        part = mpwriter.append(file_to_send.open("rb"))
        part.set_content_disposition(
            "attachment",
            name="arg1",
            filename="to_send.txt",
        )

    resp = await server.post("/scripts/print_args.sh", data=mpwriter)

    assert resp.status == 200
    rs_id = await resp.text()

    # Wait for script to complete
    await running_ws_client.get_return_code(rs_id=rs_id)

    # Get passed arguments
    args = (
        (await (await server.get(f"/running/{rs_id}/output")).text())
        .strip()
        .split("\n")
    )
    assert len(args) == 2

    assert args[0] == "The first"

    assert Path(args[1]).name == "to_send.txt"
    assert Path(args[1]).parent.name.startswith("print_args.sh_")
    assert Path(args[1]).read_text() == "Hello, world!"


async def test_run_script_with_absent_file(
    server: ClientSession,
    running_ws_client: RunningWSClient,
    print_args_sh: None,
    tmp_path: Path,
) -> None:
    with MultipartWriter("form-data") as mpwriter:
        part = mpwriter.append(b"")
        part.set_content_disposition(
            "attachment",
            name="arg0",
            filename="",
        )

    resp = await server.post("/scripts/print_args.sh", data=mpwriter)

    assert resp.status == 200
    rs_id = await resp.text()

    # Wait for script to complete
    await running_ws_client.get_return_code(rs_id=rs_id)

    assert (await (await server.get(f"/running/{rs_id}/output")).text()) == "\n"


@pytest.mark.parametrize(
    "args",
    [
        # No 'arg0'
        ["not_named_arg_0"],
        ["arg1"],
        # Missing one
        ["arg0", "arg1", "arg3"],
        # Extras
        ["arg0", "arg1", "foobar"],
    ],
)
async def test_run_script_bad_arg_names(
    server: ClientSession,
    print_args_sh: None,
    args: list[str],
) -> None:
    resp = await server.post("/scripts/print_args.sh", data={arg: arg for arg in args})
    assert resp.status == 400


async def test_run_script_cleanup(
    server: ClientSession,
    running_ws_client: RunningWSClient,
    print_args_sh: None,
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    import scriptie.server as server_module

    monkeypatch.setattr(server_module, "CLEANUP_DELAY", 0.2)

    # Send a file
    file_to_send = tmp_path / "to_send.txt"
    file_to_send.write_text("Hello, world!")
    with MultipartWriter("form-data") as mpwriter:
        part = mpwriter.append(file_to_send.open("rb"))
        part.set_content_disposition(
            "attachment",
            name="arg0",
            filename="to_send.txt",
        )

    resp = await server.post("/scripts/print_args.sh", data=mpwriter)
    assert resp.status == 200
    rs_id = await resp.text()

    # Wait for exit
    await running_ws_client.get_return_code(rs_id=rs_id)
    
    # Make sure the running list still includes the old script
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            running_ws_client.wait_for_running_change(old_rs_ids=[rs_id]),
            timeout=0.1,
        )

    # Check temporary file still exists
    f = Path((await (await server.get(f"/running/{rs_id}/output")).text()).rstrip())
    assert f.is_file()

    wait_for_running_change_task = asyncio.create_task(
        running_ws_client.wait_for_running_change(old_rs_ids=[rs_id])
    )

    # Wait for timeout
    await asyncio.sleep(0.1)

    # Should be gone from history
    with pytest.raises(RunningWSCommandError):
        await running_ws_client.get_return_code(rs_id=rs_id)
    
    # Make sure wait_for_running_change unblocks
    assert await wait_for_running_change_task == []

    # Temporary file should also be gone
    assert not f.is_file()

    # Running list should be empty too
    assert await (await server.get("/running/")).json() == []


async def test_enumerate_running(
    server: ClientSession,
    running_ws_client: RunningWSClient,
    make_script: Callable[[str, str], None],
) -> None:
    make_script(
        "minimal.sh",
        """
        #!/bin/sh
        sleep 0.1
        """,
    )
    make_script(
        "full_featured.sh",
        """
        #!/bin/sh
        sleep 0.1
        echo "## status: finished"
        echo "## progress: 100/100"
        exit 123
        """,
    )

    assert await (await server.get("/running/")).json() == []

    resp = await server.post("/scripts/minimal.sh")
    assert resp.status == 200
    minimal_rs_id = await resp.text()

    resp = await server.post(
        "/scripts/full_featured.sh",
        data={"arg0": "first", "arg1": "second"},
    )
    assert resp.status == 200
    full_featured_rs_id = await resp.text()

    running = await (await server.get("/running/")).json()
    assert len(running) == 2

    # Both should have started about now...
    assert (
        datetime.datetime.now()
        - datetime.datetime.fromisoformat(running[0].pop("start_time"))
    ).total_seconds() == pytest.approx(0, abs=0.05)
    assert (
        datetime.datetime.now()
        - datetime.datetime.fromisoformat(running[1].pop("start_time"))
    ).total_seconds() == pytest.approx(0, abs=0.05)

    # Check remaining (more predictable) values
    assert running == [
        {
            "id": minimal_rs_id,
            "script": "minimal.sh",
            "name": "minimal",
            "args": [],
            "end_time": None,
            "progress": [0.0, 0.0],
            "status": "",
            "return_code": None,
        },
        {
            "id": full_featured_rs_id,
            "script": "full_featured.sh",
            "name": "full_featured",
            "args": ["first", "second"],
            "end_time": None,
            "progress": [0.0, 0.0],
            "status": "",
            "return_code": None,
        },
    ]

    # Check on exit
    assert await running_ws_client.get_return_code(rs_id=minimal_rs_id) == 0
    assert await running_ws_client.get_return_code(rs_id=full_featured_rs_id) == 123

    running = await (await server.get("/running/")).json()
    assert len(running) == 2

    # Both should have ended about now...
    running[0].pop("start_time")
    running[1].pop("start_time")
    assert (
        datetime.datetime.now()
        - datetime.datetime.fromisoformat(running[0].pop("end_time"))
    ).total_seconds() == pytest.approx(0, abs=0.05)
    assert (
        datetime.datetime.now()
        - datetime.datetime.fromisoformat(running[1].pop("end_time"))
    ).total_seconds() == pytest.approx(0, abs=0.05)

    # Check remaining (more predictable) values
    assert running == [
        {
            "id": minimal_rs_id,
            "script": "minimal.sh",
            "name": "minimal",
            "args": [],
            "progress": [0.0, 0.0],
            "status": "",
            "return_code": 0,
        },
        {
            "id": full_featured_rs_id,
            "script": "full_featured.sh",
            "name": "full_featured",
            "args": ["first", "second"],
            "progress": [100.0, 100.0],
            "status": "finished",
            "return_code": 123,
        },
    ]


async def test_delete_script(
    server: ClientSession,
    running_ws_client: RunningWSClient,
    make_script: Callable[[str, str], None],
    tmp_path: Path,
) -> None:
    # Delete two different scripts, one which will exit immediately one which
    # will sleep for a long time (and therefore need to be killed during
    # deletion)
    make_script(
        "exit.sh",
        """
        #!/bin/sh
        exit 0
        """,
    )
    make_script(
        "sleep.sh",
        """
        #!/bin/sh
        sleep 1000
        """,
    )

    rs_ids = []
    for script in ["exit.sh", "sleep.sh"]:
        # Send a file
        file_to_send = tmp_path / "to_send.txt"
        file_to_send.write_text("Hello, world!")
        with MultipartWriter("form-data") as mpwriter:
            part = mpwriter.append(file_to_send.open("rb"))
            part.set_content_disposition(
                "attachment",
                name="arg0",
                filename="to_send.txt",
            )

        resp = await server.post(f"/scripts/{script}", data=mpwriter)
        assert resp.status == 200
        rs_ids.append((await resp.text()))

    exit_rs_id, sleep_rs_id = rs_ids

    # Get temporary file names
    files = [
        Path(rs_info["args"][0])
        for rs_info in await (await server.get(f"/running/")).json()
    ]

    # Wait for quick script to exit
    assert await running_ws_client.get_return_code(rs_id=exit_rs_id) == 0

    # Delete both scripts, checking wait_for_running_change unblocks
    wait_for_running_change_task = asyncio.create_task(
        running_ws_client.wait_for_running_change(old_rs_ids=[exit_rs_id, sleep_rs_id])
    )
    resp = await server.delete(f"/running/{exit_rs_id}")
    assert resp.status == 200
    assert len(await wait_for_running_change_task) == 1
    
    wait_for_running_change_task = asyncio.create_task(
        running_ws_client.wait_for_running_change(old_rs_ids=[sleep_rs_id])
    )
    resp = await server.delete(f"/running/{sleep_rs_id}")
    assert resp.status == 200
    assert await wait_for_running_change_task == []

    # Make sure both no longer listed
    assert await (await server.get("/running/")).json() == []

    # Make sure temporary files are cleaned up
    for file in files:
        assert not file.is_file()


async def test_kill(
    server: ClientSession,
    running_ws_client: RunningWSClient,
    make_script: Callable[[str, str], None],
) -> None:
    make_script(
        "test.sh",
        """
        #!/bin/sh
        echo You should see this...
        sleep 100
        echo But never this...
        """,
    )

    resp = await server.post("/scripts/test.sh")
    assert resp.status == 200
    rs_id = await resp.text()

    assert (
        await running_ws_client.get_output(rs_id=rs_id, after=0) ==
        "You should see this...\n"
    )

    resp = await server.post(f"/running/{rs_id}/kill")
    assert resp.status == 200

    # Wait until definitely exited
    assert await running_ws_client.get_return_code(rs_id=rs_id) < 0

    # Should have actually stopped during sleep
    resp = await server.get(f"/running/{rs_id}/output")
    assert resp.status == 200
    assert await resp.text() == "You should see this...\n"


async def test_running_ws(
    server: ClientSession,
    running_ws_client: RunningWSClient,
    make_script: Callable[[str, str], None],
) -> None:
    make_script(
        "sleep.sh",
        """
        #!/bin/sh
        echo "Going to sleep..."
        sleep 1000
        """,
    )

    resp = await server.post("/scripts/sleep.sh")
    assert resp.status == 200
    rs_id = await resp.text()

    # Should block for valid commands
    assert (
        await running_ws_client.get_output(rs_id=rs_id, after=0)
        == "Going to sleep...\n"
    )
    
    # Should pass back failures on command errors
    with pytest.raises(RunningWSCommandError):
        await running_ws_client.get_output(rs_id=rs_id, after="not valid at all")
    
    # Should pass back failure on non-existant command
    with pytest.raises(RunningWSCommandError):
        await running_ws_client.get_foobar(rs_id=rs_id)
    
    # Should pass back failure on bad RunningScript ID
    with pytest.raises(RunningWSCommandError):
        await running_ws_client.get_output(rs_id="bad", after=0)
    
    # Should support cancellation
    # TODO: Check if cancellation actually occurred rather than just checking
    # it didn't crash...
    task = asyncio.create_task(
        running_ws_client.get_output(rs_id=rs_id, after=len("Going to sleep...\n"))
    )
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    
    # Check end time is in JSON-friendly type
    assert (await server.post(f"/running/{rs_id}/kill")).status == 200
    end_time_text = await running_ws_client.get_end_time(rs_id=rs_id)
    end_time = datetime.datetime.fromisoformat(end_time_text)
    assert (datetime.datetime.now() - end_time).total_seconds() == pytest.approx(0.0, abs=0.05)
