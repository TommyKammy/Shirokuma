"""Bind one Unix listener relative to an inherited directory descriptor.

This short-lived child changes only its own cwd; the multithreaded owner never
changes cwd. The private socketpair transports the bound listener descriptor.
"""
import os
import socket
import struct
import sys


def main():
    directory = int(sys.argv[1])
    channel = socket.socket(fileno=int(sys.argv[2]))
    os.fchdir(directory)
    os.umask(0o177)  # Bind creates the socket as 0600; the owner must not chmod an entry later.
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind('control.sock')
        channel.sendmsg([b'B'], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                                   struct.pack('i', listener.fileno()))])
    channel.close()


if __name__ == '__main__':
    main()
