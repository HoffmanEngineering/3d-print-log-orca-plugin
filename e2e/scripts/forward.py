#!/usr/bin/env python3
"""Forwards localhost:<port> in the container to the same port on the Docker host.

The claim page opens inside Orca's tab at the web address the plugin is configured with
(http://localhost:4320), and that page calls the API at http://localhost:5101. Inside the
container both are the host's servers, so the URLs the plugin logs work unchanged in a browser
on the host.

    forward.py <host name> <port> [<port>...]
"""

import asyncio
import sys


async def pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


def handler(host, port):
    async def handle(client_reader, client_writer):
        try:
            server_reader, server_writer = await asyncio.open_connection(host, port)
        except OSError:
            client_writer.close()
            return
        await asyncio.gather(pipe(client_reader, server_writer), pipe(server_reader, client_writer))

    return handle


async def main():
    host, ports = sys.argv[1], [int(p) for p in sys.argv[2:]]
    servers = [await asyncio.start_server(handler(host, p), "127.0.0.1", p) for p in ports]
    await asyncio.gather(*(s.serve_forever() for s in servers))


if __name__ == "__main__":
    asyncio.run(main())
