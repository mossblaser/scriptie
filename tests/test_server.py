import pytest

from typing import Any
from collections.abc import Callable, Awaitable

import asyncio

from pathlib import Path

from aiohttp import web, ClientSession, MultipartWriter
from multidict import MultiDict

import datetime

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


async def test_run_script_no_args(server: ClientSession, print_args_sh: None) -> None:
    resp = await server.post("/scripts/print_args.sh")
    assert resp.status == 200
    rs_id = await resp.text()

    # Wait for script to complete
    await server.get(f"/running/{rs_id}/return_code")

    # Get passed arguments
    args = await (await server.get(f"/running/{rs_id}/output")).text()
    assert args == "\n"


@pytest.mark.parametrize("multipart", [False, True])
async def test_run_script_simple_args(
    server: ClientSession,
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
    await server.get(f"/running/{rs_id}/return_code")

    # Get passed arguments
    args = await (await server.get(f"/running/{rs_id}/output")).text()
    assert args.strip().split("\n") == [
        "The first",
        "Another",
    ]


async def test_run_script_with_file(
    server: ClientSession,
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
    await server.get(f"/running/{rs_id}/return_code")

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
    print_args_sh: None,
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    import scriptie.server as server_module

    monkeypatch.setattr(server_module, "CLEANUP_DELAY", 0.1)

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
    await server.get(f"/running/{rs_id}/return_code")

    # Check temporary file still exists
    f = Path((await (await server.get(f"/running/{rs_id}/output")).text()).rstrip())
    assert f.is_file()

    # Wait for timeout
    await asyncio.sleep(0.1)

    # Should be gone from history
    resp = await server.get(f"/running/{rs_id}/return_code")
    assert resp.status == 404

    # Temporary file should also be gone
    assert not f.is_file()

    # Running list should be empty too
    assert await (await server.get("/running/")).json() == []


async def test_enumerate_running(
    server: ClientSession,
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
            "args": [],
            "end_time": None,
            "progress": [0.0, 0.0],
            "status": "",
            "return_code": None,
        },
        {
            "id": full_featured_rs_id,
            "script": "full_featured.sh",
            "args": ["first", "second"],
            "end_time": None,
            "progress": [0.0, 0.0],
            "status": "",
            "return_code": None,
        },
    ]

    # Check on exit
    assert (
        await (await server.get(f"/running/{minimal_rs_id}/return_code")).text() == "0"
    )
    assert (
        await (await server.get(f"/running/{full_featured_rs_id}/return_code")).text()
        == "123"
    )

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
            "args": [],
            "progress": [0.0, 0.0],
            "status": "",
            "return_code": 0,
        },
        {
            "id": full_featured_rs_id,
            "script": "full_featured.sh",
            "args": ["first", "second"],
            "progress": [100.0, 100.0],
            "status": "finished",
            "return_code": 123,
        },
    ]


async def test_delete_script(
    server: ClientSession,
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
    assert await (await server.get(f"/running/{exit_rs_id}/return_code")).text() == "0"

    # Delete both scripts
    for rs_id in rs_ids:
        resp = await server.delete(f"/running/{rs_id}")
        assert resp.status == 200

    # Make sure both no longer listed
    assert await (await server.get("/running/")).json() == []

    # Make sure temporary files are cleaned up
    for file in files:
        assert not file.is_file()


async def test_output(
    server: ClientSession,
    make_script: Callable[[str, str], None],
) -> None:
    make_script(
        "test.sh",
        """
        #!/bin/sh
        echo One
        sleep 0.1
        echo Two
        """,
    )

    resp = await server.post("/scripts/test.sh")
    assert resp.status == 200
    rs_id = await resp.text()

    # Get some output
    resp = await server.get(f"/running/{rs_id}/output", params={"from": "0"})
    assert await resp.text() == "One\n"

    # Get all available shouldn't block
    resp = await server.get(f"/running/{rs_id}/output")
    assert await resp.text() == "One\n"

    # Get more output, should block
    resp = await server.get(f"/running/{rs_id}/output", params={"from": "4"})
    assert await resp.text() == "Two\n"


async def test_progress(
    server: ClientSession,
    make_script: Callable[[str, str], None],
) -> None:
    make_script(
        "test.sh",
        """
        #!/bin/sh
        echo '## progress: 1/2'
        sleep 0.1
        echo '## progress: 2/2'
        """,
    )

    resp = await server.post("/scripts/test.sh")
    assert resp.status == 200
    rs_id = await resp.text()

    # Get initial progress, blocking until change
    resp = await server.get(f"/running/{rs_id}/progress", params={"since": "[0,0]"})
    assert await resp.json() == [1.0, 2.0]

    # Get current shouldn't block
    resp = await server.get(f"/running/{rs_id}/progress")
    assert await resp.json() == [1.0, 2.0]

    # Get new progress, should block
    resp = await server.get(f"/running/{rs_id}/progress", params={"since": "[1,2]"})
    assert await resp.json() == [2.0, 2.0]


async def test_status(
    server: ClientSession,
    make_script: Callable[[str, str], None],
) -> None:
    make_script(
        "test.sh",
        """
        #!/bin/sh
        echo '## status: foo'
        sleep 0.1
        echo '## status: bar'
        """,
    )

    resp = await server.post("/scripts/test.sh")
    assert resp.status == 200
    rs_id = await resp.text()

    # Get initial status, blocking until change
    resp = await server.get(f"/running/{rs_id}/status", params={"since": ""})
    assert await resp.text() == "foo"

    # Get current shouldn't block
    resp = await server.get(f"/running/{rs_id}/status")
    assert await resp.text() == "foo"

    # Get new status, should block
    resp = await server.get(f"/running/{rs_id}/status", params={"since": "foo"})
    assert await resp.text() == "bar"


async def test_return_code(
    server: ClientSession,
    make_script: Callable[[str, str], None],
) -> None:
    make_script(
        "test.sh",
        """
        #!/bin/sh
        sleep 0.1
        exit 123
        """,
    )

    resp = await server.post("/scripts/test.sh")
    assert resp.status == 200
    rs_id = await resp.text()

    resp = await server.get(f"/running/{rs_id}/return_code")
    assert await resp.text() == "123"

    resp = await server.get(f"/running/{rs_id}/return_code")
    assert await resp.text() == "123"


async def test_start_and_end_time(
    server: ClientSession,
    make_script: Callable[[str, str], None],
) -> None:
    make_script(
        "test.sh",
        """
        #!/bin/sh
        sleep 0.1
        """,
    )

    resp = await server.post("/scripts/test.sh")
    assert resp.status == 200
    rs_id = await resp.text()

    start_time = datetime.datetime.fromisoformat(
        (await (await server.get(f"/running/{rs_id}")).json())["start_time"]
    )
    end_time = datetime.datetime.fromisoformat(
        await (await server.get(f"/running/{rs_id}/end_time")).text()
    )

    assert (end_time - start_time).total_seconds() == pytest.approx(0.1, abs=0.03)


async def test_kill(
    server: ClientSession,
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

    resp = await server.get(f"/running/{rs_id}/output", params={"from": "0"})
    assert resp.status == 200
    assert await resp.text() == "You should see this...\n"

    resp = await server.post(f"/running/{rs_id}/kill")
    assert resp.status == 200

    # Wait until definitely exited
    resp = await server.get(f"/running/{rs_id}/return_code")
    assert resp.status == 200
    assert int(await resp.text()) < 0

    # Should have actually stopped during sleep
    resp = await server.get(f"/running/{rs_id}/output")
    assert resp.status == 200
    assert await resp.text() == "You should see this...\n"
