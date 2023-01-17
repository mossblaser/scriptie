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


GET /running/{id}/output?from={byte_offset}
-------------------------------------------

Returns the current (interleaved) stdout/stderr contents.

If 'from' is not given, immediately returns whatever output has been received
so far.

If 'from' param is given and is a byte offset into the output stream, blocks
until output beyond the given byte offset has been emitted and returns that.
Alternatively, if the script exits, returns empty.


GET /running/{id}/progress?since={progress}
-------------------------------------------

Returns the current progress reported by the script as a JSON [numer, denom]
pair. Note that if no progress information has been output by the script, [0,
0] is returned.

If 'since' is not given, immediately returns whatever the current progress is.

If 'since' param is given and is a JSON progress tuple, blocks until either
further progress is reported or the script exits, then returns a progress
tuple.


GET /running/{id}/status?since={progress}
-----------------------------------------

Returns the current status reported by the script as free text. Note that if no
statusinformation has been output by the script an empty response is returned.

If 'since' is not given, immediately returns whatever the current status is.

If 'since' param is given and is a previous status value, blocks until either
further status is reported or the script exits, then returns the status.


GET /running/{id}/return_code
-----------------------------

Blocks until the script exits or is killed, then produces the return code left
by the script.


GET /running/{id}/end_time
--------------------------

Blocks until the script exits or is killed, then produces the time at which the
script exited.


POST /running/{id}/kill
-----------------------

Kills the script (if it is running).

By contrast with DELETE /running/{id}, information about the execution
(including output) is not removed until CLEANUP_DELAY seconds have ellapsed.

"""

import asyncio

from typing import cast

from aiohttp import web, BodyPartReader

from pathlib import Path

import json

import uuid

from tempfile import TemporaryDirectory

from itertools import count

from scriptie.scripts import (
    enumerate_scripts,
    RunningScript,
)


CLEANUP_DELAY = 24 * 60 * 60
"""
How long to wait (in seconds) before removing complete scripts from the
records.
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
                temp_dir = TemporaryDirectory(
                    prefix=f"{script.executable.name}_",
                    ignore_cleanup_errors=True,
                )
                temp_dirs.append(temp_dir)
                arg_file = Path(temp_dir.name) / (part.filename or "no_name")
                arg_file.write_bytes(await part.read())
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

    async def cleanup() -> None:
        try:
            await rs.get_return_code()
            await asyncio.sleep(CLEANUP_DELAY)
            running_scripts.pop(rs_id, None)
        finally:  # In case of cancellation
            # NB: Files kept around until expiary to aid debugging
            for temp_dir in temporary_dirs.pop(rs_id, []):
                temp_dir.cleanup()

    cleanup_tasks.append(asyncio.create_task(cleanup()))

    return web.Response(text=rs_id)


@routes.get("/running/")
async def get_running(request: web.Request) -> web.Response:
    running_scripts: dict[str, RunningScript] = request.app["running_scripts"]

    return web.json_response(
        [
            {
                "id": rs_id,
                "script": rs.script.executable.name,
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
    )


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

    for temp_dir in temporary_dirs.pop(rs_id, []):
        temp_dir.cleanup()

    return web.Response()


@routes.get("/running/{id}/output")
async def get_output(request: web.Request) -> web.Response:
    running_scripts: dict[str, RunningScript] = request.app["running_scripts"]
    rs: RunningScript | None = running_scripts.get(request.match_info["id"])
    if rs is None:
        raise web.HTTPNotFound()

    if "from" not in request.query:
        return web.Response(text=rs.output)

    try:
        from_offset = int(request.query["from"])
    except ValueError:
        raise web.HTTPBadRequest(text="from must be an integer")

    return web.Response(text=await rs.get_output(from_offset))


@routes.get("/running/{id}/progress")
async def get_progress(request: web.Request) -> web.Response:
    running_scripts: dict[str, RunningScript] = request.app["running_scripts"]
    rs: RunningScript | None = running_scripts.get(request.match_info["id"])
    if rs is None:
        raise web.HTTPNotFound()

    if "since" not in request.query:
        return web.json_response(rs.progress)

    try:
        since = json.loads(request.query["since"])
    except json.JSONDecodeError:
        raise web.HTTPBadRequest(text="since must be valid JSON")
    if not (
        isinstance(since, list)
        and len(since) == 2
        and isinstance(since[0], (int, float))
        and isinstance(since[1], (int, float))
    ):
        raise web.HTTPBadRequest(text="since must be a [float, float] array")

    return web.json_response(await rs.get_progress((float(since[0]), float(since[1]))))


@routes.get("/running/{id}/status")
async def get_status(request: web.Request) -> web.Response:
    running_scripts: dict[str, RunningScript] = request.app["running_scripts"]
    rs: RunningScript | None = running_scripts.get(request.match_info["id"])
    if rs is None:
        raise web.HTTPNotFound()

    if "since" not in request.query:
        return web.Response(text=rs.status)
    else:
        return web.Response(text=await rs.get_status(request.query["since"]))


@routes.get("/running/{id}/return_code")
async def get_return_code(request: web.Request) -> web.Response:
    running_scripts: dict[str, RunningScript] = request.app["running_scripts"]
    rs: RunningScript | None = running_scripts.get(request.match_info["id"])
    if rs is None:
        raise web.HTTPNotFound()

    return web.Response(text=str(await rs.get_return_code()))


@routes.get("/running/{id}/end_time")
async def get_end_time(request: web.Request) -> web.Response:
    running_scripts: dict[str, RunningScript] = request.app["running_scripts"]
    rs: RunningScript | None = running_scripts.get(request.match_info["id"])
    if rs is None:
        raise web.HTTPNotFound()

    await rs.get_return_code()
    assert rs.end_time is not None

    return web.Response(text=rs.end_time.isoformat())


@routes.post("/running/{id}/kill")
async def post_kill(request: web.Request) -> web.Response:
    running_scripts: dict[str, RunningScript] = request.app["running_scripts"]
    rs: RunningScript | None = running_scripts.get(request.match_info["id"])
    if rs is None:
        raise web.HTTPNotFound()

    await rs.kill()

    return web.Response()


def make_app(script_dir: Path) -> web.Application:
    app = web.Application()
    app.add_routes(routes)

    app["script_dir"] = script_dir
    app["running_scripts"] = {}
    app["temporary_dirs"] = {}  # List of TemporaryDirectory per running script
    app["cleanup_tasks"] = []

    @app.on_cleanup.append
    async def cleanup(app: web.Application) -> None:
        # Make sure all scripts have exited by now
        for rs in cast(dict[str, RunningScript], app["running_scripts"]).values():
            await rs.kill()

        for task in app["cleanup_tasks"]:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    return app


if __name__ == "__main__":
    import sys

    web.run_app(make_app(Path(sys.argv[1])))
