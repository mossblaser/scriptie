"""
The scriptie API server.

The following endpoints are defined

GET /scripts/
-------------

Enumerates all available scripts and details about them.


POST /scripts/{script_name}
---------------------------

Start executing this script.

Arguments must be given with names arg0, arg1, and so on.

File uploads will be saved to a temporary directory and a filename passed to
the script insteaed. All other arguments are passed as is. There is no checking
that the provided values correspond in any way with the arguments the script
describes itself as accepting.

The response contains the ID of the newly running script.


GET /running/
-------------

Enumerate all currently running scripts along with all scripts which have
finished executing within the last CLEANUP_DELAY seconds. Given in order of
script start time.


GET /running/{id}
-----------------

Show details of a given script execution (same as returned in /running/).


DELETE /running/{id}
--------------------

Kill a running script (if running), delete any temporary files and remove all
record of it from the server.


GET /running/{id}/output
------------------------

Returns the current (interleaved) stdout/stderr contents.


POST /running/{id}/kill
-----------------------

Kills the script (if it is running).

By contrast with DELETE /running/{id}, information about the execution
(including output) is not removed until CLEANUP_DELAY seconds have ellapsed.


GET /running/ws (websocket)
---------------------------

A websocket which can be used to monitor status changes in running scripts.

The websocket expects to be sent JSON objects of the shape:

    {"id": "unique ID here", "type": "name here", other args here}

The server will, in due course and in any order, respond with JSON objects of
the form:

    {"id": "matching unique ID here", "value": "..."}
    ...or...
    {"id": "matching unique ID here", "error": "..."}

Alternatively, the client can send the following JSON to cancel a no longer
needed request

    {"id": "matching unique ID here"}

This does not guarantee a response will be returned anyway, however, due to the
obvious race condition.

The following commands are available:

    get_output(rs_id: str, after: int) -> str
    get_status(rs_id: str, old_status: str) -> str
    get_progress(rs_id: str, old_progres: tuple[float, float]) -> tuple[float, float]
    get_return_code(rs_id: str) -> int
    get_end_time(rs_id: str) -> str

Behaving as the simillarly named methods of
:py:class:`scriptie.scripts.RunningScript` do.

In addition, the following command is available:

    wait_for_running_change(old_rs_ids: str) -> new /running output

This command will block until the output of /running contains a different set
of old_rs_ids (i.e. a run has started, been deleted or expired). It returns a
new copy of the data which would be returned by the /running endpoint. Note
that this does *not* return when an existing running script changes.

"""

import asyncio

from typing import cast, Any

from aiohttp import web, BodyPartReader, WSMsgType, WSCloseCode

from pathlib import Path

import json

import uuid

import traceback

import datetime

from tempfile import TemporaryDirectory

from itertools import count

from weakref import WeakSet

from scriptie.scripts import (
    enumerate_scripts,
    RunningScript,
)


CLEANUP_DELAY = 24 * 60 * 60
"""
How long to wait (in seconds) before removing complete scripts from the
records.
"""

STATIC_FILE_DIR = Path(__file__).parent / "static_files"
"""
Directory in which static files (e.g. the web UI) are stored.
"""

routes = web.RouteTableDef()


@routes.get("/scripts/")
async def get_scripts(request: web.Request) -> web.Response:
    script_dir: Path = request.app["script_dir"]
    return web.json_response(
        [
            {
                "script": script.executable.name,
                "name": script.name,
                "description": script.description,
                "args": [
                    {
                        "description": arg.description,
                        "type": arg.type,
                    }
                    for arg in script.args
                ],
            }
            for script in enumerate_scripts(script_dir)
        ]
    )


@routes.get("/scripts/{script}")
async def get_script(request: web.Request) -> web.Response:
    script_dir: Path = request.app["script_dir"]
    scripts = {
        script.executable.name: script for script in enumerate_scripts(script_dir)
    }
    script = scripts.get(request.match_info["script"])
    if script is None:
        raise web.HTTPNotFound()

    return web.json_response(
        {
            "script": script.executable.name,
            "name": script.name,
            "description": script.description,
            "args": [
                {
                    "description": arg.description,
                    "type": arg.type,
                }
                for arg in script.args
            ],
        }
    )


