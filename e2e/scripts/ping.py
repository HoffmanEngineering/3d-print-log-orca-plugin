#!/usr/bin/env python3
"""Measures how long Orca's UI thread stops answering, with the window manager's ping.

A GTK window answers _NET_WM_PING from its main loop, so while Orca's UI thread is busy (an upload
running inside on_message) the answer waits. This sends one ping at a time to the window, waits
for the answer, and writes one line per ping: "<seconds since start> <latency ms>". The longest
latency is the longest freeze, to within the 20 ms between pings. A modal dialog (a permission
prompt) runs a nested main loop, so the time a prompt waits for an answer is not counted.

    ping.py <window id> <out file> <seconds>
"""

import ctypes
import ctypes.util
import sys
import time

CLIENT_MESSAGE = 33
SUBSTRUCTURE_NOTIFY = 1 << 19
SUBSTRUCTURE_REDIRECT = 1 << 20
NO_EVENT_MASK = 0


class ClientMessage(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("message_type", ctypes.c_ulong),
        ("format", ctypes.c_int),
        ("data", ctypes.c_long * 5),
    ]


class Event(ctypes.Union):
    _fields_ = [("type", ctypes.c_int), ("client", ClientMessage), ("pad", ctypes.c_long * 24)]


def main():
    window, out_path, seconds = int(sys.argv[1], 0), sys.argv[2], float(sys.argv[3])
    x = ctypes.cdll.LoadLibrary(ctypes.util.find_library("X11"))
    x.XOpenDisplay.restype = ctypes.c_void_p
    x.XInternAtom.restype = ctypes.c_ulong
    x.XDefaultRootWindow.restype = ctypes.c_ulong
    display = ctypes.c_void_p(x.XOpenDisplay(None))
    root = x.XDefaultRootWindow(display)
    protocols = x.XInternAtom(display, b"WM_PROTOCOLS", 0)
    ping = x.XInternAtom(display, b"_NET_WM_PING", 0)
    # The answer comes back as a ClientMessage sent to the root window.
    x.XSelectInput(display, ctypes.c_ulong(root), ctypes.c_long(SUBSTRUCTURE_NOTIFY))
    start = time.monotonic()
    stamp = 1000
    with open(out_path, "w") as out:
        while time.monotonic() - start < seconds:
            stamp += 1
            event = Event()
            event.client.type = CLIENT_MESSAGE
            event.client.window = window
            event.client.message_type = protocols
            event.client.format = 32
            event.client.data[0] = ping
            event.client.data[1] = stamp
            event.client.data[2] = window
            sent = time.monotonic()
            x.XSendEvent(
                display,
                ctypes.c_ulong(window),
                0,
                ctypes.c_long(NO_EVENT_MASK),
                ctypes.byref(event),
            )
            x.XFlush(display)
            answered = False
            while not answered and time.monotonic() - start < seconds + 60:
                while x.XPending(display):
                    reply = Event()
                    x.XNextEvent(display, ctypes.byref(reply))
                    if (
                        reply.type == CLIENT_MESSAGE
                        and reply.client.message_type == protocols
                        and reply.client.data[0] == ping
                        and reply.client.data[1] == stamp
                    ):
                        answered = True
                if not answered:
                    time.sleep(0.002)
            latency = (time.monotonic() - sent) * 1000
            out.write(f"{sent - start:.3f} {latency:.0f}{'' if answered else ' unanswered'}\n")
            out.flush()
            if not answered:
                break
            time.sleep(0.02)


if __name__ == "__main__":
    main()