@routes.post("/scripts/{script}")
async def run_script(request: web.Request) -> web.Response:
    script_dir: Path = request.app["script_dir"]
    running_scripts: dict[str, RunningScript] = request.app["running_scripts"]
    temporary_dirs: dict[str, list[TemporaryDirectory]] = request.app["temporary_dirs"]
    cleanup_tasks: list[asyncio.Future] = request.app["cleanup_tasks"]

    scripts = {
        script.executable.name: script for script in enumerate_scripts(script_dir)
    }
    script = scripts.get(request.match_info["script"])
    if script is None:
        raise web.HTTPNotFound()

    args_by_name: dict[str, str] = {}
    temp_dirs: list[TemporaryDirectory] = []

    # Collect arguments
    if request.content_type == "application/x-www-form-urlencoded":
        args_by_name.update(cast(dict, await request.post()))
    elif request.content_type == "multipart/form-data":
        async for part in (await request.multipart()):
            if part.name is None:
                raise web.HTTPBadRequest(text="All form values must be named.")

            assert isinstance(part, BodyPartReader)
            if part.filename is None:
                args_by_name[part.name] = await part.text()
            else:
                # Convert uploaded files into a filename for said argument
                file_contents = await part.read()
                if file_contents == b"" and not part.filename:
                    # Special case: No file selected
                    args_by_name[part.name] = ""
                else:
                    temp_dir = TemporaryDirectory(
                        prefix=f"{script.executable.name}_",
                        ignore_cleanup_errors=True,
                    )
                    temp_dirs.append(temp_dir)
                    arg_file = Path(temp_dir.name) / (part.filename or "no_name")
                    arg_file.write_bytes(file_contents)
                    args_by_name[part.name] = str(arg_file)
    else:
        # Assume no arguments
        pass

    # Order arguments appropriately
    args: list[str] = []
    for n in count():
        name = f"arg{n}"
        if name in args_by_name:
            args.append(args_by_name.pop(name))
        else:
            break

    # Check non left over
    if args_by_name:
        raise web.HTTPBadRequest(text=f"Unexpected fields: {', '.join(args_by_name)}")

    # Actually run the script
    rs_id = str(uuid.uuid4())
    rs = running_scripts[rs_id] = RunningScript(script, args)
    temporary_dirs[rs_id] = temp_dirs
    running_scripts_changed(request.app)

    async def cleanup() -> None:
        try:
            await rs.get_return_code()
            await asyncio.sleep(CLEANUP_DELAY)
            running_scripts.pop(rs_id, None)
            running_scripts_changed(request.app)
        finally:  # In case of cancellation
            # NB: Files kept around until expiary to aid debugging
            for temp_dir in temporary_dirs.pop(rs_id, []):
                temp_dir.cleanup()

    cleanup_tasks.append(asyncio.create_task(cleanup()))

    return web.Response(text=rs_id)


def running_scripts_changed(app: web.Application) -> None:
    """Notify all waiting processes that app["running_scripts"] has chagned."""
    running_scripts_changed: asyncio.Event = app["running_scripts_changed"][0]
    running_scripts_changed.set()
    app["running_scripts_changed"][0] = asyncio.Event()

async def wait_for_running_scripts_change(app: web.Application) -> None:
    """Notify all waiting processes that app["running_scripts"] has chagned."""
    running_scripts_changed: asyncio.Event = app["running_scripts_changed"][0]
    await running_scripts_changed.wait()


def enumerate_running(running_scripts: dict[str, RunningScript]) -> list[dict[str, Any]]:
    return [
        {
            "id": rs_id,
            "script": rs.script.executable.name,
            "name": rs.script.name,
            "args": rs.args,
            "start_time": rs.start_time.isoformat(),
            "end_time": rs.end_time.isoformat()
            if rs.end_time is not None
            else None,
            "progress": rs.progress,
            "status": rs.status,
            "return_code": rs.return_code,
        }
        for rs_id, rs in running_scripts.items()
    ]


@routes.get("/running/")
async def get_running(request: web.Request) -> web.Response:
    running_scripts: dict[str, RunningScript] = request.app["running_scripts"]

    return web.json_response(enumerate_running(running_scripts))


@routes.get("/running/ws")
async def get_running_websocket(request: web.Request) -> web.WebSocketResponse:
    running_scripts: dict[str, RunningScript] = request.app["running_scripts"]
    websockets: WeakSet[WebSocketResponse] = request.app["websockets"]

    ws = web.WebSocketResponse()
    await ws.prepare(request)
    websockets.add(ws)
    
    tasks: dict[str, asyncio.Task] = {}
    
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                raise ValueError(f"Unexpected message type: {msg.type}")
            match msg.json():
                case {
                    "id": command_id,
                    "type": "wait_for_running_change",
                    "old_rs_ids": old_rs_ids,
                }:
                    async def wait_for_running_change(command_id: str, old_rs_ids: list[str]) -> None:
                        while set(running_scripts) == set(old_rs_ids):
                            await wait_for_running_scripts_change(request.app)
                        await ws.send_json({"id": command_id, "value": enumerate_running(running_scripts)})
                    
                    tasks[command_id] = asyncio.create_task(
                        wait_for_running_change(command_id, old_rs_ids)
                    )
                case {
                    "id": command_id,
                    "type": command_type,
                    "rs_id": rs_id,
                    **command_args,
                }:
                    if rs_id not in running_scripts:
                        await ws.send_json({"id": command_id, "error": "Unknown running script ID"})
                    else:
                        rs = running_scripts[rs_id]
                        if not hasattr(rs, command_type) or command_type.startswith("_"):
                            await ws.send_json({"id": command_id, "error": "Unknown command type"})
                        else:
                            async def run_command(
                                command_id: str,
                                rs: RunningScript,
                                command_type: str,
                                command_args: dict[str, Any],
                            ) -> None:
                                try:
                                    value = await getattr(rs, command_type)(**command_args)
                                    # Yuck: Special case for get_end_time
                                    if isinstance(value, datetime.datetime):
                                        value = value.isoformat()
                                    await ws.send_json({"id": command_id, "value": value})
                                except asyncio.CancelledError:
                                    pass
                                except ConnectionResetError:
                                    pass
                                except Exception as exc:
                                    await ws.send_json({"id": command_id, "error": str(exc)})
                                finally:
                                    tasks.pop(command_id, None)
                            
                            tasks[command_id] = asyncio.create_task(
                                run_command(
                                    command_id,
                                    rs,
                                    command_type,
                                    cast(dict[str, Any], command_args),
                                )
                            )
                case {"id": command_id}:
                    if task := tasks.pop(command_id, None):
                        task.cancel()
                case other:
                    raise Exception("Unsupported request", other)
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        traceback.print_exc()
    finally:
        websockets.discard(ws)
        
        while tasks:
            tasks.popitem()[1].cancel()

    return ws


@routes.get("/running/{id}")
async def get_running_script(request: web.Request) -> web.Response:
    running_scripts: dict[str, RunningScript] = request.app["running_scripts"]
    rs_id = request.match_info["id"]
    rs: RunningScript | None = running_scripts.get(rs_id)
    if rs is None:
        raise web.HTTPNotFound()

    return web.json_response(
        {
            "id": rs_id,
            "script": rs.script.executable.name,
            "name": rs.script.name,
            "args": rs.args,
            "start_time": rs.start_time.isoformat(),
            "end_time": rs.end_time.isoformat() if rs.end_time is not None else None,
            "progress": rs.progress,
            "status": rs.status,
            "return_code": rs.return_code,
        }
    )


@routes.delete("/running/{id}")
async def delete_running_script(request: web.Request) -> web.Response:
    running_scripts: dict[str, RunningScript] = request.app["running_scripts"]
    temporary_dirs: dict[str, list[TemporaryDirectory]] = request.app["temporary_dirs"]
    rs_id = request.match_info["id"]
    rs: RunningScript | None = running_scripts.get(rs_id, None)
    if rs is None:
        raise web.HTTPNotFound()

    await rs.kill()

    running_scripts.pop(rs_id, None)
    running_scripts_changed(request.app)

    for temp_dir in temporary_dirs.pop(rs_id, []):
        temp_dir.cleanup()

    return web.Response()


@routes.get("/running/{id}/output")
async def get_output(request: web.Request) -> web.Response:
    running_scripts: dict[str, RunningScript] = request.app["running_scripts"]
    rs: RunningScript | None = running_scripts.get(request.match_info["id"])
    if rs is None:
        raise web.HTTPNotFound()

    return web.Response(text=rs.output)


@routes.post("/running/{id}/kill")
async def post_kill(request: web.Request) -> web.Response:
    running_scripts: dict[str, RunningScript] = request.app["running_scripts"]
    rs: RunningScript | None = running_scripts.get(request.match_info["id"])
    if rs is None:
        raise web.HTTPNotFound()

    await rs.kill()

    return web.Response()


@routes.get('/')
async def get_index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_FILE_DIR / "index.html")

routes.static('/', STATIC_FILE_DIR)

def make_app(script_dir: Path) -> web.Application:
    app = web.Application()
    app.add_routes(routes)

    app["script_dir"] = script_dir
    app["running_scripts"] = {}
    app["running_scripts_changed"] = [asyncio.Event()]
    app["temporary_dirs"] = {}  # List of TemporaryDirectory per running script
    app["cleanup_tasks"] = []
    app["websockets"] = WeakSet()

    @app.on_shutdown.append
    async def cleanup(app: web.Application) -> None:
        # Make sure all scripts have exited by now
        for rs in cast(dict[str, RunningScript], app["running_scripts"]).values():
            await rs.kill()
        
        # Close any ongoing websocket connections
        websockets: WeakSet[WebSocketResponse] = app["websockets"]
        for ws in set(websockets):
            await ws.close(
                code=WSCloseCode.GOING_AWAY,
                message="Server shutdown",
            )

        # Cancel all scheduled cleanup tasks (temporary directories will be
        # deleted automatically anyway on shutdown)
        for task in app["cleanup_tasks"]:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    return app


def main() -> None:
    """
    Run the server from the command line.
    """
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Serve a simple web UI for launching and monitoring scripts."
    )
    
    parser.add_argument(
        "script_directory",
        type=Path,
        default=Path(),
        help="""
            The directory containing scripts. Defaults to the current
            directory.
        """,
    )
    
    parser.add_argument(
        "--host",
        "-H",
        default="127.0.0.1",
        help="The host interface to serve on. Defaults to %(default)s.",
    )
    
    parser.add_argument(
        "--port",
        "-P",
        default=8080,
        type=int,
        help="The port to serve on. Defaults to %(default)s.",
    )
    
    args = parser.parse_args()
    
    web.run_app(
        make_app(args.script_directory),
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
